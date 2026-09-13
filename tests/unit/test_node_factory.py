#!/usr/bin/env python
# coding: utf-8

import pytest
from xrpld_lab.models import (
    Protocol,
    DeployMode,
    NodeRole,
    NodeDbType,
    PortSet,
    NodeConfig,
    NodeDbConfig,
    ValidatorIdentity,
)
from xrpld_lab.protocol import get_spec
from xrpld_lab.node_factory import NodeFactory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def xrpl_spec():
    return get_spec(Protocol.XRPL)


@pytest.fixture
def three_validators():
    """Three dummy validator public keys."""
    return ["PUBKEY_V1", "PUBKEY_V2", "PUBKEY_V3"]


@pytest.fixture
def three_ips_fixed():
    """ips_fixed entries for a 3-validator network."""
    return ["v1 51335", "v2 51435", "v3 51535"]


# ---------------------------------------------------------------------------
# create_standalone
# ---------------------------------------------------------------------------


class TestCreateStandalone:
    """Tests for NodeFactory.create_standalone."""

    def test_role_is_standalone(self):
        node = NodeFactory.create_standalone(
            protocol=Protocol.XRPL,
            name="standalone",
            network_id=1,
        )
        assert node.role == NodeRole.STANDALONE

    def test_ports_match_standalone_base(self):
        node = NodeFactory.create_standalone(
            protocol=Protocol.XRPL,
            name="standalone",
            network_id=1,
        )
        expected = PortSet.for_node(0, NodeRole.STANDALONE)
        assert node.ports == expected

    def test_default_log_level_is_trace(self):
        node = NodeFactory.create_standalone(
            protocol=Protocol.XRPL,
            name="standalone",
            network_id=1,
        )
        assert node.log_level == "trace"

    def test_custom_log_level(self):
        node = NodeFactory.create_standalone(
            protocol=Protocol.XRPL,
            name="standalone",
            network_id=1,
            log_level="warning",
        )
        assert node.log_level == "warning"

    def test_nodedb_matches_standalone_mode(self):
        node = NodeFactory.create_standalone(
            protocol=Protocol.XRPL,
            name="standalone",
            network_id=1,
        )
        expected_db = NodeDbConfig.for_mode(NodeDbType.NUDB, DeployMode.STANDALONE)
        assert node.node_db.db_type == expected_db.db_type
        assert node.node_db.path == expected_db.path

    def test_db_path_is_docker(self):
        node = NodeFactory.create_standalone(
            protocol=Protocol.XRPL,
            name="standalone",
            network_id=1,
        )
        assert node.db_path == "/var/lib/xrpld/db/rdb"

    def test_debug_path_is_docker(self):
        node = NodeFactory.create_standalone(
            protocol=Protocol.XRPL,
            name="standalone",
            network_id=1,
        )
        assert node.debug_path == "/opt/ripple/log/debug.log"

    def test_no_validator_identity(self):
        node = NodeFactory.create_standalone(
            protocol=Protocol.XRPL,
            name="standalone",
            network_id=1,
        )
        assert node.validator is None

    def test_amendment_majority_time_xrpl(self, xrpl_spec):
        node = NodeFactory.create_standalone(
            protocol=Protocol.XRPL,
            name="standalone",
            network_id=1,
        )
        assert node.amendment_majority_time == xrpl_spec.amendment_majority_time
        assert node.amendment_majority_time == "15 minutes"

    def test_vl_sites_passed_through(self):
        node = NodeFactory.create_standalone(
            protocol=Protocol.XRPL,
            name="standalone",
            network_id=1,
            vl_sites=["http://vl/vl.json"],
        )
        assert node.vl_sites == ["http://vl/vl.json"]

    def test_vl_keys_passed_through(self):
        node = NodeFactory.create_standalone(
            protocol=Protocol.XRPL,
            name="standalone",
            network_id=1,
            vl_keys=["KEY1", "KEY2"],
        )
        assert node.vl_keys == ["KEY1", "KEY2"]

    def test_ips_urls_passed_through(self):
        node = NodeFactory.create_standalone(
            protocol=Protocol.XRPL,
            name="standalone",
            network_id=1,
            ips_urls=["r.ripple.com 51235"],
        )
        assert node.ips_urls == ["r.ripple.com 51235"]

    def test_ips_fixed_urls_passed_through(self):
        node = NodeFactory.create_standalone(
            protocol=Protocol.XRPL,
            name="standalone",
            network_id=1,
            ips_fixed_urls=["peer1 51235"],
        )
        assert node.ips_fixed_urls == ["peer1 51235"]

    def test_node_db_num_ledgers_is_256(self):
        node = NodeFactory.create_standalone(
            protocol=Protocol.XRPL,
            name="standalone",
            network_id=1,
        )
        assert node.node_db.num_ledgers == 256

    def test_size_node_is_huge(self):
        node = NodeFactory.create_standalone(
            protocol=Protocol.XRPL,
            name="standalone",
            network_id=1,
        )
        assert node.size_node == "huge"

    def test_index_is_zero(self):
        node = NodeFactory.create_standalone(
            protocol=Protocol.XRPL,
            name="standalone",
            network_id=1,
        )
        assert node.index == 0

    def test_name_and_network_id(self):
        node = NodeFactory.create_standalone(
            protocol=Protocol.XRPL,
            name="my_standalone",
            network_id=42,
        )
        assert node.name == "my_standalone"
        assert node.network_id == 42
        assert node.protocol == Protocol.XRPL

    def test_custom_node_db_type_memory(self):
        node = NodeFactory.create_standalone(
            protocol=Protocol.XRPL,
            name="standalone",
            network_id=1,
            node_db_type=NodeDbType.MEMORY,
        )
        assert node.node_db.db_type == NodeDbType.MEMORY
        assert node.node_db.path == "./"


