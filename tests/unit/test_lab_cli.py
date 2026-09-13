#!/usr/bin/env python
# coding: utf-8

"""Tests for xrpld_lab.cli — the new thin CLI layer.

Covers:
- _build_parser: subcommand existence and default values
- build_lab_config: protocol-specific defaults and arg passthrough
- main(): dispatch to LabRunner and operational commands
"""

import argparse
from unittest.mock import patch, MagicMock

import pytest

from xrpld_lab.cli import (
    _build_parser,
    build_lab_config,
    main,
    _flatten_ips,
    _DEFAULT_VL_KEY,
)
from xrpld_lab.models import (
    BuildType,
    DeployMode,
    NodeDbType,
    Protocol,
)
from xrpld_lab.protocol import XRPL


# -------------------------------------------------------------------------
# _build_parser tests
# -------------------------------------------------------------------------


class TestBuildParser:
    """Test that _build_parser creates the expected subcommands and defaults."""

    def test_parser_creates_without_error(self):
        parser = _build_parser()
        assert parser is not None

    def test_up_standalone_defaults(self):
        parser = _build_parser()
        args = parser.parse_args(["up:standalone"])
        assert args.command == "up:standalone"
        assert args.log_level == "trace"
        assert args.public_key == _DEFAULT_VL_KEY
        assert args.protocol == "xrpl"
        assert args.network_id is None
        assert args.network_type == "standalone"
        assert args.server is None
        assert args.version is None
        assert args.ipfs is False
        assert args.nodedb_type == "NuDB"

    @pytest.mark.parametrize("value", ["False", "false", "0"])
    def test_up_standalone_ipfs_false_values(self, value):
        args = _build_parser().parse_args(["up:standalone", "--ipfs", value])
        assert args.ipfs is False

    @pytest.mark.parametrize("value", ["True", "true", "1"])
    def test_up_standalone_ipfs_true_values(self, value):
        args = _build_parser().parse_args(["up:standalone", "--ipfs", value])
        assert args.ipfs is True

    @pytest.mark.parametrize("command", ["up:standalone", "create:network", "up:local"])
    def test_nodedb_type_rwdb_is_rejected(self, command):
        with pytest.raises(SystemExit):
            _build_parser().parse_args([command, "--nodedb_type", "rwdb"])

    def test_create_network_defaults(self):
        parser = _build_parser()
        args = parser.parse_args(["create:network"])
        assert args.command == "create:network"
        assert args.log_level == "trace"
        assert args.protocol == "xrpl"
        assert args.num_validators == 3
        assert args.num_peers == 1
        assert args.network_id is None
        assert args.build_server is None
        assert args.build_version is None
        assert args.genesis is None
        assert args.quorum is None
        assert args.nodedb_type == "NuDB"
        assert args.local is False
        assert args.binary_name == "xrpld"

    def test_operational_commands_exist(self):
        parser = _build_parser()
        # up
        args = parser.parse_args(["up", "--name", "my-net"])
        assert args.command == "up"
        assert args.name == "my-net"
        # down
        args = parser.parse_args(["down", "--name", "my-net"])
        assert args.command == "down"
        assert args.name == "my-net"
        # remove
        args = parser.parse_args(["remove", "--name", "my-net"])
        assert args.command == "remove"
        assert args.name == "my-net"

    # -- New operational subparsers --

    def test_down_standalone_defaults(self):
        parser = _build_parser()
        args = parser.parse_args(["down:standalone"])
        assert args.command == "down:standalone"
        assert args.name is None
        assert args.protocol == "xrpl"
        assert args.version is None

    def test_down_standalone_with_name(self):
        parser = _build_parser()
        args = parser.parse_args(["down:standalone", "--name", "my-standalone"])
        assert args.name == "my-standalone"

    def test_down_standalone_with_version(self):
        parser = _build_parser()
        args = parser.parse_args(["down:standalone", "--version", "1.0.0"])
        assert args.version == "1.0.0"
        assert args.protocol == "xrpl"

    def test_up_local_defaults(self):
        parser = _build_parser()
        args = parser.parse_args(["up:local"])
        assert args.command == "up:local"
        assert args.log_level == "trace"
        assert args.public_key == _DEFAULT_VL_KEY
        assert args.protocol == "xrpl"
        assert args.network_type == "standalone"
        assert args.network_id is None
        assert args.nodedb_type == "NuDB"

    def test_down_local_exists(self):
        parser = _build_parser()
        args = parser.parse_args(["down:local"])
        assert args.command == "down:local"

    def test_update_node_args(self):
        parser = _build_parser()
        args = parser.parse_args(
            [
                "update:node",
                "--name",
                "my-net",
                "--node_id",
                "2",
                "--node_type",
                "validator",
                "--build_server",
                "https://build.example.com",
                "--build_version",
                "2.0.0",
            ]
        )
        assert args.command == "update:node"
        assert args.name == "my-net"
        assert args.node_id == 2
        assert args.node_type == "validator"
        assert args.build_server == "https://build.example.com"
        assert args.build_version == "2.0.0"

    def test_update_node_peer_type(self):
        parser = _build_parser()
        args = parser.parse_args(
            [
                "update:node",
                "--name",
                "my-net",
                "--node_id",
                "1",
                "--node_type",
                "peer",
                "--build_server",
                "https://build.example.com",
                "--build_version",
                "1.0.0",
            ]
        )
        assert args.node_type == "peer"

    def test_vote_amendment_args(self):
        parser = _build_parser()
        args = parser.parse_args(
            [
                "vote:amendment",
                "--name",
                "my-net",
                "--amendment_name",
                "fixNFTokenRemint",
            ]
        )
        assert args.command == "vote:amendment"
        assert args.name == "my-net"
        assert args.amendment_name == "fixNFTokenRemint"
        assert args.node_id is None

    def test_vote_amendment_single_validator(self):
        args = _build_parser().parse_args(
            ["vote:amendment", "--name", "n", "--amendment_name", "x", "--node_id", "3"]
        )
        assert args.node_id == 3

    def test_vote_amendment_rejects_node_type(self):
        with pytest.raises(SystemExit):
            _build_parser().parse_args(
                [
                    "vote:amendment",
                    "--name",
                    "n",
                    "--amendment_name",
                    "x",
                    "--node_type",
                    "peer",
                ]
            )

    def test_logs_local_defaults(self):
        parser = _build_parser()
        args = parser.parse_args(["logs:local"])
        assert args.command == "logs:local"
        assert args.node is None

    def test_logs_local_with_node(self):
        parser = _build_parser()
        args = parser.parse_args(["logs:local", "--node", "vnode1"])
        assert args.node == "vnode1"

    def test_logs_standalone_defaults(self):
        parser = _build_parser()
        args = parser.parse_args(["logs:standalone"])
        assert args.command == "logs:standalone"
        assert args.protocol == "xrpl"

    def test_logs_standalone_with_protocol(self):
        parser = _build_parser()
        args = parser.parse_args(["logs:standalone", "--protocol", "xrpl"])
        assert args.protocol == "xrpl"

    def test_create_network_ansible_flag(self):
        parser = _build_parser()
        args = parser.parse_args(
            [
                "create:network",
                "--ansible",
                "--vips",
                "10.0.0.1",
                "10.0.0.2",
                "--pips",
                "10.0.0.3",
            ]
        )
        assert args.ansible is True
        assert args.vips == ["10.0.0.1", "10.0.0.2"]
        assert args.pips == ["10.0.0.3"]
        assert args.ssh_port == 20
        assert args.ssh_user == "ubuntu"

    def test_create_ansible_defaults(self):
        parser = _build_parser()
        args = parser.parse_args(
            ["create:ansible", "--vips", "10.0.0.1", "--pips", "10.0.0.2"]
        )
        assert args.command == "create:ansible"
        assert args.vips == ["10.0.0.1"]
        assert args.pips == ["10.0.0.2"]
        assert args.protocol == "xrpl"
        assert args.num_validators == 3

    def test_create_ansible_with_ssh_args(self):
        parser = _build_parser()
        args = parser.parse_args(
            [
                "create:ansible",
                "--vips",
                "10.0.0.1",
                "--pips",
                "10.0.0.2",
                "--ssh_port",
                "22",
                "--ssh_user",
                "admin",
                "--ssh_key",
                "/root/.ssh/key",
            ]
        )
        assert args.ssh_port == 22
        assert args.ssh_user == "admin"
        assert args.ssh_key == "/root/.ssh/key"

    def test_deploy_ansible_args(self):
        parser = _build_parser()
        args = parser.parse_args(["deploy:ansible", "--name", "my-cluster"])
        assert args.command == "deploy:ansible"
        assert args.name == "my-cluster"

    def test_all_subcommands_present(self):
        """The parser registers exactly the documented subcommands."""
        parser = _build_parser()
        subparsers = next(
            a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
        )
        assert set(subparsers.choices) == {
            "up:standalone",
            "create:network",
            "create:ansible",
            "deploy:ansible",
            "health",
            "up",
            "down",
            "remove",
            "down:standalone",
            "up:local",
            "down:local",
            "update:node",
            "vote:amendment",
            "node:stall",
            "node:restart",
            "logs:local",
            "logs:standalone",
        }


