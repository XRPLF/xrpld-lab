"""Centralized docker-compose.yml builder for xrpld_lab.

Replaces the inline dict construction scattered across
``xrpld_netgen/main.py`` and ``xrpld_netgen/network.py``.
"""

from __future__ import annotations

import yaml

from xrpld_lab.models import PortSet, NodeRole


class ComposeBuilder:
    """Builds docker-compose.yml as a structured dict."""

    def __init__(self, network_name: str):
        self.network_name = network_name
        self.services: dict = {}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _port_mappings(self, ports: PortSet) -> list[str]:
        return ports.publish_mappings()

    # ------------------------------------------------------------------
    # Service builders
    # ------------------------------------------------------------------

    def add_node_service(
        self,
        name: str,
        ports: PortSet,
        role: NodeRole,
    ) -> ComposeBuilder:
        """Add an xrpld node service (validator or peer).

        - build context is the node directory name, dockerfile "Dockerfile"
        - platform: linux/x86_64
        - port mappings for all 5 ports
        - volumes: ./name/config, ./name/log, ./name/lib -> /opt/ripple/*,
          ./name/db -> /var/lib/xrpld/db
        """
        self.services[name] = {
            "build": {"context": name, "dockerfile": "Dockerfile"},
            "platform": "linux/x86_64",
            "container_name": name,
            "ports": self._port_mappings(ports),
            "volumes": [
                f"./{name}/config:/opt/ripple/config",
                f"./{name}/log:/opt/ripple/log",
                f"./{name}/lib:/opt/ripple/lib",
                f"./{name}/db:/var/lib/xrpld/db",
            ],
            "networks": [self.network_name],
        }
        return self

    def add_standalone_service(
        self,
        protocol: str,
        ports: PortSet,
    ) -> ComposeBuilder:
        """Add standalone xrpld service.

        - build: context ".", dockerfile "Dockerfile"
        - platform: linux/x86_64
        - container_name: protocol name
        - volumes use ${PWD}/protocol/... paths
        """
        pwd = "${PWD}"
        self.services[protocol] = {
            "build": {"context": ".", "dockerfile": "Dockerfile"},
            "platform": "linux/x86_64",
            "container_name": protocol,
            "ports": self._port_mappings(ports),
            "volumes": [
                f"{pwd}/{protocol}/config:/etc/opt/ripple",
                f"{pwd}/{protocol}/log:/opt/ripple/log",
                f"{pwd}/{protocol}/lib:/opt/ripple/lib",
            ],
            "networks": [self.network_name],
        }
        return self

    def add_explorer_service(
        self,
        ws_port: int = 6006,
        standalone: bool = True,
    ) -> ComposeBuilder:
        """Add explorer service.

        standalone=True: image transia/explorer:latest, container "explorer",
            env PORT=4000, VUE_APP_WSS_ENDPOINT=ws://0.0.0.0:{ws_port}
        standalone=False: container "network-explorer", with wss env
        """
        if standalone:
            self.services["explorer"] = {
                "image": "transia/explorer:latest",
                "container_name": "explorer",
                "environment": [
                    "PORT=4000",
                    f"VUE_APP_WSS_ENDPOINT=ws://0.0.0.0:{ws_port}",
                ],
                "ports": ["4000:4000"],
                "networks": [self.network_name],
            }
        else:
            self.services["network-explorer"] = {
                "image": "transia/explorer:latest",
                "container_name": "network-explorer",
                "environment": [
                    "PORT=4000",
                    f"VUE_APP_WSS_ENDPOINT=ws://0.0.0.0:{ws_port}",
                ],
                "ports": ["4000:4000"],
                "networks": [self.network_name],
            }
        return self

    def add_vl_service(self) -> ComposeBuilder:
        """Add VL (validator list) service for network mode.

        - build context "vl", dockerfile "Dockerfile"
        - container "vl", port 80:80
        - healthcheck: curl -f http://localhost/vl.json
        """
        self.services["vl"] = {
            "build": {"context": "vl", "dockerfile": "Dockerfile"},
            "container_name": "vl",
            "ports": ["80:80"],
            "networks": [self.network_name],
            "healthcheck": {
                "test": ["CMD", "curl", "-f", "http://localhost/vl.json"],
                "interval": "5s",
                "timeout": "3s",
                "retries": 3,
                "start_period": "10s",
            },
        }
        return self

    def add_ipfs_service(self, protocol: str) -> ComposeBuilder:
        """Add IPFS service.

        - image: ipfs/go-ipfs:latest
        - ports: 4001, 5001, 8080
        - volumes: ${PWD}/protocol/ipfs_staging, ipfs_data
        """
        pwd = "${PWD}"
        self.services["ipfs"] = {
            "image": "ipfs/go-ipfs:latest",
            "container_name": "ipfs",
            "environment": ["IPFS_PROFILE=server"],
            "ports": ["4001:4001", "5001:5001", "8080:8080"],
            "volumes": [
                f"{pwd}/{protocol}/ipfs_staging:/export",
                f"{pwd}/{protocol}/ipfs_data:/data/ipfs",
            ],
            "networks": [self.network_name],
        }
        return self

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def build(self) -> dict:
        """Return the full docker-compose dict."""
        return {
            "services": self.services,
            "networks": {self.network_name: {"driver": "bridge"}},
        }

    def render(self) -> str:
        """Render to YAML string."""
        return yaml.dump(self.build(), default_flow_style=False)

    def write(self, path: str) -> None:
        """Write docker-compose.yml to path."""
        with open(path, "w") as f:
            yaml.dump(self.build(), f, default_flow_style=False)
