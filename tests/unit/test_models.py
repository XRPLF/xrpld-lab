#!/usr/bin/env python
# coding: utf-8

import pytest
from xrpld_lab.models import (
    Protocol,
    DeployMode,
    NodeRole,
    BuildType,
    NodeDbType,
    PortSet,
    ServerConfig,
    NodeDbConfig,
    TransactionQueueConfig,
    WorkerConfig,
    VotingConfig,
    ValidatorIdentity,
    NodeConfig,
    BuildSource,
    LabConfig,
    AnsibleConfig,
    ServicesHost,
    StatusConfig,
    NginxConfig,
    VlConfig,
    RedisConfig,
    FaucetConfig,
    StreamConfig,
    DebugConfig,
    CompilerConfig,
    GcpConfig,
)


class TestPortSet:
    def test_publish_mappings_bind_admin_ports_to_loopback(self):
        ports = PortSet.for_node(1, NodeRole.VALIDATOR)
        mappings = ports.publish_mappings()
        assert mappings == [
            f"{ports.rpc_public}:{ports.rpc_public}",
            f"127.0.0.1:{ports.rpc_admin}:{ports.rpc_admin}",
            f"{ports.ws_public}:{ports.ws_public}",
            f"127.0.0.1:{ports.ws_admin}:{ports.ws_admin}",
            f"{ports.peer}:{ports.peer}",
        ]

    """Test PortSet.for_node with various node roles and indices."""

    def test_validator_index_1(self):
        ports = PortSet.for_node(1, NodeRole.VALIDATOR)
        assert ports.rpc_public == 5107
        assert ports.rpc_admin == 5105
        assert ports.ws_public == 6108
        assert ports.ws_admin == 6106
        assert ports.peer == 51335

    def test_validator_index_2(self):
        ports = PortSet.for_node(2, NodeRole.VALIDATOR)
        assert ports.rpc_public == 5207
        assert ports.rpc_admin == 5205
        assert ports.ws_public == 6208
        assert ports.ws_admin == 6206
        assert ports.peer == 51435

    def test_peer_index_1(self):
        ports = PortSet.for_node(1, NodeRole.PEER)
        assert ports.rpc_public == 5017
        assert ports.rpc_admin == 5015
        assert ports.ws_public == 6018
        assert ports.ws_admin == 6016
        assert ports.peer == 51245

    def test_peer_index_2(self):
        ports = PortSet.for_node(2, NodeRole.PEER)
        assert ports.rpc_public == 5027
        assert ports.rpc_admin == 5025
        assert ports.ws_public == 6028
        assert ports.ws_admin == 6026
        assert ports.peer == 51255

    def test_standalone(self):
        ports = PortSet.for_node(0, NodeRole.STANDALONE)
        assert ports.rpc_public == 5007
        assert ports.rpc_admin == 5005
        assert ports.ws_public == 6008
        assert ports.ws_admin == 6006
        assert ports.peer == 51235

    def test_frozen_immutable(self):
        ports = PortSet.for_node(1, NodeRole.VALIDATOR)
        with pytest.raises(AttributeError):
            ports.rpc_public = 9999


class TestNodeDbConfig:
    """Test NodeDbConfig.for_mode with different db types and deploy modes."""

    def test_nudb_local(self):
        cfg = NodeDbConfig.for_mode(NodeDbType.NUDB, DeployMode.LOCAL)
        assert cfg.db_type == NodeDbType.NUDB
        assert cfg.path == "db"

    def test_nudb_standalone(self):
        cfg = NodeDbConfig.for_mode(NodeDbType.NUDB, DeployMode.STANDALONE)
        assert cfg.db_type == NodeDbType.NUDB
        assert cfg.path == "/opt/ripple/lib/db"

    def test_nudb_network(self):
        cfg = NodeDbConfig.for_mode(NodeDbType.NUDB, DeployMode.NETWORK)
        assert cfg.db_type == NodeDbType.NUDB
        assert cfg.path == "/var/lib/xrpld/db"

    def test_memory(self):
        cfg = NodeDbConfig.for_mode(NodeDbType.MEMORY, DeployMode.LOCAL)
        assert cfg.db_type == NodeDbType.MEMORY
        assert cfg.path == "./"

    def test_memory_network(self):
        cfg = NodeDbConfig.for_mode(NodeDbType.MEMORY, DeployMode.NETWORK)
        assert cfg.path == "./"

    def test_rwdb_is_not_a_backend(self):
        with pytest.raises(ValueError):
            NodeDbType("rwdb")


