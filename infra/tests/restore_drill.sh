#!/usr/bin/env bash
# Phase-1 restore drill (spec WP-1.5c; WP-1.5 "drill restore lulus"). Test stacks only: it replaces the
# database and the workspaces of the stack in KK_ENV_FILE. Needs root and the running stack.
#
#   KK_ENV_FILE=infra/tests/ci.env infra/tests/restore_drill.sh
#
# seed (audit marker, a git project owned by 10001, files backup must skip) -> backup -> damage ->
# restore -> the database and every kept file are exactly as at backup time; then a backup with one changed
# byte must be refused before anything stops. The operational drill on a staging VM stays RUNBOOK §7.3.
set -euo pipefail
cd "$(dirname "$0")/../.."

env_file=${KK_ENV_FILE:-.env}
compose=(docker compose -f infra/compose.yml --env-file "$env_file")
workspace=$(sed -n 's/^WORKSPACE_HOST_PATH=//p' "$env_file" | tail -1)
workspace=${workspace:-/srv/kodekantor/workspaces}
fail() { echo "restore drill: FAIL: $*"; exit 1; }
pass() { echo "restore drill: PASS: $*"; }
trap 'echo "restore drill: FAIL: command at line $LINENO exited $?"' ERR

work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
drill_env="$work/drill.env"
cat "$env_file" > "$drill_env"
printf 'BACKUP_DIR=%s/backups\nBACKUP_RETENTION_DAYS=14\n' "$work" >> "$drill_env"
export KK_ENV_FILE="$drill_env"

sql_in() { "${compose[@]}" exec -T postgres sh -c "export PGPASSWORD=\"\$POSTGRES_PASSWORD\"; psql -X -A -t -v ON_ERROR_STOP=1 -h 127.0.0.1 -U kodekantor -d $1 -c \"$2\"" </dev/null; }
sql() { sql_in kodekantor "$1"; }
as_owner() { setpriv --reuid=10001 --regid=10001 --clear-groups env HOME=/tmp "$@"; }
# Everything backup keeps, with nanosecond mtimes and content hashes.
snapshot() {
  (cd "$workspace" && {
    find . -path './*/uploads' -prune -o -path './*/.kk-runs' -prune -o -name node_modules -prune \
      -o -printf '%m %U:%G %s %T@ %p\n'
    find . -path './*/uploads' -prune -o -path './*/.kk-runs' -prune -o -name node_modules -prune \
      -o -type f -print0 | xargs -0 -r sha256sum
  } | LC_ALL=C sort)
}

# --- seed -------------------------------------------------------------------------------------------------
project="$workspace/drill-$$"
as_owner mkdir -p "$project/repo/node_modules/x" "$project/repo/uploads" "$project/uploads" "$project/.kk-runs/r1"
as_owner git -C "$project/repo" -c init.defaultBranch=main init -q
echo hello | as_owner tee "$project/repo/file with space.txt" >/dev/null
echo 'print(1)' | as_owner tee "$project/repo/app.py" >/dev/null
as_owner chmod 0750 "$project/repo/app.py"
echo kept | as_owner tee "$project/repo/uploads/user-file.txt" >/dev/null
echo skipped | as_owner tee "$project/repo/node_modules/x/i.js" "$project/uploads/import.zip" "$project/.kk-runs/r1/out" >/dev/null
as_owner git -C "$project/repo" -c user.email=drill@example.test -c user.name=drill add -A .
as_owner git -C "$project/repo" -c user.email=drill@example.test -c user.name=drill commit -qm drill
sql "INSERT INTO audit_events (actor_type, actor_id, action, result) VALUES ('system', 'drill', 'drill.marker', 'success')" >/dev/null
snapshot > "$work/before.txt"

