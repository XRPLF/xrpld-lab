"""LabRunner -- orchestrates standalone, network, and local network workflows.

Ties together all the building blocks (NodeFactory, CfgBuilder, ComposeBuilder,
ScriptBuilder, SourceResolver, amendments) to execute the three main
deployment modes.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import List

import requests

from xrpld_publisher.publisher import PublisherClient
from xrpld_publisher.validator import ValidatorClient

from xrpld_lab.models import (
    BuildType,
    DeployMode,
    LabConfig,
    NodeRole,
    PortSet,
)
from xrpld_lab.protocol import get_spec
from xrpld_lab.workspace import Workspace
from xrpld_lab.node_factory import NodeFactory
from xrpld_lab.config import apply_overrides
from xrpld_lab.config_builder import XrpldCfgBuilder, ValidatorsTxtBuilder
from xrpld_lab.compose_builder import ComposeBuilder
from xrpld_lab.script_builder import DockerfileBuilder, ScriptBuilder
from xrpld_lab.source_resolver import SourceResolver
from xrpld_lab.amendments import (
    parse_amendments,
    update_genesis,
    get_feature_lines_from_content,
    get_feature_lines_from_path,
)
from xrpld_lab.ansible_builder import AnsibleBuilder
from xrpld_lab.utils import write_file, save_config, write_executable


def explorer_target(num_peers: int, port_offset: int = 0) -> tuple[str, int]:
    """Node name and admin websocket port the explorer follows: pnode1, or vnode1
    when the cluster has no peers."""
    if num_peers > 0:
        return "pnode1", PortSet.for_node(1, NodeRole.PEER, port_offset).ws_admin
    return "vnode1", PortSet.for_node(1, NodeRole.VALIDATOR, port_offset).ws_admin


class LabRunner:
    """Orchestrates standalone, network, and local network deployments."""

    def __init__(self, lab: LabConfig, workspace: Workspace = None):
        self.lab = lab
        self.spec = get_spec(lab.protocol)
        self.workspace = workspace or Workspace()
        self.resolver = SourceResolver()
        self._cached_preload = None
        self._cached_preload_seeds = None

    # ------------------------------------------------------------------
    # Genesis (with optional perf-iac prefunded state)
    # ------------------------------------------------------------------

    def _genesis(self, features, protocol_name):
        """Build the genesis dict, merging prefunded accounts/trustlines when
        configured."""
        return update_genesis(
            features,
            protocol_name,
            genesis_path=getattr(self.lab, "genesis_file", None),
            preload_entries=self._preload_entries(),
        )

    def _resolve_feature_lines(self, source, spec):
        """Return features.macro lines for amendment parsing.

        Prefers a local ``--features_file`` so the enabled amendment set exactly
        matches the local binary (and never fetches from GitHub) -- required when
        the binary is built from an unpushed commit, and safer regardless since a
        mismatched remote macro can enable amendments the binary lacks, which
        amendment-blocks the node. Falls back to downloading the macro from the
        repo at the resolved commit.
        """
        local = getattr(self.lab, "features_file", None)
        if local:
            print(f"  [xrpld-lab] reading features from local file {local}")
            return get_feature_lines_from_path(local)
        feature_content = self.resolver.resolve_features(source, spec)
        return get_feature_lines_from_content(feature_content)

    def _resolve_local_feature_lines(self, source, spec):
        """Local-mode feature lines: ``--features_file``, else GitHub at the
        commit ref, else ``../<feature path>`` of the build directory the
        command runs in."""
        if self.lab.features_file:
            return self._resolve_feature_lines(source, spec)
        if source.commit_hash:
            try:
                return self._resolve_feature_lines(source, spec)
            except requests.RequestException as exc:
                print(
                    f"  [xrpld-lab] could not fetch features at "
                    f"{source.commit_hash} ({exc}); looking for a build "
                    "directory instead"
                )
        for fpath in spec.feature_paths:
            candidate = os.path.join("..", fpath)
            if os.path.exists(candidate):
                print(f"  [xrpld-lab] reading features from {candidate}")
                return get_feature_lines_from_path(candidate)
        raise FileNotFoundError(
            "no features.macro: pass --features_file, or --build_server "
            "<github tree url> with --build_version <commit>, or run from a "
            "rippled build directory"
        )

    def _preload_entries(self):
        if not getattr(self.lab, "preload_accounts", 0):
            return None
        if self._cached_preload is None:
            from xrpld_lab.ledger_generator import generate

            entries, seeds = generate(
                self.lab.preload_accounts,
                self.lab.preload_balance,
                self.lab.preload_trustlines,
                self.lab.preload_currency,
            )
            self._cached_preload = entries
            self._cached_preload_seeds = seeds
            print(
                f"  [xrpld-lab] preloading genesis with {self.lab.preload_accounts} "
                f"accounts + {self.lab.preload_trustlines} trustlines"
            )
        return self._cached_preload

    def _write_preload_wallets(self, out_dir):
        """Write prefunded account seeds in the loadtester's
        wallets.sub.<node> format."""
        if self._cached_preload_seeds:
            write_file(
                os.path.join(out_dir, "wallets.sub.1.json"),
                json.dumps(self._cached_preload_seeds),
            )
            print(
                f"  [xrpld-lab] wrote {len(self._cached_preload_seeds)} prefunded "
                f"seeds -> {os.path.join(out_dir, 'wallets.sub.1.json')}"
            )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self):
        """Execute the workflow based on lab.mode.

        :return: The generated directory name for standalone, else ``None``
        """
        if self.lab.mode == DeployMode.STANDALONE:
            return self._run_standalone()
        elif self.lab.mode == DeployMode.NETWORK:
            self._run_network()
        elif self.lab.mode == DeployMode.LOCAL:
            self._run_local_network()
        return None

    def _render_cfg(self, node) -> str:
        """Render the node's xrpld.cfg with the lab's config overrides applied."""
        return apply_overrides(XrpldCfgBuilder(node).build(), self.lab.config_overrides)

    def _resolve_genesis(self, cluster_path: str) -> bool:
        """The lab's genesis flag, else fresh unless the cluster has a keystore."""
        if self.lab.genesis is not None:
            return self.lab.genesis
        return not os.path.isdir(os.path.join(cluster_path, "keystore"))

    def _stage_binary(self, source, dest: str) -> None:
        """Copy the local binary, or download it from the build server in binary mode.

        :param source: The build source configuration
        :param dest: Path the binary is written to
        """
        if source.binary_path:
            shutil.copy2(source.binary_path, dest)
            os.chmod(dest, 0o755)
        elif source.build_type == BuildType.BINARY:
            self.resolver.download_binary(
                f"{source.build_server}/{source.build_version}", dest
            )

    @staticmethod
    def _copy_binary_to_node(staged: str, node_dir: str) -> None:
        """Put the staged binary in the node directory, the docker build context."""
        dest = os.path.join(node_dir, os.path.basename(staged))
        shutil.copy2(staged, dest)
        os.chmod(dest, 0o755)

    # ------------------------------------------------------------------
    # Standalone workflow
    # ------------------------------------------------------------------

    def _run_standalone(self):
        """Execute standalone workflow:

        1. Resolve features from source
        2. Create NodeConfig via NodeFactory
        3. Build xrpld.cfg and validators.txt
        4. Parse amendments, update genesis
        5. Create Dockerfile
        6. Build docker-compose
        7. Generate start/stop scripts

        :return: The generated directory name, relative to the workspace base
        """
        lab = self.lab
        spec = self.spec
        source = lab.build_source

        # Determine build dir name
        name = source.build_version
        protocol_name = lab.protocol.value

        # 1. Resolve features before the directory exists
        feature_lines = self._resolve_feature_lines(source, spec)
        base_dir = self.workspace.standalone_dir(protocol_name, name)

        # 2. Create node config
        node = NodeFactory.create_standalone(
            protocol=lab.protocol,
            name=name,
            network_id=lab.network_id,
            log_level=lab.log_level,
            node_db_type=lab.node_db_type,
            vl_keys=[lab.public_vl_key] if lab.public_vl_key else None,
            datagram_monitor=[lab.datagram_monitor] if lab.datagram_monitor else None,
        )

        # 3. Build config files
        cfg_content = self._render_cfg(node)
        vl_content = ValidatorsTxtBuilder(node, genesis=False).build()

        cfg_path = os.path.join(base_dir, "config")
        os.makedirs(cfg_path, exist_ok=True)
        save_config(protocol_name, cfg_path, cfg_content, vl_content)

        # 4. Amendments + genesis
        features = parse_amendments(
            feature_lines, include_unsupported=self.lab.all_amendments
        )
        genesis = self._genesis(features, protocol_name)
        write_file(
            os.path.join(base_dir, "genesis.json"),
            json.dumps(genesis, indent=4, sort_keys=True),
        )

        # 5. Binary + Dockerfile
        self._stage_binary(source, os.path.join(base_dir, f"{protocol_name}d.{name}"))
        dockerfile = DockerfileBuilder.build(
            protocol=protocol_name,
            ports=node.ports,
            image_name=source.image or "ubuntu:noble",
            binary=source.build_type == BuildType.BINARY,
            version=name,
            include_genesis=True,
            standalone="-a",
        )
        write_file(os.path.join(base_dir, "Dockerfile"), dockerfile)

        # Copy entrypoint
        entrypoint_src = os.path.join(
            self.workspace.package_dir, "deploykit", spec.entrypoint_file
        )
        if os.path.exists(entrypoint_src):
            shutil.copy2(entrypoint_src, os.path.join(base_dir, "entrypoint"))

        # 6. Docker compose
        compose = ComposeBuilder("standalone-network")
        compose.add_standalone_service(protocol_name, node.ports)
        compose.add_explorer_service(ws_port=node.ports.ws_admin, standalone=True)
        if lab.add_ipfs:
            compose.add_ipfs_service(protocol_name)
        compose.write(os.path.join(base_dir, "docker-compose.yml"))

        # 7. Scripts
        ws_base = os.path.dirname(base_dir)
        write_file(
            os.path.join(base_dir, "start.sh"),
            ScriptBuilder.standalone_start(ws_base, protocol_name, name),
        )
        os.chmod(os.path.join(base_dir, "start.sh"), 0o755)
        write_file(
            os.path.join(base_dir, "stop.sh"),
            ScriptBuilder.standalone_stop(ws_base, protocol_name, name),
        )
        os.chmod(os.path.join(base_dir, "stop.sh"), 0o755)

        return os.path.basename(base_dir)

    # ------------------------------------------------------------------
    # Network workflow
    # ------------------------------------------------------------------

    def _run_network(self):
        """Execute network workflow:

        1. Resolve features from source
        2. Create VL (validator list) keys
        3. Create validator keys for each node
        4. Create NodeConfigs for validators and peers
        5. Build config files for each node
        6. Parse amendments, write genesis for each node
        7. Create Dockerfiles and copy entrypoints
        8. Build docker-compose with all services
        9. Sign UNL and write VL artifacts
        10. Generate start/stop scripts
        """
        lab = self.lab
        spec = self.spec
        source = lab.build_source

        name = source.cluster_name or source.build_version
        protocol_name = lab.protocol.value
        cluster_path = self.workspace.cluster_path(name)

        # 1. Resolve every input before the cluster directory exists
        feature_lines = self._resolve_feature_lines(source, spec)
        if source.binary_path and not os.path.isfile(source.binary_path):
            raise FileNotFoundError(f"binary not found: {source.binary_path}")
        genesis = self._resolve_genesis(cluster_path)
        cluster_dir = self.workspace.cluster_dir(name)

        # 1c. Binary: copy local or download from build server
        staged_binary = os.path.join(cluster_dir, f"xrpld.{name}")
        self._stage_binary(source, staged_binary)
        use_binary = source.build_type == BuildType.BINARY

        # 2. Create VL keys
        original_dir = os.getcwd()
        os.chdir(cluster_dir)
        try:
            publisher = PublisherClient()
            vl_keys = publisher.get_keys()
            if not vl_keys:
                # Fresh VL keys change the network identity — never mint them
                # silently for a network being preserved.
                if not genesis:
                    raise RuntimeError(
                        "non-genesis deploy but no VL publisher keys in this "
                        "workspace — run from the workspace that created the "
                        "network (or restore its keystore/)"
                    )
                publisher.create_keys()
                vl_keys = publisher.get_keys()

            vl_pub_key = vl_keys["publicKey"]

            # 3. Create validator keys
            manifests: List[str] = []
            validators: List[str] = []
            tokens: List[str] = []
            ips_fixed: List[str] = []
            use_ansible = lab.ansible is not None

            for i in range(1, lab.num_validators + 1):
                node_name = f"vnode{i}"
                vc = ValidatorClient(node_name)
                key_path = f"keystore/{node_name}/key.json"
                if not os.path.exists(key_path):
                    if not genesis:
                        raise RuntimeError(
                            f"non-genesis deploy but {key_path} is missing — "
                            "a regenerated validator key would change the "
                            "network identity"
                        )
                    vc.create_keys()
                    vc.set_domain(f"{protocol_name}.{node_name}.transia.co")
                    vc.create_token()
                keys = vc.get_keys()
                token = vc.read_token()
                manifest = vc.read_manifest()
                manifests.append(manifest)
                validators.append(keys["public_key"])
                tokens.append(token)

                ports = PortSet.for_node(i, NodeRole.VALIDATOR)
                if use_ansible and i <= len(lab.ansible.vips):
                    ips_fixed.append(f"{lab.ansible.vips[i - 1]} {ports.peer}")
                else:
                    ips_fixed.append(f"vnode{i} {ports.peer}")

        finally:
            os.chdir(original_dir)

        # 4-7. Create nodes, configs, genesis, dockerfiles
        compose = ComposeBuilder(f"{name}-network")
        image_name = source.image or "ubuntu:noble"
        # Ansible pulls the image the node Dockerfile builds FROM: the ubuntu base
        # under a staged binary, a supplied --image, else the transia/cluster tag.
        if use_binary:
            ansible_image = image_name
        else:
            ansible_image = source.image or (
                f"transia/cluster:{source.commit_hash or source.build_version}"
            )

        for i in range(1, lab.num_validators + 1):
            node_name = f"vnode{i}"
            node_ip = (
                lab.ansible.vips[i - 1]
                if use_ansible and i <= len(lab.ansible.vips)
                else ""
            )
            node = NodeFactory.create_validator(
                index=i,
                protocol=lab.protocol,
                name=name,
                network_id=lab.network_id,
                token=tokens[i - 1],
                all_validators=validators,
                vl_key=vl_pub_key,
                ips_fixed=ips_fixed,
                log_level=lab.log_level,
                node_db_type=lab.node_db_type,
                datagram_monitor=lab.datagram_monitor_for(node_ip),
                num_ledgers=lab.online_delete,
                tree_cache_target_entries=lab.tree_cache_target_entries,
                memory_limit=lab.memory_limit,
                database_path=lab.database_path,
                vl_sites=[lab.vl_site] if lab.vl_site else None,
                statsd_address=lab.statsd_address,
                statsd_prefix=lab.statsd_prefix_for(name, node_name),
                perf_path=lab.perf_path,
            )

            node_dir = self.workspace.node_dir(cluster_dir, node_name)
            cfg_path = self.workspace.config_dir(node_dir)
            self.workspace.log_dir(node_dir)
            if use_binary:
                self._copy_binary_to_node(staged_binary, node_dir)

            # Config
            cfg_content = self._render_cfg(node)
            vl_content = ValidatorsTxtBuilder(
                node, genesis=True, bootstrap_vl=lab.bootstrap_vl
            ).build()
            save_config(protocol_name, cfg_path, cfg_content, vl_content)

            # Amendments + genesis. A preserved network has no genesis to write: the
            # node boots from its existing db and takes amendments from the chain.
            if genesis:
                features = parse_amendments(
                    feature_lines, include_unsupported=self.lab.all_amendments
                )
                genesis = self._genesis(features, protocol_name)
                write_file(
                    os.path.join(node_dir, "genesis.json"),
                    json.dumps(genesis, indent=4, sort_keys=True),
                )

            # Dockerfile
            dockerfile = DockerfileBuilder.build(
                protocol=protocol_name,
                ports=node.ports,
                image_name=image_name,
                network=True,
                binary=use_binary,
                version=name,
                # Non-genesis: no genesis.json in the image and a plain entrypoint —
                # the node boots from its preserved db and syncs with its peers.
                include_genesis=genesis or lab.db_seed,
                quorum=lab.effective_quorum,
                standalone="--valid" if genesis else None,
                db_seed=lab.db_seed,
            )
            write_file(os.path.join(node_dir, "Dockerfile"), dockerfile)

            # Entrypoint
            entrypoint_src = os.path.join(
                self.workspace.package_dir,
                "deploykit",
                spec.network_entrypoint_file,
            )
            if os.path.exists(entrypoint_src):
                shutil.copy2(entrypoint_src, os.path.join(node_dir, "entrypoint"))

            # Compose service
            compose.add_node_service(node_name, node.ports, node.role, network=True)

        for i in range(1, lab.num_peers + 1):
            node_name = f"pnode{i}"
            node_ip = (
                lab.ansible.pips[i - 1]
                if use_ansible and i <= len(lab.ansible.pips)
                else ""
            )
            node = NodeFactory.create_peer(
                index=i,
                protocol=lab.protocol,
                name=name,
                network_id=lab.network_id,
                validators=validators,
                vl_key=vl_pub_key,
                ips_fixed=ips_fixed,
                log_level=lab.log_level,
                node_db_type=lab.node_db_type,
                datagram_monitor=lab.datagram_monitor_for(node_ip),
                num_ledgers=lab.online_delete,
                tree_cache_target_entries=lab.tree_cache_target_entries,
                memory_limit=lab.memory_limit,
                database_path=lab.database_path,
                vl_sites=[lab.vl_site] if lab.vl_site else None,
                statsd_address=lab.statsd_address,
                statsd_prefix=lab.statsd_prefix_for(name, node_name),
                perf_path=lab.perf_path,
            )

            node_dir = self.workspace.node_dir(cluster_dir, node_name)
            cfg_path = self.workspace.config_dir(node_dir)
            self.workspace.log_dir(node_dir)
            if use_binary:
                self._copy_binary_to_node(staged_binary, node_dir)

            # Config
            cfg_content = self._render_cfg(node)
            vl_content = ValidatorsTxtBuilder(
                node, genesis=True, bootstrap_vl=lab.bootstrap_vl
            ).build()
            save_config(protocol_name, cfg_path, cfg_content, vl_content)

            # Amendments + genesis (peers always get all amendments)
            if genesis:
                features = parse_amendments(
                    feature_lines, include_unsupported=self.lab.all_amendments
                )
                genesis = self._genesis(features, protocol_name)
                write_file(
                    os.path.join(node_dir, "genesis.json"),
                    json.dumps(genesis, indent=4, sort_keys=True),
                )

            # Dockerfile
            dockerfile = DockerfileBuilder.build(
                protocol=protocol_name,
                ports=node.ports,
                image_name=image_name,
                network=True,
                binary=use_binary,
                version=name,
                # Non-genesis: no genesis.json in the image and a plain entrypoint —
                # the node boots from its preserved db and syncs with its peers.
                include_genesis=genesis or lab.db_seed,
                quorum=lab.effective_quorum,
                standalone="--valid" if genesis else None,
                db_seed=lab.db_seed,
            )
            write_file(os.path.join(node_dir, "Dockerfile"), dockerfile)

            # Entrypoint
            entrypoint_src = os.path.join(
                self.workspace.package_dir,
                "deploykit",
                spec.network_entrypoint_file,
            )
            if os.path.exists(entrypoint_src):
                shutil.copy2(entrypoint_src, os.path.join(node_dir, "entrypoint"))

            # Compose service
            compose.add_node_service(node_name, node.ports, node.role, network=True)

        # 8. VL + explorer services
        compose.add_vl_service()
        _, ws_port = explorer_target(lab.num_peers)
        compose.add_explorer_service(ws_port=ws_port, standalone=False)
        compose.write(os.path.join(cluster_dir, "docker-compose.yml"))

        # 9. Sign UNL and write VL artifacts
        vl_dir = os.path.join(cluster_dir, "vl")
        os.makedirs(vl_dir, exist_ok=True)

        original_dir = os.getcwd()
        os.chdir(cluster_dir)
        try:
            for manifest in manifests:
                publisher.add_validator(manifest)
            publisher.sign_unl(os.path.join(vl_dir, "vl.json"))
        finally:
            os.chdir(original_dir)

        # Copy nginx dockerfile for VL
        nginx_src = os.path.join(
            self.workspace.package_dir,
            "deploykit",
            "nginx.dockerfile",
        )
        if os.path.exists(nginx_src):
            shutil.copyfile(nginx_src, os.path.join(vl_dir, "Dockerfile"))

        # 10. Scripts
        write_executable(
            os.path.join(cluster_dir, "start.sh"),
            ScriptBuilder.network_start(),
        )
        write_executable(
            os.path.join(cluster_dir, "stop.sh"),
            ScriptBuilder.network_stop(name, lab.num_validators, lab.num_peers),
        )

        # 10b. Prefunded wallet seeds for the loadtester (perf-iac genesis)
        self._write_preload_wallets(cluster_dir)

        # 11. Ansible deployment (if configured)
        if lab.ansible:
            self._generate_ansible(
                lab,
                cluster_dir,
                name,
                ansible_image,
                genesis,
                build_tag=(
                    (source.commit_hash or source.build_version) if use_binary else ""
                ),
            )

    # ------------------------------------------------------------------
    # Ansible generation
    # ------------------------------------------------------------------

    def _generate_ansible(
        self,
        lab: LabConfig,
        cluster_dir: str,
        name: str,
        image_name: str,
        genesis: bool,
        build_tag: str = "",
    ):
        """Generate ansible deployment files for the cluster."""
        ansible_builder = AnsibleBuilder(
            cluster_dir=cluster_dir,
            config=lab.ansible,
            image_name=image_name,
            genesis=genesis,
            build_tag=build_tag,
        )

        for i in range(1, lab.num_validators + 1):
            node_name = f"vnode{i}"
            ip = lab.ansible.vips[i - 1] if i <= len(lab.ansible.vips) else ""
            node_dir = self.workspace.node_dir(cluster_dir, node_name)
            cfg_path = self.workspace.config_dir(node_dir)
            ports = PortSet.for_node(i, NodeRole.VALIDATOR)
            ansible_builder.add_node(
                name=node_name,
                ip=ip,
                ports=ports,
                config_path=cfg_path,
                role="validator",
            )

        for i in range(1, lab.num_peers + 1):
            node_name = f"pnode{i}"
            ip = lab.ansible.pips[i - 1] if i <= len(lab.ansible.pips) else ""
            node_dir = self.workspace.node_dir(cluster_dir, node_name)
            cfg_path = self.workspace.config_dir(node_dir)
            ports = PortSet.for_node(i, NodeRole.PEER)
            ansible_builder.add_node(
                name=node_name,
                ip=ip,
                ports=ports,
                config_path=cfg_path,
                role="peer",
            )

        ansible_builder.write()

    # ------------------------------------------------------------------
    # Local network workflow
    # ------------------------------------------------------------------

    def _run_local_network(self):
        """Execute local network workflow:

        1. Resolve features and the binary before the cluster directory exists
        2. Create VL keys
        3. Create validator keys
        4. Create NodeConfigs for local validators and peers
        5. Build config files for each node
        6. Parse amendments, write genesis for each node
        7. Build docker-compose for Docker-only services (VL + Explorer)
        8. Generate native start/stop scripts
        9. Sign UNL and write VL artifacts
        """
        lab = self.lab
        spec = self.spec

        name = f"local-{lab.protocol.value}"
        protocol_name = lab.protocol.value
        binary_name = lab.binary_name

        # 1. Resolve every input before the cluster directory exists
        feature_lines = self._resolve_local_feature_lines(lab.build_source, spec)
        binary_src = os.path.abspath(
            lab.build_source.binary_path or os.path.join(os.getcwd(), binary_name)
        )
        if not os.path.isfile(binary_src):
            raise FileNotFoundError(f"binary not found: {binary_src}")
        cluster_dir = self.workspace.cluster_dir(name)

        # Copy the binary into the cluster dir so start.sh finds it
        binary_dest = os.path.join(cluster_dir, binary_name)
        shutil.copy2(binary_src, binary_dest)
        os.chmod(binary_dest, 0o755)

        # 2-3. Create VL + validator keys
        original_dir = os.getcwd()
        os.chdir(cluster_dir)
        try:
            publisher = PublisherClient()
            vl_keys = publisher.get_keys()
            if not vl_keys:
                publisher.create_keys()
                vl_keys = publisher.get_keys()

            vl_pub_key = vl_keys["publicKey"]

            manifests: List[str] = []
            validators: List[str] = []
            tokens: List[str] = []
            ips_fixed: List[str] = []

            for i in range(1, lab.num_validators + 1):
                node_name = f"vnode{i}"
                vc = ValidatorClient(node_name)
                key_path = f"keystore/{node_name}/key.json"
                if not os.path.exists(key_path):
                    vc.create_keys()
                    vc.set_domain(f"{protocol_name}.{node_name}.transia.co")
                    vc.create_token()
                keys = vc.get_keys()
                token = vc.read_token()
                manifest = vc.read_manifest()
                manifests.append(manifest)
                validators.append(keys["public_key"])
                tokens.append(token)

                ports = PortSet.for_node(i, NodeRole.VALIDATOR, lab.port_offset)
                ips_fixed.append(f"127.0.0.1 {ports.peer}")

        finally:
            os.chdir(original_dir)

        # 4-6. Create nodes, configs, genesis
        for i in range(1, lab.num_validators + 1):
            node_name = f"vnode{i}"
            node = NodeFactory.create_local_validator(
                index=i,
                protocol=lab.protocol,
                name=name,
                network_id=lab.network_id,
                token=tokens[i - 1],
                all_validators=validators,
                vl_key=vl_pub_key,
                ips_fixed=ips_fixed,
                log_level=lab.log_level,
                node_db_type=lab.node_db_type,
                datagram_monitor=(
                    [lab.datagram_monitor] if lab.datagram_monitor else None
                ),
                num_ledgers=lab.online_delete,
                tree_cache_target_entries=lab.tree_cache_target_entries,
                memory_limit=lab.memory_limit,
                port_offset=lab.port_offset,
            )

            node_dir = self.workspace.node_dir(cluster_dir, node_name)
            cfg_path = self.workspace.config_dir(node_dir)
            self.workspace.log_dir(node_dir)

            cfg_content = self._render_cfg(node)
            vl_content = ValidatorsTxtBuilder(
                node, genesis=True, bootstrap_vl=lab.bootstrap_vl
            ).build()
            save_config(protocol_name, cfg_path, cfg_content, vl_content)

            features = parse_amendments(
                feature_lines, include_unsupported=self.lab.all_amendments
            )
            genesis = self._genesis(features, protocol_name)
            write_file(
                os.path.join(cfg_path, "genesis.json"),
                json.dumps(genesis, indent=4, sort_keys=True),
            )

        for i in range(1, lab.num_peers + 1):
            node_name = f"pnode{i}"
            node = NodeFactory.create_local_peer(
                index=i,
                protocol=lab.protocol,
                name=name,
                network_id=lab.network_id,
                validators=validators,
                vl_key=vl_pub_key,
                ips_fixed=ips_fixed,
                log_level=lab.log_level,
                node_db_type=lab.node_db_type,
                datagram_monitor=(
                    [lab.datagram_monitor] if lab.datagram_monitor else None
                ),
                num_ledgers=lab.online_delete,
                tree_cache_target_entries=lab.tree_cache_target_entries,
                memory_limit=lab.memory_limit,
                port_offset=lab.port_offset,
            )

            node_dir = self.workspace.node_dir(cluster_dir, node_name)
            cfg_path = self.workspace.config_dir(node_dir)
            self.workspace.log_dir(node_dir)

            cfg_content = self._render_cfg(node)
            vl_content = ValidatorsTxtBuilder(
                node, genesis=True, bootstrap_vl=lab.bootstrap_vl
            ).build()
            save_config(protocol_name, cfg_path, cfg_content, vl_content)

            features = parse_amendments(
                feature_lines, include_unsupported=self.lab.all_amendments
            )
            genesis = self._genesis(features, protocol_name)
            write_file(
                os.path.join(cfg_path, "genesis.json"),
                json.dumps(genesis, indent=4, sort_keys=True),
            )

        # 7. Docker compose for Docker-only services (VL + Explorer)
        compose = ComposeBuilder(f"{name}-network")
        compose.add_vl_service()
        ws_node, ws_port = explorer_target(lab.num_peers, lab.port_offset)
        compose.add_explorer_service(ws_port=ws_port, standalone=False)
        compose.write(os.path.join(cluster_dir, "docker-compose.yml"))

        # 8. Native start/stop scripts
        write_executable(
            os.path.join(cluster_dir, "start.sh"),
            ScriptBuilder.local_network_start(
                name,
                lab.num_validators,
                lab.num_peers,
                lab.binary_name,
                ws_node=ws_node,
                ws_port=ws_port,
            ),
        )
        write_executable(
            os.path.join(cluster_dir, "stop.sh"),
            ScriptBuilder.local_network_stop(name, lab.num_validators, lab.num_peers),
        )

        # 9. Sign UNL and write VL artifacts
        vl_dir = os.path.join(cluster_dir, "vl")
        os.makedirs(vl_dir, exist_ok=True)

        original_dir = os.getcwd()
        os.chdir(cluster_dir)
        try:
            for manifest in manifests:
                publisher.add_validator(manifest)
            publisher.sign_unl(os.path.join(vl_dir, "vl.json"))
        finally:
            os.chdir(original_dir)

        # Copy nginx dockerfile for VL
        nginx_src = os.path.join(
            self.workspace.package_dir,
            "deploykit",
            "nginx.dockerfile",
        )
        if os.path.exists(nginx_src):
            shutil.copyfile(nginx_src, os.path.join(vl_dir, "Dockerfile"))
