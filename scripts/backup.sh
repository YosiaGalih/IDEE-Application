#!/usr/bin/env bash
# Daily backup (RUNBOOK §7.1, spec WP-1.5c). Run as root from the repository, e.g. by cron at 02:30:
#
#   scripts/backup.sh [--quiesce]          (env file: KK_ENV_FILE, default .env; or: make backup)
#
# Writes $BACKUP_DIR/kodekantor-<UTC>/ (0700, files 0600):
#   postgres.dump        pg_dump --format=custom, checked with pg_restore --list
#   workspaces.tar.gz    WORKSPACE_HOST_PATH with numeric owners; no node_modules, .venv, __pycache__, nor the
#                        per-project uploads/ and .kk-runs/
#   workspaces.manifest  the archive's own listing: mode, uid/gid, size, mtime, path
#   manifest.json        time, git commit, image tag, alembic revision, counts, sizes
#   SHA256SUMS           checksums of the four files above
# The backup is assembled in kodekantor-<UTC>.partial/ and renamed only when complete. Then backups older
# than BACKUP_RETENTION_DAYS are removed: only folders named exactly kodekantor-YYYYMMDDTHHMMSSZ.
# .env and other secrets are never copied. --quiesce stops api, worker and scheduler while the database
# and workspaces are copied (upgrades); without it the dump is consistent but the workspace tar is live.
set -euo pipefail
cd "$(dirname "$0")/.."

quiesce=0
case "${1:-}" in
  "") ;;
  --quiesce) quiesce=1 ;;
  *) echo "usage: scripts/backup.sh [--quiesce]" >&2; exit 2 ;;
esac