# ---------------------------------------------------------------------------
# create_validator
# ---------------------------------------------------------------------------


class TestCreateValidator:
    """Tests for NodeFactory.create_validator."""

    def test_role_is_validator(self, three_validators):
        node = NodeFactory.create_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="vl1",
            network_id=21337,
            token="TOKEN1",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.role == NodeRole.VALIDATOR

    def test_ports_use_validator_offset(self, three_validators):
        node = NodeFactory.create_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="vl1",
            network_id=21337,
            token="TOKEN1",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        expected = PortSet.for_node(1, NodeRole.VALIDATOR)
        assert node.ports == expected
        assert node.ports.rpc_admin == 5105

    def test_ports_index_2(self, three_validators):
        node = NodeFactory.create_validator(
            index=2,
            protocol=Protocol.XRPL,
            name="vl2",
            network_id=21337,
            token="TOKEN2",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        expected = PortSet.for_node(2, NodeRole.VALIDATOR)
        assert node.ports == expected
        assert node.ports.rpc_admin == 5205

    def test_validator_token_is_set(self, three_validators):
        node = NodeFactory.create_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="vl1",
            network_id=21337,
            token="MY_TOKEN",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.validator is not None
        assert node.validator.token == "MY_TOKEN"

    def test_validator_public_key_matches_index(self, three_validators):
        node = NodeFactory.create_validator(
            index=2,
            protocol=Protocol.XRPL,
            name="vl2",
            network_id=21337,
            token="TOKEN2",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        # index is 1-based, so index=2 → all_validators[1] = "PUBKEY_V2"
        assert node.validator.public_key == "PUBKEY_V2"

    def test_validators_list_excludes_self(self, three_validators):
        node = NodeFactory.create_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="vl1",
            network_id=21337,
            token="TOKEN1",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        # Self is PUBKEY_V1 (index=1 → all_validators[0])
        assert "PUBKEY_V1" not in node.validators
        assert "PUBKEY_V2" in node.validators
        assert "PUBKEY_V3" in node.validators
        assert len(node.validators) == 2

    def test_ips_fixed_excludes_self(self, three_validators, three_ips_fixed):
        node = NodeFactory.create_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="vl1",
            network_id=21337,
            token="TOKEN1",
            all_validators=three_validators,
            vl_key="VL_KEY",
            ips_fixed=three_ips_fixed,
        )
        # Self is index 1, so ips_fixed[0] ("v1 51335") should be excluded
        assert "v1 51335" not in node.ips_fixed_urls
        assert "v2 51435" in node.ips_fixed_urls
        assert "v3 51535" in node.ips_fixed_urls
        assert len(node.ips_fixed_urls) == 2

    def test_vl_sites_is_vl_json(self, three_validators):
        node = NodeFactory.create_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="vl1",
            network_id=21337,
            token="TOKEN1",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.vl_sites == ["http://vl/vl.json"]

    def test_vl_keys_set(self, three_validators):
        node = NodeFactory.create_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="vl1",
            network_id=21337,
            token="TOKEN1",
            all_validators=three_validators,
            vl_key="MY_VL_KEY",
        )
        assert node.vl_keys == ["MY_VL_KEY"]

    def test_node_db_num_ledgers_256(self, three_validators):
        node = NodeFactory.create_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="vl1",
            network_id=21337,
            token="TOKEN1",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.node_db.num_ledgers == 256

    def test_node_db_uses_network_mode(self, three_validators):
        node = NodeFactory.create_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="vl1",
            network_id=21337,
            token="TOKEN1",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        expected = NodeDbConfig.for_mode(NodeDbType.NUDB, DeployMode.NETWORK)
        assert node.node_db.path == expected.path

    def test_default_log_level_is_warning(self, three_validators):
        node = NodeFactory.create_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="vl1",
            network_id=21337,
            token="TOKEN1",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.log_level == "warning"

    def test_amendment_majority_time_from_spec(self, three_validators):
        node = NodeFactory.create_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="vl1",
            network_id=21337,
            token="TOKEN1",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.amendment_majority_time == "15 minutes"

    def test_db_path_is_docker(self, three_validators):
        node = NodeFactory.create_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="vl1",
            network_id=21337,
            token="TOKEN1",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.db_path == "/var/lib/xrpld/db/rdb"

    def test_validator_manifest_is_empty(self, three_validators):
        node = NodeFactory.create_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="vl1",
            network_id=21337,
            token="TOKEN1",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.validator.manifest == ""