# -------------------------------------------------------------------------
# build_lab_config tests — up:standalone
# -------------------------------------------------------------------------


class TestBuildLabConfigStandalone:
    """Test build_lab_config for the up:standalone command."""

    def _parse(self, *extra_args):
        parser = _build_parser()
        return parser.parse_args(["up:standalone", *extra_args])

    # -- xrpl defaults --

    def test_xrpl_defaults_build_server(self):
        args = self._parse("--protocol", "xrpl")
        cfg = build_lab_config(args)
        assert cfg.build_source.build_server == "rippleci"

    def test_xrpl_defaults_build_type_image(self):
        args = self._parse("--protocol", "xrpl")
        cfg = build_lab_config(args)
        assert cfg.build_source.build_type == BuildType.IMAGE

    def test_xrpl_fallback_version(self):
        args = self._parse("--protocol", "xrpl")
        cfg = build_lab_config(args)
        assert cfg.build_source.build_version == XRPL.default_build_version

    # -- custom overrides --

    def test_custom_version_overrides_fallback(self):
        args = self._parse("--protocol", "xrpl", "--version", "2099.1.1-custom")
        cfg = build_lab_config(args)
        assert cfg.build_source.build_version == "2099.1.1-custom"

    def test_custom_server_overrides_default(self):
        args = self._parse("--protocol", "xrpl", "--server", "https://custom.build")
        cfg = build_lab_config(args)
        assert cfg.build_source.build_server == "https://custom.build"

    # -- passthrough fields --

    def test_mode_is_standalone(self):
        args = self._parse()
        cfg = build_lab_config(args)
        assert cfg.mode == DeployMode.STANDALONE

    def test_network_id_passed_through(self):
        args = self._parse("--network_id", "42")
        cfg = build_lab_config(args)
        assert cfg.network_id == 42

    def test_network_id_zero_is_honoured(self):
        args = self._parse("--network_id", "0")
        cfg = build_lab_config(args)
        assert cfg.network_id == 0

    def test_network_id_defaults_to_the_standalone_id(self):
        args = self._parse()
        cfg = build_lab_config(args)
        assert cfg.network_id == XRPL.default_standalone_network_id == 1

    def test_log_level_passed_through(self):
        args = self._parse("--log_level", "debug")
        cfg = build_lab_config(args)
        assert cfg.log_level == "debug"

    def test_nodedb_type_parsed_as_enum(self):
        args = self._parse("--nodedb_type", "Memory")
        cfg = build_lab_config(args)
        assert cfg.node_db_type == NodeDbType.MEMORY

    def test_nodedb_type_nudb(self):
        args = self._parse("--nodedb_type", "NuDB")
        cfg = build_lab_config(args)
        assert cfg.node_db_type == NodeDbType.NUDB

    def test_add_ipfs_flag(self):
        args = self._parse("--ipfs", "True")
        cfg = build_lab_config(args)
        assert cfg.add_ipfs is True

    # -- image string for xrpl --

    def test_xrpl_image_string(self):
        args = self._parse("--protocol", "xrpl")
        cfg = build_lab_config(args)
        assert cfg.build_source.image == f"rippleci/xrpld:{XRPL.default_build_version}"

    # -- github owner/repo from spec --

    def test_xrpl_owner_repo(self):
        args = self._parse("--protocol", "xrpl")
        cfg = build_lab_config(args)
        assert cfg.build_source.owner == "XRPLF"
        assert cfg.build_source.repo == "rippled"


# -------------------------------------------------------------------------
# build_lab_config tests — create:network
# -------------------------------------------------------------------------


