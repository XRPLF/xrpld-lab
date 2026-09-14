"""Tests for xrpld_lab.keytool -- the validator-keys subprocess wrapper."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from xrpld_lab import keytool
from xrpld_lab.keytool import (
    ENV_BIN,
    IMAGE_TOOL_PATH,
    LIST_LIFETIME,
    XRPL_EPOCH_OFFSET,
    KeyTool,
    node_public_key_hex,
    token_from_block,
    unsigned_list,
)

from tests.unit.keytool_double import PUBLIC_KEYS, PUBLIC_KEYS_HEX

TOKEN_FILE = """# validator public key: nHU14dDXXxUuXaGByd3mZMse1vhFpviofaT2H8LH8nND6ohSnatY

[validator_token]
eyJtYW5pZmVzdCI6IkpBQUFBQUZ4SWUzSHEzVjVTNXZucVE1VWZjT1lZY1dENk5IUjE1bUVP
MjBDdTZXdkJjeW8ySE1oN1ljcXZFSVZUY0E2dldCYnpMazhSMjd5NlAxUFFVbG9pQUc0ZWh4
MDZCMEE5In0=
"""
TOKEN = (
    "eyJtYW5pZmVzdCI6IkpBQUFBQUZ4SWUzSHEzVjVTNXZucVE1VWZjT1lZY1dENk5IUjE1bUVP"
    "MjBDdTZXdkJjeW8ySE1oN1ljcXZFSVZUY0E2dldCYnpMazhSMjd5NlAxUFFVbG9pQUc0ZWh4"
    "MDZCMEE5In0="
)
ATTESTATION_OUTPUT = (
    "The domain attestation for validator nHDD is:\n\n"
    'attestation="0007BBBDE3F8AB15"\n\n'
    "You should include it in your xrp-ledger.toml file.\n"
)
MANIFEST_OUTPUT = "Manifest #1 (Base64):\nJAAAAAFxIe3Hq3V5S5vnqQ5U\n\n"
REPORT_OK = {"ok": True, "errors": [], "blobs": [{"validators": 1}]}


# ---------------------------------------------------------------------------
# Parsers and builders
# ---------------------------------------------------------------------------


class TestTokenFromBlock:
    def test_joins_the_wrapped_lines_of_a_token_file(self):
        assert token_from_block(TOKEN_FILE) == TOKEN

    def test_skips_the_preamble_the_tool_prints_to_stdout(self):
        text = "Update xrpld.cfg file with these values:\n\n" + TOKEN_FILE
        assert token_from_block(text) == TOKEN

    def test_ignores_comment_lines_after_the_header(self):
        assert token_from_block("[validator_token]\n# note\nabc\n") == "abc"

    def test_rejects_text_without_the_section(self):
        with pytest.raises(ValueError, match=r"no \[validator_token\]"):
            token_from_block("# validator public key: x\nabc\n")

    def test_rejects_an_empty_section(self):
        with pytest.raises(ValueError, match="empty"):
            token_from_block("[validator_token]\n\n")


class TestNodePublicKeyHex:
    @pytest.mark.parametrize("name", ["vl", "vnode1", "vnode2"])
    def test_matches_decode_node_public_key(self, name):
        assert node_public_key_hex(PUBLIC_KEYS[name]) == PUBLIC_KEYS_HEX[name]

    def test_is_33_uppercase_hex_bytes(self):
        value = node_public_key_hex(PUBLIC_KEYS["vnode1"])
        assert len(bytes.fromhex(value)) == 33
        assert value == value.upper()


class TestUnsignedList:
    def test_sequence_is_the_signing_time_and_expiration_30_days_on(self):
        entries = [{"validation_public_key": "ED01", "manifest": "m"}]
        doc = unsigned_list(entries, now=1_800_000_000)
        assert doc == {
            "sequence": 1_800_000_000,
            "expiration": 1_800_000_000 + LIST_LIFETIME - XRPL_EPOCH_OFFSET,
            "validators": entries,
        }
        assert doc["validators"] is not entries

    def test_defaults_to_the_current_time(self, monkeypatch):
        monkeypatch.setattr(keytool.time, "time", lambda: 1_800_000_000.7)
        assert unsigned_list([])["sequence"] == 1_800_000_000


# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------


class TestResolution:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch):
        monkeypatch.delenv(ENV_BIN, raising=False)
        monkeypatch.setattr(keytool.shutil, "which", lambda name: None)
        monkeypatch.setattr(keytool.os, "getuid", lambda: 501, raising=False)
        monkeypatch.setattr(keytool.os, "getgid", lambda: 20, raising=False)

    @staticmethod
    def _accept(monkeypatch, *commands):
        """Only the given commands answer ``--version``."""
        accepted = [list(c) for c in commands]
        monkeypatch.setattr(KeyTool, "_works", staticmethod(lambda c: c in accepted))

    def test_env_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv(ENV_BIN, "/opt/bin/validator-keys")
        monkeypatch.setattr(keytool.shutil, "which", lambda name: "/usr/bin/vk")
        self._accept(monkeypatch, ["/opt/bin/validator-keys"], ["/usr/bin/vk"])
        tool = KeyTool(tmp_path, binary_path="/build/xrpld", image="img")
        assert tool.command == ["/opt/bin/validator-keys"]

    def test_env_that_does_not_run_is_an_error(self, monkeypatch, tmp_path):
        monkeypatch.setenv(ENV_BIN, "/nope/validator-keys")
        self._accept(monkeypatch)
        with pytest.raises(RuntimeError, match="VALIDATOR_KEYS_BIN=/nope"):
            KeyTool(tmp_path)

    def test_tool_beside_the_binary_comes_before_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(keytool.shutil, "which", lambda name: "/usr/bin/vk")
        self._accept(monkeypatch, ["/build/validator-keys"], ["/usr/bin/vk"])
        tool = KeyTool(tmp_path, binary_path="/build/xrpld")
        assert tool.command == ["/build/validator-keys"]

    def test_path_comes_before_docker(self, monkeypatch, tmp_path):
        monkeypatch.setattr(keytool.shutil, "which", lambda name: "/usr/bin/vk")
        docker = KeyTool._docker_command.__get__(_bare(tmp_path))("img")
        self._accept(monkeypatch, ["/usr/bin/vk"], docker)
        tool = KeyTool(tmp_path, binary_path="/build/xrpld", image="img")
        assert tool.command == ["/usr/bin/vk"]

    def test_docker_image_is_the_last_resort(self, monkeypatch, tmp_path):
        docker = KeyTool._docker_command.__get__(_bare(tmp_path))("rippleci/xrpld:1")
        self._accept(monkeypatch, docker)
        tool = KeyTool(tmp_path, binary_path="/build/xrpld", image="rippleci/xrpld:1")
        assert tool.command == docker
        assert docker[:4] == ["docker", "run", "--rm", "--user"]
        assert docker[4] == "501:20"
        assert docker[5:] == [
            "-v",
            f"{os.path.abspath(tmp_path)}:/work",
            "-w",
            "/work",
            "--entrypoint",
            IMAGE_TOOL_PATH,
            "rippleci/xrpld:1",
        ]

    def test_nothing_found_explains_how_to_provide_the_tool(self, monkeypatch, tmp_path):
        self._accept(monkeypatch)
        with pytest.raises(RuntimeError, match="VALIDATOR_KEYS_BIN.*validator_keys=ON"):
            KeyTool(tmp_path, binary_path="/build/xrpld", image="img")

    def test_no_image_means_no_docker_candidate(self, monkeypatch, tmp_path):
        self._accept(monkeypatch)
        assert _bare(tmp_path)._candidates("", "") == []

    def test_works_is_a_zero_exit_from_version(self, monkeypatch):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "validator-keys 0.4.0", "")

        monkeypatch.setattr(keytool.subprocess, "run", run)
        assert KeyTool._works(["vk"]) is True
        assert calls == [["vk", "--version"]]

    def test_works_is_false_on_a_non_zero_exit(self, monkeypatch):
        monkeypatch.setattr(
            keytool.subprocess,
            "run",
            lambda command, **kw: subprocess.CompletedProcess(command, 1, "", "bad"),
        )
        assert KeyTool._works(["vk"]) is False

    def test_works_is_false_when_the_command_cannot_start(self, monkeypatch):
        def run(command, **kwargs):
            raise FileNotFoundError(command[0])

        monkeypatch.setattr(keytool.subprocess, "run", run)
        assert KeyTool._works(["missing"]) is False


def _bare(cluster_dir) -> KeyTool:
    """A KeyTool without resolution, for calling its instance helpers."""
    tool = KeyTool.__new__(KeyTool)
    tool.cluster_dir = os.path.abspath(cluster_dir)
    tool.command = ["vk"]
    return tool


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


class Runner:
    """Records every subprocess.run call and answers from ``responses``: a
    list of (stdout, returncode[, stderr]) consumed in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        stdout, code, *rest = self.responses.pop(0)
        stderr = rest[0] if rest else ""
        return subprocess.CompletedProcess(command, code, stdout, stderr)

    def args(self, index=0):
        """The tool arguments of one call, without the binary."""
        return self.calls[index][0][1:]


