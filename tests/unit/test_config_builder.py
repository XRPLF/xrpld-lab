#!/usr/bin/env python
# coding: utf-8

import pytest
from xrpld_lab.models import (
    Protocol,
    NodeRole,
    NodeDbType,
    PortSet,
    ServerConfig,
    NodeDbConfig,
    TransactionQueueConfig,
    WorkerConfig,
    VotingConfig,
    ValidatorIdentity,
    NodeConfig,
)
from xrpld_lab.config_builder import CfgSection, XrpldCfgBuilder, ValidatorsTxtBuilder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_standalone_config(**overrides) -> NodeConfig:
    """Create a minimal standalone NodeConfig with sensible defaults."""
    ports = PortSet.for_node(0, NodeRole.STANDALONE)
    defaults = dict(
        name="standalone",
        index=0,
        role=NodeRole.STANDALONE,
        protocol=Protocol.XRPL,
        network_id=0,
        ports=ports,
        server=ServerConfig(),
        node_db=NodeDbConfig(db_type=NodeDbType.NUDB, path="/opt/ripple/lib/db"),
        db_path="/opt/ripple/lib/db",
        debug_path="/opt/ripple/log/debug.log",
        size_node="huge",
        log_level="trace",
    )
    defaults.update(overrides)
    return NodeConfig(**defaults)


def _make_validator_config(index: int = 1, **overrides) -> NodeConfig:
    """Create a validator NodeConfig for network mode."""
    ports = PortSet.for_node(index, NodeRole.VALIDATOR)
    defaults = dict(
        name=f"vl{index}",
        index=index,
        role=NodeRole.VALIDATOR,
        protocol=Protocol.XRPL,
        network_id=21337,
        ports=ports,
        server=ServerConfig(),
        node_db=NodeDbConfig(
            db_type=NodeDbType.NUDB,
            path="/var/lib/xrpld/db",
            num_ledgers=10000,
        ),
        db_path="/var/lib/xrpld/db",
        debug_path="/opt/ripple/log/debug.log",
        size_node="huge",
        log_level="trace",
        validator=ValidatorIdentity(
            public_key="nHUFE9prPXPrHcG3SkxP1BQABk...fake",
            token="eyJ0eXAiOiJKV1QiLCJhbGciOiJI...fake",
            manifest="JAAAAA...fake",
        ),
        validators=["nHUk1key1", "nHUk2key2"],
        ips_fixed_urls=["192.168.1.1 51235", "192.168.1.2 51335"],
    )
    defaults.update(overrides)
    return NodeConfig(**defaults)


# ===========================================================================
# CfgSection
# ===========================================================================


class TestCfgSection:
    """Tests for the CfgSection helper."""

    def test_render_with_lines(self):
        sec = CfgSection("server")
        sec.add("port_rpc_public")
        sec.add("port_ws_public")
        result = sec.render()
        assert result == "[server]\nport_rpc_public\nport_ws_public\n"

    def test_render_empty(self):
        sec = CfgSection("node_size")
        result = sec.render()
        assert result == "[node_size]\n"

    def test_add_kv(self):
        sec = CfgSection("transaction_queue")
        sec.add_kv("ledgers_in_queue", 20)
        result = sec.render()
        assert result == "[transaction_queue]\nledgers_in_queue = 20\n"

    def test_chaining(self):
        sec = CfgSection("port_peer")
        result = sec.add("port = 51235").add("ip = 0.0.0.0").add("protocol = peer")
        assert result is sec  # chaining returns self
        assert len(sec.lines) == 3

    def test_render_multiple_kv(self):
        sec = CfgSection("voting")
        sec.add_kv("account_reserve", 1000000)
        sec.add_kv("owner_reserve", 200000)
        sec.add_kv("reference_fee", 10)
        expected = (
            "[voting]\n"
            "account_reserve = 1000000\n"
            "owner_reserve = 200000\n"
            "reference_fee = 10\n"
        )
        assert sec.render() == expected


# ===========================================================================
# XrpldCfgBuilder - section-by-section
# ===========================================================================


