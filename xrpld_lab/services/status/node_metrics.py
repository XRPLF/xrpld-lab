#!/usr/bin/env python3
"""node-metrics: sample the host and xrpld -> SQLite ring buffer -> read-only JSON API.

Samples /proc, the local xrpld admin RPC, and the debug stream's own endpoints every
`interval` seconds, keeps raw samples plus 5-minute and 1-hour rollups, and serves both
the
series and the dashboard over HTTP. nginx terminates TLS in front of it.

With NODE_METRICS_NETWORK_NODES set it also serves /api/network and /api/network/health:
every listed node's /api/latest rolled up for the network dashboard, merged with the
operator-written network.json.

A build carrying DatagramMonitor also emits one packed binary "XDGM" packet per second
per
`[datagram_monitor]` sink. When one is pointed at this process the UDP listener fills in
the
fields only xrpld knows -- cache hit rates, node store IO, proposer count -- and doubles
as a
1 Hz liveness signal that does not depend on the admin RPC answering under load.

Stdlib only — no venv on the node.
"""

import argparse
import concurrent.futures
import json
import os
import socket
import sqlite3
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SECTOR_BYTES = 512
# Root of the procfs tree; tests point it at a directory of captured files.
PROC = "/proc"

XDGM_MAGIC = 0x4D474458  # 'XDGM'
XDGM_HEADER_SIZE = 652
XDGM_LGR_SIZE = 8
XDGM_OBJ_SIZE = 64

# Wire format is little-endian `[[gnu::packed]]`: a fixed 652-byte header, then
# ledger_range_count x LgrRange (8 bytes), then object entries (56-byte NUL-padded
# name +
# uint64). Decoder verified against DatagramMonitor.h.
_XDGM_FMT = (
    "<"
    "10I"  # magic, version, network_id, server_state, peer_count,
    #        memory_limit_gb, cpu_cores, ledger_range_count, warning_flags, padding_1
    "12Q"  # timestamp, uptime, io_latency_us, validation_quorum,
    #        fetch_pack_size, proposer_count, converge_time_ms,
    #        load_factor, load_base, reserve_base, reserve_inc, ledger_seq
    "32s33s7s32s"  # ledger_hash, node_public_key, padding2, version_string
    "8Q"  # process_memory_pages, system_memory_total/free/used,
    #       system_disk_total/free/used, io_wait_time
    "3d"  # load_avg_1min, load_avg_5min, load_avg_15min
    "5Q5Q"  # state_transitions[5], state_durations[5]
    "Q"  # initial_sync_us
    "16d"  # rates: network_in/out, disk_read/write x {1m,5m,1h,24h}
    "4QIiIIIIiII5Q"  # DebugCounters
)
_XDGM_FIELDS = [
    "magic",
    "version",
    "network_id",
    "server_state",
    "peer_count",
    "size_slot",
    "cpu_cores",
    "ledger_range_count",
    "warning_flags",
    "padding_1",
    "timestamp",
    "uptime",
    "io_latency_us",
    "validation_quorum",
    "fetch_pack_size",
    "proposer_count",
    "converge_time_ms",
    "load_factor",
    "load_base",
    "reserve_base",
    "reserve_inc",
    "ledger_seq",
    "ledger_hash",
    "node_public_key",
    "padding2",
    "version_string",
    "process_memory_pages",
    "system_memory_total",
    "system_memory_free",
    "system_memory_used",
    "system_disk_total",
    "system_disk_free",
    "system_disk_used",
    "io_wait_time",
    "load_avg_1min",
    "load_avg_5min",
    "load_avg_15min",
    "state_transitions_0",
    "state_transitions_1",
    "state_transitions_2",
    "state_transitions_3",
    "state_transitions_4",
    "state_durations_0",
    "state_durations_1",
    "state_durations_2",
    "state_durations_3",
    "state_durations_4",
    "initial_sync_us",
    "rate_net_in_1m",
    "rate_net_in_5m",
    "rate_net_in_1h",
    "rate_net_in_24h",
    "rate_net_out_1m",
    "rate_net_out_5m",
    "rate_net_out_1h",
    "rate_net_out_24h",
    "rate_disk_read_1m",
    "rate_disk_read_5m",
    "rate_disk_read_1h",
    "rate_disk_read_24h",
    "rate_disk_write_1m",
    "rate_disk_write_5m",
    "rate_disk_write_1h",
    "rate_disk_write_24h",
    "db_kb_total",
    "db_kb_ledger",
    "db_kb_transaction",
    "local_tx_count",
    "write_load",
    "historical_per_minute",
    "sle_hit_rate",
    "ledger_hit_rate",
    "al_size",
    "al_hit_rate",
    "fullbelow_size",
    "treenode_cache_size",
    "treenode_track_size",
    "node_write_count",
    "node_write_size",
    "node_fetch_count",
    "node_fetch_hit_count",
    "node_fetch_size",
]
assert struct.calcsize(_XDGM_FMT) == XDGM_HEADER_SIZE, struct.calcsize(_XDGM_FMT)

