"""KeyTool -- runs rippled's ``validator-keys`` tool against a cluster's keystore.

Key files, tokens, domain attestations, manifests and signed validator lists
all come from the tool; this module locates it, runs it with the cluster
directory as the working directory, and parses what it prints.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from typing import Dict, List, Optional

from xrpl.core.addresscodec import decode_node_public_key

# Seconds between the unix epoch and the XRP Ledger epoch (2000-01-01).
XRPL_EPOCH_OFFSET = 946684800
# A signed list is valid for 30 days from signing.
LIST_LIFETIME = 30 * 86400
# Where multibranch-builder places the tool inside an xrpld image.
IMAGE_TOOL_PATH = "/opt/xrpld/bin/validator-keys"
ENV_BIN = "VALIDATOR_KEYS_BIN"
MISSING_TOOL = (
    "validator-keys not found: set VALIDATOR_KEYS_BIN to the tool, put it on "
    "PATH or beside the xrpld binary (build rippled with -Dvalidator_keys=ON), "
    "or use an xrpld image that ships it at " + IMAGE_TOOL_PATH
)

_ATTESTATION = re.compile(r'attestation="([0-9A-Fa-f]+)"')
_MANIFEST = re.compile(r"\(Base64\):\s*\n\s*(\S+)")


def token_from_block(text: str) -> str:
    """The base64 of a ``[validator_token]`` block, its wrapped lines joined."""
    lines = [line.strip() for line in text.splitlines()]
    if "[validator_token]" not in lines:
        raise ValueError("no [validator_token] block in the token text")
    body = lines[lines.index("[validator_token]") + 1 :]
    token = "".join(line for line in body if line and not line.startswith("#"))
    if not token:
        raise ValueError("empty [validator_token] block")
    return token


def node_public_key_hex(public_key: str) -> str:
    """A base58 node public key as 33 uppercase hex bytes."""
    return decode_node_public_key(public_key).hex().upper()


def unsigned_list(validators: List[Dict[str, str]], now: Optional[int] = None) -> dict:
    """The list ``sign_list`` signs: sequence is the signing time in unix seconds,
    expiration 30 days later in XRP Ledger epoch seconds."""
    now = int(time.time()) if now is None else now
    return {
        "sequence": now,
        "expiration": now + LIST_LIFETIME - XRPL_EPOCH_OFFSET,
        "validators": list(validators),
    }


class KeyTool:
    """Runs ``validator-keys`` with ``cluster_dir`` as the working directory, so
    every path handed to it is relative to the cluster (``keystore/vl/key.json``).

    The binary is taken from ``$VALIDATOR_KEYS_BIN``, else ``validator-keys``
    beside the lab's xrpld binary, else ``validator-keys`` on PATH, else the
    tool inside the cluster's docker image.
    """

    def __init__(self, cluster_dir: str, binary_path: str = "", image: str = ""):
        self.cluster_dir = os.path.abspath(cluster_dir)
        self.command = self._resolve(binary_path, image)

    # -- binary resolution ---------------------------------------------------

    def _resolve(self, binary_path: str, image: str) -> List[str]:
        explicit = os.environ.get(ENV_BIN)
        if explicit:
            if self._works([explicit]):
                return [explicit]
            raise RuntimeError(f"{ENV_BIN}={explicit} does not run")
        for command in self._candidates(binary_path, image):
            if self._works(command):
                return command
        raise RuntimeError(MISSING_TOOL)

    def _candidates(self, binary_path: str, image: str) -> List[List[str]]:
        candidates: List[List[str]] = []
        if binary_path:
            beside = os.path.join(
                os.path.dirname(os.path.abspath(binary_path)), "validator-keys"
            )
            candidates.append([beside])
        on_path = shutil.which("validator-keys")
        if on_path:
            candidates.append([on_path])
        if image:
            candidates.append(self._docker_command(image))
        return candidates

    def _docker_command(self, image: str) -> List[str]:
        return [
            "docker",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-v",
            f"{self.cluster_dir}:/work",
            "-w",
            "/work",
            "--entrypoint",
            IMAGE_TOOL_PATH,
            image,
        ]

    @staticmethod
    def _works(command: List[str]) -> bool:
        """True when ``<command> --version`` exits 0."""
        try:
            result = subprocess.run(
                command + ["--version"], capture_output=True, text=True
            )
        except OSError:
            return False
        return result.returncode == 0

    # -- running -------------------------------------------------------------

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(
            self.command + list(args),
            cwd=self.cluster_dir,
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"validator-keys {' '.join(args)} failed: {detail}")
        return result

    def _path(self, relative: str) -> str:
        return os.path.join(self.cluster_dir, relative)

    def read_text(self, relative: str) -> str:
        with open(self._path(relative)) as f:
            return f.read()

    def write_text(self, relative: str, text: str) -> None:
        path = self._path(relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    # -- keys and tokens -----------------------------------------------------

    def create_keys(self, keyfile: str) -> None:
        os.makedirs(os.path.dirname(self._path(keyfile)), exist_ok=True)
        self._run("--keyfile", keyfile, "create_keys")

    def create_token(
        self, keyfile: str, key_type: Optional[str] = None, out: Optional[str] = None
    ) -> str:
        """Generate a token; its base64 is returned whether printed or written to
        ``out``."""
        args = ["--keyfile", keyfile, "create_token"]
        if key_type:
            args += ["--token-key-type", key_type]
        return self._token(args, out)

    def set_domain(
        self, keyfile: str, domain: str, out: Optional[str] = None
    ) -> str:
        """Set the key's domain; the tool issues a new token, returned as base64."""
        return self._token(["--keyfile", keyfile, "set_domain", domain], out)

    def _token(self, args: List[str], out: Optional[str]) -> str:
        if out:
            self._run(*args, "--out", out)
            return token_from_block(self.read_text(out))
        return token_from_block(self._run(*args).stdout)

    def attest_domain(self, keyfile: str) -> str:
        """The domain attestation as hex."""
        output = self._run("--keyfile", keyfile, "attest_domain").stdout
        match = _ATTESTATION.search(output)
        if not match:
            raise RuntimeError(f"no attestation in validator-keys output: {output}")
        return match.group(1)

    def show_manifest(self, keyfile: str) -> str:
        """The key's current manifest as base64."""
        output = self._run("--keyfile", keyfile, "show_manifest", "base64").stdout
        match = _MANIFEST.search(output)
        if not match:
            raise RuntimeError(f"no manifest in validator-keys output: {output}")
        return match.group(1)

    def read_keys(self, keyfile: str) -> Optional[dict]:
        """The key file's JSON, or None when the file does not exist."""
        path = self._path(keyfile)
        if not os.path.isfile(path):
            return None
        with open(path) as f:
            return json.load(f)

    def public_key_hex(self, keyfile: str) -> str:
        """The key file's public key as hex, the form ``[validator_list_keys]``
        and a list's ``validation_public_key`` take."""
        return node_public_key_hex(self.read_keys(keyfile)["public_key"])

    def read_token(self, token_file: str) -> str:
        return token_from_block(self.read_text(token_file))

    def read_manifest(self, manifest_file: str) -> str:
        return self.read_text(manifest_file).strip()

    # -- validator lists -----------------------------------------------------

    def sign_list(self, unsigned_path: str, token_file: str, out: str) -> None:
        self._run("sign_list", unsigned_path, "--token-file", token_file, "--out", out)

    def verify_list(
        self,
        vl_path: str,
        validators: Optional[str] = None,
        expected_key: Optional[str] = None,
    ) -> dict:
        """The tool's verification report; raises when any check fails."""
        args = ["verify_list", vl_path]
        if validators:
            args += ["--validators", validators]
        if expected_key:
            args += ["--expected-key", expected_key]
        result = self._run(*args, check=False)
        try:
            report = json.loads(result.stdout)
        except ValueError:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"validator-keys verify_list failed: {detail}")
        if result.returncode != 0 or not report.get("ok"):
            errors = "; ".join(report.get("errors", [])) or "not ok"
            raise RuntimeError(f"{vl_path} failed verification: {errors}")
        return report

    def publish_list(
        self,
        validators: List[Dict[str, str]],
        token_file: str = "keystore/vl/token.txt",
        out_dir: str = "vl",
    ) -> dict:
        """Write ``<out_dir>/unsigned.json``, sign it to ``<out_dir>/vl.json`` with
        the publisher token and verify the result against the unsigned list."""
        unsigned = os.path.join(out_dir, "unsigned.json")
        signed = os.path.join(out_dir, "vl.json")
        self.write_text(unsigned, json.dumps(unsigned_list(validators), indent=2))
        self.sign_list(unsigned, token_file, signed)
        return self.verify_list(signed, validators=unsigned)
