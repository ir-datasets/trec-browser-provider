# Contributing

## Development setup

The easiest way is the provided devcontainer (`.devcontainer/`), which already
installs `ir_datasets`'s unreleased `v2` package from git plus this package in
editable mode. Manually:

```bash
pip install 'git+https://github.com/ir-datasets/ir-datasets.git@v2'
pip install -r requirements-test.txt
pip install -e .
```

Note: `ir_datasets`'s `v2` branch is still unstable, and CI and the graph-db
image track its head -- expect occasional upstream breakage until it settles.

## Tests

```bash
pytest                                                    # fast, no network
pytest --cov=trec_browser_provider --cov-report=term-missing
```

CI runs the same suite with `--cov-report=xml` and uploads the result to
[Codecov](https://codecov.io/gh/ir-datasets/trec-browser-provider) (the badge
in the README). The upload **requires** a repo-scoped `CODECOV_TOKEN`
repository secret -- Codecov rejects tokenless uploads; get the token from
codecov.io (trec-browser-provider -> Settings).

The suite never touches the network: scraper tests run against committed
snapshots of real (sometimes malformed) NIST pages in `test/resources/`, and
download paths are exercised with stand-in sources. Actually downloading a
run/summary file needs the shared TREC credentials -- see
[`trec_browser_provider/docs/trec-browser.md`](trec_browser_provider/docs/trec-browser.md).

## Lint

```bash
ruff check .
```

Configuration lives in `pyproject.toml` (CI pins the ruff version in
`.github/workflows/test.yml`). The codebase's existing style -- long
explanatory docstrings/comments -- is intentional; long lines are only
allowed in `test/test_trec_browser_export.py`'s embedded copies of real NIST
HTML, which must not be re-wrapped.

## Regenerating the static index

The shipped index (`trec_browser_provider/etc/trec_browser_runs.json.gz`) is a
scrape of the TREC Browser's public pages; refresh it when the live site gains
tracks/runs:

```bash
python -m trec_browser_provider.export            # full scrape
python -m trec_browser_provider.export --limit 2  # quick smoke test
```

Commit the regenerated file together with a note of what changed on the live
site. Runs added after the last regeneration still resolve without it (the
dynamic generators cover every name shape) -- they just lack the scraped
metadata until the index is refreshed.

## Pull requests

CI must pass: `lint` (ruff) and `pytest` across Python 3.10-3.13. Keep changes
surgical, and mirror the codebase's convention of explaining *why* in
docstrings/comments when a decision is not obvious.
