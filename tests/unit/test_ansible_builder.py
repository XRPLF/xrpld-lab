#!/usr/bin/env python
# coding: utf-8

import os
import re
import yaml
import pytest

from xrpld_lab.models import (
    AlloyConfig,
    VlConfig,
    AnsibleConfig,
    NginxConfig,
    RedisConfig,
    FaucetConfig,
    StreamConfig,
    DebugConfig,
    CompilerConfig,
    PortSet,
    NodeRole,
    ServicesHost,
    StatusConfig,
)
from xrpld_lab.ansible_builder import AnsibleBuilder
import xrpld_lab.ansible_builder as ansible_builder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validator_ports(index: int = 1) -> PortSet:
    return PortSet.for_node(index, NodeRole.VALIDATOR)


def _peer_ports(index: int = 1) -> PortSet:
    return PortSet.for_node(index, NodeRole.PEER)


def _basic_config() -> AnsibleConfig:
    return AnsibleConfig(
        ssh_port=20,
        ssh_user="ubuntu",
        ssh_key_path="~/.ssh/id_rsa",
        vips=["10.0.0.1", "10.0.0.2"],
        pips=["10.0.0.10"],
    )


def _build_basic(tmp_path) -> AnsibleBuilder:
    cluster_dir = str(tmp_path / "test-cluster")
    os.makedirs(cluster_dir, exist_ok=True)
    config = _basic_config()
    builder = AnsibleBuilder(
        cluster_dir=cluster_dir,
        config=config,
        image_name="transia/cluster:abc123",
    )
    builder.add_node(
        "vnode1",
        "10.0.0.1",
        _validator_ports(1),
        f"{cluster_dir}/vnode1/config/",
        "validator",
    )
    builder.add_node(
        "vnode2",
        "10.0.0.2",
        _validator_ports(2),
        f"{cluster_dir}/vnode2/config/",
        "validator",
    )
    builder.add_node(
        "pnode1", "10.0.0.10", _peer_ports(1), f"{cluster_dir}/pnode1/config/", "peer"
    )
    return builder


def _services_host(name="proxy", ip="10.0.0.10", **kwargs) -> ServicesHost:
    return ServicesHost(name=name, ip=ip, **kwargs)


# ===========================================================================
# Core file generation
# ===========================================================================


class TestAddNode:
    def test_empty_ip_is_rejected(self, tmp_path):
        builder = AnsibleBuilder(
            cluster_dir=str(tmp_path),
            config=_basic_config(),
            image_name="transia/cluster:abc123",
        )
        with pytest.raises(ValueError, match="vnode3 has no IP address"):
            builder.add_node("vnode3", "", _validator_ports(3), "cfg/", "validator")
        assert builder._nodes == []


