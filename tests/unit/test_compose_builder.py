#!/usr/bin/env python
# coding: utf-8

import os
import yaml
import pytest

from xrpld_lab.models import PortSet, NodeRole
from xrpld_lab.compose_builder import ComposeBuilder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_ports() -> PortSet:
    """Return a default PortSet for testing."""
    return PortSet(
        rpc_public=5007, rpc_admin=5005, ws_public=6008, ws_admin=6006, peer=51235
    )


def _validator_ports(index: int = 1) -> PortSet:
    """Return validator PortSet for a given index."""
    return PortSet.for_node(index, NodeRole.VALIDATOR)


def _peer_ports(index: int = 1) -> PortSet:
    """Return peer PortSet for a given index."""
    return PortSet.for_node(index, NodeRole.PEER)


# ===========================================================================
# Build empty compose
# ===========================================================================


class TestBuildEmpty:
    """Test building a compose dict with no services."""

    def test_build_empty_has_no_version(self):
        builder = ComposeBuilder("test-network")
        result = builder.build()
        assert "version" not in result

    def test_build_empty_has_networks(self):
        builder = ComposeBuilder("test-network")
        result = builder.build()
        assert result["networks"] == {"test-network": {"driver": "bridge"}}

    def test_build_empty_has_empty_services(self):
        builder = ComposeBuilder("test-network")
        result = builder.build()
        assert result["services"] == {}

    def test_build_empty_keys(self):
        builder = ComposeBuilder("mynet")
        result = builder.build()
        assert set(result.keys()) == {"services", "networks"}


# ===========================================================================
# add_standalone_service
# ===========================================================================


class TestAddStandaloneService:
    """Test adding a standalone xrpld service."""

    def test_service_key(self):
        builder = ComposeBuilder("standalone-network")
        ports = _default_ports()
        builder.add_standalone_service("xrpl", ports)
        assert "xrpl" in builder.services

    def test_build_context(self):
        builder = ComposeBuilder("standalone-network")
        ports = _default_ports()
        builder.add_standalone_service("xrpl", ports)
        svc = builder.services["xrpl"]
        assert svc["build"] == {"context": ".", "dockerfile": "Dockerfile"}

    def test_platform(self):
        builder = ComposeBuilder("standalone-network")
        ports = _default_ports()
        builder.add_standalone_service("xrpl", ports)
        svc = builder.services["xrpl"]
        assert svc["platform"] == "linux/x86_64"

    def test_container_name(self):
        builder = ComposeBuilder("standalone-network")
        ports = _default_ports()
        builder.add_standalone_service("xrpl", ports)
        svc = builder.services["xrpl"]
        assert svc["container_name"] == "xrpl"

    def test_ports(self):
        builder = ComposeBuilder("standalone-network")
        ports = _default_ports()
        builder.add_standalone_service("xrpl", ports)
        svc = builder.services["xrpl"]
        expected_ports = [
            "5007:5007",
            "127.0.0.1:5005:5005",
            "6008:6008",
            "127.0.0.1:6006:6006",
            "51235:51235",
        ]
        assert svc["ports"] == expected_ports

    def test_volumes(self):
        builder = ComposeBuilder("standalone-network")
        ports = _default_ports()
        builder.add_standalone_service("xrpl", ports)
        svc = builder.services["xrpl"]
        expected_volumes = [
            "${PWD}/xrpl/config:/etc/opt/ripple",
            "${PWD}/xrpl/log:/opt/ripple/log",
            "${PWD}/xrpl/lib:/opt/ripple/lib",
        ]
        assert svc["volumes"] == expected_volumes

    def test_networks(self):
        builder = ComposeBuilder("standalone-network")
        ports = _default_ports()
        builder.add_standalone_service("xrpl", ports)
        svc = builder.services["xrpl"]
        assert svc["networks"] == ["standalone-network"]


# ===========================================================================
# add_node_service (validator)
# ===========================================================================


