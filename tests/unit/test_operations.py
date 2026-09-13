#!/usr/bin/env python
# coding: utf-8

"""Tests for xrpld_lab.operations — operational helpers for the CLI.

Covers:
- run_start_script / run_stop_script: script existence checks and exit codes
- remove_network: directory removal
- stop_standalone: name vs. protocol+version resolution; removal only after stop
- start_local / stop_local: CWD-based script running
- update_node_binary: binary sourcing before stop, Dockerfile rewrite, exit codes
- restart_local_node: docker vs bare-process relaunch and pidfile
- vote_amendment / node_stall: JSON-RPC dispatch and error reporting
- view_local_logs / view_standalone_logs: log file discovery
"""

import json
import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest
import requests

from xrpld_lab.models import NodeRole, PortSet
from xrpld_lab.operations import (
    _dockerfile_with_binary,
    vote_amendment,
    node_stall,
    remove_network,
    restart_local_node,
    run_start_script,
    run_stop_script,
    start_local,
    stop_local,
    stop_standalone,
    update_node_binary,
    view_local_logs,
    view_standalone_logs,
)
from xrpld_lab.script_builder import DockerfileBuilder
from xrpld_lab.utils import sha512_half


def _workspace(base: str) -> MagicMock:
    ws = MagicMock()
    ws.base = base
    return ws


# -------------------------------------------------------------------------
# run_start_script / run_stop_script
# -------------------------------------------------------------------------


class TestRunScripts:
    """Test script runner helpers."""

    @patch("xrpld_lab.operations.run_command", return_value=0)
    @patch("os.path.isfile", return_value=True)
    def test_run_start_script_calls_bash(self, mock_isfile, mock_run):
        assert run_start_script(_workspace("/workspace"), "my-net") is True
        mock_run.assert_called_once_with("/workspace/my-net", "bash start.sh")

    @patch("xrpld_lab.operations.run_command", return_value=7)
    @patch("os.path.isfile", return_value=True)
    def test_run_start_script_reports_failure(self, mock_isfile, mock_run):
        assert run_start_script(_workspace("/workspace"), "my-net") is False

    @patch("xrpld_lab.operations.run_command")
    @patch("os.path.isfile", return_value=False)
    def test_run_start_script_missing_prints_error(self, mock_isfile, mock_run, capsys):
        assert run_start_script(_workspace("/workspace"), "my-net") is False
        mock_run.assert_not_called()
        assert "start.sh not found" in capsys.readouterr().out

    @patch("xrpld_lab.operations.run_command", return_value=0)
    @patch("os.path.isfile", return_value=True)
    def test_run_stop_script_calls_bash(self, mock_isfile, mock_run):
        assert run_stop_script(_workspace("/workspace"), "my-net") is True
        mock_run.assert_called_once_with("/workspace/my-net", "bash stop.sh")

    @patch("xrpld_lab.operations.run_command", return_value=1)
    @patch("os.path.isfile", return_value=True)
    def test_run_stop_script_reports_failure(self, mock_isfile, mock_run):
        assert run_stop_script(_workspace("/workspace"), "my-net") is False

    @patch("xrpld_lab.operations.run_command")
    @patch("os.path.isfile", return_value=False)
    def test_run_stop_script_missing_prints_error(self, mock_isfile, mock_run, capsys):
        assert run_stop_script(_workspace("/workspace"), "my-net") is False
        mock_run.assert_not_called()
        assert "stop.sh not found" in capsys.readouterr().out


# -------------------------------------------------------------------------
# remove_network
# -------------------------------------------------------------------------


class TestRemoveNetwork:
    """Test network directory removal."""

    def test_removes_existing_directory(self, tmp_path):
        (tmp_path / "my-net").mkdir()

        assert remove_network(_workspace(str(tmp_path)), "my-net") is True
        assert not (tmp_path / "my-net").exists()

    def test_missing_directory_prints_error(self, tmp_path, capsys):
        assert remove_network(_workspace(str(tmp_path)), "my-net") is False
        assert "not found" in capsys.readouterr().out


# -------------------------------------------------------------------------
# stop_standalone
# -------------------------------------------------------------------------


