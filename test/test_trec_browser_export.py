"""Unit tests for the TREC Browser metadata scraper
(trec_browser_provider/export.py) -- parsing only, no network: each
test feeds a small hand-written HTML fixture matching the real site's
confirmed DOM shape (one ``<h4>`` per run, then its ``<p>`` of links, then
its ``<ul>`` of metadata bullets -- see export.py's own
docstring) rather than hitting pages.nist.gov.
"""
import re
import unittest

from trec_browser_provider import export as tbe

_PAGE_URL = 'https://pages.nist.gov/trec-browser/trec28/decisions/runs/'

#: Two runs: one with every field (the module's primary, gated shape), one
#: deliberately missing optional bits (no MD5 digest, no summary links, and
#: linking to a non-trec.nist.gov host) -- the real site has both kinds (see
#: TREC-COVID's ``ir.nist.gov``-hosted runs, which this module's
#: ``build_index`` must therefore skip rather than crash on).
_FIXTURE = '''
<html><body>
<h1 id="runs-decision-2019">Runs - Decision 2019</h1>
<h4 id="ictnetv1bm25">ICTNETv1BM25</h4>
<p><a href=".././results/#ictnetv1bm25"><code>Results</code></a> | <a href=".././participants/#ictnet"><code>Participants</code></a> | <a href=".././proceedings/#ictnet-at-trec-2019-decision-track"><code>Proceedings</code></a> | <a href="https://trec.nist.gov/results/trec28/decision/input.ICTNETv1BM25.gz"><code>Input</code></a> | <a href="https://trec.nist.gov/results/trec28/decision/summary.trec_eval.ICTNETv1BM25"><code>Summary (trec_eval)</code></a> | <a href="https://trec.nist.gov/results/trec28/decision/summary.extended.ICTNETv1BM25"><code>Summary (extended)</code></a> | <a href="https://trec.nist.gov/pubs/trec28/appendices/decisions/ICTNETv1BM25.pdf"><code>Appendix</code></a></p>
<ul>
<li><strong>Run ID:</strong> ICTNETv1BM25</li>
<li><strong>Participant:</strong> ICTNET</li>
<li><strong>Track:</strong> Decision</li>
<li><strong>Year:</strong> 2019</li>
<li><strong>Submission:</strong> 8/27/2019</li>
<li><strong>Type:</strong> auto</li>
<li><strong>MD5:</strong> <code>7c29ba0024c1011bcf3eefe60ad2410f</code></li>
<li><strong>Run description:</strong> We extract the content of the document and use BM25 for retrieval.</li>
</ul>
<h4 id="nomd5run">NoMd5Run</h4>
<p><a href="https://ir.nist.gov/archive/round1/nomd5run"><code>Input</code></a></p>
<ul>
<li><strong>Run ID:</strong> NoMd5Run</li>
<li><strong>Participant:</strong> SomeTeam</li>
<li><strong>Track:</strong> Decision</li>
<li><strong>Year:</strong> 2019</li>
<li><strong>Submission:</strong> 8/28/2019</li>
<li><strong>Type:</strong> manual</li>
<li><strong>MD5:</strong> </li>
<li><strong>Run description:</strong> A run without a results/-hosted input link.</li>
</ul>
</body></html>
'''