class TestLabConfig:
    """Test LabConfig effective_quorum property."""

    def test_effective_quorum_3_validators(self):
        build = BuildSource(
            protocol=Protocol.XRPL,
            build_type=BuildType.IMAGE,
            build_server="rippleci",
            build_version="3.1.1",
        )
        lab = LabConfig(
            protocol=Protocol.XRPL,
            mode=DeployMode.NETWORK,
            build_source=build,
            network_id=21337,
            num_validators=3,
        )
        assert lab.effective_quorum == 2

    def test_effective_quorum_explicit_override(self):
        build = BuildSource(
            protocol=Protocol.XRPL,
            build_type=BuildType.IMAGE,
            build_server="rippleci",
            build_version="3.1.1",
        )
        lab = LabConfig(
            protocol=Protocol.XRPL,
            mode=DeployMode.NETWORK,
            build_source=build,
            network_id=21337,
            num_validators=3,
            quorum=1,
        )
        assert lab.effective_quorum == 1

    def test_effective_quorum_1_validator(self):
        build = BuildSource(
            protocol=Protocol.XRPL,
            build_type=BuildType.IMAGE,
            build_server="rippleci",
            build_version="3.3.0",
        )
        lab = LabConfig(
            protocol=Protocol.XRPL,
            mode=DeployMode.STANDALONE,
            build_source=build,
            network_id=1,
            num_validators=1,
        )
        # max(1 - 1, 1) == 1
        assert lab.effective_quorum == 1


class TestNodeConfig:
    """Test NodeConfig default factories produce independent instances."""

    def test_default_lists_not_shared(self):
        ports = PortSet.for_node(0, NodeRole.STANDALONE)
        node_a = NodeConfig(
            name="node_a",
            index=0,
            role=NodeRole.STANDALONE,
            protocol=Protocol.XRPL,
            network_id=1,
            ports=ports,
        )
        node_b = NodeConfig(
            name="node_b",
            index=1,
            role=NodeRole.STANDALONE,
            protocol=Protocol.XRPL,
            network_id=1,
            ports=ports,
        )
        node_a.validators.append("some_key")
        assert node_b.validators == []

    def test_default_lists_empty(self):
        ports = PortSet.for_node(0, NodeRole.STANDALONE)
        node = NodeConfig(
            name="node",
            index=0,
            role=NodeRole.STANDALONE,
            protocol=Protocol.XRPL,
            network_id=1,
            ports=ports,
        )
        assert node.validators == []
        assert node.cluster_nodes == []
        assert node.ips_urls == []
        assert node.ips_fixed_urls == []
        assert node.vl_sites == []
        assert node.vl_keys == []
        assert node.amendments == {}


class TestEnums:
    """Test enum values are correct."""

    def test_protocol_values(self):
        assert Protocol.XRPL.value == "xrpl"

    def test_deploy_mode_values(self):
        assert DeployMode.STANDALONE.value == "standalone"
        assert DeployMode.NETWORK.value == "network"
        assert DeployMode.LOCAL.value == "local"

    def test_node_role_values(self):
        assert NodeRole.VALIDATOR.value == "validator"
        assert NodeRole.PEER.value == "peer"
        assert NodeRole.STANDALONE.value == "standalone"

    def test_build_type_values(self):
        assert BuildType.IMAGE.value == "image"
        assert BuildType.BINARY.value == "binary"

    def test_nodedb_type_values(self):
        assert NodeDbType.NUDB.value == "NuDB"
        assert NodeDbType.MEMORY.value == "Memory"
        assert [t.value for t in NodeDbType] == ["NuDB", "Memory"]


class TestServerConfig:
    """Test ServerConfig defaults."""

    def test_defaults(self):
        cfg = ServerConfig()
        assert cfg.rpc_public is True
        assert cfg.rpc_admin is True
        assert cfg.ws_public is True
        assert cfg.ws_admin is True
        assert cfg.peer is True
        assert cfg.ssl_verify is False
        assert cfg.ssl_key_path is None
        assert cfg.ssl_cert_path is None
        assert cfg.send_queue_limit == 65535


class TestTransactionQueueConfig:
    """Test TransactionQueueConfig defaults match xrpld_cfg.py."""

    def test_defaults(self):
        cfg = TransactionQueueConfig()
        assert cfg.ledgers_in_queue == 20
        assert cfg.minimum_queue_size == 2000
        assert cfg.retry_sequence_percent == 25
        assert cfg.minimum_escalation_multiplier == 500
        assert cfg.minimum_txn_in_ledger == 100000
        assert cfg.minimum_txn_in_ledger_standalone == 100000
        assert cfg.target_txn_in_ledger == 100000
        assert cfg.maximum_txn_in_ledger == 100000
        assert cfg.normal_consensus_increase_percent == 20
        assert cfg.slow_consensus_decrease_percent == 50
        assert cfg.maximum_txn_per_account == 100000
        assert cfg.minimum_last_ledger_buffer == 2
        assert cfg.zero_basefee_transaction_feelevel == 256000


