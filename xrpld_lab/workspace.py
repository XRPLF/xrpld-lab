import os


class Workspace:
    """Manages output directories for lab artifacts."""

    def __init__(self, base: str = None):
        if base:
            self.base = os.path.abspath(base)
        else:
            self.base = os.path.abspath(os.path.join(os.getcwd(), "workspace"))
        os.makedirs(self.base, exist_ok=True)

    @property
    def package_dir(self) -> str:
        """Directory containing static assets (deploykit, genesis files, etc.)."""
        return os.path.abspath(os.path.dirname(__file__))

    def cluster_path(self, name: str) -> str:
        """Path of a network cluster directory, without creating it."""
        return os.path.join(self.base, f"{name}-cluster")

    def standalone_path(self, protocol: str, name: str) -> str:
        """Path of a standalone directory, without creating it."""
        return os.path.join(self.base, f"{protocol}-{name}")

    def cluster_dir(self, name: str) -> str:
        """Create and return path for a network cluster."""
        path = self.cluster_path(name)
        os.makedirs(path, exist_ok=True)
        return path

    def standalone_dir(self, protocol: str, name: str) -> str:
        """Create and return path for a standalone instance."""
        path = self.standalone_path(protocol, name)
        os.makedirs(path, exist_ok=True)
        return path

    def node_dir(self, parent: str, node_name: str) -> str:
        """Create and return path for a node within a cluster/standalone."""
        path = os.path.join(parent, node_name)
        os.makedirs(path, exist_ok=True)
        return path

    def config_dir(self, node_path: str) -> str:
        """Create and return config directory for a node."""
        path = os.path.join(node_path, "config")
        os.makedirs(path, exist_ok=True)
        return path

    def log_dir(self, node_path: str) -> str:
        """Create and return log directory for a node."""
        path = os.path.join(node_path, "log")
        os.makedirs(path, exist_ok=True)
        return path