# OperatingMode enum order in NetworkOPs.h.
SERVER_STATES = ["disconnected", "connected", "syncing", "tracking", "full"]


def decode_xdgm(data):
    """Decode one XDGM datagram into a flat dict, or None if it is not one."""
    if len(data) < XDGM_HEADER_SIZE:
        return None
    rec = dict(zip(_XDGM_FIELDS, struct.unpack(_XDGM_FMT, data[:XDGM_HEADER_SIZE])))
    if rec["magic"] != XDGM_MAGIC:
        return None

    # v1 carried the [node_size] tier in this slot; v2 repurposed it as the memory
    # budget in
    # GB. Decoding a v1 tier as gigabytes would report "2 GB" for medium.
    slot = rec.pop("size_slot")
    rec["memory_limit_gb"] = slot if rec["version"] >= 2 else None

    rec["version_string"] = (
        rec["version_string"].split(b"\x00", 1)[0].decode("ascii", "replace")
    )
    del rec["padding_1"], rec["padding2"], rec["ledger_hash"], rec["node_public_key"]

    off = XDGM_HEADER_SIZE
    ranges = []
    for _ in range(rec.get("ledger_range_count", 0)):
        if off + XDGM_LGR_SIZE > len(data):
            break
        ranges.append(struct.unpack_from("<2I", data, off))
        off += XDGM_LGR_SIZE
    rec["ledger_ranges"] = ranges
    return rec


# Column -> aggregate used when rolling raw samples up. Gauges average; the rest are
# states
# that only make sense as the last value in the bucket.
NUMERIC = [
    "cpu_pct",
    "cpu_iowait_pct",
    "load1",
    "load5",
    "load15",
    "mem_total",
    "mem_used",
    "mem_avail",
    "swap_total",
    "swap_used",
    "xrpld_rss",
    "disk_total",
    "disk_used",
    "disk_read_bps",
    "disk_write_bps",
    "net_rx_bps",
    "net_tx_bps",
    "peers",
    "converge_ms",
    "load_factor",
    "io_latency_ms",
    "jq_overflow",
    "validated_seq",
    "ledger_span",
    "initial_sync_s",
    # Only a DatagramMonitor build reports these; they stay null until one runs.
    "proposers",
    "sle_hit_rate",
    "ledger_hit_rate",
    "node_write_count",
    "node_fetch_count",
    "treenode_cache_size",
    "write_load",
    "xdgm_age",
]
LAST = [
    "server_state",
    "build_version",
    "complete_ledgers",
    "xrpld_ok",
    "debugstream_ok",
    "redis_ok",
    "uptime",
    "xdgm_ok",
]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS samples (
    ts INTEGER PRIMARY KEY,
    {', '.join(f'{c} REAL' for c in NUMERIC)},
    server_state TEXT, build_version TEXT, complete_ledgers TEXT,
    xrpld_ok INTEGER, debugstream_ok INTEGER, redis_ok INTEGER, uptime INTEGER
);
CREATE TABLE IF NOT EXISTS rollup_5m (ts INTEGER PRIMARY KEY,
    {', '.join(f'{c} REAL' for c in NUMERIC)},
    server_state TEXT, build_version TEXT, complete_ledgers TEXT,
    xrpld_ok INTEGER, debugstream_ok INTEGER, redis_ok INTEGER, uptime INTEGER);