class TestBuildLabConfigNetwork:
    """Test build_lab_config for the create:network command."""

    def _parse(self, *extra_args):
        parser = _build_parser()
        return parser.parse_args(["create:network", *extra_args])

    # -- deploy mode --

    def test_local_flag_sets_local_mode(self):
        args = self._parse("--local")
        cfg = build_lab_config(args)
        assert cfg.mode == DeployMode.LOCAL

    # -- image for xrpl --

    def test_xrpl_network_defaults_to_the_release_image(self):
        cfg = build_lab_config(self._parse("--protocol", "xrpl"))
        assert cfg.build_source.build_type == BuildType.IMAGE
        assert cfg.build_source.image == f"rippleci/xrpld:{XRPL.default_build_version}"

    def test_xrpl_ansible_image_flag_wins(self):
        args = _build_parser().parse_args(
            [
                "create:ansible",
                "--protocol",
                "xrpl",
                "--vips",
                "10.0.0.1",
                "--pips",
                "10.0.0.2",
                "--image",
                "example/xrpld:dev",
            ]
        )
        cfg = build_lab_config(args)
        assert cfg.build_source.image == "example/xrpld:dev"

    def test_no_local_flag_sets_network_mode(self):
        args = self._parse()
        cfg = build_lab_config(args)
        assert cfg.mode == DeployMode.NETWORK

    # -- passthrough fields --

    def test_num_validators_passed_through(self):
        args = self._parse("--num_validators", "5")
        cfg = build_lab_config(args)
        assert cfg.num_validators == 5

    def test_num_peers_passed_through(self):
        args = self._parse("--num_peers", "3")
        cfg = build_lab_config(args)
        assert cfg.num_peers == 3

    def test_genesis_flag(self):
        args = self._parse("--genesis", "True")
        cfg = build_lab_config(args)
        assert cfg.genesis is True

    def test_quorum_passed_through(self):
        args = self._parse("--quorum", "2")
        cfg = build_lab_config(args)
        assert cfg.quorum == 2

    def test_quorum_none_uses_effective(self):
        args = self._parse("--num_validators", "3")
        cfg = build_lab_config(args)
        assert cfg.quorum is None
        assert cfg.effective_quorum == 2

    # -- protocol defaults --

    def test_xrpl_with_local_uses_github_url(self):
        args = self._parse("--protocol", "xrpl", "--local")
        cfg = build_lab_config(args)
        assert cfg.build_source.build_server == "https://github.com/XRPLF/rippled/tree"
        assert cfg.mode == DeployMode.LOCAL

    def test_xrpl_without_local_uses_spec_default(self):
        args = self._parse("--protocol", "xrpl")
        cfg = build_lab_config(args)
        # spec.default_build_server for xrpl is "rippleci"
        assert cfg.build_source.build_server == "rippleci"

    def test_binary_name_passed_through(self):
        args = self._parse("--binary_name", "my-xrpld")
        cfg = build_lab_config(args)
        assert cfg.binary_name == "my-xrpld"

    def test_network_id_from_spec_when_omitted(self):
        args = self._parse("--protocol", "xrpl")
        cfg = build_lab_config(args)
        assert cfg.network_id == XRPL.default_network_id == 1025

    def test_network_id_zero_is_honoured(self):
        args = self._parse("--protocol", "xrpl", "--network_id", "0")
        cfg = build_lab_config(args)
        assert cfg.network_id == 0

    # -- GitHub URL mode (custom XRPL builds) --

    def test_github_url_extracts_cluster_name(self):
        args = self._parse(
            "--protocol",
            "xrpl",
            "--build_server",
            "https://github.com/XRPLF/rippled/tree/xrplf-smart-contracts",
            "--build_version",
            "abc123",
        )
        cfg = build_lab_config(args)
        assert cfg.build_source.cluster_name == "xrplf-smart-contracts"

    def test_github_url_sets_commit_hash(self):
        args = self._parse(
            "--protocol",
            "xrpl",
            "--build_server",
            "https://github.com/XRPLF/rippled/tree/xrplf-smart-contracts",
            "--build_version",
            "abc123def456",
        )
        cfg = build_lab_config(args)
        assert cfg.build_source.commit_hash == "abc123def456"

    def test_github_url_sets_binary_path(self):
        args = self._parse(
            "--protocol",
            "xrpl",
            "--build_server",
            "https://github.com/XRPLF/rippled/tree/xrplf-smart-contracts",
            "--build_version",
            "abc123",
        )
        cfg = build_lab_config(args)
        assert cfg.build_source.binary_path == "./xrpld"

    def test_github_url_custom_binary_path(self):
        args = self._parse(
            "--protocol",
            "xrpl",
            "--build_server",
            "https://github.com/XRPLF/rippled/tree/xrplf-smart-contracts",
            "--build_version",
            "abc123",
            "--binary_path",
            "/opt/builds/xrpld",
        )
        cfg = build_lab_config(args)
        assert cfg.build_source.binary_path == "/opt/builds/xrpld"

    def test_github_url_sets_build_type_binary(self):
        args = self._parse(
            "--protocol",
            "xrpl",
            "--build_server",
            "https://github.com/XRPLF/rippled/tree/feature-branch",
            "--build_version",
            "abc123",
        )
        cfg = build_lab_config(args)
        assert cfg.build_source.build_type == BuildType.BINARY

    def test_github_url_extracts_owner(self):
        args = self._parse(
            "--protocol",
            "xrpl",
            "--build_server",
            "https://github.com/Transia-RnD/rippled/tree/custom-branch",
            "--build_version",
            "abc123",
        )
        cfg = build_lab_config(args)
        assert cfg.build_source.owner == "Transia-RnD"

    def test_github_url_xrplf_rippled(self):
        args = self._parse(
            "--protocol",
            "xrpl",
            "--build_server",
            "https://github.com/XRPLF/rippled/tree/some-branch",
            "--build_version",
            "abc123",
        )
        cfg = build_lab_config(args)
        assert cfg.build_source.owner == "XRPLF"
        assert cfg.build_source.repo == "rippled"
        assert cfg.build_source.cluster_name == "some-branch"

    def test_github_url_extracts_repo(self):
        args = self._parse(
            "--protocol",
            "xrpl",
            "--build_server",
            "https://github.com/Transia-RnD/xrpld-private/tree/custom-branch",
            "--build_version",
            "abc123",
        )
        cfg = build_lab_config(args)
        assert cfg.build_source.owner == "Transia-RnD"
        assert cfg.build_source.repo == "xrpld-private"
        assert cfg.build_source.cluster_name == "custom-branch"
        assert cfg.build_source.build_type == BuildType.BINARY

    def test_github_url_without_repo_falls_back_to_rippled(self):
        args = self._parse(
            "--protocol",
            "xrpl",
            "--build_server",
            "https://github.com/Transia-RnD/",
            "--build_version",
            "abc123",
        )
        cfg = build_lab_config(args)
        assert cfg.build_source.owner == "Transia-RnD"
        assert cfg.build_source.repo == "rippled"

    def test_github_url_branch_with_slashes(self):
        args = self._parse(
            "--protocol",
            "xrpl",
            "--build_server",
            "https://github.com/XRPLF/rippled/tree/feature/my-branch",
            "--build_version",
            "abc123",
        )
        cfg = build_lab_config(args)
        assert cfg.build_source.cluster_name == "feature-my-branch"

    # -- ansible flag on create:network --

    def test_ansible_flag_creates_ansible_config(self):
        args = self._parse(
            "--ansible",
            "--vips",
            "10.0.0.1",
            "10.0.0.2",
            "10.0.0.3",
            "--pips",
            "10.0.0.4",
        )
        cfg = build_lab_config(args)
        assert cfg.ansible is not None
        assert cfg.ansible.vips == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
        assert cfg.ansible.pips == ["10.0.0.4"]

    def test_no_ansible_flag_leaves_ansible_none(self):
        args = self._parse()
        cfg = build_lab_config(args)
        assert cfg.ansible is None

    def test_ansible_ssh_args_passed_through(self):
        args = self._parse(
            "--ansible",
            "--vips",
            "10.0.0.1",
            "--pips",
            "10.0.0.2",
            "--ssh_port",
            "22",
            "--ssh_user",
            "admin",
            "--ssh_key",
            "/root/.ssh/key",
        )
        cfg = build_lab_config(args)
        assert cfg.ansible.ssh_port == 22
        assert cfg.ansible.ssh_user == "admin"
        assert cfg.ansible.ssh_key_path == "/root/.ssh/key"


