#!/usr/bin/env python
# coding: utf-8

"""Shell script and Dockerfile generation for xrpld nodes.

Replaces the functions previously in ``xrpld_netgen/utils/deploy_kit.py``.
"""

from __future__ import annotations

from typing import List, Optional

from xrpld_lab.models import PortSet


# ---------------------------------------------------------------------------
# DockerfileBuilder
# ---------------------------------------------------------------------------


class DockerfileBuilder:
    """Generates Dockerfile content for xrpld nodes."""

    @staticmethod
    def build(
        protocol: str,
        ports: PortSet,
        image_name: str,
        network: bool = False,
        binary: bool = False,
        version: str = "",
        include_genesis: bool = False,
        quorum: Optional[int] = None,
        standalone: Optional[str] = None,
        data_port: int = 12345,
        db_seed: bool = False,
    ) -> str:
        """Generate Dockerfile content.

        Parameters
        ----------
        protocol:
            Protocol name (``"xrpl"``).
        ports:
            Port assignments for the node.
        image_name:
            Base Docker image.
        network:
            If *True* emit ENV/EXPOSE with variable references (network mode).
        binary:
            If *True* add a COPY line for the protocol binary.
        version:
            Binary version suffix (used with *binary*).
        include_genesis:
            If *True* add COPY genesis.json and genesis ENTRYPOINT.
        db_seed:
            If *True* boot with ``--load`` from the node's (snapshot-restored)
            database directory: no genesis.json in the image, entrypoint's
            first arg is the ``--load`` sentinel. Overrides *include_genesis*.
        quorum:
            Quorum value passed to the entrypoint when *include_genesis*.
        standalone:
            Standalone flag passed to the entrypoint when *include_genesis*.
        data_port:
            Data port for network mode (default 12345).
        """
        dockerfile = f"""
    FROM {image_name} as base

    USER root

    WORKDIR /app

    LABEL maintainer="dangell@transia.co"

    RUN export LANGUAGE=C.UTF-8; export LANG=C.UTF-8; export LC_ALL=C.UTF-8; export DEBIAN_FRONTEND=noninteractive

    COPY entrypoint /entrypoint.sh
    """  # noqa: E501

        if not network:
            dockerfile += "COPY config /config\n"

        if include_genesis and not db_seed:
            dockerfile += "COPY genesis.json /genesis.json\n"

        if binary:
            dockerfile += (
                f"COPY {protocol}d.{version} /opt/{protocol}d/bin/{protocol}d\n"
                # The execute bit is not reliably preserved through the ansible
                # copy → docker COPY layers, so set it explicitly in-image.
                f"RUN chmod +x /opt/{protocol}d/bin/{protocol}d\n"
            )

        if network:
            dockerfile += f"""
    ENV RPC_PUBLIC={ports.rpc_public}
    ENV RPC_ADMIN={ports.rpc_admin}
    ENV WS_PUBLIC={ports.ws_public}
    ENV WS_ADMIN={ports.ws_admin}
    ENV PEER={ports.peer}
    ENV DATA={data_port}

    EXPOSE $RPC_PUBLIC $RPC_ADMIN $WS_PUBLIC $WS_ADMIN $PEER $PEER/udp $DATA $DATA/udp
        """

        dockerfile += """
    RUN chmod +x /entrypoint.sh && \
        echo '#!/bin/bash' > /usr/bin/server_info && \
        echo '/entrypoint.sh server_info' >> /usr/bin/server_info && \
        chmod +x /usr/bin/server_info
    """  # noqa: E501

        if not network:
            dockerfile += (
                f"EXPOSE {ports.rpc_public} {ports.rpc_admin}"
                f" {ports.ws_public} {ports.ws_admin}"
                f" {ports.peer} {ports.peer}/udp\n"
            )

        if include_genesis or db_seed:
            # A single-node standalone has no quorum; render empty (not "None")
            # so the entrypoint omits --quorum instead of passing --quorum=None.
            quorum_arg = "" if quorum is None else str(quorum)
            # db-seed: no genesis file in the image — the node boots with --load
            # from its (snapshot-restored) database directory.
            first = '"--load"' if db_seed else '"/genesis.json"'
            parts = ['"/entrypoint.sh"', first, f'"{quorum_arg}"']
            if standalone:
                parts.append(f'"{standalone}"')
            dockerfile += f'ENTRYPOINT [ {", ".join(parts)} ]'
        else:
            dockerfile += 'ENTRYPOINT [ "/entrypoint.sh" ]'

        return dockerfile


