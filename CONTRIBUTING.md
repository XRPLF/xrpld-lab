# Contributing

## Setup

```bash
git clone https://github.com/XRPLF/xrpld-lab
cd xrpld-lab
poetry install
```

## Development

TDD workflow: write tests first, then implement.

```bash
# Run all tests
poetry run pytest tests/ -v

# Run a specific test file
poetry run pytest tests/unit/test_config_builder.py -v

# Run with coverage
poetry run pytest tests/ --cov=xrpld_lab
```

## Project structure

Source code lives in `xrpld_lab/`. Tests live in `tests/unit/`. Each module has a corresponding test file.

## Publishing

`main` requires the `lint`, `unit-tests` and `cli-test` checks, so a release goes through a pull request. This is the process 4.0.0 followed.

1. Add a `## X` section to `CHANGELOG.md` and set `version = "X"` in `pyproject.toml`.
2. Open a pull request titled `Release X` with those two changes and merge it once the checks pass.
3. Tag the merge commit and publish the GitHub release with the changelog section as its notes:

   ```bash
   cd /path/to/xrpld-lab && git checkout main && git pull
   git tag vX && git push origin vX
   gh release create vX --title X --notes-file /path/to/release-notes.md
   ```

4. Build and upload to PyPI from a checkout of the tag:

   ```bash
   cd /path/to/xrpld-lab && git checkout vX
   rm -rf dist && poetry publish --build
   ```

Requires a PyPI API token configured via `poetry config pypi-token.pypi <token>`.
