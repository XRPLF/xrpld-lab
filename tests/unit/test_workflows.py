#!/usr/bin/env python
# coding: utf-8

"""Tests for xrpld_lab.workflows -- the LabRunner orchestrator."""

import json
import os
from unittest.mock import MagicMock, patch, call

import pytest

from xrpld_lab.models import (
    LabConfig,
    BuildSource,
    BuildType,
    DeployMode,
    NodeConfig,
    NodeDbType,
    NodeRole,
    PortSet,
    Protocol,
)
from xrpld_lab.protocol import get_spec, XRPL
from xrpld_lab.workspace import Workspace
from xrpld_lab.workflows import LabRunner, explorer_target


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def xrpl_source():
    """BuildSource for XRPL image build."""
    return BuildSource(
        protocol=Protocol.XRPL,
        build_type=BuildType.IMAGE,
        build_server="rippleci",
        build_version="3.1.1",
        owner="XRPLF",
        repo="rippled",
        image="rippleci/xrpld:3.1.1",
    )


@pytest.fixture
def standalone_lab(xrpl_source):
    """LabConfig for standalone XRPL deployment."""
    return LabConfig(
        protocol=Protocol.XRPL,
        mode=DeployMode.STANDALONE,
        build_source=xrpl_source,
        network_id=1,
        log_level="trace",
    )


@pytest.fixture
def network_lab(xrpl_source):
    """LabConfig for network XRPL deployment."""
    return LabConfig(
        protocol=Protocol.XRPL,
        mode=DeployMode.NETWORK,
        build_source=xrpl_source,
        network_id=21337,
        num_validators=2,
        num_peers=1,
        genesis=True,
        log_level="warning",
    )


@pytest.fixture
def local_lab(xrpl_source):
    """LabConfig for local network deployment."""
    return LabConfig(
        protocol=Protocol.XRPL,
        mode=DeployMode.LOCAL,
        build_source=xrpl_source,
        network_id=21337,
        num_validators=2,
        num_peers=1,
        genesis=True,
        log_level="trace",
        binary_name="xrpld",
    )


@pytest.fixture
def tmp_workspace(tmp_path):
    """Workspace rooted in a temporary directory."""
    return Workspace(base=str(tmp_path / "workspace"))


@pytest.fixture
def mock_features():
    """Fake feature content bytes."""
    return b"XRPL_FEATURE(Feature1, Supported::yes, DefaultVote::yes)\n"


@pytest.fixture
def mock_genesis():
    """Minimal genesis dict for testing."""
    return {
        "ledger": {
            "accountState": [
                {"Amendments": ["old_hash"]},
            ]
        }
    }


def _make_node(name="test", role=NodeRole.STANDALONE) -> NodeConfig:
    """Create a minimal NodeConfig for mocking."""
    return NodeConfig(
        name=name,
        index=0,
        role=role,
        protocol=Protocol.XRPL,
        network_id=1,
        ports=PortSet.for_node(0, NodeRole.STANDALONE),
    )


# ===========================================================================
# LabRunner construction
# ===========================================================================


