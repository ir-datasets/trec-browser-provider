# Changelog

Notable changes to this project. The package is not yet published to PyPI
(see `README.md`), so versions refer to the git history / Docker image tags.

## [0.1.0] - 2026-10-29

First standalone release, extracted from the `ir-datasets` monorepo.

### Added

- The `trec-browser:` provider: run files (`{track}/{subtrack}/input.{run_id}.gz`)
  and `trec_eval` summaries (`summary.trec_eval.{run_id}`,
  `summary.extended.{run_id}`) linked from the TREC Browser resolve to parsed
  `TrecScoredDocs`/`TrecEval` tables; everything else under
  `trec.nist.gov/results/` resolves to a bare, unparsed `Resource`.
- A static metadata index (`trec_browser_provider/etc/trec_browser_runs.json.gz`),
  scraped from the browser's public pages, giving known runs a real MD5 plus
  `participant`/`track`/`year`/`submission`/`submission_type` metadata and a
  deep link back to the browser page. Regenerate with
  `python -m trec_browser_provider.export`.
- One `Benchmark` per `track/subtrack`, with `SUITE_MEMBER` edges to every
  known run/summary submitted to it and a `docs` facet resolved from the
  browser's `ir_datasets` cross-references where unambiguous.
- Dedicated `{path}.raw` generators so direct raw-resource lookups keep the
  real MD5 instead of falling through to the generic catch-all.
- Soft (warn-only) MD5 verification: a scraped hash mismatch never aborts a
  download.
- Graph-db Docker image (`docker/graph-db.Dockerfile`), served with gunicorn,
  published manually via GitHub Actions.
- Devcontainer; CI running ruff lint and a pytest matrix (3.10-3.13) with a
  coverage report uploaded to Codecov (badged in the README).

### Fixed

- The scraper no longer concatenates every run's field values when NIST's
  HTML leaves a run's `<ul>` unclosed (e.g. `trec19/entity`'s ICTNETRun1).
- Stray whitespace can no longer leak into track/subtrack node names from
  unescaped NIST hrefs.
- Direct `.raw` resource lookups resolve the real, MD5-bearing resource even
  when looked up before their parent table.
