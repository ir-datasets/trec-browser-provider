"""``trec-browser:`` -- run files and ``trec_eval`` summaries linked from the
TREC Browser (https://pages.nist.gov/trec-browser/), NIST's static site for
browsing/searching the metadata of every run ever submitted to TREC.

The browser itself (the "overview"/"runs"/"participants" pages under
``pages.nist.gov/trec-browser/<track>/<subtrack>/...``) is public, and is
*not* what this module addresses -- it has no stable per-run URL worth a v2
name, and (per the TREC Browser paper, see ``CITATION`` below) its own
`search`/listing API is not publicly hosted (self-host only, gated behind the
same private database described below). What this module *does* address is
the actual run/summary files those pages link to, served from
``trec.nist.gov/results/...`` and gated behind one shared HTTP Basic Auth
account -- the same "generic" account used across the TREC community (not a
personal one): see ``docs/trec-browser.md`` for how to obtain it.

One Generator per filename shape NIST uses under ``results/<track>/
<subtrack>/``, observed directly off the live browser's own run-listing pages
(e.g. ``https://pages.nist.gov/trec-browser/trec28/decisions/runs/``):

* ``input.<run_id>.gz``      -- the run file itself: a gzip'd TREC-format
  ranking (``query_id iter doc_id rank score run_tag``). Resolves directly to
  a parsed ``TrecScoredDocs`` table (``RunTable``), not a bare ``Resource`` --
  this is the "run.txt file that can be loaded and parsed" shape the module
  was asked for, so loading it is already useful, not just a byte stream.
* ``summary.trec_eval.<run_id>``   -- the plain-text ``trec_eval`` output for
  that run, standard measures only.
* ``summary.extended.<run_id>``    -- same, NIST's extended measure set.

A fourth, synthetic shape -- ``<track>/<subtrack>`` (just the two directory
segments, no filename) -- resolves to a ``Benchmark`` node: every run/summary
sharing that ``track``/``subtrack`` was submitted to the same TREC task, so
they belong to the same evaluable benchmark, not separate ones. This name
never collides with a real NIST file (every actual ``results/`` path has a
filename too, i.e. a third segment). The corpus the track was run against
(a ``docs`` facet) is filled in *when possible*: the TREC Browser's own
``.../data/`` pages sometimes cross-reference the ``ir_datasets`` id(s) of
that corpus (see ``export.py``'s own docstring, and the ``ir_datasets_ids``
scraped field) -- resolved lazily, the same ``ir_datasets.load(...)`` id a
caller would use, and left unset (not an error) whenever that cross-reference
is missing, ambiguous (more than one distinct corpus listed), or simply not
(yet) known to this branch of ``ir_datasets.v2``.

These four cover every example the module was asked for; everything else
under ``results/`` (appendices, proceedings PDFs, ...) still resolves, via a
catch-all generator registered last (first-match-wins order -- see
``base.Generator``/``registry.ManifestProvider.__getitem__``), to a bare
``Resource`` wrapping the same gated URL, unparsed.

Names are the literal path under ``trec.nist.gov/results/`` (track/subtrack/
filename, three ``/``-segments) -- not this package's usual hyphen-flattened
convention (see ``clirmatrix.py``'s own module docstring on why *that* family
flattens): a name here is already how NIST's own site addresses the file, and
inventing a different one would make it harder, not easier, to go from a
browser page to a v2 name and back. A bare ``Node.name`` may contain ``/``,
just not ``:`` (``base.Node.__init__``) -- the same thing ``hf:owner/repo``
already relies on.

No manifest/package (nothing to bootstrap-import or freeze -- see
``clirmatrix_provider.py``'s own docstring on the same point): this family is
exactly as open-ended as ``hf:``'s or ``clirmatrix:``'s, except the *set* of
valid names isn't even knowable ahead of time the way CLIRMatrix's finite
parameter domains are (TREC adds tracks and runs every year), so every
generator below is ``enumerable=False``.

This module lives in the ``trec-browser-provider`` package (a third-party
``ir_datasets.v2`` provider, not part of ``ir_datasets`` itself) -- see
``provider.py``'s own docstring for how it's wired in via the
``ir_datasets.providers`` entry-point group.
"""
import base64
import collections
import contextlib
import gzip
import hashlib
import io
import json
import os

from ir_datasets import log as _ir_log
from ir_datasets.util.download import Download, RequestsDownload

from ir_datasets.v2.base import Edge, Generator, Node, Param
from ir_datasets.v2.formats import TrecScoredDocs
from ir_datasets.v2.graph import default_graph
from ir_datasets.v2.nodes import (
    Benchmark, DERIVED_FROM, RESOURCE, Resource, TABLE, source_resources)
