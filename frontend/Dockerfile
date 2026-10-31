# kodekantor/web: the SPA built from frontend/, served by nginx-unprivileged (DEPLOYMENT §2, spec WP-1.4a).
# Build context: the repository root (infra/compose.yml). Runs as uid 101 on a read-only rootfs (tmpfs /tmp).
#
# Both bases are pinned by digest: node 22-trixie-slim (the digest of sandbox-images/node) and
# nginx-unprivileged 1.30-alpine. *_REPO may name a mirror; the digests stay the same.
# build_ca (optional BuildKit secret): CA bundle of a TLS-intercepting proxy, never stored in a layer.
ARG NODE_REPO=docker.io/library/node
ARG NGINX_REPO=docker.io/nginxinc/nginx-unprivileged

FROM ${NODE_REPO}@sha256:2117f713a417f3aff682ac8f5bc2ebbdf1beed51f36e67ab1e0fd3866b6d2d8f AS build
ENV NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
# --ignore-scripts: no dependency runs code at install time (supply chain, SECURITY_MODEL T8).
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -s /run/secrets/build_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/build_ca; fi \
    && npm ci --no-audit --ignore-scripts
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/public ./public
COPY frontend/src ./src
RUN npx vite build

FROM ${NGINX_REPO}@sha256:f5116ed4606f3eb015dd4a8e28cb9b351476dc0ad4bc6e9f93b19cec130cdf03
COPY frontend/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
USER 101:101
EXPOSE 8080
