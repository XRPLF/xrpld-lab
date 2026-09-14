"""End-to-end run of the real validator-keys tool: keys, token, domain, list
signing and verification. Runs only when VALIDATOR_KEYS_BIN names the tool."""

from __future__ import annotations

import json
import os

import pytest

from xrpld_lab.keytool import ENV_BIN, KeyTool

pytestmark = pytest.mark.skipif(
    not os.environ.get(ENV_BIN), reason=f"{ENV_BIN} not set"
)


@pytest.fixture
def tool(tmp_path):
    return KeyTool(tmp_path)


def test_publisher_and_validator_keys_sign_a_verifiable_list(tool):
    tool.create_keys("keystore/vl/key.json")
    vl_token = tool.create_token(
        "keystore/vl/key.json", key_type="ed25519", out="keystore/vl/token.txt"
    )
    assert tool.read_token("keystore/vl/token.txt") == vl_token
    vl_keys = tool.read_keys("keystore/vl/key.json")
    assert vl_keys["key_type"] == "ed25519"
    assert vl_keys["token_sequence"] == 1
    vl_manifest = tool.show_manifest("keystore/vl/key.json")
    assert vl_manifest.startswith("JAAAAAF")

    tool.create_keys("keystore/vnode1/key.json")
    token = tool.set_domain(
        "keystore/vnode1/key.json",
        "xrpl.vnode1.transia.co",
        out="keystore/vnode1/token.txt",
    )
    assert tool.read_token("keystore/vnode1/token.txt") == token
    assert tool.read_keys("keystore/vnode1/key.json")["domain"] == (
        "xrpl.vnode1.transia.co"
    )
    attestation = tool.attest_domain("keystore/vnode1/key.json")
    assert len(bytes.fromhex(attestation)) == 64
    manifest = tool.show_manifest("keystore/vnode1/key.json")
    public_key_hex = tool.public_key_hex("keystore/vnode1/key.json")
    assert len(bytes.fromhex(public_key_hex)) == 33

    report = tool.publish_list(
        [{"validation_public_key": public_key_hex, "manifest": manifest}]
    )
    assert report["ok"] is True
    assert report["errors"] == []
    assert report["blobs"][0]["validators"] == 1
    assert report["public_key"] == tool.public_key_hex("keystore/vl/key.json")

    signed = json.load(open(os.path.join(tool.cluster_dir, "vl", "vl.json")))
    assert set(signed) >= {"blob", "manifest", "public_key", "signature", "version"}
    unsigned = json.load(open(os.path.join(tool.cluster_dir, "vl", "unsigned.json")))
    assert report["blobs"][0]["sequence"] == unsigned["sequence"]


def test_verify_list_rejects_the_wrong_master_key(tool):
    tool.create_keys("keystore/vl/key.json")
    tool.create_token(
        "keystore/vl/key.json", key_type="ed25519", out="keystore/vl/token.txt"
    )
    tool.create_keys("keystore/vnode1/key.json")
    tool.set_domain(
        "keystore/vnode1/key.json",
        "xrpl.vnode1.transia.co",
        out="keystore/vnode1/token.txt",
    )
    tool.publish_list(
        [
            {
                "validation_public_key": tool.public_key_hex(
                    "keystore/vnode1/key.json"
                ),
                "manifest": tool.show_manifest("keystore/vnode1/key.json"),
            }
        ]
    )
    with pytest.raises(RuntimeError, match="not the expected key"):
        tool.verify_list(
            "vl/vl.json",
            expected_key=tool.public_key_hex("keystore/vnode1/key.json"),
        )


def test_create_keys_refuses_to_overwrite(tool):
    tool.create_keys("keystore/vl/key.json")
    with pytest.raises(RuntimeError, match="Refusing to overwrite"):
        tool.create_keys("keystore/vl/key.json")
