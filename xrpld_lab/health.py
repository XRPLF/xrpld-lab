"""Network health check — poll validators until they reach consensus.

After ansible starts the nodes, we confirm the network actually converged:
each validator's public RPC ``server_info`` must report ``server_state``
``proposing`` and an advancing validated ledger. A validator in ``full`` only
follows the ledger (outside the UNL, or with the wrong validation key), so
``full`` is not enough. The rule is the status sampler's ``HEALTHY_STATE`` so
``health`` and ``/api/network/health`` agree. Uses the stdlib only (urllib) so
it carries no new deps.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Callable, List

from xrpld_lab.models import NodeRole, PortSet
from xrpld_lab.services.status.node_metrics import HEALTHY_STATE
from xrpld_lab.utils import bcolors


def _server_state(url: str, timeout: float) -> tuple[str | None, int]:
    """Return (server_state, validated_seq) from a node's RPC, or (None, 0)."""
    payload = json.dumps({"method": "server_info", "params": [{}]}).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        info = json.loads(resp.read()).get("result", {}).get("info", {})
    seq = info.get("validated_ledger", {}).get("seq", 0)
    return info.get("server_state"), int(seq or 0)


def check_consensus(
    vips: List[str],
    timeout_s: int = 300,
    interval_s: int = 10,
    rpc_timeout_s: float = 5.0,
    *,
    fetch: Callable[[str, float], tuple[str | None, int]] = _server_state,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Poll every validator until all are proposing AND a ledger has advanced.

    Args:
        vips: validator external IPs, in node order (node i → PortSet i).
        timeout_s: overall deadline.
        interval_s: seconds between polling rounds.
        fetch: ``(url, rpc_timeout_s) -> (server_state, validated_seq)``.
        clock: monotonic seconds, read for the deadline.
        sleep: waits between polling rounds.

    Returns True once the whole set is healthy; False on timeout.
    """
    endpoints = [
        (i + 1, f"http://{ip}:{PortSet.for_node(i + 1, NodeRole.VALIDATOR).rpc_public}")
        for i, ip in enumerate(vips)
    ]
    deadline = clock() + timeout_s
    first_seq: dict[int, int] = {}

    print(
        f"{bcolors.CYAN}[health] waiting for consensus across "
        f"{len(endpoints)} validators…{bcolors.END}"
    )

    while clock() < deadline:
        healthy = 0
        for node_id, url in endpoints:
            try:
                state, seq = fetch(url, rpc_timeout_s)
            except (urllib.error.URLError, OSError, ValueError) as e:
                print(f"  vnode{node_id} {url} — unreachable ({e})")
                continue

            first_seq.setdefault(node_id, seq)
            advanced = seq > first_seq[node_id]
            ok = state == HEALTHY_STATE["validator"] and advanced
            mark = (bcolors.GREEN + "OK") if ok else (bcolors.PURPLE + str(state))
            print(
                f"  vnode{node_id} state={state} seq={seq} "
                f"{'(advanced)' if advanced else '(waiting)'} {mark}{bcolors.END}"
            )
            if ok:
                healthy += 1

        if healthy == len(endpoints):
            print(
                f"{bcolors.GREEN}[health] all {healthy} validators in consensus, "
                f"ledgers advancing.{bcolors.END}"
            )
            return True

        print(f"  {healthy}/{len(endpoints)} healthy — retrying in {interval_s}s")
        sleep(interval_s)

    print(
        f"{bcolors.RED}[health] timed out after {timeout_s}s waiting for "
        f"consensus.{bcolors.END}"
    )
    return False
