#!/usr/bin/env python
# coding: utf-8

import json
import os
import socket
import sqlite3
import struct
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from xrpld_lab.services.status import node_metrics as nm


def _latest(state, seq=100, ok=1, **extra):
    row = {
        "server_state": state,
        "validated_seq": seq,
        "xrpld_ok": ok,
        "peers": 4,
        "uptime": 3600,
        "build_version": "3.1.0",
    }
    row.update(extra)
    return row


def _fake_fetch(table):
    """urlopen stand-in keyed by node URL; a missing URL is a refused connection."""

    def fetch(url, timeout):
        if url not in table:
            raise urllib.error.URLError("connection refused")
        return table[url]

    return fetch


def _url(srv, path=""):
    return f"http://127.0.0.1:{srv.server_address[1]}{path}"


def _get(srv, path):
    try:
        with urllib.request.urlopen(_url(srv, path), timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _get_raw(srv, path):
    with urllib.request.urlopen(_url(srv, path), timeout=5) as resp:
        return resp.status, resp.headers["Content-Type"], resp.read()


def _serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _closed_port():
    """A loopback port nothing listens on: bound once, then released."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def rpc():
    """Loopback stand-in for xrpld's admin RPC and the debug stream's /health.

    POST bodies are decoded into `calls`; `result` is the "result" of every
    answer unless `body` sets the raw bytes to send instead.
    """
    state = SimpleNamespace(result=None, body=None, calls=[])

    class RpcHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _reply(self, code, payload, content_type):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self._reply(200 if self.path == "/health" else 503, b"ok", "text/plain")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            state.calls.append(
                (self.headers["Content-Type"], json.loads(self.rfile.read(length)))
            )
            payload = state.body
            if payload is None:
                payload = json.dumps({"result": state.result}).encode()
            self._reply(200, payload, "application/json")

    srv = _serve(RpcHandler)
    state.port = srv.server_address[1]
    state.url = _url(srv, "/")
    yield state
    srv.shutdown()


def _write_proc(
    root,
    cpu=(1000, 50, 300, 8000, 200, 10, 20, 0, 0, 0),
    sda=(20000, 8000),
    eth0=(100000, 40000),
    procs=(),
):
    """A procfs tree of captured files under `root`.

    `cpu` is the aggregate jiffy row of /proc/stat, `sda` and `eth0` the sector
    and byte counters of the one whole disk and the one non-loopback interface,
    `procs` a list of (pid, cmdline bytes or None, comm or None, VmRSS kB or None).
    """
    root = Path(root)
    (root / "net").mkdir(parents=True, exist_ok=True)
    (root / "self").mkdir(exist_ok=True)
    (root / "stat").write_text(
        "cpu  " + " ".join(str(v) for v in cpu) + "\n"
        "cpu0 500 25 150 4000 100 5 10 0 0 0\n"
        "intr 12345 0 0\n"
        "ctxt 67890\n"
    )
    (root / "meminfo").write_text(
        "MemTotal:       16384000 kB\n"
        "MemFree:         1024000 kB\n"
        "MemAvailable:    8192000 kB\n"
        "Buffers:          256000 kB\n"
        "SwapTotal:       2097152 kB\n"
        "SwapFree:        1048576 kB\n"
    )
    (root / "diskstats").write_text(
        "   7       0 loop0 10 0 80 5 0 0 0 0 0 0 0\n"
        "   1       0 ram0 0 0 0 0 0 0 0 0 0 0 0\n"
        " 253       0 dm-0 500 0 4000 100 300 0 2400 50 0 0 0\n"
        f"   8       0 sda 1000 20 {sda[0]} 400 500 10 {sda[1]} 300 0 500 700\n"
        "   8       1 sda1 900 20 18000 350 450 10 7000 250 0 400 600\n"
    )
    (root / "net" / "dev").write_text(
        "Inter-|   Receive                                                "
        "|  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast"
        "|bytes    packets errs drop fifo colls carrier compressed\n"
        "    lo: 5000 50 0 0 0 0 0 0 5000 50 0 0 0 0 0 0\n"
        f"  eth0: {eth0[0]} 800 0 0 0 0 0 0 {eth0[1]} 300 0 0 0 0 0 0\n"
        "  eth1: 20000 100 0 0 0 0 0 0 10000 60 0 0 0 0 0 0\n"
    )
    (root / "loadavg").write_text("0.52 0.48 0.40 1/512 12345\n")
    for pid, cmdline, comm, rss_kb in procs:
        entry = root / str(pid)
        entry.mkdir(exist_ok=True)
        if cmdline is not None:
            (entry / "cmdline").write_bytes(cmdline)
        if comm is not None:
            (entry / "comm").write_text(comm + "\n")
        if rss_kb is not None:
            (entry / "status").write_text(
                f"Name:\txrpld\nVmPeak:\t{rss_kb + 4096} kB\nVmRSS:\t{rss_kb} kB\n"
            )
    return str(root)


XRPLD_PROC = (
    4242,
    b"/opt/xrpld/xrpld\x00--conf\x00/etc/xrpld.cfg\x00",
    "xrpld-main",
    2048000,
)


_XDGM_BYTES_FIELDS = ("ledger_hash", "node_public_key", "padding2", "version_string")


def _xdgm_packet(**over):
    """A v2 XDGM header packed with the module's own format; `over` sets fields."""
    values = {name: 0 for name in nm._XDGM_FIELDS}
    values.update({name: b"" for name in _XDGM_BYTES_FIELDS})
    values.update(magic=nm.XDGM_MAGIC, version=2)
    values.update(over)
    return struct.pack(nm._XDGM_FMT, *(values[name] for name in nm._XDGM_FIELDS))


def _sampler(monkeypatch, cfg, cls=None):
    monkeypatch.setattr(nm, "read_proc_stat", lambda: (0, 0, 0))
    monkeypatch.setattr(nm, "read_diskstats", lambda: (0, 0))
    monkeypatch.setattr(nm, "read_netdev", lambda: (0, 0))
    return (cls or nm.Sampler)(cfg)


NODES = nm.parse_network_nodes(
    "vnode1=http://10.0.0.1:8687 role=validator,"
    "vnode2=http://10.0.0.2:8687 role=validator,"
    "pnode1=http://127.0.0.1:8687 role=peer"
)


class TestParseNetworkNodes:
    def test_roles_from_name_prefix(self):
        nodes = nm.parse_network_nodes("vnode1=http://a:1,pnode1=http://b:1/")
        assert nodes == [
            ("vnode1", "validator", "http://a:1"),
            ("pnode1", "peer", "http://b:1"),
        ]

    def test_explicit_role_token_overrides_the_prefix(self):
        nodes = nm.parse_network_nodes(
            "watcher=http://c:1 role=validator, other=http://d:1"
        )
        assert nodes[0] == ("watcher", "validator", "http://c:1")
        assert nodes[1][1] == "peer"

    def test_empty_spec(self):
        assert nm.parse_network_nodes("") == []

    def test_bad_entries_raise(self):
        with pytest.raises(ValueError):
            nm.parse_network_nodes("vnode1")
        with pytest.raises(ValueError):
            nm.parse_network_nodes("vnode1=http://a:1 role=king")
        with pytest.raises(ValueError):
            nm.parse_network_nodes("vnode1=http://a:1 colour=red")


class TestNetworkReport:
    def _network(self, table, tmp_path=None, network=None):
        path = ""
        if tmp_path is not None:
            path = str(tmp_path / "network.json")
            if network is not None:
                (tmp_path / "network.json").write_text(json.dumps(network))
        return nm.Network(
            NODES, network_file=path, name="alphanet", fetch=_fake_fetch(table)
        )

    def test_agreement_when_every_validator_reports_the_same_ledger(self):
        report = self._network(
            {
                "http://10.0.0.1:8687": _latest("proposing", 500),
                "http://10.0.0.2:8687": _latest("proposing", 500),
                "http://127.0.0.1:8687": _latest("full", 499),
            }
        ).report()
        assert report["agreement"] is True
        assert report["validated_ledger"] == 500
        assert report["name"] == "alphanet"
        assert [n["name"] for n in report["nodes"]] == ["vnode1", "vnode2", "pnode1"]
        vnode1 = report["nodes"][0]
        assert vnode1["role"] == "validator"
        assert vnode1["server_state"] == "proposing"
        assert vnode1["build_version"] == "3.1.0"
        assert vnode1["validated_ledger"] == 500
        assert vnode1["peers"] == 4
        assert vnode1["uptime"] == 3600
        assert vnode1["ok"] is True
        assert vnode1["error"] is None
        assert isinstance(report["generated_at"], int)

    def test_no_agreement_when_validators_differ(self):
        report = self._network(
            {
                "http://10.0.0.1:8687": _latest("proposing", 500),
                "http://10.0.0.2:8687": _latest("proposing", 498),
                "http://127.0.0.1:8687": _latest("full", 500),
            }
        ).report()
        assert report["agreement"] is False

    def test_no_agreement_when_a_validator_is_unreachable(self):
        report = self._network(
            {
                "http://10.0.0.1:8687": _latest("proposing", 500),
                "http://127.0.0.1:8687": _latest("full", 500),
            }
        ).report()
        assert report["agreement"] is False
        vnode2 = report["nodes"][1]
        assert vnode2["ok"] is False
        assert vnode2["server_state"] is None
        assert "refused" in vnode2["error"]

    def test_peer_only_network_has_no_agreement(self):
        net = nm.Network(
            nm.parse_network_nodes("pnode1=http://a:1"),
            fetch=_fake_fetch({"http://a:1": _latest("full")}),
        )
        assert net.report()["agreement"] is False

    def test_network_json_merged(self, tmp_path):
        info = {
            "last_deploy": {"sha": "abc123"},
            "branches": [{"repo": "XRPLF/rippled"}],
        }
        report = self._network({}, tmp_path, info).report()
        assert report["network"] == info

    def test_missing_network_json_is_empty(self, tmp_path):
        assert self._network({}, tmp_path).report()["network"] == {}

    def test_malformed_network_json_is_reported_not_raised(self, tmp_path):
        (tmp_path / "network.json").write_text("{not json")
        report = self._network({}, tmp_path).report()
        assert "network.json" in report["network"]["error"]


class TestNetworkHealth:
    def _health(self, table):
        return nm.Network(NODES, fetch=_fake_fetch(table)).health()

    def test_200_when_validators_propose_and_peers_are_full(self):
        body, code = self._health(
            {
                "http://10.0.0.1:8687": _latest("proposing"),
                "http://10.0.0.2:8687": _latest("proposing"),
                "http://127.0.0.1:8687": _latest("full"),
            }
        )
        assert code == 200
        assert body["ok"] is True
        assert body["failing"] == []

    def test_503_lists_the_failing_nodes(self):
        body, code = self._health(
            {
                "http://10.0.0.1:8687": _latest("proposing"),
                "http://10.0.0.2:8687": _latest("full"),
            }
        )
        assert code == 503
        assert body["ok"] is False
        assert body["failing"] == [
            {"name": "vnode2", "role": "validator", "server_state": "full"},
            {"name": "pnode1", "role": "peer", "server_state": None},
        ]

    def test_a_full_validator_is_not_healthy(self):
        body, code = self._health(
            {
                "http://10.0.0.1:8687": _latest("full"),
                "http://10.0.0.2:8687": _latest("proposing"),
                "http://127.0.0.1:8687": _latest("full"),
            }
        )
        assert code == 503
        assert [f["name"] for f in body["failing"]] == ["vnode1"]


class TestNetworkRoutes:
    @pytest.fixture
    def server(self):
        table = {
            "http://10.0.0.1:8687": _latest("proposing"),
            "http://10.0.0.2:8687": _latest("proposing"),
            "http://127.0.0.1:8687": _latest("full"),
        }
        nm.Handler.network = nm.Network(
            NODES, name="alphanet", fetch=_fake_fetch(table)
        )
        srv = ThreadingHTTPServer(("127.0.0.1", 0), nm.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        yield srv, table
        srv.shutdown()
        nm.Handler.network = None

    def test_network_route(self, server):
        srv, _ = server
        code, body = _get(srv, "/api/network")
        assert code == 200
        assert body["name"] == "alphanet"
        assert body["agreement"] is True

    def test_health_route_flips_to_503(self, server):
        srv, table = server
        assert _get(srv, "/api/network/health")[0] == 200
        table["http://127.0.0.1:8687"] = _latest("syncing")
        code, body = _get(srv, "/api/network/health")
        assert code == 503
        assert body["failing"][0]["name"] == "pnode1"

    def test_network_routes_absent_without_node_list(self, server):
        srv, _ = server
        nm.Handler.network = None
        assert _get(srv, "/api/network")[0] == 404


class TestXdgmListener:
    def test_bind_failure_is_recorded_and_the_listener_exits(
        self, tmp_path, monkeypatch, capsys
    ):
        db = str(tmp_path / "metrics.db")
        conn = nm.connect(db)
        conn.executescript(nm.SCHEMA)
        conn.close()
        cfg = SimpleNamespace(db=db, xdgm_host="203.0.113.7", xdgm_port=9999)

        class FailingSocket:
            def __init__(self, *args):
                pass

            def setsockopt(self, *args):
                pass

            def bind(self, addr):
                raise OSError(49, "Can't assign requested address")

        monkeypatch.setattr(socket, "socket", FailingSocket)
        monkeypatch.setattr(nm, "read_proc_stat", lambda: (0, 0, 0))
        monkeypatch.setattr(nm, "read_diskstats", lambda: (0, 0))
        monkeypatch.setattr(nm, "read_netdev", lambda: (0, 0))

        nm.Sampler(cfg).listen_xdgm()

        rows = nm.connect(db).execute("SELECT kind, detail FROM events").fetchall()
        assert [r["kind"] for r in rows] == ["sampler_error"]
        assert "xdgm bind 203.0.113.7:9999" in rows[0]["detail"]
        assert "Can't assign requested address" in rows[0]["detail"]
        assert "xdgm bind 203.0.113.7:9999" in capsys.readouterr().err

    def test_default_host_is_every_interface(self, monkeypatch):
        monkeypatch.delenv("NODE_METRICS_XDGM_HOST", raising=False)
        monkeypatch.setattr("sys.argv", ["node_metrics.py"])
        assert nm.parse_args().xdgm_host == "0.0.0.0"


class TestDecodeXdgm:
    def test_field_mapping(self):
        data = _xdgm_packet(
            network_id=21337,
            server_state=4,
            peer_count=7,
            size_slot=16,
            ledger_range_count=2,
            uptime=99,
            io_latency_us=1500,
            proposer_count=5,
            converge_time_ms=2100,
            ledger_seq=1234,
            version_string=b"3.1.0-b1",
            load_avg_1min=0.5,
            sle_hit_rate=88,
            node_fetch_size=4096,
        )
        data += struct.pack("<2I", 100, 200) + struct.pack("<2I", 300, 400)
        rec = nm.decode_xdgm(data)
        assert rec["magic"] == nm.XDGM_MAGIC
        assert rec["version"] == 2
        assert rec["network_id"] == 21337
        assert rec["server_state"] == 4
        assert rec["peer_count"] == 7
        assert rec["memory_limit_gb"] == 16
        assert rec["ledger_range_count"] == 2
        assert rec["uptime"] == 99
        assert rec["io_latency_us"] == 1500
        assert rec["proposer_count"] == 5
        assert rec["converge_time_ms"] == 2100
        assert rec["ledger_seq"] == 1234
        assert rec["version_string"] == "3.1.0-b1"
        assert rec["load_avg_1min"] == 0.5
        assert rec["sle_hit_rate"] == 88
        assert rec["node_fetch_size"] == 4096
        assert rec["ledger_ranges"] == [(100, 200), (300, 400)]
        for dropped in (
            "size_slot",
            "padding_1",
            "padding2",
            "ledger_hash",
            "node_public_key",
        ):
            assert dropped not in rec

    def test_v1_size_slot_is_a_node_size_tier_not_a_memory_limit(self):
        assert (
            nm.decode_xdgm(_xdgm_packet(version=1, size_slot=2))["memory_limit_gb"]
            is None
        )
        assert (
            nm.decode_xdgm(_xdgm_packet(version=2, size_slot=2))["memory_limit_gb"] == 2
        )

    def test_short_packet_is_not_a_datagram(self):
        assert nm.decode_xdgm(b"") is None
        assert nm.decode_xdgm(_xdgm_packet()[:-1]) is None

    def test_wrong_magic_is_not_a_datagram(self):
        assert nm.decode_xdgm(_xdgm_packet(magic=0x41424344)) is None

    def test_truncated_ledger_ranges_keep_the_complete_ones(self):
        data = _xdgm_packet(ledger_range_count=3) + struct.pack("<2I", 1, 2) + b"\x03"
        assert nm.decode_xdgm(data)["ledger_ranges"] == [(1, 2)]

    def test_full_width_version_string_has_no_terminator(self):
        rec = nm.decode_xdgm(_xdgm_packet(version_string=b"v" * 32))
        assert rec["version_string"] == "v" * 32


def _server_info(state, build="3.1.0", **info):
    info = {
        "server_state": state,
        "build_version": build,
        "complete_ledgers": "1-100",
        "peers": 4,
        "uptime": 60,
        "validated_ledger": {"seq": 100},
        **info,
    }
    return {"info": info}


def _packet_full():
    return nm.decode_xdgm(
        _xdgm_packet(
            server_state=4,
            version_string=b"3.2.0",
            peer_count=9,
            ledger_seq=555,
            proposer_count=3,
            uptime=61,
        )
    )


class _Stop(BaseException):
    """Ends sampler_loop once the scripted ticks run out."""


class _ScriptedSampler(nm.Sampler):
    """Builds the xrpld part of each row from a script of (rpc_result, packet).

    A tick that is an exception is raised instead of producing a row.
    """

    def __init__(self, cfg, ticks=()):
        super().__init__(cfg)
        self.ticks = list(ticks)
        self.rpc = None
        self.tick = 0
        self.base_ts = int(time.time()) - 600

    def sample(self):
        if not self.ticks:
            raise _Stop
        tick = self.ticks.pop(0)
        self.tick += 1
        if isinstance(tick, BaseException):
            raise tick
        self.rpc, packet = tick
        self.xdgm = packet
        self.xdgm_ts = time.time() if packet else 0.0
        row = {"ts": self.base_ts + self.tick * 10, "xrpld_ok": 1}
        row.update(self.server_info())
        row.update(self.from_xdgm(row))
        self.latest = row
        return row


def _run_loop(tmp_path, monkeypatch, ticks, cls=_ScriptedSampler):
    """Drive sampler_loop through `ticks`; (sample rows, events, sampler)."""
    db = str(tmp_path / "metrics.db")
    cfg = SimpleNamespace(
        db=db,
        interval=0,
        admin_rpc="http://127.0.0.1:5007/",
        retain_raw_hours=48,
        retain_5m_days=30,
        retain_1h_days=365,
    )
    sampler = _sampler(monkeypatch, cfg, cls)
    sampler.ticks = list(ticks)
    monkeypatch.setattr(nm, "admin_rpc", lambda url, command: sampler.rpc)
    with pytest.raises(_Stop):
        nm.sampler_loop(cfg, sampler)
    conn = nm.connect(db)
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT server_state, build_version, proposers, xdgm_ok, initial_sync_s "
            "FROM samples ORDER BY ts"
        )
    ]
    events = []
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'events'").fetchone():
        events = [
            (r["kind"], r["detail"])
            for r in conn.execute("SELECT kind, detail FROM events ORDER BY ts")
        ]
    conn.close()
    return rows, events, sampler


