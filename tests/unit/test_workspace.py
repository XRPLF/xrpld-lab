import os
import pytest

from xrpld_lab.workspace import Workspace


class TestWorkspaceCreation:
    def test_workspace_creates_base_dir(self, tmp_path):
        base = str(tmp_path / "output")
        ws = Workspace(base=base)
        assert os.path.isdir(ws.base)
        assert ws.base == os.path.abspath(base)

    def test_workspace_default_base(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        ws = Workspace()
        assert ws.base == os.path.abspath(os.path.join(str(tmp_path), "workspace"))
        assert os.path.isdir(ws.base)


class TestClusterDir:
    def test_cluster_dir(self, tmp_path):
        ws = Workspace(base=str(tmp_path))
        path = ws.cluster_dir("testnet")
        assert path == os.path.join(str(tmp_path), "testnet-cluster")
        assert os.path.isdir(path)

    def test_cluster_dir_idempotent(self, tmp_path):
        ws = Workspace(base=str(tmp_path))
        path1 = ws.cluster_dir("testnet")
        path2 = ws.cluster_dir("testnet")
        assert path1 == path2


class TestStandaloneDir:
    def test_standalone_dir(self, tmp_path):
        ws = Workspace(base=str(tmp_path))
        path = ws.standalone_dir("xrpld", "node1")
        assert path == os.path.join(str(tmp_path), "xrpld-node1")
        assert os.path.isdir(path)


class TestNodeDir:
    def test_node_dir(self, tmp_path):
        ws = Workspace(base=str(tmp_path))
        parent = ws.cluster_dir("testnet")
        path = ws.node_dir(parent, "vl")
        assert path == os.path.join(parent, "vl")
        assert os.path.isdir(path)


class TestConfigDir:
    def test_config_dir(self, tmp_path):
        ws = Workspace(base=str(tmp_path))
        parent = ws.cluster_dir("testnet")
        node = ws.node_dir(parent, "vl")
        path = ws.config_dir(node)
        assert path == os.path.join(node, "config")
        assert os.path.isdir(path)


class TestLogDir:
    def test_log_dir(self, tmp_path):
        ws = Workspace(base=str(tmp_path))
        parent = ws.cluster_dir("testnet")
        node = ws.node_dir(parent, "vl")
        path = ws.log_dir(node)
        assert path == os.path.join(node, "log")
        assert os.path.isdir(path)


class TestPackageDir:
    def test_package_dir_exists(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        ws = Workspace()
        pkg_dir = ws.package_dir
        # Should point to the xrpld_lab package directory
        assert os.path.isdir(pkg_dir)
        assert pkg_dir.endswith("xrpld_lab")