env_file=${KK_ENV_FILE:-.env}
fail() { echo "backup: $*" >&2; exit 1; }
[[ -f "$env_file" ]] || fail "$env_file not found"
setting() { sed -n "s/^$1=//p" "$env_file" | tail -1; }
plain_path() {  # absolute path of plain characters, no . or .. part (same rule as prepare-workspace.sh)
  [[ "$2" =~ ^/[A-Za-z0-9._/-]*[A-Za-z0-9_-]$ && "/$2/" != */./* && "/$2/" != */../* ]] \
    || fail "$1 must be an absolute path of letters, digits and . _ - / without . or .. parts (got: $2)"
}

backup_dir=$(setting BACKUP_DIR); backup_dir=${backup_dir:-/var/backups/kodekantor}
retention=$(setting BACKUP_RETENTION_DAYS); retention=${retention:-14}
workspace=$(setting WORKSPACE_HOST_PATH); workspace=${workspace:-/srv/kodekantor/workspaces}
image_tag=$(setting KK_IMAGE_TAG); image_tag=${image_tag:-dev}
plain_path BACKUP_DIR "$backup_dir"
plain_path WORKSPACE_HOST_PATH "$workspace"
[[ "$retention" =~ ^[1-9][0-9]{0,3}$ ]] || fail "BACKUP_RETENTION_DAYS must be a whole number of days (got: $retention)"
[[ -d "$workspace" ]] || fail "$workspace does not exist"

compose=(docker compose -f infra/compose.yml --env-file "$env_file")
# In the postgres container, as the owner; the password comes from that container's own environment.
in_pg() { "${compose[@]}" exec -T postgres sh -c "export PGPASSWORD=\"\$POSTGRES_PASSWORD\"; $1" </dev/null; }
query() { in_pg "psql -X -A -t -F '|' -v ON_ERROR_STOP=1 -h 127.0.0.1 -U kodekantor -d kodekantor -c \"$1\""; }

umask 077
mkdir -p "$backup_dir"
chmod 0700 "$backup_dir"
exec 9>"$backup_dir/.lock"
flock -n 9 || fail "another backup or restore holds $backup_dir/.lock"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
final="$backup_dir/kodekantor-$stamp"
work="$final.partial"
[[ ! -e "$final" && ! -e "$work" ]] || fail "$final already exists"
mkdir "$work"

stopped=0
cleanup() {
  local status=$?
  if (( stopped )); then "${compose[@]}" start api worker scheduler >/dev/null 2>&1 || true; fi
  if (( status != 0 )); then rm -rf -- "$work"; echo "backup: failed, nothing kept" >&2; fi
}
trap cleanup EXIT

if (( quiesce )); then
  echo "backup: stopping api, worker and scheduler"
  stopped=1
  "${compose[@]}" stop api worker scheduler >/dev/null
fi

echo "backup: database -> postgres.dump"
in_pg "pg_dump --format=custom -h 127.0.0.1 -U kodekantor -d kodekantor" > "$work/postgres.dump"
"${compose[@]}" exec -T postgres pg_restore --list < "$work/postgres.dump" > "$work/.dump-list"
grep -q "TABLE DATA public alembic_version" "$work/.dump-list" || fail "pg_restore --list shows no alembic_version data"
rm -f "$work/.dump-list"
IFS='|' read -r revision users projects tasks audits pg_version < <(query \
  "SELECT (SELECT string_agg(version_num, ',') FROM alembic_version), (SELECT count(*) FROM users), (SELECT count(*) FROM projects), (SELECT count(*) FROM tasks), (SELECT count(*) FROM audit_events), current_setting('server_version')")
[[ "$revision" =~ ^[0-9a-z_,]+$ ]] || fail "no readable alembic revision in the database (got: $revision)"
for count in "$users" "$projects" "$tasks" "$audits"; do
  [[ "$count" =~ ^[0-9]+$ ]] || fail "could not count rows in the database (got: $count)"
done
[[ "$pg_version" =~ ^[0-9A-Za-z.\ ()_-]+$ ]] || fail "unexpected server_version: $pg_version"

echo "backup: $workspace -> workspaces.tar.gz"
# posix (pax) format: keeps sub-second mtimes and long names; GNU's default keeps whole seconds only.
tar --create --gzip --format=posix --numeric-owner --file "$work/workspaces.tar.gz" --directory "$workspace" \
  --exclude=node_modules --exclude=.venv --exclude=__pycache__ \
  --no-wildcards-match-slash --exclude='./*/uploads' --exclude='./*/.kk-runs' .  # project level only
tar --list --verbose --gzip --numeric-owner --file "$work/workspaces.tar.gz" > "$work/workspaces.manifest"

if (( stopped )); then
  "${compose[@]}" start api worker scheduler >/dev/null
  stopped=0
fi

size() { stat -c %s "$work/$1"; }
commit=$(git rev-parse HEAD 2>/dev/null || echo unknown)
cat > "$work/manifest.json" <<JSON
{
  "created_at": "$stamp",
  "git_commit": "$commit",
  "image_tag": "$image_tag",
  "alembic_revision": "$revision",
  "postgres_version": "$pg_version",
  "counts": {"users": $users, "projects": $projects, "tasks": $tasks, "audit_events": $audits},
  "sizes": {"postgres.dump": $(size postgres.dump), "workspaces.tar.gz": $(size workspaces.tar.gz), "workspaces.manifest": $(size workspaces.manifest)},
  "workspace_path": "$workspace",
  "quiesced": $( (( quiesce )) && echo true || echo false ),
  "retention_days": $retention
}
JSON
(cd "$work" && sha256sum postgres.dump workspaces.tar.gz workspaces.manifest manifest.json > SHA256SUMS)
chmod 0600 "$work"/*
mv "$work" "$final"
echo "backup: $final complete"

# Retention: names carry the UTC time, so compare names; never touch anything else in BACKUP_DIR.
cutoff=$(date -u -d "-$retention days" +%Y%m%dT%H%M%SZ)
partial_cutoff=$(date -u -d "-1 day" +%Y%m%dT%H%M%SZ)
for old in "$backup_dir"/kodekantor-*; do
  name=${old##*/}
  if [[ "$name" =~ ^kodekantor-([0-9]{8}T[0-9]{6}Z)$ && "${BASH_REMATCH[1]}" < "$cutoff" ]] \
    || [[ "$name" =~ ^kodekantor-([0-9]{8}T[0-9]{6}Z)\.partial$ && "${BASH_REMATCH[1]}" < "$partial_cutoff" ]]; then
    rm -rf -- "$old"
    echo "backup: removed $name (older than retention)"
  fi
done