from ir_datasets.v2.sources import Source

from .export import DEFAULT_INDEX_PATH
from .provider import trec_browser

_logger = _ir_log.easy()

#: Breuer, Voorhees, Soboroff. "Browsing and Searching Metadata of TREC."
#: ICTIR '24 (co-located with SIGIR 2024), pp. 313-323.
CITATION = 'doi:10.1145/3626772.3657873'

#: A trec_eval summary table, as its own node type -- a local stand-in for
#: ``ir_datasets.v2.nodes.EvaluationTable``/``ir_datasets.v2.formats.TrecEval``,
#: which existed in an earlier snapshot of ir_datasets' v2 branch this module
#: was first written against. That branch is still unstable (a single,
#: repeatedly force-pushed WIP commit, with no history to recover an older
#: implementation from -- see ``ir_datasets``'s own git log), and as of the
#: snapshot this module currently installs against, the "evaluation" concept
#: has been dropped there entirely, with nothing to replace it. Declaring our
#: own node type here (``parent=TABLE``, as the module-level docstring on
#: ``registry.ManifestProvider.node_type`` explicitly invites a third-party
#: provider to do) keeps this provider working without waiting on upstream to
#: restore (or permanently settle on dropping) that concept.
EVALUATION_TABLE = trec_browser.node_type(
    'EvaluationTable', parent=TABLE,
    desc='A Table representing a trec_eval summary for one run: one row per '
         '(measure, query_id) pair, plus an "all" row aggregating across '
         'queries.')


class TrecEvalMeasure(collections.namedtuple('TrecEvalMeasure', ['measure', 'query_id', 'value'])):
    """One row of a ``trec_eval`` summary: a measure name, the query_id it was
    computed for (or ``'all'`` for the aggregate row), and its value (left as
    ``str`` -- trec_eval itself prints some measures, e.g. ``num_ret``, as
    integers and others as floats, and this module has no need to pick a
    single numeric type apart from what the text already says)."""
    __slots__ = ()


class TrecEval(Node):
    """Parsed ``trec_eval`` summary output: whitespace-separated ``measure
    query_id value`` rows, one per line (trec_eval's own plain-text summary
    format, for both the standard and NIST's extended measure sets -- see
    ``_summary`` below, this class's only caller)."""
    type = EVALUATION_TABLE

    def __init__(self, name, *, source, **meta):
        self.source = source
        self._structural = [Edge(DERIVED_FROM, r) for r in source_resources(source)]
        super().__init__(name, **meta)

    def structural_edges(self):
        return list(self._structural)

    def __iter__(self):
        with self.source.stream() as stream:
            for raw_line in stream:
                line = raw_line.decode() if isinstance(raw_line, bytes) else raw_line
                parts = line.split()
                if len(parts) >= 3:
                    yield TrecEvalMeasure(parts[0], parts[1], parts[2])

#: Where NIST serves the actual (gated) run/summary files -- the browser
#: site itself (pages.nist.gov/trec-browser/...) only ever links here.
_RESULTS_BASE_URL = 'https://trec.nist.gov/results/'

#: Read at Source-build time (see ``_EnvBasicAuthSource`` below), not at
#: import time: a process that never actually downloads one of these files
#: (e.g. just listing/inspecting the graph) should not need the credentials
#: set at all.
USERNAME_ENVVAR = 'IRDS_TREC_BROWSER_USERNAME'
PASSWORD_ENVVAR = 'IRDS_TREC_BROWSER_PASSWORD'

#: One shared ("generic") account, the same for every track/run -- not a
#: personal NIST account. See docs/trec-browser.md for how to request it.
DUA = (
    'Downloading from trec.nist.gov/results/ requires a username/password '
    f'(HTTP Basic Auth), read from the {USERNAME_ENVVAR}/{PASSWORD_ENVVAR} '
    'environment variables. This is one shared, generic account used across '
    'the TREC community (not a personal one) -- obtain it from a shared '
    'task\'s organizers, or ask in the TREC community Slack '
    '(acmsigir.slack.com). See docs/trec-browser.md.'
)


def _missing_credentials():
    return RuntimeError(
        f'{USERNAME_ENVVAR} and {PASSWORD_ENVVAR} must both be set to '
        f'download trec-browser: resources. {DUA}')


def _basic_auth_header():
    username = os.environ.get(USERNAME_ENVVAR)
    password = os.environ.get(PASSWORD_ENVVAR)
    if not username or not password:
        raise _missing_credentials()
    token = base64.b64encode(f'{username}:{password}'.encode('utf8')).decode('ascii')
    return {'Authorization': f'Basic {token}'}


