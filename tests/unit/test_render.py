"""Render tests: run the real LabRunner into a temporary workspace and inspect
the files it writes. Only GitHub fetches and key generation are replaced."""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

import pytest
import yaml

from xrpld_lab import cli
from xrpld_lab.amendments import _amendment_name_hash
from xrpld_lab.config import parse_xrpld_cfg
from xrpld_lab.source_resolver import SourceResolver
from xrpld_lab.workflows import LabRunner
from xrpld_lab.workspace import Workspace

SUPPORTED_FEATURE = "PermissionedDEX"
UNSUPPORTED_FEATURE = "Clawback"

# Two lines in the syntax parse_amendments accepts; the comment line is skipped.
FEATURES_MACRO = (
    "// Amendments in the order they were introduced.\n"
    f"XRPL_FEATURE({SUPPORTED_FEATURE}, Supported::yes, VoteBehavior::DefaultNo)\n"
    f"XRPL_FEATURE({UNSUPPORTED_FEATURE}, Supported::no, VoteBehavior::DefaultNo)\n"
).encode("utf-8")

PUBLISHER_KEY = "ED0000000000000000000000000000000000000000000000000000000000000001"
VALIDATOR_KEYS = {
    "vnode1": "nHValidatorOnePublicKeyBase58",
    "vnode2": "nHValidatorTwoPublicKeyBase58",
}

DOCKER_COMPOSE_UP = (
    "docker compose -f docker-compose.yml up --build --force-recreate -d"
)


class FakePublisherClient:
    """PublisherClient stand-in: keys live in memory, sign_unl writes the path."""

    def __init__(self, vl_path=None):
        self.keys = None
        self.manifests = []

    def get_keys(self):
        return self.keys

    def create_keys(self, vl_algorithm="ed25519", eph_algorithm="ed25519"):
        self.keys = {"publicKey": PUBLISHER_KEY}

    def add_validator(self, manifest):
        self.manifests.append(manifest)

    def sign_unl(self, path, effective=None, expiration=None):
        with open(path, "w") as f:
            json.dump({"public_key": PUBLISHER_KEY, "manifests": self.manifests}, f)


class FakeValidatorClient:
    """ValidatorClient stand-in returning one fixed identity per node name."""

    def __init__(self, name):
        self.name = name

    def create_keys(self, algorithm="ed25519"):
        pass

    def set_domain(self, domain):
        pass

    def create_token(self, ephemeral_algorithm="secp256k1"):
        pass

    def get_keys(self):
        return {"public_key": VALIDATOR_KEYS[self.name]}

    def read_token(self):
        return f"token-{self.name}"

    def read_manifest(self):
        return f"manifest-{self.name}"


def _lab_config(argv):
    return cli.build_lab_config(cli._build_parser().parse_args(argv))


def _amendments(genesis):
    entries = [
        e
        for e in genesis["ledger"]["accountState"]
        if e["LedgerEntryType"] == "Amendments"
    ]
    assert len(entries) == 1
    return entries[0]["Amendments"]


def _first_code_line(text):
    return next(line.strip() for line in text.splitlines() if line.strip())


def _as_list(value):
    return value if isinstance(value, list) else [value]


# ---------------------------------------------------------------------------
# Standalone
# ---------------------------------------------------------------------------


@pytest.fixture
def standalone_tree(tmp_path):
    lab = _lab_config(["up:standalone"])
    with (
        patch.object(SourceResolver, "resolve_features", return_value=FEATURES_MACRO),
    ):
        name = LabRunner(lab, Workspace(base=str(tmp_path))).run()
    return name, tmp_path / name


