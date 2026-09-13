"""Operational helpers for the xrpld-lab CLI.

Functions for managing running networks/standalones:
- start/stop/remove networks
- update node binaries
- enable amendments via RPC
- view logs
"""

from __future__ import annotations

import glob
import json
import os
import re
import subprocess

import requests

from xrpld_lab.models import NodeRole, PortSet
from xrpld_lab.utils import (
    bcolors,
    remove_directory,
    run_command,
    sha512_half,
)
from xrpld_lab.workspace import Workspace


# ---------------------------------------------------------------------------
# Script runners
# ---------------------------------------------------------------------------


def run_start_script(workspace: Workspace, name: str) -> bool:
    """Run the start.sh script for a named network/cluster; True when it exits 0."""
    script = os.path.join(workspace.base, name, "start.sh")
    if not os.path.isfile(script):
        print(f"{bcolors.RED}start.sh not found: {script}{bcolors.END}")
        return False
    return run_command(os.path.join(workspace.base, name), "bash start.sh") == 0


def run_stop_script(workspace: Workspace, name: str) -> bool:
    """Run the stop.sh script for a named network/cluster; True when it exits 0."""
    script = os.path.join(workspace.base, name, "stop.sh")
    if not os.path.isfile(script):
        print(f"{bcolors.RED}stop.sh not found: {script}{bcolors.END}")
        return False
    return run_command(os.path.join(workspace.base, name), "bash stop.sh") == 0


def remove_network(workspace: Workspace, name: str) -> bool:
    """Stop a network with ``stop.sh --remove`` and delete its directory.

    The directory is removed only after the stop script exits 0, so a running
    cluster is never left without its compose file. A directory with no
    ``stop.sh`` (nothing was ever generated) is removed directly.
    """
    path = os.path.join(workspace.base, name)
    if not os.path.isdir(path):
        print(f"{bcolors.RED}Directory not found: {path}{bcolors.END}")
        return False
    if os.path.isfile(os.path.join(path, "stop.sh")):
        if run_command(path, "bash stop.sh --remove") != 0:
            print(f"{bcolors.RED}stop.sh --remove failed; {path} kept{bcolors.END}")
            return False
    return remove_directory(path)


# ---------------------------------------------------------------------------
# down:standalone
# ---------------------------------------------------------------------------


def stop_standalone(
    workspace: Workspace,
    name: str | None,
    protocol: str,
    version: str | None,
) -> bool:
    """Stop and remove a standalone ledger; True when both steps succeeded.

    If *name* is given directly, use that as the directory name.
    Otherwise construct ``{protocol}-{version}`` from protocol and version.
    The directory is removed only after stop.sh exits 0.
    """
    if name:
        dir_name = name
    elif version:
        dir_name = f"{protocol}-{version}"
    else:
        print(f"{bcolors.RED}Either --name or --version is required.{bcolors.END}")
        return False

    net_dir = os.path.join(workspace.base, dir_name)
    if not os.path.isdir(net_dir):
        print(f"{bcolors.RED}Directory not found: {net_dir}{bcolors.END}")
        return False

    stop_script = os.path.join(net_dir, "stop.sh")
    if not os.path.isfile(stop_script):
        print(f"{bcolors.RED}stop.sh not found: {stop_script}{bcolors.END}")
        return False
    if run_command(net_dir, "bash stop.sh") != 0:
        return False

    return remove_directory(net_dir)


# ---------------------------------------------------------------------------
# up:local / down:local
# ---------------------------------------------------------------------------


