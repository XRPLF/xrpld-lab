"""Thin CLI layer for xrpld-lab.

Parses command-line arguments, converts them into a LabConfig dataclass,
and passes the config to LabRunner.run().
"""

from __future__ import annotations

import argparse
import re
from typing import List, Optional

from xrpld_lab.config import load_ansible_config, load_overrides_file
from xrpld_lab.models import (
    AlloyConfig,
    AnsibleConfig,
    BuildSource,
    BuildType,
    CompilerConfig,
    DebugConfig,
    DeployMode,
    FaucetConfig,
    LabConfig,
    NginxConfig,
    NodeDbType,
    Protocol,
    RedisConfig,
    ServicesHost,
    StatusConfig,
    StreamConfig,
    VlConfig,
)
from xrpld_lab.operations import (
    vote_amendment,
    node_stall,
    remove_network,
    restart_local_node,
    run_start_script,
    run_stop_script,
    start_local,
    stop_local,
    stop_standalone,
    update_node_binary,
    view_local_logs,
    view_standalone_logs,
)
from xrpld_lab.protocol import get_spec
from xrpld_lab.workspace import Workspace
from xrpld_lab.workflows import LabRunner

# ---------------------------------------------------------------------------
# Default VL keys
# ---------------------------------------------------------------------------

_DEFAULT_VL_KEY: str = (
    "ED87E0EA91AAFFA130B78B75D2CC3E53202AA1BD8AB3D5E7BAC530C8440E328501"
)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _parse_bool(value: str) -> bool:
    """argparse type for boolean values: 1/true/yes and 0/false/no (any case)."""
    if isinstance(value, bool):
        return value
    lowered = value.strip().lower()
    if lowered in ("1", "true", "yes", "y"):
        return True
    if lowered in ("0", "false", "no", "n"):
        return False
    raise argparse.ArgumentTypeError(f"expected a boolean, got {value!r}")