CREATE TABLE IF NOT EXISTS rollup_1h (ts INTEGER PRIMARY KEY,
    {', '.join(f'{c} REAL' for c in NUMERIC)},
    server_state TEXT, build_version TEXT, complete_ledgers TEXT,
    xrpld_ok INTEGER, debugstream_ok INTEGER, redis_ok INTEGER, uptime INTEGER);
CREATE TABLE IF NOT EXISTS events (
    ts INTEGER, kind TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
"""


# ------------------------------------------------------------------ host readings
def read_proc_stat():
    """Aggregate jiffy counters from /proc/stat: (total, idle+iowait, iowait)."""
    with open(f"{PROC}/stat") as fh:
        parts = fh.readline().split()
    v = [int(x) for x in parts[1:]]
    idle = v[3] + v[4]
    return sum(v), idle, v[4]


def read_meminfo():
    out = {}
    with open(f"{PROC}/meminfo") as fh:
        for line in fh:
            k, _, rest = line.partition(":")
            out[k] = int(rest.split()[0]) * 1024
    return out


def read_diskstats():
    """Bytes read and written across whole disks; partitions are skipped to avoid
    double-counting the same IO."""
    read_b = write_b = 0
    with open(f"{PROC}/diskstats") as fh:
        for line in fh:
            f = line.split()
            name = f[2]
            if name.startswith(("loop", "ram", "dm-")) or name[-1].isdigit():
                continue
            read_b += int(f[5]) * SECTOR_BYTES
            write_b += int(f[9]) * SECTOR_BYTES
    return read_b, write_b


def read_netdev():
    rx = tx = 0
    with open(f"{PROC}/net/dev") as fh:
        for line in fh.read().splitlines()[2:]:
            name, _, rest = line.partition(":")
            if name.strip() == "lo":
                continue
            f = rest.split()
            rx += int(f[0])
            tx += int(f[8])
    return rx, tx


def find_pid(name):
    """Pid of the process whose executable is `name`. Matched on argv[0], because xrpld
    renames its main thread and /proc/<pid>/comm reads `xrpld-main`."""
    for entry in os.listdir(PROC):
        if not entry.isdigit():
            continue
        try:
            with open(f"{PROC}/{entry}/cmdline", "rb") as fh:
                argv0 = fh.read().split(b"\x00", 1)[0]
            if argv0 and os.path.basename(argv0.decode("utf-8", "replace")) == name:
                return int(entry)
            with open(f"{PROC}/{entry}/comm") as fh:
                if fh.read().strip().split("-")[0] == name:
                    return int(entry)
        except OSError:
            continue
    return None


def read_rss(pid):
    try:
        with open(f"{PROC}/{pid}/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def port_open(port, host="127.0.0.1", timeout=2):
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def http_ok(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def admin_rpc(url, command, timeout=5):
    body = json.dumps({"method": command, "params": [{}]}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read()).get("result", {})
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------------ sampler
class Sampler:
    def __init__(self, cfg):
        self.cfg = cfg
        self.xdgm = None
        self.xdgm_ts = 0.0
        self.prev_cpu = read_proc_stat()
        self.prev_disk = read_diskstats()
        self.prev_net = read_netdev()
        self.prev_t = time.time()
        self.latest = {}

    def listen_xdgm(self):
        """Keep the most recent XDGM packet.

        One packet arrives per second per sink, so the newest is the only one
        worth holding.
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.cfg.xdgm_host, self.cfg.xdgm_port))
        except OSError as exc:
            # Without a socket the listener has nothing to do: record why and exit.
            detail = f"xdgm bind {self.cfg.xdgm_host}:{self.cfg.xdgm_port}: {exc}"
            print(detail, file=sys.stderr, flush=True)
            conn = connect(self.cfg.db)
            try:
                conn.executescript(SCHEMA)
                record_event(conn, "sampler_error", detail)
            finally:
                conn.close()
            return
        while True:
            try:
                data, _ = sock.recvfrom(65535)
                rec = decode_xdgm(data)
                if rec:
                    self.xdgm = rec
                    self.xdgm_ts = time.time()
            except OSError:
                time.sleep(1)

    def fresh_xdgm(self):
        """The last packet if it is recent enough to describe the node now."""
        if self.xdgm and time.time() - self.xdgm_ts <= max(5.0, self.cfg.interval * 3):
            return self.xdgm
        return None

    def sample(self):
        now = time.time()
        dt = max(now - self.prev_t, 1e-6)

        total, idle, iowait = read_proc_stat()
        d_total = total - self.prev_cpu[0]
        d_idle = idle - self.prev_cpu[1]
        d_iowait = iowait - self.prev_cpu[2]
        cpu_pct = 100.0 * (d_total - d_idle) / d_total if d_total > 0 else 0.0
        iowait_pct = 100.0 * d_iowait / d_total if d_total > 0 else 0.0
        self.prev_cpu = (total, idle, iowait)

        dr, dw = read_diskstats()
        disk_read_bps = max(dr - self.prev_disk[0], 0) / dt
        disk_write_bps = max(dw - self.prev_disk[1], 0) / dt
        self.prev_disk = (dr, dw)

        rx, tx = read_netdev()
        net_rx_bps = max(rx - self.prev_net[0], 0) / dt
        net_tx_bps = max(tx - self.prev_net[1], 0) / dt
        self.prev_net = (rx, tx)
        self.prev_t = now

        mem = read_meminfo()
        mem_total = mem.get("MemTotal", 0)
        mem_avail = mem.get("MemAvailable", 0)
        swap_total = mem.get("SwapTotal", 0)
        swap_used = swap_total - mem.get("SwapFree", 0)

        with open(f"{PROC}/loadavg") as fh:
            la = fh.read().split()

        st = os.statvfs(self.cfg.disk_path)
        disk_total = st.f_blocks * st.f_frsize
        disk_used = (st.f_blocks - st.f_bfree) * st.f_frsize

        pid = find_pid(self.cfg.process)
        rss = read_rss(pid) if pid else None

        row = {
            "ts": int(now),
            "cpu_pct": round(cpu_pct, 2),
            "cpu_iowait_pct": round(iowait_pct, 2),
            "load1": float(la[0]),
            "load5": float(la[1]),
            "load15": float(la[2]),
            "mem_total": mem_total,
            "mem_avail": mem_avail,
            "mem_used": mem_total - mem_avail,
            "swap_total": swap_total,
            "swap_used": swap_used,
            "xrpld_rss": rss,
            "disk_total": disk_total,
            "disk_used": disk_used,
            "disk_read_bps": round(disk_read_bps),
            "disk_write_bps": round(disk_write_bps),
            "net_rx_bps": round(net_rx_bps),
            "net_tx_bps": round(net_tx_bps),
            "xrpld_ok": 1 if pid else 0,
            # An empty URL or port 0 means the node runs neither; the column stays null.
            "debugstream_ok": (
                (1 if http_ok(self.cfg.debugstream_health) else 0)
                if self.cfg.debugstream_health
                else None
            ),
            "redis_ok": (
                (1 if port_open(self.cfg.redis_port) else 0)
                if self.cfg.redis_port
                else None
            ),
        }
        row.update(self.server_info())
        row.update(self.from_xdgm(row))
        self.latest = row
        return row

    def from_xdgm(self, row):
        """Fields only xrpld can report.

        Also carries a fallback for every field the admin RPC would have given
        `row` if it had answered this tick.
        """
        rec = self.fresh_xdgm()
        if not rec:
            return {"xdgm_ok": 0}
        out = {
            "xdgm_ok": 1,
            "xdgm_age": round(time.time() - self.xdgm_ts, 2),
            "proposers": rec.get("proposer_count"),
            "sle_hit_rate": rec.get("sle_hit_rate"),
            "ledger_hit_rate": rec.get("ledger_hit_rate"),
            "node_write_count": rec.get("node_write_count"),
            "node_fetch_count": rec.get("node_fetch_count"),
            "treenode_cache_size": rec.get("treenode_cache_size"),
            "write_load": rec.get("write_load"),
        }
        if row.get("server_state") in (None, "unreachable"):
            ranges = rec.get("ledger_ranges") or []
            state = rec.get("server_state")
            out.update(
                {
                    "server_state": (
                        SERVER_STATES[state]
                        if state is not None and state < len(SERVER_STATES)
                        else "unknown"
                    ),
                    "build_version": rec.get("version_string"),
                    "peers": rec.get("peer_count"),
                    "uptime": rec.get("uptime"),
                    "validated_seq": rec.get("ledger_seq"),
                    "converge_ms": rec.get("converge_time_ms"),
                    "load_factor": rec.get("load_factor"),
                    "io_latency_ms": round((rec.get("io_latency_us") or 0) / 1000),
                    "ledger_span": (
                        (max(r[1] for r in ranges) - min(r[0] for r in ranges))
                        if ranges
                        else None
                    ),
                    "initial_sync_s": round((rec.get("initial_sync_us") or 0) / 1e6, 1)
                    or None,
                }
            )
        return out

    def server_info(self):
        """The fields of server_info worth a time series. Absent when xrpld
        does not answer."""
        result = admin_rpc(self.cfg.admin_rpc, "server_info")
        info = (result or {}).get("info")
        if not info:
            return {"server_state": "unreachable"}
        complete = info.get("complete_ledgers", "")
        span = None
        if "-" in complete:
            lo, _, hi = complete.rpartition("-")
            lo = lo.split(",")[-1]
            if lo.strip().isdigit() and hi.strip().isdigit():
                span = int(hi) - int(lo)
        return {
            "server_state": info.get("server_state"),
            "build_version": info.get("build_version"),
            "complete_ledgers": complete,
            "peers": info.get("peers"),
            "uptime": info.get("uptime"),
            "validated_seq": (info.get("validated_ledger") or {}).get("seq"),
            "ledger_span": span,
            "converge_ms": round(
                (info.get("last_close") or {}).get("converge_time_s", 0) * 1000
            ),
            "load_factor": info.get("load_factor"),
            "io_latency_ms": info.get("io_latency_ms"),
            "jq_overflow": int(info.get("jq_trans_overflow", 0) or 0),
            # How long this run of the process took to first reach `full`.
            "initial_sync_s": round(
                int(info.get("initial_sync_duration_us", 0) or 0) / 1e6, 1
            )
            or None,
        }