@pytest.fixture
def tool(tmp_path):
    return _bare(tmp_path)


@pytest.fixture
def runner(monkeypatch):
    def install(*responses):
        r = Runner(responses)
        monkeypatch.setattr(keytool.subprocess, "run", r)
        return r

    return install


class TestRun:
    def test_runs_in_the_cluster_directory(self, tool, runner):
        r = runner(("out", 0))
        result = tool._run("--keyfile", "k", "create_keys")
        assert result.stdout == "out"
        command, kwargs = r.calls[0]
        assert command == ["vk", "--keyfile", "k", "create_keys"]
        assert kwargs["cwd"] == tool.cluster_dir
        assert kwargs["capture_output"] and kwargs["text"]

    def test_failure_raises_with_the_tools_message(self, tool, runner):
        runner(("", 1, "Failed to open file: k\n"))
        with pytest.raises(RuntimeError, match="create_keys failed: Failed to open"):
            tool._run("--keyfile", "k", "create_keys")

    def test_failure_message_falls_back_to_stdout(self, tool, runner):
        runner(("Refusing to overwrite existing key file: k\n", 1))
        with pytest.raises(RuntimeError, match="Refusing to overwrite"):
            tool._run("--keyfile", "k", "create_keys")

    def test_unchecked_run_returns_the_failure(self, tool, runner):
        runner(("{}", 1))
        assert tool._run("verify_list", "x", check=False).returncode == 1


