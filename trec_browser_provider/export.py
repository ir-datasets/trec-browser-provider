"""Scrapes TREC Browser (https://pages.nist.gov/trec-browser/) run metadata
-- which runs exist, by which team, for which track/year, with a deep link
back into the browser, and (for the run file itself) its real MD5 -- and
turns it into the static index ``trec_browser_provider/etc/trec_browser_runs.json``
that ``dataset.py`` ships and reads at import time.

It also scrapes each track/subtrack's ``.../data/`` page (distinct from the
per-run ``.../runs/`` pages above) for the ``ir_datasets`` id(s) NIST
sometimes cross-references there -- e.g. ``trec29/deep/data/`` lists
``msmarco-passage-v2/trec-dl-2020``/``msmarco-document-v2/trec-dl-2020``;
``trec34/rag/data/`` lists none at all. Extracted "when possible": a
track/subtrack without this cross-reference simply ships without an
``ir_datasets_ids`` field, not an error (see ``parse_data_page``/
``build_dataset_index`` below, and ``dataset.py``'s own use of
the resulting field).

There is no public API for this (see ``docs/trec-browser.md``): the TREC
Browser's own REST API (github.com/usnistgov/trec-browser, ``api/``)
requires self-hosting against a private database obtained from NIST, under
the same access gate as the actual run files. This module instead scrapes
the *public* static browser pages, which already print every run's metadata
next to its deep link -- no credentials needed (unlike actually downloading
a run/summary file via the ``trec-browser:`` provider, see the same doc).

Regenerate the static index (a one-off maintenance task, same idea as
regenerating v1's ``etc/downloads.json``) with::

    python -m trec_browser_provider.export
    # writes trec_browser_provider/etc/trec_browser_runs.json.gz by default; --out to
    # change where, --limit for a quick smoke test.

New tracks/runs added to the live site after the index was last regenerated
still resolve: ``dataset.py`` also registers an open-ended,
non-enumerable catch-all generator for anything not in the static index (see
its own module docstring).
"""
import argparse
import gzip
import json
import pathlib
import re
import sys
import time
import urllib.parse

import ir_datasets

SITE = 'https://pages.nist.gov/trec-browser/'
SITEMAP = SITE + 'sitemap.xml'

#: The listing page's own labels -> this module's field names.
_FIELD_LABELS = {
    'Run ID': 'run_id', 'Participant': 'participant', 'Track': 'track',
    'Year': 'year', 'Submission': 'submission', 'Type': 'type',
    'MD5': 'md5', 'Run description': 'description',
}
#: The listing page's own link labels -> this module's link keys.
_LINK_LABELS = {
    'Results': 'results', 'Participants': 'participants',
    'Proceedings': 'proceedings', 'Input': 'input',
    'Summary (trec_eval)': 'summary_trec_eval',
    'Summary (extended)': 'summary_extended', 'Appendix': 'appendix',
}

#: Pulls `{track}/{subtrack}` out of a real trec.nist.gov/results/ URL (the
#: one shape every one of this module's `trec-browser:` node *names* uses --
#: see dataset.py). Preferred over the *browsing* page's own
#: URL (`.../trec28/decisions/runs/`): the nav path segment is sometimes
#: plural (`decisions`) where the actual results/ path is singular
#: (`decision`) -- the gated link is the one that must match exactly.
_RESULTS_PATH_RE = re.compile(r'^https://trec\.nist\.gov/results/([^/]+)/([^/]+)/')

#: Pulls the literal, already-URL-safe filename segment TREC itself hosts a
#: run's files under -- e.g. ``input.BLIP%20BLIP2%20...diffusion.gz`` ->
#: ``BLIP%20BLIP2%20...diffusion`` -- out of each of the three link kinds
#: that can carry it. This, never the free-text "Run ID:" bullet below, is
#: what `run_id` must come from: a submitting team's own typed "Run ID" is
#: never sanitized by NIST, and real submissions do contain literal spaces
#: (confirmed live, e.g. trec34/avs's "Fuse all sub-models"); using that
#: text to build a `trec-browser:` node name (`input.{run_id}.gz`) would put
#: raw whitespace into the node name itself -- broken as a graph identifier,
#: a URL path segment, a CLI argument, and an exported filename all at once.
#: The hosted filename usually already has whatever a browser would need
#: percent-encoded (a space, among other things) encoded in the href itself
#: -- but not always: some tracks' pages (confirmed live, e.g.
#: trec33/vtt's "VTC for two model") emit an entirely unescaped href with a
#: literal raw space in it. So the extracted segment cannot simply be
#: echoed back verbatim; it is normalized (unquoted, then re-quoted) by
#: `_run_id_from_links` below, which is idempotent regardless of whether
#: the source href was already percent-encoded or not, and guarantees the
#: result is always whitespace-free and exactly the path segment the gated
#: URL needs.
_RUN_ID_FROM_LINK_RES = {
    'input': re.compile(r'/input\.(?P<run_id>[^/]+)\.gz$'),
    'summary_trec_eval': re.compile(r'/summary\.trec_eval\.(?P<run_id>[^/]+)$'),
    'summary_extended': re.compile(r'/summary\.extended\.(?P<run_id>[^/]+)$'),
}