class TestStopStandalone:
    """Standalone stop and cleanup: the directory goes only after stop.sh exits 0."""

    @staticmethod
    def _standalone(tmp_path, dir_name: str) -> str:
        net_dir = tmp_path / dir_name
        net_dir.mkdir()
        (net_dir / "stop.sh").write_text("#!/bin/bash\n")
        return str(net_dir)

    @patch("xrpld_lab.operations.run_command", return_value=0)
    def test_with_name_uses_name_directly(self, mock_run, tmp_path):
        net_dir = self._standalone(tmp_path, "custom-name")

        assert stop_standalone(_workspace(str(tmp_path)), "custom-name", "xrpl", None)
        mock_run.assert_called_once_with(net_dir, "bash stop.sh")
        assert not os.path.exists(net_dir)

    @patch("xrpld_lab.operations.run_command", return_value=0)
    def test_with_version_constructs_dir_name(self, mock_run, tmp_path):
        net_dir = self._standalone(tmp_path, "xrpl-1.0.0")

        assert stop_standalone(_workspace(str(tmp_path)), None, "xrpl", "1.0.0")
        mock_run.assert_called_once_with(net_dir, "bash stop.sh")
        assert not os.path.exists(net_dir)

    @patch("xrpld_lab.operations.run_command", return_value=7)
    def test_failed_stop_keeps_the_directory(self, mock_run, tmp_path):
        net_dir = self._standalone(tmp_path, "custom-name")

        result = stop_standalone(_workspace(str(tmp_path)), "custom-name", "xrpl", None)

        assert result is False
        assert os.path.isdir(net_dir)
        assert os.path.isfile(os.path.join(net_dir, "stop.sh"))

    @patch("xrpld_lab.operations.run_command")
    def test_missing_directory_returns_false(self, mock_run, tmp_path, capsys):
        result = stop_standalone(_workspace(str(tmp_path)), "custom-name", "xrpl", None)

        assert result is False
        mock_run.assert_not_called()
        assert "Directory not found" in capsys.readouterr().out

    @patch("xrpld_lab.operations.run_command")
    def test_missing_stop_script_keeps_the_directory(self, mock_run, tmp_path, capsys):
        (tmp_path / "custom-name").mkdir()

        result = stop_standalone(_workspace(str(tmp_path)), "custom-name", "xrpl", None)

        assert result is False
        mock_run.assert_not_called()
        assert (tmp_path / "custom-name").is_dir()
        assert "stop.sh not found" in capsys.readouterr().out

    @patch("xrpld_lab.operations.run_command")
    def test_no_name_or_version_prints_error(self, mock_run, tmp_path, capsys):
        assert stop_standalone(_workspace(str(tmp_path)), None, "xrpl", None) is False
        mock_run.assert_not_called()
        assert "--name or --version is required" in capsys.readouterr().out


# -------------------------------------------------------------------------
# start_local / stop_local
# -------------------------------------------------------------------------


