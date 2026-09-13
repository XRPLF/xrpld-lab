#!/usr/bin/env python
# coding: utf-8

import json
import hashlib
import pytest

from xrpld_lab.amendments import (
    parse_supported,
    get_feature_lines_from_content,
    get_feature_lines_from_path,
    parse_amendments,
    convert_to_list_of_hashes,
    update_genesis,
)
from xrpld_lab.ledger_generator import generate


def _amendment_hash(name: str) -> str:
    """Helper: compute the expected SHA-512 half hash for an amendment name."""
    return hashlib.sha512(name.encode("utf-8")).digest().hex().upper()[:64]


# ---------------------------------------------------------------------------
# parse_supported
# ---------------------------------------------------------------------------


class TestParseSupported:
    """Test the parse_supported function that converts string values to booleans."""

    def test_no_returns_false(self):
        assert parse_supported("no") is False

    def test_yes_returns_true(self):
        assert parse_supported("yes") is True

    def test_any_other_value_returns_true(self):
        assert parse_supported("maybe") is True
        assert parse_supported("") is True
        assert parse_supported("1") is True


# ---------------------------------------------------------------------------
# get_feature_lines_from_content
# ---------------------------------------------------------------------------


class TestGetFeatureLinesFromContent:
    """Test parsing feature lines from bytes content."""

    def test_basic_decode(self):
        content = b"Line 1\nLine 2\nLine 3"
        result = get_feature_lines_from_content(content)
        assert result == ["Line 1", "Line 2", "Line 3"]

    def test_empty_content(self):
        content = b""
        result = get_feature_lines_from_content(content)
        assert result == []

    def test_unicode_handling(self):
        content = "Feature ✅\nAnother line".encode("utf-8")
        result = get_feature_lines_from_content(content)
        assert result == ["Feature ✅", "Another line"]


# ---------------------------------------------------------------------------
# get_feature_lines_from_path
# ---------------------------------------------------------------------------


class TestGetFeatureLinesFromPath:
    """Test reading feature lines from a file path."""

    def test_reads_file(self, tmp_path):
        f = tmp_path / "features.cpp"
        f.write_text("XRPL_FEATURE(Foo, Supported::yes, DefaultVote::yes,\nline2\n")
        result = get_feature_lines_from_path(str(f))
        assert len(result) == 2
        assert "XRPL_FEATURE" in result[0]

    def test_file_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            get_feature_lines_from_path("/nonexistent/path/features.cpp")


# ---------------------------------------------------------------------------
# parse_amendments
# ---------------------------------------------------------------------------