class _EnvBasicAuthSource(Source):
    """A ``Source`` behind the shared trec.nist.gov/results/ account, read
    from ``USERNAME_ENVVAR``/``PASSWORD_ENVVAR`` at download time (not v1's
    ``auth=`` key, which names a file under ``~/.ir_datasets/auth/`` or
    prompts interactively -- neither fits an env-var-only credential). The
    header is built fresh on every ``_build()`` (not cached at construction),
    so a credential set *after* the graph is imported (a common pattern: set
    the env vars right before the one script that actually downloads) still
    works, and nothing with the real token is ever kept on the node itself.
    """

    def _build(self, file):
        return RequestsDownload(self.url, headers=_basic_auth_header())

    def describe(self, order):
        # Unlike a plain `headers=` source (see Source.describe's own
        # docstring: "plain headers ... are not credentials"), this one's
        # header *is* one -- flag it accordingly, the same as `auth=`/
        # `cookies=` would, without ever including the header's own value.
        d = super().describe(order)
        d['auth'] = True
        return d


def _path(track, subtrack, filename):
    return f'{track}/{subtrack}/{filename}'


class _SoftHashStream(io.RawIOBase):
    """Like ``ir_datasets.util.HashStream``, but a mismatch is only logged
    (``_logger.warn``), never raised -- see ``_SoftHashResource``."""

    def __init__(self, stream, expected, algo='md5'):
        super().__init__()
        self._stream = stream
        self._hasher = hashlib.new(algo)
        self._expected = expected
        self._algo = algo
        self._done = False

    def readable(self):
        return True

    def readinto(self, b):
        count = self._stream.readinto(b)
        self._hasher.update(b[:count])
        if count == 0 and not self._done:
            self._done = True
            digest = self._hasher.hexdigest().lower()
            if self._expected and digest != self._expected.lower():
                _logger.warn(
                    f'{self._algo} mismatch for a trec-browser: resource '
                    f'(expected {self._expected}, got {digest}) -- TREC '
                    'Browser-scraped MD5s are a hint lifted off an HTML '
                    'listing page, not computed from the file itself, so '
                    'this is not treated as a hard failure. See '
                    'docs/trec-browser.md.')
        return count


def _soft_verified(stream, hashes):
    for algo, expected in hashes.items():
        stream = _SoftHashStream(stream, expected, algo)
    return stream


class _SoftHashResource(Resource):
    """A ``Resource`` whose declared ``hash=`` is informational only, not
    enforced: unlike every other Resource in ir_datasets (whose hash is an
    author-verified guarantee, hard-checked by the v1 ``Download``/
    ``HashVerifier`` machinery), a TREC Browser MD5 is scraped off an HTML
    listing page -- NIST's own disk copy can legitimately drift from what
    was last scraped (a withdrawn/resubmitted run, a stale index) without
    the file actually being corrupt. The hash is still reported as metadata
    (``discovery_literals``'s ``validation``, same shape a real Resource
    would report) and still checked as bytes are read -- just as a warning,
    never an exception that aborts the download.

    Implemented by never forwarding ``self.hashes``/``self.md5`` into the
    v1 ``Download`` this builds (``sources.build_download`` is what wires a
    Resource's declared hash into that hard check) and instead verifying,
    warn-only, in ``stream()`` itself -- the download always completes.
    """

    @property
    def download(self):
        if self._download is None:
            downloads = [source._build(self) for source in self.sources]
            self._download = Download(
                downloads,
                cache_path=str(self.cache_path) if self.cache_path else None,
                dua=self.dua, size_hint=self.size)
        return self._download

    @contextlib.contextmanager
    def stream(self):
        with super().stream() as f:
            yield _soft_verified(f, self.hashes) if self.hashes else f


def _gated_resource(name, url_path=None, **meta):
    """A Resource named ``name``, at ``_RESULTS_BASE_URL`` + ``url_path``
    (``name`` itself, if ``url_path`` is omitted), behind the shared account
    (see ``_EnvBasicAuthSource``). A ``_SoftHashResource``, not a plain
    ``Resource`` (see its own docstring): any ``hash=`` passed in ``meta``
    is a scraped, best-effort hint, not an enforced guarantee."""
    url = _RESULTS_BASE_URL + (url_path if url_path is not None else name)
    return _SoftHashResource(name, sources=[_EnvBasicAuthSource(url)], dua=DUA,
                              citation=CITATION, **meta)