class TestStandaloneRender:
    def test_returns_directory_name_under_workspace(self, standalone_tree):
        name, out = standalone_tree
        assert name == "xrpl-3.3.0"
        assert out.is_dir()

    def test_xrpld_cfg_network_id_ports_and_node_db(self, standalone_tree):
        _, out = standalone_tree
        text = (out / "config" / "xrpld.cfg").read_text()
        assert "[network_id]\n1\n" in text
        cfg = parse_xrpld_cfg(text)
        assert cfg["network_id"] == "1"
        assert cfg["port_rpc_admin_local"]["port"] == "5005"
        assert cfg["port_ws_admin_local"]["port"] == "6006"
        assert cfg["node_db"]["type"] == "NuDB"
        assert cfg["node_db"]["path"] == "/opt/ripple/lib/db"
        assert cfg["database_path"] == "/opt/ripple/lib/db/rdb"

    def test_validators_txt_has_publisher_sections(self, standalone_tree):
        _, out = standalone_tree
        text = (out / "config" / "validators.txt").read_text()
        assert "[validator_list_sites]\n" in text
        assert f"[validator_list_keys]\n    {cli._DEFAULT_VL_KEY}\n" in text

    def test_genesis_enables_only_supported_amendments(self, standalone_tree):
        _, out = standalone_tree
        genesis = json.loads((out / "genesis.json").read_text())
        assert _amendments(genesis) == [_amendment_name_hash(SUPPORTED_FEATURE)]
        assert _amendment_name_hash(UNSUPPORTED_FEATURE) not in _amendments(genesis)

    def test_dockerfile_base_image_and_root_user(self, standalone_tree):
        _, out = standalone_tree
        dockerfile = (out / "Dockerfile").read_text()
        assert _first_code_line(dockerfile).startswith("FROM rippleci/xrpld:3.3.0")
        assert "USER root" in dockerfile
        assert (out / "entrypoint").is_file()

    def test_compose_publishes_the_admin_ports_the_cfg_declares(self, standalone_tree):
        _, out = standalone_tree
        cfg = parse_xrpld_cfg((out / "config" / "xrpld.cfg").read_text())
        compose = yaml.safe_load((out / "docker-compose.yml").read_text())
        service = compose["services"]["xrpl"]
        assert service["container_name"] == "xrpl"
        rpc_admin = cfg["port_rpc_admin_local"]["port"]
        ws_admin = cfg["port_ws_admin_local"]["port"]
        assert f"127.0.0.1:{rpc_admin}:{rpc_admin}" in service["ports"]
        assert f"127.0.0.1:{ws_admin}:{ws_admin}" in service["ports"]
        assert not any(p.startswith(f"{rpc_admin}:") for p in service["ports"])

    def test_start_script_is_executable_and_names_the_compose_file(
        self, standalone_tree
    ):
        _, out = standalone_tree
        start = out / "start.sh"
        assert os.access(start, os.X_OK)
        assert str(out / "docker-compose.yml") in start.read_text()


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

NETWORK_NODES = ("vnode1", "vnode2", "pnode1")


@pytest.fixture
def network_tree(tmp_path):
    lab = _lab_config(
        [
            "create:network",
            "--num_validators",
            "2",
            "--num_peers",
            "1",
            "--genesis",
            "True",
            "--workspace",
            str(tmp_path),
        ]
    )
    with (
        patch.object(SourceResolver, "resolve_features", return_value=FEATURES_MACRO),
        patch("xrpld_lab.workflows.PublisherClient", FakePublisherClient),
        patch("xrpld_lab.workflows.ValidatorClient", FakeValidatorClient),
    ):
        LabRunner(lab, Workspace(base=str(tmp_path))).run()
    return lab, tmp_path / "3.3.0-cluster"


