#!/usr/bin/env python
# coding: utf-8

import pytest
from xrpld_lab.models import PortSet
from xrpld_lab.script_builder import DockerfileBuilder, ScriptBuilder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_ports() -> PortSet:
    return PortSet(
        rpc_public=5007, rpc_admin=5005, ws_public=6008, ws_admin=6006, peer=51235
    )


# ---------------------------------------------------------------------------
# DockerfileBuilder
# ---------------------------------------------------------------------------


class TestDockerfileBuilder:
    """Tests for DockerfileBuilder.build()."""

    def test_standalone_dockerfile(self):
        """Standalone mode: COPY config, literal EXPOSE, ENTRYPOINT with genesis."""
        ports = _default_ports()
        result = DockerfileBuilder.build(
            protocol="xrpl",
            ports=ports,
            image_name="xrpld-base:latest",
            network=False,
            binary=False,
            version="",
            include_genesis=True,
            quorum=1,
            standalone="true",
        )
        # Should COPY config (non-network)
        assert "COPY config /config" in result
        # Should NOT have ENV vars (non-network)
        assert "ENV RPC_PUBLIC=" not in result
        # Should have literal port EXPOSE
        assert "EXPOSE 5007 5005 6008 6006 51235 51235/udp" in result
        # Should have ENTRYPOINT with genesis
        assert 'ENTRYPOINT [ "/entrypoint.sh", "/genesis.json", "1", "true" ]' in result

    def test_db_seed_dockerfile(self):
        """db-seed mode: no genesis.json in the image; ENTRYPOINT boots with --load."""
        result = DockerfileBuilder.build(
            protocol="xrpl",
            ports=_default_ports(),
            image_name="xrpld-base:latest",
            network=True,
            include_genesis=True,
            quorum=3,
            db_seed=True,
        )
        assert "COPY genesis.json" not in result
        assert 'ENTRYPOINT [ "/entrypoint.sh", "--load", "3" ]' in result

    def test_network_dockerfile(self):
        """Network mode: ENV vars, $VAR EXPOSE, COPY entrypoint, no COPY config."""
        ports = _default_ports()
        result = DockerfileBuilder.build(
            protocol="xrpl",
            ports=ports,
            image_name="xrpld-base:latest",
            network=True,
            binary=False,
            version="",
        )
        # Should NOT have COPY config (network mode)
        assert "COPY config /config" not in result
        # Should have ENV vars
        assert "ENV RPC_PUBLIC=5007" in result
        assert "ENV RPC_ADMIN=5005" in result
        assert "ENV WS_PUBLIC=6008" in result
        assert "ENV WS_ADMIN=6006" in result
        assert "ENV PEER=51235" in result
        assert "ENV DATA=12345" in result
        # Should have $VAR EXPOSE
        assert (
            "$RPC_PUBLIC $RPC_ADMIN $WS_PUBLIC $WS_ADMIN $PEER $PEER/udp $DATA $DATA/udp"
            in result
        )
        # Should have COPY entrypoint
        assert "COPY entrypoint /entrypoint.sh" in result
        # ENTRYPOINT without genesis (default)
        assert 'ENTRYPOINT [ "/entrypoint.sh" ]' in result

    def test_binary_mode(self):
        """Binary mode: COPY binary line present."""
        ports = _default_ports()
        result = DockerfileBuilder.build(
            protocol="xrpl",
            ports=ports,
            image_name="xrpld-base:latest",
            network=False,
            binary=True,
            version="1.2.3",
        )
        assert "COPY xrpld.1.2.3 /opt/xrpld/bin/xrpld" in result

    def test_no_binary(self):
        """No binary mode: no COPY binary line."""
        ports = _default_ports()
        result = DockerfileBuilder.build(
            protocol="xrpl",
            ports=ports,
            image_name="xrpld-base:latest",
            network=False,
            binary=False,
            version="",
        )
        assert "COPY xrpld" not in result or "COPY entrypoint" in result
        # More precise: should not have COPY <protocol>d.<version>
        assert "/app/xrpld" not in result

    def test_include_genesis(self):
        """Include genesis: COPY genesis.json and ENTRYPOINT with genesis args."""
        ports = _default_ports()
        result = DockerfileBuilder.build(
            protocol="xrpl",
            ports=ports,
            image_name="xrpld-base:latest",
            network=False,
            binary=False,
            version="",
            include_genesis=True,
            quorum=2,
            standalone="false",
        )
        assert "COPY genesis.json /genesis.json" in result
        assert (
            'ENTRYPOINT [ "/entrypoint.sh", "/genesis.json", "2", "false" ]' in result
        )

    def test_no_genesis(self):
        """No genesis: ENTRYPOINT without genesis args."""
        ports = _default_ports()
        result = DockerfileBuilder.build(
            protocol="xrpl",
            ports=ports,
            image_name="xrpld-base:latest",
            network=False,
            binary=False,
            version="",
            include_genesis=False,
        )
        assert "COPY genesis.json" not in result
        assert 'ENTRYPOINT [ "/entrypoint.sh" ]' in result

    def test_dockerfile_common_elements(self):
        """Common elements present in all Dockerfiles."""
        ports = _default_ports()
        result = DockerfileBuilder.build(
            protocol="xrpl",
            ports=ports,
            image_name="myimage:v1",
            network=False,
            binary=False,
            version="",
        )
        assert "FROM myimage:v1 as base" in result
        assert result.index("USER root") > result.index("FROM myimage:v1 as base")
        assert result.index("USER root") < result.index("COPY entrypoint")
        assert "WORKDIR /app" in result
        assert 'LABEL maintainer="dangell@transia.co"' in result
        assert "COPY entrypoint /entrypoint.sh" in result
        assert "chmod +x /entrypoint.sh" in result
        assert "server_info" in result


