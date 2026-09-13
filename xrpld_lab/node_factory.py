"""NodeFactory -- creates NodeConfig instances for each deployment scenario."""

from __future__ import annotations

from typing import List, Optional

from xrpld_lab.models import (
    NodeConfig,
    PortSet,
    NodeRole,
    Protocol,
    DeployMode,
    ServerConfig,
    NodeDbConfig,
    NodeDbType,
    ValidatorIdentity,
)
from xrpld_lab.protocol import get_spec


class NodeFactory:
    """Creates NodeConfig instances for network nodes."""

    @staticmethod
    def create_standalone(
        protocol: Protocol,
        name: str,
        network_id: int,
        log_level: str = "trace",
        node_db_type: NodeDbType = NodeDbType.NUDB,
        vl_sites: Optional[List[str]] = None,
        vl_keys: Optional[List[str]] = None,
        ips_urls: Optional[List[str]] = None,
        ips_fixed_urls: Optional[List[str]] = None,
        datagram_monitor: Optional[List[str]] = None,
        database_path: str = "/opt/ripple/lib/db/rdb",
    ) -> NodeConfig:
        """Create a NodeConfig for a standalone node."""
        spec = get_spec(protocol)
        ports = PortSet.for_node(0, NodeRole.STANDALONE)
        node_db = NodeDbConfig.for_mode(node_db_type, DeployMode.STANDALONE)
        node_db.num_ledgers = (
            256  # realistic validator: online_delete on, keep ~256 ledgers
        )

        return NodeConfig(
            name=name,
            index=0,
            role=NodeRole.STANDALONE,
            protocol=protocol,
            network_id=network_id,
            ports=ports,
            server=ServerConfig(),
            node_db=node_db,
            db_path=database_path,
            debug_path="/opt/ripple/log/debug.log",
            size_node="huge",
            log_level=log_level,
            amendment_majority_time=spec.amendment_majority_time,
            vl_sites=vl_sites or [],
            vl_keys=vl_keys or [],
            ips_urls=ips_urls or [],
            ips_fixed_urls=ips_fixed_urls or [],
            datagram_monitor=datagram_monitor or [],
        )

    @staticmethod
    def create_validator(
        index: int,
        protocol: Protocol,
        name: str,
        network_id: int,
        token: str,
        all_validators: List[str],
        vl_key: str,
        ips_fixed: Optional[List[str]] = None,
        log_level: str = "warning",
        node_db_type: NodeDbType = NodeDbType.NUDB,
        ports: Optional[PortSet] = None,
        datagram_monitor: Optional[List[str]] = None,
        num_ledgers: Optional[int] = 256,
        tree_cache_target_entries: int = 0,
        memory_limit: Optional[int] = None,
        database_path: str = "/var/lib/xrpld/db/rdb",
        vl_sites: Optional[List[str]] = None,
        statsd_address: Optional[str] = None,
        statsd_prefix: str = "rippled",
        perf_path: Optional[str] = None,
    ) -> NodeConfig:
        """Create a NodeConfig for a validator node in a network."""
        spec = get_spec(protocol)
        ports = ports or PortSet.for_node(index, NodeRole.VALIDATOR)
        node_db = NodeDbConfig.for_mode(node_db_type, DeployMode.NETWORK)
        # None = online_delete off, [ledger_history] full (growth-study setting)
        node_db.num_ledgers = num_ledgers

        # Validator identity: own public key from all_validators (1-based index)
        self_key = all_validators[index - 1]
        validator = ValidatorIdentity(
            public_key=self_key,
            token=token,
            manifest="",
        )

        # Exclude self from validators list
        validators = [v for v in all_validators if v != self_key]

        # Exclude self from ips_fixed
        all_ips = ips_fixed or []
        self_ips_entry = all_ips[index - 1] if index - 1 < len(all_ips) else None
        ips_fixed_urls = [ip for ip in all_ips if ip != self_ips_entry]

        return NodeConfig(
            name=name,
            index=index,
            role=NodeRole.VALIDATOR,
            protocol=protocol,
            network_id=network_id,
            ports=ports,
            server=ServerConfig(),
            node_db=node_db,
            db_path=database_path,
            debug_path="/opt/ripple/log/debug.log",
            size_node="huge",
            log_level=log_level,
            validator=validator,
            validators=validators,
            vl_sites=vl_sites or ["http://vl/vl.json"],
            vl_keys=[vl_key],
            statsd_address=statsd_address,
            statsd_prefix=statsd_prefix,
            perf_path=perf_path,
            ips_fixed_urls=ips_fixed_urls,
            amendment_majority_time=spec.amendment_majority_time,
            datagram_monitor=datagram_monitor or [],
            tree_cache_target_entries=tree_cache_target_entries,
            memory_limit=memory_limit,
        )

    @staticmethod
    def create_peer(
        index: int,
        protocol: Protocol,
        name: str,
        network_id: int,
        validators: List[str],
        vl_key: str,
        ips_fixed: Optional[List[str]] = None,
        log_level: str = "warning",
        node_db_type: NodeDbType = NodeDbType.NUDB,
        ports: Optional[PortSet] = None,
        datagram_monitor: Optional[List[str]] = None,
        num_ledgers: Optional[int] = 256,
        tree_cache_target_entries: int = 0,
        memory_limit: Optional[int] = None,
        database_path: str = "/var/lib/xrpld/db/rdb",
        vl_sites: Optional[List[str]] = None,
        statsd_address: Optional[str] = None,
        statsd_prefix: str = "rippled",
        perf_path: Optional[str] = None,
    ) -> NodeConfig:
        """Create a NodeConfig for a peer node in a network."""
        spec = get_spec(protocol)
        ports = ports or PortSet.for_node(index, NodeRole.PEER)
        node_db = NodeDbConfig.for_mode(node_db_type, DeployMode.NETWORK)
        # None = online_delete off, [ledger_history] full (growth-study setting)
        node_db.num_ledgers = num_ledgers

        return NodeConfig(
            name=name,
            index=index,
            role=NodeRole.PEER,
            protocol=protocol,
            network_id=network_id,
            ports=ports,
            server=ServerConfig(),
            node_db=node_db,
            db_path=database_path,
            debug_path="/opt/ripple/log/debug.log",
            size_node="huge",
            log_level=log_level,
            validators=list(validators),
            vl_sites=vl_sites or ["http://vl/vl.json"],
            vl_keys=[vl_key],
            statsd_address=statsd_address,
            statsd_prefix=statsd_prefix,
            perf_path=perf_path,
            ips_fixed_urls=list(ips_fixed) if ips_fixed else [],
            amendment_majority_time=spec.amendment_majority_time,
            datagram_monitor=datagram_monitor or [],
            tree_cache_target_entries=tree_cache_target_entries,
            memory_limit=memory_limit,
        )

    @staticmethod
    def create_local_standalone(
        protocol: Protocol,
        name: str,
        network_id: int,
        log_level: str = "trace",
        node_db_type: NodeDbType = NodeDbType.NUDB,
        vl_keys: Optional[List[str]] = None,
    ) -> NodeConfig:
        """Create a NodeConfig for a local (native process) standalone node."""
        spec = get_spec(protocol)
        ports = PortSet.for_node(0, NodeRole.STANDALONE)
        node_db = NodeDbConfig.for_mode(node_db_type, DeployMode.LOCAL)
        node_db.num_ledgers = (
            256  # realistic validator: online_delete on, keep ~256 ledgers
        )

        return NodeConfig(
            name=name,
            index=0,
            role=NodeRole.STANDALONE,
            protocol=protocol,
            network_id=network_id,
            ports=ports,
            server=ServerConfig(),
            node_db=node_db,
            db_path="db",
            debug_path="log/debug.log",
            size_node="huge",
            log_level=log_level,
            amendment_majority_time=spec.amendment_majority_time,
            vl_keys=vl_keys or [],
        )

    @staticmethod
    def create_local_validator(
        index: int,
        protocol: Protocol,
        name: str,
        network_id: int,
        token: str,
        all_validators: List[str],
        vl_key: str,
        ips_fixed: Optional[List[str]] = None,
        log_level: str = "trace",
        node_db_type: NodeDbType = NodeDbType.NUDB,
        datagram_monitor: Optional[List[str]] = None,
        num_ledgers: Optional[int] = 256,
        tree_cache_target_entries: int = 0,
        memory_limit: Optional[int] = None,
        port_offset: int = 0,
    ) -> NodeConfig:
        """Create a NodeConfig for a local (native process) validator."""
        spec = get_spec(protocol)
        ports = PortSet.for_node(index, NodeRole.VALIDATOR, port_offset)
        node_db = NodeDbConfig.for_mode(node_db_type, DeployMode.LOCAL)
        # None = online_delete off, [ledger_history] full (growth-study setting)
        node_db.num_ledgers = num_ledgers

        # Validator identity: own public key from all_validators (1-based index)
        self_key = all_validators[index - 1]
        validator = ValidatorIdentity(
            public_key=self_key,
            token=token,
            manifest="",
        )

        # Exclude self from validators list
        validators = [v for v in all_validators if v != self_key]

        # Exclude self from ips_fixed
        all_ips = ips_fixed or []
        self_ips_entry = all_ips[index - 1] if index - 1 < len(all_ips) else None
        ips_fixed_urls = [ip for ip in all_ips if ip != self_ips_entry]

        return NodeConfig(
            name=name,
            index=index,
            role=NodeRole.VALIDATOR,
            protocol=protocol,
            network_id=network_id,
            ports=ports,
            server=ServerConfig(),
            node_db=node_db,
            db_path="db",
            debug_path="log/debug.log",
            size_node="huge",
            log_level=log_level,
            validator=validator,
            validators=validators,
            vl_sites=["http://127.0.0.1/vl.json"],
            vl_keys=[vl_key],
            ips_fixed_urls=ips_fixed_urls,
            amendment_majority_time=spec.amendment_majority_time,
            datagram_monitor=datagram_monitor or [],
            tree_cache_target_entries=tree_cache_target_entries,
            memory_limit=memory_limit,
        )

    @staticmethod
    def create_local_peer(
        index: int,
        protocol: Protocol,
        name: str,
        network_id: int,
        validators: List[str],
        vl_key: str,
        ips_fixed: Optional[List[str]] = None,
        log_level: str = "trace",
        node_db_type: NodeDbType = NodeDbType.NUDB,
        datagram_monitor: Optional[List[str]] = None,
        num_ledgers: Optional[int] = 256,
        tree_cache_target_entries: int = 0,
        memory_limit: Optional[int] = None,
        port_offset: int = 0,
    ) -> NodeConfig:
        """Create a NodeConfig for a local peer node."""
        spec = get_spec(protocol)
        ports = PortSet.for_node(index, NodeRole.PEER, port_offset)
        node_db = NodeDbConfig.for_mode(node_db_type, DeployMode.LOCAL)
        # None = online_delete off, [ledger_history] full (growth-study setting)
        node_db.num_ledgers = num_ledgers

        return NodeConfig(
            name=name,
            index=index,
            role=NodeRole.PEER,
            protocol=protocol,
            network_id=network_id,
            ports=ports,
            server=ServerConfig(),
            node_db=node_db,
            db_path="db",
            debug_path="log/debug.log",
            size_node="huge",
            log_level=log_level,
            validators=list(validators),
            vl_sites=["http://127.0.0.1/vl.json"],
            vl_keys=[vl_key],
            ips_fixed_urls=list(ips_fixed) if ips_fixed else [],
            amendment_majority_time=spec.amendment_majority_time,
            datagram_monitor=datagram_monitor or [],
            tree_cache_target_entries=tree_cache_target_entries,
            memory_limit=memory_limit,
        )
