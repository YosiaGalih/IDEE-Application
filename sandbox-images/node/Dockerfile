# kodekantor/sandbox-node: the image for node.* profiles (ARCHITECTURE §8, ADR-026 (2)).
#
# Runs as uid 10001 with a read-only rootfs and no network; the manager adds every runtime limit.
# The base is pinned by digest (22-trixie-slim: Debian 13, like the Python images). BASE_REPO may name a mirror;
# the digest stays the same.
ARG BASE_REPO=docker.io/library/node
FROM ${BASE_REPO}@sha256:2117f713a417f3aff682ac8f5bc2ebbdf1beed51f36e67ab1e0fd3866b6d2d8f

ENV NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_OFFLINE=true

RUN groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid 10001 --home-dir /home/sandbox --no-create-home --shell /usr/sbin/nologin sandbox \
    && mkdir -p /workspace /home/sandbox \
    && chown 10001:10001 /home/sandbox \
    && find / -xdev -perm /6000 -type f -exec chmod a-s {} +

USER 10001:10001
WORKDIR /workspace
