import json
import os
import stat
import pytest

from unittest.mock import patch

from xrpld_lab.utils import (
    sha512_half,
    write_file,
    write_executable,
    read_json,
    remove_directory,
    run_command,
    save_config,
)


class TestSha512Half:
    def test_sha512_half_deterministic(self):
        hex_input = "deadbeef"
        result1 = sha512_half(hex_input)
        result2 = sha512_half(hex_input)
        assert result1 == result2

    def test_sha512_half_correct_length(self):
        hex_input = "deadbeef"
        result = sha512_half(hex_input)
        # SHA-512 produces 128 hex chars; half is 64
        assert len(result) == 64

    def test_sha512_half_uppercase(self):
        result = sha512_half("00")
        assert result == result.upper()


class TestWriteFile:
    def test_write_file(self, tmp_path):
        path = str(tmp_path / "test.txt")
        write_file(path, "hello world")
        with open(path) as f:
            assert f.read() == "hello world"


class TestWriteExecutable:
    def test_write_executable(self, tmp_path):
        path = str(tmp_path / "run.sh")
        write_executable(path, "#!/bin/bash\necho hi")
        assert os.path.exists(path)
        file_stat = os.stat(path)
        assert file_stat.st_mode & stat.S_IXUSR  # owner execute bit
        assert file_stat.st_mode & stat.S_IXGRP  # group execute bit
        assert file_stat.st_mode & stat.S_IXOTH  # other execute bit


class TestReadJson:
    def test_read_json(self, tmp_path):
        path = str(tmp_path / "data.json")
        data = {"key": "value", "count": 42}
        with open(path, "w") as f:
            json.dump(data, f)
        result = read_json(path)
        assert result == data

    def test_read_json_nested(self, tmp_path):
        path = str(tmp_path / "nested.json")
        data = {"outer": {"inner": [1, 2, 3]}}
        with open(path, "w") as f:
            json.dump(data, f)
        result = read_json(path)
        assert result["outer"]["inner"] == [1, 2, 3]


class TestRunCommand:
    def test_returns_zero_on_success(self, tmp_path):
        assert run_command(str(tmp_path), "true") == 0

    def test_returns_the_exit_code_on_failure(self, tmp_path, capsys):
        assert run_command(str(tmp_path), "sh -c 'exit 7'") == 7
        assert "Command failed (exit 7)" in capsys.readouterr().out

    def test_missing_command_returns_127(self, tmp_path, capsys):
        assert run_command(str(tmp_path), "no-such-command-xrpld-lab") == 127
        assert "Command not found" in capsys.readouterr().out

    def test_output_is_inherited_not_captured(self, tmp_path):
        with patch("xrpld_lab.utils.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            run_command(str(tmp_path), "bash start.sh")

        assert mock_run.call_args.args[0] == ["bash", "start.sh"]
        kwargs = mock_run.call_args.kwargs
        assert kwargs == {"cwd": str(tmp_path)}


class TestRemoveDirectory:
    def test_removes_and_returns_true(self, tmp_path):
        target = tmp_path / "net"
        target.mkdir()
        (target / "f").write_text("x")

        assert remove_directory(str(target)) is True
        assert not target.exists()

    def test_missing_returns_false(self, tmp_path, capsys):
        assert remove_directory(str(tmp_path / "missing")) is False
        assert "Not found" in capsys.readouterr().out


class TestSaveConfig:
    def test_writes_daemon_cfg_and_validators_txt(self, tmp_path):
        cfg = "[server]\nport_rpc_admin_local\n"
        vl = "[validators]\nnHB1X37qrniVugfQcuBTAjswphC1drx7QjFFojJPZwKHHnt8kU7v\n"

        save_config("xrpl", str(tmp_path), cfg, vl)

        # The daemon file name is "<protocol>d.cfg": protocol "xrpl" -> xrpld.cfg.
        assert sorted(p.name for p in tmp_path.iterdir()) == [
            "validators.txt",
            "xrpld.cfg",
        ]
        assert (tmp_path / "xrpld.cfg").read_text() == cfg
        assert (tmp_path / "validators.txt").read_text() == vl

    def test_overwrites_existing_files(self, tmp_path):
        (tmp_path / "xrpld.cfg").write_text("old")
        (tmp_path / "validators.txt").write_text("old")

        save_config("xrpl", str(tmp_path), "new cfg", "new vl")

        assert (tmp_path / "xrpld.cfg").read_text() == "new cfg"
        assert (tmp_path / "validators.txt").read_text() == "new vl"

    def test_missing_directory_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            save_config("xrpl", str(tmp_path / "absent"), "cfg", "vl")
