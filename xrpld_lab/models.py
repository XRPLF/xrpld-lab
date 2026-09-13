from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Protocol(Enum):
    XRPL = "xrpl"


class DeployMode(Enum):
    STANDALONE = "standalone"
    NETWORK = "network"
    LOCAL = "local"


class NodeRole(Enum):
    VALIDATOR = "validator"
    PEER = "peer"
    STANDALONE = "standalone"


class BuildType(Enum):
    IMAGE = "image"
    BINARY = "binary"


class NodeDbType(Enum):
    NUDB = "NuDB"
    MEMORY = "Memory"


# ---------------------------------------------------------------------------
# Port calculation
# ---------------------------------------------------------------------------

# Base ports (from xrpld_netgen/utils/misc.py)
_RPC_PUBLIC: int = 5007
_RPC_ADMIN: int = 5005
_WS_PUBLIC: int = 6008
_WS_ADMIN: int = 6006
_PEER: int = 51235


@dataclass(frozen=True)
class PortSet:
    """Immutable set of network ports for a single node."""

    rpc_public: int
    rpc_admin: int
    ws_public: int
    ws_admin: int
    peer: int

    @classmethod
    def for_node(cls, index: int, role: NodeRole, port_offset: int = 0) -> PortSet:
        """Calculate ports for a node based on its role and index.

        - VALIDATOR: base + index * 100
        - PEER: base + index * 10
        - STANDALONE: base ports (index is ignored)

        port_offset shifts every port so a second cluster can run beside the first
        (candidate binary next to a baseline) without colliding.
        """
        if role == NodeRole.VALIDATOR:
            offset = index * 100
        elif role == NodeRole.PEER:
            offset = index * 10
        elif role == NodeRole.STANDALONE:
            offset = 0
        else:
            raise ValueError(f"Unknown node role: {role}")
        offset += port_offset

        return cls(
            rpc_public=_RPC_PUBLIC + offset,
            rpc_admin=_RPC_ADMIN + offset,
            ws_public=_WS_PUBLIC + offset,
            ws_admin=_WS_ADMIN + offset,
            peer=_PEER + offset,
        )

    def publish_mappings(self) -> list[str]:
        """Docker port publications; admin ports bind the host's loopback only."""
        return [
            f"{self.rpc_public}:{self.rpc_public}",
            f"127.0.0.1:{self.rpc_admin}:{self.rpc_admin}",
            f"{self.ws_public}:{self.ws_public}",
            f"127.0.0.1:{self.ws_admin}:{self.ws_admin}",
            f"{self.peer}:{self.peer}",
        ]


# ---------------------------------------------------------------------------
# Config sub-objects
# ---------------------------------------------------------------------------


@dataclass
class ServerConfig:
    rpc_public: bool = True
    rpc_admin: bool = True
    ws_public: bool = True
    ws_admin: bool = True
    peer: bool = True
    ssl_verify: bool = False
    ssl_key_path: Optional[str] = None
    ssl_cert_path: Optional[str] = None
    send_queue_limit: int = 65535


@dataclass
class NodeDbConfig:
    db_type: NodeDbType = NodeDbType.NUDB
    path: str = "db"
    num_ledgers: Optional[int] = 10000

    @classmethod
    def for_mode(cls, db_type: NodeDbType, mode: DeployMode) -> NodeDbConfig:
        """Derive the [node_db] path from the db type and deploy mode."""
        if db_type == NodeDbType.MEMORY:
            path = "./"
        elif mode == DeployMode.STANDALONE:
            path = "/opt/ripple/lib/db"
        elif mode == DeployMode.NETWORK:
            path = "/var/lib/xrpld/db"
        else:
            path = "db"
        return cls(db_type=db_type, path=path)


@dataclass
class TransactionQueueConfig:
    ledgers_in_queue: int = 20
    minimum_queue_size: int = 2000
    retry_sequence_percent: int = 25
    minimum_escalation_multiplier: int = 500
    # Open-ledger caps raised so the fee market stays FLAT for perf runs: the open
    # ledger
    # accepts every txn at base fee up to 100k/ledger, so nothing escalates or
    # queues, and the
    # measured bottleneck is the node's apply rate (real capacity) not a fee-
    # market artifact.
    # (A hard cap of 10k was filling at peak load and queueing every overflow
    # regardless of fee.)
    minimum_txn_in_ledger: int = 100000
    minimum_txn_in_ledger_standalone: int = 100000
    target_txn_in_ledger: int = 100000
    maximum_txn_in_ledger: int = 100000
    normal_consensus_increase_percent: int = 20
    slow_consensus_decrease_percent: int = 50
    maximum_txn_per_account: int = 100000
    minimum_last_ledger_buffer: int = 2
    zero_basefee_transaction_feelevel: int = 256000