class TestWorkerConfig:
    """Test WorkerConfig defaults."""

    def test_defaults(self):
        cfg = WorkerConfig()
        assert cfg.workers == 10
        assert cfg.io_workers == 10
        assert cfg.prefetch_workers == 10


class TestVotingConfig:
    """Test VotingConfig defaults match xrpld_cfg.py."""

    def test_defaults(self):
        cfg = VotingConfig()
        assert cfg.account_reserve == 1000000
        assert cfg.owner_reserve == 200000
        assert cfg.reference_fee == 10


class TestDatagramMonitorFor:
    """Per-node [datagram_monitor] sink: explicit sink wins, else the status sampler."""

    def _lab(self, **kw):
        build = BuildSource(
            protocol=Protocol.XRPL,
            build_type=BuildType.IMAGE,
            build_server="rippleci",
            build_version="3.1.1",
        )
        return LabConfig(
            protocol=Protocol.XRPL,
            mode=DeployMode.NETWORK,
            build_source=build,
            network_id=21337,
            **kw,
        )

    def _ansible(self, status):
        return AnsibleConfig(
            vips=["10.0.0.1"],
            pips=["10.0.0.10"],
            services=[ServicesHost(name="pnode1", ip="10.0.0.10", status=status)],
        )

    def test_none_without_sink_or_status(self):
        assert (
            self._lab(ansible=self._ansible(None)).datagram_monitor_for("10.0.0.1")
            is None
        )
        assert self._lab().datagram_monitor_for("10.0.0.1") is None

    def test_explicit_sink_wins(self):
        lab = self._lab(
            datagram_monitor="10.128.0.2 9876", ansible=self._ansible(StatusConfig())
        )
        assert lab.datagram_monitor_for("10.0.0.1") == ["10.128.0.2 9876"]

    def test_status_sampler_on_the_node_address(self):
        lab = self._lab(ansible=self._ansible(StatusConfig(xdgm_port=9998)))
        assert lab.datagram_monitor_for("10.0.0.1") == ["10.0.0.1 9998"]

    def test_node_without_address_gets_no_sink(self):
        lab = self._lab(ansible=self._ansible(StatusConfig()))
        assert lab.datagram_monitor_for("") is None

    def test_status_host_property(self):
        ansible = self._ansible(StatusConfig())
        assert ansible.status_host.name == "pnode1"
        assert "status" in ansible.services[0].enabled_services
        assert self._ansible(None).status_host is None


class TestPortSetUnknownRole:
    def test_unknown_role_raises(self):
        with pytest.raises(ValueError, match="Unknown node role"):
            PortSet.for_node(1, "bogus")


class TestServicesHostEnabledServices:
    def test_empty_without_services(self):
        assert ServicesHost(ip="10.0.0.10", name="infra").enabled_services == []

    def test_every_service_in_render_order(self):
        host = ServicesHost(
            ip="10.0.0.10",
            name="infra",
            nginx=NginxConfig(domain="example.test"),
            vl=VlConfig(),
            redis=RedisConfig(),
            faucet=FaucetConfig(),
            stream=StreamConfig(),
            debug=DebugConfig(),
            compiler=CompilerConfig(),
            status=StatusConfig(),
        )
        assert host.enabled_services == [
            "nginx",
            "status",
            "redis",
            "faucet",
            "stream",
            "debug",
            "compiler",
        ]

    def test_vl_alone_is_not_a_service(self):
        host = ServicesHost(ip="10.0.0.10", name="infra", vl=VlConfig())
        assert host.enabled_services == []


class TestGcpConfig:
    def test_region_is_the_first_validator_zone_without_its_letter(self):
        assert GcpConfig(project="perf").region == "us-central1"
        gcp = GcpConfig(project="perf", validator_zones=["europe-west1-b"])
        assert gcp.region == "europe-west1"

    def test_node_counts_follow_the_zone_lists(self):
        gcp = GcpConfig(project="perf")
        assert gcp.num_validators == 5
        assert gcp.num_peers == 4
        custom = GcpConfig(
            project="perf", validator_zones=["asia-east1-a"], peer_zones=[]
        )
        assert (custom.num_validators, custom.num_peers) == (1, 0)
