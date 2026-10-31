#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh (RUNBOOK §7.2, spec WP-1.5c). Run as root from the repository:
#
#   scripts/restore.sh [--yes] [--db-only | --workspace-only] <backup-dir>   (env file: KK_ENV_FILE, default .env)
#
# 1. The backup is untrusted until checked: SHA256SUMS must cover exactly the files this restore needs and
#    must verify; nothing is stopped or changed before that. Then type RESTORE (or pass --yes).
# 2. Writers stop: caddy, web, api, worker, scheduler, sandbox-manager, egress-proxy and sandbox task
#    containers (they bind-mount workspace folders). postgres stays up.
# 3. Database: the runtime role must exist; the database is dropped and recreated, its database-level ACL
#    restored (CONNECT for kodekantor_app only; it is lost with DROP), the dump loaded with
#    pg_restore --exit-on-error, and an ops.restore audit event records that history was rolled back.
# 4. Workspaces: the current folder moves to <path>.pre-restore-<UTC> (kept until the operator deletes it); the
#    archive is extracted into a fresh 10001:10001 0750 folder as uid/gid 10001, so a tampered archive cannot
#    write where uid 10001 cannot.
# 5. The stack starts (migrate re-grants the runtime role) and scripts/healthcheck.sh must pass.
# Redis and Caddy's certificates are not restored (RUNBOOK §7.2, DEPLOYMENT §4).
set -euo pipefail
cd "$(dirname "$0")/.."

usage() { echo "usage: scripts/restore.sh [--yes] [--db-only | --workspace-only] <backup-dir>" >&2; exit 2; }
yes=0 db=1 ws=1 only="" dir=""
for arg in "$@"; do
  case "$arg" in
    --yes) yes=1 ;;
    --db-only) [[ -z "$only" ]] || usage; only=db; ws=0 ;;
    --workspace-only) [[ -z "$only" ]] || usage; only=ws; db=0 ;;
    -*) usage ;;
    *) [[ -z "$dir" ]] || usage; dir=$arg ;;
  esac
done
[[ -n "$dir" ]] || usage

env_file=${KK_ENV_FILE:-.env}
fail() { echo "restore: $*" >&2; exit 1; }
[[ -f "$env_file" ]] || fail "$env_file not found"
setting() { sed -n "s/^$1=//p" "$env_file" | tail -1; }