# ------------------------------------------------------------------ storage
def connect(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_columns(conn):
    """Add columns a database from an earlier version does not have."""
    for table in ("samples", "rollup_5m", "rollup_1h"):
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col in NUMERIC:
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} REAL")
        for col in LAST:
            if col not in have:
                kind = (
                    "TEXT"
                    if col in ("server_state", "build_version", "complete_ledgers")
                    else "INTEGER"
                )
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
    conn.commit()


def insert(conn, row):
    cols = ["ts"] + NUMERIC + LAST
    values = [row.get(c) for c in cols]
    placeholders = ",".join("?" * len(cols))
    conn.execute(
        f"INSERT OR REPLACE INTO samples ({','.join(cols)}) VALUES ({placeholders})",
        values,
    )
    conn.commit()


def record_event(conn, kind, detail):
    conn.execute(
        "INSERT INTO events (ts, kind, detail) VALUES (?,?,?)",
        (int(time.time()), kind, detail),
    )
    conn.commit()


def rollup(conn, src, dst, bucket):
    """Fold completed buckets of `src` into `dst`, starting at the newest bucket
    `dst` already holds. Re-running is harmless."""
    avg = ", ".join(f"AVG({c}) AS {c}" for c in NUMERIC)
    last = ", ".join(
        f"(SELECT {c} FROM {src} s2 WHERE s2.ts/{bucket} = s1.ts/{bucket}"
        f" ORDER BY s2.ts DESC LIMIT 1) AS {c}"
        for c in LAST
    )
    cutoff = int(time.time()) // bucket * bucket
    conn.execute(
        f"INSERT OR REPLACE INTO {dst} (ts, {','.join(NUMERIC)}, {','.join(LAST)}) "
        f"SELECT (ts/{bucket})*{bucket} AS ts, {avg}, {last} FROM {src} s1 "
        f"WHERE ts >= (SELECT COALESCE(MAX(ts), 0) FROM {dst}) AND ts < ? "
        f"GROUP BY ts/{bucket}",
        (cutoff,),
    )
    conn.commit()