# -------------------------------------------------------------------------
# build_lab_config tests — create:ansible
# -------------------------------------------------------------------------


class TestBuildLabConfigAnsible:
    """Test build_lab_config for the create:ansible command."""

    def _parse(self, *extra_args):
        parser = _build_parser()
        return parser.parse_args(
            [
                "create:ansible",
                "--vips",
                "10.0.0.1",
                "10.0.0.2",
                "--pips",
                "10.0.0.3",
                *extra_args,
            ]
        )

    def test_creates_ansible_config(self):
        args = self._parse()
        cfg = build_lab_config(args)
        assert cfg.ansible is not None
        assert cfg.ansible.vips == ["10.0.0.1", "10.0.0.2"]
        assert cfg.ansible.pips == ["10.0.0.3"]

    def test_mode_is_network(self):
        args = self._parse()
        cfg = build_lab_config(args)
        assert cfg.mode == DeployMode.NETWORK

    def test_xrpl_defaults_applied(self):
        args = self._parse("--protocol", "xrpl")
        cfg = build_lab_config(args)
        assert cfg.build_source.build_server == "rippleci"

    def test_nodedb_type_memory(self):
        args = self._parse("--nodedb_type", "Memory")
        cfg = build_lab_config(args)
        assert cfg.node_db_type == NodeDbType.MEMORY

    def test_ansible_config_from_file(self, tmp_path):
        config_file = tmp_path / "ansible.yml"
        config_file.write_text(
            "ssh_port: 22\n"
            "ssh_user: admin\n"
            "ssh_key_path: /root/.ssh/key\n"
            "vips:\n  - 192.168.1.1\n  - 192.168.1.2\n"
            "pips:\n  - 192.168.1.3\n"
        )
        parser = _build_parser()
        args = parser.parse_args(
            [
                "create:ansible",
                "--vips",
                "ignored",
                "--pips",
                "ignored",
                "--ansible_config",
                str(config_file),
            ]
        )
        cfg = build_lab_config(args)
        assert cfg.ansible.ssh_port == 22
        assert cfg.ansible.ssh_user == "admin"
        assert cfg.ansible.vips == ["192.168.1.1", "192.168.1.2"]
        assert cfg.ansible.pips == ["192.168.1.3"]

    def test_ansible_config_file_with_services(self, tmp_path):
        config_file = tmp_path / "ansible.yml"
        config_file.write_text(
            "vips:\n  - 10.0.0.1\n"
            "pips:\n  - 10.0.0.2\n"
            "services:\n"
            "  - ip: 10.0.0.3\n"
            "    name: infra\n"
            "    nginx:\n"
            "      domain: example.com\n"
            "    redis: {}\n"
        )
        parser = _build_parser()
        args = parser.parse_args(
            [
                "create:ansible",
                "--vips",
                "ignored",
                "--pips",
                "ignored",
                "--ansible_config",
                str(config_file),
            ]
        )
        cfg = build_lab_config(args)
        assert len(cfg.ansible.services) == 1
        assert cfg.ansible.services[0].ip == "10.0.0.3"
        assert cfg.ansible.services[0].name == "infra"
        assert cfg.ansible.services[0].nginx is not None
        assert cfg.ansible.services[0].nginx.domain == "example.com"
        assert cfg.ansible.services[0].redis is not None

    def test_ansible_config_file_with_status(self, tmp_path):
        config_file = tmp_path / "ansible.yml"
        config_file.write_text(
            "vips:\n  - 10.0.0.1\n"
            "pips:\n  - 10.0.0.2\n"
            "services:\n"
            "  - ip: 10.0.0.2\n"
            "    name: pnode1\n"
            "    nginx:\n"
            "      domain: example.com\n"
            "    status:\n"
            "      port: 8700\n"
        )
        parser = _build_parser()
        args = parser.parse_args(
            [
                "create:ansible",
                "--vips",
                "ignored",
                "--pips",
                "ignored",
                "--ansible_config",
                str(config_file),
            ]
        )
        cfg = build_lab_config(args)
        status = cfg.ansible.services[0].status
        assert status is not None
        assert status.port == 8700
        assert status.xdgm_port == 9999
        assert cfg.ansible.status_host is cfg.ansible.services[0]
        # Every node's [datagram_monitor] targets its own address and the sampler's XDGM port.
        assert cfg.datagram_monitor_for("10.0.0.1") == ["10.0.0.1 9999"]

    def test_ansible_config_file_status_defaults(self, tmp_path):
        config_file = tmp_path / "ansible.yml"
        config_file.write_text(
            "vips:\n  - 10.0.0.1\n"
            "pips:\n  - 10.0.0.2\n"
            "services:\n"
            "  - ip: 10.0.0.2\n"
            "    name: pnode1\n"
            "    status: {}\n"
        )
        parser = _build_parser()
        args = parser.parse_args(
            [
                "create:ansible",
                "--vips",
                "ignored",
                "--pips",
                "ignored",
                "--ansible_config",
                str(config_file),
            ]
        )
        cfg = build_lab_config(args)
        assert cfg.ansible.services[0].status.port == 8687

    def test_stream_service_forces_trace_log_level(self, tmp_path):
        config_file = tmp_path / "ansible.yml"
        config_file.write_text(
            "vips:\n  - 10.0.0.1\n"
            "pips:\n  - 10.0.0.2\n"
            "services:\n"
            "  - ip: 10.0.0.3\n"
            "    name: infra\n"
            "    stream:\n"
            "      port: 1400\n"
            "      container_name: pnode1\n"
        )
        parser = _build_parser()
        args = parser.parse_args(
            [
                "create:ansible",
                "--vips",
                "ignored",
                "--pips",
                "ignored",
                "--log_level",
                "warning",
                "--ansible_config",
                str(config_file),
            ]
        )
        cfg = build_lab_config(args)
        assert cfg.log_level == "trace"

    def test_stream_auto_creates_debug(self, tmp_path):
        """stream and debug are always paired — stream alone creates debug."""
        config_file = tmp_path / "ansible.yml"
        config_file.write_text(
            "vips:\n  - 10.0.0.1\n"
            "pips:\n  - 10.0.0.2\n"
            "services:\n"
            "  - ip: 10.0.0.3\n"
            "    name: infra\n"
            "    stream:\n"
            "      port: 1400\n"
            "      container_name: pnode1\n"
        )
        parser = _build_parser()
        args = parser.parse_args(
            [
                "create:ansible",
                "--vips",
                "ignored",
                "--pips",
                "ignored",
                "--ansible_config",
                str(config_file),
            ]
        )
        cfg = build_lab_config(args)
        assert cfg.ansible.services[0].debug is not None
        assert cfg.ansible.services[0].debug.endpoint == "ws://10.0.0.3:1400/"

    def test_debug_auto_creates_stream(self, tmp_path):
        """stream and debug are always paired — debug alone creates stream."""
        config_file = tmp_path / "ansible.yml"
        config_file.write_text(
            "vips:\n  - 10.0.0.1\n"
            "pips:\n  - 10.0.0.2\n"
            "services:\n"
            "  - ip: 10.0.0.3\n"
            "    name: infra\n"
            "    debug:\n"
            "      port: 8081\n"
        )
        parser = _build_parser()
        args = parser.parse_args(
            [
                "create:ansible",
                "--vips",
                "ignored",
                "--pips",
                "ignored",
                "--ansible_config",
                str(config_file),
            ]
        )
        cfg = build_lab_config(args)
        assert cfg.ansible.services[0].stream is not None

    def test_stream_debug_auto_creates_redis(self, tmp_path):
        """stream/debug require redis — auto-created if missing."""
        config_file = tmp_path / "ansible.yml"
        config_file.write_text(
            "vips:\n  - 10.0.0.1\n"
            "pips:\n  - 10.0.0.2\n"
            "services:\n"
            "  - ip: 10.0.0.3\n"
            "    name: infra\n"
            "    stream:\n"
            "      port: 1400\n"
            "      container_name: pnode1\n"
        )
        parser = _build_parser()
        args = parser.parse_args(
            [
                "create:ansible",
                "--vips",
                "ignored",
                "--pips",
                "ignored",
                "--ansible_config",
                str(config_file),
            ]
        )
        cfg = build_lab_config(args)
        assert cfg.ansible.services[0].redis is not None

    def test_debug_endpoint_auto_derived_from_stream(self, tmp_path):
        config_file = tmp_path / "ansible.yml"
        config_file.write_text(
            "vips:\n  - 10.0.0.1\n"
            "pips:\n  - 10.0.0.2\n"
            "services:\n"
            "  - ip: 10.0.0.3\n"
            "    name: infra\n"
            "    stream:\n"
            "      port: 1400\n"
            "      container_name: pnode1\n"
            "    debug:\n"
            "      port: 8081\n"
        )
        parser = _build_parser()
        args = parser.parse_args(
            [
                "create:ansible",
                "--vips",
                "ignored",
                "--pips",
                "ignored",
                "--ansible_config",
                str(config_file),
            ]
        )
        cfg = build_lab_config(args)
        assert cfg.ansible.services[0].debug.endpoint == "ws://10.0.0.3:1400/"

    def test_debug_explicit_endpoint_not_overridden(self, tmp_path):
        config_file = tmp_path / "ansible.yml"
        config_file.write_text(
            "vips:\n  - 10.0.0.1\n"
            "pips:\n  - 10.0.0.2\n"
            "services:\n"
            "  - ip: 10.0.0.3\n"
            "    name: infra\n"
            "    stream:\n"
            "      port: 1400\n"
            "      container_name: pnode1\n"
            "    debug:\n"
            "      endpoint: ws://custom:9999/\n"
        )
        parser = _build_parser()
        args = parser.parse_args(
            [
                "create:ansible",
                "--vips",
                "ignored",
                "--pips",
                "ignored",
                "--ansible_config",
                str(config_file),
            ]
        )
        cfg = build_lab_config(args)
        assert cfg.ansible.services[0].debug.endpoint == "ws://custom:9999/"

    def test_no_services_keeps_log_level(self):
        parser = _build_parser()
        args = parser.parse_args(
            [
                "create:ansible",
                "--vips",
                "10.0.0.1",
                "--pips",
                "10.0.0.2",
                "--log_level",
                "warning",
            ]
        )
        cfg = build_lab_config(args)
        assert cfg.log_level == "warning"


