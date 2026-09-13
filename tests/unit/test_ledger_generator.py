"""Tests for xrpld_lab.ledger_generator — keylets, prefunded state, XRP conservation.

The account keylet is checked against the genesis root AccountRoot shipped in
``xrpld_lab/genesis.xrpl.json``, whose ``index`` the node itself computed.
"""

import copy
import json
import os

import pytest

from xrpld_lab.ledger_generator import (
    _main,
    account_index,
    currency_to_bytes,
    generate,
    make_wallet,
    merge_into_genesis,
    ripple_state_index,
)

GENESIS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "xrpld_lab", "genesis.xrpl.json"
)
ROOT_ACCOUNT = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"
ROOT_INDEX = "2B6AC232AA4C4BE41BF49D2459FA4A0347E1B543A4C92FCEE0821C0201E2E9A8"
TOTAL_COINS = 100000000000000000


def _load_genesis() -> dict:
    with open(GENESIS_PATH) as f:
        return json.load(f)


def _account_roots(genesis: dict) -> list:
    return [
        e
        for e in genesis["ledger"]["accountState"]
        if e.get("LedgerEntryType") == "AccountRoot"
    ]


class TestKeylets:
    def test_account_index_matches_the_genesis_root(self):
        genesis = _load_genesis()
        root = [e for e in _account_roots(genesis) if e["Account"] == ROOT_ACCOUNT]

        assert len(root) == 1
        assert root[0]["index"] == ROOT_INDEX
        assert account_index(ROOT_ACCOUNT) == ROOT_INDEX

    def test_ripple_state_index_is_order_independent(self):
        a = make_wallet(0).classic_address
        b = make_wallet(1).classic_address

        assert ripple_state_index(a, b, "USD") == ripple_state_index(b, a, "USD")
        assert ripple_state_index(a, b, "USD") != ripple_state_index(a, b, "EUR")

    def test_iso_currency_is_padded_into_20_bytes(self):
        assert currency_to_bytes("USD") == b"\x00" * 12 + b"USD" + b"\x00" * 5

    def test_hex_currency_is_taken_verbatim(self):
        code = "0158415500000000C1F76FF6ECB0BAC600000000"
        assert currency_to_bytes(code) == bytes.fromhex(code)

    def test_bad_currency_length_raises(self):
        with pytest.raises(ValueError):
            currency_to_bytes("USDX")


class TestGenerate:
    def test_entry_counts(self):
        # 5 AccountRoot + 2 RippleState + one DirectoryNode for the hub and one
        # per spoke account that holds a trustline.
        states, seeds = generate(5, num_trustlines=2)

        by_type: dict = {}
        for e in states:
            by_type[e["LedgerEntryType"]] = by_type.get(e["LedgerEntryType"], 0) + 1
        assert by_type == {"AccountRoot": 5, "RippleState": 2, "DirectoryNode": 3}
        assert len(states) == 10
        assert len(seeds) == 5

    def test_no_trustlines_means_only_account_roots(self):
        states, seeds = generate(3)

        assert [e["LedgerEntryType"] for e in states] == ["AccountRoot"] * 3
        assert all(e["OwnerCount"] == 0 for e in states)
        assert len(seeds) == 3

    def test_trustlines_are_capped_at_the_spoke_count(self):
        states, _ = generate(3, num_trustlines=10)

        assert sum(1 for e in states if e["LedgerEntryType"] == "RippleState") == 2

    def test_same_call_twice_is_identical(self):
        assert generate(4, num_trustlines=2) == generate(4, num_trustlines=2)

    def test_prefix_changes_the_account_set(self):
        _, seeds_a = generate(2)
        _, seeds_b = generate(2, prefix=b"other-")

        assert set(seeds_a).isdisjoint(seeds_b)

    def test_hub_owner_count_equals_its_trustlines(self):
        states, _ = generate(4, num_trustlines=3)
        hub = make_wallet(0).classic_address
        roots = {
            e["Account"]: e for e in states if e["LedgerEntryType"] == "AccountRoot"
        }

        assert roots[hub]["OwnerCount"] == 3
        assert all(e["OwnerCount"] == 1 for a, e in roots.items() if a != hub)
        assert all(e["Balance"] == "1000000000" for e in roots.values())

    def test_every_entry_index_is_unique(self):
        states, _ = generate(6, num_trustlines=4)
        indexes = [e["index"] for e in states]

        assert len(indexes) == len(set(indexes))
        assert all(len(i) == 64 and i == i.upper() for i in indexes)


