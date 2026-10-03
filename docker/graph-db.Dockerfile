# docker build -f docker/graph-db.Dockerfile -t ghcr.io/ir-datasets/trec-browser-provider:0.0.1-trec-browser .
#
# Rebuilds ir-datasets.com's own dev image (see
# ../../ir-datasets.com/.devcontainer/Dockerfile, which installs this
# package with a plain `pip install git+...` -- i.e. whatever commit was
# current the last time that image was built) with *this* checkout's code
# instead, bakes a graph.db/.nq.gz built from just the `trec-browser`
# provider into the image, and serves it by default -- so the image is
# directly runnable, with no separate build step at deploy time.
FROM ghcr.io/ir-datasets/ir-datasets.com:0.0.1-dev
LABEL org.opencontainers.image.source="https://github.com/ir-datasets/trec-browser-provider"

# Overwrite whatever commit of this package the base image shipped with.
# --no-deps: this package declares no dependencies of its own (ir_datasets.v2
# isn't on PyPI yet -- see pyproject.toml's own comment), so there is nothing
# to reinstall here, and no risk of bumping the base image's pinned
# ir_datasets install.
WORKDIR /workspaces/trec-browser-provider
COPY . .
RUN pip install --no-cache-dir --no-deps --force-reinstall .

# Same working directory/output paths ir-datasets.com's own devcontainer
# already uses -- see `ir-datasets-site build-graph-db --help`.
WORKDIR /workspaces/ir-datasets.com
RUN ir-datasets-site build-graph-db --providers trec-browser

# `ir-datasets-site serve` (Flask's own dev server) is for local development
# only -- it warns, and isn't meant to carry real traffic. gunicorn runs the
# same module-level `app` object (see cli.py's own `from .app import app`)
# as a production WSGI server instead; `IR_DATASETS_SITE_STORE` is the env
# var app.py reads at import time to find graph.db (same one `serve --store`
# sets -- defaults to ./graph.db, which is this WORKDIR, where build-graph-db
# just wrote it).
RUN pip install --no-cache-dir gunicorn
ENV IR_DATASETS_SITE_STORE=/workspaces/ir-datasets.com/graph.db

EXPOSE 5000
ENTRYPOINT ["gunicorn", "--bind", "0.0.0.0:5000", "ir_datasets_site.app:app"]
