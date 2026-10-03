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

These three cover every example the module was asked for; everything else
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
from ir_datasets.v2.nodes import DERIVED_FROM, RESOURCE, Resource, TABLE, source_resources
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


def _run(track, subtrack, run_id, record=None):
    name = _path(track, subtrack, f'input.{run_id}.gz')
    # The backing raw Resource gets its own, distinct *name* (`.raw` suffix)
    # but the *same* real URL: the user-facing name (`name`, the literal
    # results/ path) belongs to the parsed TrecScoredDocs table below, not to
    # the bytes it's parsed from -- reusing one name for both would make the
    # table's own `derived_from` edge (added by Table.__init__ via
    # source_resources(source)) register a second, different node under the
    # same qualified name, silently clobbering whichever of the two was
    # registered second (see registry.ManifestProvider._register_one).
    resource_kwargs = {}
    extra_meta = {}
    if record:
        if record.get('md5'):
            resource_kwargs['hash'] = f"md5:{record['md5']}"
        for key in ('participant', 'track', 'year', 'submission', 'type',
                    'deep_link', 'ir_datasets_ids'):
            if record.get(key):
                extra_meta[key] = record[key]
    resource = _gated_resource(f'{name}.raw', url_path=name, **resource_kwargs)
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


def _summary(track, subtrack, kind, run_id, record=None):
    name = _path(track, subtrack, f'summary.{kind}.{run_id}')
    label = 'standard' if kind == 'trec_eval' else 'extended'
    desc = (f'The {label} trec_eval summary for run {run_id!r}, submitted '
            f'to {track}/{subtrack} -- parsed as trec_eval `measure qid '
            'value` rows.')
    # The backing raw Resource gets its own, distinct *name* (`.raw` suffix,
    # same reasoning as `_run`'s) since the user-facing name belongs to the
    # parsed TrecEval table below, not to the bytes it's parsed from.
    resource_kwargs = {}
    extra_meta = {}
    if record:
        if record.get('md5'):
            resource_kwargs['hash'] = f"md5:{record['md5']}"
        for key in ('participant', 'track', 'year', 'submission', 'type',
                    'deep_link', 'ir_datasets_ids'):
            if record.get(key):
                extra_meta[key] = record[key]
        if record.get('description'):
            desc += f" {record['description']}"
    resource = _gated_resource(f'{name}.raw', url_path=name, **resource_kwargs)
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
    for key in ('participant', 'track', 'year', 'submission', 'type',
                'deep_link', 'ir_datasets_ids'):
        if record.get(key):
            row[key] = record[key]
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

# Catch-all: anything else under results/ (appendices, proceedings, ...);
# registered last so every more specific shape above always wins first --
# see base.Generator/registry.ManifestProvider.__getitem__'s first-match-wins
# iteration.
trec_browser.register_generator(Generator(
    '{path}', params={'path': Param(pattern=r'.+')},
    type=RESOURCE, resolver=_generic, enumerable=False,
    desc='Any other file linked from the TREC Browser, under '
        'trec.nist.gov/results/.'))
