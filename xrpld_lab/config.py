"""Config parsing and merging for xrpld configuration files.

Reads INI-style .cfg files from the repo, parses them into structured dicts,
and merges them with hardcoded defaults and local YAML/JSON overrides.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict

import yaml


def parse_xrpld_cfg(content: str) -> Dict[str, Any]:
    """Parse INI-style xrpld.cfg content into a structured dict.

    Sections become keys. Sections can contain:
    - A single value: section_name -> "value"
    - Key-value pairs (with = or space =): section_name -> {key: value}
    - Multiple lines (IP addresses, etc.): section_name -> [line1, line2, ...]

    Lines starting with # are comments and ignored.
    Empty lines separate sections.
    """
    result: Dict[str, Any] = {}
    current_section: str | None = None
    section_lines: list[str] = []

    def _flush_section():
        nonlocal current_section, section_lines
        if current_section is None:
            return
        value = _parse_section_lines(section_lines)
        if value is not None:
            if current_section in result:
                value = _append_section(result[current_section], value)
            result[current_section] = value
        current_section = None
        section_lines = []

    for raw_line in content.splitlines():
        line = raw_line.strip()

        # Skip comments
        if line.startswith("#"):
            continue

        # Check for section header
        section_match = re.match(r"^\[([^\]]+)\]$", line)
        if section_match:
            _flush_section()
            current_section = section_match.group(1)
            section_lines = []
            continue

        # Accumulate lines within a section
        if current_section is not None:
            section_lines.append(line)

    # Flush the last section
    _flush_section()

    return result


def _parse_section_lines(section_lines: list[str]) -> Any:
    """Structure a section's lines: dict for key=value, str for one, else list."""
    lines = [line.strip() for line in section_lines if line.strip()]
    if not lines:
        return None
    if any("=" in line for line in lines):
        d: Dict[str, str] = {}
        for line in lines:
            if "=" in line:
                key, _, value = line.partition("=")
                d[key.strip()] = value.strip()
        return d
    if len(lines) == 1:
        return lines[0]
    return lines


def _append_section(existing: Any, new: Any) -> Any:
    """Combine a repeated section the way xrpld does: keys merge, lines append."""
    if isinstance(existing, dict) and isinstance(new, dict):
        return {**existing, **new}
    return _section_lines(existing) + _section_lines(new)


def _section_lines(value: Any) -> list:
    """A section value as its list of lines."""
    if isinstance(value, dict):
        return [f"{k} = {v}" for k, v in value.items()]
    if isinstance(value, list):
        return list(value)
    return [value]


def _render_section(name: str, value: Any) -> str:
    """Render one section as INI text with a trailing blank line."""
    if isinstance(value, dict):
        body = "".join(f"{k} = {v}\n" for k, v in value.items())
    elif isinstance(value, (list, tuple)):
        body = "".join(f"{v}\n" for v in value)
    else:
        body = f"{value}\n"
    return f"[{name}]\n{body}\n"


_SECTION_HEADER = re.compile(r"^\[([^\]]+)\]\s*$")


def apply_overrides(content: str, overrides: Dict[str, Any]) -> str:
    """Apply per-section overrides to rendered xrpld.cfg text.

    A mapping merges into the section's keys, a list or scalar replaces the
    section, and a section the text lacks is appended. Untouched sections
    keep their exact text.
    """
    if not overrides:
        return content
    pending = dict(overrides)
    out: list[str] = []
    name: str | None = None
    raw: list[str] = []

    def flush():
        if name is None:
            out.extend(raw)
            return
        if name in pending:
            current = _parse_section_lines(raw)
            new = pending.pop(name)
            if isinstance(current, dict) and isinstance(new, dict):
                new = _deep_merge(current, new)
            out.append(_render_section(name, new))
        else:
            out.append(f"[{name}]\n" + "".join(raw))

    for line in content.splitlines(keepends=True):
        m = _SECTION_HEADER.match(line)
        if m:
            flush()
            name, raw = m.group(1), []
        elif name is None:
            out.append(line)
        else:
            raw.append(line)
    flush()

    text = "".join(out)
    if pending and text and not text.endswith("\n"):
        text += "\n"
    for extra, value in pending.items():
        if not text.endswith("\n\n") and text:
            text += "\n"
        text += _render_section(extra, value)
    return text


def load_overrides_file(path: str) -> Dict[str, Any]:
    """Load config overrides from a YAML or JSON file.

    Returns empty dict if file doesn't exist or is empty.
    Supports .yaml, .yml, and .json extensions.
    """
    if not os.path.exists(path):
        return {}

    with open(path, "r") as f:
        content = f.read()

    if not content.strip():
        return {}

    if path.endswith(".json"):
        return json.loads(content)

    # Default: treat as YAML (.yaml, .yml, or anything else)
    result = yaml.safe_load(content)
    return result if result is not None else {}


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Deep merge overlay into base. Overlay values win."""
    result = base.copy()
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_ansible_config(path: str) -> Dict[str, Any]:
    """Load ansible deployment config from a YAML file.

    Expected structure::

        ssh_port: 20
        ssh_user: ubuntu
        ssh_key_path: ~/.ssh/id_rsa
        vips:
          - 10.0.0.1
          - 10.0.0.2
        pips:
          - 10.0.0.3
        services:
          - ip: 10.0.0.4
            name: infra
            nginx:
              domain: example.com
              ...
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Ansible config not found: {path}")

    with open(path, "r") as f:
        data = yaml.safe_load(f)

    if not data or not isinstance(data, dict):
        raise ValueError(f"Invalid ansible config: {path}")

    return data