class TestLabRunnerConstruction:
    """LabRunner.__init__ wiring."""

    def test_default_workspace_is_under_the_cwd(
        self, standalone_lab, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        runner = LabRunner(standalone_lab)
        assert runner.lab is standalone_lab
        assert runner.workspace.base == str(tmp_path / "workspace")

    def test_creates_with_custom_workspace(self, standalone_lab, tmp_workspace):
        runner = LabRunner(standalone_lab, workspace=tmp_workspace)
        assert runner.workspace is tmp_workspace

    def test_spec_matches_protocol_xrpl(self, standalone_lab, tmp_workspace):
        runner = LabRunner(standalone_lab, workspace=tmp_workspace)
        assert runner.spec is XRPL


# ===========================================================================
# run() dispatch
# ===========================================================================


class TestRunDispatch:
    """LabRunner.run() delegates to the correct private method."""

    def test_standalone_dispatches(self, standalone_lab, tmp_workspace):
        runner = LabRunner(standalone_lab, workspace=tmp_workspace)
        with patch.object(runner, "_run_standalone") as m:
            m.return_value = "xrpl-3.3.0"
            assert runner.run() == "xrpl-3.3.0"
            m.assert_called_once()

    def test_network_dispatches(self, network_lab, tmp_workspace):
        runner = LabRunner(network_lab, workspace=tmp_workspace)
        with patch.object(runner, "_run_network") as m:
            runner.run()
            m.assert_called_once()

    def test_local_dispatches(self, local_lab, tmp_workspace):
        runner = LabRunner(local_lab, workspace=tmp_workspace)
        with patch.object(runner, "_run_local_network") as m:
            runner.run()
            m.assert_called_once()


# ===========================================================================
# _run_standalone workflow
# ===========================================================================


class TestRunStandalone:
    """_run_standalone end-to-end orchestration with mocks."""

    @pytest.fixture(autouse=True)
    def setup_patches(self, standalone_lab, tmp_workspace, mock_features, mock_genesis):
        """Patch all external dependencies for standalone workflow tests."""
        self.lab = standalone_lab
        self.workspace = tmp_workspace
        self.runner = LabRunner(standalone_lab, workspace=tmp_workspace)

        # Create patches
        self.patches = {}
        self.mocks = {}

        # SourceResolver.resolve_features
        p = patch.object(
            self.runner.resolver, "resolve_features", return_value=mock_features
        )
        self.mocks["resolve_features"] = p.start()
        self.patches["resolve_features"] = p

        # NodeFactory.create_standalone
        self.fake_node = _make_node("3.1.1")
        p = patch(
            "xrpld_lab.workflows.NodeFactory.create_standalone",
            return_value=self.fake_node,
        )
        self.mocks["create_standalone"] = p.start()
        self.patches["create_standalone"] = p

        # XrpldCfgBuilder
        mock_cfg_builder = MagicMock()
        mock_cfg_builder.return_value.build.return_value = "[server]\nport_rpc_public\n"
        p = patch("xrpld_lab.workflows.XrpldCfgBuilder", mock_cfg_builder)
        self.mocks["cfg_builder"] = p.start()
        self.patches["cfg_builder"] = p

        # ValidatorsTxtBuilder
        mock_vl_builder = MagicMock()
        mock_vl_builder.return_value.build.return_value = "[validator_list_sites]\n"
        p = patch("xrpld_lab.workflows.ValidatorsTxtBuilder", mock_vl_builder)
        self.mocks["vl_builder"] = p.start()
        self.patches["vl_builder"] = p

        # save_config
        p = patch("xrpld_lab.workflows.save_config")
        self.mocks["save_config"] = p.start()
        self.patches["save_config"] = p

        # parse_amendments
        p = patch(
            "xrpld_lab.workflows.parse_amendments",
            return_value={"Feature1": "HASH1"},
        )
        self.mocks["parse_amendments"] = p.start()
        self.patches["parse_amendments"] = p

        # get_feature_lines_from_content
        p = patch(
            "xrpld_lab.workflows.get_feature_lines_from_content",
            return_value=["XRPL_FEATURE(Feature1, Supported::yes, DefaultVote::yes)"],
        )
        self.mocks["get_feature_lines"] = p.start()
        self.patches["get_feature_lines"] = p

        # update_genesis
        p = patch(
            "xrpld_lab.workflows.update_genesis",
            return_value=mock_genesis,
        )
        self.mocks["update_genesis"] = p.start()
        self.patches["update_genesis"] = p

        # write_file
        p = patch("xrpld_lab.workflows.write_file")
        self.mocks["write_file"] = p.start()
        self.patches["write_file"] = p

        # DockerfileBuilder.build
        p = patch(
            "xrpld_lab.workflows.DockerfileBuilder.build",
            return_value="FROM ubuntu:jammy\n",
        )
        self.mocks["dockerfile_build"] = p.start()
        self.patches["dockerfile_build"] = p

        # ComposeBuilder
        mock_compose = MagicMock()
        p = patch("xrpld_lab.workflows.ComposeBuilder", return_value=mock_compose)
        self.mocks["compose_builder"] = p.start()
        self.patches["compose_builder"] = p
        self.mock_compose_instance = mock_compose

        # ScriptBuilder
        p = patch(
            "xrpld_lab.workflows.ScriptBuilder.standalone_start",
            return_value="#! /bin/bash\ndocker compose up\n",
        )
        self.mocks["script_start"] = p.start()
        self.patches["script_start"] = p

        p = patch(
            "xrpld_lab.workflows.ScriptBuilder.standalone_stop",
            return_value="#! /bin/bash\ndocker compose down\n",
        )
        self.mocks["script_stop"] = p.start()
        self.patches["script_stop"] = p

        # shutil.copy2 (for entrypoint copy)
        p = patch("xrpld_lab.workflows.shutil.copy2")
        self.mocks["copy2"] = p.start()
        self.patches["copy2"] = p

        # os.path.exists for entrypoint check
        p = patch("xrpld_lab.workflows.os.path.exists", return_value=True)
        self.mocks["path_exists"] = p.start()
        self.patches["path_exists"] = p

        # os.chmod
        p = patch("xrpld_lab.workflows.os.chmod")
        self.mocks["chmod"] = p.start()
        self.patches["chmod"] = p

        yield

        for p in self.patches.values():
            p.stop()

    def test_stages_the_binary_beside_the_dockerfile(self):
        with patch.object(self.runner, "_stage_binary") as m:
            name = self.runner._run_standalone()

        dest = m.call_args.args[1]
        assert os.path.basename(dest) == "xrpld.3.1.1"
        assert os.path.basename(os.path.dirname(dest)) == name

    def test_calls_resolve_features(self):
        self.runner._run_standalone()
        self.mocks["resolve_features"].assert_called_once_with(
            self.lab.build_source, self.runner.spec
        )

    def test_calls_node_factory_create_standalone(self):
        self.runner._run_standalone()
        self.mocks["create_standalone"].assert_called_once_with(
            protocol=Protocol.XRPL,
            name="3.1.1",
            network_id=1,
            log_level="trace",
            node_db_type=NodeDbType.NUDB,
            vl_keys=None,
            datagram_monitor=None,
        )

    def test_public_vl_key_reaches_the_node(self):
        self.lab.public_vl_key = "EDPUBLISHER"
        self.runner._run_standalone()
        assert self.mocks["create_standalone"].call_args.kwargs["vl_keys"] == [
            "EDPUBLISHER"
        ]

    def test_builds_cfg_with_xrpld_cfg_builder(self):
        self.runner._run_standalone()
        self.mocks["cfg_builder"].assert_called_once_with(self.fake_node)
        self.mocks["cfg_builder"].return_value.build.assert_called_once()

    def test_builds_validators_txt(self):
        self.runner._run_standalone()
        self.mocks["vl_builder"].assert_called_once_with(self.fake_node, genesis=False)
        self.mocks["vl_builder"].return_value.build.assert_called_once()

    def test_calls_save_config(self):
        self.runner._run_standalone()
        self.mocks["save_config"].assert_called_once()
        args = self.mocks["save_config"].call_args
        assert args[0][0] == "xrpl"  # protocol_name
        assert "config" in args[0][1]  # cfg_path contains config

    def test_calls_parse_amendments(self):
        self.runner._run_standalone()
        self.mocks["get_feature_lines"].assert_called_once()
        self.mocks["parse_amendments"].assert_called_once()

    def test_calls_update_genesis(self):
        self.runner._run_standalone()
        self.mocks["update_genesis"].assert_called_once_with(
            {"Feature1": "HASH1"}, "xrpl", genesis_path=None, preload_entries=None
        )

    def test_writes_genesis_json(self):
        self.runner._run_standalone()
        # Find the write_file call for genesis.json
        genesis_calls = [
            c
            for c in self.mocks["write_file"].call_args_list
            if "genesis.json" in str(c)
        ]
        assert len(genesis_calls) == 1

    def test_creates_dockerfile(self):
        self.runner._run_standalone()
        self.mocks["dockerfile_build"].assert_called_once()
        # Verify key args
        call_kwargs = self.mocks["dockerfile_build"].call_args
        assert call_kwargs[1]["protocol"] == "xrpl" or call_kwargs[0][0] == "xrpl"

    def test_writes_dockerfile(self):
        self.runner._run_standalone()
        dockerfile_calls = [
            c for c in self.mocks["write_file"].call_args_list if "Dockerfile" in str(c)
        ]
        assert len(dockerfile_calls) == 1

    def test_creates_docker_compose(self):
        self.runner._run_standalone()
        self.mocks["compose_builder"].assert_called_once_with("standalone-network")
        self.mock_compose_instance.add_standalone_service.assert_called_once()
        self.mock_compose_instance.add_explorer_service.assert_called_once()
        self.mock_compose_instance.write.assert_called_once()

    def test_creates_start_script(self):
        self.runner._run_standalone()
        self.mocks["script_start"].assert_called_once()
        # Verify start.sh was written
        start_calls = [
            c for c in self.mocks["write_file"].call_args_list if "start.sh" in str(c)
        ]
        assert len(start_calls) == 1

    def test_creates_stop_script(self):
        self.runner._run_standalone()
        self.mocks["script_stop"].assert_called_once()
        stop_calls = [
            c for c in self.mocks["write_file"].call_args_list if "stop.sh" in str(c)
        ]
        assert len(stop_calls) == 1

    def test_no_ipfs_by_default(self):
        self.runner._run_standalone()
        self.mock_compose_instance.add_ipfs_service.assert_not_called()

    def test_adds_ipfs_when_configured(self, xrpl_source):
        lab = LabConfig(
            protocol=Protocol.XRPL,
            mode=DeployMode.STANDALONE,
            build_source=xrpl_source,
            network_id=1,
            add_ipfs=True,
        )
        runner = LabRunner(lab, workspace=self.workspace)
        # Re-patch the resolver on the new runner
        runner.resolver = self.runner.resolver
        runner._run_standalone()
        self.mock_compose_instance.add_ipfs_service.assert_called_once_with("xrpl")

    def test_copies_entrypoint(self):
        self.runner._run_standalone()
        self.mocks["copy2"].assert_called_once()


# ===========================================================================
# _run_network workflow
# ===========================================================================


class TestRunNetwork:
    """_run_network orchestration with mocks."""

    @pytest.fixture(autouse=True)
    def setup_patches(self, network_lab, tmp_workspace, mock_features, mock_genesis):
        self.lab = network_lab
        self.workspace = tmp_workspace
        self.runner = LabRunner(network_lab, workspace=tmp_workspace)

        self.patches = {}
        self.mocks = {}

        # SourceResolver.resolve_features
        p = patch.object(
            self.runner.resolver, "resolve_features", return_value=mock_features
        )
        self.mocks["resolve_features"] = p.start()
        self.patches["resolve_features"] = p

        # get_feature_lines_from_content
        p = patch(
            "xrpld_lab.workflows.get_feature_lines_from_content",
            return_value=["XRPL_FEATURE(Feature1, Supported::yes, DefaultVote::yes)"],
        )
        self.mocks["get_feature_lines"] = p.start()
        self.patches["get_feature_lines"] = p

        # parse_amendments
        p = patch(
            "xrpld_lab.workflows.parse_amendments",
            return_value={"Feature1": "HASH1"},
        )
        self.mocks["parse_amendments"] = p.start()
        self.patches["parse_amendments"] = p

        # update_genesis
        p = patch(
            "xrpld_lab.workflows.update_genesis",
            return_value=mock_genesis,
        )
        self.mocks["update_genesis"] = p.start()
        self.patches["update_genesis"] = p

        # NodeFactory methods
        self.fake_validator = _make_node("vnode1", NodeRole.VALIDATOR)
        self.fake_peer = _make_node("pnode1", NodeRole.PEER)

        p = patch(
            "xrpld_lab.workflows.NodeFactory.create_validator",
            return_value=self.fake_validator,
        )
        self.mocks["create_validator"] = p.start()
        self.patches["create_validator"] = p

        p = patch(
            "xrpld_lab.workflows.NodeFactory.create_peer",
            return_value=self.fake_peer,
        )
        self.mocks["create_peer"] = p.start()
        self.patches["create_peer"] = p

        # Config builders
        mock_cfg = MagicMock()
        mock_cfg.return_value.build.return_value = "[server]\n"
        p = patch("xrpld_lab.workflows.XrpldCfgBuilder", mock_cfg)
        self.mocks["cfg_builder"] = p.start()
        self.patches["cfg_builder"] = p

        mock_vl = MagicMock()
        mock_vl.return_value.build.return_value = "[validators]\n"
        p = patch("xrpld_lab.workflows.ValidatorsTxtBuilder", mock_vl)
        self.mocks["vl_builder"] = p.start()
        self.patches["vl_builder"] = p

        # save_config
        p = patch("xrpld_lab.workflows.save_config")
        self.mocks["save_config"] = p.start()
        self.patches["save_config"] = p

        # write_file
        p = patch("xrpld_lab.workflows.write_file")
        self.mocks["write_file"] = p.start()
        self.patches["write_file"] = p

        # write_executable
        p = patch("xrpld_lab.workflows.write_executable")
        self.mocks["write_executable"] = p.start()
        self.patches["write_executable"] = p

        # DockerfileBuilder
        p = patch(
            "xrpld_lab.workflows.DockerfileBuilder.build",
            return_value="FROM ubuntu:jammy\n",
        )
        self.mocks["dockerfile_build"] = p.start()
        self.patches["dockerfile_build"] = p

        # ComposeBuilder
        mock_compose = MagicMock()
        p = patch("xrpld_lab.workflows.ComposeBuilder", return_value=mock_compose)
        self.mocks["compose_builder"] = p.start()
        self.patches["compose_builder"] = p
        self.mock_compose_instance = mock_compose

        # ScriptBuilder
        p = patch(
            "xrpld_lab.workflows.ScriptBuilder.network_start",
            return_value="#! /bin/bash\n",
        )
        self.mocks["script_start"] = p.start()
        self.patches["script_start"] = p

        p = patch(
            "xrpld_lab.workflows.ScriptBuilder.network_stop",
            return_value="#! /bin/bash\n",
        )
        self.mocks["script_stop"] = p.start()
        self.patches["script_stop"] = p

        # shutil.copy2
        p = patch("xrpld_lab.workflows.shutil.copy2")
        self.mocks["copy2"] = p.start()
        self.patches["copy2"] = p

        # os.path.exists
        p = patch("xrpld_lab.workflows.os.path.exists", return_value=True)
        self.mocks["path_exists"] = p.start()
        self.patches["path_exists"] = p

        # os.chmod
        p = patch("xrpld_lab.workflows.os.chmod")
        self.mocks["chmod"] = p.start()
        self.patches["chmod"] = p

        # Mock VL key generation (PublisherClient and ValidatorClient)
        mock_publisher = MagicMock()
        mock_publisher.return_value.get_keys.return_value = {
            "publicKey": "ED_VL_PUB_KEY",
        }
        p = patch("xrpld_lab.workflows.PublisherClient", mock_publisher)
        self.mocks["publisher_client"] = p.start()
        self.patches["publisher_client"] = p
        self.mock_publisher_instance = mock_publisher.return_value

        mock_validator = MagicMock()
        mock_validator.return_value.get_keys.return_value = {
            "public_key": "ED_VAL_PUB_KEY",
        }
        mock_validator.return_value.read_token.return_value = "TOKEN_DATA"
        mock_validator.return_value.read_manifest.return_value = "MANIFEST_DATA"
        p = patch("xrpld_lab.workflows.ValidatorClient", mock_validator)
        self.mocks["validator_client"] = p.start()
        self.patches["validator_client"] = p

        # shutil.copyfile
        p = patch("xrpld_lab.workflows.shutil.copyfile")
        self.mocks["copyfile"] = p.start()
        self.patches["copyfile"] = p

        yield

        for p in self.patches.values():
            p.stop()

    def test_creates_vl_keys(self):
        self.runner._run_network()
        self.mocks["publisher_client"].assert_called()
        self.mock_publisher_instance.get_keys.assert_called()

    def test_generates_validator_nodes(self):
        self.runner._run_network()
        # 2 validators configured
        assert self.mocks["create_validator"].call_count == 2

    def test_generates_peer_nodes(self):
        self.runner._run_network()
        # 1 peer configured
        assert self.mocks["create_peer"].call_count == 1

    def test_writes_configs_for_each_node(self):
        self.runner._run_network()
        # 2 validators + 1 peer = 3 config writes
        assert self.mocks["save_config"].call_count == 3

    def test_creates_docker_compose_with_services(self):
        self.runner._run_network()
        self.mocks["compose_builder"].assert_called_once()
        # Should add node services for validators and peers
        assert self.mock_compose_instance.add_node_service.call_count == 3
        # VL and explorer services
        self.mock_compose_instance.add_vl_service.assert_called_once()
        self.mock_compose_instance.add_explorer_service.assert_called_once_with(
            ws_port=PortSet.for_node(1, NodeRole.PEER).ws_admin, standalone=False
        )

    def test_creates_start_stop_scripts(self):
        self.runner._run_network()
        self.mocks["script_start"].assert_called_once()
        self.mocks["script_stop"].assert_called_once()

    def test_signs_unl(self):
        self.runner._run_network()
        self.mock_publisher_instance.sign_unl.assert_called_once()

    def test_copies_nginx_dockerfile(self):
        self.runner._run_network()
        # Should copy nginx dockerfile for VL service
        copyfile_calls = [
            c for c in self.mocks["copyfile"].call_args_list if "nginx" in str(c)
        ]
        assert len(copyfile_calls) == 1


# ===========================================================================
# _run_local_network workflow
# ===========================================================================


class TestRunLocalNetwork:
    """_run_local_network orchestration with mocks."""

    @pytest.fixture(autouse=True)
    def setup_patches(self, local_lab, tmp_workspace, mock_features, mock_genesis):
        self.lab = local_lab
        self.workspace = tmp_workspace
        self.runner = LabRunner(local_lab, workspace=tmp_workspace)

        self.patches = {}
        self.mocks = {}

        # get_feature_lines_from_path
        p = patch(
            "xrpld_lab.workflows.get_feature_lines_from_path",
            return_value=["XRPL_FEATURE(Feature1, Supported::yes, DefaultVote::yes)"],
        )
        self.mocks["get_feature_lines_path"] = p.start()
        self.patches["get_feature_lines_path"] = p

        # parse_amendments
        p = patch(
            "xrpld_lab.workflows.parse_amendments",
            return_value={"Feature1": "HASH1"},
        )
        self.mocks["parse_amendments"] = p.start()
        self.patches["parse_amendments"] = p

        # update_genesis
        p = patch(
            "xrpld_lab.workflows.update_genesis",
            return_value=mock_genesis,
        )
        self.mocks["update_genesis"] = p.start()
        self.patches["update_genesis"] = p

        # NodeFactory local methods
        self.fake_validator = _make_node("vnode1", NodeRole.VALIDATOR)
        self.fake_peer = _make_node("pnode1", NodeRole.PEER)

        p = patch(
            "xrpld_lab.workflows.NodeFactory.create_local_validator",
            return_value=self.fake_validator,
        )
        self.mocks["create_local_validator"] = p.start()
        self.patches["create_local_validator"] = p

        p = patch(
            "xrpld_lab.workflows.NodeFactory.create_local_peer",
            return_value=self.fake_peer,
        )
        self.mocks["create_local_peer"] = p.start()
        self.patches["create_local_peer"] = p

        # Config builders
        mock_cfg = MagicMock()
        mock_cfg.return_value.build.return_value = "[server]\n"
        p = patch("xrpld_lab.workflows.XrpldCfgBuilder", mock_cfg)
        self.mocks["cfg_builder"] = p.start()
        self.patches["cfg_builder"] = p

        mock_vl = MagicMock()
        mock_vl.return_value.build.return_value = "[validators]\n"
        p = patch("xrpld_lab.workflows.ValidatorsTxtBuilder", mock_vl)
        self.mocks["vl_builder"] = p.start()
        self.patches["vl_builder"] = p

        # save_config
        p = patch("xrpld_lab.workflows.save_config")
        self.mocks["save_config"] = p.start()
        self.patches["save_config"] = p

        # write_file
        p = patch("xrpld_lab.workflows.write_file")
        self.mocks["write_file"] = p.start()
        self.patches["write_file"] = p

        # write_executable
        p = patch("xrpld_lab.workflows.write_executable")
        self.mocks["write_executable"] = p.start()
        self.patches["write_executable"] = p

        # ComposeBuilder
        mock_compose = MagicMock()
        p = patch("xrpld_lab.workflows.ComposeBuilder", return_value=mock_compose)
        self.mocks["compose_builder"] = p.start()
        self.patches["compose_builder"] = p
        self.mock_compose_instance = mock_compose

        # ScriptBuilder local methods
        p = patch(
            "xrpld_lab.workflows.ScriptBuilder.local_network_start",
            return_value="#! /bin/bash\n",
        )
        self.mocks["script_start"] = p.start()
        self.patches["script_start"] = p

        p = patch(
            "xrpld_lab.workflows.ScriptBuilder.local_network_stop",
            return_value="#! /bin/bash\n",
        )
        self.mocks["script_stop"] = p.start()
        self.patches["script_stop"] = p

        # os.path.exists
        p = patch("xrpld_lab.workflows.os.path.exists", return_value=True)
        self.mocks["path_exists"] = p.start()
        self.patches["path_exists"] = p

        # os.chmod
        p = patch("xrpld_lab.workflows.os.chmod")
        self.mocks["chmod"] = p.start()
        self.patches["chmod"] = p

        # VL key generation
        mock_publisher = MagicMock()
        mock_publisher.return_value.get_keys.return_value = {
            "publicKey": "ED_VL_PUB_KEY",
        }
        p = patch("xrpld_lab.workflows.PublisherClient", mock_publisher)
        self.mocks["publisher_client"] = p.start()
        self.patches["publisher_client"] = p
        self.mock_publisher_instance = mock_publisher.return_value

        mock_validator = MagicMock()
        mock_validator.return_value.get_keys.return_value = {
            "public_key": "ED_VAL_PUB_KEY",
        }
        mock_validator.return_value.read_token.return_value = "TOKEN_DATA"
        mock_validator.return_value.read_manifest.return_value = "MANIFEST_DATA"
        p = patch("xrpld_lab.workflows.ValidatorClient", mock_validator)
        self.mocks["validator_client"] = p.start()
        self.patches["validator_client"] = p

        # shutil.copyfile
        p = patch("xrpld_lab.workflows.shutil.copyfile")
        self.mocks["copyfile"] = p.start()
        self.patches["copyfile"] = p

        # shutil.copy2 (binary copy into each node dir)
        p = patch("xrpld_lab.workflows.shutil.copy2")
        self.mocks["copy2"] = p.start()
        self.patches["copy2"] = p

        yield

        for p in self.patches.values():
            p.stop()

    def test_creates_local_validator_nodes(self):
        self.runner._run_local_network()
        # 2 validators
        assert self.mocks["create_local_validator"].call_count == 2

    def test_creates_local_peer_nodes(self):
        self.runner._run_local_network()
        # 1 peer
        assert self.mocks["create_local_peer"].call_count == 1

    def test_writes_configs_for_each_node(self):
        self.runner._run_local_network()
        # 2 validators + 1 peer = 3
        assert self.mocks["save_config"].call_count == 3

    def test_creates_compose_for_docker_services_only(self):
        self.runner._run_local_network()
        self.mocks["compose_builder"].assert_called_once()
        # Local network only has VL + explorer in Docker, no node services
        self.mock_compose_instance.add_node_service.assert_not_called()
        self.mock_compose_instance.add_vl_service.assert_called_once()
        self.mock_compose_instance.add_explorer_service.assert_called_once_with(
            ws_port=6016, standalone=False
        )

    def test_explorer_and_start_script_follow_pnode1_under_port_offset(self):
        self.lab.port_offset = 1000
        self.runner._run_local_network()
        self.mock_compose_instance.add_explorer_service.assert_called_once_with(
            ws_port=7016, standalone=False
        )
        kwargs = self.mocks["script_start"].call_args.kwargs
        assert (kwargs["ws_node"], kwargs["ws_port"]) == ("pnode1", 7016)

    def test_explorer_follows_vnode1_without_peers(self):
        self.lab.num_peers = 0
        self.runner._run_local_network()
        self.mock_compose_instance.add_explorer_service.assert_called_once_with(
            ws_port=6106, standalone=False
        )
        kwargs = self.mocks["script_start"].call_args.kwargs
        assert (kwargs["ws_node"], kwargs["ws_port"]) == ("vnode1", 6106)

    def test_creates_start_stop_scripts(self):
        self.runner._run_local_network()
        self.mocks["script_start"].assert_called_once()
        self.mocks["script_stop"].assert_called_once()

    def test_signs_unl(self):
        self.runner._run_local_network()
        self.mock_publisher_instance.sign_unl.assert_called_once()


# ---------------------------------------------------------------------------
# Ansible integration in _run_network
# ---------------------------------------------------------------------------


def _network_lab(ansible):
    """Two-validator, one-peer network LabConfig with an optional AnsibleConfig."""
    return LabConfig(
        protocol=Protocol.XRPL,
        mode=DeployMode.NETWORK,
        build_source=BuildSource(
            protocol=Protocol.XRPL,
            build_type=BuildType.IMAGE,
            build_server="rippleci",
            build_version="3.1.1",
            owner="XRPLF",
            repo="rippled",
            image="rippleci/xrpld:3.1.1",
        ),
        network_id=21337,
        num_validators=2,
        num_peers=1,
        genesis=True,
        log_level="warning",
        ansible=ansible,
    )


def _ansible_config():
    from xrpld_lab.models import AnsibleConfig

    return AnsibleConfig(
        ssh_port=22,
        ssh_user="ubuntu",
        ssh_key_path="~/.ssh/id_rsa",
        vips=["10.0.0.1", "10.0.0.2"],
        pips=["10.0.0.3"],
    )


_NETWORK_PATCH_TARGETS = [
    ("resolver", "xrpld_lab.workflows.SourceResolver"),
    ("publisher", "xrpld_lab.workflows.PublisherClient"),
    ("validator_client", "xrpld_lab.workflows.ValidatorClient"),
    ("node_factory", "xrpld_lab.workflows.NodeFactory"),
    ("cfg_builder", "xrpld_lab.workflows.XrpldCfgBuilder"),
    ("vl_builder", "xrpld_lab.workflows.ValidatorsTxtBuilder"),
    ("compose_builder", "xrpld_lab.workflows.ComposeBuilder"),
    ("dockerfile_builder", "xrpld_lab.workflows.DockerfileBuilder"),
    ("script_start", "xrpld_lab.workflows.ScriptBuilder.network_start"),
    ("script_stop", "xrpld_lab.workflows.ScriptBuilder.network_stop"),
    ("parse_amendments", "xrpld_lab.workflows.parse_amendments"),
    ("update_genesis", "xrpld_lab.workflows.update_genesis"),
    ("get_lines", "xrpld_lab.workflows.get_feature_lines_from_content"),
    ("write_file", "xrpld_lab.workflows.write_file"),
    ("save_config", "xrpld_lab.workflows.save_config"),
    ("write_executable", "xrpld_lab.workflows.write_executable"),
    ("makedirs", "xrpld_lab.workflows.os.makedirs"),
    ("chdir", "xrpld_lab.workflows.os.chdir"),
    ("getcwd", "xrpld_lab.workflows.os.getcwd"),
    ("exists", "xrpld_lab.workflows.os.path.exists"),
    ("copy2", "xrpld_lab.workflows.shutil.copy2"),
    ("copyfile", "xrpld_lab.workflows.shutil.copyfile"),
    ("ansible_builder", "xrpld_lab.workflows.AnsibleBuilder"),
]


class _NetworkRunnerSetup:
    """Patches every collaborator of _run_network; subclasses set ``ansible``."""

    ansible: bool

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_workspace):
        self.lab = _network_lab(_ansible_config() if self.ansible else None)
        self.workspace = tmp_workspace
        self.patches = {}
        self.mocks = {}
        for name, target in _NETWORK_PATCH_TARGETS:
            self.patches[name] = patch(target)
            self.mocks[name] = self.patches[name].start()

        self.mocks["resolver"].return_value.resolve_features.return_value = b"feature"
        self.mocks["resolver"].return_value.resolve_repo_config.return_value = {}
        self.mocks["get_lines"].return_value = ["line"]
        self.mocks["parse_amendments"].return_value = {}
        self.mocks["update_genesis"].return_value = {"ledger": {}}
        self.mocks["exists"].return_value = True
        self.mocks["getcwd"].return_value = "/old"
        self.mocks["script_start"].return_value = "#!/bin/bash"
        self.mocks["script_stop"].return_value = "#!/bin/bash"

        mock_pub = MagicMock()
        mock_pub.get_keys.return_value = {"publicKey": "EDPUBKEY"}
        self.mocks["publisher"].return_value = mock_pub

        mock_vc = MagicMock()
        mock_vc.get_keys.return_value = {"public_key": "VALKEY"}
        mock_vc.read_token.return_value = "token"
        mock_vc.read_manifest.return_value = "manifest"
        self.mocks["validator_client"].return_value = mock_vc

        mock_node = _make_node("vnode1", NodeRole.VALIDATOR)
        self.mocks["node_factory"].create_validator.return_value = mock_node
        self.mocks["node_factory"].create_peer.return_value = mock_node

        mock_cfg = MagicMock()
        mock_cfg.build.return_value = "[server]\nport=5005"
        self.mocks["cfg_builder"].return_value = mock_cfg
        self.mocks["vl_builder"].return_value = mock_cfg
        self.mocks["compose_builder"].return_value = MagicMock()
        self.mocks["dockerfile_builder"].build = MagicMock(return_value="FROM ubuntu")
        self.mocks["ansible_builder"].return_value = MagicMock()

        self.runner = LabRunner(self.lab, self.workspace)
        yield
        for p in self.patches.values():
            p.stop()