# --- backup -----------------------------------------------------------------------------------------------
scripts/backup.sh
backup=$(find "$work/backups" -maxdepth 1 -name 'kodekantor-*' -type d | sort | tail -1)
[[ -n "$backup" ]] || fail "no backup folder"
[[ "$(stat -c %a "$backup")" == 700 ]] || fail "backup folder is not 0700"
[[ "$(stat -c %a "$backup"/* | sort -u)" == 600 ]] || fail "backup files are not 0600"
(cd "$backup" && sha256sum --check --strict --quiet SHA256SUMS) || fail "SHA256SUMS does not verify"
manifest_audits=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["counts"]["audit_events"])' "$backup/manifest.json")
[[ "$manifest_audits" == "$(sql 'SELECT count(*) FROM audit_events')" ]] || fail "manifest counts differ from the database"
if grep -qE 'node_modules|/uploads/import\.zip|\.kk-runs' "$backup/workspaces.manifest"; then fail "excluded files were archived"; fi
grep -q 'repo/uploads/user-file.txt' "$backup/workspaces.manifest" || fail "a user folder named uploads was skipped"
pass "backup $(basename "$backup"): files, modes, checksums, counts, exclusions"

# --- damage -----------------------------------------------------------------------------------------------
sql "INSERT INTO audit_events (actor_type, actor_id, action, result) VALUES ('system', 'drill', 'drill.damage', 'success')" >/dev/null
echo changed | as_owner tee -a "$project/repo/file with space.txt" >/dev/null
as_owner rm -- "$project/repo/app.py"
echo new | as_owner tee "$project/repo/new.txt" >/dev/null

# --- restore ----------------------------------------------------------------------------------------------
scripts/restore.sh --yes "$backup"
snapshot > "$work/after.txt"
diff "$work/before.txt" "$work/after.txt" >/dev/null || fail "workspace differs from the backup (diff $work/before.txt $work/after.txt)"
[[ -z "$(find "$workspace" ! -uid 10001 -print -quit)" ]] || fail "restored files not owned by 10001"
as_owner git -C "$project/repo" fsck --no-progress >/dev/null || fail "git fsck failed on the restored repo"
[[ "$(sql "SELECT count(*) FROM audit_events WHERE action = 'drill.damage'")" == 0 ]] || fail "damage survived the restore"
[[ "$(sql "SELECT count(*) FROM audit_events WHERE action = 'drill.marker'")" -ge 1 ]] || fail "marker lost"
[[ "$(sql "SELECT count(*) FROM audit_events WHERE action = 'ops.restore' AND metadata->>'backup' = '$(basename "$backup")'")" == 1 ]] \
  || fail "no ops.restore audit event for this backup"
[[ "$(sql 'SELECT count(*) FROM audit_events')" == $((manifest_audits + 1)) ]] || fail "audit rows are not backup + ops.restore"
previous=$(find "$(dirname "$workspace")" -maxdepth 1 -name "$(basename "$workspace").pre-restore-*" -type d | sort | tail -1)
[[ -f "$previous/drill-$$/repo/new.txt" ]] || fail "pre-restore copy does not hold the damaged state"
pre_db=$(sql_in postgres "SELECT datname FROM pg_database WHERE datname LIKE 'kodekantor_pre_restore_%' ORDER BY datname DESC LIMIT 1")
[[ -n "$pre_db" ]] || fail "the previous database was not kept"
[[ "$(sql_in "$pre_db" "SELECT count(*) FROM audit_events WHERE action = 'drill.damage'")" == 1 ]] \
  || fail "$pre_db does not hold the damaged state"
pass "restore: database and workspace equal the backup (modes, owners, mtimes, sha256), git fsck clean, ops.restore recorded, previous database and workspace kept"

# --- a changed byte is refused before anything stops ------------------------------------------------------
tampered="$work/backups/kodekantor-20000101T000000Z"
cp -a "$backup" "$tampered"
printf 'X' | dd of="$tampered/workspaces.tar.gz" bs=1 seek=100 conv=notrunc status=none
api_before=$("${compose[@]}" ps -q --status running api)
if scripts/restore.sh --yes "$tampered" >"$work/tampered.log" 2>&1; then fail "a tampered backup was restored"; fi
grep -q "checksums do not match" "$work/tampered.log" || fail "tampered backup refused for another reason: $(cat "$work/tampered.log")"
[[ "$("${compose[@]}" ps -q --status running api)" == "$api_before" && -n "$api_before" ]] || fail "services stopped for a refused restore"
pass "tampered backup refused before any service stopped"

sql_in postgres "DROP DATABASE $pre_db" >/dev/null
rm -rf -- "$previous" "$project"
echo "restore drill: all checks passed"