def _run_id_from_links(links):
    """The hosted-filename-derived run id (see ``_RUN_ID_FROM_LINK_RES``),
    checked in the same link-priority order as ``results_path`` below --
    ``None`` if none of these three links are present/shaped as expected
    (e.g. a run that only links to a non-``trec.nist.gov`` host).

    The extracted segment is normalized via unquote-then-requote: NIST's
    own HTML is inconsistent about whether a filename-unsafe character
    (chiefly a space) is percent-encoded in the href, so round-tripping
    through unquote/quote first undoes any existing encoding, then
    re-applies it uniformly -- the result is always whitespace-free and
    stable no matter which way the source href happened to be written."""
    for key, pattern in _RUN_ID_FROM_LINK_RES.items():
        href = links.get(key)
        if not href:
            continue
        m = pattern.search(href)
        if m:
            return urllib.parse.quote(urllib.parse.unquote(m.group('run_id')), safe='')
    return None


def _lxml_html():
    return ir_datasets.lazy_libs.lxml_html()


def run_listing_pages(session):
    """Every ``.../runs/`` page the site's own sitemap.xml lists -- one per
    track/subtrack/year, discovered rather than hand-maintained (new tracks
    show up here automatically)."""
    html = _lxml_html()
    resp = session.get(SITEMAP, timeout=30)
    resp.raise_for_status()
    tree = html.fromstring(resp.content)
    for loc in tree.xpath('//*[local-name()="loc"]/text()'):
        if loc.rstrip('/').endswith('/runs'):
            yield loc


def _text(li, label):
    # Each <li> is `<span icon/> <strong>Label:</strong> value`; strip the
    # icon/label, keep whatever text (or a nested <code>, for MD5) follows.
    value = ''.join(li.xpath(f'.//strong[starts-with(text(), "{label}")]'
                              '/following-sibling::text() | '
                              f'.//strong[starts-with(text(), "{label}")]'
                              '/following-sibling::code/text()'))
    return value.strip()


def parse_run_listing(content, page_url):
    """Every run's metadata + links on one ``.../runs/`` page's already-
    fetched HTML ``content`` (bytes) -- split from ``scrape_run_listing``
    so tests can feed a saved fixture instead of hitting the network."""
    html = _lxml_html()
    tree = html.fromstring(content)
    tree.make_links_absolute(page_url)
    for header in tree.xpath('//h4'):
        anchor = header.get('id')
        # The <p> of links, then the <ul> of metadata -- both immediately
        # follow this run's <h4> (confirmed DOM shape; see this module's
        # own docstring and dataset.py's).
        p = header.xpath('following-sibling::p[1]')
        ul = header.xpath('following-sibling::ul[1]')
        links = {}
        if p:
            for a in p[0].xpath('.//a'):
                label = re.sub(r'\s+', ' ', a.text_content()).strip()
                key = _LINK_LABELS.get(label)
                if key:
                    links[key] = a.get('href')
        record = {'links': links}
        if ul:
            for label, field in _FIELD_LABELS.items():
                record[field] = _text(ul[0], label)
        # Overrides the free-text "Run ID:" bullet (just parsed into
        # record['run_id'] above, by _FIELD_LABELS) with the hosted-filename
        # id whenever one of the gated links is present -- see
        # _run_id_from_links's own docstring. Falls back to the free-text
        # field only for a run with no resolvable results/ link at all (one
        # that, per results_path below, gets no results_track either, and so
        # never backs a `trec-browser:` node name to begin with).
        record['run_id'] = _run_id_from_links(links) or record.get('run_id')
        record['page_url'] = page_url
        record['deep_link'] = f'{page_url}#{anchor}' if anchor else page_url
        # The gated input/summary links are the authoritative source of the
        # `track`/`subtrack` this run's `trec-browser:` node name uses (see
        # _RESULTS_PATH_RE's own docstring) -- not the (sometimes
        # differently-pluralized) browsing page URL.
        results_path = next(
            (links[k] for k in ('input', 'summary_trec_eval', 'summary_extended')
             if k in links), None)
        m = _RESULTS_PATH_RE.match(results_path) if results_path else None
        record['results_track'] = m.group(1) if m else None
        record['results_subtrack'] = m.group(2) if m else None
        yield record