class TestXdgmFallback:
    def _sampler(self, monkeypatch):
        s = _sampler(monkeypatch, SimpleNamespace(interval=10))
        s.xdgm = _packet_full()
        s.xdgm_ts = time.time()
        return s

    def test_packet_fills_the_rpc_fields_when_the_rpc_stops_answering(
        self, monkeypatch
    ):
        s = self._sampler(monkeypatch)
        s.latest = {"server_state": "full", "build_version": "3.1.0"}
        row = {"server_state": "unreachable"}
        row.update(s.from_xdgm(row))
        assert row["server_state"] == "full"
        assert row["build_version"] == "3.2.0"
        assert row["peers"] == 9
        assert row["validated_seq"] == 555
        assert row["proposers"] == 3
        assert row["xdgm_ok"] == 1

    def test_rpc_answer_wins_over_the_packet(self, monkeypatch):
        s = self._sampler(monkeypatch)
        s.latest = {"server_state": "unreachable"}
        row = {"server_state": "proposing", "build_version": "3.1.0", "peers": 4}
        out = s.from_xdgm(row)
        assert out["xdgm_ok"] == 1
        assert out["proposers"] == 3
        for key in ("server_state", "build_version", "peers", "validated_seq"):
            assert key not in out
        row.update(out)
        assert row["server_state"] == "proposing"
        assert row["build_version"] == "3.1.0"
        assert row["peers"] == 4

    def _run(self, tmp_path, monkeypatch, ticks):
        return _run_loop(tmp_path, monkeypatch, ticks)[:2]

    def test_no_state_change_event_when_only_the_rpc_drops(self, tmp_path, monkeypatch):
        rows, events = self._run(
            tmp_path,
            monkeypatch,
            [
                (_server_info("full"), None),
                (None, _packet_full()),
                (_server_info("full"), _packet_full()),
            ],
        )
        assert [r["server_state"] for r in rows] == ["full", "full", "full"]
        assert [r["build_version"] for r in rows] == ["3.1.0", "3.2.0", "3.1.0"]
        assert [r["xdgm_ok"] for r in rows] == [0, 1, 1]
        assert events == []

    def test_rpc_recovery_does_not_bounce_through_the_packet_state(
        self, tmp_path, monkeypatch
    ):
        rows, events = self._run(
            tmp_path,
            monkeypatch,
            [
                (None, None),
                (_server_info("proposing"), _packet_full()),
                (_server_info("proposing"), _packet_full()),
            ],
        )
        assert [r["server_state"] for r in rows] == [
            "unreachable",
            "proposing",
            "proposing",
        ]
        assert [r["build_version"] for r in rows] == [None, "3.1.0", "3.1.0"]
        assert [r["proposers"] for r in rows] == [None, 3, 3]
        assert events == [("server_state", "unreachable -> proposing")]