class TestTrecBrowserExport(unittest.TestCase):
    def setUp(self):
        self.records = list(tbe.parse_run_listing(_FIXTURE.encode('utf-8'), _PAGE_URL))

    def test_finds_every_run(self):
        self.assertEqual(['ICTNETv1BM25', 'NoMd5Run'],
                         [r['run_id'] for r in self.records])

    def test_parses_every_metadata_field(self):
        run = self.records[0]
        self.assertEqual('ICTNETv1BM25', run['run_id'])
        self.assertEqual('ICTNET', run['participant'])
        self.assertEqual('Decision', run['track'])
        self.assertEqual('2019', run['year'])
        self.assertEqual('8/27/2019', run['submission'])
        self.assertEqual('auto', run['type'])
        self.assertEqual('7c29ba0024c1011bcf3eefe60ad2410f', run['md5'])
        self.assertEqual(
            'We extract the content of the document and use BM25 for retrieval.',
            run['description'])

    def test_parses_every_link(self):
        links = self.records[0]['links']
        self.assertEqual(
            'https://trec.nist.gov/results/trec28/decision/input.ICTNETv1BM25.gz',
            links['input'])
        self.assertEqual(
            'https://trec.nist.gov/results/trec28/decision/summary.trec_eval.ICTNETv1BM25',
            links['summary_trec_eval'])
        self.assertEqual(
            'https://trec.nist.gov/results/trec28/decision/summary.extended.ICTNETv1BM25',
            links['summary_extended'])
        self.assertEqual(
            'https://trec.nist.gov/pubs/trec28/appendices/decisions/ICTNETv1BM25.pdf',
            links['appendix'])

    def test_deep_link_is_the_page_plus_the_run_anchor(self):
        self.assertEqual(f'{_PAGE_URL}#ictnetv1bm25', self.records[0]['deep_link'])

    def test_results_track_and_subtrack_come_from_the_gated_link(self):
        self.assertEqual('trec28', self.records[0]['results_track'])
        self.assertEqual('decision', self.records[0]['results_subtrack'])

    def test_a_run_with_no_results_hosted_link_has_no_results_track(self):
        run = self.records[1]
        self.assertIsNone(run['results_track'])
        self.assertIsNone(run['results_subtrack'])
        self.assertEqual('', run['md5'])

    def test_build_index_keys_by_track_subtrack_run_id(self):
        index = tbe.build_index(self.records)
        self.assertEqual(['ICTNETv1BM25'],
                         [k[2] for k in index])
        self.assertEqual('7c29ba0024c1011bcf3eefe60ad2410f',
                         index[('trec28', 'decision', 'ICTNETv1BM25')]['md5'])

    def test_build_index_skips_runs_without_a_resolvable_results_path(self):
        """``NoMd5Run`` links only to ``ir.nist.gov``, not
        ``trec.nist.gov/results/`` -- there is no ``trec-browser:`` name for
        it to back, so it must not appear in the index."""
        index = tbe.build_index(self.records)
        self.assertNotIn(('Decision', None, 'NoMd5Run'), index)
        self.assertEqual(1, len(index))


#: A run whose submitting team typed a free-text "Run ID" with real spaces
#: in it (NIST does not sanitize this field -- confirmed live, e.g.
#: trec34/avs's "Fuse all sub-models"/"BLIP BLIP2 CLIP LaCLIP SLIP
#: diffusion"). The *hosted* filename TREC itself gives the run is
#: space-free and percent-encoded (``input.BLIP%20BLIP2...gz``) -- this is
#: what `run_id` (and therefore every `trec-browser:` node name built from
#: it) must come from, never the free-text display field.
_WHITESPACE_PAGE_URL = 'https://pages.nist.gov/trec-browser/trec34/avs/runs/'
_WHITESPACE_RUN_ID_FIXTURE = '''
<html><body>
<h1 id="runs-avs-2025">Runs - Adhoc Video Search 2025</h1>
<h4 id="blip-blip2-clip-laclip-slip-diffusion">BLIP BLIP2 CLIP LaCLIP SLIP diffusion</h4>
<p><a href=".././participants/#whu-nercms"><code>Participants</code></a> | <a href="https://trec.nist.gov/results/trec34/avs/input.BLIP%20BLIP2%20CLIP%20LaCLIP%20SLIP%20diffusion.gz"><code>Input</code></a> | <a href="https://trec.nist.gov/pubs/trec34/appendices/trec2025-avs-main.html"><code>Appendix</code></a></p>
<ul>
<li><strong>Run ID:</strong> BLIP BLIP2 CLIP LaCLIP SLIP diffusion</li>
<li><strong>Participant:</strong> WHU-NERCMS</li>
<li><strong>Track:</strong> Adhoc Video Search</li>
<li><strong>Year:</strong> 2025</li>
<li><strong>Submission:</strong> 2025-07-28</li>
<li><strong>Type:</strong> automatic</li>
<li><strong>MD5:</strong> <code>9132f6cfa8d3e46c38d35939ee46bfc7</code></li>
<li><strong>Run description:</strong> 16:4:10:3:3:3</li>
</ul>
</body></html>
'''