def scrape_run_listing(session, page_url):
    """Fetches and parses one ``.../runs/`` page -- see ``parse_run_listing``
    for the actual parsing, kept network-free for testing."""
    resp = session.get(page_url, timeout=30)
    resp.raise_for_status()
    return parse_run_listing(resp.content, page_url)


#: Pulls `{track}/{subtrack}` straight out of a `.../data/` page's own URL
#: (unlike `_RESULTS_PATH_RE`, there's no gated link to prefer here -- a
#: data page's ir_datasets cross-references are track-wide, not per-run, so
#: nothing on the page itself ties back to a particular results/ path).
_DATA_PAGE_URL_RE = re.compile(
    r'^https://pages\.nist\.gov/trec-browser/([^/]+)/([^/]+)/data/?$')


def data_listing_pages(session):
    """Every ``.../data/`` page the site's own sitemap.xml lists -- one per
    track/subtrack, distinct from the per-run ``.../runs/`` pages
    (``run_listing_pages``): this is the page where NIST, when it has done
    so, cross-references the real ``ir_datasets`` dataset id(s) backing
    that track's data (see ``parse_data_page``)."""
    html = _lxml_html()
    resp = session.get(SITEMAP, timeout=30)
    resp.raise_for_status()
    tree = html.fromstring(resp.content)
    for loc in tree.xpath('//*[local-name()="loc"]/text()'):
        if loc.rstrip('/').endswith('/data'):
            yield loc


def parse_data_page(content, page_url):
    """The ``ir_datasets`` cross-reference(s) (if any) one ``.../data/``
    page's already-fetched HTML ``content`` (bytes) lists -- a single
    ``<li><strong>ir_datasets</strong>: <a href="https://ir-datasets.com/
    {page}.html#{dataset_id}">{label}</a> | ...</li>`` bullet, one ``<a>``
    per dataset it covers. The real ``ir_datasets`` id is the URL
    *fragment* (``#msmarco-passage-v2/trec-dl-2020``), never the link text
    (a display label only, e.g. "Passage Ranking") -- see this module's own
    docstring and ``dataset.py``'s.

    Confirmed present (e.g. ``trec29/deep/data/``) and confirmed *absent*
    (e.g. ``trec34/rag/data/``, no such bullet at all) on the live site --
    extracted "when possible", so a page without it yields nothing, not an
    error."""
    html = _lxml_html()
    tree = html.fromstring(content)
    tree.make_links_absolute(page_url)
    m = _DATA_PAGE_URL_RE.match(page_url)
    track, subtrack = (m.group(1), m.group(2)) if m else (None, None)
    for li in tree.xpath(
            '//li[strong[starts-with(normalize-space(string(.)), "ir_datasets")]]'):
        for a in li.xpath('.//a'):
            href = a.get('href')
            if not href or '#' not in href:
                continue
            dataset_id = href.split('#', 1)[1]
            if not dataset_id:
                continue
            yield {
                'results_track': track, 'results_subtrack': subtrack,
                'dataset_id': dataset_id,
                'label': re.sub(r'\s+', ' ', a.text_content()).strip(),
                'url': href, 'page_url': page_url,
            }


def scrape_data_page(session, page_url):
    """Fetches and parses one ``.../data/`` page -- see ``parse_data_page``
    for the actual parsing, kept network-free for testing."""
    resp = session.get(page_url, timeout=30)
    resp.raise_for_status()
    return list(parse_data_page(resp.content, page_url))


def export_all_datasets(session=None, limit=None, sleep=0.2, log=None):
    """Every ``ir_datasets`` cross-reference across every track/subtrack
    data page the site currently lists -- most tracks have none at all."""
    requests = ir_datasets.lazy_libs.requests()
    session = session or requests.Session()
    session.headers.setdefault('User-Agent', 'trec-browser-provider')
    pages = list(data_listing_pages(session))
    if limit:
        pages = pages[:limit]
    for i, page_url in enumerate(pages):
        if log:
            log(f'[{i + 1}/{len(pages)}] {page_url}')
        yield from scrape_data_page(session, page_url)
        if sleep:
            time.sleep(sleep)