class TestAddNodeServiceValidator:
    """Test adding a validator node service for network mode."""

    def test_service_key(self):
        builder = ComposeBuilder("testnet-network")
        ports = _validator_ports(1)
        builder.add_node_service("vnode1", ports, NodeRole.VALIDATOR)
        assert "vnode1" in builder.services

    def test_build_context(self):
        builder = ComposeBuilder("testnet-network")
        ports = _validator_ports(1)
        builder.add_node_service("vnode1", ports, NodeRole.VALIDATOR)
        svc = builder.services["vnode1"]
        assert svc["build"] == {"context": "vnode1", "dockerfile": "Dockerfile"}

    def test_platform(self):
        builder = ComposeBuilder("testnet-network")
        ports = _validator_ports(1)
        builder.add_node_service("vnode1", ports, NodeRole.VALIDATOR)
        svc = builder.services["vnode1"]
        assert svc["platform"] == "linux/x86_64"

    def test_container_name(self):
        builder = ComposeBuilder("testnet-network")
        ports = _validator_ports(1)
        builder.add_node_service("vnode1", ports, NodeRole.VALIDATOR)
        svc = builder.services["vnode1"]
        assert svc["container_name"] == "vnode1"

    def test_ports(self):
        builder = ComposeBuilder("testnet-network")
        ports = _validator_ports(1)
        builder.add_node_service("vnode1", ports, NodeRole.VALIDATOR)
        svc = builder.services["vnode1"]
        expected_ports = [
            f"{ports.rpc_public}:{ports.rpc_public}",
            f"127.0.0.1:{ports.rpc_admin}:{ports.rpc_admin}",
            f"{ports.ws_public}:{ports.ws_public}",
            f"127.0.0.1:{ports.ws_admin}:{ports.ws_admin}",
            f"{ports.peer}:{ports.peer}",
        ]
        assert svc["ports"] == expected_ports

    def test_volumes(self):
        builder = ComposeBuilder("testnet-network")
        ports = _validator_ports(1)
        builder.add_node_service("vnode1", ports, NodeRole.VALIDATOR)
        svc = builder.services["vnode1"]
        expected_volumes = [
            "./vnode1/config:/opt/ripple/config",
            "./vnode1/log:/opt/ripple/log",
            "./vnode1/lib:/opt/ripple/lib",
            "./vnode1/db:/var/lib/xrpld/db",
        ]
        assert svc["volumes"] == expected_volumes

    def test_networks(self):
        builder = ComposeBuilder("testnet-network")
        ports = _validator_ports(1)
        builder.add_node_service("vnode1", ports, NodeRole.VALIDATOR)
        svc = builder.services["vnode1"]
        assert svc["networks"] == ["testnet-network"]

    def test_validator_index_2(self):
        """Validator at index 2 should have different ports."""
        builder = ComposeBuilder("testnet-network")
        ports = _validator_ports(2)
        builder.add_node_service("vnode2", ports, NodeRole.VALIDATOR)
        svc = builder.services["vnode2"]
        assert svc["build"]["context"] == "vnode2"
        assert svc["container_name"] == "vnode2"
        assert f"{ports.rpc_public}:{ports.rpc_public}" in svc["ports"]


# ===========================================================================
# add_node_service (peer)
# ===========================================================================


class TestAddNodeServicePeer:
    """Test adding a peer node service for network mode."""

    def test_service_key(self):
        builder = ComposeBuilder("testnet-network")
        ports = _peer_ports(1)
        builder.add_node_service("pnode1", ports, NodeRole.PEER)
        assert "pnode1" in builder.services

    def test_build_context(self):
        builder = ComposeBuilder("testnet-network")
        ports = _peer_ports(1)
        builder.add_node_service("pnode1", ports, NodeRole.PEER)
        svc = builder.services["pnode1"]
        assert svc["build"] == {"context": "pnode1", "dockerfile": "Dockerfile"}

    def test_container_name(self):
        builder = ComposeBuilder("testnet-network")
        ports = _peer_ports(1)
        builder.add_node_service("pnode1", ports, NodeRole.PEER)
        svc = builder.services["pnode1"]
        assert svc["container_name"] == "pnode1"

    def test_volumes(self):
        builder = ComposeBuilder("testnet-network")
        ports = _peer_ports(1)
        builder.add_node_service("pnode1", ports, NodeRole.PEER)
        svc = builder.services["pnode1"]
        expected_volumes = [
            "./pnode1/config:/opt/ripple/config",
            "./pnode1/log:/opt/ripple/log",
            "./pnode1/lib:/opt/ripple/lib",
            "./pnode1/db:/var/lib/xrpld/db",
        ]
        assert svc["volumes"] == expected_volumes

    def test_networks(self):
        builder = ComposeBuilder("testnet-network")
        ports = _peer_ports(1)
        builder.add_node_service("pnode1", ports, NodeRole.PEER)
        svc = builder.services["pnode1"]
        assert svc["networks"] == ["testnet-network"]