def _add_network_args(p: argparse.ArgumentParser) -> None:
    """Add arguments shared by create:network and create:ansible."""
    p.add_argument(
        "--log_level", default="trace", choices=["warning", "debug", "trace"]
    )
    p.add_argument("--protocol", default="xrpl")
    p.add_argument("--num_validators", type=int, default=3)
    p.add_argument("--num_peers", type=int, default=1)
    p.add_argument(
        "--network_id",
        type=int,
        default=None,
        help="Network id. Omit to use the protocol default; every real "
        "network should set its own (>1024 for replay protection).",
    )
    p.add_argument("--build_server", default=None)
    p.add_argument("--build_version", default=None)
    p.add_argument(
        "--genesis",
        type=_parse_bool,
        default=False,
        help="True: fresh chain from a generated genesis (deploy wipes node "
        "state). False: join/preserve — nodes keep their db and boot "
        "normally.",
    )
    p.add_argument(
        "--db_seed",
        action="store_true",
        help="Boot nodes with --load from a snapshot-restored db dir "
        "(no genesis.json baked into the image)",
    )
    p.add_argument(
        "--genesis_file",
        default=None,
        help="Custom genesis JSON (e.g. prefunded accounts). Default: xrpld-lab's "
        "bundled genesis; the resolved amendments are merged into it.",
    )
    p.add_argument("--quorum", type=int, default=None)
    p.add_argument(
        "--tree_cache_target_entries",
        type=int,
        default=0,
        help="Explicit [tree_cache_target_entries] for network nodes; "
        "0 omits the stanza (node_size preset + RAM guardrail).",
    )
    p.add_argument(
        "--memory_limit",
        type=int,
        default=None,
        help="RAM budget in GB ([memory_limit]) for the memory-pressure "
        "binary; replaces node_size/tree_cache stanzas. Omit for "
        "stock builds (node_size path).",
    )
    p.add_argument(
        "--online_delete",
        type=int,
        default=256,
        help="online_delete ledger count for network nodes; "
        "0 = disabled ([ledger_history] full — growth-study setting)",
    )
    p.add_argument(
        "--database_path",
        default="/var/lib/xrpld/db/rdb",
        help="[database_path] (SQLite) for network nodes. Pin to the running "
        "network's existing path on a preserve deploy.",
    )
    p.add_argument("--nodedb_type", default="NuDB", choices=["Memory", "NuDB"])
    p.add_argument(
        "--config_overrides",
        type=str,
        default=None,
        help="Path to YAML/JSON file with config overrides",
    )
    p.add_argument(
        "--port_offset",
        type=int,
        default=0,
        help="Shift every node port by N so a candidate cluster can run "
        "beside an existing one (local mode).",
    )
    p.add_argument(
        "--datagram_monitor",
        type=str,
        default=None,
        help="Perf-server XDGM sink as 'HOST PORT' (internal IP, e.g. "
        "'10.128.0.2 9876'); adds [datagram_monitor] to every node cfg",
    )
    p.add_argument(
        "--binary_path",
        type=str,
        default=None,
        help="Path to pre-built binary (skips download)",
    )
    p.add_argument(
        "--quantum",
        action="store_true",
        help="Use dilithium (post-quantum) keys for validators and publisher",
    )
    p.add_argument(
        "--all-amendments",
        dest="all_amendments",
        action="store_true",
        help="Pre-enable EVERY amendment in genesis, ignoring the "
        "Supported flag. Requires a binary built with those "
        "amendments supported (else it amendment-blocks).",
    )
    p.add_argument(
        "--features_file",
        default=None,
        help="Read features.macro from this local path instead of "
        "fetching from GitHub. Use with a binary built from an "
        "unpushed commit so the amendment set matches the binary.",
    )
    p.add_argument(
        "--preload_accounts",
        type=int,
        default=0,
        help="Prefund N accounts directly in genesis (perf-iac style)",
    )
    p.add_argument(
        "--preload_trustlines",
        type=int,
        default=0,
        help="Create N trustlines from account 0 (issuer hub) in genesis",
    )
    p.add_argument(
        "--preload_balance",
        default="1000000000",
        help="Drops per prefunded account (default 1000 XRP)",
    )
    p.add_argument(
        "--preload_currency",
        default="USD",
        help="Currency code for preloaded trustlines",
    )
    p.add_argument(
        "--workspace",
        default=None,
        help="Workspace root holding <cluster>-cluster and its keystore "
        "(default: ./workspace). Point at an existing network's "
        "workspace to reuse its identity instead of copying keys.",
    )
    p.add_argument(
        "--vl_site",
        default=None,
        help="Publisher list URL for [validator_list_sites]. Default is the "
        "compose-internal http://vl/vl.json, which only resolves inside "
        "a local cluster.",
    )
    p.add_argument(
        "--bootstrap_vl",
        action="store_true",
        help="Emit the static [validators] list alongside the publisher list, "
        "so a fresh chain reaches quorum before the VL site is up.",
    )
    p.add_argument(
        "--statsd_address",
        default=None,
        help="[insight] StatsD sink as IP:PORT (e.g. 127.0.0.1:9125). Required "
        "by the Alloy telemetry sidecar.",
    )
    p.add_argument(
        "--perf_path",
        default=None,
        help="[perf] perf_log path (e.g. /opt/ripple/log/perf.log). Required "
        "by the Alloy telemetry sidecar.",
    )


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser with all subcommands."""
    parser = argparse.ArgumentParser(
        description="xrpld-lab: build xrpld networks and standalone ledgers.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # -- up:standalone -------------------------------------------------------
    p = subparsers.add_parser(
        "up:standalone", help="Create and start standalone ledger"
    )
    p.add_argument(
        "--log_level", default="trace", choices=["warning", "debug", "trace"]
    )
    p.add_argument("--public_key", default=_DEFAULT_VL_KEY)
    p.add_argument("--protocol", default="xrpl")
    p.add_argument(
        "--network_id",
        type=int,
        default=None,
        help="Network id. Omit to use the protocol default; every real "
        "network should set its own (>1024 for replay protection).",
    )
    p.add_argument("--network_type", default="standalone")
    p.add_argument("--server", default=None)
    p.add_argument("--version", default=None)
    p.add_argument(
        "--commit",
        default=None,
        help="Commit sha OR release version (e.g. 3.2.0) of an "
        "all-amendments 'supported' build: pulls "
        "ghcr.io/xrplf/xrpld/supported:<sha-short|version> and "
        "resolves the amendment set from that ref.",
    )
    p.add_argument("--ipfs", type=_parse_bool, default=False)
    p.add_argument("--nodedb_type", default="NuDB", choices=["Memory", "NuDB"])
    p.add_argument(
        "--config_overrides",
        type=str,
        default=None,
        help="Path to YAML/JSON file with config overrides",
    )
    p.add_argument(
        "--port_offset",
        type=int,
        default=0,
        help="Shift every node port by N so a candidate cluster can run "
        "beside an existing one (local mode).",
    )
    p.add_argument(
        "--datagram_monitor",
        type=str,
        default=None,
        help="Perf-server XDGM sink as 'HOST PORT' (e.g. '10.128.0.2 9876')",
    )
    p.add_argument(
        "--all-amendments",
        dest="all_amendments",
        action="store_true",
        help="Pre-enable EVERY amendment in genesis, ignoring the "
        "Supported flag. Requires a binary built with those "
        "amendments supported (else it amendment-blocks).",
    )
    p.add_argument(
        "--features_file",
        default=None,
        help="Read features.macro from this local path instead of "
        "fetching from GitHub. Use with a binary built from an "
        "unpushed commit so the amendment set matches the binary.",
    )

    # -- create:network ------------------------------------------------------
    p = subparsers.add_parser("create:network", help="Create a multi-node network")
    _add_network_args(p)
    p.add_argument("--local", action="store_true")
    p.add_argument("--binary_name", default="xrpld")
    p.add_argument(
        "--ansible", action="store_true", help="Also generate ansible deployment files"
    )
    p.add_argument(
        "--vips", nargs="+", default=None, help="Validator IP addresses (for ansible)"
    )
    p.add_argument(
        "--pips", nargs="+", default=None, help="Peer IP addresses (for ansible)"
    )
    p.add_argument(
        "--ssh_port", type=int, default=20, help="SSH port for ansible (default: 20)"
    )
    p.add_argument(
        "--ssh_user", default="ubuntu", help="SSH user for ansible (default: ubuntu)"
    )
    p.add_argument(
        "--ssh_key", default="~/.ssh/id_rsa", help="SSH key path for ansible"
    )

    # -- create:ansible ------------------------------------------------------
    p = subparsers.add_parser(
        "create:ansible", help="Create network with ansible deployment"
    )
    _add_network_args(p)
    p.add_argument("--binary_name", default="xrpld")
    p.add_argument("--vips", nargs="+", default=None, help="Validator IP addresses")
    p.add_argument("--pips", nargs="+", default=None, help="Peer IP addresses")
    p.add_argument("--ssh_port", type=int, default=20)
    p.add_argument("--ssh_user", default="ubuntu")
    p.add_argument("--ssh_key", default="~/.ssh/id_rsa")
    p.add_argument(
        "--cluster",
        default=None,
        help="Cluster name (workspace dir). Pass the same name to deploy:ansible.",
    )
    p.add_argument(
        "--image",
        default=None,
        help="Prebuilt node image to deploy (the image IS the binary, built "
        "elsewhere). Skips the local-binary path; build_type becomes IMAGE.",
    )
    p.add_argument(
        "--ansible_config",
        type=str,
        default=None,
        help="Path to YAML file with full ansible config (services, etc.)",
    )

    # -- deploy:ansible ------------------------------------------------------
    p = subparsers.add_parser(
        "deploy:ansible", help="Run ansible deployment for an existing cluster"
    )
    p.add_argument("--name", required=True, help="Cluster name")
    p.add_argument(
        "--workspace",
        default=None,
        help="Workspace root holding <name>-cluster (default: ./workspace)",
    )

    # -- health --------------------------------------------------------------
    p = subparsers.add_parser(
        "health", help="Poll validators until they reach consensus"
    )
    p.add_argument(
        "--vips", nargs="+", required=True, help="Validator external IPs, in node order"
    )
    p.add_argument(
        "--timeout", type=int, default=300, help="Overall deadline (seconds)"
    )
    p.add_argument("--interval", type=int, default=10, help="Seconds between polls")

    # -- Operational commands ------------------------------------------------
    p = subparsers.add_parser("up", help="Start network")
    p.add_argument("--name", required=True)

    p = subparsers.add_parser("down", help="Stop network")
    p.add_argument("--name", required=True)

    p = subparsers.add_parser("remove", help="Remove network")
    p.add_argument("--name", required=True)

    # -- down:standalone -----------------------------------------------------
    p = subparsers.add_parser(
        "down:standalone", help="Stop and remove standalone ledger"
    )
    p.add_argument("--name", default=None)
    p.add_argument("--protocol", default="xrpl")
    p.add_argument("--version", default=None)

    # -- up:local ------------------------------------------------------------
    p = subparsers.add_parser("up:local", help="Start local standalone")
    p.add_argument(
        "--log_level", default="trace", choices=["warning", "debug", "trace"]
    )
    p.add_argument("--public_key", default=_DEFAULT_VL_KEY)
    p.add_argument("--protocol", default="xrpl")
    p.add_argument("--network_type", default="standalone")
    p.add_argument(
        "--network_id",
        type=int,
        default=None,
        help="Network id. Omit to use the protocol default; every real "
        "network should set its own (>1024 for replay protection).",
    )
    p.add_argument("--nodedb_type", default="NuDB", choices=["Memory", "NuDB"])

    # -- down:local ----------------------------------------------------------
    p = subparsers.add_parser("down:local", help="Stop local standalone")

    # -- update:node ---------------------------------------------------------
    p = subparsers.add_parser("update:node", help="Update a node binary")
    p.add_argument("--name", required=True)
    p.add_argument("--node_id", type=int, required=True)
    p.add_argument("--node_type", required=True, choices=["validator", "peer"])
    p.add_argument(
        "--build_version",
        required=True,
        help="version label for the on-disk binary + Dockerfile COPY line",
    )
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--build_server",
        default=None,
        help="raw-binary source; the binary is fetched from "
        "<build_server>/<build_version>",
    )
    source.add_argument(
        "--image",
        default=None,
        help="docker image to extract the xrpld binary from (local or GAR)",
    )
    p.add_argument(
        "--workspace",
        default=None,
        help="workspace root holding the cluster dir; defaults to ./workspace "
        "(set this when invoking from outside the lab root)",
    )

    # -- vote:amendment ----------------------------------------------------
    p = subparsers.add_parser(
        "vote:amendment",
        help="Lift the veto on an amendment so the validators vote for it; "
        "activation follows the flag-ledger supermajority",
    )
    p.add_argument("--name", required=True, help="Cluster directory name")
    p.add_argument("--amendment_name", required=True)
    p.add_argument(
        "--node_id", type=int, default=None, help="One validator instead of all"
    )

    # -- node:stall ----------------------------------------------------------
    p = subparsers.add_parser("node:stall", help="Stall a node (pause consensus)")
    p.add_argument("--name", required=True)
    p.add_argument("--node_id", type=int, required=True)
    p.add_argument("--node_type", required=True, choices=["validator", "peer"])
    p.add_argument(
        "--duration_ms",
        type=int,
        default=30000,
        help="Stall duration in ms (default: 30000)",
    )
    p.add_argument("--clear", action="store_true", help="Clear the stall immediately")

    # -- node:restart --------------------------------------------------------
    p = subparsers.add_parser(
        "node:restart",
        help="Restart a single local node (syncs from network)",
    )
    p.add_argument("node_name", help="Node directory name (e.g. vnode2)")
    p.add_argument(
        "--genesis",
        action="store_true",
        help="Start from genesis instead of syncing from network",
    )

    # -- logs:local ----------------------------------------------------------
    p = subparsers.add_parser("logs:local", help="View local node logs")
    p.add_argument("--node", default=None)

    # -- logs:standalone -----------------------------------------------------
    p = subparsers.add_parser("logs:standalone", help="View standalone Docker logs")
    p.add_argument("--protocol", default="xrpl")

    return parser


# ---------------------------------------------------------------------------
# Ansible config helpers
# ---------------------------------------------------------------------------


def _resolve_service_dependencies(ansible: AnsibleConfig, log_level: str) -> tuple:
    """Enforce service dependency constraints.

    - stream + debug are always paired (auto-create missing half)
    - stream/debug require redis on the same host
    - stream/debug require trace log level on nodes
    - debug.endpoint auto-derives from stream ip:port if not set
    """
    needs_trace = False

    for host in ansible.services:
        # stream and debug are a pair — auto-create the missing half
        if host.stream and not host.debug:
            host.debug = DebugConfig(
                endpoint=f"ws://{host.ip}:{host.stream.port}/",
            )
        elif host.debug and not host.stream:
            host.stream = StreamConfig()

        if host.stream or host.debug:
            needs_trace = True

            # debug/stream require redis
            if not host.redis:
                host.redis = RedisConfig()

            # auto-derive debug endpoint from stream
            if host.debug and host.stream and not host.debug.endpoint:
                host.debug.endpoint = f"ws://{host.ip}:{host.stream.port}/"

    if needs_trace and log_level != "trace":
        print(
            f"  [xrpld-lab] stream/debug services require trace logging, "
            f"overriding log_level from '{log_level}' to 'trace'"
        )
        log_level = "trace"

    return ansible, log_level


def _flatten_ips(seq) -> List[str]:
    """Normalise an IP arg into a flat list. Accepts pre-split tokens
    (--vips a b c) or a single whitespace/comma-joined string (--vips "a b c"),
    so a shell that doesn't word-split (zsh) or a joined $VAR can't collapse the
    fleet into one host (which silently made num_validators=1)."""
    out: List[str] = []
    for item in seq or []:
        out.extend(tok for tok in str(item).replace(",", " ").split() if tok)
    return out


def _build_ansible_config_from_args(args) -> AnsibleConfig:
    """Build AnsibleConfig from CLI args (--vips, --pips, --ssh_*)."""
    return AnsibleConfig(
        ssh_port=args.ssh_port,
        ssh_user=args.ssh_user,
        ssh_key_path=args.ssh_key,
        vips=_flatten_ips(args.vips),
        pips=_flatten_ips(args.pips),
    )


def _build_ansible_config_from_file(path: str, args) -> AnsibleConfig:
    """Build AnsibleConfig from a YAML file, with CLI args as fallback."""
    data = load_ansible_config(path)

    services: List[ServicesHost] = []
    for svc_data in data.get("services", []):
        host = ServicesHost(
            ip=svc_data["ip"],
            name=svc_data["name"],
            nginx=(
                NginxConfig(**(svc_data["nginx"] or {}))
                if "nginx" in svc_data
                else None
            ),
            vl=VlConfig(**(svc_data["vl"] or {})) if "vl" in svc_data else None,
            redis=(
                RedisConfig(**(svc_data["redis"] or {}))
                if "redis" in svc_data
                else None
            ),
            faucet=(
                FaucetConfig(**(svc_data["faucet"] or {}))
                if "faucet" in svc_data
                else None
            ),
            stream=(
                StreamConfig(**(svc_data["stream"] or {}))
                if "stream" in svc_data
                else None
            ),
            debug=(
                DebugConfig(**(svc_data["debug"] or {}))
                if "debug" in svc_data
                else None
            ),
            compiler=(
                CompilerConfig(**(svc_data["compiler"] or {}))
                if "compiler" in svc_data
                else None
            ),
            status=(
                StatusConfig(**(svc_data["status"] or {}))
                if "status" in svc_data
                else None
            ),
        )
        services.append(host)

    alloy_data = data.get("alloy")
    return AnsibleConfig(
        ssh_port=data.get("ssh_port", args.ssh_port),
        ssh_user=data.get("ssh_user", args.ssh_user),
        ssh_key_path=data.get("ssh_key_path", args.ssh_key),
        vips=_flatten_ips(data.get("vips", args.vips)),
        pips=_flatten_ips(data.get("pips", args.pips)),
        services=services,
        ssh_keys=dict(data.get("ssh_keys") or {}),
        alloy=AlloyConfig(**alloy_data) if alloy_data else None,
    )


# ---------------------------------------------------------------------------
# Args -> LabConfig conversion
# ---------------------------------------------------------------------------


def build_lab_config(args: argparse.Namespace) -> LabConfig:
    """Convert parsed CLI args into a LabConfig.

    Applies protocol-specific defaults and constructs the proper BuildSource.
    """
    protocol = Protocol(args.protocol)
    spec = get_spec(protocol)

    if args.command == "up:standalone":
        return _build_standalone_config(args, protocol, spec)

    if args.command in ("create:network", "create:ansible"):
        return _build_network_config(args, protocol, spec)

    raise ValueError(f"Cannot build LabConfig for command: {args.command!r}")


def _build_standalone_config(args, protocol, spec):
    """Build LabConfig for the up:standalone command."""
    server = args.server
    version = args.version

    # Load config overrides from file if provided
    config_overrides = {}
    if args.config_overrides:
        config_overrides = load_overrides_file(args.config_overrides)

    commit_hash = ""
    image = ""
    build_type = BuildType.IMAGE
    if args.commit:
        # Run an "all amendments Supported::Yes" image published by CI, and
        # resolve the amendment set from that same ref on GitHub. A semver
        # ref (e.g. 3.2.0) maps to the version tag; anything else is treated
        # as a commit sha -> sha-<short>.
        ref = args.commit
        if re.match(r"^v?\d+\.\d+", ref):
            tag = ref.lstrip("v")
        else:
            tag = f"sha-{ref[:7]}"
        image = f"ghcr.io/xrplf/xrpld/supported:{tag}"
        version = ref
        commit_hash = ref
    else:
        server = server or "rippleci"
        version = version or spec.default_build_version
        image = f"{server}/xrpld:{version}"

    source = BuildSource(
        protocol=protocol,
        build_type=build_type,
        build_server=server,
        build_version=version,
        owner=spec.github_owner,
        repo=spec.github_repo,
        commit_hash=commit_hash,
        image=image,
    )

    return LabConfig(
        protocol=protocol,
        mode=DeployMode.STANDALONE,
        build_source=source,
        network_id=(
            args.network_id
            if args.network_id is not None
            else spec.default_standalone_network_id
        ),
        log_level=args.log_level,
        node_db_type=NodeDbType(args.nodedb_type),
        add_ipfs=args.ipfs,
        public_vl_key=args.public_key,
        config_overrides=config_overrides,
        datagram_monitor=getattr(args, "datagram_monitor", None),
        all_amendments=getattr(args, "all_amendments", False),
        features_file=getattr(args, "features_file", None),
    )


def _build_network_config(args, protocol, spec):
    """Build LabConfig for create:network and create:ansible commands."""
    is_ansible = args.command == "create:ansible"
    has_local = hasattr(args, "local") and args.local
    mode = DeployMode.LOCAL if has_local else DeployMode.NETWORK
    server = args.build_server
    version = args.build_version

    # Load config overrides from file if provided
    config_overrides = {}
    if args.config_overrides:
        config_overrides = load_overrides_file(args.config_overrides)

    cluster_name = ""
    commit_hash = ""
    binary_path = ""
    default_image = ""
    build_type = BuildType.IMAGE
    owner = spec.github_owner
    repo = spec.github_repo

    if server and server.startswith("https://github.com/"):
        # https://github.com/<owner>/<repo>/tree/<branch>
        owner, _, tail = server[len("https://github.com/") :].partition("/")
        repo = tail.split("/")[0] or "rippled"
        branch = tail.split("/tree/")[1] if "/tree/" in tail else tail
        cluster_name = branch.replace("/", "-")
        commit_hash = version or ""
        binary_path = args.binary_path if args.binary_path else "./xrpld"
        build_type = BuildType.BINARY
    elif has_local:
        server = server or "https://github.com/XRPLF/rippled/tree"
        version = version or spec.default_build_version
        build_type = BuildType.BINARY
    else:
        server = server or spec.default_build_server
        version = version or spec.default_build_version
        default_image = f"{server}/xrpld:{version}"

    # Explicit cluster name (workspace dir) overrides the branch-derived one.
    if getattr(args, "cluster", None):
        cluster_name = args.cluster

    # A prebuilt image IS the binary: deploy it directly, skip the local-binary path.
    image = getattr(args, "image", None) or default_image
    if image:
        build_type = BuildType.IMAGE
        binary_path = ""

    source = BuildSource(
        protocol=protocol,
        build_type=build_type,
        build_server=server or "",
        build_version=version or "",
        owner=owner,
        repo=repo,
        cluster_name=cluster_name,
        commit_hash=commit_hash,
        binary_path=binary_path,
        image=image,
    )

    # Ansible config
    ansible: Optional[AnsibleConfig] = None
    if is_ansible:
        if hasattr(args, "ansible_config") and args.ansible_config:
            ansible = _build_ansible_config_from_file(args.ansible_config, args)
        else:
            if not args.vips or not args.pips:
                raise SystemExit(
                    "error: create:ansible requires --vips and --pips "
                    "(or --ansible_config with vips/pips in the YAML file)"
                )
            ansible = _build_ansible_config_from_args(args)
    elif hasattr(args, "ansible") and args.ansible:
        ansible = _build_ansible_config_from_args(args)

    # Auto-derive debug endpoint from stream, enforce trace for stream/debug
    log_level = args.log_level
    if ansible:
        ansible, log_level = _resolve_service_dependencies(ansible, log_level)

    # For ansible, derive counts from the YAML if not explicitly overridden
    num_validators = args.num_validators
    num_peers = args.num_peers
    if is_ansible and ansible:
        if ansible.vips and num_validators == 3:
            num_validators = len(ansible.vips)
        if ansible.pips and num_peers == 1:
            num_peers = len(ansible.pips)

    key_algorithm = "dilithium" if getattr(args, "quantum", False) else "ed25519"

    return LabConfig(
        protocol=protocol,
        mode=mode,
        build_source=source,
        network_id=(
            args.network_id if args.network_id is not None else spec.default_network_id
        ),
        log_level=log_level,
        num_validators=num_validators,
        num_peers=num_peers,
        port_offset=getattr(args, "port_offset", 0),
        genesis=args.genesis,
        db_seed=getattr(args, "db_seed", False),
        all_amendments=getattr(args, "all_amendments", False),
        features_file=getattr(args, "features_file", None),
        genesis_file=getattr(args, "genesis_file", None),
        quorum=args.quorum,
        node_db_type=NodeDbType(args.nodedb_type),
        online_delete=getattr(args, "online_delete", 256) or None,
        database_path=getattr(args, "database_path", "/var/lib/xrpld/db/rdb"),
        tree_cache_target_entries=getattr(args, "tree_cache_target_entries", 0),
        memory_limit=getattr(args, "memory_limit", None),
        binary_name=args.binary_name,
        key_algorithm=key_algorithm,
        config_overrides=config_overrides,
        datagram_monitor=getattr(args, "datagram_monitor", None),
        vl_site=getattr(args, "vl_site", None),
        bootstrap_vl=getattr(args, "bootstrap_vl", False),
        statsd_address=getattr(args, "statsd_address", None),
        perf_path=getattr(args, "perf_path", None),
        ansible=ansible,
        preload_accounts=getattr(args, "preload_accounts", 0),
        preload_trustlines=getattr(args, "preload_trustlines", 0),
        preload_balance=getattr(args, "preload_balance", "1000000000"),
        preload_currency=getattr(args, "preload_currency", "USD"),
    )


def _local_network_id(args: argparse.Namespace) -> int:
    """--network_id as given, else the spec default for the local network type."""
    if args.network_id is not None:
        return args.network_id
    spec = get_spec(Protocol(args.protocol))
    if args.network_type == "standalone":
        return spec.default_standalone_network_id
    return spec.default_network_id


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point: parse args and dispatch."""
    parser = _build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Workspace root holds each cluster's keystore — the network's identity. Point it at
    # the canonical location for a live network instead of copying keys around.
    ws_base = getattr(args, "workspace", None)
    workspace = Workspace(ws_base) if ws_base else Workspace()

    # Commands that need LabConfig -> LabRunner
    if args.command in ("up:standalone", "create:network", "create:ansible"):
        lab = build_lab_config(args)
        generated = LabRunner(lab, workspace).run()
        if args.command == "up:standalone" and not run_start_script(
            workspace, generated
        ):
            raise SystemExit(1)
        return

    # Operational commands return True on success; anything else exits 1.
    ok = True
    if args.command == "health":
        from xrpld_lab.health import check_consensus

        ok = check_consensus(
            args.vips, timeout_s=args.timeout, interval_s=args.interval
        )
    elif args.command == "deploy:ansible":
        ok = _deploy_ansible(workspace, args.name)
    elif args.command == "up":
        ok = run_start_script(workspace, args.name)
    elif args.command == "down":
        ok = run_stop_script(workspace, args.name)
    elif args.command == "remove":
        ok = remove_network(workspace, args.name)
    elif args.command == "down:standalone":
        ok = stop_standalone(workspace, args.name, args.protocol, args.version)
    elif args.command == "up:local":
        ok = start_local(
            protocol=args.protocol,
            network_type=args.network_type,
            network_id=_local_network_id(args),
            log_level=args.log_level,
            nodedb_type=args.nodedb_type,
            public_key=args.public_key,
        )
    elif args.command == "down:local":
        ok = stop_local()
    elif args.command == "update:node":
        ok = update_node_binary(
            workspace,
            args.name,
            args.node_id,
            args.node_type,
            args.build_server,
            args.build_version,
            image=args.image,
        )
    elif args.command == "vote:amendment":
        ok = vote_amendment(
            args.name, args.amendment_name, workspace, node_id=args.node_id
        )
    elif args.command == "node:stall":
        ok = node_stall(
            args.name,
            args.node_id,
            args.node_type,
            workspace,
            args.duration_ms,
            args.clear,
        )
    elif args.command == "node:restart":
        ok = restart_local_node(args.node_name, genesis=args.genesis)
    elif args.command == "logs:local":
        view_local_logs(args.node)
    elif args.command == "logs:standalone":
        view_standalone_logs(args.protocol)
    if not ok:
        raise SystemExit(1)


def _deploy_ansible(workspace: Workspace, name: str) -> bool:
    """Run the ansible deployment for an existing cluster; True when run.sh exits 0."""
    import os
    import subprocess

    cluster_dir = workspace.cluster_dir(name)
    ansible_dir = os.path.join(cluster_dir, "ansible")
    run_sh = os.path.join(ansible_dir, "run.sh")

    if not os.path.exists(run_sh):
        print(f"No ansible deployment found at {ansible_dir}")
        print("Run 'xrpld-lab create:ansible' first to generate deployment files.")
        return False

    # ansible refuses non-blocking stdio, which a parent can leave on inherited fds.
    for fd in (0, 1, 2):
        try:
            os.set_blocking(fd, True)
        except OSError:
            pass
    return subprocess.run(["bash", run_sh], cwd=ansible_dir).returncode == 0
