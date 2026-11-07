# kodekantor/socket-proxy: tecnativa/docker-socket-proxy v0.5.0 (HAProxy 3.4, Alpine 3.24) with its Alpine packages
# moved to their fixed releases, because the newest upstream digest still carries them (DECISIONS EX-03, Trivy
# 31 Oct-7 Nov 2026). Build context: the repository root (infra/compose.yml).
#
# Nothing else changes: the same HAProxy binary, haproxy.cfg template, entrypoint and environment flags (ADR-026 (3),
# set in compose and checked by infra/tests/test_compose_policy.py, which also keeps this file to ARG/FROM/RUN so the
# image's ALLOW_*/SOCKET_PATH defaults stay upstream's). HAProxy links libssl/libcrypto, pcre2 and musl (not zlib).
# Minimum versions, not exact pins: Alpine drops old revisions from the v3.24 index, so an exact pin breaks the build.
# apk takes the newest revision in the branch (CI 7 Nov 2026: OpenSSL 3.5.9-r0, zlib 1.4-r0), and security-scan
# scans what was built; clean-install probes the allowlist (infra/tests/socket_proxy_probe.py).
#
# The base is pinned by digest. BASE_REPO may name a mirror; the digest stays the same.
# build_ca (optional BuildKit secret): CA bundle of a TLS-intercepting proxy, trusted only while apk runs and
# removed again in the same layer.
ARG BASE_REPO=docker.io/tecnativa/docker-socket-proxy

FROM ${BASE_REPO}@sha256:1f5038b54f06c3e18422902cf00ba21803d1c97805aae032e5e6673d532d3459
# The upstream image already runs as root (cap_drop ALL in compose); no USER change.
# Fixed releases (Trivy, 7 Nov 2026): OpenSSL CVE-2026-14456 -> 3.5.8-r0; PCRE2 CVE-2026-86145/89157/89161 -> 10.48-r0
# and CVE-2026-103111 -> 10.49-r0; zlib CVE-2026-85091 -> 1.3.2-r1; musl CVE-2026-107891 -> 1.2.6-r3.
RUN --mount=type=secret,id=build_ca,required=false \
    cp /etc/ssl/certs/ca-certificates.crt /tmp/ca-bundle.orig \
    && if [ -s /run/secrets/build_ca ]; then cat /run/secrets/build_ca >> /etc/ssl/certs/ca-certificates.crt; fi \
    && apk add --no-cache 'libcrypto3>=3.5.8-r0' 'libssl3>=3.5.8-r0' 'pcre2>=10.49-r0' 'zlib>=1.3.2-r1' \
        'musl>=1.2.6-r3' 'musl-utils>=1.2.6-r3' \
    && mv /tmp/ca-bundle.orig /etc/ssl/certs/ca-certificates.crt \
    && find / -xdev -perm /6000 -type f -exec chmod a-s {} + \
    && haproxy -v