# -------------------------------------------------------------------------
# main() tests
# -------------------------------------------------------------------------


class TestMain:
    """Test main() dispatches correctly."""

    @pytest.fixture(autouse=True)
    def _run_in_tmp_dir(self, tmp_path, monkeypatch):
        """main() builds Workspace() under the cwd; keep that out of the repo."""
        monkeypatch.chdir(tmp_path)

    @patch("xrpld_lab.cli.run_start_script")
    @patch("xrpld_lab.cli.LabRunner")
    @patch("xrpld_lab.cli.build_lab_config")
    def test_up_standalone_calls_runner(self, mock_build, mock_runner_cls, mock_start):
        mock_config = MagicMock()
        mock_build.return_value = mock_config
        mock_runner = MagicMock()
        mock_runner.run.return_value = "xrpl-3.3.0"
        mock_runner_cls.return_value = mock_runner

        with patch("sys.argv", ["xrpld-lab", "up:standalone", "--protocol", "xrpl"]):
            main()

        mock_build.assert_called_once()
        assert mock_runner_cls.call_count == 1
        assert mock_runner_cls.call_args.args[0] is mock_config
        mock_runner.run.assert_called_once()
        mock_start.assert_called_once()
        assert mock_start.call_args.args[1] == "xrpl-3.3.0"

    @patch("xrpld_lab.cli.run_start_script")
    @patch("xrpld_lab.cli.LabRunner")
    @patch("xrpld_lab.cli.build_lab_config")
    def test_create_network_does_not_start(
        self, mock_build, mock_runner_cls, mock_start
    ):
        mock_build.return_value = MagicMock()
        mock_runner_cls.return_value = MagicMock()

        with patch("sys.argv", ["xrpld-lab", "create:network", "--protocol", "xrpl"]):
            main()

        mock_start.assert_not_called()

    @patch("xrpld_lab.cli.LabRunner")
    @patch("xrpld_lab.cli.build_lab_config")
    def test_create_network_calls_runner(self, mock_build, mock_runner_cls):
        mock_config = MagicMock()
        mock_build.return_value = mock_config
        mock_runner = MagicMock()
        mock_runner_cls.return_value = mock_runner

        with patch("sys.argv", ["xrpld-lab", "create:network", "--protocol", "xrpl"]):
            main()

        mock_build.assert_called_once()
        assert mock_runner_cls.call_count == 1
        assert mock_runner_cls.call_args.args[0] is mock_config
        mock_runner.run.assert_called_once()

    @patch("xrpld_lab.cli._build_parser")
    def test_no_command_prints_help(self, mock_build_parser):
        mock_parser = MagicMock()
        mock_args = MagicMock()
        mock_args.command = None
        mock_parser.parse_args.return_value = mock_args
        mock_build_parser.return_value = mock_parser

        with patch("sys.argv", ["xrpld-lab"]):
            main()

        mock_parser.print_help.assert_called_once()

    # -- Operational command dispatch tests --

    @patch("xrpld_lab.cli.run_start_script")
    @patch("xrpld_lab.cli.Workspace")
    def test_up_dispatches_to_run_start_script(self, mock_ws_cls, mock_run):
        mock_ws = MagicMock()
        mock_ws_cls.return_value = mock_ws

        with patch("sys.argv", ["xrpld-lab", "up", "--name", "my-net"]):
            main()

        mock_run.assert_called_once_with(mock_ws, "my-net")

    @patch("xrpld_lab.cli.run_stop_script")
    @patch("xrpld_lab.cli.Workspace")
    def test_down_dispatches_to_run_stop_script(self, mock_ws_cls, mock_run):
        mock_ws = MagicMock()
        mock_ws_cls.return_value = mock_ws

        with patch("sys.argv", ["xrpld-lab", "down", "--name", "my-net"]):
            main()

        mock_run.assert_called_once_with(mock_ws, "my-net")

    @patch("xrpld_lab.cli.remove_network")
    @patch("xrpld_lab.cli.Workspace")
    def test_remove_dispatches_to_remove_network(self, mock_ws_cls, mock_run):
        mock_ws = MagicMock()
        mock_ws_cls.return_value = mock_ws

        with patch("sys.argv", ["xrpld-lab", "remove", "--name", "my-net"]):
            main()

        mock_run.assert_called_once_with(mock_ws, "my-net")

    @patch("xrpld_lab.cli.stop_standalone")
    @patch("xrpld_lab.cli.Workspace")
    def test_down_standalone_dispatches(self, mock_ws_cls, mock_run):
        mock_ws = MagicMock()
        mock_ws_cls.return_value = mock_ws

        with patch(
            "sys.argv", ["xrpld-lab", "down:standalone", "--name", "my-standalone"]
        ):
            main()

        mock_run.assert_called_once_with(mock_ws, "my-standalone", "xrpl", None)

    @patch("xrpld_lab.cli.stop_standalone")
    @patch("xrpld_lab.cli.Workspace")
    def test_down_standalone_with_version(self, mock_ws_cls, mock_run):
        mock_ws = MagicMock()
        mock_ws_cls.return_value = mock_ws

        with patch(
            "sys.argv",
            [
                "xrpld-lab",
                "down:standalone",
                "--protocol",
                "xrpl",
                "--version",
                "3.1.1",
            ],
        ):
            main()

        mock_run.assert_called_once_with(mock_ws, None, "xrpl", "3.1.1")

    @patch("xrpld_lab.cli.start_local")
    @patch("xrpld_lab.cli.Workspace")
    def test_up_local_dispatches(self, mock_ws_cls, mock_run):
        with patch("sys.argv", ["xrpld-lab", "up:local"]):
            main()

        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["network_type"] == "standalone"
        assert (
            mock_run.call_args.kwargs["network_id"]
            == XRPL.default_standalone_network_id
            == 1
        )

    @patch("xrpld_lab.cli.start_local")
    @patch("xrpld_lab.cli.Workspace")
    def test_up_local_network_type_uses_network_default(self, mock_ws_cls, mock_run):
        with patch("sys.argv", ["xrpld-lab", "up:local", "--network_type", "network"]):
            main()

        assert mock_run.call_args.kwargs["network_id"] == XRPL.default_network_id

    @patch("xrpld_lab.cli.start_local")
    @patch("xrpld_lab.cli.Workspace")
    def test_up_local_explicit_network_id_zero(self, mock_ws_cls, mock_run):
        with patch("sys.argv", ["xrpld-lab", "up:local", "--network_id", "0"]):
            main()

        assert mock_run.call_args.kwargs["network_id"] == 0

    @patch("xrpld_lab.cli.stop_local")
    @patch("xrpld_lab.cli.Workspace")
    def test_down_local_dispatches(self, mock_ws_cls, mock_run):
        with patch("sys.argv", ["xrpld-lab", "down:local"]):
            main()

        mock_run.assert_called_once()

    @patch("xrpld_lab.cli.update_node_binary")
    @patch("xrpld_lab.cli.Workspace")
    def test_update_node_dispatches(self, mock_ws_cls, mock_run):
        mock_ws = MagicMock()
        mock_ws_cls.return_value = mock_ws

        with patch(
            "sys.argv",
            [
                "xrpld-lab",
                "update:node",
                "--name",
                "my-net",
                "--node_id",
                "2",
                "--node_type",
                "validator",
                "--build_server",
                "https://build.example.com",
                "--build_version",
                "2.0.0",
            ],
        ):
            main()

        mock_run.assert_called_once_with(
            mock_ws,
            "my-net",
            2,
            "validator",
            "https://build.example.com",
            "2.0.0",
            image=None,
        )

    @patch("xrpld_lab.cli.update_node_binary")
    @patch("xrpld_lab.cli.Workspace")
    def test_update_node_from_image_dispatches(self, mock_ws_cls, mock_run):
        mock_ws = MagicMock()
        mock_ws_cls.return_value = mock_ws

        with patch(
            "sys.argv",
            [
                "xrpld-lab",
                "update:node",
                "--name",
                "my-net",
                "--node_id",
                "2",
                "--node_type",
                "validator",
                "--build_version",
                "3.3.0-rc1",
                "--image",
                "rippleci/xrpld:3.3.0-rc1",
            ],
        ):
            main()

        mock_run.assert_called_once_with(
            mock_ws,
            "my-net",
            2,
            "validator",
            None,
            "3.3.0-rc1",
            image="rippleci/xrpld:3.3.0-rc1",
        )

    @patch("xrpld_lab.cli.vote_amendment")
    @patch("xrpld_lab.cli.Workspace")
    def test_vote_amendment_dispatches(self, mock_ws_cls, mock_run):
        mock_ws = MagicMock()
        mock_ws_cls.return_value = mock_ws
        mock_run.return_value = True

        with patch(
            "sys.argv",
            [
                "xrpld-lab",
                "vote:amendment",
                "--name",
                "my-net",
                "--amendment_name",
                "fixNFTokenRemint",
                "--node_id",
                "1",
            ],
        ):
            main()

        mock_run.assert_called_once_with(
            "my-net", "fixNFTokenRemint", mock_ws, node_id=1
        )

    @patch("xrpld_lab.cli.view_local_logs")
    @patch("xrpld_lab.cli.Workspace")
    def test_logs_local_dispatches(self, mock_ws_cls, mock_run):
        with patch("sys.argv", ["xrpld-lab", "logs:local", "--node", "vnode1"]):
            main()

        mock_run.assert_called_once_with("vnode1")

    @patch("xrpld_lab.cli.view_local_logs")
    @patch("xrpld_lab.cli.Workspace")
    def test_logs_local_no_node(self, mock_ws_cls, mock_run):
        with patch("sys.argv", ["xrpld-lab", "logs:local"]):
            main()

        mock_run.assert_called_once_with(None)

    @patch("xrpld_lab.cli.view_standalone_logs")
    @patch("xrpld_lab.cli.Workspace")
    def test_logs_standalone_dispatches(self, mock_ws_cls, mock_run):
        with patch("sys.argv", ["xrpld-lab", "logs:standalone"]):
            main()

        mock_run.assert_called_once_with("xrpl")

    @patch("xrpld_lab.cli.view_standalone_logs")
    @patch("xrpld_lab.cli.Workspace")
    def test_logs_standalone_with_protocol(self, mock_ws_cls, mock_run):
        with patch("sys.argv", ["xrpld-lab", "logs:standalone", "--protocol", "xrpl"]):
            main()

        mock_run.assert_called_once_with("xrpl")

    @patch("xrpld_lab.cli.LabRunner")
    @patch("xrpld_lab.cli.build_lab_config")
    def test_create_ansible_calls_runner(self, mock_build, mock_runner_cls):
        mock_config = MagicMock()
        mock_build.return_value = mock_config
        mock_runner = MagicMock()
        mock_runner_cls.return_value = mock_runner

        with patch(
            "sys.argv",
            ["xrpld-lab", "create:ansible", "--vips", "10.0.0.1", "--pips", "10.0.0.2"],
        ):
            main()

        mock_build.assert_called_once()
        assert mock_runner_cls.call_count == 1
        assert mock_runner_cls.call_args.args[0] is mock_config
        mock_runner.run.assert_called_once()

    @patch("xrpld_lab.cli._deploy_ansible")
    @patch("xrpld_lab.cli.Workspace")
    def test_deploy_ansible_dispatches(self, mock_ws_cls, mock_deploy):
        mock_ws = MagicMock()
        mock_ws_cls.return_value = mock_ws

        with patch("sys.argv", ["xrpld-lab", "deploy:ansible", "--name", "my-cluster"]):
            main()

        mock_deploy.assert_called_once_with(mock_ws, "my-cluster")

    # -- Exit status: a failed operation exits 1, a successful one returns --

    @patch("xrpld_lab.cli.run_start_script", return_value=True)
    @patch("xrpld_lab.cli.Workspace")
    def test_successful_up_returns_normally(self, mock_ws_cls, mock_run):
        with patch("sys.argv", ["xrpld-lab", "up", "--name", "my-net"]):
            assert main() is None

    @patch("xrpld_lab.cli.run_start_script", return_value=False)
    @patch("xrpld_lab.cli.Workspace")
    def test_failing_up_exits_1(self, mock_ws_cls, mock_run):
        with patch("sys.argv", ["xrpld-lab", "up", "--name", "my-net"]):
            with pytest.raises(SystemExit) as exc:
                main()

        assert exc.value.code == 1

    @patch("xrpld_lab.cli.run_stop_script", return_value=False)
    @patch("xrpld_lab.cli.Workspace")
    def test_failing_down_exits_1(self, mock_ws_cls, mock_run):
        with patch("sys.argv", ["xrpld-lab", "down", "--name", "my-net"]):
            with pytest.raises(SystemExit) as exc:
                main()

        assert exc.value.code == 1

    @patch("xrpld_lab.cli.stop_standalone", return_value=False)
    @patch("xrpld_lab.cli.Workspace")
    def test_failing_down_standalone_exits_1(self, mock_ws_cls, mock_run):
        with patch("sys.argv", ["xrpld-lab", "down:standalone", "--name", "sa"]):
            with pytest.raises(SystemExit) as exc:
                main()

        assert exc.value.code == 1

    @patch("xrpld_lab.operations.run_command", return_value=7)
    def test_failing_down_standalone_keeps_the_directory(self, mock_run, tmp_path):
        from xrpld_lab.workspace import Workspace

        net_dir = tmp_path / "my-standalone"
        net_dir.mkdir()
        (net_dir / "stop.sh").write_text("#!/bin/bash\nexit 7\n")

        with patch("xrpld_lab.cli.Workspace", return_value=Workspace(str(tmp_path))):
            with patch(
                "sys.argv", ["xrpld-lab", "down:standalone", "--name", "my-standalone"]
            ):
                with pytest.raises(SystemExit) as exc:
                    main()

        assert exc.value.code == 1
        mock_run.assert_called_once_with(str(net_dir), "bash stop.sh")
        assert (net_dir / "stop.sh").is_file()

    @patch("xrpld_lab.cli._deploy_ansible", return_value=False)
    @patch("xrpld_lab.cli.Workspace")
    def test_failing_deploy_ansible_exits_1(self, mock_ws_cls, mock_deploy):
        with patch("sys.argv", ["xrpld-lab", "deploy:ansible", "--name", "c"]):
            with pytest.raises(SystemExit) as exc:
                main()

        assert exc.value.code == 1

    @patch("xrpld_lab.cli.restart_local_node", return_value=False)
    @patch("xrpld_lab.cli.Workspace")
    def test_failing_node_restart_exits_1(self, mock_ws_cls, mock_restart):
        with patch("sys.argv", ["xrpld-lab", "node:restart", "vnode1"]):
            with pytest.raises(SystemExit) as exc:
                main()

        assert exc.value.code == 1

    # -- update:node needs exactly one binary source --

    @patch("xrpld_lab.cli.update_node_binary")
    @patch("xrpld_lab.cli.Workspace")
    def test_update_node_requires_a_binary_source(self, mock_ws_cls, mock_run):
        with patch(
            "sys.argv",
            [
                "xrpld-lab",
                "update:node",
                "--name",
                "my-net",
                "--node_id",
                "2",
                "--node_type",
                "validator",
                "--build_version",
                "3.3.0",
            ],
        ):
            with pytest.raises(SystemExit) as exc:
                main()

        assert exc.value.code == 2
        mock_run.assert_not_called()

    @patch("xrpld_lab.cli.update_node_binary")
    @patch("xrpld_lab.cli.Workspace")
    def test_update_node_rejects_both_binary_sources(self, mock_ws_cls, mock_run):
        with patch(
            "sys.argv",
            [
                "xrpld-lab",
                "update:node",
                "--name",
                "my-net",
                "--node_id",
                "2",
                "--node_type",
                "validator",
                "--build_version",
                "3.3.0",
                "--build_server",
                "https://build.example.com",
                "--image",
                "rippleci/xrpld:3.3.0",
            ],
        ):
            with pytest.raises(SystemExit) as exc:
                main()

        assert exc.value.code == 2
        mock_run.assert_not_called()