class TestLocalScripts:
    """Test local standalone start/stop."""

    @patch("xrpld_lab.operations.subprocess.run")
    @patch("os.path.isfile", return_value=False)
    @patch("os.getcwd", return_value="/my/project")
    def test_start_local_missing_binary(self, mock_cwd, mock_isfile, mock_run, capsys):
        assert start_local() is False
        mock_run.assert_not_called()
        assert "not found in /my/project" in capsys.readouterr().out

    @staticmethod
    def _build_tree(tmp_path, feature_line: str) -> str:
        """A fake ``<repo>/build`` with an xrpld binary and the features.macro."""
        build = tmp_path / "build"
        build.mkdir()
        (build / "xrpld").write_text("#!/bin/sh\n")
        macro = tmp_path / "include" / "xrpl" / "protocol" / "detail"
        macro.mkdir(parents=True)
        (macro / "features.macro").write_text(feature_line + "\n")
        return str(build)

    @patch("xrpld_lab.operations.subprocess.run")
    def test_start_local_writes_config_and_launches(
        self, mock_run, tmp_path, monkeypatch, capsys
    ):
        build = self._build_tree(
            tmp_path, "XRPL_FEATURE(DID, Supported::yes, VoteBehavior::DefaultYes)"
        )
        monkeypatch.chdir(build)
        mock_run.return_value.returncode = 0

        assert start_local(protocol="xrpl", network_id=1234) is True

        start = tmp_path / "build" / "start.sh"
        stop = tmp_path / "build" / "stop.sh"
        assert start.read_text() == (
            "#!/bin/bash\n"
            "exec ./xrpld -a --conf config/xrpld.cfg --ledgerfile config/genesis.json\n"
        )
        assert stop.read_text() == (
            "#!/bin/bash\n"
            "pkill -f './xrpld' && echo \"xrpld stopped\""
            ' || echo "No running xrpld found"\n'
        )
        assert os.access(start, os.X_OK) and os.access(stop, os.X_OK)
        assert (tmp_path / "build" / "db").is_dir()
        assert (tmp_path / "build" / "log").is_dir()

        config = tmp_path / "build" / "config"
        cfg = (config / "xrpld.cfg").read_text()
        assert "[network_id]\n1234\n" in cfg
        assert "[validators]\n" in (config / "validators.txt").read_text()
        with open(config / "genesis.json") as f:
            genesis = json.load(f)
        amendments = [
            e
            for e in genesis["ledger"]["accountState"]
            if e["LedgerEntryType"] == "Amendments"
        ]
        assert amendments[0]["Amendments"] == [sha512_half(b"DID".hex())]

        assert mock_run.call_args.args[0] == ["bash", "start.sh"]
        assert mock_run.call_args.kwargs == {"cwd": build}
        out = capsys.readouterr().out
        assert f"Generated config in {config}" in out
        assert "Starting xrpld (Ctrl+C to stop)..." in out

    @patch("xrpld_lab.operations.subprocess.run")
    def test_start_local_network_mode_drops_the_standalone_flag(
        self, mock_run, tmp_path, monkeypatch
    ):
        build = self._build_tree(
            tmp_path, "XRPL_FEATURE(DID, Supported::yes, VoteBehavior::DefaultYes)"
        )
        monkeypatch.chdir(build)
        mock_run.return_value.returncode = 0

        assert start_local(network_type="network", network_id=7) is True

        assert (tmp_path / "build" / "start.sh").read_text() == (
            "#!/bin/bash\n"
            "exec ./xrpld  --conf config/xrpld.cfg --ledgerfile config/genesis.json\n"
        )

    @patch("xrpld_lab.operations.subprocess.run")
    def test_start_local_without_supported_features_raises(
        self, mock_run, tmp_path, monkeypatch
    ):
        # update_genesis raises when no amendment is Supported::yes; start_local
        # does not turn that into a False return.
        build = self._build_tree(
            tmp_path, "XRPL_FEATURE(DID, Supported::no, VoteBehavior::DefaultNo)"
        )
        monkeypatch.chdir(build)

        with pytest.raises(RuntimeError, match="No features found for xrpl"):
            start_local(network_id=1234)

        mock_run.assert_not_called()
        assert not (tmp_path / "build" / "start.sh").exists()

    @patch("xrpld_lab.operations.subprocess.run")
    def test_start_local_without_a_features_file_returns_false(
        self, mock_run, tmp_path, monkeypatch, capsys
    ):
        build = tmp_path / "build"
        build.mkdir()
        (build / "xrpld").write_text("")
        monkeypatch.chdir(build)

        assert start_local() is False

        mock_run.assert_not_called()
        assert "Could not resolve features" in capsys.readouterr().out

    @patch("xrpld_lab.operations.run_command", return_value=0)
    @patch("os.path.isfile", return_value=True)
    @patch("os.getcwd", return_value="/my/project")
    def test_stop_local_runs_stop_sh(self, mock_cwd, mock_isfile, mock_run):
        assert stop_local() is True
        mock_run.assert_called_once_with("/my/project", "bash stop.sh")

    @patch("xrpld_lab.operations.run_command", return_value=1)
    @patch("os.path.isfile", return_value=True)
    @patch("os.getcwd", return_value="/my/project")
    def test_stop_local_reports_failure(self, mock_cwd, mock_isfile, mock_run):
        assert stop_local() is False


# -------------------------------------------------------------------------
# update_node_binary
# -------------------------------------------------------------------------


def _network_dockerfile(binary: bool, version: str = "3.3.0") -> str:
    return DockerfileBuilder.build(
        protocol="xrpl",
        ports=PortSet.for_node(2, NodeRole.VALIDATOR),
        image_name="rippleci/xrpld:3.3.0",
        network=True,
        binary=binary,
        version=version,
    )


def _write_fetched(dest: str) -> bool:
    with open(dest, "wb") as f:
        f.write(b"\x7fELF")
    return True