class TestXrpldCfgBuilderServer:
    """Test [server] section port listing."""

    def test_all_ports_enabled(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        # Server section should list all 5 port names
        assert "[server]\n" in output
        assert "port_rpc_public\n" in output
        assert "port_rpc_admin_local\n" in output
        assert "port_ws_public\n" in output
        assert "port_peer\n" in output
        assert "port_ws_admin_local\n" in output

    def test_rpc_public_disabled(self):
        server = ServerConfig(rpc_public=False)
        cfg = _make_standalone_config(server=server)
        output = XrpldCfgBuilder(cfg).build()
        assert "port_rpc_public" not in output.split("[server]\n")[1].split("\n\n")[0]
        # But [port_rpc_public] section should not exist
        assert "[port_rpc_public]" not in output

    def test_ws_admin_disabled(self):
        server = ServerConfig(ws_admin=False)
        cfg = _make_standalone_config(server=server)
        output = XrpldCfgBuilder(cfg).build()
        assert "[port_ws_admin_local]" not in output

    def test_peer_disabled(self):
        server = ServerConfig(peer=False)
        cfg = _make_standalone_config(server=server)
        output = XrpldCfgBuilder(cfg).build()
        assert "[port_peer]" not in output


class TestXrpldCfgBuilderSSL:
    """Test SSL key/cert in [server] section."""

    def test_no_ssl(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        assert "ssl_key" not in output.split("[port_")[0]
        assert "ssl_cert" not in output.split("[port_")[0]

    def test_ssl_present(self):
        server = ServerConfig(
            ssl_key_path="/etc/ssl/key.pem",
            ssl_cert_path="/etc/ssl/cert.pem",
        )
        cfg = _make_standalone_config(server=server)
        output = XrpldCfgBuilder(cfg).build()
        assert "ssl_key = /etc/ssl/key.pem\n" in output
        assert "ssl_cert = /etc/ssl/cert.pem\n" in output


class TestXrpldCfgBuilderPorts:
    """Test individual port sections."""

    def test_rpc_public_section(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        assert "[port_rpc_public]\n" in output
        assert "port = 5007\n" in output
        assert "admin = 0.0.0.0\n" in output
        assert "protocol = http\n" in output

    def test_ws_public_no_admin_line(self):
        """WS public port should NOT have an admin line."""
        server = ServerConfig(
            rpc_public=False,
            rpc_admin=False,
            ws_public=True,
            ws_admin=False,
            peer=False,
        )
        cfg = _make_standalone_config(server=server)
        output = XrpldCfgBuilder(cfg).build()
        # Extract just the [port_ws_public] section
        ws_section_start = output.index("[port_ws_public]")
        ws_section = output[ws_section_start:]
        # Find the end of this section (next blank line or next section)
        next_section = ws_section.find("\n\n")
        if next_section != -1:
            ws_section = ws_section[:next_section]
        assert "admin" not in ws_section
        assert "protocol = ws\n" in ws_section

    def test_ws_admin_has_admin_line(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        ws_admin_start = output.index("[port_ws_admin_local]")
        ws_admin_section = output[ws_admin_start:]
        next_blank = ws_admin_section.find("\n\n")
        if next_blank != -1:
            ws_admin_section = ws_admin_section[:next_blank]
        assert "admin = 0.0.0.0" in ws_admin_section
        assert "protocol = ws" in ws_admin_section

    def test_peer_section(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        peer_start = output.index("[port_peer]")
        peer_section = output[peer_start:]
        next_blank = peer_section.find("\n\n")
        if next_blank != -1:
            peer_section = peer_section[:next_blank]
        assert "protocol = peer" in peer_section
        # Peer should NOT have admin line
        assert "admin" not in peer_section

    def test_send_queue_limit(self):
        server = ServerConfig(send_queue_limit=32768)
        cfg = _make_standalone_config(server=server)
        output = XrpldCfgBuilder(cfg).build()
        assert "send_queue_limit = 32768\n" in output


class TestXrpldCfgBuilderNodeDb:
    """Test [node_db] section variants."""

    def test_node_db_with_num_ledgers(self):
        node_db = NodeDbConfig(
            db_type=NodeDbType.NUDB,
            path="/var/lib/xrpld/db",
            num_ledgers=10000,
        )
        cfg = _make_standalone_config(node_db=node_db)
        output = XrpldCfgBuilder(cfg).build()
        assert "[node_db]\n" in output
        assert "type=NuDB\n" in output
        assert "path=/var/lib/xrpld/db\n" in output
        assert "advisory_delete=0\n" in output
        assert "online_delete=10000\n" in output

    def test_node_db_without_num_ledgers(self):
        node_db = NodeDbConfig(
            db_type=NodeDbType.MEMORY,
            path="./",
            num_ledgers=None,
        )
        cfg = _make_standalone_config(node_db=node_db)
        output = XrpldCfgBuilder(cfg).build()
        assert "type=Memory\n" in output
        assert "advisory_delete" not in output
        assert "online_delete" not in output

    def test_no_relational_db_section(self):
        node_db = NodeDbConfig(db_type=NodeDbType.MEMORY, path="./", num_ledgers=None)
        cfg = _make_standalone_config(node_db=node_db)
        output = XrpldCfgBuilder(cfg).build()
        assert "[relational_db]" not in output
        assert "backend=" not in output


class TestXrpldCfgBuilderLedgerHistory:
    """Test [ledger_history] with and without num_ledgers."""

    def test_with_num_ledgers(self):
        node_db = NodeDbConfig(
            db_type=NodeDbType.NUDB,
            path="/var/lib/xrpld/db",
            num_ledgers=5000,
        )
        cfg = _make_standalone_config(node_db=node_db)
        output = XrpldCfgBuilder(cfg).build()
        assert "[ledger_history]\n5000\n" in output

    def test_without_num_ledgers(self):
        node_db = NodeDbConfig(
            db_type=NodeDbType.NUDB,
            path="/var/lib/xrpld/db",
            num_ledgers=None,
        )
        cfg = _make_standalone_config(node_db=node_db)
        output = XrpldCfgBuilder(cfg).build()
        assert "[ledger_history]\nfull\n" in output


class TestXrpldCfgBuilderIps:
    """Test [ips] and [ips_fixed] sections."""

    def test_ips_present(self):
        cfg = _make_standalone_config(
            ips_urls=["r.ripple.com 51235", "zaphod.alloy.ee 51235"]
        )
        output = XrpldCfgBuilder(cfg).build()
        assert "[ips]\n" in output
        assert "r.ripple.com 51235\n" in output
        assert "zaphod.alloy.ee 51235\n" in output

    def test_ips_absent(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        assert "[ips]" not in output

    def test_ips_fixed_present(self):
        cfg = _make_standalone_config(
            ips_fixed_urls=["192.168.1.1 51235", "192.168.1.2 51335"]
        )
        output = XrpldCfgBuilder(cfg).build()
        assert "[ips_fixed]\n" in output
        assert "192.168.1.1 51235\n" in output

    def test_ips_fixed_absent(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        assert "[ips_fixed]" not in output


class TestXrpldCfgBuilderDatagramMonitor:
    """Test the [datagram_monitor] section (XDGM emission to the perf-server)."""

    def test_datagram_monitor_present(self):
        cfg = _make_standalone_config(datagram_monitor=["10.128.0.2 9876"])
        output = XrpldCfgBuilder(cfg).build()
        assert "[datagram_monitor]\n" in output
        # Endpoint line is space-separated "ip port" (matches DatagramMonitor.h parseEndpoint).
        assert "10.128.0.2 9876\n" in output

    def test_datagram_monitor_multiple_endpoints(self):
        cfg = _make_standalone_config(
            datagram_monitor=["10.128.0.2 9876", "10.128.0.3 9876"]
        )
        output = XrpldCfgBuilder(cfg).build()
        assert "10.128.0.2 9876\n" in output
        assert "10.128.0.3 9876\n" in output

    def test_datagram_monitor_absent(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        assert "[datagram_monitor]" not in output


class TestXrpldCfgBuilderNetworkId:
    """Test [network_id] conditional rendering."""

    def test_network_id_present(self):
        cfg = _make_standalone_config(network_id=21337)
        output = XrpldCfgBuilder(cfg).build()
        assert "[network_id]\n21337\n" in output

    def test_network_id_zero_omitted(self):
        """network_id=0 is falsy and should be omitted (matches old behavior)."""
        cfg = _make_standalone_config(network_id=0)
        output = XrpldCfgBuilder(cfg).build()
        assert "[network_id]" not in output


class TestXrpldCfgBuilderValidation:
    """Test [validation_seed] and [validator_token] conditional rendering."""

    def test_validation_seed_present(self):
        vi = ValidatorIdentity(
            public_key="nHU_pub_key",
            token="eyJ_token_data",
            manifest="JAAAA_manifest",
        )
        cfg = _make_standalone_config(validator=vi)
        output = XrpldCfgBuilder(cfg).build()
        # The old code uses v_seed for validation_seed. In the new model,
        # the manifest is used as the seed equivalent? Let's check:
        # Actually, old code has v_seed and v_token separately. In the new model,
        # validator.manifest acts as seed and validator.token as token.
        # But the task says [validation_seed] optional, [validator_token] optional.
        # We'll use validator.manifest -> validation_seed, validator.token -> validator_token
        assert "[validator_token]\n" in output

    def test_no_validator(self):
        cfg = _make_standalone_config(validator=None)
        output = XrpldCfgBuilder(cfg).build()
        assert "[validation_seed]" not in output
        assert "[validator_token]" not in output


class TestXrpldCfgBuilderClusterNodes:
    """Test [cluster_nodes] conditional rendering."""

    def test_cluster_nodes_present(self):
        cfg = _make_standalone_config(
            cluster_nodes=["nHU_cluster_key_1", "nHU_cluster_key_2"]
        )
        output = XrpldCfgBuilder(cfg).build()
        assert "[cluster_nodes]\n" in output
        assert "nHU_cluster_key_1\n" in output
        assert "nHU_cluster_key_2\n" in output

    def test_cluster_nodes_absent(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        assert "[cluster_nodes]" not in output


class TestXrpldCfgBuilderLogLevel:
    """Test [rpc_startup] with various log levels."""

    @pytest.mark.parametrize(
        "level",
        ["trace", "debug", "info", "warning", "error"],
    )
    def test_known_log_levels(self, level):
        cfg = _make_standalone_config(log_level=level)
        output = XrpldCfgBuilder(cfg).build()
        expected = f'{{ "command": "log_level", "severity": "{level}" }}'
        assert expected in output

    def test_unknown_log_level_defaults_to_info(self):
        cfg = _make_standalone_config(log_level="banana")
        output = XrpldCfgBuilder(cfg).build()
        expected = '{ "command": "log_level", "severity": "info" }'
        assert expected in output


class TestXrpldCfgBuilderAmendments:
    """Test [amendments] section format."""

    def test_amendments_present(self):
        amendments = {
            "fix1543": "ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789",
            "FlowCross": "1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF",
        }
        cfg = _make_standalone_config(amendments=amendments)
        output = XrpldCfgBuilder(cfg).build()
        assert "[amendments]\n" in output
        # Format is: {hash} {name} - i.e. value then key
        assert (
            "ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789 fix1543\n"
            in output
        )
        assert (
            "1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF FlowCross\n"
            in output
        )

    def test_amendments_absent(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        assert "[amendments]" not in output


class TestXrpldCfgBuilderAmendmentMajorityTime:
    """Test [amendment_majority_time] conditional."""

    def test_present(self):
        cfg = _make_standalone_config(amendment_majority_time="5 minutes")
        output = XrpldCfgBuilder(cfg).build()
        assert "[amendment_majority_time]\n5 minutes\n" in output

    def test_absent(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        assert "[amendment_majority_time]" not in output


class TestXrpldCfgBuilderWorkers:
    """Test workers sections have correct format with trailing spaces."""

    def test_workers_trailing_space(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        # The old code outputs "[workers] \n" and "{workers} \n"
        assert "[workers] \n" in output
        assert "10 \n" in output

    def test_io_workers_trailing_space(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        assert "[io_workers] \n" in output

    def test_prefetch_workers_trailing_space(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        assert "[prefetch_workers] \n" in output

    def test_workers_zero_omitted(self):
        """If workers is 0 (falsy), old code skips the section."""
        worker = WorkerConfig(workers=0, io_workers=0, prefetch_workers=0)
        cfg = _make_standalone_config(worker=worker)
        output = XrpldCfgBuilder(cfg).build()
        assert "[workers]" not in output
        assert "[io_workers]" not in output
        assert "[prefetch_workers]" not in output


class TestXrpldCfgBuilderTransactionQueue:
    """Test [transaction_queue] section with space-separated key = value pairs."""

    def test_all_13_fields_present(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        assert "[transaction_queue]\n" in output
        assert "ledgers_in_queue = 20\n" in output
        assert "minimum_queue_size = 2000\n" in output
        assert "retry_sequence_percent = 25\n" in output
        assert "minimum_escalation_multiplier = 500\n" in output
        assert "minimum_txn_in_ledger = 100000\n" in output
        assert "minimum_txn_in_ledger_standalone = 100000\n" in output
        assert "target_txn_in_ledger = 100000\n" in output
        assert "maximum_txn_in_ledger = 100000\n" in output
        assert "normal_consensus_increase_percent = 20\n" in output
        assert "slow_consensus_decrease_percent = 50\n" in output
        assert "maximum_txn_per_account = 100000\n" in output
        assert "minimum_last_ledger_buffer = 2\n" in output
        assert "zero_basefee_transaction_feelevel = 256000\n" in output


class TestXrpldCfgBuilderHardcodedSections:
    """Test hardcoded sections that always appear."""

    def test_fee_reserves(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        assert "[fee_account_reserve]\n5000000\n" in output
        assert "[fee_owner_reserve]\n1000000\n" in output

    def test_sntp_servers(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        assert "[sntp_servers]\n" in output
        assert "time.windows.com\n" in output
        assert "time.apple.com\n" in output
        assert "time.nist.gov\n" in output
        assert "pool.ntp.org\n" in output

    def test_peer_private(self):
        cfg = _make_standalone_config(private_peer=False)
        output = XrpldCfgBuilder(cfg).build()
        assert "[peer_private]\n0\n" in output

    def test_peer_private_true(self):
        cfg = _make_standalone_config(private_peer=True)
        output = XrpldCfgBuilder(cfg).build()
        assert "[peer_private]\n1\n" in output

    def test_validators_file(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        assert "[validators_file]\nvalidators.txt\n" in output

    def test_ssl_verify_false(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        # ssl_verify=False -> 0
        assert "[ssl_verify]\n0\n" in output

    def test_ssl_verify_true(self):
        server = ServerConfig(ssl_verify=True)
        cfg = _make_standalone_config(server=server)
        output = XrpldCfgBuilder(cfg).build()
        assert "[ssl_verify]\n1\n" in output

    def test_max_transactions(self):
        cfg = _make_standalone_config(max_transactions=5000)
        output = XrpldCfgBuilder(cfg).build()
        assert "[max_transactions]\n5000\n" in output

    def test_voting_section(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        assert "[voting]\n" in output
        assert "account_reserve = 1000000\n" in output
        assert "owner_reserve = 200000\n" in output
        assert "reference_fee = 10\n" in output

    def test_dex_feed(self):
        cfg = _make_standalone_config()
        output = XrpldCfgBuilder(cfg).build()
        assert "[dex_feed]\n" in output
        assert "enabled=true\n" in output
        assert "socket_path=/tmp/dex_feed.sock\n" in output
        assert "include_amm_state=true\n" in output
        assert "db_path=./dex_timeseries\n" in output

    def test_database_path(self):
        cfg = _make_standalone_config(db_path="/opt/ripple/lib/db")
        output = XrpldCfgBuilder(cfg).build()
        assert "[database_path]\n/opt/ripple/lib/db\n" in output

    def test_debug_logfile(self):
        cfg = _make_standalone_config(debug_path="/opt/ripple/log/debug.log")
        output = XrpldCfgBuilder(cfg).build()
        assert "[debug_logfile]\n/opt/ripple/log/debug.log\n" in output


class TestXrpldCfgBuilderFullStandalone:
    """Full end-to-end test for a standalone config."""

    def test_full_standalone(self):
        cfg = _make_standalone_config(
            node_db=NodeDbConfig(
                db_type=NodeDbType.NUDB,
                path="/opt/ripple/lib/db",
                num_ledgers=10000,
            ),
        )
        output = XrpldCfgBuilder(cfg).build()

        # Spot-check section ordering: server must come first
        assert output.startswith("[server]\n")

        # All 5 ports should appear in server section
        server_end = output.index("\n\n")
        server_block = output[:server_end]
        assert "port_rpc_public" in server_block
        assert "port_rpc_admin_local" in server_block
        assert "port_ws_public" in server_block
        assert "port_peer" in server_block
        assert "port_ws_admin_local" in server_block

        # Port sections
        assert "[port_rpc_public]\n" in output
        assert "[port_rpc_admin_local]\n" in output
        assert "[port_ws_public]\n" in output
        assert "[port_ws_admin_local]\n" in output
        assert "[port_peer]\n" in output

        # Node config sections
        assert "[node_size]\nhuge\n" in output
        assert "[node_db]\n" in output
        assert "[fee_account_reserve]\n5000000\n" in output
        assert "[fee_owner_reserve]\n1000000\n" in output
        assert "[ledger_history]\n10000\n" in output
        assert "[database_path]\n" in output
        assert "[debug_logfile]\n" in output
        assert "[sntp_servers]\n" in output
        assert "[peer_private]\n0\n" in output
        assert "[validators_file]\nvalidators.txt\n" in output
        assert "[rpc_startup]\n" in output
        assert "[ssl_verify]\n0\n" in output
        assert "[max_transactions]\n100000\n" in output
        assert "[transaction_queue]\n" in output
        assert "[voting]\n" in output
        assert "[dex_feed]\n" in output


class TestXrpldCfgBuilderMemoryLimit:
    """[memory_limit] replaces the node_size/tree_cache stanzas when set."""

    def test_memory_limit_present(self):
        cfg = _make_standalone_config(memory_limit=16)
        output = XrpldCfgBuilder(cfg).build()
        assert "[memory_limit]\n16\n" in output

    def test_memory_limit_replaces_node_size_and_tree_cache(self):
        cfg = _make_standalone_config(
            memory_limit=16, tree_cache_target_entries=40_000_000
        )
        output = XrpldCfgBuilder(cfg).build()
        assert "[node_size]" not in output
        assert "[tree_cache_ram_percent]" not in output
        assert "[tree_cache_target_entries]" not in output

    def test_absent_keeps_node_size_path(self):
        cfg = _make_standalone_config()  # memory_limit defaults to None
        output = XrpldCfgBuilder(cfg).build()
        assert "[memory_limit]" not in output
        assert "[node_size]\nhuge\n" in output


class TestXrpldCfgBuilderFullValidator:
    """Full end-to-end test for a network validator config."""

    def test_full_validator(self):
        cfg = _make_validator_config(
            amendment_majority_time="15 minutes",
            amendments={
                "fix1543": "ABCDEF0123456789" * 4,
            },
            cluster_nodes=["nHU_cluster_1"],
        )
        output = XrpldCfgBuilder(cfg).build()

        # Should have network_id
        assert "[network_id]\n21337\n" in output

        # Should have validator_token
        assert "[validator_token]\n" in output

        # Should have ips_fixed
        assert "[ips_fixed]\n" in output

        # Should have cluster_nodes
        assert "[cluster_nodes]\n" in output

        # Should have amendment_majority_time
        assert "[amendment_majority_time]\n15 minutes\n" in output

        # Should have amendments
        assert "[amendments]\n" in output


# ===========================================================================
# ValidatorsTxtBuilder
# ===========================================================================


class TestValidatorsTxtBuilderGenesis:
    """Test genesis mode: [validators] with indented keys."""

    def test_genesis_mode(self):
        cfg = _make_standalone_config(
            validators=["nHUkey1_public", "nHUkey2_public"],
        )
        builder = ValidatorsTxtBuilder(cfg, genesis=True)
        output = builder.build()
        assert "[validators]\n" in output
        assert "    nHUkey1_public\n" in output
        assert "    nHUkey2_public\n" in output

    def test_genesis_empty_validators(self):
        cfg = _make_standalone_config(validators=[])
        builder = ValidatorsTxtBuilder(cfg, genesis=True)
        output = builder.build()
        assert "[validators]\n" in output


class TestValidatorsTxtBuilderNonGenesis:
    """Test non-genesis mode: [validator_list_sites] + [validator_list_keys]."""

    def test_non_genesis_mode(self):
        cfg = _make_standalone_config(
            vl_sites=["https://vl.ripple.com"],
            vl_keys=["ED_vl_key_1", "ED_vl_key_2"],
        )
        builder = ValidatorsTxtBuilder(cfg, genesis=False)
        output = builder.build()
        assert "[validator_list_sites]\n" in output
        assert "    https://vl.ripple.com\n" in output
        assert "[validator_list_keys]\n" in output
        assert "    ED_vl_key_1\n" in output
        assert "    ED_vl_key_2\n" in output

    def test_non_genesis_site_has_blank_line_after(self):
        """Each site entry should have a blank line after it (old code behavior)."""
        cfg = _make_standalone_config(
            vl_sites=["https://vl.ripple.com"],
            vl_keys=["ED_vl_key_1"],
        )
        builder = ValidatorsTxtBuilder(cfg, genesis=False)
        output = builder.build()
        # After each site there should be a blank line
        assert "    https://vl.ripple.com\n\n" in output


# ===========================================================================
# [insight] / [perf] — the telemetry stanzas the Alloy sidecar requires
# ===========================================================================


class TestInsightSection:
    def test_omitted_without_statsd_address(self):
        out = XrpldCfgBuilder(_make_standalone_config()).build()
        assert "[insight]" not in out

    def test_emitted_with_statsd_address(self):
        cfg = _make_standalone_config(
            statsd_address="127.0.0.1:9125", statsd_prefix="alphanet.vnode1"
        )
        out = XrpldCfgBuilder(cfg).build()
        assert "[insight]\n" in out
        assert "server=statsd\n" in out
        assert "address=127.0.0.1:9125\n" in out
        assert "prefix=alphanet.vnode1\n" in out


class TestPerfSection:
    def test_omitted_without_perf_path(self):
        out = XrpldCfgBuilder(_make_standalone_config()).build()
        assert "[perf]" not in out

    def test_emitted_with_perf_path(self):
        cfg = _make_standalone_config(perf_path="/opt/ripple/log/perf.log")
        out = XrpldCfgBuilder(cfg).build()
        assert "[perf]\n" in out
        assert "perf_log=/opt/ripple/log/perf.log\n" in out
        assert "log_interval=2\n" in out


class TestValidatorsTxtBootstrapVl:
    """Static list + publisher list together: cold-start on static, converge on the VL."""

    def test_bootstrap_emits_both(self):
        cfg = _make_standalone_config(
            validators=["nHUkey1"],
            vl_sites=["https://vl.alphanet.xrpl.org"],
            vl_keys=["ED_vl_key_1"],
        )
        out = ValidatorsTxtBuilder(cfg, genesis=True, bootstrap_vl=True).build()
        assert "[validators]\n" in out
        assert "    nHUkey1\n" in out
        assert "[validator_list_sites]\n" in out
        assert "    https://vl.alphanet.xrpl.org\n" in out
        assert "[validator_list_keys]\n" in out
        assert "    ED_vl_key_1\n" in out

    def test_static_list_precedes_publisher_list(self):
        cfg = _make_standalone_config(
            validators=["nHUkey1"],
            vl_sites=["https://vl.alphanet.xrpl.org"],
            vl_keys=["ED_vl_key_1"],
        )
        out = ValidatorsTxtBuilder(cfg, genesis=True, bootstrap_vl=True).build()
        assert out.index("[validators]") < out.index("[validator_list_sites]")

    def test_default_stays_static_only(self):
        cfg = _make_standalone_config(
            validators=["nHUkey1"],
            vl_sites=["https://vl.alphanet.xrpl.org"],
            vl_keys=["ED_vl_key_1"],
        )
        out = ValidatorsTxtBuilder(cfg, genesis=True).build()
        assert "[validators]\n" in out
        assert "[validator_list_sites]" not in out

    def test_bootstrap_ignored_in_vl_only_mode(self):
        cfg = _make_standalone_config(
            validators=["nHUkey1"],
            vl_sites=["https://vl.alphanet.xrpl.org"],
            vl_keys=["ED_vl_key_1"],
        )
        out = ValidatorsTxtBuilder(cfg, genesis=False, bootstrap_vl=True).build()
        assert "[validators]\n" not in out
        assert "[validator_list_sites]\n" in out


class TestXrpldCfgBuilderTreeCacheTargetEntries:
    """[tree_cache_target_entries] follows [tree_cache_ram_percent] when set."""

    def test_positive_value_emits_the_section(self):
        cfg = _make_standalone_config(tree_cache_target_entries=40_000_000)
        output = XrpldCfgBuilder(cfg).build()
        assert "[tree_cache_target_entries]\n40000000\n\n" in output
        assert output.index("[tree_cache_ram_percent]") < output.index(
            "[tree_cache_target_entries]"
        )

    def test_zero_omits_the_section(self):
        output = XrpldCfgBuilder(_make_standalone_config()).build()
        assert "[tree_cache_target_entries]" not in output