# ===========================================================================
# add_explorer_service (standalone)
# ===========================================================================


class TestAddExplorerServiceStandalone:
    """Test adding explorer service in standalone mode."""

    def test_service_key(self):
        builder = ComposeBuilder("standalone-network")
        builder.add_explorer_service(ws_port=6006, standalone=True)
        assert "explorer" in builder.services

    def test_image(self):
        builder = ComposeBuilder("standalone-network")
        builder.add_explorer_service(ws_port=6006, standalone=True)
        svc = builder.services["explorer"]
        assert svc["image"] == "transia/explorer:latest"

    def test_container_name(self):
        builder = ComposeBuilder("standalone-network")
        builder.add_explorer_service(ws_port=6006, standalone=True)
        svc = builder.services["explorer"]
        assert svc["container_name"] == "explorer"

    def test_environment(self):
        builder = ComposeBuilder("standalone-network")
        builder.add_explorer_service(ws_port=6006, standalone=True)
        svc = builder.services["explorer"]
        assert "PORT=4000" in svc["environment"]
        assert "VUE_APP_WSS_ENDPOINT=ws://0.0.0.0:6006" in svc["environment"]

    def test_ports(self):
        builder = ComposeBuilder("standalone-network")
        builder.add_explorer_service(ws_port=6006, standalone=True)
        svc = builder.services["explorer"]
        assert svc["ports"] == ["4000:4000"]

    def test_networks(self):
        builder = ComposeBuilder("standalone-network")
        builder.add_explorer_service(ws_port=6006, standalone=True)
        svc = builder.services["explorer"]
        assert svc["networks"] == ["standalone-network"]

    def test_custom_ws_port(self):
        builder = ComposeBuilder("standalone-network")
        builder.add_explorer_service(ws_port=7777, standalone=True)
        svc = builder.services["explorer"]
        assert "VUE_APP_WSS_ENDPOINT=ws://0.0.0.0:7777" in svc["environment"]


# ===========================================================================
# add_explorer_service (network)
# ===========================================================================


class TestAddExplorerServiceNetwork:
    """Test adding explorer service in network mode."""

    def test_service_key(self):
        builder = ComposeBuilder("testnet-network")
        builder.add_explorer_service(ws_port=6016, standalone=False)
        assert "network-explorer" in builder.services

    def test_container_name(self):
        builder = ComposeBuilder("testnet-network")
        builder.add_explorer_service(ws_port=6016, standalone=False)
        svc = builder.services["network-explorer"]
        assert svc["container_name"] == "network-explorer"

    def test_image(self):
        builder = ComposeBuilder("testnet-network")
        builder.add_explorer_service(ws_port=6016, standalone=False)
        svc = builder.services["network-explorer"]
        assert svc["image"] == "transia/explorer:latest"

    def test_environment(self):
        builder = ComposeBuilder("testnet-network")
        builder.add_explorer_service(ws_port=6016, standalone=False)
        svc = builder.services["network-explorer"]
        assert "PORT=4000" in svc["environment"]
        assert "VUE_APP_WSS_ENDPOINT=ws://0.0.0.0:6016" in svc["environment"]

    def test_ports(self):
        builder = ComposeBuilder("testnet-network")
        builder.add_explorer_service(ws_port=6016, standalone=False)
        svc = builder.services["network-explorer"]
        assert svc["ports"] == ["4000:4000"]

    def test_networks(self):
        builder = ComposeBuilder("testnet-network")
        builder.add_explorer_service(ws_port=6016, standalone=False)
        svc = builder.services["network-explorer"]
        assert svc["networks"] == ["testnet-network"]


# ===========================================================================
# add_vl_service
# ===========================================================================


