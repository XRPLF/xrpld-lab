#!/usr/bin/env python
# coding: utf-8

import json
import socket
import struct
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

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


def _get(srv, path):
    url = f"http://127.0.0.1:{srv.server_address[1]}{path}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


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


def _server_info(state, build="3.1.0"):
    return {
        "info": {
            "server_state": state,
            "build_version": build,
            "complete_ledgers": "1-100",
            "peers": 4,
            "uptime": 60,
            "validated_ledger": {"seq": 100},
        }
    }


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
    """Builds the xrpld part of each row from a script of (rpc_result, packet)."""

    def __init__(self, cfg, ticks=()):
        super().__init__(cfg)
        self.ticks = list(ticks)
        self.rpc = None
        self.tick = 0
        self.base_ts = int(time.time()) - 600

    def sample(self):
        if not self.ticks:
            raise _Stop
        self.rpc, packet = self.ticks.pop(0)
        self.xdgm = packet
        self.xdgm_ts = time.time() if packet else 0.0
        self.tick += 1
        row = {"ts": self.base_ts + self.tick * 10, "xrpld_ok": 1}
        row.update(self.server_info())
        row.update(self.from_xdgm(row))
        self.latest = row
        return row


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
        db = str(tmp_path / "metrics.db")
        cfg = SimpleNamespace(
            db=db,
            interval=0,
            admin_rpc="http://127.0.0.1:5007/",
            retain_raw_hours=48,
            retain_5m_days=30,
            retain_1h_days=365,
        )
        sampler = _sampler(monkeypatch, cfg, _ScriptedSampler)
        sampler.ticks = list(ticks)
        monkeypatch.setattr(nm, "admin_rpc", lambda url, command: sampler.rpc)
        with pytest.raises(_Stop):
            nm.sampler_loop(cfg, sampler)
        conn = nm.connect(db)
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT server_state, build_version, proposers, xdgm_ok "
                "FROM samples ORDER BY ts"
            )
        ]
        events = [
            (r["kind"], r["detail"])
            for r in conn.execute("SELECT kind, detail FROM events ORDER BY ts")
        ]
        conn.close()
        return rows, events

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