# ---------------------------------------------------------------------------
# create_peer
# ---------------------------------------------------------------------------


class TestCreatePeer:
    """Tests for NodeFactory.create_peer."""

    def test_role_is_peer(self, three_validators):
        node = NodeFactory.create_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.role == NodeRole.PEER

    def test_ports_use_peer_offset(self, three_validators):
        node = NodeFactory.create_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
        )
        expected = PortSet.for_node(1, NodeRole.PEER)
        assert node.ports == expected
        assert node.ports.rpc_admin == 5015

    def test_no_validator_identity(self, three_validators):
        node = NodeFactory.create_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.validator is None

    def test_validators_list_includes_all(self, three_validators):
        node = NodeFactory.create_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.validators == three_validators
        assert len(node.validators) == 3

    def test_ips_fixed_includes_all(self, three_validators, three_ips_fixed):
        node = NodeFactory.create_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
            ips_fixed=three_ips_fixed,
        )
        assert node.ips_fixed_urls == three_ips_fixed
        assert len(node.ips_fixed_urls) == 3

    def test_node_db_num_ledgers(self, three_validators):
        """Peers prune old ledgers like validators."""
        node = NodeFactory.create_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.node_db.num_ledgers == 256

    def test_vl_sites_is_vl_json(self, three_validators):
        node = NodeFactory.create_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.vl_sites == ["http://vl/vl.json"]

    def test_default_log_level_is_warning(self, three_validators):
        node = NodeFactory.create_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.log_level == "warning"

    def test_db_path_is_docker(self, three_validators):
        node = NodeFactory.create_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.db_path == "/var/lib/xrpld/db/rdb"

    def test_amendment_majority_time_from_spec(self, three_validators):
        node = NodeFactory.create_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.amendment_majority_time == "15 minutes"


# ---------------------------------------------------------------------------
# create_local_validator
# ---------------------------------------------------------------------------