@dataclass
class WorkerConfig:
    workers: int = 10
    io_workers: int = 10
    prefetch_workers: int = 10


@dataclass
class VotingConfig:
    account_reserve: int = 1000000
    owner_reserve: int = 200000
    reference_fee: int = 10


@dataclass
class ValidatorIdentity:
    public_key: str
    token: str
    manifest: str


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


@dataclass
class NodeConfig:
    """Full configuration for a single xrpld node."""

    name: str
    index: int
    role: NodeRole
    protocol: Protocol
    network_id: int
    ports: PortSet
    server: ServerConfig = field(default_factory=ServerConfig)
    node_db: NodeDbConfig = field(default_factory=NodeDbConfig)
    tx_queue: TransactionQueueConfig = field(default_factory=TransactionQueueConfig)
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    voting: VotingConfig = field(default_factory=VotingConfig)
    db_path: str = "/opt/ripple/lib/db"
    debug_path: str = "/opt/ripple/log/debug.log"
    # [insight] StatsD sink as "<ip>:<port>"; None omits the stanza. The Alloy sidecar
    # shares the node container's network namespace, so it listens on this same
    # loopback.
    statsd_address: Optional[str] = None
    statsd_prefix: str = "rippled"
    # [perf] perf_log path; None omits the stanza. Alloy requires this file to exist.
    perf_path: Optional[str] = None
    perf_log_interval: int = 2
    size_node: str = "huge"
    tree_cache_ram_percent: int = 50
    # Explicit tree-cache entry target ([tree_cache_target_entries]); 0 = omit
    # the stanza (node_size preset + RAM guardrail decide). Set when the cache
    # must hold the whole expected live tree (e.g. 25M-account growth study).
    tree_cache_target_entries: int = 0
    # RAM budget in GB for the memory-pressure binary ([memory_limit]). When set,
    # replaces [node_size]/[tree_cache_ram_percent]/[tree_cache_target_entries]
    # (all removed on that branch); None keeps the node_size path for stock builds.
    memory_limit: Optional[int] = None
    consensus_reserve_threads: int = 2
    log_level: str = "trace"
    private_peer: bool = False
    # JobQueue JtTransaction cap; raised kMaxJobQueueTx clamps to [100,100000]
    max_transactions: int = 100000
    validator: Optional[ValidatorIdentity] = None
    validators: List[str] = field(default_factory=list)
    cluster_nodes: List[str] = field(default_factory=list)
    ips_urls: List[str] = field(default_factory=list)
    ips_fixed_urls: List[str] = field(default_factory=list)
    # [datagram_monitor] endpoint lines ("<ip> <port>", space-separated to match the
    # node's parseEndpoint). Each measured node fires XDGM here — the perf-server sink.
    datagram_monitor: List[str] = field(default_factory=list)
    vl_sites: List[str] = field(default_factory=list)
    vl_keys: List[str] = field(default_factory=list)
    amendment_majority_time: Optional[str] = None
    amendments: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Build source
# ---------------------------------------------------------------------------


@dataclass
class BuildSource:
    protocol: Protocol
    build_type: BuildType
    build_server: str
    build_version: str
    owner: str = ""
    repo: str = ""
    image: str = ""
    binary_path: str = ""
    cluster_name: str = ""
    commit_hash: str = ""
    feature_content: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Ansible optional service configs
# ---------------------------------------------------------------------------


@dataclass
class NginxConfig:
    domain: str = ""
    ssl_org: str = ""
    ssl_ou: str = ""
    ws_port: str = ""
    rpc_port: str = ""
    faucet_port: str = "8080"
    debug_port: str = "8081"
    compiler_port: str = "9000"
    # Services issued publicly-trusted Let's Encrypt certs instead of
    # self-signed ones (for DNS-only hostnames that clients hit directly).
    # Valid entries: wss, rpc, faucet, debug, compiler, vl.
    letsencrypt_services: list = field(default_factory=list)
    letsencrypt_email: str = ""


@dataclass
class VlConfig:
    """Static publisher-list (UNL) vhost served at vl.<domain>.

    The list is signed by the publisher key and checked against
    [validator_list_keys], so its integrity does not come from TLS: plain http is a
    valid validator_list_site. That matters because Let's Encrypt cannot issue for the
    hostname until its DNS record exists, whereas http serving works the moment the
    record resolves.
    """

    # Signed list, relative to the cluster dir. xrpld-lab writes it here at
    # generate time.
    source: str = "vl/vl.json"
    filename: str = "vl.json"
    # Serve the host's own domain root too, not just vl.<domain>.
    root_dir: str = "/var/www/vl"


