"""Tests for xrpld_lab.health — consensus polling with an injected fetch and clock.

check_consensus is driven by three injected callables: ``fetch`` returns the
``(server_state, validated_seq)`` pair a node would report, ``clock`` is the
monotonic time, and ``sleep`` advances that clock. No network, no real sleeping.
"""

import io
import json
import urllib.error
from unittest.mock import patch

from xrpld_lab import health
from xrpld_lab.health import _server_state, check_consensus
from xrpld_lab.services.status import node_metrics


class FakeClock:
    """Monotonic clock that only moves when ``sleep`` is called."""

    def __init__(self):
        self.now = 0.0
        self.sleeps: list = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeNodes:
    """Scripted ``fetch``: one reply list per URL, consumed one poll at a time.

    A reply is a ``(state, seq)`` tuple or an exception instance to raise. The
    last reply repeats once the list runs out.
    """

    def __init__(self, replies: dict):
        self.replies = {url: list(r) for url, r in replies.items()}
        self.calls: list = []

    def __call__(self, url: str, timeout: float):
        self.calls.append((url, timeout))
        script = self.replies[url]
        reply = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


V1 = "http://10.0.0.1:5107"
V2 = "http://10.0.0.2:5207"


def _run(nodes: FakeNodes, vips, timeout_s=60, interval_s=10):
    clock = FakeClock()
    ok = check_consensus(
        vips,
        timeout_s=timeout_s,
        interval_s=interval_s,
        fetch=nodes,
        clock=clock,
        sleep=clock.sleep,
    )
    return ok, clock


