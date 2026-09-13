# Changelog

## 4.0.0

Everything since 3.2.0, including the 3.3.0, 3.4.0 and 3.5.0 versions that were declared in `pyproject.toml` but never tagged or published.

### Added
- `status` service (`services: - status: {port: 8687}` in the ansible config): every node runs `node_metrics.py` as the `xrpld-status` systemd unit, the services host aggregates them at `/api/network` and `/api/network/health`, and its nginx vhost serves `/status/`, `/status/api/` and `/status/nodes/<name>/`. The network page is organised by audience and agreement tolerates one ledger of lag.
- `update:node`: replaces a node's binary from a docker image (`--image`) or a build server (`--build_server`), for binary-mode and image-mode clusters alike; the binary is fetched before the node is stopped.
- `vote:amendment`: lifts the veto on an amendment on every validator in the cluster, or one with `--node_id`, and prints each validator's returned `vetoed` and `enabled` fields.
- `--config_overrides` applies a YAML or JSON file to the rendered `xrpld.cfg` section by section: a mapping merges into a section, a list or scalar replaces it, an unknown section is appended.
- `--commit` runs the CI-published all-amendments supported image for a rippled ref; `--all-amendments` and `--features_file` for supported-build networks.
- db-seed boot mode: nodes `--load` from a snapshot-restored database directory.
- Non-genesis preserve deploys: serial rolling, keystore guards, `--genesis` parsing.
- `--workspace` and `--database_path`, so a running network can be redeployed in place; `--online_delete`; `memory_limit`; tree cache and consensus reserve knobs.
- Let's Encrypt certificate issuance for selected nginx services; the signed publisher list served from an nginx vhost on a services host; telemetry stanzas, the Alloy sidecar, per-node ssh keys and VL bootstrap for ansible clusters.
- Render tests that generate real standalone and network trees and assert on the files; CI runs the unit suite with coverage.
- ISC licence; PEP 621 package metadata with the README as the long description and the repository URL.

### Changed
- Python 3.10 or newer. `xrpl-py` is `^2.6.0`, the range `xrpld-publisher` allows; idna, urllib3, requests and python-dotenv updated.
- XRPL networks and standalones run the `rippleci/xrpld:<version>` release image by default; the default version is 3.3.0.
- Admin RPC and admin WS ports are published on the host's loopback only. Anything on another host that called a node's admin port must use the public port.
- `up:standalone` starts the node it generates. The generated image builds as root, the entrypoints find `xrpld` on PATH when no binary was copied in, and the stop scripts delete root-owned node data through a container.
- Operational commands exit non-zero when a step fails; command output streams live instead of being captured. `down:standalone` removes the directory only after a clean stop.
- The Artifact Registry token reaches the login task through the environment with `no_log`; the XDGM listener binds every interface so a NAT address no longer kills it.
- The GitHub-branch build URL supplies the repository; `--local` defaults to `XRPLF/rippled`. Binary-mode networks stage the binary into every node directory, and the ansible wrapper image tag keys on the commit.
- XRPL default network id is 1025; validator `online_delete` and `ledger_history` are 256.
- flake8 7.3.0 with black formatting; workflows run with a read-only token; `main` requires the three CI checks.

### Removed
- Xahau support: `--protocol xahau`, `xahau.entrypoint`, `genesis.xahau.json`, the Xahau build server and release defaults.
- `--import_key` and the `[import_vl_keys]` stanza.
- `--build_type` on `up:standalone`.
- `--nodedb_type rwdb` and the `[relational_db]` stanza; xrpld 3.3.0 has no such backend.
- `enable:amendment`, replaced by `vote:amendment`.
- `create:gcp`.
- The repository-config merge layer behind `--config_overrides`; overrides now apply to the generated config directly.

### Fixed
- The declared dependencies could not resolve, so `poetry install` failed everywhere.
- The CI entry point still named `xrpld-netgen`; the feature file for a registry build source was fetched from an HTTP URL made of the registry namespace.
- `alloy.yml` failed on every host on an inspect template that was not under `{% raw %}`.
- The standalone Dockerfile copied a binary nothing had downloaded.
- `--ipfs False` enabled IPFS.
- `node:restart` on a bare-process node killed the node and never relaunched it.
- `--vips` and `--pips` given as one string no longer collapse the fleet to one node; `stop.sh` with no peers; network SQLite `database_path` on the NVMe volume; non-genesis deploys no longer generate a genesis they do not need; the binary in node Dockerfiles is made executable; `run.sh` sets up ssh-agent portably.

### Known issues
- The standalone `validators.txt` carries no publisher key: `_run_standalone` does not pass `--public_key` through.
- `create:network` with the default `--genesis False` fails on a fresh workspace and leaves the cluster directory behind.
- Docker network mode keeps the node database in the container layer, so `docker compose down` discards it.
- Eight Dependabot alerts (cryptography, h11, pytest) are held by `xrpld-publisher` 2.1.0's pins and need a new release of that package.

## 3.2.0

### Added
- **`create:gcp`** — provision a multi-region xrpld perf cluster on GCP (one VM per node,
  terraform), harvest IPs into the ansible pipeline, deploy and health-check consensus.
- **Prefunded genesis (perf-iac style)** — inject N AccountRoot + M RippleState entries
  directly into genesis (`--preload_accounts`, `--preload_trustlines`, `--preload_balance`,
  `--preload_currency`) so a network starts with realistic state.
- **`--datagram_monitor "HOST PORT"`** on `create:network` / `create:ansible` / `create:gcp`
  (and `up:standalone`) — writes a `[datagram_monitor]` stanza into every node's config so
  each node emits XDGM metrics to the given sink (e.g. a perf-results server). Endpoint is
  space-separated `"<ip> <port>"`, matching xrpld's `DatagramMonitor` `parseEndpoint`.
- `endpoints.json` manifest emitted by `create:gcp` (named validators/peers with `ws`/`rpc`
  URLs) for downstream load tooling.

### Changed
- Default protocol is now `xrpl`.

### Fixed
- `update_genesis` now fails loud on a genesis template with no amendments or no `Amendments`
  entry, instead of silently producing an empty/unchanged genesis.