class TestFlattenIps:
    """_flatten_ips normalises IP args so a joined string can't collapse the fleet."""

    def test_pre_split_tokens(self):
        assert _flatten_ips(["a", "b", "c"]) == ["a", "b", "c"]

    def test_single_joined_string(self):
        # nargs="+" caller passing --vips "a b c d" (or zsh no-word-split)
        assert _flatten_ips(["10.0.0.1 10.0.0.2 10.0.0.3 10.0.0.4"]) == [
            "10.0.0.1",
            "10.0.0.2",
            "10.0.0.3",
            "10.0.0.4",
        ]

    def test_comma_and_mixed(self):
        assert _flatten_ips(["a,b", "c d"]) == ["a", "b", "c", "d"]

    def test_empty_and_none(self):
        assert _flatten_ips(None) == []
        assert _flatten_ips([]) == []
        assert _flatten_ips(["", "  "]) == []


class TestGenesisFlagParsing:
    """--genesis must accept true/false values, not truthy strings."""

    def _parse(self, value):
        parser = _build_parser()
        return parser.parse_args(["create:network", "--genesis", value]).genesis

    def test_false_strings_parse_false(self):
        for v in ("False", "false", "0", "no"):
            assert self._parse(v) is False

    def test_true_strings_parse_true(self):
        for v in ("True", "true", "1", "yes"):
            assert self._parse(v) is True

    def test_default_is_false(self):
        parser = _build_parser()
        assert parser.parse_args(["create:network"]).genesis is None

    def test_garbage_rejected(self):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["create:network", "--genesis", "maybe"])


