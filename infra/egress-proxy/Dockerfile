# kodekantor/egress-proxy: tinyproxy, default deny, CONNECT to the ADR-007 registries on port 443 only
# (DEPLOYMENT §2, spec WP-1.5b). Build context: the repository root (infra/compose.yml). Runs as uid 100 on a
# read-only rootfs; the config and allowlist are part of the image, not mounted.
#
# The base is pinned by digest (alpine 3.24). BASE_REPO may name a mirror; the digest stays the same.
# build_ca (optional BuildKit secret): CA bundle of a TLS-intercepting proxy, trusted only while apk runs
# and removed again in the same layer.
ARG BASE_REPO=docker.io/library/alpine

FROM ${BASE_REPO}@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a
ARG TINYPROXY_VERSION=1.11.3-r0
RUN --mount=type=secret,id=build_ca,required=false \
    cp /etc/ssl/certs/ca-certificates.crt /tmp/ca-bundle.orig \
    && if [ -s /run/secrets/build_ca ]; then cat /run/secrets/build_ca >> /etc/ssl/certs/ca-certificates.crt; fi \
    && apk add --no-cache "tinyproxy=${TINYPROXY_VERSION}" \
    && mv /tmp/ca-bundle.orig /etc/ssl/certs/ca-certificates.crt \
    && find / -xdev -perm /6000 -type f -exec chmod a-s {} +
COPY infra/egress-proxy/tinyproxy.conf infra/egress-proxy/allowlist /etc/egress-proxy/
USER 100:101
EXPOSE 3128
ENTRYPOINT ["tinyproxy", "-d", "-c", "/etc/egress-proxy/tinyproxy.conf"]
