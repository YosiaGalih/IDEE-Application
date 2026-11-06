# kodekantor/backend: one image for api, worker, scheduler and migrate (ARCHITECTURE §2, DEPLOYMENT §2).
# Build context: the repository root (infra/compose.yml). Runs as uid 10001 on a read-only rootfs.
#
# The base is pinned by digest (python 3.12-slim). BASE_REPO may name a mirror; the digest stays the same.
# build_ca (optional BuildKit secret): CA bundle of a TLS-intercepting proxy, never stored in a layer.
ARG BASE_REPO=docker.io/library/python

FROM ${BASE_REPO}@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
COPY infra/build/uv-requirements.txt /tmp/uv-requirements.txt
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi \
    && pip install --require-hashes -r /tmp/uv-requirements.txt
WORKDIR /src
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export SSL_CERT_FILE=/run/secrets/build_ca; fi \
    && uv sync --locked --no-dev --no-install-project

FROM ${BASE_REPO}@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
ENV PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
# git: GitAdapter (app/gitops, the only process the backend starts) creates and reads project repositories
# (WP-2.1a). Debian trixie's git, without recommends; set-uid bits are stripped after it is installed.
# Not pinned to a revision (DL3008): the Debian archive serves only the latest one, so a pin breaks the build at
# the next security update. The base is pinned by digest and security-scan (Trivy) checks what this installs.
# apt and libapt-pkg7.0 are upgraded to the archive's current revision: the base digest carries apt 3.0.3
# (CVE-2026-109518, fixed in 3.0.4). Not pinned (DL3008), as for git: the archive serves only the latest.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends git apt libapt-pkg7.0 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app \
    && find / -xdev -perm /6000 -type f -exec chmod a-s {} +
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic
COPY backend/app ./app
COPY backend/config ./config
# Registry schema 2 at the configured AGENT_REGISTRY_PATH (/app/agents/registry.yaml; WP-9.0 rename).
COPY agents/registry.v2.yaml ./agents/registry.yaml
USER 10001:10001
EXPOSE 8000
# Proxy headers are believed only from FORWARDED_ALLOW_IPS (uvicorn default 127.0.0.1; compose sets Caddy).
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