class TestParseAmendments:
    """Test parsing amendments from C++ feature macro lines."""

    def test_xrpl_feature(self):
        lines = [
            "XRPL_FEATURE(TestFeature, Supported::yes, DefaultVote::yes,",
        ]
        result = parse_amendments(lines)
        assert "TestFeature" in result
        assert result["TestFeature"] == _amendment_hash("TestFeature")

    def test_xrpl_fix_prepends_fix(self):
        lines = [
            "XRPL_FIX(1234, Supported::yes, DefaultVote::yes,",
        ]
        result = parse_amendments(lines)
        assert "fix1234" in result
        assert result["fix1234"] == _amendment_hash("fix1234")

    def test_register_feature(self):
        lines = [
            "REGISTER_FEATURE(TestFeature, Supported::yes, DefaultVote::yes,",
        ]
        result = parse_amendments(lines)
        assert "TestFeature" in result
        assert result["TestFeature"] == _amendment_hash("TestFeature")

    def test_register_fix(self):
        lines = [
            "REGISTER_FIX(FixName, Supported::yes, DefaultVote::yes,",
        ]
        result = parse_amendments(lines)
        assert "FixName" in result
        assert result["FixName"] == _amendment_hash("FixName")

    def test_unsupported_amendments_filtered(self):
        lines = [
            "XRPL_FEATURE(SupportedFeature, Supported::yes, DefaultVote::yes,",
            "XRPL_FEATURE(UnsupportedFeature, Supported::no, DefaultVote::yes,",
        ]
        result = parse_amendments(lines)
        assert "SupportedFeature" in result
        # The new implementation correctly filters unsupported amendments
        assert "UnsupportedFeature" not in result

    def test_multiple_features(self):
        lines = [
            "XRPL_FEATURE(Feature1, Supported::yes, DefaultVote::yes,",
            "XRPL_FEATURE(Feature2, Supported::yes, DefaultVote::no,",
            "XRPL_FIX(1000, Supported::yes, DefaultVote::yes,",
        ]
        result = parse_amendments(lines)
        assert len(result) == 3
        assert "Feature1" in result
        assert "Feature2" in result
        assert "fix1000" in result

    def test_non_feature_lines_ignored(self):
        lines = [
            "// This is a comment",
            "XRPL_FEATURE(ValidFeature, Supported::yes, DefaultVote::yes,",
            "Some other code",
            "",
        ]
        result = parse_amendments(lines)
        assert len(result) == 1
        assert "ValidFeature" in result

    def test_mixed_xrpl_and_register_macros(self):
        lines = [
            "XRPL_FEATURE(XrplFeature, Supported::yes, DefaultVote::yes,",
            "XRPL_FIX(1234, Supported::yes, DefaultVote::yes,",
            "REGISTER_FEATURE(LegacyFeature, Supported::yes, DefaultVote::yes,",
            "REGISTER_FIX(LegacyFix, Supported::yes, DefaultVote::yes,",
        ]
        result = parse_amendments(lines)
        assert len(result) == 4
        assert "XrplFeature" in result
        assert "fix1234" in result
        assert "LegacyFeature" in result
        assert "LegacyFix" in result

    def test_hash_format_uppercase_hex_64_chars(self):
        lines = [
            "XRPL_FEATURE(SomeFeature, Supported::yes, DefaultVote::yes,",
        ]
        result = parse_amendments(lines)
        h = result["SomeFeature"]
        assert len(h) == 64
        assert h == h.upper()
        # Verify it's valid hex
        int(h, 16)

    def test_empty_lines(self):
        result = parse_amendments([])
        assert result == {}


# ---------------------------------------------------------------------------
# convert_to_list_of_hashes
# ---------------------------------------------------------------------------


class TestConvertToListOfHashes:
    """Test converting feature dict to list of hashes."""

    def test_empty_dict(self):
        assert convert_to_list_of_hashes({}) == []

    def test_single_entry(self):
        features = {"Feature1": "ABCD1234"}
        result = convert_to_list_of_hashes(features)
        assert result == ["ABCD1234"]

    def test_multiple_entries(self):
        features = {
            "Feature1": "ABCD1234",
            "Feature2": "EFGH5678",
            "Feature3": "IJKL9012",
        }
        result = convert_to_list_of_hashes(features)
        assert len(result) == 3
        assert "ABCD1234" in result
        assert "EFGH5678" in result
        assert "IJKL9012" in result


# ---------------------------------------------------------------------------
# update_genesis
# ---------------------------------------------------------------------------