class TestKeysAndTokens:
    def test_create_keys_makes_the_keystore_directory(self, tool, runner):
        r = runner(("Validator keys stored in keystore/vl/key.json\n", 0))
        tool.create_keys("keystore/vl/key.json")
        assert os.path.isdir(os.path.join(tool.cluster_dir, "keystore", "vl"))
        assert r.args() == ["--keyfile", "keystore/vl/key.json", "create_keys"]

    def test_create_token_to_a_file_reads_the_token_back(self, tool, runner):
        out = "keystore/vl/token.txt"
        tool.write_text(out, TOKEN_FILE)
        r = runner((f"[validator_token] written to {out}\n", 0))
        token = tool.create_token("keystore/vl/key.json", key_type="ed25519", out=out)
        assert token == TOKEN
        assert r.args() == [
            "--keyfile",
            "keystore/vl/key.json",
            "create_token",
            "--token-key-type",
            "ed25519",
            "--out",
            out,
        ]

    def test_create_token_to_stdout_parses_the_block(self, tool, runner):
        r = runner(("Update xrpld.cfg file with these values:\n\n" + TOKEN_FILE, 0))
        assert tool.create_token("keystore/vnode1/key.json") == TOKEN
        assert r.args() == ["--keyfile", "keystore/vnode1/key.json", "create_token"]

    def test_set_domain_returns_the_new_token(self, tool, runner):
        out = "keystore/vnode1/token.txt"
        tool.write_text(out, TOKEN_FILE)
        r = runner(("The domain name has been set to: xrpl.vnode1\n", 0))
        token = tool.set_domain("keystore/vnode1/key.json", "xrpl.vnode1", out=out)
        assert token == TOKEN
        assert r.args() == [
            "--keyfile",
            "keystore/vnode1/key.json",
            "set_domain",
            "xrpl.vnode1",
            "--out",
            out,
        ]

    def test_attest_domain_returns_the_hex(self, tool, runner):
        r = runner((ATTESTATION_OUTPUT, 0))
        assert tool.attest_domain("keystore/vnode1/key.json") == "0007BBBDE3F8AB15"
        assert r.args() == ["--keyfile", "keystore/vnode1/key.json", "attest_domain"]

    def test_attest_domain_without_an_attestation_is_an_error(self, tool, runner):
        runner(("No domain set.\n", 0))
        with pytest.raises(RuntimeError, match="no attestation"):
            tool.attest_domain("keystore/vnode1/key.json")

    def test_show_manifest_returns_the_base64(self, tool, runner):
        r = runner((MANIFEST_OUTPUT, 0))
        assert tool.show_manifest("keystore/vl/key.json") == "JAAAAAFxIe3Hq3V5S5vnqQ5U"
        assert r.args() == [
            "--keyfile",
            "keystore/vl/key.json",
            "show_manifest",
            "base64",
        ]

    def test_show_manifest_without_a_manifest_is_an_error(self, tool, runner):
        runner(("No manifest generated yet.\n", 0))
        with pytest.raises(RuntimeError, match="no manifest"):
            tool.show_manifest("keystore/vl/key.json")