#: Scraped record keys copied onto a run/summary node's own ``metadata``,
#: mapped to the metadata key they're stored under. ``type`` (the scraped
#: submission type, e.g. "automatic"/"manual") is deliberately renamed to
#: ``submission_type``: ``Node.metadata``'s ``type`` key is reserved by
#: ``ir_datasets.v2`` itself for the node's own graph type (see
#: ``freeze.row_for``/``Generator.enumerate_rows``, both of which build a row
#: as ``{'type': node.type, **node.metadata}`` / ``{'type': ..., **row_metadata(...)}``);
#: keeping the scraped field under the same ``type`` key would silently
#: overwrite the node's real graph type with the scraped value whenever a
#: record has one.
_SCRAPED_METADATA_KEYS = {
    'participant': 'participant', 'track': 'track', 'year': 'year',
    'submission': 'submission', 'type': 'submission_type',
    'deep_link': 'deep_link', 'ir_datasets_ids': 'ir_datasets_ids'}


def _scraped_metadata(record):
    """``record``'s scraped fields, keyed for ``Node.metadata`` (see
    ``_SCRAPED_METADATA_KEYS``'s docstring for why ``type`` is renamed)."""
    return {meta_key: record[key]
            for key, meta_key in _SCRAPED_METADATA_KEYS.items()
            if record.get(key)}


def _run_resource(track, subtrack, run_id, record=None):
    """The raw gated Resource a run's own parsed ``TrecScoredDocs`` table
    (``_run``, below) is built from -- factored out so the dedicated
    ``...input.{run_id}.gz.raw`` generators (registered below, alongside
    ``_run``'s own) can build/resolve *exactly* the same, real-MD5-bearing
    Resource a direct name lookup expects, instead of silently falling
    through to the generic catch-all's hash-less placeholder (see those
    generators' own docstring for why that fallback would otherwise win)."""
    name = _path(track, subtrack, f'input.{run_id}.gz')
    resource_kwargs = {}
    if record and record.get('md5'):
        resource_kwargs['hash'] = f"md5:{record['md5']}"
    # The resource gets its own, distinct *name* (`.raw` suffix) but the
    # *same* real URL: the user-facing name (`name`, the literal results/
    # path) belongs to the parsed TrecScoredDocs table, not to the bytes
    # it's parsed from -- reusing one name for both would make the table's
    # own `derived_from` edge (added by Table.__init__ via
    # source_resources(source)) register a second, different node under the
    # same qualified name, silently clobbering whichever of the two was
    # registered second (see registry.ManifestProvider._register_one).
    return _gated_resource(f'{name}.raw', url_path=name, **resource_kwargs)


def _run(track, subtrack, run_id, record=None):
    name = _path(track, subtrack, f'input.{run_id}.gz')
    resource = _run_resource(track, subtrack, run_id, record)
    extra_meta = _scraped_metadata(record) if record else {}
    desc = (f'Run {run_id!r}, submitted to {track}/{subtrack} -- '
            'downloaded and parsed from the TREC Browser-linked run file at '
            f'{_RESULTS_BASE_URL}{name}.')
    if record and record.get('participant'):
        desc += f" Submitted by {record['participant']}."
    if record and record.get('description'):
        desc += f" {record['description']}"
    table_kwargs = {'metadata': extra_meta} if extra_meta else {}
    return TrecScoredDocs(
        name, source=resource.gunzip(), desc=desc, citation=CITATION,
        **table_kwargs)


def _summary_resource(track, subtrack, kind, run_id, record=None):
    """The raw gated Resource a summary's own parsed ``TrecEval`` table
    (``_summary``, below) is built from -- same reasoning/role as
    ``_run_resource``'s own docstring."""
    name = _path(track, subtrack, f'summary.{kind}.{run_id}')
    resource_kwargs = {}
    if record and record.get('md5'):
        resource_kwargs['hash'] = f"md5:{record['md5']}"
    return _gated_resource(f'{name}.raw', url_path=name, **resource_kwargs)


def _summary(track, subtrack, kind, run_id, record=None):
    name = _path(track, subtrack, f'summary.{kind}.{run_id}')
    label = 'standard' if kind == 'trec_eval' else 'extended'
    desc = (f'The {label} trec_eval summary for run {run_id!r}, submitted '
            f'to {track}/{subtrack} -- parsed as trec_eval `measure qid '
            'value` rows.')
    resource = _summary_resource(track, subtrack, kind, run_id, record)
    extra_meta = _scraped_metadata(record) if record else {}
    if record and record.get('description'):
        desc += f" {record['description']}"
    table_kwargs = {'metadata': extra_meta} if extra_meta else {}
    return TrecEval(name, source=resource, desc=desc, citation=CITATION,
                     **table_kwargs)


