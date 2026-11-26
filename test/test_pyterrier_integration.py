"""Integration test for the run-file page's "Use It" PyTerrier snippet on
ir-datasets.com:

    from ir_datasets.v2 import load
    import pyterrier as pt

    path = load('trec-browser:...raw').path()
    run = pt.io.read_results(path)

No network: the resource's download is swapped for local bytes, exercising
everything the snippet relies on -- ``path()`` materializing the file under
the resource's own name (the ``.gz`` suffix ``read_results`` decompresses by)
and the file reading into a DataFrame of qid/docno/score.
"""
import gzip
import hashlib
import os
import tempfile
import unittest
from unittest import mock

import pyterrier as pt

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


class TestPyTerrierUseItSnippet(unittest.TestCase):
    def setUp(self):
        # keep the materialized run file out of the user's real cache
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {'IR_DATASETS_HOME': tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_read_results_parses_a_run_files_path(self):
        resource = tbm._SoftHashResource(
            'input.2002cuaq1.gz', sources=[_FixedBytesSource(RUN_BYTES)],
            hash=f'md5:{hashlib.md5(RUN_BYTES).hexdigest()}')
        df = pt.io.read_results(resource.path())
        self.assertTrue({'qid', 'docno', 'score'} <= set(df.columns))
        self.assertEqual(
            [('Q0', 'docA', 0.9), ('Q0', 'docB', 0.5), ('Q1', 'docC', 0.7)],
            sorted(df[['qid', 'docno', 'score']].itertuples(index=False, name=None)))


if __name__ == '__main__':
    unittest.main()