class TestAddVlService:
    """Test adding VL (validator list) service."""

    def test_service_key(self):
        builder = ComposeBuilder("testnet-network")
        builder.add_vl_service()
        assert "vl" in builder.services

    def test_build(self):
        builder = ComposeBuilder("testnet-network")
        builder.add_vl_service()
        svc = builder.services["vl"]
        assert svc["build"] == {"context": "vl", "dockerfile": "Dockerfile"}

    def test_container_name(self):
        builder = ComposeBuilder("testnet-network")
        builder.add_vl_service()
        svc = builder.services["vl"]
        assert svc["container_name"] == "vl"

    def test_ports(self):
        builder = ComposeBuilder("testnet-network")
        builder.add_vl_service()
        svc = builder.services["vl"]
        assert svc["ports"] == ["80:80"]

    def test_networks(self):
        builder = ComposeBuilder("testnet-network")
        builder.add_vl_service()
        svc = builder.services["vl"]
        assert svc["networks"] == ["testnet-network"]

    def test_healthcheck(self):
        builder = ComposeBuilder("testnet-network")
        builder.add_vl_service()
        svc = builder.services["vl"]
        assert "healthcheck" in svc
        hc = svc["healthcheck"]
        assert hc["test"] == ["CMD", "curl", "-f", "http://localhost/vl.json"]
        assert hc["interval"] == "5s"
        assert hc["timeout"] == "3s"
        assert hc["retries"] == 3
        assert hc["start_period"] == "10s"


# ===========================================================================
# add_ipfs_service
# ===========================================================================


class TestAddIpfsService:
    """Test adding IPFS service."""

    def test_service_key(self):
        builder = ComposeBuilder("standalone-network")
        builder.add_ipfs_service("xrpl")
        assert "ipfs" in builder.services

    def test_image(self):
        builder = ComposeBuilder("standalone-network")
        builder.add_ipfs_service("xrpl")
        svc = builder.services["ipfs"]
        assert svc["image"] == "ipfs/go-ipfs:latest"

    def test_container_name(self):
        builder = ComposeBuilder("standalone-network")
        builder.add_ipfs_service("xrpl")
        svc = builder.services["ipfs"]
        assert svc["container_name"] == "ipfs"

    def test_environment(self):
        builder = ComposeBuilder("standalone-network")
        builder.add_ipfs_service("xrpl")
        svc = builder.services["ipfs"]
        assert "IPFS_PROFILE=server" in svc["environment"]

    def test_ports(self):
        builder = ComposeBuilder("standalone-network")
        builder.add_ipfs_service("xrpl")
        svc = builder.services["ipfs"]
        assert svc["ports"] == ["4001:4001", "5001:5001", "8080:8080"]

    def test_volumes(self):
        builder = ComposeBuilder("standalone-network")
        builder.add_ipfs_service("xrpl")
        svc = builder.services["ipfs"]
        expected_volumes = [
            "${PWD}/xrpl/ipfs_staging:/export",
            "${PWD}/xrpl/ipfs_data:/data/ipfs",
        ]
        assert svc["volumes"] == expected_volumes

    def test_networks(self):
        builder = ComposeBuilder("standalone-network")
        builder.add_ipfs_service("xrpl")
        svc = builder.services["ipfs"]
        assert svc["networks"] == ["standalone-network"]


# ===========================================================================
# Multiple services
# ===========================================================================


class TestMultipleServices:
    """Test adding multiple services to a single compose."""

    def test_all_services_present(self):
        builder = ComposeBuilder("testnet-network")
        ports_v1 = _validator_ports(1)
        ports_v2 = _validator_ports(2)
        ports_p1 = _peer_ports(1)

        builder.add_node_service("vnode1", ports_v1, NodeRole.VALIDATOR)
        builder.add_node_service("vnode2", ports_v2, NodeRole.VALIDATOR)
        builder.add_node_service("pnode1", ports_p1, NodeRole.PEER)
        builder.add_vl_service()
        builder.add_explorer_service(ws_port=6016, standalone=False)

        result = builder.build()
        assert set(result["services"].keys()) == {
            "vnode1",
            "vnode2",
            "pnode1",
            "vl",
            "network-explorer",
        }

    def test_standalone_with_explorer_and_ipfs(self):
        builder = ComposeBuilder("standalone-network")
        ports = _default_ports()

        builder.add_standalone_service("xrpl", ports)
        builder.add_explorer_service(ws_port=6006, standalone=True)
        builder.add_ipfs_service("xrpl")

        result = builder.build()
        assert set(result["services"].keys()) == {"xrpl", "explorer", "ipfs"}

    def test_services_do_not_overwrite(self):
        """Each service should retain its own config."""
        builder = ComposeBuilder("testnet-network")
        ports_v1 = _validator_ports(1)
        ports_v2 = _validator_ports(2)

        builder.add_node_service("vnode1", ports_v1, NodeRole.VALIDATOR)
        builder.add_node_service("vnode2", ports_v2, NodeRole.VALIDATOR)

        svc1 = builder.services["vnode1"]
        svc2 = builder.services["vnode2"]
        assert svc1["build"]["context"] == "vnode1"
        assert svc2["build"]["context"] == "vnode2"
        assert svc1["ports"] != svc2["ports"]