def prune(conn, cfg):
    now = int(time.time())
    conn.execute(
        "DELETE FROM samples WHERE ts < ?", (now - cfg.retain_raw_hours * 3600,)
    )
    conn.execute(
        "DELETE FROM rollup_5m WHERE ts < ?", (now - cfg.retain_5m_days * 86400,)
    )
    conn.execute(
        "DELETE FROM rollup_1h WHERE ts < ?", (now - cfg.retain_1h_days * 86400,)
    )
    conn.execute("DELETE FROM events WHERE ts < ?", (now - cfg.retain_1h_days * 86400,))
    conn.commit()


def table_for(window_s, cfg):
    """Coarsest table that still covers the window at useful resolution."""
    if window_s <= cfg.retain_raw_hours * 3600:
        return "samples"
    if window_s <= cfg.retain_5m_days * 86400:
        return "rollup_5m"
    return "rollup_1h"


# ------------------------------------------------------------------ network roll-up
# Health per role: a validator must be proposing, everything else must be full.
HEALTHY_STATE = {"validator": "proposing", "peer": "full"}


def parse_network_nodes(spec):
    """Parse "name=url[ role=validator|peer], ..." into [(name, role, url)]. A name
    starting with vnode is a validator and any other name a peer unless role= says
    otherwise."""
    nodes = []
    for item in (spec or "").split(","):
        tokens = item.split()
        if not tokens:
            continue
        name, _, url = tokens[0].partition("=")
        if not name or not url:
            raise ValueError(f"network node entry needs name=url: {item!r}")
        role = "validator" if name.startswith("vnode") else "peer"
        for token in tokens[1:]:
            key, _, value = token.partition("=")
            if key == "role" and value in HEALTHY_STATE:
                role = value
            else:
                raise ValueError(f"unknown network node token {token!r} in {item!r}")
        nodes.append((name, role, url.rstrip("/")))
    return nodes