def _generic(path):
    return _gated_resource(
        path,
        desc='A TREC Browser-linked file under trec.nist.gov/results/, with '
            'no more specific shape this module recognizes (an appendix, a '
            'proceedings PDF, ...); use the live browser '
            '(https://pages.nist.gov/trec-browser/) to see what this is.')


#: track/subtrack as they appear in a results/ path -- permissive (alphanumerics
#: plus the handful of punctuation NIST actually uses), but deliberately not
#: `.+`: both must stop at the next `/`, or e.g. `input.X.gz` could itself be
#: swallowed into `subtrack`.
_SEGMENT = Param(pattern=r'[^/]+')
#: A run id never contains `.` or `/` in any observed NIST path (both are the
#: filename's own separators) -- excluding them keeps `input.{run_id}.gz`
#: from, say, matching a run id that itself ends in `.gz`. Whitespace is
#: excluded too: a node name must never contain it (see
#: export.py's ``_run_id_from_links`` for where a run id
#: actually comes from -- the hosted, already-URL-safe filename, never a
#: submitting team's free-text "Run ID" display field, which NIST does not
#: sanitize and can contain literal spaces).
_RUN_ID = Param(pattern=r'[^./\s]+')


def _load_static_index(path=DEFAULT_INDEX_PATH):
    """The package-shipped ``ir_datasets/etc/trec_browser_runs.json.gz`` --
    every run NIST's own ``runs/`` listing pages linked as of the last
    ``python -m trec_browser_provider.export`` regeneration, keyed by
    ``(results_track, results_subtrack, run_id)``. Missing/corrupt (e.g. a
    stripped-down source tree) degrades to "no known runs", not an error --
    the pattern-based generators below still cover every name dynamically.
    """
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as fh:
            rows = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {(r['results_track'], r['results_subtrack'], r['run_id']): r for r in rows}


_STATIC_INDEX = _load_static_index()
#: `'{track}/{subtrack}/input.{run_id}.gz'` -> `(track, subtrack, run_id)`,
#: one entry per statically-known run -- the single flat Param below is keyed
#: on these full paths (not separate track/subtrack/run_id Params) because
#: `Generator.enumerate_params()` requires every Param to carry a closed
#: `values=` set, and a 3-Param cross product over ~33 tracks x ~100
#: subtracks x ~15k run ids would be tens of millions of (mostly invalid)
#: combinations -- TREC run ids aren't shared across tracks the way, say,
#: CLIRMatrix's language pairs are.
_RUN_PATHS = {
    _path(track, subtrack, f'input.{run_id}.gz'): (track, subtrack, run_id)
    for (track, subtrack, run_id) in _STATIC_INDEX}
_SUMMARY_PATHS = {
    _kind: {
        _path(track, subtrack, f'summary.{_kind}.{run_id}'): (track, subtrack, run_id)
        for (track, subtrack, run_id) in _STATIC_INDEX}
    for _kind in ('trec_eval', 'extended')}

#: `(track, subtrack)` -> the `ir_datasets_ids` scraped for that pair (see
#: export.py's `build_dataset_index`/`trimmed_rows`), one entry per pair that
#: actually has a cross-reference -- taken from whichever record happens to
#: carry it first; every run sharing a `(track, subtrack)` was scraped with
#: the same value (it comes from that pair's own `.../data/` page, not the
#: individual run), so it makes no difference which record "wins".
_BENCHMARK_IDS = {}
for (_track, _subtrack, _run_id), _record in _STATIC_INDEX.items():
    _key = (_track, _subtrack)
    if _key not in _BENCHMARK_IDS and _record.get('ir_datasets_ids'):
        _BENCHMARK_IDS[_key] = list(_record['ir_datasets_ids'])

#: `'{track}/{subtrack}'` -> `(track, subtrack)`, one entry per pair with at
#: least one statically-known run -- same flat-Param reasoning as
#: `_RUN_PATHS`'s own docstring (a 2-Param cross product here would be much
#: smaller, but still needlessly produces `(track, subtrack)` combinations
#: that were never actually submitted to).
_BENCHMARK_PATHS = {
    f'{track}/{subtrack}': (track, subtrack)
    for (track, subtrack) in sorted({(t, s) for (t, s, _) in _STATIC_INDEX})}


