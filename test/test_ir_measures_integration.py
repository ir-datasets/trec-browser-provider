"""Integration tests for the run-file page's "Use It" ir_measures snippet on
ir-datasets.com:

    from ir_datasets.v2 import load
    from ir_measures import read_trec_run

    run = read_trec_run(load('trec-browser:...raw').path())

No network: the resource's download is swapped for local bytes, exercising
everything the snippet relies on -- ``path()`` materializing the file under
the resource's own name (the ``.gz`` suffix ``read_trec_run`` keys its gzip
branch on) and the file parsing into ScoredDocs / evaluating against qrels.
"""
import gzip
import hashlib
import os
import tempfile
import unittest
from unittest import mock

import ir_measures
from ir_measures import P, ScoredDoc, nDCG

# Importing trec_browser_provider first would trigger ir_datasets' entry-point
# discovery while this package is still partially initialized (the provider
# registers itself at import time), so bootstrap ir_datasets first.
import ir_datasets.v2  # noqa: F401  (import order matters; see comment above)
from trec_browser_provider import dataset as tbm

from test_trec_browser import _FixedBytesSource  # no __init__.py: a sibling test module

RUN_BYTES = gzip.compress(
    b'Q0 0 docA 0 0.9 runA\n'
    b'Q0 0 docB 1 0.5 runA\n'
    b'Q1 0 docC 0 0.7 runA\n')
QRELS_BYTES = b'Q0 0 docA 1\nQ0 0 docB 0\nQ1 0 docC 1\n'


class TestIrMeasuresUseItSnippet(unittest.TestCase):
    def setUp(self):
        # keep the materialized files out of the user's real cache
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {'IR_DATASETS_HOME': tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_resource(self):
        return tbm._SoftHashResource(
            'input.2002cuaq1.gz', sources=[_FixedBytesSource(RUN_BYTES)],
            hash=f'md5:{hashlib.md5(RUN_BYTES).hexdigest()}')

    def test_read_trec_run_parses_a_run_files_path(self):
        path = self.run_resource().path()
        self.assertTrue(path.endswith('input.2002cuaq1.gz'),
                        'the .gz suffix is what read_trec_run keys its gzip branch on')
        self.assertEqual([
            ScoredDoc(query_id='Q0', doc_id='docA', score=0.9),
            ScoredDoc(query_id='Q0', doc_id='docB', score=0.5),
            ScoredDoc(query_id='Q1', doc_id='docC', score=0.7),
        ], list(ir_measures.read_trec_run(path)))

    def test_calc_aggregate_evaluates_a_run_against_qrels(self):
        # the snippet's other form, shown when the run's benchmark has a qrels
        # facet: a one-shot run, read once, aggregated over both metrics
        qrels = tbm._SoftHashResource('qrels', sources=[_FixedBytesSource(QRELS_BYTES)])
        run = ir_measures.read_trec_run(self.run_resource().path())
        scores = ir_measures.calc_aggregate(
            [nDCG@10, P(rel=1)@10], ir_measures.read_trec_qrels(qrels.path()), run)
        # both rankings are perfect, and each query retrieves one relevant
        # document of the 10 the P@10 cutoff asks for (trec_eval semantics)
        self.assertEqual(2, len(scores))
        self.assertEqual(1.0, scores[nDCG@10])
        self.assertAlmostEqual(0.1, scores[P(rel=1)@10])


if __name__ == '__main__':
    unittest.main()