def start_local(
    protocol: str = "xrpl",
    network_type: str = "standalone",
    network_id: int | None = None,
    log_level: str = "trace",
    nodedb_type: str = "NuDB",
    public_key: str = None,
) -> bool:
    """Set up and start a local standalone node from the current directory.

    Expects to be run from the build directory of a built xrpld repo.
    Generates config/, db/, log/ dirs, writes xrpld.cfg, validators.txt,
    genesis.json, start.sh, and stop.sh, then launches the node.
    """
    from xrpld_lab.amendments import (
        get_feature_lines_from_path,
        parse_amendments,
        update_genesis,
    )
    from xrpld_lab.config_builder import XrpldCfgBuilder, ValidatorsTxtBuilder
    from xrpld_lab.models import NodeDbType, Protocol
    from xrpld_lab.node_factory import NodeFactory
    from xrpld_lab.protocol import get_spec
    from xrpld_lab.utils import save_config, write_executable, write_file

    cwd = os.getcwd()
    protocol_enum = Protocol(protocol)
    spec = get_spec(protocol_enum)
    network_id = network_id or spec.default_network_id
    binary_name = spec.daemon_name
    config_filename = f"{binary_name}.cfg"

    # 1. Find binary in CWD
    binary_path = os.path.join(cwd, binary_name)
    if not os.path.isfile(binary_path):
        print(f"{bcolors.RED}{binary_name} not found in {cwd}{bcolors.END}")
        return False

    # 2. Resolve features from source tree (CWD is build/, repo root is ../)
    feature_lines: list = []
    features_path = ""
    for fpath in spec.feature_paths:
        candidate = os.path.join(cwd, "..", fpath)
        if os.path.exists(candidate):
            feature_lines = get_feature_lines_from_path(candidate)
            features_path = candidate
            print(f"{bcolors.CYAN}Resolved features from {candidate}{bcolors.END}")
            break

    if not feature_lines:
        print(f"{bcolors.RED}Could not resolve features from source tree{bcolors.END}")
        return False

    # 3. Parse amendments and build the genesis before anything is written
    features = parse_amendments(feature_lines)
    if not features:
        print(
            f"{bcolors.RED}No Supported::yes amendment in {features_path}; "
            f"nothing written{bcolors.END}"
        )
        return False
    genesis = update_genesis(features, protocol)

    # 4. Create directories
    config_dir = os.path.join(cwd, "config")
    os.makedirs(config_dir, exist_ok=True)
    os.makedirs(os.path.join(cwd, "db"), exist_ok=True)
    os.makedirs(os.path.join(cwd, "log"), exist_ok=True)

    # 5. Build NodeConfig for local standalone
    node = NodeFactory.create_local_standalone(
        protocol=protocol_enum,
        name="local",
        network_id=network_id,
        log_level=log_level,
        node_db_type=NodeDbType(nodedb_type),
        vl_keys=[public_key] if public_key else [],
    )

    # 6. Generate config files
    cfg_content = XrpldCfgBuilder(node).build()
    vl_content = ValidatorsTxtBuilder(node, genesis=True).build()
    save_config(protocol, config_dir, cfg_content, vl_content)
    write_file(
        os.path.join(config_dir, "genesis.json"),
        json.dumps(genesis, indent=4, sort_keys=True),
    )

    # 7. Generate start.sh and stop.sh. start.sh execs the daemon in the shell's
    # own process, so the shell's pid written to xrpld.pid is the daemon's pid
    # and stop.sh kills only this node.
    flag = "-a" if network_type == "standalone" else ""
    start_content = (
        "#!/bin/bash\n"
        "echo $$ > xrpld.pid\n"
        f"exec ./{binary_name} {flag} --conf config/{config_filename}"
        " --ledgerfile config/genesis.json\n"
    )
    stop_content = (
        "#!/bin/bash\n"
        "if [ ! -f xrpld.pid ]; then\n"
        f'  echo "xrpld.pid not found in $(pwd): no {binary_name} started'
        ' by start.sh here"\n'
        "  exit 1\n"
        "fi\n"
        "PID=$(cat xrpld.pid)\n"
        'if kill "$PID" 2>/dev/null; then\n'
        f'  echo "{binary_name} (PID $PID) stopped"\n'
        "else\n"
        f'  echo "No running {binary_name} with PID $PID (stale xrpld.pid)"\n'
        "fi\n"
        "rm -f xrpld.pid\n"
    )
    write_executable(os.path.join(cwd, "start.sh"), start_content)
    write_executable(os.path.join(cwd, "stop.sh"), stop_content)

    print(f"{bcolors.CYAN}Generated config in {config_dir}{bcolors.END}")
    print(f"{bcolors.CYAN}Starting {binary_name} (Ctrl+C to stop)...{bcolors.END}")

    # 8. Launch in foreground — stdout/stderr stream to this terminal
    return subprocess.run(["bash", "start.sh"], cwd=cwd).returncode == 0


