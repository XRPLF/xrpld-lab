"""Ansible deployment file generator for xrpld_lab.

Generates a complete ansible directory for deploying xrpld clusters
to remote servers, with optional per-host services (nginx, redis,
faucet, stream, debug, compiler, status) and idempotent run.sh that skips
already-completed playbook stages on rebuild.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from typing import List

import yaml

from xrpld_lab.models import (
    AnsibleConfig,
    PortSet,
    ServicesHost,
)


# ---------------------------------------------------------------------------
# Node registration
# ---------------------------------------------------------------------------


@dataclass
class AnsibleNode:
    name: str
    ip: str
    ports: PortSet
    config_path: str
    role: str  # "validator" or "peer"


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class AnsibleBuilder:
    """Generates ansible deployment files for an xrpld cluster."""

    def __init__(
        self,
        cluster_dir: str,
        config: AnsibleConfig,
        image_name: str,
        network_name: str = "loadnet",
        genesis: bool = True,
        build_tag: str = "",
    ):
        self.cluster_dir = cluster_dir
        self.config = config
        self.image_name = image_name
        # Tag for the per-node wrapper image; the base image's tag unless given.
        self.build_tag = build_tag or image_name.rsplit(":", 1)[-1]
        self.network_name = network_name
        # genesis=True: reset every node's state for a fresh chain, all hosts
        # in parallel.
        # genesis=False: preserve db/config and roll one host at a time (live network).
        self.genesis = genesis
        self.ansible_dir = os.path.join(cluster_dir, "ansible")
        self._nodes: List[AnsibleNode] = []

    def add_node(
        self,
        name: str,
        ip: str,
        ports: PortSet,
        config_path: str,
        role: str = "validator",
    ) -> AnsibleBuilder:
        """Register a node host; every node needs an address to be deployed to."""
        if not ip:
            raise ValueError(f"{name} has no IP address")
        self._nodes.append(AnsibleNode(name, ip, ports, config_path, role))
        return self

    def write(self) -> str:
        """Write all ansible files and return the ansible directory path."""
        os.makedirs(self.ansible_dir, exist_ok=True)
        os.makedirs(os.path.join(self.ansible_dir, "host_vars"), exist_ok=True)

        self._write_hosts_txt()
        self._write_host_vars()
        self._write_deps_yml()
        self._write_main_yml()
        self._write_clean_yml()
        self._write_clean_sh()
        if self.config.alloy:
            self._write_alloy()
        if self.config.status_host:
            self._write_status()

        for svc_host in self.config.services:
            self._write_services_host(svc_host)

        self._write_run_sh()

        return self.ansible_dir

    # ------------------------------------------------------------------
    # hosts.txt
    # ------------------------------------------------------------------

    def _write_hosts_txt(self) -> None:
        c = self.config
        lines = [
            "",
            "# this is a basic file putting different hosts into categories",
            "# used by ansible to determine which actions to run on which hosts",
            "# nodes: every validator and peer; the node playbooks target this group",
            "[nodes]",
        ]
        for node in self._nodes:
            lines.append(self._host_line(node.ip))

        for svc_host in c.services:
            lines.append("")
            lines.append(f"[{svc_host.name}]")
            lines.append(self._host_line(svc_host.ip))

        lines.append("")
        self._file_write(os.path.join(self.ansible_dir, "hosts.txt"), "\n".join(lines))

    def _host_line(self, ip: str) -> str:
        c = self.config
        return (
            f"{ip} ansible_port={c.ssh_port}"
            f" ansible_user={c.ssh_user}"
            f" ansible_ssh_private_key_file={c.key_for(ip)}"
            f" vars_file=host_vars/{ip}.yml "
        )

    # ------------------------------------------------------------------
    # host_vars/{ip}.yml
    # ------------------------------------------------------------------

    def _write_host_vars(self) -> None:
        hv_dir = os.path.join(self.ansible_dir, "host_vars")
        for node in self._nodes:
            data = {
                "config_path": node.config_path,
                # Node build context (Dockerfile + entrypoint +
                # genesis.json) — the parent of
                # the config dir. The deploy builds this wrapper on
                # the host, like standalone.
                "build_context": os.path.dirname(node.config_path),
                # Tag the wrapper by the base image's tag so each build gets a distinct
                # name and the container RECREATES on a new image (a constant tag makes
                # docker_container see "same image" and merely restart the stale one).
                "docker_build_tag": f"{node.name}:{self.build_tag}",
                "docker_container_name": node.name,
                "docker_container_ports": node.ports.publish_mappings(),
                "docker_env_variables": {
                    "RPC_PUBLIC": str(node.ports.rpc_public),
                    "RPC_ADMIN": str(node.ports.rpc_admin),
                    "WS_PUBLIC": str(node.ports.ws_public),
                    "WS_ADMIN": str(node.ports.ws_admin),
                    "PEER": str(node.ports.peer),
                },
                "docker_image_name": self.image_name,
                "docker_network_name": self.network_name,
                "docker_volumes": [
                    "/opt/ripple/config:/opt/ripple/config",
                    "/opt/ripple/log:/opt/ripple/log",
                    "/opt/ripple/lib:/opt/ripple/lib",
                    "/var/lib/xrpld/db:/var/lib/xrpld/db",
                ],
                "peer_port": node.ports.peer,
                "ssh_port": self.config.ssh_port,
                **self._alloy_host_vars(node),
                **self._status_host_vars(node),
                # /var/lib/xrpld/db is a Local NVMe mountpoint —
                # excluded from the cleanup
                # rmtree (can't delete a live mount; it's ephemeral
                # and fresh on boot anyway).
                "volumes": [
                    "/opt/ripple/config",
                    "/opt/ripple/log",
                    "/opt/ripple/lib",
                ],
                "ws_port": node.ports.ws_public,
            }
            path = os.path.join(hv_dir, f"{node.ip}.yml")
            with open(path, "w") as f:
                yaml.dump(data, f, explicit_start=True, default_flow_style=False)

    # ------------------------------------------------------------------
    # deps.yml
    # ------------------------------------------------------------------

    def _write_deps_yml(self) -> None:
        self._file_write(os.path.join(self.ansible_dir, "deps.yml"), _DEPS_YML)

    # ------------------------------------------------------------------
    # main.yml
    # ------------------------------------------------------------------

    def _write_main_yml(self) -> None:
        if self.genesis:
            content = _MAIN_HEADER + _MAIN_RESET_TASKS + _MAIN_DEPLOY_TASKS
        else:
            content = _MAIN_HEADER_ROLLING + _MAIN_DEPLOY_TASKS
        self._file_write(os.path.join(self.ansible_dir, "main.yml"), content)

    # ------------------------------------------------------------------
    # clean.yml / clean.sh
    # ------------------------------------------------------------------

    def _write_clean_yml(self) -> None:
        self._file_write(os.path.join(self.ansible_dir, "clean.yml"), _CLEAN_YML)

    def _write_clean_sh(self) -> None:
        content = (
            "#!/bin/sh\n"
            "export ANSIBLE_HOST_KEY_CHECKING=False\n"
            "ansible -i hosts.txt all -u ubuntu -m ping\n"
            "ansible-playbook -i hosts.txt clean.yml\n"
            "rm -f .done_*\n"
        )
        self._file_write_executable(os.path.join(self.ansible_dir, "clean.sh"), content)

    # ------------------------------------------------------------------
    # Per-ServicesHost file generation
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Alloy telemetry sidecar
    # ------------------------------------------------------------------

    def _alloy_host_vars(self, node: AnsibleNode) -> dict:
        a = self.config.alloy
        if not a:
            return {}
        statsd = f"127.0.0.1:{a.statsd_port}"
        creds = a.creds_for(node.name)
        return {
            "alloy_container_name": a.container_name,
            "alloy_image": a.image,
            "alloy_env_variables": {
                "ALLOY_NODE": a.node_label(node.name),
                "ALLOY_PUSH_HOST": a.push_host,
                "ALLOY_USERNAME": creds["username"],
                "ALLOY_PASSWORD": creds["password"],
                # Sidecar shares the node's netns, so both ends of the StatsD hop are
                # the same loopback the node's [insight] stanza points at.
                "ALLOY_STATSD_LISTEN": statsd,
                "ALLOY_RIPPLED_STATSD_ADDRESS": statsd,
                "ALLOY_STATSD_RELAY_ADDR": a.statsd_relay_addr,
            },
            "alloy_rippled_config": a.rippled_config_path,
            "alloy_rippled_log_dir": a.rippled_log_dir,
        }

    def _write_alloy(self) -> None:
        a = self.config.alloy
        alloy_dir = os.path.join(self.ansible_dir, "alloy")
        if os.path.isdir(alloy_dir):
            shutil.rmtree(alloy_dir)
        shutil.copytree(a.source_dir, alloy_dir)
        self._file_write(os.path.join(self.ansible_dir, "alloy.yml"), _ALLOY_YML)

    # ------------------------------------------------------------------
    # Status sampler (every node) and network roll-up (services host)
    # ------------------------------------------------------------------

    STATUS_DIR = "/opt/xrpld-status"
    STATUS_STATE_DIR = "/var/lib/xrpld-status"

    def _status_url(self, node: AnsibleNode, host: ServicesHost) -> str:
        # The services host's own sampler is reached over loopback, every
        # other over its IP.
        ip = "127.0.0.1" if node.ip == host.ip else node.ip
        return f"http://{ip}:{host.status.port}"

    def _status_network_nodes(self, host: ServicesHost) -> str:
        return ",".join(
            f"{n.name}={self._status_url(n, host)} role={n.role}" for n in self._nodes
        )

    def _status_host_vars(self, node: AnsibleNode) -> dict:
        host = self.config.status_host
        if not host:
            return {}
        st = host.status
        local = node.ip == host.ip
        env = {
            "NODE_METRICS_DB": f"{self.STATUS_STATE_DIR}/metrics.db",
            # Only nginx on the services host talks to its sampler; every other node's
            # sampler is fetched across the network by the roll-up, so it
            # binds all addresses.
            "NODE_METRICS_HTTP_HOST": "127.0.0.1" if local else "0.0.0.0",
            "NODE_METRICS_HTTP_PORT": str(st.port),
            "NODE_METRICS_INTERVAL": str(st.interval),
            "NODE_METRICS_DASHBOARD": f"{self.STATUS_DIR}/node-dashboard.html",
            "NODE_METRICS_ADMIN_RPC": f"http://127.0.0.1:{node.ports.rpc_admin}/",
            "NODE_METRICS_DEBUGSTREAM_HEALTH": (
                f"http://127.0.0.1:{host.debug.port}/health"
                if local and host.debug
                else ""
            ),
            "NODE_METRICS_REDIS_PORT": "6379" if local and host.redis else "0",
            "NODE_METRICS_DISK_PATH": st.disk_path,
            "NODE_METRICS_PROCESS": st.process,
            "NODE_METRICS_RETAIN_RAW_HOURS": str(st.retain_raw_hours),
            "NODE_METRICS_RETAIN_5M_DAYS": str(st.retain_5m_days),
            "NODE_METRICS_RETAIN_1H_DAYS": str(st.retain_1h_days),
            # xrpld sends XDGM from its bridge-network container to the host address,
            # which under 1:1 NAT is not on any local interface, so the listener binds
            # every interface; ufw admits the port from the docker subnets only.
            "NODE_METRICS_XDGM_HOST": "0.0.0.0",
            "NODE_METRICS_XDGM_PORT": str(st.xdgm_port),
        }
        if local:
            env["NODE_METRICS_NETWORK_NODES"] = self._status_network_nodes(host)
            env["NODE_METRICS_NETWORK_FILE"] = f"{self.STATUS_DIR}/network.json"
            env["NODE_METRICS_NETWORK_NAME"] = st.network_name or (
                host.nginx.domain if host.nginx else ""
            )
        return {
            "status_env": env,
            "status_http_port": st.port,
            "status_xdgm_port": st.xdgm_port,
            # Source allowed through ufw to the sampler port; empty on the
            # services host,
            # whose sampler is loopback-only.
            "status_allow_from": "" if local else host.ip,
        }

    def _write_status(self) -> None:
        host = self.config.status_host
        if self._node_for_ip(host.ip) is None:
            raise ValueError(
                f"status host {host.name} ({host.ip}) must be one of the nodes: "
                "its sampler serves /status/api/ for the network page"
            )
        status_dir = os.path.join(self.ansible_dir, "status")
        os.makedirs(status_dir, exist_ok=True)
        for filename in (
            "node_metrics.py",
            "node-dashboard.html",
            "network-dashboard.html",
        ):
            shutil.copyfile(
                os.path.join(_STATUS_SOURCE_DIR, filename),
                os.path.join(status_dir, filename),
            )
        self._file_write(os.path.join(self.ansible_dir, "status.yml"), _STATUS_YML)

    def _write_status_site(self, host_dir: str, host: ServicesHost) -> None:
        svc_dir = os.path.join(host_dir, "status")
        os.makedirs(svc_dir, exist_ok=True)
        self._write_vars(
            os.path.join(svc_dir, "vars.yml"),
            {
                "STATUS_DIR": self.STATUS_DIR,
                "STATUS_PORT": host.status.port,
            },
        )
        self._file_write(
            os.path.join(svc_dir, "main.yml"),
            _STATUS_SITE_MAIN_TPL.format(group=host.name),
        )

    def _status_nginx_locations(self, host: ServicesHost) -> str:
        """The /status/ locations inside the bare-domain server block."""
        port = host.status.port
        proxy = (
            "                  limit_req zone=xrpld_status burst=40 nodelay;\n"
            "                  proxy_pass {target};\n"
            "                  proxy_http_version 1.1;\n"
            "                  proxy_set_header Host $host;\n"
            "                  proxy_set_header X-Real-IP $remote_addr;\n"
            "                  proxy_read_timeout 30s;\n"
        )
        out = (
            "              location = /status {\n"
            "                  return 301 /status/;\n"
            "              }\n"
            "              location = /status/ {\n"
            "                  limit_req zone=xrpld_status burst=40 nodelay;\n"
            f"                  root {self.STATUS_DIR}/www;\n"
            "                  try_files /network-dashboard.html =404;\n"
            "              }\n"
            "              location /status/api/ {\n"
            + proxy.format(target=f"http://127.0.0.1:{port}/api/")
            + "              }\n"
        )
        for node in self._nodes:
            out += (
                f"              location /status/nodes/{node.name}/ {{\n"
                + proxy.format(target=f"{self._status_url(node, host)}/")
                + "              }\n"
            )
        return out

    def _write_services_host(self, host: ServicesHost) -> None:
        host_dir = os.path.join(self.ansible_dir, "services", host.name)
        os.makedirs(host_dir, exist_ok=True)

        if host.nginx:
            self._write_nginx(host_dir, host)
        if host.vl:
            self._write_vl(host_dir, host)
        if host.status:
            self._write_status_site(host_dir, host)
        if host.redis:
            self._write_redis(host_dir, host)
        if host.faucet:
            self._write_faucet(host_dir, host)
        if host.stream:
            self._write_stream(host_dir, host)
        if host.debug:
            self._write_debug(host_dir, host)
        if host.compiler:
            self._write_compiler(host_dir, host)

    # ------------------------------------------------------------------
    # run.sh — smart runner with skip-on-success
    # ------------------------------------------------------------------

    def _write_run_sh(self) -> None:
        lines = [
            "#!/bin/bash",
            "# Best-effort deploy: a failing host or playbook is logged but does NOT",
            "# abort the",
            "# run, so the remaining nodes still deploy and the run reaches the end.",
            "",
            'SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"',
            'cd "$SCRIPT_DIR"',
            "",
            "# Parse flags",
            "FORCE=false",
            'for arg in "$@"; do',
            '  case "$arg" in',
            "    --force) FORCE=true ;;",
            "  esac",
            "done",
            "",
            "# Run a playbook once. Skips if .done_{name} marker exists.",
            "# Usage: run_once <name> <playbook> [extra args...]",
            "run_once() {",
            "  local name=$1; shift",
            "  local playbook=$1; shift",
            '  if [ "$FORCE" = false ] && [ -f ".done_${name}" ]; then',
            '    echo "SKIP: ${playbook} (already completed'
            ' — delete .done_${name} or use --force to re-run)"',
            "    return 0",
            "  fi",
            '  echo "RUNNING: ${playbook}"',
            '  if ansible-playbook -i hosts.txt "${playbook}" "$@"; then',
            '    touch ".done_${name}"',
            '    echo "DONE: ${playbook}"',
            "  else",
            '    echo "FAILED: ${playbook}"',
            "    return 1",
            "  fi",
            "}",
            "",
            "# Run a playbook every time (no skip logic).",
            "run_always() {",
            "  local playbook=$1; shift",
            '  echo "RUNNING: ${playbook}"',
            '  ansible-playbook -i hosts.txt "${playbook}" "$@"',
            "}",
            "",
            "# Stop at the first failed playbook; never report a partial deploy done.",
            "set -e",
            "export ANSIBLE_HOST_KEY_CHECKING=False",
            "",
            "# SSH agent — optional: skip when SSH_PATH is unset (key already agent-",
            "# loaded",
            "# or inventory carries ansible_ssh_private_key_file). --apple-use-",
            "# keychain is",
            "# macOS-only; fall back to a plain ssh-add elsewhere.",
            'if [ -n "${SSH_PATH:-}" ]; then',
            '  eval "$(ssh-agent -s)"',
            '  ssh-add --apple-use-keychain "$SSH_PATH" 2>/dev/null \\',
            '    || ssh-add "$SSH_PATH"',
            "fi",
            "",
            "# Ping all hosts (informational — unreachable hosts must not abort the",
            "# run)",
            "ansible -i hosts.txt all -u ubuntu -m ping || true",
            "",
            "# Short-lived operator token so each node's docker can pull the private",
            "# AR image; main.yml reads it from the environment, not from argv.",
            'export AR_TOKEN="$(gcloud auth print-access-token 2>/dev/null || true)"',
            "",
            "# --- Core playbooks ---",
            "run_once deps deps.yml",
            "run_always main.yml",
        ]

        if self.config.alloy:
            lines.append("")
            lines.append("# --- Telemetry sidecar (re-run after every node deploy) ---")
            lines.append("run_always alloy.yml")
        if self.config.status_host:
            lines.append("")
            lines.append(
                "# --- Status sampler on every node, re-run after each node deploy ---"
            )
            lines.append("run_always status.yml")

        for host in self.config.services:
            n = host.name
            prefix = f"services/{n}"
            lines.append("")
            lines.append(f"# --- Services: {n} ({host.ip}) ---")

            if host.nginx:
                lines.append(f"run_once {n}_nginx_deps {prefix}/nginx/deps.yml")
                lines.append(f"run_once {n}_nginx_ssl {prefix}/nginx/ssl.yml")
                lines.append(f"run_once {n}_nginx {prefix}/nginx/main.yml")
            if host.vl:
                # Always re-run: a rotated or re-signed list must reach the web root.
                lines.append(f"run_always {prefix}/vl/main.yml")
            if host.status:
                lines.append(f"run_always {prefix}/status/main.yml")
            if host.redis:
                lines.append(f"run_once {n}_redis {prefix}/redis/main.yml")
            if host.faucet:
                lines.append(f"run_once {n}_faucet {prefix}/faucet/main.yml")
            if host.stream:
                lines.append(f"run_once {n}_stream {prefix}/stream/main.yml")
            if host.debug:
                lines.append(f"run_once {n}_debug {prefix}/debug/main.yml")
            if host.compiler:
                lines.append(f"run_once {n}_compiler {prefix}/compiler/main.yml")

        lines.append("")
        lines.append('echo ""')
        lines.append('echo "Deployment complete."')
        lines.append("")

        self._file_write_executable(
            os.path.join(self.ansible_dir, "run.sh"), "\n".join(lines)
        )

    # ------------------------------------------------------------------
    # Service writers (per-host)
    # ------------------------------------------------------------------

    def _write_nginx(self, host_dir: str, host: ServicesHost) -> None:
        nginx_dir = os.path.join(host_dir, "nginx")
        os.makedirs(nginx_dir, exist_ok=True)
        cfg = host.nginx

        node = self._node_for_ip(host.ip)
        le_services = list(cfg.letsencrypt_services or [])
        unknown = set(le_services) - set(NGINX_TLS_SERVICES)
        if unknown:
            raise ValueError(
                f"Unknown letsencrypt_services {sorted(unknown)}; "
                f"valid: {sorted(NGINX_TLS_SERVICES)}"
            )
        vars_data = {
            "IP": "0.0.0.0",
            "PORT": cfg.ws_port or (str(node.ports.ws_public) if node else "6018"),
            "SSL_CERT": f"/etc/ssl/certs/{cfg.domain}.csr.pem",
            "SSL_KEY": f"/etc/ssl/private/{cfg.domain}.pem",
            "SSL_CN": cfg.domain,
            "SSL_O": cfg.ssl_org,
            "SSL_OU": cfg.ssl_ou,
            "RPC_IP": "0.0.0.0",
            "RPC_PORT": cfg.rpc_port
            or (str(node.ports.rpc_public) if node else "5017"),
            "RPC_SSL_CERT": f"/etc/ssl/certs/rpc.{cfg.domain}.csr.pem",
            "RPC_SSL_KEY": f"/etc/ssl/private/rpc.{cfg.domain}.pem",
            "RPC_SSL_CN": f"rpc.{cfg.domain}",
            "FAUCET_IP": "0.0.0.0",
            "FAUCET_PORT": cfg.faucet_port,
            "FAUCET_SSL_CERT": f"/etc/ssl/certs/faucet.{cfg.domain}.csr.pem",
            "FAUCET_SSL_KEY": f"/etc/ssl/private/faucet.{cfg.domain}.pem",
            "FAUCET_SSL_CN": f"faucet.{cfg.domain}",
            "DEBUG_IP": "0.0.0.0",
            "DEBUG_PORT": cfg.debug_port,
            "DEBUG_SSL_CERT": f"/etc/ssl/certs/debug.{cfg.domain}.csr.pem",
            "DEBUG_SSL_KEY": f"/etc/ssl/private/debug.{cfg.domain}.pem",
            "DEBUG_SSL_CN": f"debug.{cfg.domain}",
            "COMPILER_IP": "0.0.0.0",
            "COMPILER_PORT": cfg.compiler_port,
            "COMPILER_SSL_CERT": f"/etc/ssl/certs/compiler.{cfg.domain}.csr.pem",
            "COMPILER_SSL_KEY": f"/etc/ssl/private/compiler.{cfg.domain}.pem",
            "COMPILER_SSL_CN": f"compiler.{cfg.domain}",
        }
        for svc in le_services:
            _, var = NGINX_TLS_SERVICES[svc]
            cn = _nginx_tls_cn(svc, cfg.domain)
            vars_data[f"{var}_CERT"] = f"/etc/letsencrypt/live/{cn}/fullchain.pem"
            vars_data[f"{var}_KEY"] = f"/etc/letsencrypt/live/{cn}/privkey.pem"
        self._write_vars(os.path.join(nginx_dir, "vars.yml"), vars_data)
        self._file_write(
            os.path.join(nginx_dir, "deps.yml"),
            _NGINX_DEPS_TPL.format(group=host.name, ssh_port=self.config.ssh_port),
        )
        ssl_content = _NGINX_SSL_HEADER_TPL.format(group=host.name)
        if le_services:
            le_cns = [_nginx_tls_cn(s, cfg.domain) for s in le_services]
            ssl_content += _ssl_letsencrypt_tasks(le_cns, cfg.letsencrypt_email)
        for svc, (label, var) in NGINX_TLS_SERVICES.items():
            if svc not in le_services:
                ssl_content += _ssl_selfsigned_tasks(label, var)
        self._file_write(os.path.join(nginx_dir, "ssl.yml"), ssl_content)
        self._file_write(
            os.path.join(nginx_dir, "main.yml"),
            _NGINX_MAIN_TPL.format(
                group=host.name,
                status_zone=_STATUS_NGINX_ZONE if host.status else "",
                status_locations=(
                    self._status_nginx_locations(host) if host.status else ""
                ),
            ),
        )

    def _write_vl(self, host_dir: str, host: ServicesHost) -> None:
        svc_dir = os.path.join(host_dir, "vl")
        os.makedirs(svc_dir, exist_ok=True)
        cfg = host.vl
        domain = host.nginx.domain if host.nginx else ""
        cn = _nginx_tls_cn("vl", domain)

        # The signed list is produced in the cluster dir; stage a copy beside
        # the playbook
        # so `copy: src=` resolves relative to it.
        src = os.path.join(self.cluster_dir, cfg.source)
        staged = os.path.join(svc_dir, cfg.filename)
        if os.path.exists(src):
            shutil.copyfile(src, staged)
        else:
            # A missing list must fail the deploy, not silently serve a
            # 404 that every node
            # then treats as an unreachable VL site.
            raise FileNotFoundError(
                f"no signed publisher list at {src} — generate the cluster before "
                "writing its VL vhost"
            )

        le = list(host.nginx.letsencrypt_services or []) if host.nginx else []
        vars_data = {
            "VL_SSL_CN": cn,
            "VL_ROOT": cfg.root_dir,
            "VL_FILE": cfg.filename,
            "VL_TLS": "vl" in le,
        }
        if "vl" in le:
            vars_data["VL_SSL_CERT"] = f"/etc/letsencrypt/live/{cn}/fullchain.pem"
            vars_data["VL_SSL_KEY"] = f"/etc/letsencrypt/live/{cn}/privkey.pem"
        self._write_vars(os.path.join(svc_dir, "vars.yml"), vars_data)
        self._file_write(
            os.path.join(svc_dir, "main.yml"),
            _VL_MAIN_TPL.format(group=host.name),
        )

    def _write_redis(self, host_dir: str, host: ServicesHost) -> None:
        svc_dir = os.path.join(host_dir, "redis")
        os.makedirs(svc_dir, exist_ok=True)
        cfg = host.redis
        self._write_vars(
            os.path.join(svc_dir, "vars.yml"),
            {
                "docker_network_name": cfg.network_name,
                "docker_image_name": cfg.image,
                "docker_container_name": cfg.container_name,
                "docker_container_ports": ["6379:6379"],
            },
        )
        self._file_write(
            os.path.join(svc_dir, "main.yml"),
            _REDIS_MAIN_TPL.format(group=host.name),
        )

    def _write_faucet(self, host_dir: str, host: ServicesHost) -> None:
        svc_dir = os.path.join(host_dir, "faucet")
        os.makedirs(svc_dir, exist_ok=True)
        cfg = host.faucet
        self._write_vars(
            os.path.join(svc_dir, "vars.yml"),
            {
                "docker_image_name": cfg.image,
                "docker_container_name": "faucet",
                "docker_container_ports": [f"{cfg.port}:{cfg.port}"],
                "docker_env_variables": {
                    "XRPL_FAUCET_URL": cfg.ws_url,
                    "XRPL_NETWORK_ID": cfg.network_id,
                    "XRPL_FAUCET_SEED": cfg.seed,
                },
            },
        )
        self._file_write(
            os.path.join(svc_dir, "main.yml"),
            _FAUCET_MAIN_TPL.format(group=host.name),
        )

    def _write_stream(self, host_dir: str, host: ServicesHost) -> None:
        svc_dir = os.path.join(host_dir, "stream")
        os.makedirs(svc_dir, exist_ok=True)
        cfg = host.stream
        self._write_vars(
            os.path.join(svc_dir, "vars.yml"),
            {
                "websocketd_port": cfg.port,
                "docker_container_name": cfg.container_name,
                "log_path": cfg.log_path,
            },
        )
        self._file_write(
            os.path.join(svc_dir, "main.yml"),
            _STREAM_MAIN_TPL.format(websocketd_url=_WEBSOCKETD_URL, group=host.name),
        )

    def _write_debug(self, host_dir: str, host: ServicesHost) -> None:
        svc_dir = os.path.join(host_dir, "debug")
        os.makedirs(svc_dir, exist_ok=True)
        cfg = host.debug
        endpoint = cfg.endpoint
        if not endpoint:
            stream_port = host.stream.port if host.stream else 1400
            endpoint = f"ws://{host.ip}:{stream_port}/"
        self._write_vars(
            os.path.join(svc_dir, "vars.yml"),
            {
                "docker_network_name": cfg.network_name,
                "docker_image_name": cfg.image,
                "docker_container_name": "debugstream",
                "docker_container_ports": [f"{cfg.port}:{cfg.port}"],
                "docker_env_variables": {
                    "PORT": str(cfg.port),
                    "ENDPOINT": endpoint,
                    "DEBUG": "stream*",
                    "REDIS_HOST": cfg.redis_host,
                    "REDIS_PORT": cfg.redis_port,
                },
            },
        )
        self._file_write(
            os.path.join(svc_dir, "main.yml"),
            _DEBUG_MAIN_TPL.format(group=host.name),
        )

    def _write_compiler(self, host_dir: str, host: ServicesHost) -> None:
        svc_dir = os.path.join(host_dir, "compiler")
        os.makedirs(svc_dir, exist_ok=True)
        cfg = host.compiler
        data: dict = {
            "docker_image_name": cfg.image,
            "docker_container_name": "compiler-api",
            "docker_container_ports": [f"{cfg.port}:{cfg.port}"],
            "docker_env_variables": {"PORT": str(cfg.port)},
            "compiler_repo": cfg.repo,
            "compiler_branch": cfg.branch,
        }
        if cfg.volumes:
            data["docker_volumes"] = cfg.volumes
        self._write_vars(os.path.join(svc_dir, "vars.yml"), data)
        self._file_write(
            os.path.join(svc_dir, "main.yml"),
            _COMPILER_MAIN_TPL.format(group=host.name),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _node_for_ip(self, ip: str) -> AnsibleNode | None:
        for n in self._nodes:
            if n.ip == ip:
                return n
        return None

    @staticmethod
    def _write_vars(path: str, data: dict) -> None:
        with open(path, "w") as f:
            yaml.dump(data, f, explicit_start=True, default_flow_style=False)

    @staticmethod
    def _file_write(path: str, content: str) -> None:
        with open(path, "w") as f:
            f.write(content)

    @staticmethod
    def _file_write_executable(path: str, content: str) -> None:
        with open(path, "w") as f:
            f.write(content)
        os.chmod(path, 0o755)


# ======================================================================
# Static playbook templates
# ======================================================================

_DEPS_YML = """---
- hosts: nodes
  become: true
  remote_user: root
  tasks:
  - name: Install apt prerequisites for the Docker repo
    apt:
      name:
      - apt-transport-https
      - ca-certificates
      - curl
      - gnupg
      state: present
      update_cache: yes
  - name: Add Docker apt signing key
    apt_key:
      url: https://download.docker.com/linux/ubuntu/gpg
      state: present
  - name: Add Docker apt repository (jammy)
    apt_repository:
      repo: deb [arch=amd64] https://download.docker.com/linux/ubuntu jammy stable
      state: present
      update_cache: yes
  - name: Install Docker
    apt:
      name:
      - docker-ce
      - docker-ce-cli
      - containerd.io
      state: present
  - name: Ensure Docker is started and enabled
    service:
      name: docker
      state: started
      enabled: yes
  - name: Add ubuntu user to the docker group
    user:
      name: ubuntu
      group: docker