class TestMergeIntoGenesis:
    def test_xrp_is_conserved(self):
        genesis = _load_genesis()
        entries, _ = generate(5, balance_drops="2500000000", num_trustlines=2)

        merged = merge_into_genesis(copy.deepcopy(genesis), entries)

        roots = _account_roots(merged)
        assert len(roots) == 6
        assert sum(int(e["Balance"]) for e in roots) == TOTAL_COINS
        assert int(merged["ledger"]["total_coins"]) == TOTAL_COINS
        root = [e for e in roots if e["Account"] == ROOT_ACCOUNT][0]
        assert int(root["Balance"]) == TOTAL_COINS - 5 * 2500000000

    def test_all_entries_are_appended(self):
        genesis = _load_genesis()
        before = len(genesis["ledger"]["accountState"])
        entries, _ = generate(5, num_trustlines=2)

        merged = merge_into_genesis(genesis, entries)

        state = merged["ledger"]["accountState"]
        assert len(state) == before + len(entries)
        assert state[before:] == entries

    def test_remerging_the_same_entries_dedups_by_index(self):
        genesis = _load_genesis()
        entries, _ = generate(5, num_trustlines=2)
        merged = merge_into_genesis(genesis, entries)
        count = len(merged["ledger"]["accountState"])
        root_balance = [
            e["Balance"] for e in _account_roots(merged) if e["Account"] == ROOT_ACCOUNT
        ][0]

        merged = merge_into_genesis(merged, copy.deepcopy(entries))

        assert len(merged["ledger"]["accountState"]) == count
        assert sum(int(e["Balance"]) for e in _account_roots(merged)) == TOTAL_COINS
        assert [
            e["Balance"] for e in _account_roots(merged) if e["Account"] == ROOT_ACCOUNT
        ] == [root_balance]

    def test_no_account_roots_leaves_the_root_balance_alone(self):
        genesis = _load_genesis()
        entries, _ = generate(3, num_trustlines=2)
        non_roots = [e for e in entries if e["LedgerEntryType"] != "AccountRoot"]

        merged = merge_into_genesis(genesis, non_roots)

        root = [e for e in _account_roots(merged) if e["Account"] == ROOT_ACCOUNT][0]
        assert int(root["Balance"]) == TOTAL_COINS


class TestMain:
    """``python -m xrpld_lab.ledger_generator`` writes the state and seed files."""

    def test_writes_state_and_wallets(self, tmp_path, capsys):
        state = tmp_path / "state.json"
        wallets = tmp_path / "wallets.sub.1.json"

        _main(
            [
                "--accounts",
                "3",
                "--trustlines",
                "1",
                "--out-state",
                str(state),
                "--out-wallets",
                str(wallets),
            ]
        )

        expected_states, expected_seeds = generate(3, "1000000000", 1, "USD")
        assert json.loads(state.read_text()) == expected_states
        assert json.loads(wallets.read_text()) == expected_seeds
        out = capsys.readouterr().out
        assert f"wrote 3 accounts + 1 trustlines -> {state}" in out
        assert f"wrote 3 seeds -> {wallets}" in out

    def test_balance_currency_and_prefix_reach_the_entries(self, tmp_path):
        state = tmp_path / "state.json"
        wallets = tmp_path / "wallets.sub.1.json"

        _main(
            [
                "--accounts",
                "2",
                "--trustlines",
                "1",
                "--balance",
                "5000000",
                "--currency",
                "EUR",
                "--prefix",
                "lab-",
                "--out-state",
                str(state),
                "--out-wallets",
                str(wallets),
            ]
        )

        states = json.loads(state.read_text())
        seeds = json.loads(wallets.read_text())
        expected_states, expected_seeds = generate(2, "5000000", 1, "EUR", b"lab-")
        assert states == expected_states
        assert seeds == expected_seeds
        assert [e["Balance"] for e in states if e["LedgerEntryType"] == "AccountRoot"]
        assert all(
            e["Balance"] == "5000000"
            for e in states
            if e["LedgerEntryType"] == "AccountRoot"
        )
        (trustline,) = [e for e in states if e["LedgerEntryType"] == "RippleState"]
        assert trustline["Balance"]["currency"] == "EUR"
        assert seeds != generate(2, "5000000", 1, "EUR")[1]

    def test_accounts_and_output_paths_are_required(self):
        with pytest.raises(SystemExit):
            _main(["--accounts", "1"])