class TestFiles:
    def test_read_keys_is_none_when_the_file_is_missing(self, tool):
        assert tool.read_keys("keystore/vl/key.json") is None

    def test_read_keys_returns_the_json(self, tool):
        tool.write_text("keystore/vl/key.json", json.dumps({"key_type": "ed25519"}))
        assert tool.read_keys("keystore/vl/key.json") == {"key_type": "ed25519"}

    def test_public_key_hex_decodes_the_key_file(self, tool):
        keyfile = "keystore/vnode1/key.json"
        tool.write_text(keyfile, json.dumps({"public_key": PUBLIC_KEYS["vnode1"]}))
        assert tool.public_key_hex(keyfile) == PUBLIC_KEYS_HEX["vnode1"]

    def test_read_token_joins_the_block(self, tool):
        tool.write_text("keystore/vnode1/token.txt", TOKEN_FILE)
        assert tool.read_token("keystore/vnode1/token.txt") == TOKEN

    def test_read_manifest_strips_the_newline(self, tool):
        tool.write_text("keystore/vnode1/manifest.txt", "JAAA\n")
        assert tool.read_manifest("keystore/vnode1/manifest.txt") == "JAAA"


class TestLists:
    def test_sign_list_arguments(self, tool, runner):
        r = runner(("Written to vl/vl.json\n", 0))
        tool.sign_list("vl/unsigned.json", "keystore/vl/token.txt", "vl/vl.json")
        assert r.args() == [
            "sign_list",
            "vl/unsigned.json",
            "--token-file",
            "keystore/vl/token.txt",
            "--out",
            "vl/vl.json",
        ]

    def test_verify_list_returns_the_report(self, tool, runner):
        r = runner((json.dumps(REPORT_OK), 0))
        report = tool.verify_list(
            "vl/vl.json", validators="vl/unsigned.json", expected_key="ED01"
        )
        assert report == REPORT_OK
        assert r.args() == [
            "verify_list",
            "vl/vl.json",
            "--validators",
            "vl/unsigned.json",
            "--expected-key",
            "ED01",
        ]

    def test_verify_list_without_options(self, tool, runner):
        r = runner((json.dumps(REPORT_OK), 0))
        tool.verify_list("vl/vl.json")
        assert r.args() == ["verify_list", "vl/vl.json"]

    def test_verify_list_failure_names_the_errors(self, tool, runner):
        report = {"ok": False, "errors": ["the master key is not the expected key"]}
        runner((json.dumps(report), 1))
        with pytest.raises(RuntimeError, match="vl/vl.json failed verification: the"):
            tool.verify_list("vl/vl.json")

    def test_verify_list_non_zero_exit_without_errors(self, tool, runner):
        runner((json.dumps({"ok": True, "errors": []}), 1))
        with pytest.raises(RuntimeError, match="not ok"):
            tool.verify_list("vl/vl.json")

    def test_verify_list_without_a_report_is_an_error(self, tool, runner):
        runner(("", 1, "Failed to open file: vl/vl.json\n"))
        with pytest.raises(RuntimeError, match="verify_list failed: Failed to open"):
            tool.verify_list("vl/vl.json")

    def test_publish_list_writes_signs_and_verifies(self, tool, runner, monkeypatch):
        monkeypatch.setattr(keytool.time, "time", lambda: 1_800_000_000.0)
        r = runner(("Written to vl/vl.json\n", 0), (json.dumps(REPORT_OK), 0))
        entries = [{"validation_public_key": PUBLIC_KEYS_HEX["vnode1"], "manifest": "m"}]
        assert tool.publish_list(entries) == REPORT_OK
        unsigned = json.load(open(os.path.join(tool.cluster_dir, "vl", "unsigned.json")))
        assert unsigned == unsigned_list(entries, now=1_800_000_000)
        assert r.args(0) == [
            "sign_list",
            "vl/unsigned.json",
            "--token-file",
            "keystore/vl/token.txt",
            "--out",
            "vl/vl.json",
        ]
        assert r.args(1) == [
            "verify_list",
            "vl/vl.json",
            "--validators",
            "vl/unsigned.json",
        ]
