# `trec-browser:` -- TREC Browser-linked run files and summaries

Resolves run files and `trec_eval` summaries linked from the
[TREC Browser](https://pages.nist.gov/trec-browser/), NIST's site for
browsing and searching the metadata of every run ever submitted to TREC.

```python
import ir_datasets.v2 as v2

run = v2.load('trec-browser:trec28/decision/input.ICTNETv1BM25.gz')
for sdoc in run.scoreddocs:
    ...

summary = v2.load('trec-browser:trec28/decision/summary.trec_eval.ICTNETv1BM25')
for measure in summary.evaluations:
    ...
```

A name is the literal path under `trec.nist.gov/results/` -- `track/
subtrack/filename` -- exactly as linked from a TREC Browser run listing
page (e.g. <https://pages.nist.gov/trec-browser/trec28/decisions/runs/>).
Three shapes are recognized:

* `{track}/{subtrack}/input.{run_id}.gz` -- the run file. Resolves directly
  to a parsed `TrecScoredDocs` table (a `RunTable`), not a bare byte
  resource.
* `{track}/{subtrack}/summary.trec_eval.{run_id}` -- the standard
  `trec_eval` summary. Resolves to a parsed `TrecEval` table (an
  `EvaluationTable`, not a `RunTable` -- it is not a leaderboard, it's the
  one run's own evaluation against the track's qrels), one `GenericMeasure`
  per line (`measure, qid, value` -- `qid` is `'all'` for the aggregate row,
  trec_eval's own convention; `value` is a raw string, not coerced to
  `float`, since the very first row is always `runid  all  <the run's own
  tag>`, non-numeric).
* `{track}/{subtrack}/summary.extended.{run_id}` -- the same shape, with
  NIST's extended measure set; also a `TrecEval`/`EvaluationTable`.

Anything else under `results/` (appendices, proceedings PDFs, ...) still
resolves, as a bare, unparsed `Resource`.

`{run_id}` is always the literal, percent-encoded filename segment TREC
itself hosts the run under -- e.g. `input.BLIP%20BLIP2%20...diffusion.gz`
-- never a submitting team's free-text "Run ID" as typed into the browser
page (NIST never sanitizes that field, and real submissions do contain
literal spaces, parentheses, `+`, etc.). This guarantees a `trec-browser:`
name never contains whitespace, whether or not the TREC Browser's own HTML
happened to percent-encode the href it was scraped from.

Known runs (as of the last index regeneration, see below) resolve with real
metadata attached -- an MD5 hash on the run file, plus `participant`,
`track`, `year`, `submission`, `type`, and a `deep_link` back to the live
browser page, all in `node.metadata`. Runs/summaries not (yet) in the index
still resolve, dynamically, with no extra metadata -- this family never
raises "not found" for a name shaped like a TREC Browser path.

Some tracks' TREC Browser pages also cross-reference the real `ir_datasets`
dataset id(s) backing that track's data (e.g.
[`trec29/deep/data/`](https://pages.nist.gov/trec-browser/trec29/deep/data/)
lists `msmarco-passage-v2/trec-dl-2020` and
`msmarco-document-v2/trec-dl-2020`); most don't (e.g.
[`trec34/rag/data/`](https://pages.nist.gov/trec-browser/trec34/rag/data/)
lists none). When present, this is scraped too and surfaced on every known
run/summary for that track/subtrack as `node.metadata['ir_datasets_ids']`
(a list; omitted entirely, not an empty list, when the TREC Browser itself
doesn't list any) -- extracted "when possible", never required.

The MD5 is scraped off an HTML listing page, not computed from the file's
actual bytes, so it can legitimately drift out of sync with what
`trec.nist.gov` currently serves (a withdrawn/resubmitted run, a stale
index, ...) without the file being corrupt. Because of that, a mismatch
between the declared MD5 and the downloaded run file's real MD5 only
**logs a warning** here, never raises -- unlike every other `ir_datasets`
resource, where a hash mismatch is a hard failure. The download always
completes and the bytes are always delivered; re-run the scraper (below) if
you want the index's hash refreshed.

## The static index

`trec_browser_provider/etc/trec_browser_runs.json.gz` is a package-shipped,
gzip'd JSON snapshot of every run linked from the TREC Browser's own
`.../runs/` listing pages (track, subtrack, run id, participant, year, MD5,
description, deep link, ...), plus, for any track/subtrack whose own
`.../data/` page cross-references them, the real `ir_datasets` dataset
id(s) -- all scraped from the public, ungated browsing pages (not the gated
result files themselves -- no credentials needed to build or refresh it).
It backs an `enumerable=True` generator (so these
known runs show up in a `list`/`freeze` of this provider, each with its real
MD5 and other metadata, unlike the fully dynamic family below), checked
*before* the dynamic, pattern-based generators -- a run already in the index
always resolves with its full metadata; anything added to the live site
since is still reachable through the dynamic fallback, just without it.

Regenerate it (a maintenance task, not something end users need to run) with:

```bash
python -m trec_browser_provider.export
# writes trec_browser_provider/etc/trec_browser_runs.json.gz; --out to change where,
# --limit N for a quick smoke test against just the first N listing pages.
```

This is an open-ended family overall (`ir_datasets` has no way to guarantee
the index is ever fully up to date -- NIST adds tracks and runs every
year): the dynamic fallback generators remain `enumerable=False`, the same
as `hf:`.

## Credentials

Every file under `trec.nist.gov/results/` is gated behind one shared HTTP
Basic Auth account. **This is one generic, non-personal account used across
the whole TREC community** -- not an account tied to you or your
institution. `ir_datasets` reads it from two environment variables, read
fresh at download time (not at import time, and never written to disk by
`ir_datasets` itself):

```bash
export IRDS_TREC_BROWSER_USERNAME=...
export IRDS_TREC_BROWSER_PASSWORD=...
```

If they are unset, loading triggers no error by itself -- only an actual
download does, with a `RuntimeError` pointing back here.

## Exporting with `ir_datasets export`

```bash
ir_datasets export trec-browser:trec28/decision/input.ICTNETv1BM25.gz scoreddocs
```

A glob pattern (`*`/`?`/`[...]`, matched with `fnmatch`) exports every
matching run file into its own file in a directory, instead of a single
exact name to one stream -- `--out <directory>` is required for a pattern
(there's no single stream a whole track/subtrack's worth of distinct run
files could sensibly share):

```bash
ir_datasets export 'trec-browser:trec28/decision/*' scoreddocs --out trec28-decision/
```

Quote the pattern so the shell doesn't expand it itself. Matches are limited
to what the static index (below) already knows about -- the same per-run
metadata it reports elsewhere -- and anything matched that isn't a
`scoreddocs` table (a `summary.*` evaluation table caught by the same `*`,
say) is silently skipped. A match that *is* a run file but whose own content still
isn't exportable as `scoreddocs` (see the JSON paragraph below) is skipped
the same way, with a warning, rather than aborting the whole export. Each
file is named after its own run id plus `--format` as the extension
(`ICTNETv1BM25.trec`), and keeps that run id as its own TREC runtag unless
`--runtag` is given explicitly, which then applies to every file instead.

Most `input.<run_id>.gz` files are the classic whitespace-separated TREC
run format (`query_id iteration doc_id rank score run_tag`, or the 2/3-column
MS MARCO/MMARCO shapes); some recent tracks (TREC CAsT/iKAT `response
generation` runs, e.g. `trec31/cast/input.*.gz`) are instead one JSON
document per file (`{"turns": [{"turn_id": ..., "responses": [{"provenance":
[{"id": doc_id, "score": score}, ...]}, ...]}, ...]}`). `export ... scoreddocs`
sniffs the file's own first non-whitespace byte and parses either shape into
the same `(query_id, doc_id, score)` records (`turn_id` as `query_id`,
flattened across every response's provenance list) -- see
`formats._SniffingScoredDocsHandler`. A JSON run with no retrieved-document
rankings at all (a conversational-query-rewriting run, whose turns carry
`questions` rather than `responses`/`provenance`) is not a scoreddocs file
however its name suggests, and is reported as a clean error (or, inside a
glob, skipped with a warning) rather than exported as empty/nonsense.

Pass `--raw` to persist the file exactly as hosted -- no parsing, no
run-file format conversion, not even gunzipping -- instead of exporting a
parsed `scoreddocs`/`docs`/etc. table. This works for a `summary.*`
evaluation table just as well as for a run file (the `scoreddocs` on the
command line below is ignored with `--raw`; any entity name works):

```bash
# a single run file or summary, written byte-for-byte to --out (or stdout)
ir_datasets export trec-browser:trec28/decision/input.ICTNETv1BM25.gz scoreddocs --raw --out input.ICTNETv1BM25.gz
ir_datasets export trec-browser:trec28/decision/summary.trec_eval.ICTNETv1BM25 scoreddocs --raw

# a whole track/subtrack's run files and summaries, raw, one per file
ir_datasets export 'trec-browser:trec28/decision/*' scoreddocs --out trec28-decision-raw/ --raw
```

With `--raw`, entity filtering doesn't apply (raw bytes aren't tied to one
entity), each file keeps its own literal filename (not `<run_id>.<format>`),
and `--format`/`--fields`/`--runtag` are accepted but unused. `--raw` is
only supported for v2 (`PROVIDER:name`) dataset names.

## Auditing downloads with `ir_datasets render`

Every real download of any v2 `Resource` (trec-browser or otherwise) is
appended to a shared, generic audit log (`log_utils.log_download`, see the
v2 README) -- one JSON line per fetch, with a timestamp, the node's
qualified name, the actual md5 of what landed, and where on disk. `ir_datasets
render` turns that log into a single, self-contained HTML page: one card per
distinct file you've downloaded, its own declared hash/citation/license,
every time it was fetched, and -- importantly for trec-browser -- the run
Table (and, where applicable, Benchmark) it's part of, so you can see which
dataset a run file belongs to, not just its bare filename:

```bash
ir_datasets render --out my-downloads/
```

`--out` is required and must point to a directory (created if it doesn't
already exist); the page is written as `my-downloads/index.html`.

The page is generic (nothing here is trec-browser-specific either), styled
after [ir-datasets.com](https://github.com/ir-datasets/ir-datasets.com)'s own
card layout and per-type colors -- Resource/Table/Benchmark/Suite -- without
depending on that project's code (a Flask app backed by a materialized RDF
store); this is one static file built directly from `ir_datasets.v2`'s live
graph.

**How to obtain the username/password:** this generic account is not
published on the TREC Browser site itself. Ask for it:

* from the organizers of the specific shared task/track whose data you want
  (most TREC track web pages list organizer contacts); or
* in the TREC community's own Slack, the `#trec` channels on
  [`acmsigir.slack.com`](https://acmsigir.slack.com) (the SIGIR Slack).

Anyone active in a current or past TREC track, or the organizers
themselves, can share it with you -- it is the same account NIST asks every
participant to use.

## The live browser vs. this entry point

The static browser pages (`pages.nist.gov/trec-browser/...` -- overview,
data, results, runs, participants, proceedings, one set per track/year) are
public and unrelated to the credentials above; only the actual files they
link to, served from `trec.nist.gov/results/`, are gated. This module only
addresses those linked files, not the browser pages themselves.

## Is there a metadata API? (which runs exist, by which team, deep-linked)

**No public one.** The TREC Browser's own source
([`usnistgov/trec-browser`](https://github.com/usnistgov/trec-browser)) does
contain a small REST API (`api/`, endpoints under `/trec/api/v1/`) in
addition to the static-site builder (`browser/`) -- but it is not publicly
hosted. It requires `docker compose up`-ing it yourself, against a private
SQLite database obtained from NIST under the same access gate as the result
files themselves (not something `ir_datasets` can fetch on a user's behalf).

A metadata export (every run, its team, track, year, and a deep link into
the browser) is still possible **without** an API or the gated results
files: the static browser pages that list a track's runs
(`.../<track>/<subtrack-plural>/runs/`) are public, and each run's metadata
(run id, participant/team, track, year, submission date, type, md5, run
description) is already printed on those pages next to its deep link. A
small scraper over that public HTML (one page per track/subtrack) can
produce the same export an API would, just one HTTP round-trip per listing
page instead of one API call -- see `trec_browser_provider/export.py`,
whose output is exactly the static index this provider ships and resolves
against (see above).

## Citation

Timo Breuer, Ellen M. Voorhees, and Ian Soboroff. 2024. Browsing and
Searching Metadata of TREC. In *Proceedings of the 2024 ACM SIGIR
International Conference on Theory of Information Retrieval (ICTIR '24)*,
313-323. <https://dl.acm.org/doi/abs/10.1145/3626772.3657873>