class TestRollup:
    def _conn(self):
        conn = nm.connect(":memory:")
        conn.executescript(nm.SCHEMA)
        nm.ensure_columns(conn)
        return conn

    def _raw(self, conn, ts, cpu, state):
        nm.insert(
            conn, {"ts": ts, "cpu_pct": cpu, "server_state": state, "xrpld_ok": 1}
        )

    def _rows(self, conn):
        return {r["ts"]: dict(r) for r in conn.execute("SELECT * FROM rollup_5m")}

    def test_completed_buckets_roll_up_once_and_new_ones_are_appended(self):
        conn = self._conn()
        current = int(time.time()) // 300 * 300
        b0 = current - 1200
        self._raw(conn, b0 + 10, 10, "full")
        self._raw(conn, b0 + 200, 20, "syncing")
        self._raw(conn, b0 + 305, 30, "full")
        self._raw(conn, b0 + 610, 40, "full")
        self._raw(conn, b0 + 620, 60, "full")
        self._raw(conn, current + 1, 99, "full")

        nm.rollup(conn, "samples", "rollup_5m", 300)
        first = self._rows(conn)
        assert sorted(first) == [b0, b0 + 300, b0 + 600]
        assert first[b0]["cpu_pct"] == 15
        assert first[b0]["server_state"] == "syncing"
        assert first[b0 + 300]["cpu_pct"] == 30
        assert first[b0 + 600]["cpu_pct"] == 50
        assert first[b0 + 600]["server_state"] == "full"

        self._raw(conn, b0 + 250, 1000, "disconnected")
        nm.rollup(conn, "samples", "rollup_5m", 300)
        assert self._rows(conn) == first

        self._raw(conn, b0 + 905, 70, "full")
        nm.rollup(conn, "samples", "rollup_5m", 300)
        third = self._rows(conn)
        assert sorted(third) == [b0, b0 + 300, b0 + 600, b0 + 900]
        assert {ts: third[ts] for ts in first} == first
        assert third[b0 + 900]["cpu_pct"] == 70

    def test_empty_source_leaves_the_destination_empty(self):
        conn = self._conn()
        nm.rollup(conn, "samples", "rollup_5m", 300)
        assert self._rows(conn) == {}