class TestCoreFiles:
    def test_write_creates_ansible_dir(self, tmp_path):
        builder = _build_basic(tmp_path)
        result = builder.write()
        assert os.path.isdir(result)

    def test_write_creates_hosts_txt(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        assert os.path.exists(os.path.join(builder.ansible_dir, "hosts.txt"))

    def test_write_creates_host_vars(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        hv_dir = os.path.join(builder.ansible_dir, "host_vars")
        assert os.path.exists(os.path.join(hv_dir, "10.0.0.1.yml"))
        assert os.path.exists(os.path.join(hv_dir, "10.0.0.2.yml"))
        assert os.path.exists(os.path.join(hv_dir, "10.0.0.10.yml"))

    def test_write_creates_deps_yml(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        assert os.path.exists(os.path.join(builder.ansible_dir, "deps.yml"))

    def test_write_creates_main_yml(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        assert os.path.exists(os.path.join(builder.ansible_dir, "main.yml"))

    def test_write_creates_clean_yml(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        assert os.path.exists(os.path.join(builder.ansible_dir, "clean.yml"))

    def test_write_creates_clean_sh(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        path = os.path.join(builder.ansible_dir, "clean.sh")
        assert os.path.exists(path)
        assert os.access(path, os.X_OK)

    def test_write_creates_run_sh(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        path = os.path.join(builder.ansible_dir, "run.sh")
        assert os.path.exists(path)
        assert os.access(path, os.X_OK)


# ===========================================================================
# hosts.txt content
# ===========================================================================


class TestHostsTxt:
    def test_contains_all_ips(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        content = open(os.path.join(builder.ansible_dir, "hosts.txt")).read()
        assert "10.0.0.1" in content
        assert "10.0.0.2" in content
        assert "10.0.0.10" in content

    def test_has_nodes_group(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        content = open(os.path.join(builder.ansible_dir, "hosts.txt")).read()
        assert "[nodes]" in content
        assert "[all]" not in content

    def test_no_role_based_groups(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        content = open(os.path.join(builder.ansible_dir, "hosts.txt")).read()
        assert "[validator]" not in content
        assert content.count("[nodes]") == 1

    def test_ssh_settings(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        content = open(os.path.join(builder.ansible_dir, "hosts.txt")).read()
        assert "ansible_port=20" in content
        assert "ansible_user=ubuntu" in content
        assert "ansible_ssh_private_key_file=~/.ssh/id_rsa" in content

    def test_vars_file_in_host_line(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        content = open(os.path.join(builder.ansible_dir, "hosts.txt")).read()
        assert "vars_file=host_vars/10.0.0.1.yml" in content
        assert "vars_file=host_vars/10.0.0.10.yml" in content

    def test_services_host_group(self, tmp_path):
        cluster_dir = str(tmp_path / "test-cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        config = AnsibleConfig(
            vips=["10.0.0.1"],
            pips=["10.0.0.10"],
            services=[
                _services_host(
                    "proxy", "10.0.0.10", nginx=NginxConfig(domain="test.example.com")
                )
            ],
        )
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc")
        builder.add_node(
            "vnode1",
            "10.0.0.1",
            _validator_ports(1),
            f"{cluster_dir}/vnode1/config/",
            "validator",
        )
        builder.add_node(
            "pnode1",
            "10.0.0.10",
            _peer_ports(1),
            f"{cluster_dir}/pnode1/config/",
            "peer",
        )
        builder.write()
        content = open(os.path.join(builder.ansible_dir, "hosts.txt")).read()
        assert "[proxy]" in content
        proxy_section = content.split("[proxy]")[1]
        assert "10.0.0.10" in proxy_section

    def test_multiple_services_host_groups(self, tmp_path):
        cluster_dir = str(tmp_path / "test-cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        config = AnsibleConfig(
            vips=["10.0.0.1"],
            pips=["10.0.0.10", "10.0.0.11"],
            services=[
                _services_host(
                    "proxy", "10.0.0.10", nginx=NginxConfig(domain="a.example.com")
                ),
                _services_host("infra", "10.0.0.11", redis=RedisConfig()),
            ],
        )
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc")
        builder.add_node(
            "vnode1",
            "10.0.0.1",
            _validator_ports(1),
            f"{cluster_dir}/vnode1/config/",
            "validator",
        )
        builder.add_node(
            "pnode1",
            "10.0.0.10",
            _peer_ports(1),
            f"{cluster_dir}/pnode1/config/",
            "peer",
        )
        builder.add_node(
            "pnode2",
            "10.0.0.11",
            _peer_ports(2),
            f"{cluster_dir}/pnode2/config/",
            "peer",
        )
        builder.write()
        content = open(os.path.join(builder.ansible_dir, "hosts.txt")).read()
        assert "[proxy]" in content
        assert "[infra]" in content


# ===========================================================================
# nodes group: node plays reach validators and peers only
# ===========================================================================


def _inventory_groups(content: str) -> dict:
    """Map each [group] in an ini inventory to the hosts listed under it."""
    groups: dict = {}
    current = None
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            groups[current] = []
            continue
        groups[current].append(line.split()[0])
    return groups


class TestNodesGroup:
    NODE_TEMPLATES = [
        "_DEPS_YML",
        "_MAIN_HEADER",
        "_MAIN_HEADER_ROLLING",
        "_CLEAN_YML",
        "_ALLOY_YML",
        "_STATUS_YML",
    ]

    def _builder(self, tmp_path, services):
        cluster_dir = str(tmp_path / "nodes-cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        config = AnsibleConfig(
            vips=["10.0.0.1", "10.0.0.2"], pips=["10.0.0.10"], services=services
        )
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc")
        for i, ip in enumerate(config.vips, 1):
            builder.add_node(
                f"vnode{i}",
                ip,
                _validator_ports(i),
                f"{cluster_dir}/vnode{i}/config/",
                "validator",
            )
        builder.add_node(
            "pnode1",
            "10.0.0.10",
            _peer_ports(1),
            f"{cluster_dir}/pnode1/config/",
            "peer",
        )
        builder.write()
        return builder

    def _groups(self, builder) -> dict:
        return _inventory_groups(
            open(os.path.join(builder.ansible_dir, "hosts.txt")).read()
        )

    def test_nodes_group_holds_exactly_the_validators_and_peers(self, tmp_path):
        groups = self._groups(self._builder(tmp_path, []))
        assert list(groups) == ["nodes"]
        assert groups["nodes"] == ["10.0.0.1", "10.0.0.2", "10.0.0.10"]

    @pytest.mark.parametrize("name", NODE_TEMPLATES)
    def test_node_template_targets_nodes(self, name):
        plays = yaml.safe_load(getattr(ansible_builder, name) + "  tasks: []\n")
        assert plays[0]["hosts"] == "nodes"

    def test_generated_node_playbooks_target_nodes(self, tmp_path):
        builder = self._builder(tmp_path, [])
        for playbook in ("deps.yml", "main.yml", "clean.yml"):
            plays = yaml.safe_load(
                open(os.path.join(builder.ansible_dir, playbook)).read()
            )
            assert [p["hosts"] for p in plays] == ["nodes"], playbook

    def test_dedicated_services_host_is_outside_nodes(self, tmp_path):
        builder = self._builder(
            tmp_path,
            [
                _services_host(
                    "infra",
                    "10.0.0.50",
                    nginx=NginxConfig(domain="test.example.com"),
                    redis=RedisConfig(),
                )
            ],
        )
        groups = self._groups(builder)
        assert groups["nodes"] == ["10.0.0.1", "10.0.0.2", "10.0.0.10"]
        assert groups["infra"] == ["10.0.0.50"]
        assert not os.path.exists(
            os.path.join(builder.ansible_dir, "host_vars", "10.0.0.50.yml")
        )
        for playbook in ("nginx/main.yml", "redis/main.yml"):
            plays = yaml.safe_load(
                open(
                    os.path.join(builder.ansible_dir, "services", "infra", playbook)
                ).read()
            )
            assert [p["hosts"] for p in plays] == ["infra"], playbook

    def test_services_host_that_is_a_node_is_in_both_groups(self, tmp_path):
        builder = self._builder(
            tmp_path,
            [
                _services_host(
                    "proxy",
                    "10.0.0.10",
                    nginx=NginxConfig(domain="test.example.com"),
                    status=StatusConfig(),
                )
            ],
        )
        groups = self._groups(builder)
        assert groups["nodes"] == ["10.0.0.1", "10.0.0.2", "10.0.0.10"]
        assert groups["proxy"] == ["10.0.0.10"]
        status_site = yaml.safe_load(
            open(
                os.path.join(
                    builder.ansible_dir, "services", "proxy", "status", "main.yml"
                )
            ).read()
        )
        assert status_site[0]["hosts"] == "proxy"


# ===========================================================================
# host_vars content
# ===========================================================================


class TestHostVars:
    def test_validator_host_vars_structure(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        path = os.path.join(builder.ansible_dir, "host_vars", "10.0.0.1.yml")
        with open(path) as f:
            data = yaml.safe_load(f)

        assert data["docker_container_name"] == "vnode1"
        assert data["docker_image_name"] == "transia/cluster:abc123"
        assert data["docker_network_name"] == "loadnet"
        assert data["ssh_port"] == 20
        assert isinstance(data["docker_container_ports"], list)
        assert len(data["docker_container_ports"]) == 5
        loopback = [
            p for p in data["docker_container_ports"] if p.startswith("127.0.0.1:")
        ]
        assert len(loopback) == 2

    def test_host_vars_has_env_variables(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        path = os.path.join(builder.ansible_dir, "host_vars", "10.0.0.1.yml")
        with open(path) as f:
            data = yaml.safe_load(f)

        env = data["docker_env_variables"]
        assert "RPC_PUBLIC" in env
        assert "RPC_ADMIN" in env
        assert "WS_PUBLIC" in env
        assert "WS_ADMIN" in env
        assert "PEER" in env

    def test_host_vars_has_volumes(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        path = os.path.join(builder.ansible_dir, "host_vars", "10.0.0.1.yml")
        with open(path) as f:
            data = yaml.safe_load(f)

        assert "/opt/ripple/config:/opt/ripple/config" in data["docker_volumes"]
        assert "/opt/ripple/config" in data["volumes"]

    def test_peer_host_vars(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        path = os.path.join(builder.ansible_dir, "host_vars", "10.0.0.10.yml")
        with open(path) as f:
            data = yaml.safe_load(f)

        assert data["docker_container_name"] == "pnode1"

    def test_config_path_set_correctly(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        path = os.path.join(builder.ansible_dir, "host_vars", "10.0.0.1.yml")
        with open(path) as f:
            data = yaml.safe_load(f)

        assert data["config_path"].endswith("/vnode1/config/")


# ===========================================================================
# run.sh content
# ===========================================================================


class TestRunSh:
    def test_has_run_once_function(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        content = open(os.path.join(builder.ansible_dir, "run.sh")).read()
        assert "run_once()" in content

    def test_has_run_always_function(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        content = open(os.path.join(builder.ansible_dir, "run.sh")).read()
        assert "run_always()" in content

    def test_deps_uses_run_once(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        content = open(os.path.join(builder.ansible_dir, "run.sh")).read()
        assert "run_once deps deps.yml" in content

    def test_main_uses_run_always(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        content = open(os.path.join(builder.ansible_dir, "run.sh")).read()
        assert "run_always main.yml" in content

    def test_registry_token_is_exported_not_passed_in_argv(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        content = open(os.path.join(builder.ansible_dir, "run.sh")).read()
        assert 'export AR_TOKEN="$(gcloud auth print-access-token' in content
        assert "ar_token" not in content
        assert "-e " not in content

    def test_has_force_flag(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        content = open(os.path.join(builder.ansible_dir, "run.sh")).read()
        assert "--force" in content

    def test_checks_done_marker(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        content = open(os.path.join(builder.ansible_dir, "run.sh")).read()
        assert ".done_" in content

    def test_no_optional_services_by_default(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        content = open(os.path.join(builder.ansible_dir, "run.sh")).read()
        assert "nginx" not in content
        assert "redis" not in content
        assert "faucet" not in content


# ===========================================================================
# Optional services: not created when not configured
# ===========================================================================


class TestOptionalServicesAbsent:
    def test_no_services_dir(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        assert not os.path.exists(os.path.join(builder.ansible_dir, "services"))

    def test_no_nginx_dir(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        assert not os.path.exists(os.path.join(builder.ansible_dir, "nginx"))

    def test_no_redis_dir(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        assert not os.path.exists(os.path.join(builder.ansible_dir, "redis"))

    def test_no_faucet_dir(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        assert not os.path.exists(os.path.join(builder.ansible_dir, "faucet"))

    def test_no_stream_dir(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        assert not os.path.exists(os.path.join(builder.ansible_dir, "stream"))

    def test_no_debug_dir(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        assert not os.path.exists(os.path.join(builder.ansible_dir, "debug"))

    def test_no_compiler_dir(self, tmp_path):
        builder = _build_basic(tmp_path)
        builder.write()
        assert not os.path.exists(os.path.join(builder.ansible_dir, "compiler"))


# ===========================================================================
# Optional: Nginx
# ===========================================================================


class TestNginx:
    def _builder_with_nginx(self, tmp_path):
        cluster_dir = str(tmp_path / "test-cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        config = AnsibleConfig(
            vips=["10.0.0.1"],
            pips=["10.0.0.10"],
            services=[
                ServicesHost(
                    name="proxy",
                    ip="10.0.0.10",
                    nginx=NginxConfig(
                        domain="test.example.com",
                        ssl_org="TestOrg",
                        ssl_ou="TestOU",
                    ),
                ),
            ],
        )
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc")
        builder.add_node(
            "vnode1",
            "10.0.0.1",
            _validator_ports(1),
            f"{cluster_dir}/vnode1/config/",
            "validator",
        )
        builder.add_node(
            "pnode1",
            "10.0.0.10",
            _peer_ports(1),
            f"{cluster_dir}/pnode1/config/",
            "peer",
        )
        return builder

    def test_creates_nginx_dir(self, tmp_path):
        builder = self._builder_with_nginx(tmp_path)
        builder.write()
        assert os.path.isdir(
            os.path.join(builder.ansible_dir, "services", "proxy", "nginx")
        )

    def test_creates_nginx_deps(self, tmp_path):
        builder = self._builder_with_nginx(tmp_path)
        builder.write()
        assert os.path.exists(
            os.path.join(builder.ansible_dir, "services", "proxy", "nginx", "deps.yml")
        )

    def test_creates_nginx_ssl(self, tmp_path):
        builder = self._builder_with_nginx(tmp_path)
        builder.write()
        assert os.path.exists(
            os.path.join(builder.ansible_dir, "services", "proxy", "nginx", "ssl.yml")
        )

    def test_creates_nginx_main(self, tmp_path):
        builder = self._builder_with_nginx(tmp_path)
        builder.write()
        assert os.path.exists(
            os.path.join(builder.ansible_dir, "services", "proxy", "nginx", "main.yml")
        )

    def test_creates_nginx_vars(self, tmp_path):
        builder = self._builder_with_nginx(tmp_path)
        builder.write()
        path = os.path.join(
            builder.ansible_dir, "services", "proxy", "nginx", "vars.yml"
        )
        assert os.path.exists(path)
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["SSL_CN"] == "test.example.com"
        assert data["SSL_O"] == "TestOrg"
        assert data["SSL_OU"] == "TestOU"
        assert data["RPC_SSL_CN"] == "rpc.test.example.com"
        assert data["FAUCET_SSL_CN"] == "faucet.test.example.com"

    def test_run_sh_includes_nginx(self, tmp_path):
        builder = self._builder_with_nginx(tmp_path)
        builder.write()
        content = open(os.path.join(builder.ansible_dir, "run.sh")).read()
        assert "services/proxy/nginx/deps.yml" in content
        assert "services/proxy/nginx/ssl.yml" in content
        assert "services/proxy/nginx/main.yml" in content

    def test_nginx_playbook_targets_host_group(self, tmp_path):
        builder = self._builder_with_nginx(tmp_path)
        builder.write()
        path = os.path.join(
            builder.ansible_dir, "services", "proxy", "nginx", "main.yml"
        )
        content = open(path).read()
        assert "hosts: proxy" in content

    def test_ssl_all_selfsigned_by_default(self, tmp_path):
        builder = self._builder_with_nginx(tmp_path)
        builder.write()
        path = os.path.join(
            builder.ansible_dir, "services", "proxy", "nginx", "ssl.yml"
        )
        content = open(path).read()
        assert "certbot" not in content
        for label in ("WSS", "RPC", "Faucet", "Debug", "Compiler"):
            assert f"Create {label} self-signed certificate" in content


class TestNginxLetsEncrypt:
    def _builder(self, tmp_path, **nginx_kwargs):
        cluster_dir = str(tmp_path / "test-cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        config = AnsibleConfig(
            vips=["10.0.0.1"],
            pips=["10.0.0.10"],
            services=[
                ServicesHost(
                    name="proxy",
                    ip="10.0.0.10",
                    nginx=NginxConfig(
                        domain="test.example.com",
                        ssl_org="TestOrg",
                        ssl_ou="TestOU",
                        **nginx_kwargs,
                    ),
                ),
            ],
        )
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc")
        builder.add_node(
            "vnode1",
            "10.0.0.1",
            _validator_ports(1),
            f"{cluster_dir}/vnode1/config/",
            "validator",
        )
        builder.add_node(
            "pnode1",
            "10.0.0.10",
            _peer_ports(1),
            f"{cluster_dir}/pnode1/config/",
            "peer",
        )
        return builder

    def test_ssl_yml_issues_letsencrypt_for_selected_services(self, tmp_path):
        builder = self._builder(
            tmp_path,
            letsencrypt_services=["rpc", "faucet"],
            letsencrypt_email="ops@example.com",
        )
        builder.write()
        path = os.path.join(
            builder.ansible_dir, "services", "proxy", "nginx", "ssl.yml"
        )
        content = open(path).read()
        assert "Install certbot" in content
        assert (
            "-m ops@example.com --cert-name rpc.test.example.com -d rpc.test.example.com"
            in content
        )
        assert (
            "--cert-name faucet.test.example.com -d faucet.test.example.com" in content
        )
        # LE services get no self-signed cert; the rest keep theirs
        assert "Create RPC self-signed certificate" not in content
        assert "Create Faucet self-signed certificate" not in content
        assert "Create WSS self-signed certificate" in content
        assert "Create Debug self-signed certificate" in content
        assert "Create Compiler self-signed certificate" in content

    def test_vars_point_at_letsencrypt_paths(self, tmp_path):
        builder = self._builder(tmp_path, letsencrypt_services=["rpc", "faucet"])
        builder.write()
        path = os.path.join(
            builder.ansible_dir, "services", "proxy", "nginx", "vars.yml"
        )
        with open(path) as f:
            data = yaml.safe_load(f)
        assert (
            data["RPC_SSL_CERT"]
            == "/etc/letsencrypt/live/rpc.test.example.com/fullchain.pem"
        )
        assert (
            data["RPC_SSL_KEY"]
            == "/etc/letsencrypt/live/rpc.test.example.com/privkey.pem"
        )
        assert (
            data["FAUCET_SSL_CERT"]
            == "/etc/letsencrypt/live/faucet.test.example.com/fullchain.pem"
        )
        assert (
            data["FAUCET_SSL_KEY"]
            == "/etc/letsencrypt/live/faucet.test.example.com/privkey.pem"
        )
        # untouched services keep self-signed paths
        assert data["SSL_CERT"] == "/etc/ssl/certs/test.example.com.csr.pem"
        assert data["DEBUG_SSL_CERT"] == "/etc/ssl/certs/debug.test.example.com.csr.pem"

    def test_no_email_registers_unsafely(self, tmp_path):
        builder = self._builder(tmp_path, letsencrypt_services=["rpc"])
        builder.write()
        path = os.path.join(
            builder.ansible_dir, "services", "proxy", "nginx", "ssl.yml"
        )
        content = open(path).read()
        assert "--register-unsafely-without-email" in content

    def test_wss_uses_bare_domain(self, tmp_path):
        builder = self._builder(tmp_path, letsencrypt_services=["wss"])
        builder.write()
        path = os.path.join(
            builder.ansible_dir, "services", "proxy", "nginx", "vars.yml"
        )
        with open(path) as f:
            data = yaml.safe_load(f)
        assert (
            data["SSL_CERT"] == "/etc/letsencrypt/live/test.example.com/fullchain.pem"
        )

    def test_unknown_service_raises(self, tmp_path):
        builder = self._builder(tmp_path, letsencrypt_services=["bogus"])
        with pytest.raises(ValueError, match="bogus"):
            builder.write()

    def test_ssl_yml_is_valid_yaml(self, tmp_path):
        builder = self._builder(
            tmp_path,
            letsencrypt_services=["rpc", "faucet"],
            letsencrypt_email="ops@example.com",
        )
        builder.write()
        path = os.path.join(
            builder.ansible_dir, "services", "proxy", "nginx", "ssl.yml"
        )
        with open(path) as f:
            plays = yaml.safe_load(f)
        assert plays[0]["hosts"] == "proxy"
        assert any("certbot" in str(t.get("command", "")) for t in plays[0]["tasks"])


# ===========================================================================
# Optional: Redis
# ===========================================================================


class TestRedis:
    def test_creates_redis_files(self, tmp_path):
        cluster_dir = str(tmp_path / "test-cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        config = AnsibleConfig(
            vips=["10.0.0.1"],
            pips=["10.0.0.10"],
            services=[
                ServicesHost(name="infra", ip="10.0.0.10", redis=RedisConfig()),
            ],
        )
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc")
        builder.add_node(
            "vnode1",
            "10.0.0.1",
            _validator_ports(1),
            f"{cluster_dir}/vnode1/config/",
            "validator",
        )
        builder.add_node(
            "pnode1",
            "10.0.0.10",
            _peer_ports(1),
            f"{cluster_dir}/pnode1/config/",
            "peer",
        )
        builder.write()

        assert os.path.exists(
            os.path.join(builder.ansible_dir, "services", "infra", "redis", "main.yml")
        )
        assert os.path.exists(
            os.path.join(builder.ansible_dir, "services", "infra", "redis", "vars.yml")
        )

        with open(
            os.path.join(builder.ansible_dir, "services", "infra", "redis", "vars.yml")
        ) as f:
            data = yaml.safe_load(f)
        assert data["docker_image_name"] == "redis"
        assert data["docker_container_name"] == "alpha-redis-main"

    def test_redis_playbook_targets_host_group(self, tmp_path):
        cluster_dir = str(tmp_path / "test-cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        config = AnsibleConfig(
            vips=["10.0.0.1"],
            pips=["10.0.0.10"],
            services=[ServicesHost(name="infra", ip="10.0.0.10", redis=RedisConfig())],
        )
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc")
        builder.add_node(
            "vnode1",
            "10.0.0.1",
            _validator_ports(1),
            f"{cluster_dir}/vnode1/config/",
            "validator",
        )
        builder.add_node(
            "pnode1",
            "10.0.0.10",
            _peer_ports(1),
            f"{cluster_dir}/pnode1/config/",
            "peer",
        )
        builder.write()
        content = open(
            os.path.join(builder.ansible_dir, "services", "infra", "redis", "main.yml")
        ).read()
        assert "hosts: infra" in content


# ===========================================================================
# Optional: Faucet
# ===========================================================================


class TestFaucet:
    def test_creates_faucet_files(self, tmp_path):
        cluster_dir = str(tmp_path / "test-cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        config = AnsibleConfig(
            vips=["10.0.0.1"],
            pips=["10.0.0.10"],
            services=[
                ServicesHost(
                    name="proxy",
                    ip="10.0.0.10",
                    faucet=FaucetConfig(
                        ws_url="wss://test.example.com",
                        network_id="21337",
                        seed="sEdTest",
                    ),
                ),
            ],
        )
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc")
        builder.add_node(
            "vnode1",
            "10.0.0.1",
            _validator_ports(1),
            f"{cluster_dir}/vnode1/config/",
            "validator",
        )
        builder.add_node(
            "pnode1",
            "10.0.0.10",
            _peer_ports(1),
            f"{cluster_dir}/pnode1/config/",
            "peer",
        )
        builder.write()

        assert os.path.exists(
            os.path.join(builder.ansible_dir, "services", "proxy", "faucet", "main.yml")
        )
        with open(
            os.path.join(builder.ansible_dir, "services", "proxy", "faucet", "vars.yml")
        ) as f:
            data = yaml.safe_load(f)
        assert (
            data["docker_env_variables"]["XRPL_FAUCET_URL"] == "wss://test.example.com"
        )
        assert data["docker_env_variables"]["XRPL_NETWORK_ID"] == "21337"


# ===========================================================================
# Optional: Stream
# ===========================================================================


class TestStream:
    def test_creates_stream_files(self, tmp_path):
        cluster_dir = str(tmp_path / "test-cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        config = AnsibleConfig(
            vips=["10.0.0.1"],
            pips=["10.0.0.10"],
            services=[
                ServicesHost(
                    name="proxy",
                    ip="10.0.0.10",
                    stream=StreamConfig(port=1400, container_name="pnode1"),
                ),
            ],
        )
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc")
        builder.add_node(
            "vnode1",
            "10.0.0.1",
            _validator_ports(1),
            f"{cluster_dir}/vnode1/config/",
            "validator",
        )
        builder.add_node(
            "pnode1",
            "10.0.0.10",
            _peer_ports(1),
            f"{cluster_dir}/pnode1/config/",
            "peer",
        )
        builder.write()

        assert os.path.exists(
            os.path.join(builder.ansible_dir, "services", "proxy", "stream", "main.yml")
        )
        with open(
            os.path.join(builder.ansible_dir, "services", "proxy", "stream", "vars.yml")
        ) as f:
            data = yaml.safe_load(f)
        assert data["websocketd_port"] == 1400
        assert data["docker_container_name"] == "pnode1"


# ===========================================================================
# Optional: Debug
# ===========================================================================


class TestDebug:
    def test_creates_debug_files(self, tmp_path):
        cluster_dir = str(tmp_path / "test-cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        config = AnsibleConfig(
            vips=["10.0.0.1"],
            pips=["10.0.0.10"],
            services=[
                ServicesHost(
                    name="proxy",
                    ip="10.0.0.10",
                    stream=StreamConfig(port=1400),
                    debug=DebugConfig(endpoint="ws://10.0.0.10:1400/"),
                ),
            ],
        )
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc")
        builder.add_node(
            "vnode1",
            "10.0.0.1",
            _validator_ports(1),
            f"{cluster_dir}/vnode1/config/",
            "validator",
        )
        builder.add_node(
            "pnode1",
            "10.0.0.10",
            _peer_ports(1),
            f"{cluster_dir}/pnode1/config/",
            "peer",
        )
        builder.write()

        assert os.path.exists(
            os.path.join(builder.ansible_dir, "services", "proxy", "debug", "main.yml")
        )
        with open(
            os.path.join(builder.ansible_dir, "services", "proxy", "debug", "vars.yml")
        ) as f:
            data = yaml.safe_load(f)
        assert data["docker_image_name"] == "transia/debugstream"
        assert data["docker_env_variables"]["ENDPOINT"] == "ws://10.0.0.10:1400/"


# ===========================================================================
# Optional: Compiler
# ===========================================================================


class TestCompiler:
    def test_creates_compiler_files(self, tmp_path):
        cluster_dir = str(tmp_path / "test-cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        config = AnsibleConfig(
            vips=["10.0.0.1"],
            pips=["10.0.0.10"],
            services=[
                ServicesHost(name="infra", ip="10.0.0.10", compiler=CompilerConfig()),
            ],
        )
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc")
        builder.add_node(
            "vnode1",
            "10.0.0.1",
            _validator_ports(1),
            f"{cluster_dir}/vnode1/config/",
            "validator",
        )
        builder.add_node(
            "pnode1",
            "10.0.0.10",
            _peer_ports(1),
            f"{cluster_dir}/pnode1/config/",
            "peer",
        )
        builder.write()

        assert os.path.exists(
            os.path.join(
                builder.ansible_dir, "services", "infra", "compiler", "main.yml"
            )
        )
        with open(
            os.path.join(
                builder.ansible_dir, "services", "infra", "compiler", "vars.yml"
            )
        ) as f:
            data = yaml.safe_load(f)
        assert data["docker_image_name"] == "transia/compiler-api:latest"
        assert (
            data["compiler_repo"]
            == "git@github.com:Transia-RnD/xrpl-hooks-compiler.git"
        )


# ===========================================================================
# Full deployment with all services (multi-host)
# ===========================================================================


class TestFullDeployment:
    def test_all_services_enabled(self, tmp_path):
        cluster_dir = str(tmp_path / "full-cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        config = AnsibleConfig(
            ssh_port=1988,
            ssh_user="root",
            ssh_key_path="~/.ssh/xrpl-labs",
            vips=["79.110.60.99", "79.110.60.100", "79.110.60.101"],
            pips=["79.110.60.105"],
            services=[
                ServicesHost(
                    name="proxy",
                    ip="79.110.60.105",
                    nginx=NginxConfig(
                        domain="alphanet.xrpl.org",
                        ssl_org="Transia LLC.",
                        ssl_ou="Transia RnD",
                    ),
                    faucet=FaucetConfig(
                        ws_url="wss://alphanet.xrpl.org",
                        network_id="21565",
                        seed="sEdTest",
                    ),
                    stream=StreamConfig(),
                    debug=DebugConfig(),
                ),
                ServicesHost(
                    name="infra",
                    ip="79.110.60.105",
                    redis=RedisConfig(),
                    compiler=CompilerConfig(),
                ),
            ],
        )
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc123")
        for i, ip in enumerate(config.vips, 1):
            builder.add_node(
                f"vnode{i}",
                ip,
                _validator_ports(i),
                f"{cluster_dir}/vnode{i}/config/",
                "validator",
            )
        for i, ip in enumerate(config.pips, 1):
            builder.add_node(
                f"pnode{i}",
                ip,
                _peer_ports(i),
                f"{cluster_dir}/pnode{i}/config/",
                "peer",
            )
        builder.write()

        run_sh = open(os.path.join(builder.ansible_dir, "run.sh")).read()
        assert "services/proxy/nginx/deps.yml" in run_sh
        assert "services/proxy/nginx/ssl.yml" in run_sh
        assert "services/proxy/nginx/main.yml" in run_sh
        assert "services/proxy/faucet/main.yml" in run_sh
        assert "services/proxy/stream/main.yml" in run_sh
        assert "services/proxy/debug/main.yml" in run_sh
        assert "services/infra/redis/main.yml" in run_sh
        assert "services/infra/compiler/main.yml" in run_sh

        for svc in ["nginx", "faucet", "stream", "debug"]:
            assert os.path.isdir(
                os.path.join(builder.ansible_dir, "services", "proxy", svc)
            )
        for svc in ["redis", "compiler"]:
            assert os.path.isdir(
                os.path.join(builder.ansible_dir, "services", "infra", svc)
            )

        hosts = open(os.path.join(builder.ansible_dir, "hosts.txt")).read()
        assert "ansible_port=1988" in hosts
        assert "ansible_user=root" in hosts
        assert "ansible_ssh_private_key_file=~/.ssh/xrpl-labs" in hosts
        assert "79.110.60.99" in hosts
        assert "79.110.60.105" in hosts
        assert "[proxy]" in hosts
        assert "[infra]" in hosts

    def test_single_host_all_services(self, tmp_path):
        cluster_dir = str(tmp_path / "single-host")
        os.makedirs(cluster_dir, exist_ok=True)
        config = AnsibleConfig(
            vips=["10.0.0.1"],
            pips=["10.0.0.10"],
            services=[
                ServicesHost(
                    name="main",
                    ip="10.0.0.10",
                    nginx=NginxConfig(domain="test.example.com"),
                    redis=RedisConfig(),
                    faucet=FaucetConfig(
                        ws_url="wss://test.example.com",
                        network_id="21337",
                        seed="sEdTest",
                    ),
                    stream=StreamConfig(),
                    debug=DebugConfig(),
                    compiler=CompilerConfig(),
                ),
            ],
        )
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc")
        builder.add_node(
            "vnode1",
            "10.0.0.1",
            _validator_ports(1),
            f"{cluster_dir}/vnode1/config/",
            "validator",
        )
        builder.add_node(
            "pnode1",
            "10.0.0.10",
            _peer_ports(1),
            f"{cluster_dir}/pnode1/config/",
            "peer",
        )
        builder.write()

        for svc in ["nginx", "redis", "faucet", "stream", "debug", "compiler"]:
            assert os.path.isdir(
                os.path.join(builder.ansible_dir, "services", "main", svc)
            )

        run_sh = open(os.path.join(builder.ansible_dir, "run.sh")).read()
        for svc in ["nginx", "redis", "faucet", "stream", "debug", "compiler"]:
            assert f"services/main/{svc}/main.yml" in run_sh


# ===========================================================================
# Chaining
# ===========================================================================


class TestChaining:
    def test_add_node_returns_self(self, tmp_path):
        cluster_dir = str(tmp_path / "test-cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        config = _basic_config()
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc")
        result = builder.add_node(
            "vnode1", "10.0.0.1", _validator_ports(1), f"{cluster_dir}/vnode1/config/"
        )
        assert result is builder


# ---------------------------------------------------------------------------
# Genesis vs rolling main.yml
# ---------------------------------------------------------------------------


class TestMainYmlGenesisModes:
    def _main_yml(self, tmp_path, genesis: bool) -> str:
        cluster_dir = str(tmp_path / "cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        builder = AnsibleBuilder(
            cluster_dir=cluster_dir,
            config=_basic_config(),
            image_name="transia/cluster:abc123",
            genesis=genesis,
        )
        builder.add_node(
            "vnode1",
            "10.0.0.1",
            _validator_ports(1),
            f"{cluster_dir}/vnode1/config/",
            "validator",
        )
        builder.write()
        with open(os.path.join(builder.ansible_dir, "main.yml")) as f:
            return f.read()

    def test_genesis_main_yml_resets_state(self, tmp_path):
        content = self._main_yml(tmp_path, genesis=True)
        assert "rm -rf /var/lib/xrpld/db/*" in content
        assert "docker system prune" in content
        assert "serial:" not in content

    def test_genesis_main_yml_is_valid_yaml(self, tmp_path):
        plays = yaml.safe_load(self._main_yml(tmp_path, genesis=True))
        assert plays[0]["hosts"] == "nodes"
        names = [t["name"] for t in plays[0]["tasks"]]
        assert "Deploy Docker Image" in names

    def test_rolling_main_yml_preserves_state(self, tmp_path):
        content = self._main_yml(tmp_path, genesis=False)
        assert "rm -rf /var/lib/xrpld/db/*" not in content
        assert "docker system prune" not in content
        assert "Delete folders" not in content

    def test_rolling_main_yml_is_serial_and_valid(self, tmp_path):
        plays = yaml.safe_load(self._main_yml(tmp_path, genesis=False))
        assert plays[0]["serial"] == 1
        names = [t["name"] for t in plays[0]["tasks"]]
        assert "Deploy Docker Image" in names
        assert "restart docker" not in names

    @pytest.mark.parametrize("genesis", [True, False])
    def test_registry_login_reads_the_token_from_the_environment(
        self, tmp_path, genesis
    ):
        content = self._main_yml(tmp_path, genesis=genesis)
        assert 'echo "{{ ar_token }}"' not in content
        assert "ar_token" not in content
        plays = yaml.safe_load(content)
        login = next(
            t
            for t in plays[0]["tasks"]
            if t["name"].startswith("Authenticate Docker to Artifact Registry")
        )
        assert login["no_log"] is True
        assert login["environment"] == {"AR_TOKEN": "{{ lookup('env', 'AR_TOKEN') }}"}
        assert "printf '%s' \"$AR_TOKEN\" | docker login" in login["shell"]
        assert "--password-stdin" in login["shell"]
        assert login["when"] == "lookup('env', 'AR_TOKEN') | length > 0"

    def test_default_is_genesis(self, tmp_path):
        cluster_dir = str(tmp_path / "cluster-default")
        os.makedirs(cluster_dir, exist_ok=True)
        builder = AnsibleBuilder(cluster_dir, _basic_config(), "transia/cluster:abc")
        assert builder.genesis is True


# ===========================================================================
# Per-node SSH keys (drill access handed out and revoked one node at a time)
# ===========================================================================


class TestPerNodeSshKeys:
    def _builder(self, tmp_path, ssh_keys):
        cluster_dir = str(tmp_path / "keys-cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        config = AnsibleConfig(
            ssh_port=1988,
            ssh_user="root",
            ssh_key_path="~/.ssh/xrpl-labs",
            vips=["10.0.0.1", "10.0.0.2"],
            ssh_keys=ssh_keys,
        )
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc123")
        builder.add_node(
            "vnode1", "10.0.0.1", _validator_ports(1), f"{cluster_dir}/vnode1/config/"
        )
        builder.add_node(
            "vnode2", "10.0.0.2", _validator_ports(2), f"{cluster_dir}/vnode2/config/"
        )
        return builder

    def test_each_node_gets_its_own_key(self, tmp_path):
        builder = self._builder(
            tmp_path,
            {
                "10.0.0.1": "~/.ssh/alphanet/vnode1",
                "10.0.0.2": "~/.ssh/alphanet/vnode2",
            },
        )
        builder.write()
        hosts = open(os.path.join(builder.ansible_dir, "hosts.txt")).read()
        assert (
            "10.0.0.1 ansible_port=1988 ansible_user=root ansible_ssh_private_key_file=~/.ssh/alphanet/vnode1"
            in hosts
        )
        assert (
            "10.0.0.2 ansible_port=1988 ansible_user=root ansible_ssh_private_key_file=~/.ssh/alphanet/vnode2"
            in hosts
        )

    def test_unlisted_node_falls_back_to_cluster_key(self, tmp_path):
        builder = self._builder(tmp_path, {"10.0.0.1": "~/.ssh/alphanet/vnode1"})
        builder.write()
        hosts = open(os.path.join(builder.ansible_dir, "hosts.txt")).read()
        assert (
            "10.0.0.2 ansible_port=1988 ansible_user=root ansible_ssh_private_key_file=~/.ssh/xrpl-labs"
            in hosts
        )

    def test_no_overrides_keeps_one_key_everywhere(self, tmp_path):
        builder = self._builder(tmp_path, {})
        builder.write()
        hosts = open(os.path.join(builder.ansible_dir, "hosts.txt")).read()
        assert hosts.count("ansible_ssh_private_key_file=~/.ssh/xrpl-labs") == 2


# ===========================================================================
# Alloy telemetry sidecar
# ===========================================================================


class TestAlloySidecar:
    def _builder(self, tmp_path, alloy):
        cluster_dir = str(tmp_path / "alloy-cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        config = AnsibleConfig(
            ssh_port=1988,
            ssh_user="root",
            ssh_key_path="~/.ssh/xrpl-labs",
            vips=["10.0.0.1"],
            alloy=alloy,
        )
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc123")
        builder.add_node(
            "vnode1", "10.0.0.1", _validator_ports(1), f"{cluster_dir}/vnode1/config/"
        )
        return builder

    def _alloy(self, tmp_path, **kw):
        source = tmp_path / "alloy-src" / "docker"
        source.mkdir(parents=True, exist_ok=True)
        (source / "alloy.Dockerfile").write_text("FROM grafana/alloy\n")
        defaults = dict(
            push_host="xrpl-monitoring-push-staging.aws.peersyst.tech",
            username="alphanet",
            password="s3cret",
            source_dir=str(tmp_path / "alloy-src"),
            node_label_prefix="alphanet-",
        )
        defaults.update(kw)
        return AlloyConfig(**defaults)

    def test_no_alloy_yml_when_unconfigured(self, tmp_path):
        builder = self._builder(tmp_path, None)
        builder.write()
        assert not os.path.exists(os.path.join(builder.ansible_dir, "alloy.yml"))

    def test_alloy_yml_written(self, tmp_path):
        builder = self._builder(tmp_path, self._alloy(tmp_path))
        builder.write()
        content = open(os.path.join(builder.ansible_dir, "alloy.yml")).read()
        assert 'network_mode: "container:{{ docker_container_name }}"' in content

    def test_build_context_copied(self, tmp_path):
        builder = self._builder(tmp_path, self._alloy(tmp_path))
        builder.write()
        copied = os.path.join(
            builder.ansible_dir, "alloy", "docker", "alloy.Dockerfile"
        )
        assert os.path.exists(copied)

    def test_host_vars_carry_credentials_and_node_label(self, tmp_path):
        builder = self._builder(tmp_path, self._alloy(tmp_path))
        builder.write()
        import yaml

        data = yaml.safe_load(
            open(os.path.join(builder.ansible_dir, "host_vars", "10.0.0.1.yml"))
        )
        env = data["alloy_env_variables"]
        assert env["ALLOY_NODE"] == "alphanet-vnode1"
        assert (
            env["ALLOY_PUSH_HOST"] == "xrpl-monitoring-push-staging.aws.peersyst.tech"
        )
        assert env["ALLOY_USERNAME"] == "alphanet"
        assert env["ALLOY_PASSWORD"] == "s3cret"

    def test_statsd_addresses_match_the_node_loopback(self, tmp_path):
        """Sidecar shares the node netns, so listen and [insight] address are identical."""
        builder = self._builder(tmp_path, self._alloy(tmp_path, statsd_port=19125))
        builder.write()
        import yaml

        env = yaml.safe_load(
            open(os.path.join(builder.ansible_dir, "host_vars", "10.0.0.1.yml"))
        )["alloy_env_variables"]
        assert env["ALLOY_STATSD_LISTEN"] == "127.0.0.1:19125"
        assert env["ALLOY_RIPPLED_STATSD_ADDRESS"] == "127.0.0.1:19125"

    def test_run_sh_runs_alloy_after_main(self, tmp_path):
        builder = self._builder(tmp_path, self._alloy(tmp_path))
        builder.write()
        run = open(os.path.join(builder.ansible_dir, "run.sh")).read()
        assert "run_always alloy.yml" in run
        assert run.index("run_always main.yml") < run.index("run_always alloy.yml")

    def test_clean_removes_the_sidecar(self, tmp_path):
        builder = self._builder(tmp_path, self._alloy(tmp_path))
        builder.write()
        clean = open(os.path.join(builder.ansible_dir, "clean.yml")).read()
        assert "alloy_container_name is defined" in clean

    def test_inspect_template_is_raw_for_the_ansible_templater(self, tmp_path):
        builder = self._builder(tmp_path, self._alloy(tmp_path))
        builder.write()
        content = open(os.path.join(builder.ansible_dir, "alloy.yml")).read()
        assert "{{{{" not in content
        assert "{% raw %}{{ .State.Status }}{% endraw %}" in content
        plays = yaml.safe_load(content)
        report = next(
            t for t in plays[0]["tasks"] if t.get("name") == "Report the sidecar state"
        )
        assert report["command"] == (
            "docker inspect -f '{% raw %}{{ .State.Status }}{% endraw %}' "
            '"{{ alloy_container_name }}"'
        )

    def test_per_node_credentials_override_the_cluster_pair(self, tmp_path):
        alloy = self._alloy(
            tmp_path,
            credentials={
                "vnode1": {"username": "alphanet-1", "password": "code-1"},
            },
        )
        builder = self._builder(tmp_path, alloy)
        builder.write()
        import yaml

        env = yaml.safe_load(
            open(os.path.join(builder.ansible_dir, "host_vars", "10.0.0.1.yml"))
        )["alloy_env_variables"]
        assert env["ALLOY_USERNAME"] == "alphanet-1"
        assert env["ALLOY_PASSWORD"] == "code-1"

    def test_unlisted_node_uses_the_cluster_pair(self, tmp_path):
        alloy = self._alloy(
            tmp_path,
            credentials={
                "vnode9": {"username": "other", "password": "nope"},
            },
        )
        builder = self._builder(tmp_path, alloy)
        builder.write()
        import yaml

        env = yaml.safe_load(
            open(os.path.join(builder.ansible_dir, "host_vars", "10.0.0.1.yml"))
        )["alloy_env_variables"]
        assert env["ALLOY_USERNAME"] == "alphanet"


# ===========================================================================
# Publisher-list (UNL) vhost
# ===========================================================================


class TestVlVhost:
    def _builder(self, tmp_path, vl, le=None):
        cluster_dir = str(tmp_path / "vl-cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        if vl is not None:
            vl_src = os.path.join(cluster_dir, "vl")
            os.makedirs(vl_src, exist_ok=True)
            with open(os.path.join(vl_src, "vl.json"), "w") as f:
                f.write('{"public_key":"ED_PUB","blob":"eyJ2YWxpZGF0b3JzIjpbXX0="}')
        nginx = NginxConfig(
            domain="alphanet.xrpl.org",
            letsencrypt_services=le or [],
            letsencrypt_email="ops@example.com",
        )
        config = AnsibleConfig(
            ssh_port=1988,
            ssh_user="root",
            ssh_key_path="~/.ssh/xrpl-labs",
            vips=["10.0.0.1"],
            pips=["10.0.0.10"],
            services=[ServicesHost(name="peer", ip="10.0.0.10", nginx=nginx, vl=vl)],
        )
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc123")
        builder.add_node(
            "vnode1", "10.0.0.1", _validator_ports(1), f"{cluster_dir}/vnode1/config/"
        )
        builder.add_node(
            "pnode1",
            "10.0.0.10",
            _peer_ports(1),
            f"{cluster_dir}/pnode1/config/",
            "peer",
        )
        return builder

    def _dir(self, builder):
        return os.path.join(builder.ansible_dir, "services", "peer", "vl")

    def test_not_written_when_unconfigured(self, tmp_path):
        builder = self._builder(tmp_path, None)
        builder.write()
        assert not os.path.isdir(self._dir(builder))

    def test_signed_list_is_staged_beside_the_playbook(self, tmp_path):
        builder = self._builder(tmp_path, VlConfig())
        builder.write()
        staged = os.path.join(self._dir(builder), "vl.json")
        assert os.path.exists(staged)
        assert "ED_PUB" in open(staged).read()

    def test_missing_list_fails_loudly(self, tmp_path):
        """A 404 VL site looks to every node like an unreachable publisher."""
        cluster_dir = str(tmp_path / "empty-cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        config = AnsibleConfig(
            ssh_port=1988,
            ssh_user="root",
            vips=["10.0.0.1"],
            pips=["10.0.0.10"],
            services=[
                ServicesHost(
                    name="peer",
                    ip="10.0.0.10",
                    nginx=NginxConfig(domain="alphanet.xrpl.org"),
                    vl=VlConfig(),
                )
            ],
        )
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc")
        builder.add_node(
            "pnode1",
            "10.0.0.10",
            _peer_ports(1),
            f"{cluster_dir}/pnode1/config/",
            "peer",
        )
        with pytest.raises(FileNotFoundError):
            builder.write()

    def test_hostname_is_vl_subdomain(self, tmp_path):
        builder = self._builder(tmp_path, VlConfig())
        builder.write()
        import yaml

        v = yaml.safe_load(open(os.path.join(self._dir(builder), "vars.yml")))
        assert v["VL_SSL_CN"] == "vl.alphanet.xrpl.org"

    def test_http_only_without_letsencrypt(self, tmp_path):
        builder = self._builder(tmp_path, VlConfig())
        builder.write()
        import yaml

        v = yaml.safe_load(open(os.path.join(self._dir(builder), "vars.yml")))
        assert v["VL_TLS"] is False
        assert "VL_SSL_CERT" not in v

    def test_tls_block_when_letsencrypt_covers_vl(self, tmp_path):
        builder = self._builder(tmp_path, VlConfig(), le=["vl"])
        builder.write()
        import yaml

        v = yaml.safe_load(open(os.path.join(self._dir(builder), "vars.yml")))
        assert v["VL_TLS"] is True
        assert (
            v["VL_SSL_CERT"]
            == "/etc/letsencrypt/live/vl.alphanet.xrpl.org/fullchain.pem"
        )

    def test_vl_is_a_valid_letsencrypt_service(self, tmp_path):
        """`vl` must be accepted in letsencrypt_services, not rejected as unknown."""
        builder = self._builder(tmp_path, VlConfig(), le=["vl", "rpc"])
        builder.write()
        ssl = open(
            os.path.join(builder.ansible_dir, "services", "peer", "nginx", "ssl.yml")
        ).read()
        assert "vl.alphanet.xrpl.org" in ssl

    def test_run_sh_always_reruns_vl(self, tmp_path):
        builder = self._builder(tmp_path, VlConfig())
        builder.write()
        run = open(os.path.join(builder.ansible_dir, "run.sh")).read()
        assert "run_always services/peer/vl/main.yml" in run

    def test_playbook_verifies_what_it_served(self, tmp_path):
        builder = self._builder(tmp_path, VlConfig())
        builder.write()
        main = open(os.path.join(self._dir(builder), "main.yml")).read()
        assert "nginx -t" in main
        assert "public_key" in main


# ===========================================================================
# Status sampler + network roll-up
# ===========================================================================


class TestStatusService:
    def _builder(self, tmp_path, status, debug=None, redis=None):
        cluster_dir = str(tmp_path / "status-cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        nginx = NginxConfig(domain="alphanet.xrpl.org")
        config = AnsibleConfig(
            ssh_port=1988,
            ssh_user="root",
            ssh_key_path="~/.ssh/xrpl-labs",
            vips=["10.0.0.1", "10.0.0.2"],
            pips=["10.0.0.10"],
            services=[
                ServicesHost(
                    name="pnode1",
                    ip="10.0.0.10",
                    nginx=nginx,
                    debug=debug,
                    redis=redis,
                    status=status,
                )
            ],
        )
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc123")
        builder.add_node(
            "vnode1", "10.0.0.1", _validator_ports(1), f"{cluster_dir}/vnode1/config/"
        )
        builder.add_node(
            "vnode2", "10.0.0.2", _validator_ports(2), f"{cluster_dir}/vnode2/config/"
        )
        builder.add_node(
            "pnode1",
            "10.0.0.10",
            _peer_ports(1),
            f"{cluster_dir}/pnode1/config/",
            "peer",
        )
        return builder

    def _host_vars(self, builder, ip):
        return yaml.safe_load(
            open(os.path.join(builder.ansible_dir, "host_vars", f"{ip}.yml"))
        )

    def _nginx_main(self, builder):
        return open(
            os.path.join(builder.ansible_dir, "services", "pnode1", "nginx", "main.yml")
        ).read()

    def test_nothing_written_when_unconfigured(self, tmp_path):
        builder = self._builder(tmp_path, None)
        builder.write()
        assert not os.path.exists(os.path.join(builder.ansible_dir, "status.yml"))
        assert not os.path.isdir(os.path.join(builder.ansible_dir, "status"))
        assert "status_env" not in self._host_vars(builder, "10.0.0.1")
        assert "/status/" not in self._nginx_main(builder)
        assert (
            "status.yml" not in open(os.path.join(builder.ansible_dir, "run.sh")).read()
        )

    def test_sampler_files_staged_beside_the_playbook(self, tmp_path):
        builder = self._builder(tmp_path, StatusConfig())
        builder.write()
        staged = os.path.join(builder.ansible_dir, "status")
        assert sorted(os.listdir(staged)) == [
            "network-dashboard.html",
            "node-dashboard.html",
            "node_metrics.py",
        ]
        assert "def decode_xdgm" in open(os.path.join(staged, "node_metrics.py")).read()

    def test_status_yml_installs_unit_env_and_firewall_on_every_node(self, tmp_path):
        builder = self._builder(tmp_path, StatusConfig())
        builder.write()
        content = open(os.path.join(builder.ansible_dir, "status.yml")).read()
        plays = yaml.safe_load(content)
        assert plays[0]["hosts"] == "nodes"
        names = [t.get("name") for t in plays[0]["tasks"]]
        assert "Install the sampler" in names
        assert "Write the sampler environment" in names
        assert "Install the xrpld-status systemd unit" in names
        assert "Allow the services host to reach the sampler" in names
        assert "Refuse the sampler port from anywhere else" in names
        assert "Allow XDGM datagrams from the node container network" in names
        assert "Enable and start xrpld-status" in names
        assert "dest: /opt/xrpld-status/node_metrics.py" in content
        assert "ExecStart=/usr/bin/python3 /opt/xrpld-status/node_metrics.py" in content
        assert "EnvironmentFile=/opt/xrpld-status/node_metrics.env" in content
        assert "notify: restart xrpld-status" in content

    def test_remote_node_binds_all_addresses_and_admits_only_the_services_host(
        self, tmp_path
    ):
        builder = self._builder(tmp_path, StatusConfig(port=8700))
        builder.write()
        hv = self._host_vars(builder, "10.0.0.1")
        env = hv["status_env"]
        assert env["NODE_METRICS_HTTP_HOST"] == "0.0.0.0"
        assert env["NODE_METRICS_HTTP_PORT"] == "8700"
        assert (
            env["NODE_METRICS_ADMIN_RPC"]
            == f"http://127.0.0.1:{_validator_ports(1).rpc_admin}/"
        )
        assert env["NODE_METRICS_DEBUGSTREAM_HEALTH"] == ""
        assert env["NODE_METRICS_REDIS_PORT"] == "0"
        assert "NODE_METRICS_NETWORK_NODES" not in env
        assert hv["status_allow_from"] == "10.0.0.10"
        assert hv["status_http_port"] == 8700

    def test_xdgm_listener_binds_every_interface(self, tmp_path):
        builder = self._builder(tmp_path, StatusConfig(xdgm_port=9998))
        builder.write()
        hv = self._host_vars(builder, "10.0.0.2")
        assert hv["status_env"]["NODE_METRICS_XDGM_HOST"] == "0.0.0.0"
        assert hv["status_env"]["NODE_METRICS_XDGM_PORT"] == "9998"
        assert hv["status_xdgm_port"] == 9998

    def test_services_host_sampler_is_loopback_and_aggregates(self, tmp_path):
        builder = self._builder(
            tmp_path, StatusConfig(), debug=DebugConfig(port=8081), redis=RedisConfig()
        )
        builder.write()
        hv = self._host_vars(builder, "10.0.0.10")
        env = hv["status_env"]
        assert env["NODE_METRICS_HTTP_HOST"] == "127.0.0.1"
        assert hv["status_allow_from"] == ""
        assert env["NODE_METRICS_NETWORK_NODES"] == (
            "vnode1=http://10.0.0.1:8687 role=validator,"
            "vnode2=http://10.0.0.2:8687 role=validator,"
            "pnode1=http://127.0.0.1:8687 role=peer"
        )
        assert env["NODE_METRICS_NETWORK_FILE"] == "/opt/xrpld-status/network.json"
        assert env["NODE_METRICS_NETWORK_NAME"] == "alphanet.xrpl.org"
        assert env["NODE_METRICS_DEBUGSTREAM_HEALTH"] == "http://127.0.0.1:8081/health"
        assert env["NODE_METRICS_REDIS_PORT"] == "6379"

    def test_network_name_override(self, tmp_path):
        builder = self._builder(tmp_path, StatusConfig(network_name="alphanet"))
        builder.write()
        assert (
            self._host_vars(builder, "10.0.0.10")["status_env"][
                "NODE_METRICS_NETWORK_NAME"
            ]
            == "alphanet"
        )

    def test_nginx_serves_the_page_the_api_and_every_node(self, tmp_path):
        builder = self._builder(tmp_path, StatusConfig())
        builder.write()
        main = self._nginx_main(builder)
        yaml.safe_load(main)
        assert (
            "limit_req_zone $binary_remote_addr zone=xrpld_status:10m rate=20r/s;"
            in main
        )
        assert "location = /status/ {" in main
        assert "try_files /network-dashboard.html =404;" in main
        assert "location /status/api/ {" in main
        assert "proxy_pass http://127.0.0.1:8687/api/;" in main
        assert "location /status/nodes/vnode1/ {" in main
        assert "proxy_pass http://10.0.0.1:8687/;" in main
        assert "location /status/nodes/vnode2/ {" in main
        assert "location /status/nodes/pnode1/ {" in main
        assert "proxy_pass http://127.0.0.1:8687/;" in main
        assert main.count("limit_req zone=xrpld_status burst=40 nodelay;") == 5
        # The locations sit inside the bare-domain server block, before its closing brace.
        wss = main[
            main.index("Write WSS proxy config") : main.index(
                "  - name: Link WSS proxy"
            )
        ]
        assert "location /status/api/" in wss
        assert wss.rstrip().endswith("}")

    def test_services_host_playbook_installs_the_page_and_seeds_network_json(
        self, tmp_path
    ):
        builder = self._builder(tmp_path, StatusConfig())
        builder.write()
        svc_dir = os.path.join(builder.ansible_dir, "services", "pnode1", "status")
        main = open(os.path.join(svc_dir, "main.yml")).read()
        plays = yaml.safe_load(main)
        assert plays[0]["hosts"] == "pnode1"
        assert "www/network-dashboard.html" in main
        assert "force: no" in main
        assert "/api/network" in main
        v = yaml.safe_load(open(os.path.join(svc_dir, "vars.yml")))
        assert v["STATUS_DIR"] == "/opt/xrpld-status"
        assert v["STATUS_PORT"] == 8687

    def test_run_sh_reruns_the_sampler_after_main_and_the_page_after_nginx(
        self, tmp_path
    ):
        builder = self._builder(tmp_path, StatusConfig())
        builder.write()
        run = open(os.path.join(builder.ansible_dir, "run.sh")).read()
        assert run.index("run_always main.yml") < run.index("run_always status.yml")
        assert run.index(
            "run_once pnode1_nginx services/pnode1/nginx/main.yml"
        ) < run.index("run_always services/pnode1/status/main.yml")

    def test_clean_stops_the_sampler(self, tmp_path):
        builder = self._builder(tmp_path, StatusConfig())
        builder.write()
        clean = open(os.path.join(builder.ansible_dir, "clean.yml")).read()
        assert "name: xrpld-status" in clean
        assert "status_http_port is defined" in clean

    def test_status_host_must_be_a_node(self, tmp_path):
        cluster_dir = str(tmp_path / "infra-cluster")
        os.makedirs(cluster_dir, exist_ok=True)
        config = AnsibleConfig(
            vips=["10.0.0.1"],
            services=[
                ServicesHost(
                    name="infra",
                    ip="10.0.0.50",
                    nginx=NginxConfig(domain="example.com"),
                    status=StatusConfig(),
                )
            ],
        )
        builder = AnsibleBuilder(cluster_dir, config, "transia/cluster:abc")
        builder.add_node(
            "vnode1", "10.0.0.1", _validator_ports(1), f"{cluster_dir}/vnode1/config/"
        )
        with pytest.raises(ValueError):
            builder.write()


# ===========================================================================
# Template brace hygiene
# ===========================================================================


class TestTemplateBraces:
    """A template written with _file_write reaches Ansible verbatim, so a doubled
    brace is a Jinja syntax error there; a template passed through str.format()
    needs every Jinja brace doubled, so a bare one is a KeyError or a lost var."""

    VERBATIM = [
        "_DEPS_YML",
        "_MAIN_HEADER",
        "_MAIN_HEADER_ROLLING",
        "_MAIN_RESET_TASKS",
        "_MAIN_DEPLOY_TASKS",
        "_ALLOY_YML",
        "_CLEAN_YML",
        "_STATUS_YML",
    ]
    FORMATTED = [
        "_NGINX_DEPS_TPL",
        "_NGINX_SSL_HEADER_TPL",
        "_NGINX_MAIN_TPL",
        "_VL_MAIN_TPL",
        "_REDIS_MAIN_TPL",
        "_FAUCET_MAIN_TPL",
        "_STREAM_MAIN_TPL",
        "_DEBUG_MAIN_TPL",
        "_COMPILER_MAIN_TPL",
        "_STATUS_SITE_MAIN_TPL",
    ]

    def test_every_playbook_template_is_classified(self):
        found = {
            name
            for name, value in vars(ansible_builder).items()
            if re.fullmatch(r"_[A-Z_]+", name)
            and isinstance(value, str)
            and ("hosts:" in value or "- name:" in value)
        }
        assert found == set(self.VERBATIM) | set(self.FORMATTED)

    @pytest.mark.parametrize("name", VERBATIM)
    def test_verbatim_template_has_no_doubled_braces(self, name):
        assert "{{{{" not in getattr(ansible_builder, name)
        assert "}}}}" not in getattr(ansible_builder, name)

    @pytest.mark.parametrize("name", FORMATTED)
    def test_formatted_template_has_no_bare_jinja_braces(self, name):
        assert re.search(r"(?<!\{)\{\{ ", getattr(ansible_builder, name)) is None