class TestDockerfileWithBinary:
    """Dockerfile rewrite for the two node layouts."""

    def test_image_mode_gets_copy_and_chmod_before_first_env(self):
        content = _network_dockerfile(binary=False)
        assert "COPY xrpld." not in content

        out = _dockerfile_with_binary(content, "3.4.0")

        lines = [line.strip() for line in out.split("\n")]
        copy_at = lines.index("COPY xrpld.3.4.0 /opt/xrpld/bin/xrpld")
        chmod_at = lines.index("RUN chmod +x /opt/xrpld/bin/xrpld")
        first_env = next(i for i, line in enumerate(lines) if line.startswith("ENV "))
        assert lines.index("COPY entrypoint /entrypoint.sh") < copy_at
        assert copy_at + 1 == chmod_at
        assert chmod_at + 1 == first_env

    def test_binary_mode_copy_line_is_repointed(self):
        content = _network_dockerfile(binary=True, version="3.3.0")

        out = _dockerfile_with_binary(content, "3.4.0")

        assert "COPY xrpld.3.4.0 /opt/xrpld/bin/xrpld" in out
        assert "xrpld.3.3.0" not in out
        assert out.count("COPY xrpld.") == 1
        assert out.count("RUN chmod +x /opt/xrpld/bin/xrpld") == 1

    def test_no_anchor_returns_none(self):
        assert _dockerfile_with_binary("FROM scratch\n", "3.4.0") is None