class TestRunNetworkWithAnsible(_NetworkRunnerSetup):
    """Test that _run_network calls AnsibleBuilder when lab.ansible is set."""

    ansible = True

    def test_ansible_builder_called_when_config_set(self):
        self.runner._run_network()
        self.mocks["ansible_builder"].assert_called_once()

    def test_ansible_builder_adds_validators(self):
        self.runner._run_network()
        mock_ansible = self.mocks["ansible_builder"].return_value
        # 2 validators + 1 peer = 3 add_node calls
        assert mock_ansible.add_node.call_count == 3

    def test_ansible_builder_write_called(self):
        self.runner._run_network()
        mock_ansible = self.mocks["ansible_builder"].return_value
        mock_ansible.write.assert_called_once()

    def test_ansible_builder_receives_correct_ips(self):
        self.runner._run_network()
        mock_ansible = self.mocks["ansible_builder"].return_value
        calls = mock_ansible.add_node.call_args_list
        # vnode1 -> 10.0.0.1, vnode2 -> 10.0.0.2, pnode1 -> 10.0.0.3
        assert calls[0].kwargs["ip"] == "10.0.0.1"
        assert calls[1].kwargs["ip"] == "10.0.0.2"
        assert calls[2].kwargs["ip"] == "10.0.0.3"

    def test_ansible_builder_receives_correct_roles(self):
        self.runner._run_network()
        mock_ansible = self.mocks["ansible_builder"].return_value
        calls = mock_ansible.add_node.call_args_list
        assert calls[0].kwargs["role"] == "validator"
        assert calls[1].kwargs["role"] == "validator"
        assert calls[2].kwargs["role"] == "peer"


