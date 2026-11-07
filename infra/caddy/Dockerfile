# kodekantor/caddy: the ingress (DEPLOYMENT §6), built from source instead of the official caddy image, so the Go
# toolchain and golang.org/x/net carry their security fixes without waiting for an upstream rebuild (DECISIONS EX-03:
# CVE-2026-78669 HTTP/2 SETTINGS DoS on the public :443, CVE-2026-78661). Build context: the repository root
# (infra/compose.yml). The Caddyfile is mounted read-only by compose, not baked in.
#
# infra/caddy/build/ is the official entry point (caddy v2.11.7 cmd/caddy/main.go) with the standard modules.
# go.mod and go.sum pin Caddy 2.11.7 and every dependency at the versions Caddy asks for, except the security fixes
# golang.org/x/net 0.62.0 (with the x/crypto, x/sync, x/sys, x/term, x/text it needs), go-jose v4 4.1.6 and chi v5.4.0
# (CVE-2026-114437; AG-13 PR #23 L4). x/net 0.62.0 is younger than the two-week rule: a recorded exception
# (tracker, review AG-13 PR #23). -mod=readonly refuses anything not in go.sum, and the build itself runs with
# GOPROXY=off: only `go mod download` reaches the network.
# To move Caddy or a dependency: `go get <module>@<version> && go mod tidy` in infra/caddy/build/, check every module's
# release date (two-week rule), then build and scan again (RUNBOOK §12.1).
#
# Both bases are pinned by digest: golang 1.26.10-alpine (builder only) and alpine 3.24 (the egress-proxy base).
# *_REPO may name a mirror; the digests stay the same.
# build_ca (optional BuildKit secret): CA bundle of a TLS-intercepting proxy, trusted only while modules download and
# never stored in a layer.
ARG GO_REPO=docker.io/library/golang
ARG BASE_REPO=docker.io/library/alpine

FROM ${GO_REPO}@sha256:87c65a688975c0b1d4585d204874c45ca78354670e3a5f11368c5a492303d696 AS build
ENV CGO_ENABLED=0 \
    GOFLAGS=-mod=readonly \
    GOTOOLCHAIN=local
WORKDIR /src
COPY infra/caddy/build/go.mod infra/caddy/build/go.sum ./
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then \
        cat /etc/ssl/certs/ca-certificates.crt /run/secrets/build_ca > /tmp/ca.pem && export SSL_CERT_FILE=/tmp/ca.pem; \
    fi \
    && go mod download \
    && rm -f /tmp/ca.pem
COPY infra/caddy/build/main.go ./
RUN GOPROXY=off go build -trimpath -ldflags='-s -w' -o /out/caddy . \
    && /out/caddy version

FROM ${BASE_REPO}@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a
# The same layout as the official image: config and state under /config and /data (named volumes in compose).
# No setcap: compose runs caddy as root with cap_drop ALL + NET_BIND_SERVICE, so the binary needs no file capability.
ENV XDG_CONFIG_HOME=/config \
    XDG_DATA_HOME=/data
RUN mkdir -p /config/caddy /data/caddy /etc/caddy \
    && find / -xdev -perm /6000 -type f -exec chmod a-s {} +
COPY --from=build /out/caddy /usr/bin/caddy
WORKDIR /srv
EXPOSE 80 443 443/udp 2019
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