# ===========================================================================
# Chaining
# ===========================================================================


class TestChaining:
    """Test that builder methods return self for fluent chaining."""

    def test_add_standalone_service_returns_self(self):
        builder = ComposeBuilder("standalone-network")
        result = builder.add_standalone_service("xrpl", _default_ports())
        assert result is builder

    def test_add_node_service_returns_self(self):
        builder = ComposeBuilder("testnet-network")
        result = builder.add_node_service(
            "vnode1", _validator_ports(1), NodeRole.VALIDATOR
        )
        assert result is builder

    def test_add_explorer_service_returns_self(self):
        builder = ComposeBuilder("standalone-network")
        result = builder.add_explorer_service(ws_port=6006, standalone=True)
        assert result is builder

    def test_add_vl_service_returns_self(self):
        builder = ComposeBuilder("testnet-network")
        result = builder.add_vl_service()
        assert result is builder

    def test_add_ipfs_service_returns_self(self):
        builder = ComposeBuilder("standalone-network")
        result = builder.add_ipfs_service("xrpl")
        assert result is builder

    def test_full_chain(self):
        """Chain all methods together."""
        builder = ComposeBuilder("standalone-network")
        result = (
            builder.add_standalone_service("xrpl", _default_ports())
            .add_explorer_service(ws_port=6006, standalone=True)
            .add_ipfs_service("xrpl")
        )
        assert result is builder
        assert len(builder.services) == 3


# ===========================================================================
# render
# ===========================================================================


class TestRender:
    """Test YAML rendering."""

    def test_render_returns_string(self):
        builder = ComposeBuilder("test-network")
        result = builder.render()
        assert isinstance(result, str)

    def test_render_is_valid_yaml(self):
        builder = ComposeBuilder("test-network")
        builder.add_standalone_service("xrpl", _default_ports())
        rendered = builder.render()
        parsed = yaml.safe_load(rendered)
        assert "version" not in parsed
        assert "xrpl" in parsed["services"]

    def test_render_matches_build(self):
        builder = ComposeBuilder("test-network")
        builder.add_standalone_service("xrpl", _default_ports())
        rendered = builder.render()
        parsed = yaml.safe_load(rendered)
        assert parsed == builder.build()


# ===========================================================================
# write
# ===========================================================================


class TestWrite:
    """Test writing docker-compose.yml to disk."""

    def test_write_creates_file(self, tmp_path):
        builder = ComposeBuilder("test-network")
        builder.add_standalone_service("xrpl", _default_ports())
        output_file = str(tmp_path / "docker-compose.yml")
        builder.write(output_file)
        assert os.path.exists(output_file)

    def test_write_valid_yaml(self, tmp_path):
        builder = ComposeBuilder("test-network")
        builder.add_standalone_service("xrpl", _default_ports())
        builder.add_explorer_service(ws_port=6006, standalone=True)
        output_file = str(tmp_path / "docker-compose.yml")
        builder.write(output_file)

        with open(output_file) as f:
            parsed = yaml.safe_load(f)

        assert "version" not in parsed
        assert "xrpl" in parsed["services"]
        assert "explorer" in parsed["services"]
        assert parsed["networks"] == {"test-network": {"driver": "bridge"}}

    def test_write_matches_build(self, tmp_path):
        builder = ComposeBuilder("test-network")
        builder.add_standalone_service("xrpl", _default_ports())
        output_file = str(tmp_path / "docker-compose.yml")
        builder.write(output_file)

        with open(output_file) as f:
            parsed = yaml.safe_load(f)

        assert parsed == builder.build()