# ---------------------------------------------------------------------------
# ScriptBuilder — standalone
# ---------------------------------------------------------------------------


class TestScriptBuilderStandalone:
    """Tests for standalone start/stop scripts."""

    def test_standalone_start(self):
        """Docker compose up for standalone."""
        result = ScriptBuilder.standalone_start(
            basedir="/opt/cluster",
            protocol="xrpl",
            name="test-net",
        )
        assert "#! /bin/bash" in result
        assert "docker compose" in result
        assert "-f /opt/cluster/xrpl-test-net/docker-compose.yml" in result
        assert "up --build --force-recreate -d" in result

    def test_standalone_stop(self):
        """Docker compose down + cleanup for standalone."""
        result = ScriptBuilder.standalone_stop(
            basedir="/opt/cluster",
            protocol="xrpl",
            name="test-net",
        )
        assert "#! /bin/bash" in result
        assert "docker compose" in result
        assert "-f /opt/cluster/xrpl-test-net/docker-compose.yml" in result
        assert "down --remove-orphans" in result
        # Standalone cleanup dirs
        assert (
            'docker run --rm -v "$(pwd):/wipe" alpine:3 rm -rf /wipe/xrpl\n' in result
        )
        assert "rm -r " not in result


# ---------------------------------------------------------------------------
# ScriptBuilder — network start/stop
# ---------------------------------------------------------------------------


class TestScriptBuilderNetwork:
    """Tests for network Docker start/stop scripts."""

    def test_network_start(self):
        """Compose up only; the binary is already in each node directory."""
        result = ScriptBuilder.network_start()
        assert "#! /bin/bash" in result
        assert "cp " not in result
        assert "docker compose -f docker-compose.yml" in result
        assert "up --build --force-recreate -d" in result

    def test_network_stop(self):
        """REMOVE_FLAG, compose down with --remove-orphans, cleanup."""
        result = ScriptBuilder.network_stop(
            name="testnet",
            num_validators=2,
            num_peers=1,
        )
        assert "#! /bin/bash" in result
        assert "REMOVE_FLAG=false" in result
        assert "--remove" in result
        # --remove-orphans in the remove branch
        assert "down --remove-orphans" in result
        # Cleanup for vnodes
        wipe = [line for line in result.splitlines() if line.startswith("docker run")]
        assert len(wipe) == 1
        for node in ("vnode1", "vnode2", "pnode1"):
            for leaf in ("lib", "log", "xrpld.testnet"):
                assert f" /wipe/{node}/{leaf}" in wipe[0], (node, leaf)
        assert "rm -r " not in result
        # Else branch with just compose down (no --remove-orphans)
        assert "docker compose -f docker-compose.yml down\n" in result
        # fi closing
        assert "fi" in result