class TestUpdateNodeBinary:
    """Binary is fetched first; the node is only stopped once it is on disk."""

    @staticmethod
    def _cluster(tmp_path, node: str, dockerfile: str) -> str:
        node_dir = tmp_path / "my-net" / node
        node_dir.mkdir(parents=True)
        (node_dir / "Dockerfile").write_text(dockerfile)
        return str(node_dir)

    @patch("xrpld_lab.operations.run_command", return_value=0)
    @patch(
        "xrpld_lab.operations._download_binary",
        side_effect=lambda u, d: _write_fetched(d),
    )
    def test_image_mode_cluster_gains_the_copy_lines(self, mock_dl, mock_run, tmp_path):
        node_dir = self._cluster(tmp_path, "vnode2", _network_dockerfile(binary=False))
        os.makedirs(os.path.join(node_dir, "lib"))
        net_dir = str(tmp_path / "my-net")

        ok = update_node_binary(
            _workspace(str(tmp_path)), "my-net", 2, "validator", "https://b", "3.4.0"
        )

        assert ok is True
        mock_dl.assert_called_once_with(
            "https://b/3.4.0", os.path.join(node_dir, "xrpld.3.4.0")
        )
        assert os.stat(os.path.join(node_dir, "xrpld.3.4.0")).st_mode & 0o111
        assert not os.path.exists(os.path.join(node_dir, "lib"))
        content = open(os.path.join(node_dir, "Dockerfile")).read()
        assert "COPY xrpld.3.4.0 /opt/xrpld/bin/xrpld\nRUN chmod +x" in content
        assert mock_run.call_args_list == [
            ((net_dir, "docker compose stop vnode2"),),
            ((net_dir, "docker compose up --build --force-recreate -d vnode2"),),
        ]

    @patch("xrpld_lab.operations.run_command", return_value=0)
    @patch(
        "xrpld_lab.operations._download_binary",
        side_effect=lambda u, d: _write_fetched(d),
    )
    def test_binary_mode_cluster_is_repointed(self, mock_dl, mock_run, tmp_path):
        node_dir = self._cluster(
            tmp_path, "vnode2", _network_dockerfile(binary=True, version="3.3.0")
        )

        ok = update_node_binary(
            _workspace(str(tmp_path)), "my-net", 2, "validator", "https://b", "3.4.0"
        )

        assert ok is True
        content = open(os.path.join(node_dir, "Dockerfile")).read()
        assert "COPY xrpld.3.4.0 /opt/xrpld/bin/xrpld" in content
        assert "xrpld.3.3.0" not in content

    @patch("xrpld_lab.operations.run_command")
    @patch("xrpld_lab.operations._extract_binary_from_image", return_value=False)
    def test_failed_image_fetch_leaves_the_node_running(
        self, mock_extract, mock_run, tmp_path
    ):
        dockerfile = _network_dockerfile(binary=False)
        node_dir = self._cluster(tmp_path, "vnode2", dockerfile)

        ok = update_node_binary(
            _workspace(str(tmp_path)),
            "my-net",
            2,
            "validator",
            None,
            "3.4.0",
            image="rippleci/xrpld:3.4.0",
        )

        assert ok is False
        mock_run.assert_not_called()
        assert open(os.path.join(node_dir, "Dockerfile")).read() == dockerfile
        assert not os.path.exists(os.path.join(node_dir, "xrpld.3.4.0"))

    @patch("xrpld_lab.operations.run_command")
    @patch("xrpld_lab.operations.subprocess.run")
    def test_failed_download_leaves_the_node_running(
        self, mock_subproc, mock_run, tmp_path, capsys
    ):
        mock_subproc.return_value = MagicMock(returncode=22)
        self._cluster(tmp_path, "vnode2", _network_dockerfile(binary=False))

        ok = update_node_binary(
            _workspace(str(tmp_path)), "my-net", 2, "validator", "https://b", "3.4.0"
        )

        assert ok is False
        mock_run.assert_not_called()
        assert "Failed to download https://b/3.4.0" in capsys.readouterr().out

    @patch("xrpld_lab.operations.run_command", return_value=0)
    @patch("xrpld_lab.operations.subprocess.run")
    def test_download_uses_curl_with_fail_flag(self, mock_subproc, mock_run, tmp_path):
        node_dir = self._cluster(tmp_path, "vnode2", _network_dockerfile(binary=False))
        dest = os.path.join(node_dir, "xrpld.3.4.0")

        def fake_curl(argv, **kwargs):
            _write_fetched(argv[argv.index("-o") + 1])
            return MagicMock(returncode=0)

        mock_subproc.side_effect = fake_curl

        ok = update_node_binary(
            _workspace(str(tmp_path)), "my-net", 2, "validator", "https://b", "3.4.0"
        )

        assert ok is True
        assert mock_subproc.call_args.args[0] == [
            "curl",
            "-fsSL",
            "-o",
            dest,
            "https://b/3.4.0",
        ]

    @patch("xrpld_lab.operations.run_command", return_value=0)
    @patch(
        "xrpld_lab.operations._extract_binary_from_image",
        side_effect=lambda i, d: _write_fetched(d),
    )
    def test_update_from_image_extracts_not_downloads(
        self, mock_extract, mock_run, tmp_path
    ):
        node_dir = self._cluster(tmp_path, "vnode2", _network_dockerfile(binary=False))

        with patch("xrpld_lab.operations._download_binary") as mock_dl:
            ok = update_node_binary(
                _workspace(str(tmp_path)),
                "my-net",
                2,
                "validator",
                None,
                "3.3.0-rc1",
                image="rippleci/xrpld:3.3.0-rc1",
            )

        assert ok is True
        mock_dl.assert_not_called()
        mock_extract.assert_called_once_with(
            "rippleci/xrpld:3.3.0-rc1", os.path.join(node_dir, "xrpld.3.3.0-rc1")
        )
        mock_run.assert_any_call(
            str(tmp_path / "my-net"),
            "docker compose up --build --force-recreate -d vnode2",
        )

    @patch("xrpld_lab.operations.run_command", return_value=0)
    @patch(
        "xrpld_lab.operations._download_binary",
        side_effect=lambda u, d: _write_fetched(d),
    )
    def test_update_peer_node_uses_pnode_prefix(self, mock_dl, mock_run, tmp_path):
        self._cluster(tmp_path, "pnode3", _network_dockerfile(binary=False))

        ok = update_node_binary(
            _workspace(str(tmp_path)), "my-net", 3, "peer", "https://b", "1.0.0"
        )

        assert ok is True
        mock_run.assert_any_call(str(tmp_path / "my-net"), "docker compose stop pnode3")

    @patch("xrpld_lab.operations.run_command", return_value=1)
    @patch(
        "xrpld_lab.operations._download_binary",
        side_effect=lambda u, d: _write_fetched(d),
    )
    def test_failed_stop_skips_the_rebuild(self, mock_dl, mock_run, tmp_path):
        self._cluster(tmp_path, "vnode2", _network_dockerfile(binary=False))

        ok = update_node_binary(
            _workspace(str(tmp_path)), "my-net", 2, "validator", "https://b", "3.4.0"
        )

        assert ok is False
        mock_run.assert_called_once_with(
            str(tmp_path / "my-net"), "docker compose stop vnode2"
        )

    @patch("xrpld_lab.operations.run_command", side_effect=[0, 1])
    @patch(
        "xrpld_lab.operations._download_binary",
        side_effect=lambda u, d: _write_fetched(d),
    )
    def test_failed_rebuild_returns_false(self, mock_dl, mock_run, tmp_path, capsys):
        self._cluster(tmp_path, "vnode2", _network_dockerfile(binary=False))

        ok = update_node_binary(
            _workspace(str(tmp_path)), "my-net", 2, "validator", "https://b", "3.4.0"
        )

        assert ok is False
        assert "updated to" not in capsys.readouterr().out

    @patch("xrpld_lab.operations.run_command")
    @patch(
        "xrpld_lab.operations._download_binary",
        side_effect=lambda u, d: _write_fetched(d),
    )
    def test_dockerfile_without_anchor_returns_false(self, mock_dl, mock_run, tmp_path):
        self._cluster(tmp_path, "vnode2", "FROM scratch\n")

        ok = update_node_binary(
            _workspace(str(tmp_path)), "my-net", 2, "validator", "https://b", "3.4.0"
        )

        assert ok is False
        mock_run.assert_not_called()

    @patch("xrpld_lab.operations.run_command")
    def test_update_missing_node_dir(self, mock_run, tmp_path, capsys):
        ok = update_node_binary(
            _workspace(str(tmp_path)), "my-net", 1, "peer", "https://b", "1.0.0"
        )

        assert ok is False
        mock_run.assert_not_called()
        assert "not found" in capsys.readouterr().out

    @patch("xrpld_lab.operations.run_command")
    def test_update_missing_dockerfile(self, mock_run, tmp_path, capsys):
        (tmp_path / "my-net" / "vnode1").mkdir(parents=True)

        ok = update_node_binary(
            _workspace(str(tmp_path)), "my-net", 1, "validator", "https://b", "1.0.0"
        )

        assert ok is False
        mock_run.assert_not_called()
        assert "Dockerfile not found" in capsys.readouterr().out


