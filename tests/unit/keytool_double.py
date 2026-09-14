"""FakeKeyTool: KeyTool with the validator-keys subprocess replaced. Key files,
tokens, manifests and the signed list are written with fixed contents; the file
helpers, the unsigned list and publish_list run for real."""

from __future__ import annotations

import json
import os

from xrpld_lab.keytool import KeyTool

# Key pairs produced by validator-keys create_keys; the hex is what
# xrpl.core.addresscodec.decode_node_public_key returns for the base58.
PUBLIC_KEYS = {
    "vl": "nHU14dDXXxUuXaGByd3mZMse1vhFpviofaT2H8LH8nND6ohSnatY",
    "vnode1": "nHDDTT7GAdRsM1iDw7RgVFvEM19zSRcsLSj6HF8yiNpgDpbVjNLW",
    "vnode2": "nHUmhsU7FXDiZd6ApLwznXjwyXFfSHKbBkj5J41pqfi7ZLaTxXYs",
}
PUBLIC_KEYS_HEX = {
    "vl": "EDC7AB75794B9BE7A90E547DC39861C583E8D1D1D799843B6D02BBA5AF05CCA8D8",
    "vnode1": "EDF54578B4F0799852CA5A92FF78FA9016DF12D1A1BFCDA530903F1CEA6E0D75DF",
    "vnode2": "EDB95182013C478522FB6331EBE2520C693FFB534F4335BF988856CDF3A21B95E7",
}


class FakeKeyTool(KeyTool):
    def __init__(self, cluster_dir: str, binary_path: str = "", image: str = ""):
        self.cluster_dir = os.path.abspath(cluster_dir)
        self.command = ["fake-validator-keys"]
        self.binary_path = binary_path
        self.image = image

    @staticmethod
    def _name(keyfile: str) -> str:
        """``keystore/<name>/key.json`` -> ``<name>``."""
        return keyfile.split("/")[1]

    def create_keys(self, keyfile: str) -> None:
        name = self._name(keyfile)
        self.write_text(
            keyfile,
            json.dumps(
                {
                    "key_type": "ed25519",
                    "public_key": PUBLIC_KEYS[name],
                    "secret_key": f"secret-{name}",
                    "token_sequence": 0,
                    "revoked": False,
                }
            ),
        )

    def create_token(self, keyfile, key_type=None, out=None) -> str:
        return self._token_block(f"token-{self._name(keyfile)}-{key_type}", out)

    def set_domain(self, keyfile, domain, out=None) -> str:
        return self._token_block(f"token-{self._name(keyfile)}", out)

    def _token_block(self, token: str, out) -> str:
        block = f"# validator public key: x\n\n[validator_token]\n{token}\n"
        if out:
            self.write_text(out, block)
        return token

    def attest_domain(self, keyfile: str) -> str:
        return f"00{self._name(keyfile).encode().hex().upper()}"

    def show_manifest(self, keyfile: str) -> str:
        return f"manifest-{self._name(keyfile)}"

    def sign_list(self, unsigned_path: str, token_file: str, out: str) -> None:
        unsigned = json.loads(self.read_text(unsigned_path))
        signed = {
            "public_key": PUBLIC_KEYS_HEX["vl"],
            "sequence": unsigned["sequence"],
            "manifests": [v["manifest"] for v in unsigned["validators"]],
            "token_file": token_file,
        }
        self.write_text(out, json.dumps(signed))

    def verify_list(self, vl_path, validators=None, expected_key=None) -> dict:
        return {"ok": True, "errors": []}
