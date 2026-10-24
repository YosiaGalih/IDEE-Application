# kodekantor/sandbox-python: the image for python.* profiles (ARCHITECTURE §8, ADR-026 (2)).
#
# Runs as uid 10001 with a read-only rootfs and no network; the manager adds every runtime limit.
# The base is pinned by digest. BASE_IMAGE may point at a mirror, but only with this same digest.
ARG BASE_IMAGE=docker.io/library/python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt /tmp/requirements.txt

# extra_ca (optional build secret): CA bundle for a TLS-intercepting build proxy. Never stored in a layer.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi \
    && pip install --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid 10001 --home-dir /home/sandbox --no-create-home --shell /usr/sbin/nologin sandbox \
    && mkdir -p /workspace /home/sandbox \
    && chown 10001:10001 /home/sandbox \
    && find / -xdev -perm /6000 -type f -exec chmod a-s {} +

USER 10001:10001
WORKDIR /workspace
