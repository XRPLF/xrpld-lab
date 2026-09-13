#!/usr/bin/env python
# coding: utf-8

import pytest
from xrpld_lab.models import Protocol
from xrpld_lab.protocol import (
    ProtocolSpec,
    XRPL,
    get_spec,
)


class TestGetSpec:
    """Test get_spec returns the correct ProtocolSpec for each protocol."""

    def test_get_spec_xrpl(self):
        spec = get_spec(Protocol.XRPL)
        assert spec is XRPL

    def test_unknown_protocol_raises_value_error(self):
        with pytest.raises(ValueError, match="No protocol spec for 'xahau'"):
            get_spec("xahau")


class TestXrplSpec:
    """Test XRPL protocol spec values."""

    def test_daemon_name(self):
        assert XRPL.daemon_name == "xrpld"

    def test_config_filename(self):
        assert XRPL.config_filename == "xrpld.cfg"

    def test_github_owner(self):
        assert XRPL.github_owner == "XRPLF"

    def test_github_repo(self):
        assert XRPL.github_repo == "rippled"

    def test_feature_paths(self):
        assert isinstance(XRPL.feature_paths, list)
        assert len(XRPL.feature_paths) >= 1
        assert "include/xrpl/protocol/detail/features.macro" in XRPL.feature_paths

    def test_entrypoint_file(self):
        assert XRPL.entrypoint_file == "xrpl.entrypoint"

    def test_network_entrypoint_file(self):
        assert XRPL.network_entrypoint_file == "network.entrypoint"

    def test_amendment_majority_time(self):
        assert XRPL.amendment_majority_time == "15 minutes"

    def test_default_build_server(self):
        assert XRPL.default_build_server == "rippleci"

    def test_default_build_version(self):
        assert XRPL.default_build_version == "3.3.0"

    def test_default_network_id(self):
        assert XRPL.default_network_id == 1025

    def test_default_standalone_network_id(self):
        assert XRPL.default_standalone_network_id == 1

    def test_default_vl_key(self):
        assert XRPL.default_vl_key == (
            "ED87E0EA91AAFFA130B78B75D2CC3E53202AA1BD8AB3D5E7BAC530C8440E328501"
        )


class TestProtocolSpecFrozen:
    """Test that ProtocolSpec instances are immutable."""

    def test_frozen(self):
        with pytest.raises(AttributeError):
            XRPL.daemon_name = "something_else"