class TestUpdateGenesis:
    """Test updating genesis JSON with amendment hashes."""

    def _make_genesis(self, tmp_path, amendments=None):
        """Create a minimal genesis JSON for testing."""
        genesis = {
            "ledger": {
                "accountState": [
                    {
                        "Account": "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
                        "LedgerEntryType": "AccountRoot",
                    },
                    {
                        "Amendments": amendments if amendments is not None else [],
                        "LedgerEntryType": "Amendments",
                    },
                    {
                        "BaseFee": "A",
                        "LedgerEntryType": "FeeSettings",
                    },
                ]
            }
        }
        path = tmp_path / "genesis.xrpl.json"
        path.write_text(json.dumps(genesis))
        return str(path)

    def test_updates_amendments_list(self, tmp_path):
        genesis_path = self._make_genesis(tmp_path)
        features = {
            "Feature1": "HASH1",
            "Feature2": "HASH2",
        }
        result = update_genesis(features, "xrpl", genesis_path=genesis_path)
        for entry in result["ledger"]["accountState"]:
            if "Amendments" in entry:
                assert entry["Amendments"] == ["HASH1", "HASH2"]
                break
        else:
            pytest.fail("No Amendments entry found in accountState")

    def test_raises_when_no_amendments_entry(self, tmp_path):
        """A genesis template with no Amendments entry is malformed -> fail loud."""
        genesis = {
            "ledger": {
                "accountState": [
                    {
                        "Account": "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",
                        "LedgerEntryType": "AccountRoot",
                    },
                ]
            }
        }
        path = tmp_path / "genesis.xrpl.json"
        path.write_text(json.dumps(genesis))
        features = {"Feature1": "HASH1"}
        with pytest.raises(RuntimeError, match="Amendments entry not found"):
            update_genesis(features, "xrpl", genesis_path=str(path))

    def test_preserves_other_account_state_entries(self, tmp_path):
        genesis_path = self._make_genesis(tmp_path)
        features = {"Feature1": "HASH1"}
        result = update_genesis(features, "xrpl", genesis_path=genesis_path)
        # AccountRoot and FeeSettings should still be present
        types = [e["LedgerEntryType"] for e in result["ledger"]["accountState"]]
        assert "AccountRoot" in types
        assert "FeeSettings" in types

    def test_replaces_existing_amendments(self, tmp_path):
        genesis_path = self._make_genesis(
            tmp_path, amendments=["OLD_HASH1", "OLD_HASH2"]
        )
        features = {"NewFeature": "NEW_HASH"}
        result = update_genesis(features, "xrpl", genesis_path=genesis_path)
        for entry in result["ledger"]["accountState"]:
            if "Amendments" in entry:
                assert entry["Amendments"] == ["NEW_HASH"]
                assert "OLD_HASH1" not in entry["Amendments"]
                break

    def test_default_genesis_path(self):
        """When no genesis_path is provided, falls back to package default."""
        # Non-empty features: update_genesis refuses to build a genesis with no
        # amendments; here we exercise path defaulting, not the empty-features guard.
        features = {"Feature1": "HASH1"}
        # Should load genesis.xrpl.json from the xrpld_lab package directory
        result = update_genesis(features, "xrpl")
        assert "ledger" in result
        assert "accountState" in result["ledger"]

    def test_rejects_empty_features(self):
        """No amendments at all is an error -> fail loud, never a silent empty genesis."""
        with pytest.raises(RuntimeError, match="No features found"):
            update_genesis({}, "xrpl")


class TestParseAmendmentsMalformedMacros:
    """A macro keyword with no argument list is skipped, not raised on."""

    @pytest.mark.parametrize(
        "line", ["XRPL_FIX", "XRPL_FEATURE", "REGISTER_FIX", "REGISTER_FEATURE"]
    )
    def test_macro_without_arguments_is_skipped(self, line):
        assert parse_amendments([line]) == {}

    def test_malformed_line_does_not_drop_the_rest(self):
        lines = [
            "XRPL_FIX",
            "REGISTER_FEATURE",
            "XRPL_FEATURE(Good, Supported::yes, DefaultVote::yes,",
        ]
        assert parse_amendments(lines) == {"Good": _amendment_hash("Good")}


class TestUpdateGenesisPreload:
    """preload_entries are merged into the template and XRP stays conserved."""

    def test_preload_entries_are_merged_and_xrp_is_conserved(self):
        entries, _ = generate(3, balance_drops="2500000000", num_trustlines=2)

        result = update_genesis({"Feature1": "HASH1"}, "xrpl", preload_entries=entries)

        state = result["ledger"]["accountState"]
        indexes = {e.get("index") for e in state}
        assert all(e["index"] in indexes for e in entries)
        roots = [e for e in state if e.get("LedgerEntryType") == "AccountRoot"]
        assert len(roots) == 4
        assert sum(int(e["Balance"]) for e in roots) == int(
            result["ledger"]["total_coins"]
        )
        assert sum(1 for e in state if e.get("LedgerEntryType") == "RippleState") == 2
        amendments = [e for e in state if e.get("LedgerEntryType") == "Amendments"]
        assert amendments[0]["Amendments"] == ["HASH1"]

    def test_no_preload_leaves_the_template_state_alone(self):
        result = update_genesis({"Feature1": "HASH1"}, "xrpl", preload_entries=None)
        roots = [
            e
            for e in result["ledger"]["accountState"]
            if e.get("LedgerEntryType") == "AccountRoot"
        ]
        assert len(roots) == 1