class TestRunNetworkWithoutAnsible(_NetworkRunnerSetup):
    """Test that _run_network does NOT call AnsibleBuilder when ansible is None."""

    ansible = False

    def test_ansible_builder_not_called(self):
        self.runner._run_network()
        self.mocks["ansible_builder"].assert_not_called()


class TestNonGenesisNetwork(_NetworkRunnerSetup):
    """A preserved (non-genesis) network writes no genesis and keeps its identity."""

    ansible = True

    def test_no_genesis_written(self):
        self.lab.genesis = False
        LabRunner(self.lab, self.workspace).run()
        written = [c.args[0] for c in self.mocks["write_file"].call_args_list]
        assert not any("genesis.json" in p for p in written)

    def test_genesis_written_when_genesis(self):
        self.lab.genesis = True
        LabRunner(self.lab, self.workspace).run()
        written = [c.args[0] for c in self.mocks["write_file"].call_args_list]
        assert any("genesis.json" in p for p in written)

    def test_update_genesis_not_called_on_preserve(self):
        self.lab.genesis = False
        LabRunner(self.lab, self.workspace).run()
        self.mocks["update_genesis"].assert_not_called()

    def test_ansible_builder_gets_genesis_flag(self):
        self.lab.genesis = False
        LabRunner(self.lab, self.workspace).run()
        assert self.mocks["ansible_builder"].call_args.kwargs["genesis"] is False

    def test_missing_vl_keys_refused_on_preserve(self):
        self.lab.genesis = False
        self.mocks["publisher"].return_value.get_keys.return_value = None
        with pytest.raises(RuntimeError, match="no VL publisher keys"):
            LabRunner(self.lab, self.workspace).run()

    def test_missing_validator_key_refused_on_preserve(self):
        self.lab.genesis = False
        self.mocks["exists"].return_value = False
        with pytest.raises(RuntimeError, match="would change the"):
            LabRunner(self.lab, self.workspace).run()


