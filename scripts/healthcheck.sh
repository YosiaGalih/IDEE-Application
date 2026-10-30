#!/usr/bin/env bash
# Wait for the stack to be healthy, then check it from the outside through Caddy (RUNBOOK §2, AC-20).
#
#   scripts/healthcheck.sh [--wait SECONDS]          (env file: KK_ENV_FILE, default .env)
#
# Healthy means: all 10 services exist, every long-running one reports "healthy", migrate exited 0, and
# through Caddy /api/v1/health/live, /api/v1/health/ready and / answer 200. With SANDBOX_EGRESS=allowlist the
# egress-proxy (COMPOSE_PROFILES=egress) must run and be healthy too (ADR-007). Exit 1 otherwise.
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

curl_opts=(-fsS -m 10 -o /dev/null -w '%{http_code}')
if [[ "$domain" == "localhost" ]]; then
  curl_opts+=(--insecure)  # Caddy's internal CA: local runs and CI only
fi
for path in /api/v1/health/live /api/v1/health/ready /; do
  code=$(curl "${curl_opts[@]}" "https://$domain$path") || { echo "healthcheck: GET $path failed"; exit 1; }
  echo "healthcheck: GET https://$domain$path -> $code"
done