@dataclass
class RedisConfig:
    image: str = "redis"
    container_name: str = "alpha-redis-main"
    network_name: str = "alpha"


@dataclass
class FaucetConfig:
    image: str = "transia/faucet"
    port: int = 8080
    ws_url: str = ""
    network_id: str = ""
    seed: str = ""


@dataclass
class StreamConfig:
    port: int = 1400
    container_name: str = "pnode1"
    log_path: str = "/opt/ripple/log/debug.log"


@dataclass
class DebugConfig:
    image: str = "transia/debugstream"
    port: int = 8081
    endpoint: str = ""
    redis_host: str = "alpha-redis-main"
    redis_port: str = "6379"
    network_name: str = "alpha"


@dataclass
class CompilerConfig:
    image: str = "transia/compiler-api:latest"
    port: int = 5002
    repo: str = "git@github.com:Transia-RnD/xrpl-hooks-compiler.git"
    branch: str = "dangell/smart-contracts"
    volumes: List[str] = field(default_factory=list)


@dataclass
class StatusConfig:
    """Per-node status sampler plus the network roll-up page at /status/.

    Every node runs node_metrics.py as the xrpld-status systemd unit; the copy
    on the services host also aggregates the others into /api/network for
    network-dashboard.html.
    """

    port: int = 8687
    # UDP port the sampler's XDGM listener binds; each node's [datagram_monitor]
    # targets it.
    xdgm_port: int = 9999
    interval: int = 10
    # Process name the sampler looks for in /proc for xrpld's RSS.
    process: str = "xrpld"
    disk_path: str = "/var/lib/xrpld/db"
    # Heading on the network dashboard; the nginx domain when empty.
    network_name: str = ""
    retain_raw_hours: int = 48
    retain_5m_days: int = 30
    retain_1h_days: int = 365


@dataclass
class ServicesHost:
    """A host that runs optional infrastructure services.

    Each ServicesHost becomes its own ansible group and gets a dedicated
    directory under ``ansible/services/{name}/``.  Nginx is typically
    per-host (each domain points at one IP via Cloudflare), while
    faucet/redis/stream/debug/compiler usually live on a single host
    but can be spread across multiple if needed.
    """

    ip: str
    name: str
    nginx: Optional[NginxConfig] = None
    vl: Optional[VlConfig] = None
    redis: Optional[RedisConfig] = None
    faucet: Optional[FaucetConfig] = None
    stream: Optional[StreamConfig] = None
    debug: Optional[DebugConfig] = None
    compiler: Optional[CompilerConfig] = None
    status: Optional[StatusConfig] = None

    @property
    def enabled_services(self) -> List[str]:
        out: List[str] = []
        if self.nginx:
            out.append("nginx")
        if self.status:
            out.append("status")
        if self.redis:
            out.append("redis")
        if self.faucet:
            out.append("faucet")
        if self.stream:
            out.append("stream")
        if self.debug:
            out.append("debug")
        if self.compiler:
            out.append("compiler")
        return out


@dataclass
class AnsibleConfig:
    ssh_port: int = 20
    ssh_user: str = "ubuntu"
    ssh_key_path: str = "~/.ssh/id_rsa"
    vips: List[str] = field(default_factory=list)
    pips: List[str] = field(default_factory=list)
    services: List[ServicesHost] = field(default_factory=list)
    # Per-node private key overrides, keyed by node IP. A node listed here gets its own
    # key in hosts.txt instead of the cluster-wide ssh_key_path, so drill access can be
    # handed out and revoked one node at a time.
    ssh_keys: Dict[str, str] = field(default_factory=dict)
    # Grafana Alloy telemetry sidecar; None leaves nodes un-monitored.
    alloy: Optional["AlloyConfig"] = None

    def key_for(self, ip: str) -> str:
        return self.ssh_keys.get(ip, self.ssh_key_path)

    @property
    def status_host(self) -> Optional[ServicesHost]:
        """The services host whose nginx serves /status/, or None when no
        host has it."""
        for host in self.services:
            if host.status:
                return host
        return None