def fetch_latest(url, timeout=3):
    """One node's /api/latest as a dict; raises on any transport or decode error."""
    with urllib.request.urlopen(f"{url}/api/latest", timeout=timeout) as resp:
        return json.loads(resp.read())


class Network:
    """Roll-up of every node's /api/latest for the network dashboard."""

    def __init__(self, nodes, network_file="", name="", fetch=fetch_latest, timeout=3):
        self.nodes = nodes
        self.network_file = network_file
        self.name = name
        self.fetch = fetch
        self.timeout = timeout

    def _node(self, name, role, url):
        try:
            latest = self.fetch(url, self.timeout)
            error = None
        except Exception as exc:
            latest, error = {}, str(exc)
        return {
            "name": name,
            "role": role,
            "server_state": latest.get("server_state"),
            "build_version": latest.get("build_version"),
            "validated_ledger": latest.get("validated_seq"),
            "peers": latest.get("peers"),
            "uptime": latest.get("uptime"),
            "ok": error is None and latest.get("xrpld_ok") == 1,
            "healthy": latest.get("server_state") == HEALTHY_STATE[role],
            "error": error,
        }

    def network_json(self):
        if not self.network_file:
            return {}
        try:
            with open(self.network_file) as fh:
                return json.load(fh)
        except OSError:
            return {}
        except ValueError as exc:
            return {"error": f"network.json: {exc}"}

    def report(self):
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, len(self.nodes))
        ) as pool:
            nodes = list(pool.map(lambda n: self._node(*n), self.nodes))
        validators = [n for n in nodes if n["role"] == "validator"]
        seqs = [n["validated_ledger"] for n in validators]
        # Samplers are polled a few seconds apart, so one ledger of lag is normal;
        # two or more validators further apart than that have diverged.
        agreement = bool(validators) and None not in seqs and max(seqs) - min(seqs) <= 1
        return {
            "name": self.name,
            "nodes": nodes,
            "healthy": all(n["healthy"] for n in nodes),
            "agreement": agreement,
            "validated_ledger": max(
                (
                    n["validated_ledger"]
                    for n in nodes
                    if n["validated_ledger"] is not None
                ),
                default=None,
            ),
            "network": self.network_json(),
            "generated_at": int(time.time()),
        }

    def health(self, report=None):
        report = report or self.report()
        failing = [
            {"name": n["name"], "role": n["role"], "server_state": n["server_state"]}
            for n in report["nodes"]
            if not n["healthy"]
        ]
        return {
            "ok": not failing,
            "failing": failing,
            "generated_at": report["generated_at"],
        }, (200 if not failing else 503)


