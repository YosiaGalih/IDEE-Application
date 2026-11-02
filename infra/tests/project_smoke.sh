#!/usr/bin/env bash
# Live check of WP-2.1a/b on the production stack (fresh local stack only: it creates the single owner).
# An owner signs in through Caddy and creates a project from a template; the repository must exist in the
# workspace volume with its first commit, which needs git in the backend image and a workspace writable by
# uid 10001.
#
#   KK_ENV_FILE=infra/tests/ci.env infra/tests/project_smoke.sh        (stack running)
set -euo pipefail
cd "$(dirname "$0")/../.."

fail() {
  echo "project smoke: FAIL: $*" >&2
  exit 1
}

env_file=${KK_ENV_FILE:-.env}
compose=(docker compose -f infra/compose.yml --env-file "$env_file")
domain=$(sed -n 's/^APP_DOMAIN=//p' "$env_file" | tail -1)
# It takes the platform's only owner slot with a password nobody keeps: never on a real deployment.
[[ "$domain" == "localhost" ]] || fail "refusing to run against APP_DOMAIN=$domain (local stacks only)"
base="https://$domain/api/v1"

jar=$(mktemp)
trap 'rm -f "$jar"' EXIT
curl_opts=(-sS -m 60 -b "$jar" -c "$jar" --insecure) # Caddy's internal CA on localhost

email=smoke-owner@example.test
password=$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')
# The password goes over stdin, never argv (app/cli.py, and curl's body below).
printf '%s\n' "$password" | "${compose[@]}" exec -T api \
  python -m app.cli create-owner --email "$email" --display-name "Smoke Owner" --password-stdin ||
  fail "create-owner failed (an owner already exists on this stack?)"

code=$(printf '{"email":"%s","password":"%s","remember_me":false}' "$email" "$password" |
  curl "${curl_opts[@]}" -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
    --data-binary @- "$base/auth/login") || fail "login request failed"
[[ "$code" == "200" ]] || fail "login answered $code"
csrf=$(awk '$6 == "kk_csrf" {print $7}' "$jar")
[[ -n "$csrf" ]] || fail "no kk_csrf cookie after login"

response=$(curl "${curl_opts[@]}" -w '\n%{http_code}' -H 'Content-Type: application/json' \
  -H "X-CSRF-Token: $csrf" -H "Idempotency-Key: $(cat /proc/sys/kernel/random/uuid)" \
  -d '{"name":"Smoke Project","source":{"type":"template","template_id":"python-basic"}}' "$base/projects") ||
  fail "create request failed"
code=${response##*$'\n'}
body=${response%$'\n'*}
[[ "$code" == "201" ]] || fail "create answered $code: $body"
fields=$(python3 -c '
import json, sys
data = json.load(sys.stdin)["data"]
print(data["id"], data["stats"]["last_commit"])' <<<"$body") || fail "create answer is not the expected JSON: $body"
read -r id last_commit <<<"$fields"
# The id goes into a shell command in the container: only a UUID may.
[[ "$id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || fail "not a project id: $id"
[[ "$last_commit" =~ ^[0-9a-f]{40}$ ]] || fail "no first commit reported: $last_commit"

git_dir="\$WORKSPACE_ROOT/$id/repo/.git"
history=$("${compose[@]}" exec -T api sh -c "git --git-dir \"$git_dir\" log --format='%H %s'" </dev/null) ||
  fail "no repository for $id in the workspace volume"
[[ "$history" == "$last_commit Initial commit from template python-basic" ]] ||
  fail "the repository does not match the answer: $history (reported $last_commit)"

# The project is in the list too: the same scope rule the API applies everywhere.
listed=$(curl "${curl_opts[@]}" "$base/projects") || fail "list request failed"
python3 -c '
import json, sys
answer = json.load(sys.stdin)
ids = [p["id"] for p in answer["data"]]
sys.exit(0 if sys.argv[1] in ids and answer["meta"]["total"] == len(ids) else 1)' "$id" <<<"$listed" ||
  fail "the list does not show $id: $listed"
echo "project smoke: created $id from python-basic, first commit $last_commit"

# WP-2.1b: a project from an archive, through Caddy's 210 MB import route and a real worker job.
archive=$(mktemp --suffix=.zip)
trap 'rm -f "$jar" "$archive"' EXIT
python3 - "$archive" <<'PY' || fail "could not build the smoke archive"
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], "w") as z:
    z.writestr("smoke-main/README.md", "# Smoke\n")
    z.writestr("smoke-main/app.py", "print('ok')\n")
PY
response=$(curl "${curl_opts[@]}" -w '\n%{http_code}' -H 'Content-Type: application/json' \
  -H "X-CSRF-Token: $csrf" -H "Idempotency-Key: $(cat /proc/sys/kernel/random/uuid)" \
  -d '{"name":"Smoke Archive","source":{"type":"upload"}}' "$base/projects") || fail "create (upload) request failed"
code=${response##*$'\n'}
body=${response%$'\n'*}
[[ "$code" == "201" ]] || fail "create (upload) answered $code: $body"
upload_id=$(python3 -c 'import json, sys; print(json.load(sys.stdin)["data"]["id"])' <<<"$body") ||
  fail "create (upload) answer is not the expected JSON: $body"
[[ "$upload_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || fail "not a project id: $upload_id"
code=$(curl "${curl_opts[@]}" -o /dev/null -w '%{http_code}' -H "X-CSRF-Token: $csrf" \
  -H "Idempotency-Key: $(cat /proc/sys/kernel/random/uuid)" -F "file=@$archive;filename=smoke.zip" \
  "$base/projects/$upload_id/imports") || fail "upload request failed"
[[ "$code" == "202" ]] || fail "upload answered $code"
state=""
for _ in $(seq 1 60); do
  state=$(curl "${curl_opts[@]}" "$base/projects/$upload_id" |
    python3 -c 'import json, sys; r = json.load(sys.stdin)["data"]["repository"]; print(r["sync_state"], r["error"])') ||
    fail "project read failed"
  [[ "$state" == importing* ]] || break
  sleep 1
done
[[ "$state" == "ready None" ]] || fail "the import did not finish: $state"
git_dir="\$WORKSPACE_ROOT/$upload_id/repo/.git"
subject=$("${compose[@]}" exec -T api sh -c "git --git-dir \"$git_dir\" log --format=%s" </dev/null) ||
  fail "no repository for $upload_id in the workspace volume"
[[ "$subject" == "Import from archive smoke.zip" ]] || fail "unexpected import history: $subject"
echo "project smoke: imported $upload_id from smoke.zip through the worker"