@dataclass
class AlloyConfig:
    """Grafana Alloy sidecar shipping rippled StatsD + logs to xrpl-monitoring.

    Runs in the node container's network namespace so rippled's [insight] loopback
    address reaches Alloy without publishing a port.
    """

    push_host: str
    # Build context holding docker/alloy.Dockerfile + alloy/ from the xrpl-
    # monitoring repo.
    source_dir: str
    # Cluster-wide Basic Auth, used for any node absent from `credentials`.
    username: str = ""
    password: str = ""
    # Per-node Basic Auth keyed by node name: {"vnode1": {"username": ..,
    # "password": ..}}.
    # The monitoring backend maps each credential to its own tenant, so one
    # credential per
    # node keeps a leaked node credential from carrying the whole network's telemetry.
    credentials: Dict[str, Dict[str, str]] = field(default_factory=dict)
    image: str = "xrpl-monitoring-alloy:local"
    container_name: str = "xrpl-monitoring-alloy"
    statsd_port: int = 9125
    # Remote paths the sidecar mounts read-only. The preflight parses the config for
    # [insight]/[debug_logfile]/[perf] and refuses to start if any is missing.
    rippled_config_path: str = "/opt/ripple/config/xrpld.cfg"
    rippled_log_dir: str = "/opt/ripple/log"
    # Prefixes the per-node `node` label pushed to Mimir/Loki (e.g. "alphanet-vnode1").
    node_label_prefix: str = ""
    # Optional existing StatsD collector to receive the raw rippled packets unchanged.
    statsd_relay_addr: str = ""

    def node_label(self, node_name: str) -> str:
        return (
            f"{self.node_label_prefix}{node_name}"
            if self.node_label_prefix
            else node_name
        )

    def creds_for(self, node_name: str) -> Dict[str, str]:
        c = self.credentials.get(node_name, {})
        return {
            "username": c.get("username", self.username),
            "password": c.get("password", self.password),
        }


# ---------------------------------------------------------------------------
# GCP provisioning (multi-region)
# ---------------------------------------------------------------------------

# Default zones spread the cluster across continents so the measured TPS knee
# reflects real cross-region consensus latency (method-comparable to perf-iac,
# not number-comparable to a single-region/LAN run). Validators land in 5
# regions, P2P nodes in 4 others — every node on its own ephemeral-IP VM.
_DEFAULT_VALIDATOR_ZONES: List[str] = [
    "us-central1-a",
    "europe-west1-b",
    "asia-east1-a",
    "us-east1-b",
    "australia-southeast1-a",
]
_DEFAULT_PEER_ZONES: List[str] = [
    "us-west1-a",
    "europe-west2-a",
    "asia-southeast1-a",
    "southamerica-east1-a",
]


@dataclass
class GcpConfig:
    """GCP multi-region provisioning for an xrpld perf cluster.

    Terraform stands up one VM per node (ephemeral external IP). The harvested
    IPs feed straight into the existing ansible pipeline as ``vips``/``pips`` —
    GCP is a provisioning front-end to NETWORK mode, not a separate deploy path.
    """

    project: str
    validator_zones: List[str] = field(
        default_factory=lambda: list(_DEFAULT_VALIDATOR_ZONES)
    )
    peer_zones: List[str] = field(default_factory=lambda: list(_DEFAULT_PEER_ZONES))
    machine_type: str = "n2-standard-8"
    disk_gb: int = 100
    image: str = "ubuntu-os-cloud/ubuntu-2204-lts"
    ssh_user: str = "ubuntu"
    # Public key uploaded to instance metadata; private half is used by ansible.
    ssh_pubkey_path: str = "~/.ssh/id_rsa.pub"
    ssh_key_path: str = "~/.ssh/id_rsa"
    ssh_port: int = 22
    network_tag: str = "xrpld-perf"
    # Firewall source ranges. 0.0.0.0/0 is fine for a throwaway perf net; lock
    # this down to your load-gen / collector IPs for anything longer-lived.
    allowed_source_ranges: List[str] = field(default_factory=lambda: ["0.0.0.0/0"])
    # Peer (51235+), public RPC/WS, and ssh. Admin ports stay node-local.
    open_tcp_ports: List[str] = field(
        default_factory=lambda: ["22", "5007-5520", "6008-6520", "51235-51740"]
    )

    @property
    def region(self) -> str:
        """Provider region derived from the first validator zone (us-
        central1-a → us-central1)."""
        zone = self.validator_zones[0]
        return zone.rsplit("-", 1)[0]

    @property
    def num_validators(self) -> int:
        return len(self.validator_zones)

    @property
    def num_peers(self) -> int:
        return len(self.peer_zones)


# ---------------------------------------------------------------------------
# Top-level lab configuration
# ---------------------------------------------------------------------------