def stop_local() -> bool:
    """Stop a local standalone by running ./stop.sh in the current directory."""
    cwd = os.getcwd()
    script = os.path.join(cwd, "stop.sh")
    if not os.path.isfile(script):
        print(f"{bcolors.RED}stop.sh not found in {cwd}{bcolors.END}")
        return False
    return run_command(cwd, "bash stop.sh") == 0


def _docker_container_exists(name: str) -> bool:
    """True if a docker container with this exact name exists (running or not)."""
    try:
        r = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"name=^{name}$",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    return name in r.stdout.split()


def restart_local_node(
    node_name: str,
    binary_name: str = "xrpld",
    genesis: bool = False,
) -> bool:
    """Stop and restart a single local node; True when the node was relaunched.

    With ``genesis=False`` (default) the node resumes from its existing
    database — use this when restarting after a crash, stall, or attack
    simulation. With ``genesis=True`` it loads the genesis ledger and marks it
    valid, giving the node a fresh divergent history.

    Two node layouts are supported. A bare-process node has an ``xrpld.pid`` in
    its directory and is killed and re-launched with the same arguments the
    cluster's ``start.sh`` uses, and the new PID is written back. A docker node
    has no pidfile and a container named after the node: ``genesis=False``
    ``docker restart``\\s it (keeping its database layer), ``genesis=True``
    recreates it so the database is wiped and the entrypoint reloads genesis.
    """
    cwd = os.getcwd()
    node_dir = os.path.join(cwd, node_name)
    if not os.path.isdir(node_dir):
        print(f"{bcolors.RED}Node directory not found: {node_dir}{bcolors.END}")
        return False

    pid_file = os.path.join(node_dir, "xrpld.pid")
    if not os.path.isfile(pid_file) and _docker_container_exists(node_name):
        if genesis:
            print(f"{bcolors.CYAN}Recreating {node_name} from genesis...{bcolors.END}")
            code = run_command(
                cwd, f"docker compose up --force-recreate -d {node_name}"
            )
        else:
            print(
                f"{bcolors.CYAN}Restarting {node_name} (resume from db)...{bcolors.END}"
            )
            code = run_command(cwd, f"docker restart {node_name}")
        if code != 0:
            return False
        print(f"{bcolors.GREEN}{node_name} restarted.{bcolors.END}")
        return True

    # Bare-process node: kill by pidfile, relaunch, write the new pid.
    if os.path.isfile(pid_file):
        with open(pid_file) as f:
            pid = f.read().strip()
        if pid:
            print(f"{bcolors.CYAN}Stopping {node_name} (PID {pid})...{bcolors.END}")
            subprocess.run(["kill", pid], capture_output=True)
            subprocess.run(["sleep", "2"], capture_output=True)
            subprocess.run(["kill", "-9", pid], capture_output=True)
        os.remove(pid_file)

    argv = [f"./{binary_name}", "--conf", "config/xrpld.cfg"]
    if genesis:
        argv += ["--ledgerfile", "config/genesis.json", "--valid"]
    mode = "load genesis" if genesis else "sync from network"
    print(f"{bcolors.CYAN}Starting {node_name} ({mode})...{bcolors.END}")
    try:
        proc = subprocess.Popen(
            argv,
            cwd=node_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as e:
        print(f"{bcolors.RED}Cannot start {node_name}: {e}{bcolors.END}")
        return False
    with open(pid_file, "w") as f:
        f.write(f"{proc.pid}\n")
    print(f"{bcolors.GREEN}{node_name} started (PID {proc.pid}).{bcolors.END}")
    return True


# ---------------------------------------------------------------------------
# update:node
# ---------------------------------------------------------------------------


# In-image binary locations across the layouts we build/consume: perf datagram
# images (/opt/xrpld/bin), stock rippleci images (/usr/bin), older ripple images.
_IMAGE_BINARY_PATHS = (
    "/opt/xrpld/bin/xrpld",
    "/usr/bin/xrpld",
    "/usr/local/bin/rippled",
    "/opt/ripple/bin/rippled",
    "/app/xrpld",
)


def _extract_binary_from_image(image: str, dest: str) -> bool:
    """Copy the xrpld/rippled binary out of a docker image to *dest*.

    The build pipeline publishes images (``-dg`` tags in GAR), not raw binaries,
    so an upgrade drill sources the target build from an image ref — local or, after
    a ``docker pull``, from the registry. The binary lives at a different path across
    image layouts, so each known location is tried. Returns True on success.
    """
    probe = f"xrpld-extract-{os.getpid()}"
    try:
        subprocess.run(["docker", "rm", "-f", probe], capture_output=True)
        r = subprocess.run(
            ["docker", "create", "--name", probe, image], capture_output=True, text=True
        )
        if r.returncode != 0:
            # Not present locally: pull then retry create.
            if subprocess.run(["docker", "pull", image]).returncode != 0:
                print(f"{bcolors.RED}Cannot pull image {image}{bcolors.END}")
                return False
            r = subprocess.run(
                ["docker", "create", "--name", probe, image],
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                print(
                    f"{bcolors.RED}docker create failed: "
                    f"{r.stderr.strip()}{bcolors.END}"
                )
                return False
        for path in _IMAGE_BINARY_PATHS:
            cp = subprocess.run(
                ["docker", "cp", f"{probe}:{path}", dest],
                capture_output=True,
                text=True,
            )
            if cp.returncode == 0:
                return True
        print(
            f"{bcolors.RED}No xrpld/rippled binary found in {image} "
            f"(tried {', '.join(_IMAGE_BINARY_PATHS)}){bcolors.END}"
        )
        return False
    except FileNotFoundError:
        print(f"{bcolors.RED}docker not found{bcolors.END}")
        return False
    finally:
        subprocess.run(["docker", "rm", "-f", probe], capture_output=True)


_COPY_BINARY_RE = re.compile(r"COPY xrpld\.\S+ /opt/xrpld/bin/xrpld")


def _dockerfile_with_binary(content: str, version: str) -> str | None:
    """Return *content* copying ``xrpld.<version>`` into the image, or None.

    An existing ``COPY xrpld.* /opt/xrpld/bin/xrpld`` line is repointed. Without
    one, a COPY and a chmod line are inserted before the first ``ENV`` line, so
    the copied binary sits where the entrypoint looks first. None when the
    Dockerfile has neither anchor.
    """
    copy_line = f"COPY xrpld.{version} /opt/xrpld/bin/xrpld"
    if _COPY_BINARY_RE.search(content):
        return _COPY_BINARY_RE.sub(copy_line, content)
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.lstrip().startswith("ENV "):
            lines[i:i] = [copy_line, "RUN chmod +x /opt/xrpld/bin/xrpld"]
            return "\n".join(lines)
    return None


def _download_binary(url: str, dest: str) -> bool:
    """Download *url* to *dest* with curl; True when curl exits 0."""
    try:
        code = subprocess.run(["curl", "-fsSL", "-o", dest, url]).returncode
    except FileNotFoundError:
        print(f"{bcolors.RED}curl not found{bcolors.END}")
        return False
    if code != 0:
        print(f"{bcolors.RED}Failed to download {url} (curl exit {code}){bcolors.END}")
        return False
    return True


def update_node_binary(
    workspace: Workspace,
    name: str,
    node_id: int,
    node_type: str,
    build_server: str | None,
    build_version: str,
    image: str = None,
) -> bool:
    """Update the xrpld binary for a single node in a running network.

    Steps:
    1. Source the new binary into the node directory as ``xrpld.<build_version>``:
       extract it from *image* if given, else download ``build_server/build_version``.
       Nothing is stopped until the binary is on disk.
    2. chmod 755
    3. Rewrite the node's Dockerfile to COPY the new binary
    4. docker compose stop the node
    5. Remove node's lib directory (force re-sync)
    6. docker compose up --build --force-recreate -d the node

    Returns True only when every step succeeded.
    """
    node_dir_name = f"vnode{node_id}" if node_type == "validator" else f"pnode{node_id}"
    net_dir = os.path.join(workspace.base, name)
    node_dir = os.path.join(net_dir, node_dir_name)

    if not os.path.isdir(node_dir):
        print(f"{bcolors.RED}Node directory not found: {node_dir}{bcolors.END}")
        return False

    dockerfile_path = os.path.join(node_dir, "Dockerfile")
    if not os.path.isfile(dockerfile_path):
        print(f"{bcolors.RED}Dockerfile not found: {dockerfile_path}{bcolors.END}")
        return False

    # 1. Source the new binary before touching the running node.
    binary_dest = os.path.join(node_dir, f"xrpld.{build_version}")
    if image:
        print(f"{bcolors.CYAN}Extracting binary from image {image}...{bcolors.END}")
        if not _extract_binary_from_image(image, binary_dest):
            return False
    else:
        binary_url = f"{build_server}/{build_version}"
        print(f"{bcolors.CYAN}Downloading binary from {binary_url}...{bcolors.END}")
        if not _download_binary(binary_url, binary_dest):
            return False

    # 2. chmod 755
    os.chmod(binary_dest, 0o755)

    # 3. Point the Dockerfile at the new binary
    with open(dockerfile_path) as f:
        content = _dockerfile_with_binary(f.read(), build_version)
    if content is None:
        print(
            f"{bcolors.RED}{dockerfile_path} has no COPY xrpld line and no ENV line "
            f"to insert one before{bcolors.END}"
        )
        return False
    with open(dockerfile_path, "w") as f:
        f.write(content)

    # 4. Stop the node container
    print(f"{bcolors.CYAN}Stopping {node_dir_name}...{bcolors.END}")
    if run_command(net_dir, f"docker compose stop {node_dir_name}") != 0:
        return False

    # 5. Remove node lib dir (force re-sync)
    lib_dir = os.path.join(node_dir, "lib")
    if os.path.isdir(lib_dir) and not remove_directory(lib_dir):
        return False

    # 6. Rebuild and start the node
    print(f"{bcolors.CYAN}Rebuilding {node_dir_name}...{bcolors.END}")
    code = run_command(
        net_dir,
        f"docker compose up --build --force-recreate -d {node_dir_name}",
    )
    if code != 0:
        return False
    print(
        f"{bcolors.GREEN}Node {node_dir_name} updated to {build_version}.{bcolors.END}"
    )
    return True


# ---------------------------------------------------------------------------
# admin RPC
# ---------------------------------------------------------------------------


def _admin_rpc_port(node_id: int, node_type: str) -> int:
    """RPC admin port for node *node_id* of *node_type*."""
    role = NodeRole.VALIDATOR if node_type == "validator" else NodeRole.PEER
    return PortSet.for_node(node_id, role).rpc_admin


def _admin_rpc(url: str, method: str, params: dict) -> dict | None:
    """POST one JSON-RPC command to *url*; the result dict, or None on failure."""
    try:
        resp = requests.post(
            url, json={"method": method, "params": [params]}, timeout=10
        )
    except requests.RequestException as e:
        print(f"{bcolors.RED}RPC request failed: {e}{bcolors.END}")
        return None
    if resp.status_code != 200:
        print(f"{bcolors.RED}RPC request failed: HTTP {resp.status_code}{bcolors.END}")
        return None
    try:
        result = resp.json().get("result", {})
    except ValueError:
        print(f"{bcolors.RED}RPC response is not JSON{bcolors.END}")
        return None
    if result.get("status") == "error":
        message = result.get("error_message") or result.get("error")
        print(f"{bcolors.RED}RPC error: {message}{bcolors.END}")
        return None
    return result


# ---------------------------------------------------------------------------
# vote:amendment
# ---------------------------------------------------------------------------


def _validator_ids(workspace: Workspace, name: str) -> list[int]:
    """Validator indexes in the cluster directory, from its vnode<N> subdirectories."""
    ids = []
    for path in glob.glob(os.path.join(workspace.base, name, "vnode*")):
        suffix = os.path.basename(path)[len("vnode") :]
        if suffix.isdigit():
            ids.append(int(suffix))
    return sorted(ids)


def vote_amendment(
    name: str,
    amendment_name: str,
    workspace: Workspace,
    node_id: int | None = None,
) -> bool:
    """Lift the veto on an amendment so the cluster's validators vote for it.

    Sends ``feature`` with ``vetoed: false`` to every validator's admin RPC,
    or to one validator when *node_id* is given. Each validator then votes
    yes at the next flag ledger; the amendment activates once the network's
    supermajority has held for the majority time. Returns True when every
    targeted validator accepted the command.
    """
    amendment_hash = sha512_half(amendment_name.encode("utf-8").hex())
    ids = [node_id] if node_id is not None else _validator_ids(workspace, name)
    if not ids:
        print(
            f"{bcolors.RED}No validators found under "
            f"{os.path.join(workspace.base, name)}{bcolors.END}"
        )
        return False
    ok = True
    for i in ids:
        url = f"http://localhost:{_admin_rpc_port(i, 'validator')}"
        result = _admin_rpc(
            url, "feature", {"feature": amendment_hash, "vetoed": False}
        )
        if result is None:
            ok = False
            continue
        state = result.get(amendment_hash, {})
        print(
            f"{bcolors.GREEN}vnode{i}: {amendment_name} vetoed="
            f"{state.get('vetoed')} enabled={state.get('enabled')}{bcolors.END}"
        )
    print(
        f"{bcolors.CYAN}Validators vote at the next flag ledger; activation follows "
        f"the supermajority holding for the majority time.{bcolors.END}"
    )
    return ok


# ---------------------------------------------------------------------------
# node:stall
# ---------------------------------------------------------------------------


def node_stall(
    name: str,
    node_id: int,
    node_type: str,
    workspace: Workspace,
    duration_ms: int = 30000,
    clear: bool = False,
) -> bool:
    """Stall or unstall a running node via the ``node_stall`` admin RPC.

    When stalled, the node stops participating in consensus (no proposals,
    no validations, no ledger closes).  After *duration_ms* the node
    automatically resumes.  Pass ``clear=True`` to lift the stall early.
    Returns True when the node reports success.
    """
    params = {"clear": True} if clear else {"duration_ms": duration_ms}
    url = f"http://localhost:{_admin_rpc_port(node_id, node_type)}"
    action = "Clearing stall on" if clear else f"Stalling ({duration_ms}ms)"
    print(f"{bcolors.CYAN}{action} {node_type} {node_id} at {url}...{bcolors.END}")
    if _admin_rpc(url, "node_stall", params) is None:
        return False
    print(f"{bcolors.GREEN}node_stall RPC sent.{bcolors.END}")
    return True


# ---------------------------------------------------------------------------
# logs:local
# ---------------------------------------------------------------------------


def view_local_logs(node: str | None) -> None:
    """Tail local node log files.

    If *node* is given (e.g. ``"vnode1"``), look for its ``log/debug.log``.
    Otherwise search the current directory for any ``debug.log`` files.
    """
    cwd = os.getcwd()

    if node:
        candidates = [
            os.path.join(cwd, node, "log", "debug.log"),
            os.path.join(cwd, node, "config", "debug.log"),
        ]
    else:
        candidates = glob.glob(os.path.join(cwd, "**/debug.log"), recursive=True)

    log_file = None
    for path in candidates:
        if os.path.isfile(path):
            log_file = path
            break

    if not log_file:
        search = node or "current directory"
        print(f"{bcolors.RED}No debug.log found for {search}.{bcolors.END}")
        return

    print(f"{bcolors.CYAN}Tailing {log_file} (Ctrl+C to stop)...{bcolors.END}")
    try:
        subprocess.run(["tail", "-f", log_file], check=False)
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# logs:standalone
# ---------------------------------------------------------------------------


def view_standalone_logs(protocol: str = "xrpl") -> None:
    """Tail Docker logs for the standalone container."""
    container_name = protocol
    print(
        f"{bcolors.CYAN}Tailing Docker logs for '{container_name}' "
        f"(Ctrl+C to stop)...{bcolors.END}"
    )
    try:
        subprocess.run(
            ["docker", "logs", "-f", container_name],
            check=False,
        )
    except KeyboardInterrupt:
        pass
