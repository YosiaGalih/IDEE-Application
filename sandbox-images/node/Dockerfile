# kodekantor/sandbox-node: the image for node.* profiles (ARCHITECTURE §8, ADR-026 (2)).
#
# Runs as uid 10001 with a read-only rootfs and no network; the manager adds every runtime limit.
# The base is pinned by digest. BASE_IMAGE may point at a mirror, but only with this same digest.
ARG BASE_IMAGE=docker.io/library/node:22-slim@sha256:de170b39439eb0e5a3444ad7d60b015614a381e10fecae2e30ec9e735122f38d
FROM ${BASE_IMAGE}

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
