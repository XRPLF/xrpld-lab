# xrpld-lab

Build xrpld networks and standalone ledgers for testing and development.

## What it does

xrpld-lab creates local XRPLD networks using pre-built binaries or Docker images. Instead of building rippled from source inside Docker (slow), you build natively on your machine and deploy to a multi-node network in seconds.

Four deployment modes:

- **Standalone** -- single-node Docker ledger for quick testing
- **Network** -- multi-validator Docker cluster with explorer and VL
- **Local** -- multi-node native processes (no Docker for nodes, fastest iteration)
- **Ansible** -- generate ansible playbooks for remote server deployment

## Install

```bash
pip install xrpld-lab
```

## Quick start

```bash
# Standalone XRPL ledger
xrpld-lab up:standalone

# 3-validator, 1-peer XRPL network from the rippleci/xrpld release image
xrpld-lab create:network --num_validators 3 --num_peers 1 --genesis True
xrpld-lab up --name 3.3.0-cluster

# XRPL network from a custom GitHub branch (local binary)
xrpld-lab create:network \
  --protocol xrpl \
  --build_server "https://github.com/XRPLF/rippled/tree/xrplf-smart-contracts" \
  --build_version <commit_hash> \
  --num_validators 3 --num_peers 1

# Local network (native processes, no Docker for nodes)
xrpld-lab create:network --protocol xrpl --local

# Ansible deployment to remote servers
xrpld-lab create:ansible \
  --protocol xrpl \
  --build_version 3.3.0 \
  --num_validators 6 --num_peers 2 \
  --vips 10.0.0.1 10.0.0.2 10.0.0.3 10.0.0.4 10.0.0.5 10.0.0.6 \
  --pips 10.0.0.7 10.0.0.8 \
  --genesis True --quorum 3
```

## Commands

### `up:standalone` -- Create and start a standalone ledger

```bash
xrpld-lab up:standalone [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--protocol` | `xrpl` | Protocol |
| `--version` | `3.3.0` | Build version |
| `--log_level` | `trace` | `warning`, `debug`, `trace` |
| `--network_id` | `1` | Network identifier |
| `--nodedb_type` | `NuDB` | `NuDB` (persistent) or `Memory` (fast) |
| `--ipfs` | `false` | Include IPFS server |
| `--server` | auto | Build server URL |
| `--public_key` | default | Validator list public key |
| `--config_overrides` | none | Path to YAML/JSON config overrides |

### `create:network` -- Create a multi-node network

```bash
xrpld-lab create:network [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--protocol` | `xrpl` | Protocol |
| `--num_validators` | `3` | Number of validator nodes |
| `--num_peers` | `1` | Number of peer nodes |
| `--build_version` | `3.3.0` | Build version or commit hash |
| `--build_server` | auto | Build server URL or GitHub branch URL |
| `--network_id` | `1025` | Network identifier |
| `--log_level` | `trace` | `warning`, `debug`, `trace` |
| `--genesis` | `false` | Genesis mode |
| `--quorum` | `n-1` | Consensus quorum |
| `--nodedb_type` | `NuDB` | `NuDB` (persistent) or `Memory` (fast) |
| `--local` | off | Run as native processes (no Docker for nodes) |
| `--binary_name` | `xrpld` | Binary name for local networks |
| `--binary_path` | `./xrpld` | Path to pre-built binary (GitHub URL mode) |
| `--config_overrides` | none | Path to YAML/JSON config overrides |
| `--ansible` | off | Also generate ansible deployment files |
| `--vips` | none | Validator IPs (for ansible) |
| `--pips` | none | Peer IPs (for ansible) |

**Build server modes:**

- **XRPL Docker**: `--build_server` defaults to `rippleci`. Uses Docker image `rippleci/xrpld:<version>`.
- **XRPL GitHub**: Pass `--build_server "https://github.com/OWNER/repo/tree/branch"` with `--build_version <commit_hash>`. Copies local binary, resolves features from GitHub at that commit.

### `create:ansible` -- Create network with ansible deployment

Generates everything `create:network` does, plus a complete ansible directory for deploying to remote servers.

```bash
xrpld-lab create:ansible \
  --protocol xrpl \
  --vips 10.0.0.1 10.0.0.2 10.0.0.3 \
  --pips 10.0.0.4 \
  [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--vips` | required | Validator IP addresses |
| `--pips` | required | Peer IP addresses |
| `--ssh_port` | `20` | SSH port for ansible |
| `--ssh_user` | `ubuntu` | SSH user |
| `--ssh_key` | `~/.ssh/id_rsa` | SSH private key path |
| `--ansible_config` | none | YAML file with full ansible config (services, etc.) |

Plus all `create:network` options (protocol, build_server, etc.)

