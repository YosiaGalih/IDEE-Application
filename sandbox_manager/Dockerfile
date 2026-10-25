# kodekantor/sandbox-manager (ARCHITECTURE §8, DEPLOYMENT §2). Build context: the repository root.
# Runs as uid 10001 (config.check_process_user) on a read-only rootfs; reaches Docker only through
# docker-socket-proxy (DOCKER_HOST), never through a mounted socket (ADR-009).
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
COPY sandbox_manager/pyproject.toml sandbox_manager/uv.lock ./
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export SSL_CERT_FILE=/run/secrets/build_ca; fi \
    && uv sync --locked --no-dev --no-install-project

FROM ${BASE_REPO}@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
ENV PATH=/opt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app \
    && find / -xdev -perm /6000 -type f -exec chmod a-s {} +
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY sandbox_manager/sandbox_manager ./sandbox_manager
USER 10001:10001
EXPOSE 8100
CMD ["uvicorn", "sandbox_manager.main:app", "--host", "0.0.0.0", "--port", "8100"]