def build_dataset_index(records):
    """``records`` (as from ``export_all_datasets``), keyed by
    ``(results_track, results_subtrack)`` -> the list of ``ir_datasets``
    ids that track/subtrack's ``.../data/`` page cross-references
    (deduplicated, first-seen order). A track/subtrack with no such bullet
    simply has no key here -- not an empty list -- so callers can tell
    "not scraped"/"has none" apart from "has some" with a plain ``in``/
    ``.get()``."""
    index = {}
    for record in records:
        key = (record.get('results_track'), record.get('results_subtrack'))
        if None in key:
            continue
        ids = index.setdefault(key, [])
        if record['dataset_id'] not in ids:
            ids.append(record['dataset_id'])
    return index


def export_all(session=None, limit=None, sleep=0.2, log=None):
    """Every run across every track/year the site currently lists."""
    requests = ir_datasets.lazy_libs.requests()
    session = session or requests.Session()
    session.headers.setdefault('User-Agent', 'trec-browser-provider')
    pages = list(run_listing_pages(session))
    if limit:
        pages = pages[:limit]
    for i, page_url in enumerate(pages):
        if log:
            log(f'[{i + 1}/{len(pages)}] {page_url}')
        yield from scrape_run_listing(session, page_url)
        if sleep:
            time.sleep(sleep)


def build_index(records):
    """``records`` (as from ``export_all``), keyed by
    ``(results_track, results_subtrack, run_id)`` -- the same triple
    ``dataset.py``'s generators match on. A run missing a
    resolvable ``results_track``/``results_subtrack`` (no gated link at all
    -- e.g. a withdrawn submission) is skipped: there is no `trec-browser:`
    name for it to back."""
    index = {}
    for record in records:
        key = (record.get('results_track'), record.get('results_subtrack'),
              record.get('run_id'))
        if None in key:
            continue
        index[key] = record
    return index


#: Fields worth shipping in the static index -- enough to build a real
#: Resource (md5) and useful discovery metadata (participant/track/year/
#: description/deep_link), without the full scraped record (``links``,
#: ``page_url``, the readable-but-redundant ``track``/``type`` display
#: strings already implied by ``results_track``/``results_subtrack`` in most
#: cases) -- the raw scrape is ~14MB; this trims it to a fraction of that
#: (see this module's own maintenance command below).
_SHIPPED_FIELDS = (
    'results_track', 'results_subtrack', 'run_id', 'participant', 'track',
    'year', 'submission', 'type', 'md5', 'description', 'deep_link',
)


def trimmed_rows(index, dataset_index=None):
    """``index`` (as from ``build_index``), as the flat, shipped-size list
    of rows ``dataset.py`` loads from the static index file.
    If ``dataset_index`` (as from ``build_dataset_index``) is given, each
    row also gets an ``ir_datasets_ids`` key -- the cross-referenced
    ``ir_datasets`` id(s) for that run's track/subtrack, when the TREC
    Browser's own ``.../data/`` page lists any (most don't; the key is
    simply ``None`` then, same as any other unset optional field)."""
    rows = []
    for record in index.values():
        row = {k: record.get(k) for k in _SHIPPED_FIELDS}
        if dataset_index:
            row['ir_datasets_ids'] = dataset_index.get(
                (record.get('results_track'), record.get('results_subtrack')))
        rows.append(row)
    return rows


#: Where ``dataset.py`` reads the static index from, and where
#: this module's own CLI (below) writes it by default.
DEFAULT_INDEX_PATH = (
    pathlib.Path(__file__).resolve().parent / 'etc' / 'trec_browser_runs.json.gz')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=None,
                        help='only scrape the first N listing pages (smoke test)')
    parser.add_argument('--sleep', type=float, default=0.2,
                        help='seconds to sleep between page fetches (politeness)')
    parser.add_argument('--out', type=pathlib.Path, default=DEFAULT_INDEX_PATH,
                        help='where to write the gzip'"'"'d JSON index')
    args = parser.parse_args()

    def log(msg):
        print(msg, file=sys.stderr)

    records = list(export_all(limit=args.limit, sleep=args.sleep, log=log))
    index = build_index(records)
    dataset_records = list(export_all_datasets(limit=args.limit, sleep=args.sleep, log=log))
    dataset_index = build_dataset_index(dataset_records)
    rows = trimmed_rows(index, dataset_index=dataset_index)
    log(f'{len(records)} runs scraped; {len(rows)} resolvable to a '
        f'trec.nist.gov/results/ path; {len(dataset_index)} track/subtrack '
        f'pairs with an ir_datasets cross-reference; writing to {args.out}')
    with gzip.open(args.out, 'wt', encoding='utf-8') as fout:
        json.dump(rows, fout, sort_keys=True)


if __name__ == '__main__':
    main()