@dataclass
class LabConfig:
    protocol: Protocol
    mode: DeployMode
    build_source: BuildSource
    network_id: int
    log_level: str = "trace"
    num_validators: int = 1
    num_peers: int = 0
    # Shifts every node port, so a candidate cluster can run beside an existing one.
    port_offset: int = 0
    # None: fresh chain when the workspace has no keystore for the cluster,
    # preserve when it has one. True or False forces either.
    genesis: Optional[bool] = None
    # Boot nodes with --load from a snapshot-restored database directory instead of
    # a genesis JSON (large prefunded state, see loadtester snapshot_push.sh).
    db_seed: bool = False
    # Pre-enable EVERY amendment in genesis, ignoring the Supported::yes/no flag
    # in features.macro. Requires a binary built with those amendments supported
    # (else it amendment-blocks). For perf/test networks only.
    all_amendments: bool = False
    # Read features.macro from this local path instead of fetching from GitHub at
    # the build commit. Use when the binary is built from an unpushed commit, so
    # the enabled amendment set exactly matches the binary. None = fetch remotely.
    features_file: Optional[str] = None
    # Custom genesis JSON (e.g. prefunded accounts). None = xrpld-lab's bundled
    # genesis.<protocol>.json. The resolved amendments are merged into whichever
    # is used.
    genesis_file: Optional[str] = None
    quorum: Optional[int] = None
    node_db_type: NodeDbType = NodeDbType.NUDB
    # online_delete ledger count for network nodes; None = disabled (full history).
    online_delete: Optional[int] = 256
    # [database_path] (SQLite) for network nodes. Defaults to the node-db volume so a
    # large tx history stays off the boot disk; pin it for a network already running
    # elsewhere, or its relational db is stranded on redeploy.
    database_path: str = "/var/lib/xrpld/db/rdb"
    # Explicit tree-cache entry target for network nodes; 0 = preset sizing.
    tree_cache_target_entries: int = 0
    # RAM budget in GB for the memory-pressure binary ([memory_limit]); None =
    # node_size path (stock builds).
    memory_limit: Optional[int] = None
    binary_name: str = "xrpld"
    # Perf-server XDGM sink as "<ip> <port>" (e.g. "10.128.0.2 9876"); None disables the
    # [datagram_monitor] stanza. Must be the server's INTERNAL IP (firewall is
    # VPC-only).
    datagram_monitor: Optional[str] = None
    public_vl_key: Optional[str] = None
    # Publisher list URL the nodes fetch ([validator_list_sites]). None keeps the
    # compose-internal http://vl/vl.json, which only resolves inside a local cluster.
    vl_site: Optional[str] = None
    # Emit the static [validators] list alongside the publisher list so a fresh chain
    # reaches quorum before the VL site is up, then converges on the VL.
    bootstrap_vl: bool = False
    # [insight] StatsD sink + [perf] log for the Alloy telemetry sidecar; None
    # omits both.
    statsd_address: Optional[str] = None
    perf_path: Optional[str] = None
    add_ipfs: bool = False
    ansible: Optional[AnsibleConfig] = None
    gcp: Optional[GcpConfig] = None
    key_algorithm: str = "ed25519"
    config_overrides: dict = field(default_factory=dict)
    # Prefunded genesis (perf-iac style): inject N AccountRoot + M RippleState
    # entries directly into genesis so the network starts with realistic state.
    preload_accounts: int = 0
    preload_trustlines: int = 0
    preload_balance: str = "1000000000"
    preload_currency: str = "USD"

    @property
    def effective_quorum(self) -> int:
        """Return the quorum: explicit value if set, else num_validators - 1 (min 1)."""
        if self.quorum is not None:
            return self.quorum
        return max(self.num_validators - 1, 1)

    def datagram_monitor_for(self, node_ip: str) -> Optional[List[str]]:
        """[datagram_monitor] lines for one node: the explicit sink when given, else the
        node's own address and the status sampler's XDGM port. The node runs in a bridge
        network container, so the host is reached at its own address, not at loopback.
        """
        if self.datagram_monitor:
            return [self.datagram_monitor]
        host = self.ansible.status_host if self.ansible else None
        if host and node_ip:
            return [f"{node_ip} {host.status.xdgm_port}"]
        return None

    def statsd_prefix_for(self, cluster: str, node_name: str) -> str:
        """[insight] prefix for one node. The Alloy mapping strips it, so it only has to
        be distinct enough to read in a raw relayed StatsD stream."""
        return f"{cluster}.{node_name}" if self.statsd_address else "rippled"