# ---------------------------------------------------------------------------
# ScriptBuilder — local network start/stop
# ---------------------------------------------------------------------------


class TestScriptBuilderLocalNetwork:
    """Tests for local multi-node start/stop scripts."""

    def test_local_network_start(self):
        """Binary discovery, copy, nohup start, PID files."""
        result = ScriptBuilder.local_network_start(
            name="local-net",
            num_validators=2,
            num_peers=1,
            binary_name="xrpld",
        )
        assert "#! /bin/bash" in result
        # Docker services start first
        assert "docker compose -f docker-compose.yml" in result
        assert "up --build --force-recreate -d" in result
        # Binary discovery
        assert 'CLUSTER_DIR="$(cd "$(dirname "$0")" && pwd)"' in result
        assert "$CLUSTER_DIR/../xrpld" in result
        assert "command -v xrpld" in result
        assert "BINARY_PATH" in result
        # Copy binary to nodes
        assert 'cp "$BINARY_PATH" vnode1/xrpld' in result
        assert 'cp "$BINARY_PATH" vnode2/xrpld' in result
        assert 'cp "$BINARY_PATH" pnode1/xrpld' in result
        # nohup start
        assert "nohup ./xrpld --conf config/xrpld.cfg" in result
        assert "--ledgerfile config/genesis.json --valid" in result
        # PID files
        assert "xrpld.pid" in result
        assert "echo $!" in result
        # Node lists in output
        assert "vnode1" in result
        assert "vnode2" in result
        assert "pnode1" in result
        assert 'Use "xrpld-lab logs:local --node <node_name>"' in result
        assert "xrpld-netgen" not in result

    def test_local_network_start_names_the_explorer_endpoint(self):
        result = ScriptBuilder.local_network_start(
            name="local-net",
            num_validators=2,
            num_peers=1,
            ws_node="pnode1",
            ws_port=7016,
        )
        assert "echo 'pnode1 admin WebSocket: ws://127.0.0.1:7016'" in result
        assert "6016" not in result

    def test_local_network_start_no_genesis(self):
        """Without genesis: no --ledgerfile or --valid flags."""
        result = ScriptBuilder.local_network_start(
            name="local-net",
            num_validators=2,
            num_peers=1,
            genesis=False,
        )
        assert "--ledgerfile" not in result
        assert "--valid" not in result
        assert "nohup ./xrpld --conf config/xrpld.cfg" in result

    def test_local_network_start_custom_binary(self):
        """Custom binary name is used throughout."""
        result = ScriptBuilder.local_network_start(
            name="local-net",
            num_validators=1,
            num_peers=0,
            binary_name="rippled",
        )
        assert "$CLUSTER_DIR/../rippled" in result
        assert "command -v rippled" in result
        assert 'cp "$BINARY_PATH" vnode1/rippled' in result
        assert "nohup ./rippled" in result

    def test_local_network_stop(self):
        """PID-file kill and Docker stop; no process-name matching."""
        result = ScriptBuilder.local_network_stop(
            name="local-net",
            num_validators=2,
            num_peers=1,
        )
        assert "#! /bin/bash" in result
        assert "REMOVE_FLAG=false" in result
        assert "--remove" in result
        # PID-based kill
        assert "xrpld.pid" in result
        assert "kill $PID" in result
        assert "kill -9 $PID" in result
        assert 'PID=$(cat "$CLUSTER_DIR/vnode1/xrpld.pid")' in result
        assert 'PID=$(cat "$CLUSTER_DIR/vnode2/xrpld.pid")' in result
        assert 'PID=$(cat "$CLUSTER_DIR/pnode1/xrpld.pid")' in result
        assert "pkill" not in result
        # Docker stop
        assert "docker compose -f docker-compose.yml down --remove-orphans" in result
        assert "docker compose -f docker-compose.yml down\n" in result
        # Cleanup on --remove
        assert "rm -rf vnode1/lib vnode1/log" in result
        assert "rm -rf vnode2/lib vnode2/log" in result
        assert "rm -rf pnode1/lib pnode1/log" in result
        # Final message
        assert "Local network stopped" in result