class TestNodeRoutes:
    @pytest.fixture
    def server(self, tmp_path):
        db = str(tmp_path / "metrics.db")
        conn = nm.connect(db)
        conn.executescript(nm.SCHEMA)
        nm.ensure_columns(conn)
        now = int(time.time())
        for i in range(3):
            nm.insert(
                conn,
                {
                    "ts": now - 30 + i * 10,
                    "cpu_pct": 10 * i,
                    "server_state": "full",
                    "xrpld_ok": 1,
                },
            )
        for i, kind in enumerate(["server_state", "sampler_error", "initial_sync"]):
            conn.execute(
                "INSERT INTO events (ts, kind, detail) VALUES (?,?,?)",
                (now - 100 + i, kind, f"detail {i}"),
            )
        conn.commit()
        conn.close()
        saved = (nm.Handler.cfg, nm.Handler.sampler)
        nm.Handler.cfg = SimpleNamespace(
            db=db,
            retain_raw_hours=48,
            retain_5m_days=30,
            retain_1h_days=365,
            dashboard=str(tmp_path / "missing.html"),
        )
        nm.Handler.sampler = SimpleNamespace(latest=_latest("full", ts=now))
        srv = ThreadingHTTPServer(("127.0.0.1", 0), nm.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        yield srv
        srv.shutdown()
        nm.Handler.cfg, nm.Handler.sampler = saved

    def test_latest(self, server):
        code, body = _get(server, "/api/latest")
        assert code == 200
        assert body == nm.Handler.sampler.latest

    def test_series_default_window_reads_raw_samples(self, server):
        code, body = _get(server, "/api/series")
        assert code == 200
        assert body["table"] == "samples"
        assert body["window"] == 3600
        assert [p["cpu_pct"] for p in body["points"]] == [0, 10, 20]

    def test_series_window_is_clamped_and_picks_the_table(self, server):
        code, body = _get(server, "/api/series?window=1")
        assert code == 200
        assert body["window"] == 60
        code, body = _get(server, "/api/series?window=99999999999")
        assert code == 200
        assert body["window"] == 365 * 86400
        assert body["table"] == "rollup_1h"
        assert body["points"] == []

    def test_series_rejects_a_non_numeric_window(self, server):
        code, body = _get(server, "/api/series?window=soon")
        assert code == 500
        assert "error" in body

    def test_events_newest_first(self, server):
        code, body = _get(server, "/api/events")
        assert code == 200
        assert [e["kind"] for e in body["events"]] == [
            "initial_sync",
            "sampler_error",
            "server_state",
        ]
        assert body["events"][0]["detail"] == "detail 2"

    def test_events_limit_is_bounded_on_both_sides(self, server):
        assert len(_get(server, "/api/events?limit=2")[1]["events"]) == 2
        assert len(_get(server, "/api/events?limit=-1")[1]["events"]) == 1
        assert len(_get(server, "/api/events?limit=0")[1]["events"]) == 1
        assert len(_get(server, "/api/events?limit=5000")[1]["events"]) == 3

    def test_health_follows_the_latest_row(self, server):
        code, body = _get(server, "/api/health")
        assert code == 200
        assert body["ok"] is True
        assert body["state"] == "full"
        nm.Handler.sampler.latest = _latest("syncing")
        code, body = _get(server, "/api/health")
        assert code == 503
        assert body["ok"] is False
        assert body["state"] == "syncing"
        nm.Handler.sampler.latest = _latest("full", ok=0)
        assert _get(server, "/api/health")[0] == 503

    def test_unknown_route_and_missing_dashboard_are_404(self, server):
        assert _get(server, "/api/nothing")[0] == 404
        code, body = _get(server, "/")
        assert code == 404
        assert body["error"] == "dashboard not installed"

    def test_dashboard_is_served_as_html(self, server, tmp_path):
        page = b"<!doctype html><title>node</title><p>${name}</p>"
        (tmp_path / "dashboard.html").write_bytes(page)
        nm.Handler.cfg.dashboard = str(tmp_path / "dashboard.html")
        for route in ("/", "/index.html"):
            code, content_type, body = _get_raw(server, route)
            assert code == 200
            assert content_type == "text/html; charset=utf-8"
            assert body == page

    def test_series_window_past_the_raw_retention_reads_the_5m_rollup(self, server):
        code, body = _get(server, "/api/series?window=176400")
        assert code == 200
        assert body["window"] == 176400
        assert body["table"] == "rollup_5m"
        assert body["points"] == []

    def test_fetch_latest_reads_a_node_over_http(self, server):
        assert nm.fetch_latest(_url(server)) == nm.Handler.sampler.latest
        with pytest.raises(urllib.error.URLError):
            nm.fetch_latest(f"http://127.0.0.1:{_closed_port()}", timeout=1)


class TestProcReaders:
    @pytest.fixture
    def proc(self, tmp_path, monkeypatch):
        root = tmp_path / "proc"
        monkeypatch.setattr(nm, "PROC", str(root))
        return root

    def test_proc_stat_totals_the_aggregate_cpu_row(self, proc):
        _write_proc(proc)
        assert nm.read_proc_stat() == (9580, 8200, 200)

    def test_meminfo_is_bytes_per_key(self, proc):
        _write_proc(proc)
        mem = nm.read_meminfo()
        assert mem["MemTotal"] == 16384000 * 1024
        assert mem["MemAvailable"] == 8192000 * 1024
        assert mem["SwapTotal"] == 2097152 * 1024
        assert mem["SwapFree"] == 1048576 * 1024
        assert mem["Buffers"] == 256000 * 1024

    def test_diskstats_counts_whole_disks_only(self, proc):
        _write_proc(proc, sda=(20000, 8000))
        assert nm.read_diskstats() == (20000 * 512, 8000 * 512)

    def test_netdev_sums_every_interface_but_loopback(self, proc):
        _write_proc(proc, eth0=(100000, 40000))
        assert nm.read_netdev() == (100000 + 20000, 40000 + 10000)

    def test_find_pid_matches_argv0(self, proc):
        _write_proc(
            proc,
            procs=[
                (1, b"/sbin/init\x00splash\x00", "init", None),
                (2, b"", "kthreadd", None),
                (3, None, None, None),
                XRPLD_PROC,
            ],
        )
        assert nm.find_pid("xrpld") == 4242

    def test_find_pid_falls_back_to_comm(self, proc):
        _write_proc(
            proc,
            procs=[
                (1, b"/sbin/init\x00", "init", None),
                (77, b"/usr/bin/python3\x00/opt/run.py\x00", "xrpld-main", None),
            ],
        )
        assert nm.find_pid("xrpld") == 77

    def test_find_pid_is_none_without_a_match(self, proc):
        _write_proc(
            proc,
            procs=[
                (1, b"/sbin/init\x00", "init", None),
                (2, b"", "kthreadd", None),
                (3, None, None, None),
            ],
        )
        assert nm.find_pid("xrpld") is None

    def test_rss_is_vmrss_in_bytes(self, proc):
        _write_proc(proc, procs=[XRPLD_PROC])
        assert nm.read_rss(4242) == 2048000 * 1024

    def test_rss_is_none_without_a_status_or_a_vmrss_line(self, proc):
        _write_proc(proc, procs=[(9, b"", "kthreadd", None)])
        (proc / "9" / "status").write_text("Name:\tkthreadd\nState:\tS (sleeping)\n")
        assert nm.read_rss(9) is None
        assert nm.read_rss(4242) is None


class TestProbes:
    def test_port_open(self, rpc):
        assert nm.port_open(rpc.port) is True
        assert nm.port_open(_closed_port()) is False

    def test_http_ok_needs_a_2xx(self, rpc):
        assert nm.http_ok(rpc.url + "health") is True
        assert nm.http_ok(rpc.url + "nope") is False
        assert nm.http_ok(f"http://127.0.0.1:{_closed_port()}/health") is False
        assert nm.http_ok("not a url") is False

    def test_admin_rpc_posts_the_command_and_returns_result(self, rpc):
        rpc.result = _server_info("full")
        assert nm.admin_rpc(rpc.url, "server_info") == _server_info("full")
        assert rpc.calls == [
            ("application/json", {"method": "server_info", "params": [{}]})
        ]

    def test_admin_rpc_without_a_result_is_empty(self, rpc):
        rpc.body = b'{"status": "error"}'
        assert nm.admin_rpc(rpc.url, "server_info") == {}

    def test_admin_rpc_is_none_on_bad_json_or_no_listener(self, rpc):
        rpc.body = b"<html>502 Bad Gateway</html>"
        assert nm.admin_rpc(rpc.url, "server_info") is None
        assert nm.admin_rpc(f"http://127.0.0.1:{_closed_port()}/", "peers") is None


class TestSample:
    def _cfg(self, tmp_path, **over):
        cfg = SimpleNamespace(
            interval=10,
            disk_path=str(tmp_path),
            process="xrpld",
            admin_rpc=f"http://127.0.0.1:{_closed_port()}/",
            debugstream_health="",
            redis_port=0,
        )
        cfg.__dict__.update(over)
        return cfg

    def test_row_from_a_running_node(self, tmp_path, monkeypatch, rpc):
        tree = tmp_path / "proc"
        _write_proc(tree, procs=[XRPLD_PROC])
        monkeypatch.setattr(nm, "PROC", str(tree))
        rpc.result = _server_info("full", load_factor=1, io_latency_ms=3)
        s = nm.Sampler(
            self._cfg(
                tmp_path,
                admin_rpc=rpc.url,
                debugstream_health=rpc.url + "health",
                redis_port=rpc.port,
            )
        )
        assert s.prev_cpu == (9580, 8200, 200)
        assert s.prev_disk == (20000 * 512, 8000 * 512)
        assert s.prev_net == (120000, 50000)

        _write_proc(
            tree,
            cpu=(1400, 50, 400, 8500, 300, 10, 20, 0, 0, 0),
            sda=(20200, 8100),
            eth0=(160000, 70000),
            procs=[XRPLD_PROC],
        )
        s.prev_t = time.time() - 10
        row = s.sample()

        st = os.statvfs(str(tmp_path))
        assert row["ts"] == pytest.approx(time.time(), abs=2)
        assert row["cpu_pct"] == 45.45
        assert row["cpu_iowait_pct"] == 9.09
        assert (row["load1"], row["load5"], row["load15"]) == (0.52, 0.48, 0.40)
        assert row["mem_total"] == 16384000 * 1024
        assert row["mem_avail"] == 8192000 * 1024
        assert row["mem_used"] == (16384000 - 8192000) * 1024
        assert row["swap_total"] == 2097152 * 1024
        assert row["swap_used"] == (2097152 - 1048576) * 1024
        assert row["xrpld_rss"] == 2048000 * 1024
        assert row["disk_total"] == st.f_blocks * st.f_frsize
        assert row["disk_used"] == (st.f_blocks - st.f_bfree) * st.f_frsize
        assert row["disk_read_bps"] == pytest.approx(200 * 512 / 10, rel=1e-3)
        assert row["disk_write_bps"] == pytest.approx(100 * 512 / 10, rel=1e-3)
        assert row["net_rx_bps"] == pytest.approx(60000 / 10, rel=1e-3)
        assert row["net_tx_bps"] == pytest.approx(30000 / 10, rel=1e-3)
        assert row["xrpld_ok"] == 1
        assert row["debugstream_ok"] == 1
        assert row["redis_ok"] == 1
        assert row["server_state"] == "full"
        assert row["build_version"] == "3.1.0"
        assert row["validated_seq"] == 100
        assert row["ledger_span"] == 99
        assert row["load_factor"] == 1
        assert row["io_latency_ms"] == 3
        assert row["xdgm_ok"] == 0
        assert s.latest is row
        assert s.prev_cpu == (10680, 8800, 300)
        assert s.prev_disk == (20200 * 512, 8100 * 512)
        assert s.prev_net == (180000, 80000)
        assert rpc.calls == [
            ("application/json", {"method": "server_info", "params": [{}]})
        ]

    def test_row_without_the_process_or_sidecars(self, tmp_path, monkeypatch):
        tree = tmp_path / "proc"
        _write_proc(tree)
        monkeypatch.setattr(nm, "PROC", str(tree))
        s = nm.Sampler(self._cfg(tmp_path))
        row = s.sample()
        assert row["cpu_pct"] == 0.0
        assert row["cpu_iowait_pct"] == 0.0
        assert row["disk_read_bps"] == 0
        assert row["disk_write_bps"] == 0
        assert row["net_rx_bps"] == 0
        assert row["net_tx_bps"] == 0
        assert row["xrpld_rss"] is None
        assert row["xrpld_ok"] == 0
        assert row["debugstream_ok"] is None
        assert row["redis_ok"] is None
        assert row["server_state"] == "unreachable"
        assert row["xdgm_ok"] == 0
        assert "build_version" not in row


class TestXdgmListenerLoop:
    def test_keeps_the_newest_packet_and_waits_out_socket_errors(self, monkeypatch):
        script = [
            (_xdgm_packet(ledger_seq=77, server_state=4), ("10.0.0.1", 40000)),
            (b"not a datagram", ("10.0.0.1", 40000)),
            OSError(11, "Resource temporarily unavailable"),
            _Stop(),
        ]
        bound = []

        class ScriptedSocket:
            def __init__(self, *args):
                pass

            def setsockopt(self, *args):
                pass

            def bind(self, addr):
                bound.append(addr)

            def recvfrom(self, size):
                item = script.pop(0)
                if isinstance(item, BaseException):
                    raise item
                return item

        sleeps = []
        monkeypatch.setattr(socket, "socket", ScriptedSocket)
        monkeypatch.setattr(nm.time, "sleep", sleeps.append)
        s = _sampler(
            monkeypatch,
            SimpleNamespace(interval=10, xdgm_host="0.0.0.0", xdgm_port=9999),
        )
        with pytest.raises(_Stop):
            s.listen_xdgm()
        assert bound == [("0.0.0.0", 9999)]
        assert s.xdgm["ledger_seq"] == 77
        assert s.xdgm["server_state"] == 4
        assert s.xdgm_ts == pytest.approx(time.time(), abs=2)
        assert sleeps == [1]
        assert script == []


class TestEnsureColumns:
    def test_columns_from_an_earlier_schema_are_added(self):
        conn = nm.connect(":memory:")
        conn.executescript(
            "CREATE TABLE samples (ts INTEGER PRIMARY KEY, cpu_pct REAL);"
            "CREATE TABLE rollup_5m (ts INTEGER PRIMARY KEY, cpu_pct REAL);"
            "CREATE TABLE rollup_1h (ts INTEGER PRIMARY KEY, cpu_pct REAL);"
            "CREATE TABLE events (ts INTEGER, kind TEXT, detail TEXT);"
        )
        nm.ensure_columns(conn)
        for table in ("samples", "rollup_5m", "rollup_1h"):
            kinds = {
                r["name"]: r["type"]
                for r in conn.execute(f"PRAGMA table_info({table})")
            }
            assert set(kinds) == {"ts", *nm.NUMERIC, *nm.LAST}
            assert kinds["xdgm_age"] == "REAL"
            assert kinds["build_version"] == "TEXT"
            assert kinds["xdgm_ok"] == "INTEGER"
        nm.insert(conn, {"ts": 1, "xdgm_age": 0.5, "server_state": "full"})
        row = conn.execute("SELECT xdgm_age, server_state FROM samples").fetchone()
        assert tuple(row) == (0.5, "full")


class TestSamplerLoop:
    def test_initial_sync_is_recorded_once_per_value(self, tmp_path, monkeypatch):
        synced = _server_info("full", initial_sync_duration_us=90_000_000)
        restarted = _server_info("full", initial_sync_duration_us=120_000_000)
        rows, events, _ = _run_loop(
            tmp_path,
            monkeypatch,
            [(synced, None), (synced, None), (restarted, None)],
        )
        assert [r["initial_sync_s"] for r in rows] == [90.0, 90.0, 120.0]
        assert events == [
            ("initial_sync", "reached full 90s after start (1.5 min)"),
            ("initial_sync", "reached full 120s after start (2.0 min)"),
        ]

    def test_a_failing_sample_is_recorded_and_the_loop_goes_on(
        self, tmp_path, monkeypatch
    ):
        rows, events, sampler = _run_loop(
            tmp_path,
            monkeypatch,
            [
                RuntimeError("statvfs: No such file or directory"),
                (_server_info("full"), None),
            ],
        )
        assert [r["server_state"] for r in rows] == ["full"]
        assert events == [("sampler_error", "statvfs: No such file or directory")]
        assert sampler.tick == 2

    def test_the_loop_survives_losing_the_events_table(self, tmp_path, monkeypatch):
        class DropsEvents(_ScriptedSampler):
            def sample(self):
                if self.tick == 0:
                    self.tick += 1
                    other = nm.connect(self.cfg.db)
                    other.execute("DROP TABLE events")
                    other.close()
                    raise RuntimeError("boom")
                raise _Stop

        rows, events, sampler = _run_loop(tmp_path, monkeypatch, [], DropsEvents)
        assert rows == []
        assert events == []
        assert sampler.tick == 1


class TestMain:
    @pytest.fixture
    def wiring(self, tmp_path, monkeypatch):
        """main() with the threads and the HTTP server replaced by recorders."""
        monkeypatch.setattr(nm, "PROC", _write_proc(tmp_path / "proc"))
        threads, servers = [], []

        class Thread:
            def __init__(self, target, args=(), daemon=False):
                self.target, self.args, self.daemon = target, args, daemon

            def start(self):
                threads.append(self)

        class Server:
            def __init__(self, address, handler):
                servers.append((address, handler))

            def serve_forever(self):
                pass

        monkeypatch.setattr(nm, "threading", SimpleNamespace(Thread=Thread))
        monkeypatch.setattr(nm, "ThreadingHTTPServer", Server)
        saved = (nm.Handler.cfg, nm.Handler.sampler, nm.Handler.network)
        yield SimpleNamespace(threads=threads, servers=servers)
        nm.Handler.cfg, nm.Handler.sampler, nm.Handler.network = saved

    def _argv(self, tmp_path, **over):
        args = {
            "db": str(tmp_path / "state" / "metrics.db"),
            "http-host": "127.0.0.1",
            "http-port": "18687",
            "interval": "5",
            "dashboard": str(tmp_path / "dashboard.html"),
            "admin-rpc": "http://127.0.0.1:5007/",
            "debugstream-health": "",
            "redis-port": "0",
            "disk-path": str(tmp_path),
            "process": "xrpld",
            "xdgm-host": "127.0.0.1",
            "xdgm-port": "9998",
            "retain-raw-hours": "24",
            "retain-5m-days": "7",
            "retain-1h-days": "90",
            "network-nodes": "vnode1=http://10.0.0.1:8687,pnode1=http://10.0.0.2:8687",
            "network-file": str(tmp_path / "network.json"),
            "network-name": "alphanet",
        }
        args.update(over)
        argv = ["node_metrics.py"]
        for key, value in args.items():
            argv += [f"--{key}", value]
        return argv

    def test_main_builds_the_config_and_starts_every_part(
        self, tmp_path, monkeypatch, wiring
    ):
        monkeypatch.setattr("sys.argv", self._argv(tmp_path))
        nm.main()

        cfg = nm.Handler.cfg
        assert cfg.db == str(tmp_path / "state" / "metrics.db")
        assert cfg.interval == 5.0
        assert cfg.redis_port == 0
        assert cfg.debugstream_health == ""
        assert (cfg.retain_raw_hours, cfg.retain_5m_days, cfg.retain_1h_days) == (
            24,
            7,
            90,
        )
        conn = nm.connect(cfg.db)
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
        conn.close()
        assert {"samples", "rollup_5m", "rollup_1h", "events"} <= tables

        sampler = nm.Handler.sampler
        assert isinstance(sampler, nm.Sampler)
        assert sampler.cfg is cfg
        assert sampler.prev_cpu == (9580, 8200, 200)

        assert [(t.target, t.args, t.daemon) for t in wiring.threads] == [
            (nm.sampler_loop, (cfg, sampler), True),
            (sampler.listen_xdgm, (), True),
        ]
        assert wiring.servers == [(("127.0.0.1", 18687), nm.Handler)]

        network = nm.Handler.network
        assert network.nodes == [
            ("vnode1", "validator", "http://10.0.0.1:8687"),
            ("pnode1", "peer", "http://10.0.0.2:8687"),
        ]
        assert network.name == "alphanet"
        assert network.network_file == str(tmp_path / "network.json")

    def test_xdgm_port_0_and_no_node_list_start_neither(
        self, tmp_path, monkeypatch, wiring
    ):
        monkeypatch.setattr(
            "sys.argv", self._argv(tmp_path, **{"xdgm-port": "0", "network-nodes": ""})
        )
        nm.main()
        assert [t.target for t in wiring.threads] == [nm.sampler_loop]
        assert nm.Handler.network is None
        assert nm.Handler.cfg.xdgm_port == 0