"""

_MAIN_HEADER = """- hosts: nodes
  become: true
  remote_user: root

  tasks:
  - name: set docker to use systemd cgroups driver
    copy:
      dest: "/etc/docker/daemon.json"
      content: |
        {
          "exec-opts": ["native.cgroupdriver=systemd"]
        }
  - name: restart docker
    service:
      name: docker
      state: restarted
"""

# Rolling variant: one host at a time so the network keeps quorum, no docker daemon
# restart (it would bounce the running node), no state reset.
_MAIN_HEADER_ROLLING = """- hosts: nodes
  become: true
  remote_user: root
  serial: 1

  tasks:
"""

_MAIN_RESET_TASKS = """  - name: Remove Docker cache
    command: docker system prune --all --volumes --force
  - name: Remove Docker image
    command: docker rmi -f "{{ docker_image_name }}"
    ignore_errors: yes
  - name: Delete folders
    file:
      path: "{{ item }}"
      state: absent
    loop: "{{ volumes }}"
  - name: Stop the running node container so its NVMe DB can be reset cleanly
    docker_container:
      name: "{{ docker_container_name }}"
      state: stopped
    ignore_errors: yes
  - name: Reset node DB for a fresh genesis (Local NVMe persists across redeploys)
    shell: rm -rf /var/lib/xrpld/db/* 2>/dev/null || true
"""

_MAIN_DEPLOY_TASKS = """  - name: Create Docker Network
    docker_network:
      name: "{{ docker_network_name }}"
      state: present
  - name: Authenticate Docker to Artifact Registry with a fresh operator token
    shell: >-
      printf '%s' "$AR_TOKEN" | docker login -u oauth2accesstoken
      --password-stdin https://us-central1-docker.pkg.dev
    environment:
      AR_TOKEN: "{{ lookup('env', 'AR_TOKEN') }}"
    no_log: true
    when: lookup('env', 'AR_TOKEN') | length > 0
  - name: Pull base image (the binary-in-an-image)
    docker_image:
      name: "{{ docker_image_name }}"
      source: pull
  - name: Copy node build context (Dockerfile, entrypoint, genesis) to the remote server
    copy:
      src: "{{ build_context }}/"
      dest: /opt/ripple/build/
  - name: Copy config files to the remote server
    copy:
      src: "{{ config_path }}/"
      dest: /opt/ripple/config/
  - name: Build node image (wrap the binary image with the entrypoint, like standalone)
    docker_image:
      name: "{{ docker_build_tag }}"
      source: build
      force_source: yes
      build:
        path: /opt/ripple/build
        pull: no
  - name: Deploy Docker Image
    docker_container:
      name: "{{ docker_container_name }}"
      image: "{{ docker_build_tag }}"
      ports: "{{ docker_container_ports }}"
      volumes: "{{ docker_volumes }}"
      env: "{{ docker_env_variables }}"
      networks:
        - name: "{{ docker_network_name }}"
      state: started
      restart_policy: always
      image_name_mismatch: recreate
"""

# Alloy runs in the node container's network namespace, so rippled's [insight] loopback
# address reaches it with no published port. A recreated node container tears the
# namespace
# down, which is why this runs after every main.yml.
_ALLOY_YML = """---
- hosts: nodes
  become: true
  remote_user: root

  tasks:
  - name: Copy the Alloy build context to the remote server
    copy:
      src: alloy/
      dest: /opt/xrpl-monitoring/
  - name: Build the Alloy image
    docker_image:
      name: "{{ alloy_image }}"
      source: build
      force_source: yes
      build:
        path: /opt/xrpl-monitoring
        dockerfile: docker/alloy.Dockerfile
        pull: no
  - name: Wait for the node to write its perf log (the Alloy preflight requires it)
    wait_for:
      path: "{{ alloy_rippled_log_dir }}/perf.log"
      timeout: 180
    ignore_errors: yes
  - name: Deploy the Alloy sidecar
    docker_container:
      name: "{{ alloy_container_name }}"
      image: "{{ alloy_image }}"
      network_mode: "container:{{ docker_container_name }}"
      volumes:
        - "{{ alloy_rippled_config }}:/rippled-config/rippled.cfg:ro"
        - "{{ alloy_rippled_log_dir }}:/rippled-logs:ro"
        - "alloy-data-{{ docker_container_name }}:/var/lib/alloy/data"
      env: "{{ alloy_env_variables }}"
      state: started
      restart_policy: always
      recreate: yes
  - name: Report the sidecar state
    command: >-
      docker inspect -f '{% raw %}{{ .State.Status }}{% endraw %}'
      "{{ alloy_container_name }}"
    register: alloy_state
    changed_when: false
  - debug:
      msg: "alloy={{ alloy_state.stdout }}"
"""

_CLEAN_YML = """- hosts: nodes
  become: true
  remote_user: root

  tasks:
  - name: Stop the status sampler
    systemd:
      name: xrpld-status
      state: stopped
      enabled: no
    when: status_http_port is defined
    ignore_errors: yes
  - name: Remove the Alloy sidecar (its netns is the node container's)
    docker_container:
      name: "{{ alloy_container_name }}"
      state: absent
    when: alloy_container_name is defined
    ignore_errors: yes
  - name: Stop Docker Container
    docker_container:
      name: "{{ docker_container_name }}"
      state: stopped
    ignore_errors: yes
  - name: Remove Docker cache
    command: docker system prune --all --volumes --force
  - name: Remove Docker image
    command: docker rmi -f "{{ docker_image_name }}"
    ignore_errors: yes
  - name: Remove Docker network
    docker_network:
      name: "{{ docker_network_name }}"
      state: absent
  - name: Delete folders
    file:
      path: "{{ item }}"
      state: absent
    loop: "{{ volumes }}"
"""

# --- Nginx templates (use {group} and {ssh_port} placeholders) ---

_NGINX_DEPS_TPL = """---
- hosts: {group}
  become: true
  remote_user: root
  tasks:
  - name: Install NGINX
    apt:
      name: nginx
      state: present
  - name: Disable NGINX Default Virtual Host
    command:
      cmd: unlink /etc/nginx/sites-enabled/default
    ignore_errors: yes
  - name: Ensure UFW is installed
    apt:
      name: ufw
      state: present
  - name: Enable UFW
    ufw:
      state: enabled
      policy: allow
      direction: incoming
  - name: Enable SSH
    ufw:
      rule: limit
      port: {ssh_port}
      proto: tcp
  - name: Enable 80
    ufw:
      rule: allow
      port: 80
      proto: tcp
  - name: Enable 443
    ufw:
      rule: allow
      port: 443
      proto: tcp
  - name: Stop and disable the unattended-upgrades service
    service:
      name: unattended-upgrades
      state: stopped
      enabled: no
  - name: Generate dhparam
    command: openssl dhparam -out /etc/ssl/dhparam.pem 2048
    args:
      creates: "/etc/ssl/dhparam.pem"
    become_user: root
  - name: Delete existing ssl-params
    file:
      path: /etc/nginx/snippets/ssl-params.conf
      state: absent
  - name: Create NGINX SSL Params File
    file:
      path: /etc/nginx/snippets/ssl-params.conf
      state: touch
  - name: Write NGINX SSL Params
    blockinfile:
        path: /etc/nginx/snippets/ssl-params.conf
        marker: ""
        block: |
          ssl_protocols TLSv1.2 TLSv1.3;
          ssl_prefer_server_ciphers on;
          ssl_ciphers "EECDH+AESGCM:EDH+AESGCM:AES256+EECDH:AES256+EDH";
          ssl_ecdh_curve secp384r1;
          ssl_session_cache shared:SSL:10m;
          add_header Strict-Transport-Security
            "max-age=63072000; includeSubDomains; preload";
          add_header X-Frame-Options DENY;
          add_header X-Content-Type-Options nosniff;
          add_header X-XSS-Protection "1; mode=block";
          ssl_dhparam /etc/ssl/dhparam.pem;
  - name: Restart NGINX
    service:
      name: nginx
      state: restarted
      enabled: yes
"""

_NGINX_SSL_HEADER_TPL = """- hosts: {group}
  become: true
  remote_user: root

  vars_files:
    - vars.yml

  tasks:
"""

# TLS-terminated nginx services: key -> (task label, vars prefix). The bare
# domain ("wss") has no host label prefix; every other cn is <key>.<domain>.
NGINX_TLS_SERVICES = {
    "wss": ("WSS", "SSL"),
    "rpc": ("RPC", "RPC_SSL"),
    "faucet": ("Faucet", "FAUCET_SSL"),
    "debug": ("Debug", "DEBUG_SSL"),
    "compiler": ("Compiler", "COMPILER_SSL"),
    "vl": ("VL", "VL_SSL"),
}


def _nginx_tls_cn(service: str, domain: str) -> str:
    return domain if service == "wss" else f"{service}.{domain}"


def _ssl_selfsigned_tasks(label: str, var: str) -> str:
    reg = f"{label.lower()}_cert"
    return f"""  - name: Check {label} cert exists
    stat:
      path: "{{{{ {var}_CERT }}}}"
    register: {reg}
  - name: Create {label} private key
    community.crypto.openssl_privatekey:
      path: "{{{{ {var}_KEY }}}}"
    when: not {reg}.stat.exists
  - name: Create {label} CSR
    community.crypto.openssl_csr_pipe:
      privatekey_path: "{{{{ {var}_KEY }}}}"
      common_name: "{{{{ {var}_CN }}}}"
      organization_name: "{{{{ SSL_O }}}}"
      organizational_unit_name: "{{{{ SSL_OU }}}}"
    register: csr
    when: not {reg}.stat.exists
  - name: Create {label} self-signed certificate
    community.crypto.x509_certificate:
      path: "{{{{ {var}_CERT }}}}"
      csr_content: "{{{{ csr.csr }}}}"
      privatekey_path: "{{{{ {var}_KEY }}}}"
      provider: selfsigned
    when: not {reg}.stat.exists
"""


def _ssl_letsencrypt_tasks(cns: list, email: str) -> str:
    # Publicly-trusted certs for DNS-only (unproxied) hostnames. Standalone
    # HTTP-01 needs port 80, so nginx is stopped around issuance; the hooks
    # are persisted into the renewal config for the certbot systemd timer.
    email_arg = f"-m {email}" if email else "--register-unsafely-without-email"
    parts = [
        """  - name: Install certbot
    apt:
      name: certbot
      state: present
      update_cache: yes
"""
    ]
    for cn in cns:
        reg = "le_" + re.sub(r"[^a-z0-9]", "_", cn.lower())
        parts.append(
            f"""  - name: Check Let's Encrypt cert for {cn}
    stat:
      path: /etc/letsencrypt/live/{cn}/fullchain.pem
    register: {reg}
  - name: Issue Let's Encrypt certificate for {cn}
    command: >-
      certbot certonly --standalone --non-interactive --agree-tos
      {email_arg} --cert-name {cn} -d {cn}
      --pre-hook "systemctl stop nginx || true"
      --post-hook "systemctl start nginx || true"
    when: not {reg}.stat.exists
"""
        )
    return "".join(parts)


_NGINX_MAIN_TPL = """- hosts: {group}
  become: true
  remote_user: root

  vars_files:
    - vars.yml

  tasks:
  # WSS reverse proxy
  - name: Delete existing WSS conf
    file:
      path: "/etc/nginx/sites-available/{{{{ SSL_CN }}}}_proxy.conf"
      state: absent
  - name: Create WSS conf file
    file:
      path: "/etc/nginx/sites-available/{{{{ SSL_CN }}}}_proxy.conf"
      state: touch
  - name: Write WSS proxy config
    blockinfile:
        path: "/etc/nginx/sites-available/{{{{ SSL_CN }}}}_proxy.conf"
        marker: ""
        block: |
{status_zone}          server {{
              listen 80;
              server_name "{{{{ SSL_CN }}}}";
              return 301 https://$host$request_uri;
          }}
          server {{
              listen 443 ssl;
              server_name "{{{{ SSL_CN }}}}";
              ssl_certificate "{{{{ SSL_CERT }}}}";
              ssl_certificate_key "{{{{ SSL_KEY }}}}";
              include /etc/nginx/snippets/ssl-params.conf;
              access_log /var/log/nginx/access.log;
              location / {{
                  proxy_hide_header X-Powered-By;
                  proxy_pass_header Authorization;
                  proxy_set_header Upgrade $http_upgrade;
                  proxy_set_header Host $host;
                  proxy_set_header X-Real-IP $remote_addr;
                  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                  proxy_set_header X-Forwarded-Host $host;
                  proxy_set_header X-Forwarded-Proto $scheme;
                  proxy_http_version 1.1;
                  proxy_set_header Connection "upgrade";
                  proxy_read_timeout 90s;
                  proxy_redirect off;
                  proxy_pass http://{{{{ IP }}}}:{{{{ PORT }}}};
                  proxy_cache_bypass $http_upgrade;
                  add_header 'Access-Control-Allow-Origin' '*' always;
              }}
{status_locations}          }}
  - name: Link WSS proxy
    command:
      cmd: "ln -s /etc/nginx/sites-available/{{{{ SSL_CN }}}}_proxy.conf
        /etc/nginx/sites-enabled/{{{{ SSL_CN }}}}_proxy.conf"
    ignore_errors: yes
  # RPC reverse proxy
  - name: Delete existing RPC conf
    file:
      path: "/etc/nginx/sites-available/{{{{ RPC_SSL_CN }}}}_proxy.conf"
      state: absent
  - name: Create RPC conf file
    file:
      path: "/etc/nginx/sites-available/{{{{ RPC_SSL_CN }}}}_proxy.conf"
      state: touch
  - name: Write RPC proxy config
    blockinfile:
        path: "/etc/nginx/sites-available/{{{{ RPC_SSL_CN }}}}_proxy.conf"
        marker: ""
        block: |
          server {{
              listen 80;
              server_name "{{{{ RPC_SSL_CN }}}}";
              return 301 https://$host$request_uri;
          }}
          server {{
              listen 443 ssl;
              server_name "{{{{ RPC_SSL_CN }}}}";
              ssl_certificate "{{{{ RPC_SSL_CERT }}}}";
              ssl_certificate_key "{{{{ RPC_SSL_KEY }}}}";
              include /etc/nginx/snippets/ssl-params.conf;
              access_log /var/log/nginx/access.log;
              location / {{
                  proxy_hide_header X-Powered-By;
                  proxy_pass_header Authorization;
                  proxy_set_header Upgrade $http_upgrade;
                  proxy_set_header Host $host;
                  proxy_set_header X-Real-IP $remote_addr;
                  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                  proxy_set_header X-Forwarded-Host $host;
                  proxy_set_header X-Forwarded-Proto $scheme;
                  proxy_http_version 1.1;
                  proxy_set_header Connection "upgrade";
                  proxy_read_timeout 90s;
                  proxy_redirect off;
                  proxy_pass http://{{{{ RPC_IP }}}}:{{{{ RPC_PORT }}}};
                  proxy_cache_bypass $http_upgrade;
                  add_header 'Access-Control-Allow-Origin' '*' always;
              }}
          }}
  - name: Link RPC proxy
    command:
      cmd: "ln -s /etc/nginx/sites-available/{{{{ RPC_SSL_CN }}}}_proxy.conf
        /etc/nginx/sites-enabled/{{{{ RPC_SSL_CN }}}}_proxy.conf"
    ignore_errors: yes
  # Faucet reverse proxy
  - name: Delete existing Faucet conf
    file:
      path: "/etc/nginx/sites-available/{{{{ FAUCET_SSL_CN }}}}_proxy.conf"
      state: absent
  - name: Create Faucet conf file
    file:
      path: "/etc/nginx/sites-available/{{{{ FAUCET_SSL_CN }}}}_proxy.conf"
      state: touch
  - name: Write Faucet proxy config
    blockinfile:
        path: "/etc/nginx/sites-available/{{{{ FAUCET_SSL_CN }}}}_proxy.conf"
        marker: ""
        block: |
          server {{
              listen 80;
              server_name "{{{{ FAUCET_SSL_CN }}}}";
              return 301 https://$host$request_uri;
          }}
          server {{
              listen 443 ssl;
              server_name "{{{{ FAUCET_SSL_CN }}}}";
              ssl_certificate "{{{{ FAUCET_SSL_CERT }}}}";
              ssl_certificate_key "{{{{ FAUCET_SSL_KEY }}}}";
              include /etc/nginx/snippets/ssl-params.conf;
              access_log /var/log/nginx/access.log;
              location / {{
                  proxy_hide_header X-Powered-By;
                  proxy_pass_header Authorization;
                  proxy_set_header Upgrade $http_upgrade;
                  proxy_set_header Host $host;
                  proxy_set_header X-Real-IP $remote_addr;
                  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                  proxy_set_header X-Forwarded-Host $host;
                  proxy_set_header X-Forwarded-Proto $scheme;
                  proxy_http_version 1.1;
                  proxy_set_header Connection "upgrade";
                  proxy_read_timeout 90s;
                  proxy_redirect off;
                  proxy_pass http://{{{{ FAUCET_IP }}}}:{{{{ FAUCET_PORT }}}};
                  proxy_cache_bypass $http_upgrade;
              }}
          }}
  - name: Link Faucet proxy
    command:
      cmd: "ln -s /etc/nginx/sites-available/{{{{ FAUCET_SSL_CN }}}}_proxy.conf
        /etc/nginx/sites-enabled/{{{{ FAUCET_SSL_CN }}}}_proxy.conf"
    ignore_errors: yes
  # Debug reverse proxy
  - name: Delete existing Debug conf
    file:
      path: "/etc/nginx/sites-available/{{{{ DEBUG_SSL_CN }}}}_proxy.conf"
      state: absent
  - name: Create Debug conf file
    file:
      path: "/etc/nginx/sites-available/{{{{ DEBUG_SSL_CN }}}}_proxy.conf"
      state: touch
  - name: Write Debug proxy config
    blockinfile:
        path: "/etc/nginx/sites-available/{{{{ DEBUG_SSL_CN }}}}_proxy.conf"
        marker: ""
        block: |
          server {{
              listen 80;
              server_name "{{{{ DEBUG_SSL_CN }}}}";
              return 301 https://$host$request_uri;
          }}
          server {{
              listen 443 ssl;
              server_name "{{{{ DEBUG_SSL_CN }}}}";
              ssl_certificate "{{{{ DEBUG_SSL_CERT }}}}";
              ssl_certificate_key "{{{{ DEBUG_SSL_KEY }}}}";
              include /etc/nginx/snippets/ssl-params.conf;
              access_log /var/log/nginx/access.log;
              location / {{
                  proxy_hide_header X-Powered-By;
                  proxy_pass_header Authorization;
                  proxy_set_header Upgrade $http_upgrade;
                  proxy_set_header Host $host;
                  proxy_set_header X-Real-IP $remote_addr;
                  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                  proxy_set_header X-Forwarded-Host $host;
                  proxy_set_header X-Forwarded-Proto $scheme;
                  proxy_http_version 1.1;
                  proxy_set_header Connection "upgrade";
                  proxy_read_timeout 90s;
                  proxy_redirect off;
                  proxy_pass http://{{{{ DEBUG_IP }}}}:{{{{ DEBUG_PORT }}}};
                  proxy_cache_bypass $http_upgrade;
              }}
          }}
  - name: Link Debug proxy
    command:
      cmd: "ln -s /etc/nginx/sites-available/{{{{ DEBUG_SSL_CN }}}}_proxy.conf
        /etc/nginx/sites-enabled/{{{{ DEBUG_SSL_CN }}}}_proxy.conf"
    ignore_errors: yes
  # Compiler reverse proxy
  - name: Delete existing Compiler conf
    file:
      path: "/etc/nginx/sites-available/{{{{ COMPILER_SSL_CN }}}}_proxy.conf"
      state: absent
  - name: Create Compiler conf file
    file:
      path: "/etc/nginx/sites-available/{{{{ COMPILER_SSL_CN }}}}_proxy.conf"
      state: touch
  - name: Write Compiler proxy config
    blockinfile:
        path: "/etc/nginx/sites-available/{{{{ COMPILER_SSL_CN }}}}_proxy.conf"
        marker: ""
        block: |
          server {{
              listen 80;
              server_name "{{{{ COMPILER_SSL_CN }}}}";
              return 301 https://$host$request_uri;
          }}
          server {{
              listen 443 ssl;
              server_name "{{{{ COMPILER_SSL_CN }}}}";
              ssl_certificate "{{{{ COMPILER_SSL_CERT }}}}";
              ssl_certificate_key "{{{{ COMPILER_SSL_KEY }}}}";
              include /etc/nginx/snippets/ssl-params.conf;
              access_log /var/log/nginx/access.log;
              location / {{
                  proxy_hide_header X-Powered-By;
                  proxy_pass_header Authorization;
                  proxy_set_header Upgrade $http_upgrade;
                  proxy_set_header Host $host;
                  proxy_set_header X-Real-IP $remote_addr;
                  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                  proxy_set_header X-Forwarded-Host $host;
                  proxy_set_header X-Forwarded-Proto $scheme;
                  proxy_http_version 1.1;
                  proxy_set_header Connection "upgrade";
                  proxy_read_timeout 90s;
                  proxy_redirect off;
                  proxy_pass http://{{{{ COMPILER_IP }}}}:{{{{ COMPILER_PORT }}}};
                  proxy_cache_bypass $http_upgrade;
              }}
          }}
  - name: Link Compiler proxy
    command:
      cmd: "ln -s /etc/nginx/sites-available/{{{{ COMPILER_SSL_CN }}}}_proxy.conf
        /etc/nginx/sites-enabled/{{{{ COMPILER_SSL_CN }}}}_proxy.conf"
    ignore_errors: yes
  - name: Restart NGINX
    service:
      name: nginx
      state: restarted
      enabled: yes
"""

# --- Service templates (use {group} placeholder for hosts: directive) ---

# Static publisher-list vhost. Serves plain http always and adds a TLS server block only
# when the hostname has a Let's Encrypt cert, because LE cannot issue before the DNS
# record
# exists while http serving works as soon as it resolves. The list is signed, so nodes
# verify
# it against [validator_list_keys] regardless of transport.
_VL_MAIN_TPL = """---
- hosts: {group}
  become: true
  remote_user: root

  vars_files:
    - vars.yml

  tasks:
  - name: Create the publisher list web root
    file:
      path: "{{{{ VL_ROOT }}}}"
      state: directory
      mode: "0755"
  - name: Publish the signed validator list
    copy:
      src: "{{{{ VL_FILE }}}}"
      dest: "{{{{ VL_ROOT }}}}/{{{{ VL_FILE }}}}"
      mode: "0644"
  - name: Write the VL vhost
    copy:
      dest: "/etc/nginx/sites-available/{{{{ VL_SSL_CN }}}}_proxy.conf"
      content: |
        server {{
            listen 80;
            server_name "{{{{ VL_SSL_CN }}}}";
            root {{{{ VL_ROOT }}}};
            location / {{
                try_files $uri $uri/ =404;
                add_header Cache-Control "no-cache";
                default_type application/json;
            }}
        }}
      mode: "0644"
  - name: Add the TLS server block once the hostname has a certificate
    blockinfile:
      path: "/etc/nginx/sites-available/{{{{ VL_SSL_CN }}}}_proxy.conf"
      marker: "# {{mark}} VL TLS"
      block: |
        server {{
            listen 443 ssl;
            server_name "{{{{ VL_SSL_CN }}}}";
            ssl_certificate "{{{{ VL_SSL_CERT | default('') }}}}";
            ssl_certificate_key "{{{{ VL_SSL_KEY | default('') }}}}";
            root {{{{ VL_ROOT }}}};
            location / {{
                try_files $uri $uri/ =404;
                add_header Cache-Control "no-cache";
                default_type application/json;
            }}
        }}
    when: VL_TLS | bool
  - name: Enable the VL vhost
    file:
      src: "/etc/nginx/sites-available/{{{{ VL_SSL_CN }}}}_proxy.conf"
      dest: "/etc/nginx/sites-enabled/{{{{ VL_SSL_CN }}}}_proxy.conf"
      state: link
  - name: Check the nginx config before reloading
    command: nginx -t
    changed_when: false
  - name: Reload nginx
    service:
      name: nginx
      state: reloaded
  - name: Confirm the list is served locally
    uri:
      url: "http://127.0.0.1/{{{{ VL_FILE }}}}"
      headers:
        Host: "{{{{ VL_SSL_CN }}}}"
      return_content: yes
    register: vl_check
  - debug:
      msg: "vl served: {{{{ (vl_check.content | from_json).public_key
        | default('NO PUBLIC KEY') }}}}"
"""

_REDIS_MAIN_TPL = """- hosts: {group}
  become: true
  remote_user: root

  vars_files:
    - vars.yml

  tasks:
  - name: set docker to use systemd cgroups driver
    copy:
      dest: "/etc/docker/daemon.json"
      content: |
        {{
          "exec-opts": ["native.cgroupdriver=systemd"]
        }}
  - name: Stop Docker Container
    command: docker stop "{{{{ docker_container_name }}}}"
    ignore_errors: yes
  - name: Remove Docker image
    command: docker rmi -f "{{{{ docker_image_name }}}}"
    ignore_errors: yes
  - name: Create Docker Network
    docker_network:
      name: "{{{{ docker_network_name }}}}"
      state: present
  - name: Pull Docker Image
    docker_image:
      name: "{{{{ docker_image_name }}}}"
      source: pull
  - name: Deploy Docker Image
    docker_container:
      name: "{{{{ docker_container_name }}}}"
      image: "{{{{ docker_image_name }}}}"
      networks:
        - name: "{{{{ docker_network_name }}}}"
      state: started
      restart_policy: always
      image_name_mismatch: recreate
"""

_FAUCET_MAIN_TPL = """- hosts: {group}
  become: true
  remote_user: root

  vars_files:
    - vars.yml

  tasks:
  - name: set docker to use systemd cgroups driver
    copy:
      dest: "/etc/docker/daemon.json"
      content: |
        {{
          "exec-opts": ["native.cgroupdriver=systemd"]
        }}
  - name: Remove Docker image
    command: docker rmi -f "{{{{ docker_image_name }}}}"
    ignore_errors: yes
  - name: Pull Docker Image
    docker_image:
      name: "{{{{ docker_image_name }}}}"
      source: pull
  - name: Deploy Docker Image
    docker_container:
      name: "{{{{ docker_container_name }}}}"
      image: "{{{{ docker_image_name }}}}"
      ports: "{{{{ docker_container_ports }}}}"
      env: "{{{{ docker_env_variables }}}}"
      state: started
      restart_policy: always
      image_name_mismatch: recreate
"""

_WEBSOCKETD_URL = (
    "https://github.com/joewalnes/websocketd/releases/download/v0.4.1/"
    "websocketd-0.4.1-linux_amd64.zip"
)

_STREAM_MAIN_TPL = """---
- hosts: {group}
  become: true
  remote_user: root

  vars_files:
    - vars.yml

  tasks:
  - name: Download websocketd
    get_url:
      url: "{websocketd_url}"
      dest: "/tmp/websocketd.zip"
  - name: Install unzip
    package:
      name: unzip
      state: present
  - name: Unzip websocketd
    unarchive:
      src: "/tmp/websocketd.zip"
      dest: "/usr/local/bin/"
      remote_src: yes
  - name: Make websocketd executable
    file:
      path: "/usr/local/bin/websocketd"
      mode: '0755'
  - name: Create log viewer script
    copy:
      dest: "/usr/local/bin/node-logviewer.sh"
      mode: '0755'
      content: |
        #!/bin/bash
        docker exec {{{{ docker_container_name }}}} tail -f {{{{ log_path }}}}
  - name: Create systemd service
    copy:
      dest: "/etc/systemd/system/websocketd-node-logs.service"
      content: |
        [Unit]
        Description=WebSocket Log Viewer for node
        After=docker.service
        Requires=docker.service

        [Service]
        Type=simple
        ExecStart=/usr/local/bin/websocketd --port={{{{ websocketd_port }}}} \\
          /usr/local/bin/node-logviewer.sh
        Restart=always
        RestartSec=10

        [Install]
        WantedBy=multi-user.target
  - name: Reload systemd
    systemd:
      daemon_reload: yes
  - name: Enable and start log viewer
    systemd:
      name: websocketd-node-logs
      enabled: yes
      state: restarted
"""

_DEBUG_MAIN_TPL = """- hosts: {group}
  become: true
  remote_user: root

  vars_files:
    - vars.yml

  tasks:
  - name: set docker to use systemd cgroups driver
    copy:
      dest: "/etc/docker/daemon.json"
      content: |
        {{
          "exec-opts": ["native.cgroupdriver=systemd"]
        }}
  - name: Remove Docker image
    command: docker rmi -f "{{{{ docker_image_name }}}}"
    ignore_errors: yes
  - name: Pull Docker Image
    docker_image:
      name: "{{{{ docker_image_name }}}}"
      source: pull
  - name: Deploy Docker Image
    docker_container:
      name: "{{{{ docker_container_name }}}}"
      image: "{{{{ docker_image_name }}}}"
      ports: "{{{{ docker_container_ports }}}}"
      env: "{{{{ docker_env_variables }}}}"
      networks:
        - name: "{{{{ docker_network_name }}}}"
      state: started
      restart_policy: always
      image_name_mismatch: recreate
"""

_COMPILER_MAIN_TPL = """---
- hosts: {group}
  become: true
  remote_user: root

  vars_files:
    - vars.yml

  tasks:
  - name: set docker to use systemd cgroups driver
    ansible.builtin.copy:
      dest: "/etc/docker/daemon.json"
      content: |
        {{
          "exec-opts": ["native.cgroupdriver=systemd"]
        }}
  - name: Remove existing repository directory
    ansible.builtin.file:
      path: /opt/xrpl-hooks-compiler
      state: absent
  - name: Clone repository
    ansible.builtin.git:
      repo: "{{{{ compiler_repo }}}}"
      dest: /opt/xrpl-hooks-compiler
      version: "{{{{ compiler_branch }}}}"
      force: yes
      accept_hostkey: yes
      key_file: /root/.ssh/github
    environment:
      GIT_SSH_COMMAND: "ssh -i /root/.ssh/github -o StrictHostKeyChecking=accept-new"
  - name: Run Make
    ansible.builtin.shell: make 2>&1
    args:
      chdir: /opt/xrpl-hooks-compiler/docker
      executable: /bin/bash
    changed_when: true
  - name: Build Docker image locally
    community.docker.docker_image:
      name: "{{{{ docker_image_name }}}}"
      source: build
      build:
        path: /opt/xrpl-hooks-compiler/docker
        dockerfile: .
        platform: linux/x86_64
        nocache: yes
        args:
          APP_ENV: production
          APP_PORT: "5002"
      state: present
      force_source: yes
      timeout: 900
  - name: Stop Docker Container
    ansible.builtin.command: docker stop "{{{{ docker_container_name }}}}"
    ignore_errors: yes
  - name: Deploy Docker Image
    community.docker.docker_container:
      name: "{{{{ docker_container_name }}}}"
      image: "{{{{ docker_image_name }}}}"
      ports: "{{{{ docker_container_ports }}}}"
      env: "{{{{ docker_env_variables }}}}"
      state: started
      restart_policy: always
      image_name_mismatch: recreate
"""

# --- Status sampler + network roll-up ---

_STATUS_SOURCE_DIR = os.path.join(os.path.dirname(__file__), "services", "status")

# Rate limit shared by every /status/ location; sits above the server blocks (http
# context).
_STATUS_NGINX_ZONE = (
    "          limit_req_zone $binary_remote_addr zone=xrpld_status:10m rate=20r/s;\n"
)

# Plain string, not .format()-ed: the docker -f template keeps its braces under {% raw
# %}.
# ufw on the nodes defaults to allow incoming, so each allow rule is followed by a deny.
_STATUS_YML = """---
- hosts: nodes
  become: true
  remote_user: root

  handlers:
  - name: restart xrpld-status
    systemd:
      name: xrpld-status
      state: restarted
      daemon_reload: yes

  tasks:
  - name: Create the xrpld-status group
    group:
      name: xrpld-status
      system: yes
  - name: Create the xrpld-status user
    user:
      name: xrpld-status
      group: xrpld-status
      system: yes
      shell: /usr/sbin/nologin
      create_home: no
  - name: Create the sampler install directory
    file:
      path: /opt/xrpld-status
      state: directory
      owner: root
      group: root
      mode: "0755"
  - name: Create the sampler state directory
    file:
      path: /var/lib/xrpld-status
      state: directory
      owner: xrpld-status
      group: xrpld-status
      mode: "0750"
  - name: Install the sampler
    copy:
      src: status/node_metrics.py
      dest: /opt/xrpld-status/node_metrics.py
      owner: root
      group: root
      mode: "0755"
    notify: restart xrpld-status
  - name: Install the node dashboard
    copy:
      src: status/node-dashboard.html
      dest: /opt/xrpld-status/node-dashboard.html
      owner: root
      group: root
      mode: "0644"
  - name: Write the sampler environment
    copy:
      dest: /opt/xrpld-status/node_metrics.env
      owner: root
      group: xrpld-status
      mode: "0640"
      content: |
        {% for key, value in status_env.items() %}{{ key }}={{ value }}
        {% endfor %}
    notify: restart xrpld-status
  - name: Install the xrpld-status systemd unit
    copy:
      dest: /etc/systemd/system/xrpld-status.service
      owner: root
      group: root
      mode: "0644"
      content: |
        [Unit]
        Description=xrpld status sampler and status site
        After=network-online.target docker.service
        Wants=network-online.target

        [Service]
        Type=simple
        EnvironmentFile=/opt/xrpld-status/node_metrics.env
        ExecStart=/usr/bin/python3 /opt/xrpld-status/node_metrics.py
        Restart=always
        RestartSec=5s
        User=xrpld-status
        Group=xrpld-status
        NoNewPrivileges=true
        ProtectSystem=strict
        ReadWritePaths=/var/lib/xrpld-status
        ProtectHome=true
        PrivateTmp=true
        RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
        SystemCallArchitectures=native

        [Install]
        WantedBy=multi-user.target
    notify: restart xrpld-status
  - name: Ensure UFW is installed
    apt:
      name: ufw
      state: present
  - name: Enable UFW
    ufw:
      state: enabled
      policy: allow
      direction: incoming
  - name: Keep the SSH limit rule so the ruleset has a first position to insert before
    ufw:
      rule: limit
      port: "{{ ssh_port }}"
      proto: tcp
  - name: Allow the services host to reach the sampler
    ufw:
      rule: allow
      from_ip: "{{ status_allow_from }}"
      port: "{{ status_http_port }}"
      proto: tcp
      insert: 1
    when: status_allow_from | length > 0
  - name: Refuse the sampler port from anywhere else
    ufw:
      rule: deny
      port: "{{ status_http_port }}"
      proto: tcp
    when: status_allow_from | length > 0
  - name: Read the node's docker network subnets
    command: >-
      docker network inspect "{{ docker_network_name }}"
      -f '{% raw %}{{ range .IPAM.Config }}{{ .Subnet }} {{ end }}{% endraw %}'
    register: status_docker_subnets
    changed_when: false
  - name: Allow XDGM datagrams from the node container network
    ufw:
      rule: allow
      from_ip: "{{ item }}"
      port: "{{ status_xdgm_port }}"
      proto: udp
      insert: 1
    loop: "{{ status_docker_subnets.stdout.split() }}"
  - name: Refuse XDGM datagrams from anywhere else
    ufw:
      rule: deny
      port: "{{ status_xdgm_port }}"
      proto: udp
  - name: Enable and start xrpld-status
    systemd:
      name: xrpld-status
      enabled: yes
      state: started
      daemon_reload: yes
  - meta: flush_handlers
  - name: Wait for the sampler API to answer
    uri:
      url: "http://127.0.0.1:{{ status_http_port }}/api/latest"
      status_code: [200]
    register: status_probe
    retries: 10
    delay: 3
    until: status_probe.status == 200
"""

# Services host: the network page nginx serves at /status/ and the operator-written
# network.json the sampler merges into /api/network. Re-run every deploy so a new
# dashboard build lands; network.json is created empty once and never overwritten.
_STATUS_SITE_MAIN_TPL = """---
- hosts: {group}
  become: true
  remote_user: root

  vars_files:
    - vars.yml

  tasks:
  - name: Create the network dashboard web root
    file:
      path: "{{{{ STATUS_DIR }}}}/www"
      state: directory
      owner: root
      group: root
      mode: "0755"
  - name: Install the network dashboard
    copy:
      src: ../../../status/network-dashboard.html
      dest: "{{{{ STATUS_DIR }}}}/www/network-dashboard.html"
      owner: root
      group: root
      mode: "0644"
  - name: Create an empty network.json for the operator to fill in after a deploy
    copy:
      dest: "{{{{ STATUS_DIR }}}}/network.json"
      content: "{{{{ '{{}}' }}}}\n"
      owner: root
      group: root
      mode: "0644"
      force: no
  - name: Confirm the roll-up answers
    uri:
      url: "http://127.0.0.1:{{{{ STATUS_PORT }}}}/api/network"
      return_content: yes
    register: status_network
    retries: 5
    delay: 3
    until: status_network.status == 200
  - debug:
      msg: "status nodes: {{{{ (status_network.content | from_json).nodes
        | map(attribute='name') | join(',') }}}}"
"""