def _resolve_corpus_docs(dataset_id):
    """The corpus ``dataset_id`` (an ``ir_datasets`` id, as scraped into
    ``ir_datasets_ids`` -- see export.py) resolves to, as a ``DocTable`` --
    best-effort, never raising: ``dataset_id`` is a bare name, resolved the
    same way ``default_graph()`` resolves any other bare name (``irds:``
    first, then ``legacy:`` -- see ``graph.Graph._load_bare``), which is
    exactly how ``ir_datasets.load(dataset_id)`` would address it too. Returns
    ``None`` (not an error) if this branch of ``ir_datasets.v2`` doesn't (yet)
    know that id, or knows it but it has no ``docs`` facet of its own --
    the same "extracted when possible" spirit ``ir_datasets_ids`` itself
    already has."""
    try:
        node = default_graph()[dataset_id]
    except KeyError:
        return None
    return getattr(node, 'docs', None)


def _benchmark_corpus_docs(track, subtrack):
    """The single corpus ``track``/``subtrack``'s runs were evaluated
    against, if every ``ir_datasets_ids`` cross-reference for that pair
    resolves to the *same* ``docs`` table -- ``None`` if there is no
    cross-reference at all, none of them resolve, or they resolve to more
    than one distinct corpus (ambiguous: picking one over the others would
    just be a guess)."""
    ids = _BENCHMARK_IDS.get((track, subtrack))
    if not ids:
        return None
    resolved = {}
    for dataset_id in ids:
        docs = _resolve_corpus_docs(dataset_id)
        if docs is not None:
            resolved[docs.name] = docs
    if len(resolved) == 1:
        return next(iter(resolved.values()))
    return None


def _benchmark(track, subtrack):
    name = f'{track}/{subtrack}'
    ids = _BENCHMARK_IDS.get((track, subtrack))
    desc = (f'Every run/summary submitted to {track}/{subtrack}, as linked '
            'from the TREC Browser, bundled into one evaluable benchmark.')
    return Benchmark(
        name, docs=_benchmark_corpus_docs(track, subtrack), desc=desc,
        citation=CITATION, metadata={'ir_datasets_ids': ids} if ids else {})


def _resolve_known_benchmark(benchmark_path):
    track, subtrack = _BENCHMARK_PATHS[benchmark_path]
    return _benchmark(track, subtrack)


def _known_benchmark_row(benchmark_path):
    track, subtrack = _BENCHMARK_PATHS[benchmark_path]
    ids = _BENCHMARK_IDS.get((track, subtrack))
    return {'ir_datasets_ids': ids} if ids else {}


def _resolve_known_run(run_path):
    track, subtrack, run_id = _RUN_PATHS[run_path]
    return _run(track, subtrack, run_id, record=_STATIC_INDEX[(track, subtrack, run_id)])


def _known_run_row(run_path):
    # Cheap: a dict lookup plus a few key renames, not the full resolver
    # (building the TrecScoredDocs/Resource chain) -- this runs once per
    # known run on every `discover`/`freeze`, not just on access.
    track, subtrack, run_id = _RUN_PATHS[run_path]
    record = _STATIC_INDEX[(track, subtrack, run_id)]
    row = {}
    if record.get('md5'):
        row['validation'] = {'type': 'file_hash', 'hashes': [f"md5:{record['md5']}"]}
    row.update(_scraped_metadata(record))
    return row


def _resolve_known_summary(kind, summary_path):
    track, subtrack, run_id = _SUMMARY_PATHS[kind][summary_path]
    return _summary(track, subtrack, kind, run_id,
                    record=_STATIC_INDEX[(track, subtrack, run_id)])


def _known_summary_row(kind, summary_path):
    track, subtrack, run_id = _SUMMARY_PATHS[kind][summary_path]
    record = _STATIC_INDEX[(track, subtrack, run_id)]
    row = {}
    for key in ('participant', 'track', 'year', 'deep_link', 'ir_datasets_ids'):
        if record.get(key):
            row[key] = record[key]
    return row


#: ``{run_path}``/``{summary_path}`` + literal ``.raw`` -- the name a run's
#: or summary's own backing Resource is registered under (see
#: ``_run_resource``/``_summary_resource``'s own docstrings), reused here so
#: that name also resolves correctly -- real MD5, no generic placeholder --
#: when looked up *directly*, not just as a side effect of resolving the
#: parsed table first. Without a dedicated generator for it, a bare
#: ``{path}`` lookup of e.g. ``track/subtrack/input.run_id.gz.raw`` would
#: fall through every more specific generator below (none of their patterns
#: end in ``.gz``/``summary.*`` *plus* ``.raw``) straight to the final
#: catch-all -- which has no way to know the real MD5/scraped metadata, and
#: would silently hand back a bare, unverified Resource instead. The
#: enumerable Param below reuses ``_RUN_PATHS``/``_SUMMARY_PATHS``' own
#: values (the bare, no-``.raw`` path) since that's what the generator's
#: ``run_path``/``summary_path`` param is bound to -- the literal ``.raw``
#: suffix lives in the template, not the enumerated values themselves.


