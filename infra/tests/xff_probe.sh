#!/usr/bin/env bash
# Live check that api believes X-Forwarded-For only from Caddy (spec WP-1.5b; review AG-13 WP-1.5a, L1).
# Test stacks only: it clears the login rate-limit keys.
#
#   KK_ENV_FILE=infra/tests/ci.env infra/tests/xff_probe.sh        (stack running)
#
# 1. From web (also on edge) straight to api with a forged X-Forwarded-For: the rate-limit key must carry
#    web's own address, not the forged one.
# 2. Through Caddy with a forged X-Forwarded-For: Caddy replaces it, api believes Caddy, so the key carries
#    the client address Caddy saw: neither the forged one nor Caddy's own.
set -euo pipefail
cd "$(dirname "$0")/../.."

env_file=${KK_ENV_FILE:-.env}
compose=(docker compose -f infra/compose.yml --env-file "$env_file")
domain=$(sed -n 's/^APP_DOMAIN=//p' "$env_file" | tail -1)
caddy_ip=$(sed -n 's/^KK_EDGE_CADDY_IP=//p' "$env_file" | tail -1)
caddy_ip=${caddy_ip:-10.231.231.2}
body='{"email":"xff-probe@example.test","password":"not-the-password"}'
fail() { echo "xff probe: FAIL: $*"; exit 1; }

# The password stays inside the redis container (its own REDIS_PASSWORD), never on a command line here.
# stdin is closed: docker compose exec would otherwise swallow the input of a surrounding loop.
in_redis() { "${compose[@]}" exec -T redis sh -c "export REDISCLI_AUTH=\"\$REDIS_PASSWORD\"; $1" </dev/null; }
in_redis "redis-cli --scan --pattern 'kk:rl:login:*' | xargs -r redis-cli del >/dev/null"

web_ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$("${compose[@]}" ps -q web)")
web_ip=${web_ip%% *}
"${compose[@]}" exec -T web wget -q -O /dev/null --header 'X-Forwarded-For: 198.51.100.9' \
  --header 'Content-Type: application/json' --post-data "$body" http://api:8000/api/v1/auth/login || true

curl_opts=(-sS -o /dev/null -w '%{http_code}' -m 10)
[[ "$domain" == "localhost" ]] && curl_opts+=(--insecure)  # Caddy's internal CA: local runs and CI only
code=$(curl "${curl_opts[@]}" -H 'X-Forwarded-For: 198.51.100.10' -H 'Content-Type: application/json' \
  -d "$body" "https://$domain/api/v1/auth/login")
[[ "$code" == "401" || "$code" == "429" ]] || fail "login through Caddy answered $code, expected 401"

mapfile -t keys < <(in_redis "redis-cli --scan --pattern 'kk:rl:login:*'" | sed 's/^kk:rl:login://' | sort)
echo "xff probe: rate-limit keys: ${keys[*]} (web $web_ip, caddy $caddy_ip)"
for ip in "${keys[@]}"; do
  [[ "$ip" == 198.51.100.* ]] && fail "a forged X-Forwarded-For was believed ($ip)"
  [[ "$ip" == "$caddy_ip" ]] && fail "the key is Caddy's address: api does not believe Caddy's X-Forwarded-For"
done
printf '%s\n' "${keys[@]}" | grep -qx "$web_ip" || fail "no key for web ($web_ip)"
(( ${#keys[@]} == 2 )) || fail "expected 2 keys (web and the client behind Caddy), got ${#keys[@]}"
echo "xff probe: PASS (forged headers ignored; Caddy's X-Forwarded-For believed)"