class TestNetworkRender:
    def test_every_node_has_cfg_genesis_and_dockerfile(self, network_tree):
        _, cluster = network_tree
        for node in NETWORK_NODES:
            assert (cluster / node / "config" / "xrpld.cfg").is_file()
            assert (cluster / node / "genesis.json").is_file()
            assert (cluster / node / "Dockerfile").is_file()
            assert (cluster / node / "entrypoint").is_file()

    def test_dockerfile_passes_effective_quorum_to_the_entrypoint(self, network_tree):
        lab, cluster = network_tree
        assert lab.effective_quorum == 1
        expected = (
            'ENTRYPOINT [ "/entrypoint.sh", "/genesis.json", '
            f'"{lab.effective_quorum}", "--valid" ]'
        )
        for node in NETWORK_NODES:
            dockerfile = (cluster / node / "Dockerfile").read_text()
            assert expected in dockerfile
            assert _first_code_line(dockerfile).startswith("FROM rippleci/xrpld:3.3.0")

    def test_nodes_get_distinct_ports_and_compose_publishes_them(self, network_tree):
        _, cluster = network_tree
        compose = yaml.safe_load((cluster / "docker-compose.yml").read_text())
        admin_ports = set()
        for node in NETWORK_NODES:
            cfg = parse_xrpld_cfg((cluster / node / "config" / "xrpld.cfg").read_text())
            rpc_admin = cfg["port_rpc_admin_local"]["port"]
            peer = cfg["port_peer"]["port"]
            admin_ports.add(rpc_admin)
            service = compose["services"][node]
            assert service["container_name"] == node
            assert f"127.0.0.1:{rpc_admin}:{rpc_admin}" in service["ports"]
            assert f"{peer}:{peer}" in service["ports"]
        assert len(admin_ports) == len(NETWORK_NODES)

    def test_validators_trust_each_other_and_peers_trust_both(self, network_tree):
        _, cluster = network_tree

        def trusted(node):
            text = (cluster / node / "config" / "validators.txt").read_text()
            return _as_list(parse_xrpld_cfg(text)["validators"])

        assert trusted("vnode1") == [VALIDATOR_KEYS["vnode2"]]
        assert trusted("vnode2") == [VALIDATOR_KEYS["vnode1"]]
        assert set(trusted("pnode1")) == set(VALIDATOR_KEYS.values())

    def test_validator_cfg_carries_its_own_token(self, network_tree):
        _, cluster = network_tree
        for node in ("vnode1", "vnode2"):
            cfg = parse_xrpld_cfg((cluster / node / "config" / "xrpld.cfg").read_text())
            assert cfg["validator_token"] == f"token-{node}"

    def test_genesis_enables_only_supported_amendments(self, network_tree):
        _, cluster = network_tree
        for node in NETWORK_NODES:
            genesis = json.loads((cluster / node / "genesis.json").read_text())
            assert _amendments(genesis) == [_amendment_name_hash(SUPPORTED_FEATURE)]

    def test_vl_directory_holds_signed_list_and_dockerfile(self, network_tree):
        _, cluster = network_tree
        assert (cluster / "vl").is_dir()
        assert (cluster / "vl" / "Dockerfile").is_file()
        signed = json.loads((cluster / "vl" / "vl.json").read_text())
        assert signed["manifests"] == ["manifest-vnode1", "manifest-vnode2"]

    def test_compose_has_nodes_vl_and_explorer(self, network_tree):
        _, cluster = network_tree
        compose = yaml.safe_load((cluster / "docker-compose.yml").read_text())
        assert set(compose["services"]) == {*NETWORK_NODES, "vl", "network-explorer"}
        assert compose["services"]["vl"]["container_name"] == "vl"

    def test_start_script_is_a_single_compose_up(self, network_tree):
        _, cluster = network_tree
        start = cluster / "start.sh"
        assert os.access(start, os.X_OK)
        lines = [
            line.strip()
            for line in start.read_text().splitlines()
            if line.strip() and not line.startswith("#!")
        ]
        assert lines == [DOCKER_COMPOSE_UP]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def test_main_up_standalone_renders_then_starts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["xrpld-lab", "up:standalone"])
    with (
        patch.object(SourceResolver, "resolve_features", return_value=FEATURES_MACRO),
        patch("xrpld_lab.cli.run_start_script") as run_start_script,
    ):
        cli.main()

    run_start_script.assert_called_once()
    workspace, name = run_start_script.call_args.args
    assert name == "xrpl-3.3.0"
    assert os.path.realpath(workspace.base) == os.path.realpath(tmp_path / "workspace")
    assert os.path.isfile(os.path.join(workspace.base, name, "start.sh"))


class TestConfigOverrides:
    def test_overrides_file_reaches_the_rendered_cfg(self, tmp_path):
        overrides = tmp_path / "overrides.yaml"
        overrides.write_text(
            "node_size: medium\n"
            "transaction_queue:\n"
            "  ledgers_in_queue: 50\n"
            "  maximum_txn_in_ledger: 5000\n"
        )
        lab = _lab_config(["up:standalone", "--config_overrides", str(overrides)])
        with patch.object(
            SourceResolver, "resolve_features", return_value=FEATURES_MACRO
        ):
            name = LabRunner(lab, Workspace(base=str(tmp_path))).run()

        cfg = parse_xrpld_cfg((tmp_path / name / "config" / "xrpld.cfg").read_text())
        assert cfg["node_size"] == "medium"
        assert cfg["transaction_queue"]["ledgers_in_queue"] == "50"
        assert cfg["transaction_queue"]["maximum_txn_in_ledger"] == "5000"
        assert "network_id" in cfg