class TestExplorerTarget:
    """explorer_target picks the node and admin websocket port the explorer follows."""

    def test_pnode1_when_the_cluster_has_peers(self):
        assert explorer_target(1) == ("pnode1", 6016)

    def test_vnode1_without_peers(self):
        assert explorer_target(0) == ("vnode1", 6106)

    def test_port_offset_shifts_the_port(self):
        assert explorer_target(1, 1000) == ("pnode1", 7016)
        assert explorer_target(0, 1000) == ("vnode1", 7106)


class TestStageBinary:
    """_stage_binary copies a local binary or downloads one in binary mode."""

    def _source(self, build_type, binary_path=""):
        return BuildSource(
            protocol=Protocol.XRPL,
            build_type=build_type,
            build_server="https://build.example.com",
            build_version="3.3.0",
            binary_path=binary_path,
        )

    def test_copies_a_local_binary(self, standalone_lab, tmp_workspace, tmp_path):
        local = tmp_path / "xrpld"
        local.write_bytes(b"bin")
        dest = tmp_path / "out"
        runner = LabRunner(standalone_lab, workspace=tmp_workspace)

        runner._stage_binary(self._source(BuildType.BINARY, str(local)), str(dest))

        assert dest.read_bytes() == b"bin"
        assert os.access(dest, os.X_OK)

    def test_downloads_in_binary_mode(self, standalone_lab, tmp_workspace, tmp_path):
        runner = LabRunner(standalone_lab, workspace=tmp_workspace)
        with patch.object(runner.resolver, "download_binary") as m:
            runner._stage_binary(self._source(BuildType.BINARY), str(tmp_path / "b"))

        m.assert_called_once_with(
            "https://build.example.com/3.3.0", str(tmp_path / "b")
        )

    def test_image_mode_stages_nothing(self, standalone_lab, tmp_workspace, tmp_path):
        runner = LabRunner(standalone_lab, workspace=tmp_workspace)
        with patch.object(runner.resolver, "download_binary") as m:
            runner._stage_binary(self._source(BuildType.IMAGE), str(tmp_path / "b"))

        m.assert_not_called()
        assert not (tmp_path / "b").exists()