# -------------------------------------------------------------------------
# restart_local_node
# -------------------------------------------------------------------------


class TestRestartLocalNode:
    """Restart routing: docker container vs bare-process pidfile."""

    @patch("xrpld_lab.operations.run_command", return_value=0)
    @patch("xrpld_lab.operations._docker_container_exists", return_value=True)
    @patch("os.getcwd", return_value="/cluster")
    @patch("os.path.isdir", return_value=True)
    @patch("os.path.isfile", return_value=False)
    def test_docker_resume(
        self, mock_isfile, mock_isdir, mock_cwd, mock_docker, mock_run
    ):
        assert restart_local_node("vnode2") is True
        mock_run.assert_called_once_with("/cluster", "docker restart vnode2")

    @patch("xrpld_lab.operations.run_command", return_value=0)
    @patch("xrpld_lab.operations._docker_container_exists", return_value=True)
    @patch("os.getcwd", return_value="/cluster")
    @patch("os.path.isdir", return_value=True)
    @patch("os.path.isfile", return_value=False)
    def test_docker_genesis_recreates(
        self, mock_isfile, mock_isdir, mock_cwd, mock_docker, mock_run
    ):
        assert restart_local_node("vnode5", genesis=True) is True
        mock_run.assert_called_once_with(
            "/cluster", "docker compose up --force-recreate -d vnode5"
        )

    @patch("xrpld_lab.operations.run_command", return_value=1)
    @patch("xrpld_lab.operations._docker_container_exists", return_value=True)
    @patch("os.getcwd", return_value="/cluster")
    @patch("os.path.isdir", return_value=True)
    @patch("os.path.isfile", return_value=False)
    def test_docker_failure_is_reported(
        self, mock_isfile, mock_isdir, mock_cwd, mock_docker, mock_run, capsys
    ):
        assert restart_local_node("vnode2") is False
        assert "restarted" not in capsys.readouterr().out

    @staticmethod
    def _bare_node(tmp_path, node: str, pid: str = "12345") -> str:
        node_dir = tmp_path / node
        node_dir.mkdir()
        (node_dir / "xrpld.pid").write_text(pid)
        return str(node_dir)

    @patch("xrpld_lab.operations.subprocess.Popen")
    @patch("xrpld_lab.operations.subprocess.run")
    @patch("xrpld_lab.operations._docker_container_exists", return_value=False)
    def test_bare_process_is_killed_and_relaunched(
        self, mock_docker, mock_run, mock_popen, tmp_path
    ):
        node_dir = self._bare_node(tmp_path, "vnode1")
        mock_popen.return_value = MagicMock(pid=4242)

        with patch("os.getcwd", return_value=str(tmp_path)):
            ok = restart_local_node("vnode1")

        assert ok is True
        assert mock_run.call_args_list[0].args[0] == ["kill", "12345"]
        assert mock_popen.call_args.args[0] == ["./xrpld", "--conf", "config/xrpld.cfg"]
        assert mock_popen.call_args.kwargs == {
            "cwd": node_dir,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "start_new_session": True,
        }
        assert open(os.path.join(node_dir, "xrpld.pid")).read().strip() == "4242"

    @patch("xrpld_lab.operations.subprocess.Popen")
    @patch("xrpld_lab.operations.subprocess.run")
    @patch("xrpld_lab.operations._docker_container_exists", return_value=False)
    def test_bare_process_genesis_flags(
        self, mock_docker, mock_run, mock_popen, tmp_path
    ):
        self._bare_node(tmp_path, "vnode1")
        mock_popen.return_value = MagicMock(pid=4242)

        with patch("os.getcwd", return_value=str(tmp_path)):
            assert restart_local_node("vnode1", binary_name="rippled", genesis=True)

        assert mock_popen.call_args.args[0] == [
            "./rippled",
            "--conf",
            "config/xrpld.cfg",
            "--ledgerfile",
            "config/genesis.json",
            "--valid",
        ]

    @patch(
        "xrpld_lab.operations.subprocess.Popen",
        side_effect=FileNotFoundError(2, "No such file", "./xrpld"),
    )
    @patch("xrpld_lab.operations.subprocess.run")
    @patch("xrpld_lab.operations._docker_container_exists", return_value=False)
    def test_bare_process_missing_binary_reports_failure(
        self, mock_docker, mock_run, mock_popen, tmp_path, capsys
    ):
        node_dir = self._bare_node(tmp_path, "vnode1")

        with patch("os.getcwd", return_value=str(tmp_path)):
            ok = restart_local_node("vnode1")

        assert ok is False
        assert not os.path.exists(os.path.join(node_dir, "xrpld.pid"))
        out = capsys.readouterr().out
        assert "Cannot start vnode1" in out
        assert "started" not in out

    @patch("xrpld_lab.operations.run_command")
    @patch("os.path.isdir", return_value=False)
    def test_missing_node_dir(self, mock_isdir, mock_run, capsys):
        assert restart_local_node("vnode9") is False
        mock_run.assert_not_called()
        assert "not found" in capsys.readouterr().out


