#!/usr/bin/env bash
# Live check of WP-2.1a on the production stack (fresh stack only: it creates the single owner).
# An owner signs in through Caddy and creates a project from a template; the repository must exist in the
# workspace volume with its first commit, which needs git in the backend image and a workspace writable by
# uid 10001.
#
#   KK_ENV_FILE=infra/tests/ci.env infra/tests/project_smoke.sh        (stack running)
set -euo pipefail
cd "$(dirname "$0")/../.."

env_file=${KK_ENV_FILE:-.env}
compose=(docker compose -f infra/compose.yml --env-file "$env_file")
domain=$(sed -n 's/^APP_DOMAIN=//p' "$env_file" | tail -1)
base="https://$domain/api/v1"
fail() { echo "project smoke: FAIL: $*"; exit 1; }

jar=$(mktemp)
trap 'rm -f "$jar"' EXIT
curl_opts=(-sS -m 60 -b "$jar" -c "$jar")
[[ "$domain" == "localhost" ]] && curl_opts+=(--insecure)  # Caddy's internal CA: local runs and CI only

email=smoke-owner@example.test
password=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')
# The password goes over stdin, never argv (app/cli.py).
printf '%s\n' "$password" | "${compose[@]}" exec -T api \
  python -m app.cli create-owner --email "$email" --display-name "Smoke Owner" --password-stdin

code=$(curl "${curl_opts[@]}" -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
  -d "{\"email\":\"$email\",\"password\":\"$password\",\"remember_me\":false}" "$base/auth/login")
[[ "$code" == "200" ]] || fail "login answered $code"
csrf=$(awk '$6 == "kk_csrf" {print $7}' "$jar")
[[ -n "$csrf" ]] || fail "no kk_csrf cookie after login"

response=$(curl "${curl_opts[@]}" -w '\n%{http_code}' -H 'Content-Type: application/json' \
  -H "X-CSRF-Token: $csrf" -H "Idempotency-Key: $(cat /proc/sys/kernel/random/uuid)" \
  -d '{"name":"Smoke Project","source":{"type":"template","template_id":"python-basic"}}' "$base/projects")
code=${response##*$'\n'}
body=${response%$'\n'*}
[[ "$code" == "201" ]] || fail "create answered $code: $body"
read -r id last_commit < <(python3 -c '
import json, sys
data = json.load(sys.stdin)["data"]
print(data["id"], data["stats"]["last_commit"])' <<<"$body")
[[ "$last_commit" =~ ^[0-9a-f]{40}$ ]] || fail "no first commit reported: $last_commit"

subject=$("${compose[@]}" exec -T api sh -c "git --git-dir \"\$WORKSPACE_ROOT/$id/repo/.git\" log --format=%s" </dev/null)
[[ "$subject" == "Initial commit from template python-basic" ]] || fail "unexpected history: $subject"
echo "project smoke: created $id from python-basic, first commit $last_commit"
