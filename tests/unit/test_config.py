#!/usr/bin/env python
# coding: utf-8

"""Tests for xrpld_lab.config -- INI-style config parsing and merging."""

import json
import os

import pytest
import yaml
from unittest.mock import patch, Mock

import requests

from xrpld_lab.config import (
    parse_xrpld_cfg,
    load_overrides_file,
    apply_overrides,
    _deep_merge,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def resolver():
    return SourceResolver()


@pytest.fixture
def xrpl_spec():
    return ProtocolSpec(
        name="xrpl",
        daemon_name="xrpld",
        config_filename="xrpld.cfg",
        github_owner="XRPLF",
        github_repo="rippled",
        feature_paths=[
            "include/xrpl/protocol/detail/features.macro",
            "src/libxrpl/protocol/Feature.cpp",
        ],
        config_paths=[
            "cfg/rippled-example.cfg",
            "cfg/xrpld-example.cfg",
        ],
        entrypoint_file="xrpl.entrypoint",
        network_entrypoint_file="network.entrypoint",
        amendment_majority_time="15 minutes",
        default_build_server="rippleci",
        default_build_version="3.1.1",
        default_network_id=21337,
        default_standalone_network_id=1,
        default_vl_key="ED87E0EA91AAFFA130B78B75D2CC3E53202AA1BD8AB3D5E7BAC530C8440E328501",
    )


@pytest.fixture
def xrpl_http_source():
    return BuildSource(
        protocol=Protocol.XRPL,
        build_type=BuildType.BINARY,
        build_server="https://build.example.com",
        build_version="3.3.0",
        owner="XRPLF",
        repo="rippled",
    )


# ===========================================================================
# parse_xrpld_cfg
# ===========================================================================


class TestParseXrpldCfg:
    """Parse INI-style xrpld.cfg content into a structured dict."""

    def test_simple_section_single_value(self):
        content = "[node_size]\nhuge\n"
        result = parse_xrpld_cfg(content)
        assert result == {"node_size": "huge"}

    def test_section_with_key_value_pairs(self):
        content = (
            "[transaction_queue]\nledgers_in_queue = 20\nminimum_queue_size = 2000\n"
        )
        result = parse_xrpld_cfg(content)
        assert result == {
            "transaction_queue": {
                "ledgers_in_queue": "20",
                "minimum_queue_size": "2000",
            }
        }

    def test_multiple_sections(self):
        content = (
            "[node_size]\nhuge\n\n"
            "[ledger_history]\nfull\n\n"
            "[transaction_queue]\nledgers_in_queue = 20\n"
        )
        result = parse_xrpld_cfg(content)
        assert result["node_size"] == "huge"
        assert result["ledger_history"] == "full"
        assert result["transaction_queue"] == {"ledgers_in_queue": "20"}

    def test_empty_content(self):
        result = parse_xrpld_cfg("")
        assert result == {}

    def test_comments_ignored(self):
        content = "# This is a comment\n" "[node_size]\n" "# Another comment\n" "huge\n"
        result = parse_xrpld_cfg(content)
        assert result == {"node_size": "huge"}

    def test_equals_in_value(self):
        """type=NuDB should parse as key=value."""
        content = "[node_db]\ntype=NuDB\npath=/var/lib/db\n"
        result = parse_xrpld_cfg(content)
        assert result == {
            "node_db": {
                "type": "NuDB",
                "path": "/var/lib/db",
            }
        }

    def test_key_space_equals_value(self):
        """key = value (space around =) should parse correctly."""
        content = "[voting]\naccount_reserve = 1000000\nowner_reserve = 200000\n"
        result = parse_xrpld_cfg(content)
        assert result == {
            "voting": {
                "account_reserve": "1000000",
                "owner_reserve": "200000",
            }
        }

    def test_multiline_list_section(self):
        """Section with multiple lines (no =) should produce a list."""
        content = (
            "[ips_fixed]\n192.168.1.1 51235\n192.168.1.2 51335\n192.168.1.3 51435\n"
        )
        result = parse_xrpld_cfg(content)
        assert result == {
            "ips_fixed": [
                "192.168.1.1 51235",
                "192.168.1.2 51335",
                "192.168.1.3 51435",
            ]
        }

    def test_section_with_only_whitespace_lines(self):
        """Section with only whitespace should be skipped."""
        content = "[empty_section]\n\n\n[node_size]\nhuge\n"
        result = parse_xrpld_cfg(content)
        assert result["node_size"] == "huge"

    def test_repeated_list_section_appends_lines(self):
        content = "[ips_fixed]\n10.0.0.1 51235\n10.0.0.2 51235\n\n[ips_fixed]\n10.0.0.3 51235\n"
        result = parse_xrpld_cfg(content)
        assert result == {
            "ips_fixed": ["10.0.0.1 51235", "10.0.0.2 51235", "10.0.0.3 51235"]
        }

    def test_repeated_dict_section_merges_keys(self):
        content = (
            "[node_db]\ntype=NuDB\npath=/a\n\n[node_db]\npath=/b\nonline_delete=256\n"
        )
        result = parse_xrpld_cfg(content)
        assert result == {
            "node_db": {"type": "NuDB", "path": "/b", "online_delete": "256"}
        }

    def test_repeated_scalar_section_becomes_a_list(self):
        content = "[ips]\nr.ripple.com 51235\n\n[ips]\ns.ripple.com 51235\n"
        result = parse_xrpld_cfg(content)
        assert result == {"ips": ["r.ripple.com 51235", "s.ripple.com 51235"]}

    def test_repeated_section_of_mixed_shape_keeps_every_line(self):
        content = '[rpc_startup]\n{ "command": "log_level" }\n\n[rpc_startup]\nseverity = warning\n'
        result = parse_xrpld_cfg(content)
        assert result == {
            "rpc_startup": ['{ "command": "log_level" }', "severity = warning"]
        }

    def test_mixed_sections(self):
        """Mix of single value, kv pairs, and list in one config."""
        content = (
            "[node_size]\nhuge\n\n"
            "[node_db]\ntype=NuDB\npath=/var/lib/db\n\n"
            "[ips_fixed]\n10.0.0.1 51235\n10.0.0.2 51235\n"
        )
        result = parse_xrpld_cfg(content)
        assert result["node_size"] == "huge"
        assert result["node_db"] == {"type": "NuDB", "path": "/var/lib/db"}
        assert result["ips_fixed"] == ["10.0.0.1 51235", "10.0.0.2 51235"]


# ===========================================================================
# load_overrides_file
# ===========================================================================


class TestLoadOverridesFile:
    """Load config overrides from YAML or JSON files."""

    def test_load_yaml_file(self, tmp_path):
        path = str(tmp_path / "overrides.yaml")
        data = {"node_size": "medium", "voting": {"account_reserve": 500000}}
        with open(path, "w") as f:
            yaml.dump(data, f)
        result = load_overrides_file(path)
        assert result == data

    def test_load_json_file(self, tmp_path):
        path = str(tmp_path / "overrides.json")
        data = {"node_size": "small", "network_id": 12345}
        with open(path, "w") as f:
            json.dump(data, f)
        result = load_overrides_file(path)
        assert result == data

    def test_nonexistent_file_returns_empty(self):
        result = load_overrides_file("/tmp/does_not_exist_12345.yaml")
        assert result == {}

    def test_empty_file_returns_empty(self, tmp_path):
        path = str(tmp_path / "empty.yaml")
        with open(path, "w") as f:
            f.write("")
        result = load_overrides_file(path)
        assert result == {}

    def test_load_yml_extension(self, tmp_path):
        path = str(tmp_path / "overrides.yml")
        data = {"node_size": "tiny"}
        with open(path, "w") as f:
            yaml.dump(data, f)
        result = load_overrides_file(path)
        assert result == data


# ===========================================================================
# apply_overrides / _deep_merge
# ===========================================================================


RENDERED = (
    "[server]\nport_rpc_admin_local\nport_ws_public\n\n"
    "[node_size]\nhuge\n\n"
    "[transaction_queue]\nledgers_in_queue = 20\nminimum_queue_size = 2000\n\n"
    "[ips_fixed]\n10.0.0.1 51235\n10.0.0.2 51235\n\n"
)


class TestApplyOverrides:
    def test_empty_overrides_return_the_text_unchanged(self):
        assert apply_overrides(RENDERED, {}) == RENDERED

    def test_mapping_merges_into_the_section(self):
        out = apply_overrides(RENDERED, {"transaction_queue": {"ledgers_in_queue": 50}})
        expected = (
            "[transaction_queue]\nledgers_in_queue = 50\nminimum_queue_size = 2000\n\n"
        )
        assert expected in out

    def test_scalar_replaces_the_section(self):
        out = apply_overrides(RENDERED, {"node_size": "medium"})
        assert "[node_size]\nmedium\n\n" in out
        assert "huge" not in out

    def test_list_replaces_the_section(self):
        out = apply_overrides(RENDERED, {"ips_fixed": ["10.9.9.9 51235"]})
        assert "[ips_fixed]\n10.9.9.9 51235\n\n" in out
        assert "10.0.0.1" not in out

    def test_unknown_section_is_appended(self):
        out = apply_overrides(RENDERED, {"network_id": 4242})
        assert out.startswith(RENDERED)
        assert out.endswith("[network_id]\n4242\n\n")

    def test_untouched_sections_are_byte_identical(self):
        out = apply_overrides(RENDERED, {"node_size": "small"})
        assert "[server]\nport_rpc_admin_local\nport_ws_public\n\n" in out
        assert "[ips_fixed]\n10.0.0.1 51235\n10.0.0.2 51235\n\n" in out

    def test_round_trip_parses_to_the_merged_values(self):
        out = apply_overrides(
            RENDERED,
            {
                "transaction_queue": {"maximum_txn_in_ledger": 5000},
                "node_size": "medium",
            },
        )
        parsed = parse_xrpld_cfg(out)
        assert parsed["transaction_queue"] == {
            "ledgers_in_queue": "20",
            "minimum_queue_size": "2000",
            "maximum_txn_in_ledger": "5000",
        }
        assert parsed["node_size"] == "medium"


class TestDeepMerge:
    """Unit tests for the _deep_merge helper."""

    def test_overlay_wins_for_flat_keys(self):
        result = _deep_merge({"a": 1}, {"a": 2})
        assert result == {"a": 2}

    def test_nested_dicts_are_merged(self):
        base = {"d": {"x": 1, "y": 2}}
        overlay = {"d": {"y": 3, "z": 4}}
        result = _deep_merge(base, overlay)
        assert result == {"d": {"x": 1, "y": 3, "z": 4}}

    def test_base_not_mutated(self):
        base = {"a": 1}
        overlay = {"a": 2}
        _deep_merge(base, overlay)
        assert base == {"a": 1}

    def test_overlay_replaces_non_dict_with_dict(self):
        result = _deep_merge({"a": "string"}, {"a": {"nested": True}})
        assert result == {"a": {"nested": True}}


# ===========================================================================
# CLI --config_overrides
# ===========================================================================


class TestCliConfigOverrides:
    """Test --config_overrides argument in CLI subparsers."""

    def test_standalone_accepts_config_overrides(self):
        from xrpld_lab.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(
            ["up:standalone", "--config_overrides", "/tmp/my.yaml"]
        )
        assert args.config_overrides == "/tmp/my.yaml"

    def test_network_accepts_config_overrides(self):
        from xrpld_lab.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(
            ["create:network", "--config_overrides", "/tmp/my.json"]
        )
        assert args.config_overrides == "/tmp/my.json"

    def test_standalone_default_is_none(self):
        from xrpld_lab.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["up:standalone"])
        assert args.config_overrides is None

    def test_network_default_is_none(self):
        from xrpld_lab.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["create:network"])
        assert args.config_overrides is None

    def test_overrides_loaded_into_lab_config(self, tmp_path):
        from xrpld_lab.cli import _build_parser, build_lab_config

        overrides_path = str(tmp_path / "overrides.yaml")
        data = {"node_size": "small", "voting": {"account_reserve": 500000}}
        with open(overrides_path, "w") as f:
            yaml.dump(data, f)

        parser = _build_parser()
        args = parser.parse_args(
            [
                "up:standalone",
                "--protocol",
                "xrpl",
                "--config_overrides",
                overrides_path,
            ]
        )
        cfg = build_lab_config(args)
        assert cfg.config_overrides == data

    def test_no_overrides_gives_empty_dict(self):
        from xrpld_lab.cli import _build_parser, build_lab_config

        parser = _build_parser()
        args = parser.parse_args(["up:standalone", "--protocol", "xrpl"])
        cfg = build_lab_config(args)
        assert cfg.config_overrides == {}
