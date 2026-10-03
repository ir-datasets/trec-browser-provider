"""``trec-browser-provider`` -- an ``ir_datasets.v2`` provider over resources
linked from the TREC Browser (https://pages.nist.gov/trec-browser/).

Installing this package is enough to make ``trec-browser:...`` names
resolvable in ``ir_datasets`` -- it's discovered automatically via the
``ir_datasets.providers`` entry-point group (see ``provider.py``'s own
docstring, and ``pyproject.toml``). Nothing needs to be imported from this
package directly; ``import ir_datasets.v2 as v2; v2.load('trec-browser:...')``
is enough once it's installed.
"""
from .provider import trec_browser

__all__ = ['trec_browser']