#: Some tracks' pages (confirmed live, e.g. trec33/vtt's "VTC for two
#: model") emit a gated link whose href has a *literal, unescaped* raw
#: space in it -- NIST is not even consistent about percent-encoding this
#: itself. A naive "copy the href segment verbatim" fix would still leak
#: whitespace into `run_id` for exactly these runs; the real fix has to
#: normalize (unquote, then re-quote) the extracted segment so the result
#: is whitespace-free no matter which way the source href was written.
_UNESCAPED_SPACE_PAGE_URL = 'https://pages.nist.gov/trec-browser/trec33/vtt/runs/'
_UNESCAPED_SPACE_RUN_ID_FIXTURE = '''
<html><body>
<h1 id="runs-video-to-text-2024">Runs - Video to Text 2024</h1>
<h4 id="vtc-for-two-model">VTC for two model</h4>
<p><a href=".././participants/#ruc_aim3"><code>Participants</code></a> | <a href="https://trec.nist.gov/results/trec33/vtt/input.VTC for two model.gz"><code>Input</code></a> | <a href="https://trec.nist.gov/results/trec33/vtt/summary.VTC for two model"><code>Summary</code></a> | <a href="https://trec.nist.gov/pubs/trec33/appendices/trec2024-vtt-robust.html"><code>Appendix</code></a></p>
<ul>
<li><strong>Run ID:</strong> VTC for two model</li>
<li><strong>Participant:</strong> ruc_aim3</li>
<li><strong>Track:</strong> Video to Text</li>
<li><strong>Year:</strong> 2024</li>
<li><strong>Submission:</strong> 2024-08-01</li>
<li><strong>Type:</strong> automatic</li>
<li><strong>MD5:</strong> <code>deadbeefdeadbeefdeadbeefdeadbeef</code></li>
</ul>
</body></html>
'''


class TestRunIdNeverContainsWhitespace(unittest.TestCase):
    """Regression tests for a real bug: a submitting team's free-text "Run
    ID" field can contain spaces NIST never sanitizes (confirmed live on
    trec34/avs); using that text verbatim to build a `trec-browser:` node
    name (``input.{run_id}.gz``) put literal whitespace into the node name
    itself -- broken as a graph identifier, a URL path segment, a CLI
    argument, and an exported filename all at once. The fix: `run_id` must
    come from the actual, already-URL-safe filename TREC hosts the run
    under, never the free-text display field."""

    def setUp(self):
        self.records = list(tbe.parse_run_listing(
            _WHITESPACE_RUN_ID_FIXTURE.encode('utf-8'), _WHITESPACE_PAGE_URL))

    def test_run_id_has_no_whitespace(self):
        run_id = self.records[0]['run_id']
        self.assertNotRegex(run_id, r'\s')

    def test_run_id_is_the_percent_encoded_hosted_filename(self):
        self.assertEqual(
            'BLIP%20BLIP2%20CLIP%20LaCLIP%20SLIP%20diffusion',
            self.records[0]['run_id'])

    def test_build_index_key_has_no_whitespace(self):
        index = tbe.build_index(self.records)
        [key] = index.keys()
        self.assertNotRegex(key[2], r'\s')

    def test_unescaped_space_in_href_is_still_normalized(self):
        """Even when NIST's own HTML leaves a raw space in the href
        itself (unlike the percent-encoded fixture above), `run_id` must
        still come out whitespace-free and consistently percent-encoded."""
        records = list(tbe.parse_run_listing(
            _UNESCAPED_SPACE_RUN_ID_FIXTURE.encode('utf-8'), _UNESCAPED_SPACE_PAGE_URL))
        run_id = records[0]['run_id']
        self.assertNotRegex(run_id, r'\s')
        self.assertEqual('VTC%20for%20two%20model', run_id)

    def test_no_shipped_run_id_in_the_static_index_contains_whitespace(self):
        """The regenerated static index itself (``etc/trec_browser_runs.json.gz``,
        what ``datasets/trec_browser.py`` actually ships/loads) must have no
        surviving whitespace-laden run ids from before this fix."""
        from trec_browser_provider import dataset as tbm
        index = tbm._load_static_index(tbm.DEFAULT_INDEX_PATH)
        offenders = [key for key in index if key[2] and re.search(r'\s', key[2])]
        self.assertEqual([], offenders)


#: A `.../data/` page with the `ir_datasets` cross-reference bullet (two
#: links, same real DOM shape as `trec29/deep/data/`) plus the other,
#: unrelated bullets (`Corpus`/`Topics`/`Qrels`) a real page also has -- the
#: parser must only pick out the `ir_datasets` one.
_DATA_PAGE_URL = 'https://pages.nist.gov/trec-browser/trec29/deep/data/'
_DATA_FIXTURE_WITH_IDS = '''
<html><body>
<h1>Data - Deep Learning 2020</h1>
<hr />
<ul>
<li><strong>Corpus</strong>: <a href="https://microsoft.github.io/msmarco/TREC-Deep-Learning-2020#passage-ranking-dataset">Passage Ranking</a> | <a href="https://microsoft.github.io/msmarco/TREC-Deep-Learning-2020#document-ranking-dataset">Document Ranking</a></li>
<li><strong>Topics</strong>: <a href="https://microsoft.github.io/msmarco/TREC-Deep-Learning-2020#passage-ranking-dataset">Passage Ranking</a></li>
<li><strong>ir_datasets</strong>: <a href="https://ir-datasets.com/msmarco-passage-v2.html#msmarco-passage-v2/trec-dl-2020">Passage Ranking</a> | <a href="https://ir-datasets.com/msmarco-document-v2.html#msmarco-document-v2/trec-dl-2020">Document Ranking</a></li>
</ul>
<hr />
</body></html>
'''

