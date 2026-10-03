"""Unit tests for ``trec_browser_provider/export.py`` against real, unmodified
HTML -- unlike ``test_trec_browser_export.py``'s hand-written fixtures, the
two ``.../runs/`` pages here (``test/resources/trec19_entity_runs.html`` and
``test/resources/trec28_decisions_runs.html``) were downloaded as-is from
the live site, so these exercise ``parse_run_listing`` against NIST's actual
(occasionally malformed) markup rather than a clean approximation of it.

``trec19_entity_runs.html`` is the page that was once scraped into the
shipped static index's one confirmed-corrupted record (trec19/entity/
ICTNETRun1, see ``export.py``'s ``_text``/``_ul_belongs_to_a_single_run``
docstrings): its own "Run description" bullet contains a literal, unescaped
``<table>``/``<ul>`` tag that leaves its real ``<ul>`` unclosed, so
libxml2's HTML-recovery parser merges the next run's (``ilpsA500``'s)
bullets into it. One test below confirms the fix recovers a single, clean
value per field for ICTNETRun1 anyway (not a '   '-joined blob of every run
sharing its mis-scoped ``<ul>``); another confirms its merged-into neighbor,
``ilpsA500``, likewise keeps its own value rather than ICTNETRun1's.

``trec28_decisions_runs.html`` is a second, well-formed page from a
different track/task, used both to confirm no warning/regression on clean
pages and to exercise a couple of its own run entries.
"""
import pathlib
import unittest
import warnings

from trec_browser_provider import export as tbe

_RESOURCES = pathlib.Path(__file__).parent / 'resources'

_TREC19_ENTITY_PAGE_URL = 'https://pages.nist.gov/trec-browser/trec19/entity/runs/'
_TREC28_DECISIONS_PAGE_URL = 'https://pages.nist.gov/trec-browser/trec28/decisions/runs/'


def _parse(resource_name, page_url):
    content = (_RESOURCES / resource_name).read_bytes()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        records = {r['run_id']: r for r in tbe.parse_run_listing(content, page_url)}
    return records, caught


class TestParseRunListingAgainstRealDownloadedPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trec19_entity, cls.trec19_entity_warnings = _parse(
            'trec19_entity_runs.html', _TREC19_ENTITY_PAGE_URL)
        cls.trec28_decisions, cls.trec28_decisions_warnings = _parse(
            'trec28_decisions_runs.html', _TREC28_DECISIONS_PAGE_URL)

    def test_trec19_entity_page_warns_about_the_one_mis_scoped_ul(self):
        self.assertEqual(1, len(self.trec19_entity_warnings))
        self.assertIn('ictnetrun1', str(self.trec19_entity_warnings[0].message))

    def test_trec28_decisions_page_is_well_formed_and_never_warns(self):
        self.assertEqual(0, len(self.trec28_decisions_warnings))

    def test_ictnetrun1_gets_its_own_single_clean_value_per_field(self):
        # The run whose own "Run description" bullet contains a literal,
        # unescaped <table>/<ul> that leaves its <ul> unclosed upstream --
        # confirming the fix recovers correct data (matching the live page)
        # instead of a '   '-joined blob of every run sharing that <ul>.
        run = self.trec19_entity['ICTNETRun1']
        self.assertEqual('ICTNET', run['participant'])
        self.assertEqual('Entity', run['track'])
        self.assertEqual('2010', run['year'])
        self.assertEqual('10/1/2010', run['submission'])
        self.assertEqual('automatic', run['type'])
        self.assertEqual('0d94b2bd88e28144ff9e4ada4372f2ea', run['md5'])
        self.assertTrue(
            run['description'].startswith(
                'Procedure: 1. Extend the keyword set with WordNet'))
        self.assertEqual('trec19', run['results_track'])
        self.assertEqual('entity', run['results_subtrack'])

    def test_the_run_merged_into_ictnetrun1s_ul_keeps_its_own_value_too(self):
        # ilpsA500's own <h4>/<p>/<li>s are the ones libxml2's HTML-recovery
        # parser folds into ICTNETRun1's unclosed <ul> -- it must still get
        # its own value per field, not ICTNETRun1's (or a merged blob).
        run = self.trec19_entity['ilpsA500']
        self.assertEqual('UAms', run['participant'])
        self.assertEqual('automatic', run['type'])
        self.assertEqual('9/30/2010', run['submission'])
        self.assertEqual('e0ade9666cf43cf895a5281ae60e4da7', run['md5'])
        self.assertNotEqual(
            self.trec19_entity['ICTNETRun1']['description'], run['description'])

    def test_a_run_on_the_same_page_unaffected_by_the_mis_scoped_ul(self):
        run = self.trec19_entity['bitDSHPRun']
        self.assertEqual('BIT', run['participant'])
        self.assertEqual('Entity', run['track'])
        self.assertEqual('2010', run['year'])
        self.assertEqual('automatic', run['type'])
        self.assertEqual('e7f27d6f1a41aea6f1f76bdef022d643', run['md5'])

    def test_a_selected_run_on_the_second_well_formed_page(self):
        run = self.trec28_decisions['ICTNETv1BM25']
        self.assertEqual('ICTNET', run['participant'])
        self.assertEqual('Decision', run['track'])
        self.assertEqual('2019', run['year'])
        self.assertEqual('8/27/2019', run['submission'])
        self.assertEqual('auto', run['type'])
        self.assertEqual('7c29ba0024c1011bcf3eefe60ad2410f', run['md5'])
        self.assertEqual('trec28', run['results_track'])
        self.assertEqual('decision', run['results_subtrack'])


if __name__ == '__main__':
    unittest.main()