class TestCheckConsensus:
    def test_healthy_rule_is_the_status_samplers(self):
        # One definition: health and /api/network/health agree on what healthy is.
        assert health.HEALTHY_STATE is node_metrics.HEALTHY_STATE
        assert health.HEALTHY_STATE["validator"] == "proposing"

    def test_polls_each_validator_on_its_own_public_rpc_port(self):
        # vnode i listens on 5007 + i * 100 (PortSet.for_node VALIDATOR offset).
        nodes = FakeNodes({V1: [("proposing", 5)], V2: [("proposing", 5)]})

        _run(nodes, ["10.0.0.1", "10.0.0.2"], timeout_s=10, interval_s=10)

        assert [url for url, _ in nodes.calls[:2]] == [V1, V2]
        assert {t for _, t in nodes.calls} == {5.0}

    def test_all_healthy_in_the_same_round_returns_true(self, capsys):
        nodes = FakeNodes(
            {
                V1: [("proposing", 5), ("proposing", 6)],
                V2: [("proposing", 7), ("proposing", 8)],
            }
        )

        ok, clock = _run(nodes, ["10.0.0.1", "10.0.0.2"])

        assert ok is True
        assert clock.sleeps == [10]
        out = capsys.readouterr().out
        assert "all 2 validators in consensus, ledgers advancing." in out
        assert "0/2 healthy — retrying in 10s" in out

    def test_healthy_state_without_an_advancing_ledger_keeps_waiting(self, capsys):
        # A validator counts only once a later poll's seq exceeds the first one seen.
        nodes = FakeNodes(
            {
                V1: [("proposing", 5), ("proposing", 5), ("proposing", 6)],
                V2: [("proposing", 5), ("proposing", 6), ("proposing", 7)],
            }
        )

        ok, clock = _run(nodes, ["10.0.0.1", "10.0.0.2"])

        assert ok is True
        assert clock.sleeps == [10, 10]
        lines = capsys.readouterr().out.splitlines()
        v1 = [ln for ln in lines if ln.startswith("  vnode1 state=")]
        assert "seq=5 (waiting)" in v1[0]
        assert "seq=5 (waiting)" in v1[1]
        assert "seq=6 (advanced)" in v1[2] and "OK" in v1[2]
        assert "  1/2 healthy — retrying in 10s" in lines

    def test_advanced_ledger_in_an_unhealthy_state_is_not_ok(self, capsys):
        nodes = FakeNodes({V1: [("connected", 5), ("connected", 6)]})

        ok, _ = _run(nodes, ["10.0.0.1"], timeout_s=15)

        assert ok is False
        out = capsys.readouterr().out
        assert "seq=6 (advanced) \x1b[35mconnected" in out

    def test_a_full_validator_with_an_advancing_ledger_is_not_ok(self, capsys):
        # full means the validator only follows the ledger; it is not proposing.
        nodes = FakeNodes(
            {
                V1: [("full", 5), ("full", 6), ("full", 7)],
                V2: [("proposing", 5), ("proposing", 6), ("proposing", 7)],
            }
        )

        ok, clock = _run(nodes, ["10.0.0.1", "10.0.0.2"], timeout_s=25)

        assert ok is False
        assert clock.sleeps == [10, 10, 10]
        out = capsys.readouterr().out
        assert "vnode1 state=full seq=6 (advanced) \x1b[35mfull" in out
        assert "vnode2 state=proposing seq=6 (advanced) \x1b[32mOK" in out
        assert "1/2 healthy — retrying in 10s" in out
        assert "[health] timed out after 25s waiting for consensus." in out

    def test_unreachable_validator_is_skipped_until_it_answers(self, capsys):
        nodes = FakeNodes(
            {
                V1: [
                    urllib.error.URLError("connection refused"),
                    ("proposing", 9),
                    ("proposing", 10),
                ],
                V2: [("proposing", 5), ("proposing", 6), ("proposing", 7)],
            }
        )

        ok, clock = _run(nodes, ["10.0.0.1", "10.0.0.2"])

        assert ok is True
        assert clock.sleeps == [10, 10]
        out = capsys.readouterr().out
        assert f"vnode1 {V1} — unreachable (<urlopen error connection refused>)" in out
        # The first seq for vnode1 is 9 (its first successful poll), so round 2
        # is still waiting for it while vnode2 has already advanced.
        assert "vnode1 state=proposing seq=9 (waiting)" in out
        assert "1/2 healthy — retrying in 10s" in out

    def test_never_advancing_times_out_and_returns_false(self, capsys):
        nodes = FakeNodes({V1: [("proposing", 5)], V2: [("proposing", 5)]})

        ok, clock = _run(nodes, ["10.0.0.1", "10.0.0.2"], timeout_s=25, interval_s=10)

        # Rounds at t=0, 10 and 20; the deadline at 25 stops the fourth.
        assert ok is False
        assert clock.sleeps == [10, 10, 10]
        assert len(nodes.calls) == 6
        out = capsys.readouterr().out
        assert "[health] timed out after 25s waiting for consensus." in out
        assert "all 2 validators in consensus" not in out

    def test_unreachable_forever_times_out(self, capsys):
        nodes = FakeNodes({V1: [OSError("no route to host")]})

        ok, _ = _run(nodes, ["10.0.0.1"], timeout_s=20)

        assert ok is False
        out = capsys.readouterr().out
        assert out.count("unreachable (no route to host)") == 2
        assert "0/1 healthy" in out


class TestServerState:
    """The default fetch: POST server_info and read state and validated seq."""

    @staticmethod
    def _urlopen(info: dict):
        body = json.dumps({"result": {"info": info}}).encode()

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.close()

        return lambda req, timeout: Resp(body)

    def test_posts_server_info_and_parses_the_reply(self):
        captured = {}

        def urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data)
            captured["timeout"] = timeout
            return self._urlopen(
                {"server_state": "proposing", "validated_ledger": {"seq": 42}}
            )(req, timeout)

        with patch("xrpld_lab.health.urllib.request.urlopen", urlopen):
            assert _server_state(V1, 5.0) == ("proposing", 42)

        assert captured == {
            "url": V1,
            "body": {"method": "server_info", "params": [{}]},
            "timeout": 5.0,
        }

    def test_missing_validated_ledger_reads_as_seq_zero(self):
        with patch(
            "xrpld_lab.health.urllib.request.urlopen",
            self._urlopen({"server_state": "connected"}),
        ):
            assert _server_state(V1, 5.0) == ("connected", 0)