# --- 1. check the backup before touching anything ---------------------------------------------------------
[[ -d "$dir" ]] || fail "$dir is not a folder"
dir=$(cd "$dir" && pwd)
name=${dir##*/}
[[ "$name" =~ ^kodekantor-([0-9]{8}T[0-9]{6}Z)$ ]] || fail "$name is not a backup folder (kodekantor-<UTC>)"
backup_time=${BASH_REMATCH[1]}
needed=(manifest.json)
(( db )) && needed+=(postgres.dump)
(( ws )) && needed+=(workspaces.tar.gz workspaces.manifest)
[[ -f "$dir/SHA256SUMS" ]] || fail "$name has no SHA256SUMS"
for file in "${needed[@]}"; do
  grep -Eq "^[0-9a-f]{64}  $file\$" "$dir/SHA256SUMS" || fail "SHA256SUMS does not cover $file"
done
listed=$(awk '{print $2}' "$dir/SHA256SUMS" | sort | tr '\n' ' ')
[[ "$listed" == "manifest.json postgres.dump workspaces.manifest workspaces.tar.gz " ]] \
  || fail "SHA256SUMS lists unexpected files: $listed"
(cd "$dir" && sha256sum --check --strict --quiet SHA256SUMS) || fail "checksums do not match: $name is damaged or altered"
echo "restore: $name verified (SHA256SUMS)"

workspace=$(setting WORKSPACE_HOST_PATH); workspace=${workspace:-/srv/kodekantor/workspaces}
[[ "$workspace" =~ ^/[A-Za-z0-9._/-]*[A-Za-z0-9_-]$ && "/$workspace/" != */./* && "/$workspace/" != */../* ]] \
  || fail "WORKSPACE_HOST_PATH must be an absolute path of plain characters (got: $workspace)"
if (( ws )); then
  (( EUID == 0 )) || fail "restoring workspaces needs root (the files go back to uid 10001)"
  # Extraction runs as uid 10001: it must be able to reach the folder, or the old one would already be moved.
  setpriv --reuid=10001 --regid=10001 --clear-groups test -x "$(dirname "$workspace")" \
    || fail "uid 10001 cannot reach $(dirname "$workspace") (needs o+x, as /srv/kodekantor 0755)"
fi

if (( ! yes )); then
  if (( db && ws )); then scope="the database and $workspace"
  elif (( db )); then scope="the database"
  else scope="$workspace"; fi
  read -r -p "This replaces $scope with backup $name. Type RESTORE to continue: " answer || answer=""
  [[ "$answer" == "RESTORE" ]] || fail "not confirmed; nothing changed"
fi

backup_dir=$(setting BACKUP_DIR); backup_dir=${backup_dir:-/var/backups/kodekantor}
compose=(docker compose -f infra/compose.yml --env-file "$env_file")
in_pg() { "${compose[@]}" exec -T postgres sh -c "export PGPASSWORD=\"\$POSTGRES_PASSWORD\"; $1"; }
psql_c() { echo "psql -X -A -t -v ON_ERROR_STOP=1 -h 127.0.0.1 -U kodekantor -d $1"; }
if [[ -d "$backup_dir" ]]; then
  exec 9>"$backup_dir/.lock"
  flock -n 9 || fail "another backup or restore holds $backup_dir/.lock"
fi
started=$(date +%s)

# --- 2. stop every writer ---------------------------------------------------------------------------------
echo "restore: stopping caddy, web, api, worker, scheduler, sandbox-manager, egress-proxy"
"${compose[@]}" --profile egress stop caddy web api worker scheduler sandbox-manager egress-proxy >/dev/null
docker ps -aq --filter label=kk.managed=1 | xargs -r docker rm -f >/dev/null

# --- 3. database ------------------------------------------------------------------------------------------
if (( db )); then
  role=$(in_pg "$(psql_c postgres) -c \"SELECT 1 FROM pg_roles WHERE rolname = 'kodekantor_app'\"" </dev/null)
  [[ "$role" == "1" ]] || fail "role kodekantor_app missing: start the stack once (make up) so postgres initialises it"
  echo "restore: recreating database kodekantor"
  in_pg "$(psql_c postgres) -c 'DROP DATABASE IF EXISTS kodekantor WITH (FORCE)' -c 'CREATE DATABASE kodekantor OWNER kodekantor' -c 'REVOKE ALL ON DATABASE kodekantor FROM PUBLIC' -c 'GRANT CONNECT ON DATABASE kodekantor TO kodekantor_app'" </dev/null >/dev/null
  echo "restore: loading postgres.dump"
  in_pg "pg_restore --exit-on-error --single-transaction -h 127.0.0.1 -U kodekantor -d kodekantor" < "$dir/postgres.dump"
  restored_at=$(date -u +%Y%m%dT%H%M%SZ)
  # name and times were checked against fixed patterns above: no quoting surprises in the SQL.
  in_pg "$(psql_c kodekantor) -c \"INSERT INTO audit_events (actor_type, actor_id, action, result, metadata) VALUES ('system', 'restore.sh', 'ops.restore', 'success', jsonb_build_object('backup', '$name', 'backup_time', '$backup_time', 'restored_at', '$restored_at'))\"" </dev/null >/dev/null
fi

# --- 4. workspaces ----------------------------------------------------------------------------------------
if (( ws )); then
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  previous="$workspace.pre-restore-$stamp"
  if [[ -e "$workspace" ]]; then
    mv -- "$workspace" "$previous"
    echo "restore: previous workspaces kept at $previous"
  fi
  install -d -m 0750 -o 10001 -g 10001 "$workspace"
  # As uid/gid 10001: the archive can only create what the workspace owner could.
  # The archive is opened here (the backup folder is root-only) and streamed in.
  setpriv --reuid=10001 --regid=10001 --clear-groups \
    tar --extract --gzip --preserve-permissions --file - --directory "$workspace" < "$dir/workspaces.tar.gz"
  expected=$(wc -l < "$dir/workspaces.manifest")
  actual=$(cd "$workspace" && find . | wc -l)
  [[ "$expected" == "$actual" ]] || fail "extracted $actual entries, the manifest lists $expected"
  others=$(awk '$2 != "10001/10001"' "$dir/workspaces.manifest" | wc -l)
  (( others == 0 )) || echo "restore: warning: $others archived entries were not owned by 10001; they are now"
fi

# --- 5. start and check -----------------------------------------------------------------------------------
echo "restore: starting the stack"
"${compose[@]}" up -d --wait --wait-timeout 300 >/dev/null
scripts/healthcheck.sh --wait 180
seconds=$(( $(date +%s) - started ))
echo "restore: done in ${seconds}s (RTO); backup taken $backup_time (RPO: data written after it is gone)"
(( ws )) && [[ -n "${previous:-}" && -e "$previous" ]] && echo "restore: after checking, delete $previous"
exit 0