class TestWorkspaceOverride:
    """--workspace points at an existing network's keystore instead of copying it."""

    def test_defaults_to_none(self):
        parser = _build_parser()
        args = parser.parse_args(
            ["create:ansible", "--vips", "10.0.0.1", "--pips", "10.0.0.2"]
        )
        assert args.workspace is None

    def test_parsed_on_create_ansible(self):
        parser = _build_parser()
        args = parser.parse_args(
            [
                "create:ansible",
                "--vips",
                "10.0.0.1",
                "--pips",
                "10.0.0.2",
                "--workspace",
                "/srv/ws",
            ]
        )
        assert args.workspace == "/srv/ws"

    def test_parsed_on_deploy_ansible(self):
        parser = _build_parser()
        args = parser.parse_args(
            ["deploy:ansible", "--name", "alphanet", "--workspace", "/srv/ws"]
        )
        assert args.workspace == "/srv/ws"

    @patch("xrpld_lab.cli.LabRunner")
    @patch("xrpld_lab.cli.build_lab_config")
    def test_workspace_passed_to_runner(self, mock_build, mock_runner_cls, tmp_path):
        mock_build.return_value = MagicMock()
        base = str(tmp_path / "ws")
        with patch(
            "sys.argv",
            [
                "xrpld-lab",
                "create:ansible",
                "--vips",
                "10.0.0.1",
                "--pips",
                "10.0.0.2",
                "--workspace",
                base,
            ],
        ):
            main()
        passed = mock_runner_cls.call_args.args[1]
        assert passed.base == base