**Ansible config file** for complex deployments with services:

```yaml
ssh_port: 22
ssh_user: ubuntu
ssh_key_path: ~/.ssh/id_rsa
vips:
  - 10.0.0.1
  - 10.0.0.2
  - 10.0.0.3
pips:
  - 10.0.0.4
services:
  - ip: 10.0.0.5
    name: infra
    nginx:
      domain: example.com
      ssl_org: MyOrg
    redis: {}
    faucet:
      ws_url: ws://10.0.0.4:6018
      network_id: "1025"
      seed: sEdxxxxxxxxx
    status:
      port: 8687
```

**Status service** (`status:` on the services host, which must be one of the nodes). Every
node gets `node_metrics.py` as the `xrpld-status` systemd unit: it samples `/proc`, the
admin RPC `server_info` and the XDGM datagram into a SQLite ring buffer and serves a
per-node dashboard plus `/api/latest`, `/api/series`, `/api/events`, `/api/health`. Each
node's `[datagram_monitor]` is pointed at its own address and the sampler's `xdgm_port`
(default 9999) unless `--datagram_monitor` names another sink. The services host's sampler
also aggregates every node (`/api/network`, `/api/network/health`: 200 only when every
validator is `proposing` and every peer `full`), and its bare-domain nginx vhost serves
`/status/` (network dashboard), `/status/api/` (aggregation) and `/status/nodes/<name>/`
(each node's dashboard). Samplers on the other nodes bind `0.0.0.0:<port>`, admitted by ufw
from the services host only. Operators write `/opt/xrpld-status/network.json` on the
services host after a deploy (`last_deploy`, `branches`, `faucet`, `vl`, `amendments`);
the network page renders it. `status.yml` re-runs on every deploy; the nginx vhost is a
`run_once` stage, so an existing deployment picks up the new locations with `--force` or
by deleting `.done_<host>_nginx`.

| `status` key | Default | Description |
|---|---|---|
| `port` | `8687` | Sampler HTTP port on every node |
| `xdgm_port` | `9999` | UDP port for xrpld's XDGM datagram |
| `interval` | `10` | Sampling interval in seconds |
| `process` | `xrpld` | Process name looked up in `/proc` for RSS |
| `disk_path` | `/var/lib/xrpld/db` | Filesystem measured for disk usage |
| `network_name` | nginx domain | Heading on the network dashboard |
| `retain_raw_hours` / `retain_5m_days` / `retain_1h_days` | `48` / `30` / `365` | Ring buffer retention |

### `deploy:ansible` -- Run ansible deployment

```bash
xrpld-lab deploy:ansible --name <cluster>
```

Runs the generated `run.sh` in the cluster's ansible directory.

### Operational commands

```bash
xrpld-lab up --name <network>      # Start a network
xrpld-lab down --name <network>    # Stop a network
xrpld-lab remove --name <network>  # Remove a network
```

## Config overrides

Override any xrpld config value with a YAML file:

```yaml
# overrides.yaml
node_size: medium
transaction_queue:
  ledgers_in_queue: 50
  maximum_txn_in_ledger: 5000
```

```bash
xrpld-lab up:standalone --config_overrides overrides.yaml
```

Overrides apply on top of the generated config, section by section: a mapping merges into the section's keys, a list or a scalar replaces the section, and a section the generated config lacks is appended.

## Architecture

```
xrpld_lab/
  models.py           # Dataclasses: Protocol, NodeConfig, LabConfig, PortSet, etc.
  protocol.py         # ProtocolSpec: protocol-specific defaults in one place
  config_builder.py   # XrpldCfgBuilder + ValidatorsTxtBuilder
  config.py           # INI parsing, YAML overrides, 3-layer merge, ansible config loader
  amendments.py       # C++ feature macro parsing + genesis updates
  source_resolver.py  # GitHub fetch, binary download, feature/config resolution
  node_factory.py     # Creates NodeConfig for validator/peer/standalone
  compose_builder.py  # docker-compose.yml as structured dict
  script_builder.py   # Dockerfiles + start/stop shell scripts
  workflows.py        # LabRunner: orchestrates standalone/network/local/ansible
  cli.py              # Thin CLI: argparse -> LabConfig -> LabRunner.run()
  ansible_builder.py  # Ansible playbooks, inventory, host vars, services
  services/status/    # node_metrics.py sampler + node and network dashboards
  operations.py       # Node updates, amendment enabling, log viewing
  workspace.py        # Path resolution
  utils.py            # File I/O, colors, subprocess helpers
```

## Development

```bash
git clone https://github.com/XRPLF/xrpld-lab
cd xrpld-lab
poetry install
poetry run pytest tests
```

## Current versions

- XRPL: `3.3.0`

## License

ISC. See [LICENSE](LICENSE).