#: The other, confirmed-absent shape (`trec34/rag/data/`): no `ir_datasets`
#: bullet at all, just the usual `Corpus`/`Topics`/`Qrels` ones.
_NO_IDS_PAGE_URL = 'https://pages.nist.gov/trec-browser/trec34/rag/data/'
_DATA_FIXTURE_WITHOUT_IDS = '''
<html><body>
<h1>Data - RAG</h1>
<ul>
<li><strong>Corpus</strong>: <a href="https://example.invalid/corpus">Corpus</a></li>
<li><strong>Topics</strong>: <a href="https://example.invalid/topics">Topics</a></li>
</ul>
</body></html>
'''


class TestTrecBrowserDataPages(unittest.TestCase):
    def test_extracts_every_ir_datasets_link(self):
        records = list(tbe.parse_data_page(
            _DATA_FIXTURE_WITH_IDS.encode('utf-8'), _DATA_PAGE_URL))
        self.assertEqual(
            ['msmarco-passage-v2/trec-dl-2020', 'msmarco-document-v2/trec-dl-2020'],
            [r['dataset_id'] for r in records])

    def test_dataset_id_is_the_url_fragment_not_the_link_text(self):
        records = list(tbe.parse_data_page(
            _DATA_FIXTURE_WITH_IDS.encode('utf-8'), _DATA_PAGE_URL))
        self.assertEqual('Passage Ranking', records[0]['label'])
        self.assertEqual('msmarco-passage-v2/trec-dl-2020', records[0]['dataset_id'])

    def test_track_and_subtrack_come_from_the_data_page_url(self):
        records = list(tbe.parse_data_page(
            _DATA_FIXTURE_WITH_IDS.encode('utf-8'), _DATA_PAGE_URL))
        self.assertEqual('trec29', records[0]['results_track'])
        self.assertEqual('deep', records[0]['results_subtrack'])

    def test_a_page_without_the_bullet_yields_nothing(self):
        """Extracted "when possible": absence is not an error -- see
        ``trec34/rag/data/`` on the live site."""
        records = list(tbe.parse_data_page(
            _DATA_FIXTURE_WITHOUT_IDS.encode('utf-8'), _NO_IDS_PAGE_URL))
        self.assertEqual([], records)

    def test_build_dataset_index_keys_by_track_subtrack(self):
        records = list(tbe.parse_data_page(
            _DATA_FIXTURE_WITH_IDS.encode('utf-8'), _DATA_PAGE_URL))
        index = tbe.build_dataset_index(records)
        self.assertEqual(
            ['msmarco-passage-v2/trec-dl-2020', 'msmarco-document-v2/trec-dl-2020'],
            index[('trec29', 'deep')])

    def test_build_dataset_index_has_no_key_for_a_track_without_any(self):
        index = tbe.build_dataset_index([])
        self.assertNotIn(('trec34', 'rag'), index)

    def test_build_dataset_index_deduplicates_repeated_ids(self):
        records = list(tbe.parse_data_page(
            _DATA_FIXTURE_WITH_IDS.encode('utf-8'), _DATA_PAGE_URL)) * 2
        index = tbe.build_dataset_index(records)
        self.assertEqual(2, len(index[('trec29', 'deep')]))


class TestTrimmedRowsWithDatasetIds(unittest.TestCase):
    def setUp(self):
        self.records = list(tbe.parse_run_listing(_FIXTURE.encode('utf-8'), _PAGE_URL))
        self.index = tbe.build_index(self.records)

    def test_without_dataset_index_no_ir_datasets_ids_key(self):
        rows = tbe.trimmed_rows(self.index)
        self.assertNotIn('ir_datasets_ids', rows[0])

    def test_matching_track_subtrack_gets_ir_datasets_ids(self):
        dataset_index = {('trec28', 'decision'): ['some-dataset/subset']}
        rows = tbe.trimmed_rows(self.index, dataset_index=dataset_index)
        self.assertEqual(['some-dataset/subset'], rows[0]['ir_datasets_ids'])

    def test_non_matching_track_subtrack_gets_none(self):
        dataset_index = {('trec99', 'other'): ['some-dataset/subset']}
        rows = tbe.trimmed_rows(self.index, dataset_index=dataset_index)
        self.assertIsNone(rows[0]['ir_datasets_ids'])


if __name__ == '__main__':
    unittest.main()
