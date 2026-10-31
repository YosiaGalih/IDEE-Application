#!/usr/bin/env bash
# Wait for the stack to be healthy, then check it from the outside through Caddy (RUNBOOK §2, AC-20).
#
#   scripts/healthcheck.sh [--wait SECONDS]          (env file: KK_ENV_FILE, default .env)
#
# Healthy means: all 10 services exist, every long-running one reports "healthy", migrate exited 0, and
# through Caddy /api/v1/health/live, /api/v1/health/ready and / answer 200. With SANDBOX_EGRESS=allowlist the
# egress-proxy (COMPOSE_PROFILES=egress) must run and be healthy too (ADR-007), and whenever it runs, the
# kk-egress network must be the one compose made: internal, without a host address. Exit 1 otherwise.
set -euo pipefail
cd "$(dirname "$0")/.."

env_file=${KK_ENV_FILE:-.env}
wait_s=0
if [[ "${1:-}" == "--wait" ]]; then wait_s=${2:?--wait needs seconds}; fi
compose=(docker compose -f infra/compose.yml --env-file "$env_file")
domain=$(sed -n 's/^APP_DOMAIN=//p' "$env_file" | tail -1)
[[ -n "$domain" ]] || { echo "healthcheck: APP_DOMAIN missing in $env_file" >&2; exit 1; }
egress=$(sed -n 's/^SANDBOX_EGRESS=//p' "$env_file" | tail -1)

report=$(mktemp)
trap 'rm -f "$report"' EXIT
proxy_seen=0

# compose only warns when kk-egress already exists, and then uses that network as it is.
egress_network_ok() {
  local internal project inhibit
  IFS='|' read -r internal project inhibit < <(docker network inspect kk-egress --format \
    '{{.Internal}}|{{index .Labels "com.docker.compose.project"}}|{{index .Options "com.docker.network.bridge.inhibit_ipv4"}}' \
    2>/dev/null || echo "missing||")
  local bad=0
  [[ "$internal" == "true" ]] || { echo "  kk-egress is not internal: task containers would get a route out"; bad=1; }
  [[ "$project" == "kodekantor" ]] || { echo "  kk-egress was not created by this compose project (remove it, then make up)"; bad=1; }
  [[ "$inhibit" == "true" ]] || { echo "  kk-egress lets the host keep an address on it: tasks could reach host services"; bad=1; }
  return "$bad"
}

containers_ok() {
  local bad=0 count=0 proxy=0 service health status
  while IFS='|' read -r service health status; do  # '|' is not whitespace: an empty health stays empty
    count=$((count + 1))
    [[ "$service" == "egress-proxy" ]] && proxy=1
    if [[ "$service" == "migrate" ]]; then
      [[ "$status" == Exited\ \(0\)* ]] || { echo "  migrate: $status"; bad=1; }
    elif [[ "$health" != "healthy" ]]; then
      echo "  $service: ${health:-no healthcheck} ($status)"; bad=1
    fi
  done < <("${compose[@]}" ps -a --format '{{.Service}}|{{.Health}}|{{.Status}}')
  (( count >= 10 )) || { echo "  only $count of 10 services exist"; bad=1; }
  if [[ "$egress" == "allowlist" ]] && (( ! proxy )); then
    echo "  SANDBOX_EGRESS=allowlist but egress-proxy is not running (set COMPOSE_PROFILES=egress)"; bad=1
  fi
  if (( proxy )); then
    egress_network_ok || bad=1
  fi
  proxy_seen=$proxy
  return "$bad"
}

deadline=$((SECONDS + wait_s))
until containers_ok >"$report" 2>&1; do
  if (( SECONDS >= deadline )); then
    echo "healthcheck: containers not healthy:"; cat "$report"; exit 1
  fi
  sleep 5
done
echo "healthcheck: all containers healthy, migrate exited 0"
if (( proxy_seen )) && [[ "$egress" != "allowlist" ]]; then
  echo "healthcheck: warning: egress-proxy runs while SANDBOX_EGRESS=off; stop it with: make egress-off"
fi

curl_opts=(-fsS -m 10 -o /dev/null -w '%{http_code}')
if [[ "$domain" == "localhost" ]]; then
  curl_opts+=(--insecure)  # Caddy's internal CA: local runs and CI only
fi
for path in /api/v1/health/live /api/v1/health/ready /; do
  code=$(curl "${curl_opts[@]}" "https://$domain$path") || { echo "healthcheck: GET $path failed"; exit 1; }
  echo "healthcheck: GET https://$domain$path -> $code"
done