# ---------------------------------------------------------------------------
# ScriptBuilder
# ---------------------------------------------------------------------------


class ScriptBuilder:
    """Generates shell scripts for starting/stopping xrpld nodes."""

    # -- standalone ---------------------------------------------------------

    @staticmethod
    def standalone_start(basedir: str, protocol: str, name: str) -> str:
        """Docker compose up for standalone."""
        return (
            "#! /bin/bash\n"
            "docker compose"
            f" -f {basedir}/{protocol}-{name}"
            "/docker-compose.yml"
            " up --build --force-recreate -d\n"
        )

    @staticmethod
    @staticmethod
    def wipe_as_root(paths: List[str]) -> str:
        """Shell line that deletes bind-mounted paths the node wrote as root."""
        targets = " ".join(f"/wipe/{p}" for p in paths)
        return f'docker run --rm -v "$(pwd):/wipe" alpine:3 rm -rf {targets}\n'

    @staticmethod
    def standalone_stop(basedir: str, protocol: str, name: str) -> str:
        """Docker compose down + cleanup for standalone."""
        content = "#! /bin/bash\n"
        content += (
            f"docker compose -f {basedir}/{protocol}-{name}"
            "/docker-compose.yml down --remove-orphans\n"
        )
        content += ScriptBuilder.wipe_as_root([protocol])
        return content

    # -- local (non-Docker single node) ------------------------------------

    # -- network (Docker multi-node) ---------------------------------------

    @staticmethod
    def network_start() -> str:
        """Network docker start: compose up from the node build contexts."""
        return (
            "#! /bin/bash \n"
            "docker compose -f docker-compose.yml up --build --force-recreate -d"
        )

    @staticmethod
    def network_stop(name: str, num_validators: int, num_peers: int) -> str:
        """Network docker stop with ``--remove`` flag support."""
        content = "#! /bin/bash\n"
        content += "REMOVE_FLAG=false \n"
        content += """
for arg in "$@"; do
  if [ "$arg" == "--remove" ]; then
    REMOVE_FLAG=true
    break
  fi
done
"""
        content += "\n"
        content += 'if [ "$REMOVE_FLAG" = true ]; then \n'
        content += "docker compose -f docker-compose.yml down --remove-orphans\n"

        nodes = [f"vnode{i}" for i in range(1, num_validators + 1)]
        nodes += [f"pnode{i}" for i in range(1, num_peers + 1)]
        content += ScriptBuilder.wipe_as_root(
            [
                f"{n}/{leaf}"
                for n in nodes
                for leaf in ("lib", "log", "db", f"xrpld.{name}")
            ]
        )

        content += "else \n"
        content += "docker compose -f docker-compose.yml down\n"
        content += "fi \n"
        return content

    # -- local network (native multi-node) ---------------------------------

    @staticmethod
    def local_network_start(
        name: str,
        num_validators: int,
        num_peers: int,
        binary_name: str = "xrpld",
        genesis: bool = True,
        ws_node: str = "vnode1",
        ws_port: int = 6106,
    ) -> str:
        """Local multi-node start: binary discovery, copy, nohup + PID files.

        Binary lookup: ``$CLUSTER_DIR/../{binary_name}``, then PATH, then error.
        Copies to each vnode/pnode.
        Starts each with nohup + PID file.
        Starts Docker services (Explorer & VL) first.
        """
        s = "#! /bin/bash\n\n"
        s += "# Start Explorer and VL services in Docker\n"
        s += "echo 'Starting Docker services (Explorer & VL)...'\n"
        s += (
            "docker compose -f docker-compose.yml" " up --build --force-recreate -d\n\n"
        )
        s += "# Wait for services to be ready\n"
        s += "sleep 2\n\n"

        # Cluster directory
        s += "# Get the absolute path to the cluster directory\n"
        s += 'CLUSTER_DIR="$(cd "$(dirname "$0")" && pwd)"\n\n'

        # Binary discovery
        s += "# Locate xrpld binary\n"
        s += f'if [ -f "$CLUSTER_DIR/{binary_name}" ]; then\n'
        s += f'  BINARY_PATH="$CLUSTER_DIR/{binary_name}"\n'
        s += f'elif [ -f "$CLUSTER_DIR/../{binary_name}" ]; then\n'
        s += f'  BINARY_PATH="$CLUSTER_DIR/../{binary_name}"\n'
        s += f"elif command -v {binary_name} &> /dev/null; then\n"
        s += f"  BINARY_PATH=$(command -v {binary_name})\n"
        s += "else\n"
        s += f"  echo 'Error: {binary_name} binary not found!'\n"
        s += f"  echo 'Please ensure {binary_name} is either:'\n"
        s += "  echo '  1. In the current directory:" f" $CLUSTER_DIR/{binary_name}'\n"
        s += "  echo '  2. In your PATH" f" (e.g., /usr/local/bin/{binary_name})'\n"
        s += "  exit 1\n"
        s += "fi\n\n"
        s += 'echo "Using binary: $BINARY_PATH"\n\n'

        # Copy binary to each node
        s += "# Copy xrpld binary to each node (if not already present)\n"
        for i in range(1, num_validators + 1):
            s += (
                f'if [ ! -f "vnode{i}/{binary_name}" ]'
                ' || [ "$BINARY_PATH" -nt'
                f' "vnode{i}/{binary_name}" ]; then\n'
            )
            s += f'  cp "$BINARY_PATH" vnode{i}/{binary_name}\n'
            s += f"  echo 'Copied binary to vnode{i}'\n"
            s += "fi\n"
        for i in range(1, num_peers + 1):
            s += (
                f'if [ ! -f "pnode{i}/{binary_name}" ]'
                ' || [ "$BINARY_PATH" -nt'
                f' "pnode{i}/{binary_name}" ]; then\n'
            )
            s += f'  cp "$BINARY_PATH" pnode{i}/{binary_name}\n'
            s += f"  echo 'Copied binary to pnode{i}'\n"
            s += "fi\n"
        s += "\n"

        # Build xrpld flags
        genesis_flags = " --ledgerfile config/genesis.json --valid" if genesis else ""

        # Start validator nodes
        s += "# Start validator nodes in background\n"
        for i in range(1, num_validators + 1):
            s += f"echo 'Starting vnode{i} in background...'\n"
            s += f'cd "$CLUSTER_DIR/vnode{i}"\n'
            s += (
                f"nohup ./{binary_name} --conf config/xrpld.cfg"
                f"{genesis_flags}"
                " > /dev/null 2>&1 &\n"
            )
            s += f'echo $! > "$CLUSTER_DIR/vnode{i}/xrpld.pid"\n'
            s += 'cd "$CLUSTER_DIR"\n'

        # Start peer nodes
        s += "\n# Start peer nodes in background\n"
        for i in range(1, num_peers + 1):
            s += f"echo 'Starting pnode{i} in background...'\n"
            s += f'cd "$CLUSTER_DIR/pnode{i}"\n'
            s += (
                f"nohup ./{binary_name} --conf config/xrpld.cfg"
                f"{genesis_flags}"
                " > /dev/null 2>&1 &\n"
            )
            s += f'echo $! > "$CLUSTER_DIR/pnode{i}/xrpld.pid"\n'
            s += 'cd "$CLUSTER_DIR"\n'

        # Wait and summary
        s += "\n# Wait for nodes to start\n"
        s += "sleep 3\n\n"
        s += "echo ''\n"
        s += "echo 'Local network started!'\n"
        s += "echo ''\n"
        s += "echo 'Validator nodes: "
        s += " ".join([f"vnode{i}" for i in range(1, num_validators + 1)])
        s += "'\n"
        s += "echo 'Peer nodes: "
        s += " ".join([f"pnode{i}" for i in range(1, num_peers + 1)])
        s += "'\n"
        s += "echo ''\n"
        s += "echo 'Each node is running in the background.'\n"
        s += (
            "echo 'Use \"xrpld-lab logs:local"
            ' --node <node_name>" to view logs'
            " (e.g., --node vnode1).'\n"
        )
        s += "echo 'Use \"./stop.sh\" to stop all nodes.'\n"
        s += "echo ''\n"
        s += "echo 'Explorer UI: http://localhost:4000'\n"
        s += f"echo '{ws_node} admin WebSocket: ws://127.0.0.1:{ws_port}'\n"

        return s

    @staticmethod
    def local_network_stop(
        name: str,
        num_validators: int,
        num_peers: int,
    ) -> str:
        """Local multi-node stop: kill each node by its PID file.

        Has ``--remove`` flag support for cleanup.
        """
        s = "#! /bin/bash\n\n"
        s += "REMOVE_FLAG=false\n\n"
        s += """for arg in "$@"; do
  if [ "$arg" == "--remove" ]; then
    REMOVE_FLAG=true
    break
  fi
done

"""

        s += "echo 'Stopping all xrpld nodes...'\n\n"

        s += "# Get the absolute path to the cluster directory\n"
        s += 'CLUSTER_DIR="$(cd "$(dirname "$0")" && pwd)"\n\n'

        # Stop validator nodes
        s += "# Stop validator nodes\n"
        for i in range(1, num_validators + 1):
            s += f"echo 'Stopping vnode{i}...'\n"
            s += f'if [ -f "$CLUSTER_DIR/vnode{i}/xrpld.pid" ]; then\n'
            s += f'  PID=$(cat "$CLUSTER_DIR/vnode{i}/xrpld.pid")\n'
            s += "  if ps -p $PID > /dev/null 2>&1; then\n"
            s += "    kill $PID 2>/dev/null || true\n"
            s += "    sleep 1\n"
            s += "    # Force kill if still running\n"
            s += "    if ps -p $PID > /dev/null 2>&1; then\n"
            s += "      kill -9 $PID 2>/dev/null || true\n"
            s += "    fi\n"
            s += "  fi\n"
            s += f'  rm -f "$CLUSTER_DIR/vnode{i}/xrpld.pid"\n'
            s += "fi\n"

        # Stop peer nodes
        s += "\n# Stop peer nodes\n"
        for i in range(1, num_peers + 1):
            s += f"echo 'Stopping pnode{i}...'\n"
            s += f'if [ -f "$CLUSTER_DIR/pnode{i}/xrpld.pid" ]; then\n'
            s += f'  PID=$(cat "$CLUSTER_DIR/pnode{i}/xrpld.pid")\n'
            s += "  if ps -p $PID > /dev/null 2>&1; then\n"
            s += "    kill $PID 2>/dev/null || true\n"
            s += "    sleep 1\n"
            s += "    # Force kill if still running\n"
            s += "    if ps -p $PID > /dev/null 2>&1; then\n"
            s += "      kill -9 $PID 2>/dev/null || true\n"
            s += "    fi\n"
            s += "  fi\n"
            s += f'  rm -f "$CLUSTER_DIR/pnode{i}/xrpld.pid"\n'
            s += "fi\n"

        # Wait and Docker stop
        s += "\n# Wait for processes to terminate\n"
        s += "sleep 2\n\n"

        s += "# Stop Docker services\n"
        s += 'if [ "$REMOVE_FLAG" = true ]; then\n'
        s += "  echo 'Cleaning up Docker services and data...'\n"
        s += "  docker compose -f docker-compose.yml down --remove-orphans\n"

        for i in range(1, num_validators + 1):
            s += f"  rm -rf vnode{i}/lib vnode{i}/log" f" vnode{i}/xrpld vnode{i}/db\n"
        for i in range(1, num_peers + 1):
            s += f"  rm -rf pnode{i}/lib pnode{i}/log" f" pnode{i}/xrpld pnode{i}/db\n"

        s += "else\n"
        s += "  echo 'Stopping Docker services...'\n"
        s += "  docker compose -f docker-compose.yml down\n"
        s += "fi\n\n"
        s += "echo ''\n"
        s += "echo 'Local network stopped.'\n"

        return s