# ===========================================================================
# Binary staging into node directories
# ===========================================================================


class TestNetworkBinaryStaging:
    """A network built from a local binary carries it in every node directory,
    which is the docker build context for both start.sh and ansible."""

    def _generate(self, tmp_path, *extra_args):
        from xrpld_lab.cli import _build_parser, build_lab_config

        binary = tmp_path / "xrpld"
        binary.write_text("#!/bin/sh\necho fake\n")
        features = tmp_path / "features.macro"
        features.write_text(
            "XRPL_FEATURE(Feature1, Supported::yes, DefaultVote::yes)\n"
        )
        args = _build_parser().parse_args(
            [
                "create:ansible",
                "--build_server",
                "https://github.com/XRPLF/rippled/tree/develop",
                "--build_version",
                "abc1234",
                "--binary_path",
                str(binary),
                "--features_file",
                str(features),
                "--vips",
                "10.0.0.1",
                "10.0.0.2",
                "--pips",
                "10.0.0.3",
                "--genesis",
                "True",
                "--num_validators",
                "2",
                "--num_peers",
                "1",
                *extra_args,
            ]
        )
        lab = build_lab_config(args)
        LabRunner(lab, Workspace(base=str(tmp_path / "ws"))).run()
        return tmp_path / "ws" / "develop-cluster"

    def test_binary_lands_in_every_node_dir(self, tmp_path):
        cluster = self._generate(tmp_path)
        for node in ("vnode1", "vnode2", "pnode1"):
            staged = cluster / node / "xrpld.develop"
            assert staged.is_file(), node
            assert os.access(staged, os.X_OK), node
            assert staged.read_text() == (tmp_path / "xrpld").read_text()
            dockerfile = (cluster / node / "Dockerfile").read_text()
            assert "FROM ubuntu:noble" in dockerfile
            assert "COPY xrpld.develop /opt/xrpld/bin/xrpld" in dockerfile

    def test_start_script_does_not_copy(self, tmp_path):
        cluster = self._generate(tmp_path)
        assert "cp " not in (cluster / "start.sh").read_text()

    def test_ansible_pulls_the_dockerfile_base_image(self, tmp_path):
        import yaml

        cluster = self._generate(tmp_path)
        nodes = {"10.0.0.1": "vnode1", "10.0.0.2": "vnode2", "10.0.0.3": "pnode1"}
        for ip, node in nodes.items():
            with open(cluster / "ansible" / "host_vars" / f"{ip}.yml") as f:
                host_vars = yaml.safe_load(f)
            assert host_vars["docker_image_name"] == "ubuntu:noble", ip
            assert host_vars["docker_build_tag"] == f"{node}:abc1234", ip
            assert host_vars["build_context"] == str(cluster / node)