class TestCreateLocalValidator:
    """Tests for NodeFactory.create_local_validator."""

    def test_vl_site_is_the_published_loopback_port(self, three_validators):
        node = NodeFactory.create_local_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="local_vl1",
            network_id=21337,
            token="TOKEN1",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.vl_sites == ["http://127.0.0.1/vl.json"]
        assert node.vl_keys == ["VL_KEY"]

    def test_db_path_is_local(self, three_validators):
        node = NodeFactory.create_local_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="local_vl1",
            network_id=21337,
            token="TOKEN1",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.db_path == "db"

    def test_debug_path_is_local(self, three_validators):
        node = NodeFactory.create_local_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="local_vl1",
            network_id=21337,
            token="TOKEN1",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.debug_path == "log/debug.log"

    def test_node_db_uses_local_mode(self, three_validators):
        node = NodeFactory.create_local_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="local_vl1",
            network_id=21337,
            token="TOKEN1",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        expected = NodeDbConfig.for_mode(NodeDbType.NUDB, DeployMode.LOCAL)
        assert node.node_db.path == expected.path
        assert node.node_db.path == "db"

    def test_role_is_validator(self, three_validators):
        node = NodeFactory.create_local_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="local_vl1",
            network_id=21337,
            token="TOKEN1",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.role == NodeRole.VALIDATOR

    def test_default_log_level_is_trace(self, three_validators):
        node = NodeFactory.create_local_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="local_vl1",
            network_id=21337,
            token="TOKEN1",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.log_level == "trace"

    def test_validators_list_excludes_self(self, three_validators):
        node = NodeFactory.create_local_validator(
            index=2,
            protocol=Protocol.XRPL,
            name="local_vl2",
            network_id=21337,
            token="TOKEN2",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        assert "PUBKEY_V2" not in node.validators
        assert "PUBKEY_V1" in node.validators
        assert "PUBKEY_V3" in node.validators

    def test_ips_fixed_excludes_self(self, three_validators, three_ips_fixed):
        node = NodeFactory.create_local_validator(
            index=2,
            protocol=Protocol.XRPL,
            name="local_vl2",
            network_id=21337,
            token="TOKEN2",
            all_validators=three_validators,
            vl_key="VL_KEY",
            ips_fixed=three_ips_fixed,
        )
        assert "v2 51435" not in node.ips_fixed_urls
        assert len(node.ips_fixed_urls) == 2

    def test_validator_identity_set(self, three_validators):
        node = NodeFactory.create_local_validator(
            index=1,
            protocol=Protocol.XRPL,
            name="local_vl1",
            network_id=21337,
            token="MY_TOKEN",
            all_validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.validator is not None
        assert node.validator.token == "MY_TOKEN"
        assert node.validator.public_key == "PUBKEY_V1"


# ---------------------------------------------------------------------------
# create_local_peer
# ---------------------------------------------------------------------------


class TestCreateLocalPeer:
    """Tests for NodeFactory.create_local_peer."""

    def test_vl_site_is_the_published_loopback_port(self, three_validators):
        node = NodeFactory.create_local_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="local_peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.vl_sites == ["http://127.0.0.1/vl.json"]
        assert node.vl_keys == ["VL_KEY"]

    def test_role_is_peer(self, three_validators):
        node = NodeFactory.create_local_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="local_peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.role == NodeRole.PEER

    def test_db_path_is_local(self, three_validators):
        node = NodeFactory.create_local_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="local_peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.db_path == "db"

    def test_debug_path_is_local(self, three_validators):
        node = NodeFactory.create_local_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="local_peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.debug_path == "log/debug.log"

    def test_node_db_uses_local_mode(self, three_validators):
        node = NodeFactory.create_local_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="local_peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
        )
        expected = NodeDbConfig.for_mode(NodeDbType.NUDB, DeployMode.LOCAL)
        assert node.node_db.path == expected.path

    def test_no_validator_identity(self, three_validators):
        node = NodeFactory.create_local_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="local_peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.validator is None

    def test_validators_list_includes_all(self, three_validators):
        node = NodeFactory.create_local_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="local_peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.validators == three_validators

    def test_ips_fixed_includes_all(self, three_validators, three_ips_fixed):
        node = NodeFactory.create_local_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="local_peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
            ips_fixed=three_ips_fixed,
        )
        assert node.ips_fixed_urls == three_ips_fixed

    def test_node_db_num_ledgers(self, three_validators):
        """Local peers prune old ledgers like validators."""
        node = NodeFactory.create_local_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="local_peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.node_db.num_ledgers == 256

    def test_default_log_level_is_trace(self, three_validators):
        node = NodeFactory.create_local_peer(
            index=1,
            protocol=Protocol.XRPL,
            name="local_peer1",
            network_id=21337,
            validators=three_validators,
            vl_key="VL_KEY",
        )
        assert node.log_level == "trace"