# ------------------------------------------------------------------ http
class Handler(BaseHTTPRequestHandler):
    cfg = None
    sampler = None
    network = None
    server_version = "node-metrics"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        url = urlparse(self.path)
        route = url.path.rstrip("/") or "/"
        query = parse_qs(url.query)
        try:
            if route in ("/", "/index.html"):
                return self._dashboard()
            if route == "/api/latest":
                return self._json(self.sampler.latest or {})
            if route == "/api/series":
                return self._series(query)
            if route == "/api/events":
                return self._events(query)
            if route == "/api/health":
                latest = self.sampler.latest or {}
                ok = (
                    latest.get("xrpld_ok") == 1 and latest.get("server_state") == "full"
                )
                return self._json(
                    {
                        "ok": ok,
                        "state": latest.get("server_state"),
                        "ts": latest.get("ts"),
                    },
                    200 if ok else 503,
                )
            if route == "/api/network" and self.network:
                return self._json(self.network.report())
            if route == "/api/network/health" and self.network:
                return self._json(*self.network.health())
        except Exception as exc:  # a failed panel must not take the server down
            return self._json({"error": str(exc)}, 500)
        self._json({"error": "not found"}, 404)

    def _dashboard(self):
        try:
            with open(self.cfg.dashboard, "rb") as fh:
                self._send(200, fh.read(), "text/html; charset=utf-8")
        except OSError:
            self._json({"error": "dashboard not installed"}, 404)

    def _series(self, query):
        window = int(query.get("window", ["3600"])[0])
        window = max(60, min(window, self.cfg.retain_1h_days * 86400))
        table = table_for(window, self.cfg)
        since = int(time.time()) - window
        conn = connect(self.cfg.db)
        try:
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE ts >= ? ORDER BY ts", (since,)
            ).fetchall()
        finally:
            conn.close()
        return self._json(
            {"table": table, "window": window, "points": [dict(r) for r in rows]}
        )

    def _events(self, query):
        limit = max(1, min(int(query.get("limit", ["100"])[0]), 1000))
        conn = connect(self.cfg.db)
        try:
            rows = conn.execute(
                "SELECT ts, kind, detail FROM events ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        finally:
            conn.close()
        return self._json({"events": [dict(r) for r in rows]})


# ------------------------------------------------------------------ main
def sampler_loop(cfg, sampler):
    conn = connect(cfg.db)
    conn.executescript(SCHEMA)
    ensure_columns(conn)
    last_state = None
    last_sync = None
    last_maint = 0
    while True:
        started = time.time()
        try:
            row = sampler.sample()
            insert(conn, row)
            state = row.get("server_state")
            if state != last_state:
                if last_state is not None:
                    record_event(conn, "server_state", f"{last_state} -> {state}")
                last_state = state
            sync = row.get("initial_sync_s")
            if sync and sync != last_sync:
                record_event(
                    conn,
                    "initial_sync",
                    f"reached full {sync:.0f}s after start ({sync / 60:.1f} min)",
                )
                last_sync = sync
            if started - last_maint > 300:
                rollup(conn, "samples", "rollup_5m", 300)
                rollup(conn, "rollup_5m", "rollup_1h", 3600)
                prune(conn, cfg)
                last_maint = started
        except Exception as exc:
            try:
                record_event(conn, "sampler_error", str(exc))
            except sqlite3.Error:
                pass
        time.sleep(max(0.0, cfg.interval - (time.time() - started)))


def env(name, default):
    return os.environ.get(name, default)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db", default=env("NODE_METRICS_DB", "/var/lib/node-metrics/metrics.db")
    )
    p.add_argument("--http-host", default=env("NODE_METRICS_HTTP_HOST", "127.0.0.1"))
    p.add_argument(
        "--http-port", type=int, default=int(env("NODE_METRICS_HTTP_PORT", "8687"))
    )
    p.add_argument(
        "--interval", type=float, default=float(env("NODE_METRICS_INTERVAL", "10"))
    )
    p.add_argument(
        "--dashboard",
        default=env(
            "NODE_METRICS_DASHBOARD", "/usr/local/share/node-metrics/dashboard.html"
        ),
    )
    p.add_argument(
        "--admin-rpc", default=env("NODE_METRICS_ADMIN_RPC", "http://127.0.0.1:5007/")
    )
    p.add_argument(
        "--debugstream-health",
        default=env("NODE_METRICS_DEBUGSTREAM_HEALTH", "http://127.0.0.1:3000/health"),
    )
    p.add_argument(
        "--redis-port", type=int, default=int(env("NODE_METRICS_REDIS_PORT", "6379"))
    )
    p.add_argument(
        "--disk-path", default=env("NODE_METRICS_DISK_PATH", "/var/lib/xrpld/db")
    )
    p.add_argument("--process", default=env("NODE_METRICS_PROCESS", "xrpld"))
    p.add_argument("--xdgm-host", default=env("NODE_METRICS_XDGM_HOST", "0.0.0.0"))
    p.add_argument(
        "--xdgm-port", type=int, default=int(env("NODE_METRICS_XDGM_PORT", "9999"))
    )
    p.add_argument(
        "--retain-raw-hours",
        type=int,
        default=int(env("NODE_METRICS_RETAIN_RAW_HOURS", "48")),
    )
    p.add_argument(
        "--retain-5m-days",
        type=int,
        default=int(env("NODE_METRICS_RETAIN_5M_DAYS", "30")),
    )
    p.add_argument(
        "--retain-1h-days",
        type=int,
        default=int(env("NODE_METRICS_RETAIN_1H_DAYS", "365")),
    )
    p.add_argument(
        "--network-nodes",
        default=env("NODE_METRICS_NETWORK_NODES", ""),
        help="name=url[ role=validator|peer] list, comma-separated; "
        "enables /api/network",
    )
    p.add_argument(
        "--network-file",
        default=env("NODE_METRICS_NETWORK_FILE", "/opt/xrpld-status/network.json"),
    )
    p.add_argument("--network-name", default=env("NODE_METRICS_NETWORK_NAME", ""))
    return p.parse_args()


def main():
    cfg = parse_args()
    os.makedirs(os.path.dirname(cfg.db), exist_ok=True)
    conn = connect(cfg.db)
    conn.executescript(SCHEMA)
    ensure_columns(conn)
    conn.close()

    sampler = Sampler(cfg)
    threading.Thread(target=sampler_loop, args=(cfg, sampler), daemon=True).start()
    if cfg.xdgm_port:
        threading.Thread(target=sampler.listen_xdgm, daemon=True).start()

    Handler.cfg = cfg
    Handler.sampler = sampler
    if cfg.network_nodes:
        Handler.network = Network(
            parse_network_nodes(cfg.network_nodes),
            network_file=cfg.network_file,
            name=cfg.network_name,
        )
    ThreadingHTTPServer((cfg.http_host, cfg.http_port), Handler).serve_forever()


if __name__ == "__main__":
    main()
