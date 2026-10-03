"""Unit tests for the ``trec-browser:`` dynamic provider
(trec_browser_provider/provider.py + trec_browser_provider/dataset.py).

No network: these only check name matching/resolution and the credential
check, never an actual download.
"""
import contextlib
import hashlib
import io
import os
import unittest
from unittest import mock

import ir_datasets.v2 as v2
from ir_datasets.util.download import BaseDownload
from ir_datasets.v2.sources import Source

from trec_browser_provider import dataset as tbm


class _FixedBytesSource(Source):
    """A ``Source`` that always serves ``content``, bypassing any network --
    stands in for a real ``_EnvBasicAuthSource`` in the MD5-mismatch tests
    below, which only care about ``_SoftHashResource.stream()``'s own
    behaviour, not actually reaching trec.nist.gov."""

    def __init__(self, content):
        super().__init__('https://example.invalid/fixed-bytes')
        self._content = content

    def _build(self, file):
        content = self._content

        class _Download(BaseDownload):
            def stream(self):
                return contextlib.nullcontext(io.BytesIO(content))

        return _Download()


class TestV2TrecBrowser(unittest.TestCase):
    def test_run_resolves_to_a_run_table(self):
        node = v2.graph['trec-browser:trec28/decision/input.ICTNETv1BM25.gz']
        self.assertIsInstance(node, v2.TrecScoredDocs)
        self.assertEqual(v2.RunTable.type, node.type)

    def test_trec_eval_summary_resolves_to_an_evaluation_table(self):
        node = v2.graph['trec-browser:trec28/decision/summary.trec_eval.ICTNETv1BM25']
        self.assertIsInstance(node, v2.TrecEval)
        self.assertEqual(v2.EvaluationTable.type, node.type)

    def test_extended_summary_resolves_to_an_evaluation_table(self):
        node = v2.graph['trec-browser:trec28/decision/summary.extended.ICTNETv1BM25']
        self.assertIsInstance(node, v2.TrecEval)

    def test_anything_else_falls_back_to_a_generic_resource(self):
        node = v2.graph['trec-browser:trec28/decision/appendix.ICTNETv1BM25.pdf']
        self.assertIsInstance(node, v2.Resource)

    def test_run_source_is_the_gated_url(self):
        node = v2.graph['trec-browser:trec28/decision/input.ICTNETv1BM25.gz']
        # node.source is resource.gunzip(); walk the pipeline to the Resource.
        resource = node.source
        while not isinstance(resource, v2.Resource):
            resource = resource._parent
        self.assertEqual(
            'https://trec.nist.gov/results/trec28/decision/input.ICTNETv1BM25.gz',
            resource.sources[0].url)
        self.assertTrue(resource.dua)

    def test_missing_credentials_raise_runtime_error(self):
        node = v2.graph['trec-browser:trec28/decision/summary.trec_eval.ICTNETv1BM25']
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(tbm.USERNAME_ENVVAR, None)
            os.environ.pop(tbm.PASSWORD_ENVVAR, None)
            with self.assertRaises(RuntimeError):
                tbm._basic_auth_header()
            with self.assertRaises(RuntimeError):
                with node.source.stream():
                    pass

    def test_credentials_build_a_basic_auth_header(self):
        with mock.patch.dict(os.environ, {
            tbm.USERNAME_ENVVAR: 'alice', tbm.PASSWORD_ENVVAR: 'secret',
        }):
            headers = tbm._basic_auth_header()
        self.assertEqual('Basic YWxpY2U6c2VjcmV0', headers['Authorization'])

    def test_known_run_gets_a_real_md5_hash_from_the_static_index(self):
        node = v2.graph['trec-browser:trec28/decision/input.ICTNETv1BM25.gz']
        raw = tbm.trec_browser.nodes['trec-browser:trec28/decision/input.ICTNETv1BM25.gz.raw']
        self.assertEqual('7c29ba0024c1011bcf3eefe60ad2410f', raw.md5)
        self.assertEqual('ICTNET', node.metadata.get('participant'))
        self.assertIn('deep_link', node.metadata)

    def test_known_summary_mentions_the_participant_and_deep_link_in_metadata(self):
        node = v2.graph['trec-browser:trec28/decision/summary.trec_eval.ICTNETv1BM25']
        self.assertEqual('ICTNET', node.metadata.get('participant'))
        self.assertIn('deep_link', node.metadata)

    def test_run_not_in_the_static_index_falls_back_to_the_dynamic_generator(self):
        node = v2.graph['trec-browser:trec99/madeup-subtrack/input.NOT_IN_INDEX.gz']
        self.assertIsInstance(node, v2.TrecScoredDocs)
        self.assertNotIn('participant', node.metadata)

    def test_run_surfaces_ir_datasets_ids_when_the_data_page_has_them(self):
        # Built directly (not via the shipped static index -- see
        # export.py's own scraper/tests for how this field is
        # populated from the live `.../data/` page): a run from a
        # track/subtrack the TREC Browser cross-references.
        node = tbm._run('trec29', 'deep', 'SomeRun', record={
            'ir_datasets_ids': ['msmarco-passage-v2/trec-dl-2020',
                               'msmarco-document-v2/trec-dl-2020']})
        self.assertEqual(
            ['msmarco-passage-v2/trec-dl-2020', 'msmarco-document-v2/trec-dl-2020'],
            node.metadata['ir_datasets_ids'])

    def test_run_has_no_ir_datasets_ids_key_when_the_data_page_has_none(self):
        node = tbm._run('trec34', 'rag', 'SomeRun', record={'participant': 'X'})
        self.assertNotIn('ir_datasets_ids', node.metadata)

    def test_summary_surfaces_ir_datasets_ids_in_metadata_when_present(self):
        node = tbm._summary('trec29', 'deep', 'trec_eval', 'SomeRun', record={
            'ir_datasets_ids': ['msmarco-passage-v2/trec-dl-2020']})
        self.assertEqual(['msmarco-passage-v2/trec-dl-2020'], node.metadata['ir_datasets_ids'])

    def test_summary_has_no_ir_datasets_ids_key_when_absent(self):
        node = tbm._summary('trec34', 'rag', 'trec_eval', 'SomeRun', record={'participant': 'X'})
        self.assertNotIn('ir_datasets_ids', node.metadata)

    def test_known_runs_are_enumerable_with_row_metadata(self):
        generator = tbm.trec_browser.generators[0]
        self.assertTrue(generator.enumerable)
        known = set(generator.params['run_path'].values)
        self.assertIn('trec28/decision/input.ICTNETv1BM25.gz', known)
        row = generator.row_metadata('trec28/decision/input.ICTNETv1BM25.gz')
        self.assertEqual(
            ['md5:7c29ba0024c1011bcf3eefe60ad2410f'],
            row['validation']['hashes'])
        self.assertEqual('ICTNET', row['participant'])

    def test_missing_index_file_degrades_to_no_known_runs(self):
        self.assertEqual({}, tbm._load_static_index('/no/such/file.json.gz'))


    def test_describe_flags_auth_without_leaking_credentials(self):
        with mock.patch.dict(os.environ, {
            tbm.USERNAME_ENVVAR: 'alice', tbm.PASSWORD_ENVVAR: 'secret',
        }):
            node = v2.graph['trec-browser:trec28/decision/summary.extended.someotherrun']
            described = node.source.sources[0].describe(0)
        self.assertTrue(described['auth'])

    def test_known_run_raw_file_is_a_soft_hash_resource(self):
        raw = tbm.trec_browser.nodes['trec-browser:trec28/decision/input.ICTNETv1BM25.gz.raw']
        self.assertIsInstance(raw, tbm._SoftHashResource)

    def test_soft_hash_resource_never_passes_hash_to_the_v1_download(self):
        # The hard-fail check lives in the v1 Download (util/download.py);
        # a _SoftHashResource must never hand it a hash to enforce -- see
        # the `download` property override.
        content = b'hello world'
        resource = tbm._SoftHashResource(
            'test-soft-hash', sources=[_FixedBytesSource(content)],
            hash=f'md5:{hashlib.md5(content).hexdigest()}')
        self.assertIsNone(resource.download.expected_md5)
        self.assertEqual({}, resource.download.expected_hashes)

    def test_soft_hash_resource_streams_fine_on_a_matching_hash(self):
        # The `download` property's own "no expected_md5" path (its own
        # harmless `consider adding expected_md5=...` warning, from the v1
        # Download it builds -- see its docstring) is expected here; only
        # a `md5 mismatch` warning would mean the soft check misfired.
        content = b'hello world'
        resource = tbm._SoftHashResource(
            'test-soft-hash-match', sources=[_FixedBytesSource(content)],
            hash=f'md5:{hashlib.md5(content).hexdigest()}')
        with self.assertLogs(tbm._logger.logger(), level='WARNING') as logs:
            with resource.stream() as f:
                self.assertEqual(content, f.read())
            tbm._logger.warn('(sentinel, so assertLogs always has output)')
        self.assertFalse(any('md5 mismatch' in m for m in logs.output))

    def test_soft_hash_resource_only_warns_on_a_mismatching_hash(self):
        content = b'hello world'
        resource = tbm._SoftHashResource(
            'test-soft-hash-mismatch', sources=[_FixedBytesSource(content)],
            hash='md5:0000000000000000000000000000000')
        with self.assertLogs(tbm._logger.logger(), level='WARNING') as logs:
            with resource.stream() as f:
                # a mismatch is a warning, never an exception, and the real
                # bytes are still delivered in full.
                self.assertEqual(content, f.read())
        self.assertTrue(any('md5 mismatch' in m for m in logs.output))


if __name__ == '__main__':
    unittest.main()
