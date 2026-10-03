"""The ``trec-browser:`` provider: resources linked from the TREC Browser.

This is a third-party package, not part of ``ir_datasets`` itself -- see
``ir_datasets``'s own ``AGENTS.md``/``v2/README.md`` on the entry-point
mechanism this relies on. It is generator-only, open-ended (effectively
every run ever submitted to any TREC track, across every edition), and
doesn't need anything from ``irds``'s shared registry besides the node types
it borrows (``irds:Resource``, the per-entity ``irds:Table`` subtypes).

Exposed as the ``trec-browser`` entry point in the ``ir_datasets.providers``
group (see ``pyproject.toml``); ``ir_datasets.v2``'s own ``discover()``
finds it automatically once this package is installed -- nothing in
``ir_datasets`` itself needs to import or know about it.
"""
from ir_datasets.v2.registry import ManifestProvider

trec_browser = ManifestProvider('trec-browser')

# Importing this registers every Generator onto `trec_browser` above (see
# dataset.py's own module docstring) -- done here, not left to the caller,
# so loading this one entry point is enough to fully initialize the
# provider (the same reason ir_datasets.v2's own built-in providers import
# their `datasets/*.py` module from `__init__.py`).
from . import dataset  # noqa: E402,F401
