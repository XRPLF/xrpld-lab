# Changelog

## Unreleased

### Breaking
- Keys, tokens, domain attestations, manifests and the signed validator list come from rippled's `validator-keys` tool, run as a subprocess; the `xrpld-publisher` Python package is no longer a dependency. The tool is found through `VALIDATOR_KEYS_BIN`, beside the xrpld binary named by `--binary_path`, on `PATH`, or at `/opt/xrpld/bin/validator-keys` inside the cluster's docker image.
- A `keystore/vl/key.json` written by the `xrpld-publisher` package (no `key_type` field) is refused: run `xrpld-publisher migrate-keys` on it, or redeploy as genesis.

### Changed
- The keystore keeps the tool's own files: `keystore/vl/{key.json, token.txt, manifest.txt}` for the publisher (an ed25519 signing key) and `keystore/vnodeN/{key.json, token.txt, manifest.txt, attestation.txt}` for each validator. `[validator_list_keys]` carries the publisher's master key as hex.
- The signed list is built from `vl/unsigned.json` (sequence = signing time in unix seconds, expiration 30 days later), signed with `sign_list` and checked with `verify_list` before the workflow completes.

## 5.0.0

### Breaking
- `node:restart` takes `--name <cluster>` instead of reading the current directory.
- `--ssh_port` defaults to 22, not 20.

### Changed
- Unset `--genesis` follows the cluster's keystore: a fresh chain when the workspace has none, preserve when it has one; `True` and `False` still force. Every input resolves before the cluster directory exists, so a failed run leaves nothing behind.
- One cluster name resolver: `X` and `X-cluster` mean the same directory everywhere, including `deploy:ansible`. `--workspace` on every command that addresses a cluster; `--port_offset` on `vote:amendment`, `node:stall` and `health`.
- Docker network and standalone nodes keep their database on the host (`<node>/db` and `xrpl/lib/db`), so `docker compose down` no longer discards the ledger; `stop.sh --remove` deletes it.
- `health` requires validators to be `proposing`, the same rule the status sampler applies.
- Ansible node plays target a `nodes` group; a dedicated services host is no longer pruned or failed by them.
- Services host: websocketd binds the docker bridge address, redis is published on loopback, the docker group is appended rather than replacing ubuntu's primary group, CORS sits at server level so HSTS and the other headers apply, and `vl` requires `nginx` on the same host.
- `create:network --local` honours `--features_file` and `--binary_path`; a rippled build directory still works without flags.
- `create:ansible` rejects a `--num_validators` or `--num_peers` that does not match its IP list, and derives the counts from the lists when only the lists are given.
- `remove` stops the cluster before deleting; `up:local`'s stop script kills its own pid, not every local node on the machine.
- `run.sh` stops at the first failed playbook and `deploy:ansible` propagates the exit status.
- `--network_id 0` is honoured; one build-version default; the local cluster's explorer port and start script derive from the node's ports.
- Dependencies: `xrpld-publisher` 3, `xrpl-py` 5, cryptography 50; pytest 9 in the dev group.

### Fixed
- The status sampler applied the XDGM fallback from the previous sample; it now uses the row being built, so an RPC recovery no longer records a spurious state change. The node dashboard escapes `build_version`, `complete_ledgers` and event fields. `rollup()` only aggregates new buckets; `/api/events?limit=-1` no longer returns the whole table.
- The standalone `validators.txt` carries the publisher key.
- `update:node` on a host without docker reports failure instead of raising from cleanup.
- `start_local` fails before writing anything when the features file has no supported amendment.
- `parse_xrpld_cfg` merges a repeated section the way xrpld does.

### Repository
- 100% line coverage, enforced by the `unit-tests` job.
- CodeQL rewritten on `codeql-action@v4` and enabled. Workflows run with a read-only token; `main` requires the CI checks.

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