def _resolve_known_run_raw(run_path):
    track, subtrack, run_id = _RUN_PATHS[run_path]
    return _run_resource(track, subtrack, run_id,
                         record=_STATIC_INDEX[(track, subtrack, run_id)])


def _known_run_raw_row(run_path):
    track, subtrack, run_id = _RUN_PATHS[run_path]
    record = _STATIC_INDEX[(track, subtrack, run_id)]
    if not record.get('md5'):
        return {}
    return {'validation': {'type': 'file_hash',
                           'hashes': [f"md5:{record['md5']}"]}}


def _resolve_known_summary_raw(kind, summary_path):
    track, subtrack, run_id = _SUMMARY_PATHS[kind][summary_path]
    return _summary_resource(track, subtrack, kind, run_id,
                             record=_STATIC_INDEX[(track, subtrack, run_id)])


def _known_summary_raw_row(kind, summary_path):
    track, subtrack, run_id = _SUMMARY_PATHS[kind][summary_path]
    record = _STATIC_INDEX[(track, subtrack, run_id)]
    if not record.get('md5'):
        return {}
    return {'validation': {'type': 'file_hash',
                           'hashes': [f"md5:{record['md5']}"]}}


if _RUN_PATHS:
    # Index-backed, enumerable: real metadata (MD5, participant, a deep link
    # back to the live browser page) for every run known as of the last
    # export, registered first so it always wins over the dynamic shape
    # below for names it actually knows about.
    trec_browser.register_generator(Generator(
        '{run_path}', params={'run_path': Param(values=tuple(sorted(_RUN_PATHS)))},
        type=TrecScoredDocs.type, resolver=_resolve_known_run, enumerable=True,
        row_metadata=_known_run_row,
        desc='A run submitted to TREC, as linked from the TREC Browser, '
            'resolved against the package-shipped index (real MD5, '
            'participant, deep link) -- see '
            'ir_datasets/v2/export.py.'))
    for _kind in ('trec_eval', 'extended'):
        trec_browser.register_generator(Generator(
            '{summary_path}',
            params={'summary_path': Param(values=tuple(sorted(_SUMMARY_PATHS[_kind])))},
            type=EVALUATION_TABLE,
            resolver=lambda summary_path, _kind=_kind: _resolve_known_summary(_kind, summary_path),
            enumerable=True,
            row_metadata=lambda summary_path, _kind=_kind: _known_summary_row(_kind, summary_path),
            desc=f'The {_kind} trec_eval summary for a run known to the '
                'package-shipped index.'))
    # One Benchmark per `(track, subtrack)` known to the index -- every run/
    # summary registered above shares one of these names (see the module
    # docstring's own note on this shape). Registered here too (alongside
    # the tables above, before the dynamic fallbacks/catch-all) so it always
    # wins with real `ir_datasets_ids`/`docs` metadata when available.
    trec_browser.register_generator(Generator(
        '{benchmark_path}',
        params={'benchmark_path': Param(values=tuple(sorted(_BENCHMARK_PATHS)))},
        type=Benchmark.type, resolver=_resolve_known_benchmark, enumerable=True,
        row_metadata=_known_benchmark_row,
        desc='Every run/summary submitted to one TREC track/subtrack known '
            'to the package-shipped index, bundled into one evaluable '
            'Benchmark -- its `docs` facet is the corpus it was run '
            'against, resolved from a scraped `ir_datasets_ids` '
            'cross-reference when exactly one distinct corpus is '
            'identifiable.'))
    # The ``.raw`` Resource backing each table above (see
    # ``_run_resource``/``_summary_resource``'s own docstrings) -- registered
    # too, so looking that name up *directly* (not just as a side effect of
    # resolving the parsed table first) still gets the real MD5, instead of
    # silently falling through to the generic catch-all below.
    trec_browser.register_generator(Generator(
        '{run_path}.raw',
        params={'run_path': Param(values=tuple(sorted(_RUN_PATHS)))},
        type=RESOURCE, resolver=_resolve_known_run_raw, enumerable=True,
        row_metadata=_known_run_raw_row,
        desc='The raw, unparsed run file a known run\'s TrecScoredDocs '
            'table is parsed from.'))
    for _kind in ('trec_eval', 'extended'):
        trec_browser.register_generator(Generator(
            '{summary_path}.raw',
            params={'summary_path': Param(values=tuple(sorted(_SUMMARY_PATHS[_kind])))},
            type=RESOURCE,
            resolver=lambda summary_path, _kind=_kind: _resolve_known_summary_raw(_kind, summary_path),
            enumerable=True,
            row_metadata=lambda summary_path, _kind=_kind: _known_summary_raw_row(_kind, summary_path),
            desc=f'The raw, unparsed {_kind} summary file a known '
                'summary\'s TrecEval table is parsed from.'))

