# docker build -f docker/graph-db.Dockerfile -t ghcr.io/ir-datasets/ir-datasets.com:0.0.1-trec-browser .
#
# Rebuilds ir-datasets.com's own dev image (see
# ../../ir-datasets.com/.devcontainer/Dockerfile, which installs this
# package with a plain `pip install git+...` -- i.e. whatever commit was
# current the last time that image was built) with *this* checkout's code
# instead, bakes a graph.db/.nq.gz built from just the `trec-browser`
# provider into the image, and serves it by default -- so the image is
# directly runnable, with no separate build step at deploy time.
FROM ghcr.io/ir-datasets/ir-datasets.com:0.0.1-dev

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

# 0.0.0.0, not the `serve` subcommand's own 127.0.0.1 default, so the Flask
# dev server inside the container is reachable from outside it.
EXPOSE 5000
ENTRYPOINT ["ir-datasets-site", "serve", "--host", "0.0.0.0", "--port", "5000"]
