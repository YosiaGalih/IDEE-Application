# kodekantor/sandbox-python: the image for python.* profiles (ARCHITECTURE §8, ADR-026 (2)).
#
# Runs as uid 10001 with a read-only rootfs and no network; the manager adds every runtime limit.
# The base is pinned by digest (3.12-slim). BASE_REPO may name a mirror; the digest stays the same.
ARG BASE_REPO=docker.io/library/python
FROM ${BASE_REPO}@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt /tmp/requirements.txt

# apt and libapt-pkg7.0 are upgraded to the archive's current revision: the base digest carries apt 3.0.3
# (CVE-2026-109518, fixed in 3.0.4). Not pinned (DL3008), as for git: the archive serves only the latest.
# zlib1g likewise: the base carries 1:1.3.dfsg+really1.3.1-1+b1 (CVE-2026-76844, CVE-2026-85091, fixed in +deb13u1).
# extra_ca (optional build secret): CA bundle for a TLS-intercepting build proxy. Never stored in a layer.
# hadolint ignore=DL3008
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi \
    && pip install --require-hashes -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt \
    && apt-get update \
    && apt-get install -y --no-install-recommends apt libapt-pkg7.0 zlib1g \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid 10001 --home-dir /home/sandbox --no-create-home --shell /usr/sbin/nologin sandbox \
    && mkdir -p /workspace /home/sandbox \
    && chown 10001:10001 /home/sandbox \
    && find / -xdev -perm /6000 -type f -exec chmod a-s {} +

USER 10001:10001
WORKDIR /workspace