# Dynamic fallback: the same three shapes, pattern-matched instead of listed
# -- covers any run/summary added to the live site since the index was last
# regenerated. Registered after the index-backed generators above (so a
# known run/summary always resolves with its real metadata first), but
# before the final catch-all.
trec_browser.register_generator(Generator(
    '{track}/{subtrack}/input.{run_id}.gz',
    params={'track': _SEGMENT, 'subtrack': _SEGMENT, 'run_id': _RUN_ID},
    type=TrecScoredDocs.type, resolver=_run, enumerable=False,
    desc='A run submitted to TREC, as linked from the TREC Browser, not (yet) '
        'in the package-shipped index -- resolves directly to a parsed '
        'scoreddocs table, without the extra metadata an indexed run gets.'))

for _kind in ('trec_eval', 'extended'):
    trec_browser.register_generator(Generator(
        '{track}/{subtrack}/summary.' + _kind + '.{run_id}',
        params={'track': _SEGMENT, 'subtrack': _SEGMENT, 'run_id': _RUN_ID},
        type=EVALUATION_TABLE, resolver=lambda track, subtrack, run_id, _kind=_kind:
            _summary(track, subtrack, _kind, run_id),
        enumerable=False,
        desc=f'The {_kind} trec_eval summary for a run, as linked from the '
            'TREC Browser.'))

# Same shape as the index-backed Benchmark generator above, pattern-matched
# instead of listed -- covers any `(track, subtrack)` not (yet) in the
# static index, same "every run belongs to a benchmark" guarantee, just
# without `ir_datasets_ids`/a `docs` facet to offer (nothing in the static
# index to resolve them from). Registered after the index-backed one (so a
# known track/subtrack always resolves with its real metadata first), but
# before the final catch-all -- and, since no real NIST file ever lives at
# a bare `track/subtrack` (every actual path has a filename too), this can
# never shadow an actual resource.
trec_browser.register_generator(Generator(
    '{track}/{subtrack}', params={'track': _SEGMENT, 'subtrack': _SEGMENT},
    type=Benchmark.type, resolver=_benchmark, enumerable=False,
    desc='Every run/summary submitted to one TREC track/subtrack not (yet) '
        'in the package-shipped index, bundled into one evaluable '
        'Benchmark, without the extra metadata an indexed track/subtrack '
        'gets.'))

# The ``.raw`` Resource backing each dynamic-shape table above -- same
# reasoning as the index-backed ``.raw`` generators above: without these,
# a direct lookup of e.g. `track/subtrack/input.run_id.gz.raw` for a run
# not in the static index would still fall through to the generic
# catch-all below, same bug, just without an MD5 to lose.
trec_browser.register_generator(Generator(
    '{track}/{subtrack}/input.{run_id}.gz.raw',
    params={'track': _SEGMENT, 'subtrack': _SEGMENT, 'run_id': _RUN_ID},
    type=RESOURCE, resolver=_run_resource, enumerable=False,
    desc='The raw, unparsed run file for a run not (yet) in the '
        'package-shipped index.'))

for _kind in ('trec_eval', 'extended'):
    trec_browser.register_generator(Generator(
        '{track}/{subtrack}/summary.' + _kind + '.{run_id}.raw',
        params={'track': _SEGMENT, 'subtrack': _SEGMENT, 'run_id': _RUN_ID},
        type=RESOURCE, resolver=lambda track, subtrack, run_id, _kind=_kind:
            _summary_resource(track, subtrack, _kind, run_id),
        enumerable=False,
        desc=f'The raw, unparsed {_kind} summary file for a run not (yet) '
            'in the package-shipped index.'))

# Catch-all: anything else under results/ (appendices, proceedings, ...);
# registered last so every more specific shape above always wins first --
# see base.Generator/registry.ManifestProvider.__getitem__'s first-match-wins
# iteration.
trec_browser.register_generator(Generator(
    '{path}', params={'path': Param(pattern=r'.+')},
    type=RESOURCE, resolver=_generic, enumerable=False,
    desc='Any other file linked from the TREC Browser, under '
        'trec.nist.gov/results/.'))