# -------------------------------------------------------------------------
# vote_amendment / node_stall
# -------------------------------------------------------------------------


def _rpc_response(status_code: int = 200, result: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {
        "result": result if result is not None else {"status": "success"}
    }
    return resp


class TestVoteAmendment:
    """Veto lifting via the feature admin RPC across the cluster's validators."""

    def _workspace(self, tmp_path, validators):
        ws = MagicMock()
        ws.base = str(tmp_path)
        for i in range(1, validators + 1):
            (tmp_path / "my-net" / f"vnode{i}").mkdir(parents=True)
        return ws

    @patch("xrpld_lab.operations.requests.post")
    def test_every_validator_is_asked(self, mock_post, tmp_path, capsys):
        mock_post.return_value = _rpc_response()

        ok = vote_amendment("my-net", "fixNFTokenRemint", self._workspace(tmp_path, 2))

        assert ok is True
        urls = [c.args[0] for c in mock_post.call_args_list]
        assert urls == ["http://localhost:5105", "http://localhost:5205"]
        body = mock_post.call_args.kwargs["json"]
        assert body["method"] == "feature"
        assert body["params"][0]["vetoed"] is False
        assert "next flag ledger" in capsys.readouterr().out

    @patch("xrpld_lab.operations.requests.post")
    def test_node_id_targets_one_validator(self, mock_post, tmp_path):
        mock_post.return_value = _rpc_response()

        vote_amendment("my-net", "X", self._workspace(tmp_path, 3), node_id=2)

        assert mock_post.call_count == 1
        assert mock_post.call_args.args[0] == "http://localhost:5205"

    @patch("xrpld_lab.operations.requests.post")
    def test_hash_in_payload(self, mock_post, tmp_path):
        import hashlib

        mock_post.return_value = _rpc_response()
        expected = (
            hashlib.sha512("fixNFTokenRemint".encode("utf-8")).hexdigest().upper()[:64]
        )

        vote_amendment("my-net", "fixNFTokenRemint", self._workspace(tmp_path, 1))

        assert mock_post.call_args.kwargs["json"]["params"][0]["feature"] == expected

    @patch("xrpld_lab.operations.requests.post")
    def test_reply_state_is_printed_per_validator(self, mock_post, tmp_path, capsys):
        import hashlib

        h = hashlib.sha512("X".encode("utf-8")).hexdigest().upper()[:64]
        mock_post.return_value = _rpc_response(
            result={h: {"name": "X", "vetoed": False, "enabled": False}}
        )

        vote_amendment("my-net", "X", self._workspace(tmp_path, 1))

        assert "vnode1: X vetoed=False enabled=False" in capsys.readouterr().out

    @patch("xrpld_lab.operations.requests.post")
    def test_one_failing_validator_fails_the_command(self, mock_post, tmp_path, capsys):
        mock_post.side_effect = [_rpc_response(status_code=403), _rpc_response()]

        ok = vote_amendment("my-net", "X", self._workspace(tmp_path, 2))

        assert ok is False
        assert mock_post.call_count == 2
        assert "HTTP 403" in capsys.readouterr().out

    def test_no_validators_is_a_failure(self, tmp_path, capsys):
        ws = MagicMock()
        ws.base = str(tmp_path)

        assert vote_amendment("missing", "X", ws) is False
        assert "No validators found" in capsys.readouterr().out

    @patch(
        "xrpld_lab.operations.requests.post",
        side_effect=requests.ConnectionError("refused"),
    )
    def test_unreachable_validator_is_a_failure(self, mock_post, tmp_path, capsys):
        assert vote_amendment("my-net", "X", self._workspace(tmp_path, 1)) is False
        assert "RPC request failed" in capsys.readouterr().out


class TestNodeStall:
    """node_stall admin RPC dispatch."""

    @patch("xrpld_lab.operations.requests.post")
    def test_stall_sends_duration(self, mock_post, capsys):
        mock_post.return_value = _rpc_response()

        ok = node_stall("my-net", 1, "validator", MagicMock(), duration_ms=5000)

        assert ok is True
        assert mock_post.call_args.args[0] == "http://localhost:5105"
        assert mock_post.call_args.kwargs["json"] == {
            "method": "node_stall",
            "params": [{"duration_ms": 5000}],
        }
        out = capsys.readouterr().out
        assert "Stalling (5000ms) validator 1 at http://localhost:5105..." in out
        assert "node_stall RPC sent." in out

    @patch("xrpld_lab.operations.requests.post")
    def test_clear_sends_clear(self, mock_post, capsys):
        mock_post.return_value = _rpc_response()

        assert node_stall("my-net", 2, "peer", MagicMock(), clear=True) is True

        assert mock_post.call_args.args[0] == "http://localhost:5025"
        assert mock_post.call_args.kwargs["json"]["params"] == [{"clear": True}]
        assert "Clearing stall on peer 2 at http://localhost:5025..." in (
            capsys.readouterr().out
        )

    @patch("xrpld_lab.operations.requests.post")
    def test_rpc_error_result_is_a_failure(self, mock_post, capsys):
        mock_post.return_value = _rpc_response(
            result={
                "status": "error",
                "error": "unknownCmd",
                "error_message": "Unknown method.",
            }
        )

        assert node_stall("my-net", 1, "validator", MagicMock()) is False
        out = capsys.readouterr().out
        assert "Unknown method." in out
        assert "node_stall RPC sent" not in out

    @patch("xrpld_lab.operations.requests.post")
    def test_default_duration_is_30s(self, mock_post):
        mock_post.return_value = _rpc_response()

        node_stall("my-net", 1, "validator", MagicMock())

        assert mock_post.call_args.kwargs["json"]["params"] == [{"duration_ms": 30000}]

    @patch(
        "xrpld_lab.operations.requests.post",
        side_effect=requests.ConnectionError("refused"),
    )
    def test_unreachable_node_is_a_failure(self, mock_post, capsys):
        assert node_stall("my-net", 1, "validator", MagicMock()) is False
        out = capsys.readouterr().out
        assert "RPC request failed: refused" in out
        assert "node_stall RPC sent" not in out


# -------------------------------------------------------------------------
# view_local_logs / view_standalone_logs
# -------------------------------------------------------------------------


class TestLogs:
    """Test log viewing functions."""

    @patch("subprocess.run")
    @patch("os.path.isfile", return_value=True)
    @patch("os.getcwd", return_value="/my/project")
    def test_view_local_logs_with_node(self, mock_cwd, mock_isfile, mock_run):
        view_local_logs("vnode1")
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "tail" in call_args
        assert "/my/project/vnode1/log/debug.log" in call_args

    @patch("subprocess.run")
    @patch("os.path.isfile", return_value=False)
    @patch("os.getcwd", return_value="/my/project")
    @patch("glob.glob", return_value=[])
    def test_view_local_logs_no_file_prints_error(
        self, mock_glob, mock_cwd, mock_isfile, mock_run, capsys
    ):
        view_local_logs(None)
        mock_run.assert_not_called()
        captured = capsys.readouterr()
        assert "No debug.log found" in captured.out

    @patch("subprocess.run")
    def test_view_standalone_logs_tails_docker(self, mock_run):
        view_standalone_logs()
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "docker" in call_args
        assert "xrpl" in call_args
