#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh (RUNBOOK §7.2, spec WP-1.5c). Run as root from the repository:
#
#   scripts/restore.sh [--yes] [--db-only | --workspace-only] <backup-dir>   (env file: KK_ENV_FILE, default .env)
#
# 1. Checks, before anything stops: the backup lock; a root-owned backup folder no one else can write, holding
#    regular files only; SHA256SUMS covering exactly the four files and verifying; pg_restore can read the dump;
#    the workspace folder is a real folder of uid 10001 that uid 10001 can reach, with room for a second copy.
#    SHA256SUMS proves the copy is intact, not who made it: authenticity comes from the authenticated off-site
#    tool (restic, rclone crypt), because pg_restore runs the dump's SQL as the database owner, a superuser.
#    Then type RESTORE (or pass --yes).
# 2. Writers stop: caddy, web, api, worker, scheduler, sandbox-manager, egress-proxy and sandbox task
#    containers (they bind-mount workspace folders). postgres stays up.
# 3. Database: the current one is kept as kodekantor_pre_restore_<utc>; a new kodekantor gets its database ACL
#    (CONNECT for kodekantor_app only), the dump is loaded with pg_restore --exit-on-error --single-transaction,
#    no unknown role or superuser may appear, and an ops.restore audit event records the rollback of history.
# 4. Workspaces: the current tree is kept as <path>.pre-restore-<UTC> (inside <path> when <path> is a mount
#    point); the archive is extracted as uid/gid 10001, then the root folder goes back to 0750 and no setuid or
#    setgid bit survives.
# 5. The stack starts (migrate re-grants the runtime role) and scripts/healthcheck.sh must pass.
# On failure after step 2 the services stay stopped and the script prints what is kept and how to go back.
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
plain_path() {  # absolute path of plain characters, no . or .. part (as prepare-workspace.sh)
  [[ "$2" =~ ^/[A-Za-z0-9._/-]*[A-Za-z0-9_-]$ && "/$2/" != */./* && "/$2/" != */../* ]] \
    || fail "$1 must be an absolute path of letters, digits and . _ - / without . or .. parts (got: $2)"
}
compose=(docker compose -f infra/compose.yml --env-file "$env_file")
in_pg() { "${compose[@]}" exec -T postgres sh -c "export PGPASSWORD=\"\$POSTGRES_PASSWORD\"; $1"; }
psql_c() { echo "psql -X -A -t -v ON_ERROR_STOP=1 -h 127.0.0.1 -U kodekantor -d $1"; }

# --- 1. checks, before anything stops ---------------------------------------------------------------------
backup_dir=$(setting BACKUP_DIR); backup_dir=${backup_dir:-/var/backups/kodekantor}
workspace=$(setting WORKSPACE_HOST_PATH); workspace=${workspace:-/srv/kodekantor/workspaces}
plain_path BACKUP_DIR "$backup_dir"
plain_path WORKSPACE_HOST_PATH "$workspace"
umask 077
mkdir -p "$backup_dir"
[[ -O "$backup_dir" && ! -L "$backup_dir" && ! -L "$backup_dir/.lock" ]] || fail "$backup_dir must be a folder owned by $(id -un)"
exec 9>"$backup_dir/.lock"
flock -n 9 || fail "another backup or restore holds $backup_dir/.lock"

[[ -d "$dir" && ! -L "$dir" ]] || fail "$dir is not a folder"
dir=$(cd "$dir" && pwd -P)
name=${dir##*/}
[[ "$name" =~ ^kodekantor-([0-9]{8}T[0-9]{6}Z)$ ]] || fail "$name is not a backup folder (kodekantor-<UTC>)"
backup_time=${BASH_REMATCH[1]}
# Only its owner may have written the backup: otherwise a file could change between the check and its use.
[[ -O "$dir" && -z "$(find "$dir" -maxdepth 0 -perm /022)" ]] || fail "$name must be owned by $(id -un) and not writable by others"
[[ -f "$dir/SHA256SUMS" && ! -L "$dir/SHA256SUMS" ]] || fail "$name has no SHA256SUMS"
for file in manifest.json postgres.dump workspaces.tar.gz workspaces.manifest; do
  [[ -f "$dir/$file" && ! -L "$dir/$file" ]] || fail "$name/$file is missing or not a regular file"
  grep -Eq "^[0-9a-f]{64}  $file\$" "$dir/SHA256SUMS" || fail "SHA256SUMS does not cover $file"
done
listed=$(awk '{print $2}' "$dir/SHA256SUMS" | sort | tr '\n' ' ')
[[ "$listed" == "manifest.json postgres.dump workspaces.manifest workspaces.tar.gz " ]] \
  || fail "SHA256SUMS lists unexpected files: $listed"
(cd "$dir" && sha256sum --check --strict --quiet SHA256SUMS) || fail "checksums do not match: $name is damaged or altered"
echo "restore: $name verified (SHA256SUMS: intact, not proof of origin)"

if (( db )); then
  "${compose[@]}" exec -T postgres pg_restore --list < "$dir/postgres.dump" > "$backup_dir/.restore-list" \
    || fail "pg_restore cannot read $name/postgres.dump"
  grep -q "TABLE DATA public alembic_version" "$backup_dir/.restore-list" || fail "$name/postgres.dump has no alembic_version data"
  rm -f "$backup_dir/.restore-list"
fi

mountpoint=0
if (( ws )); then
  (( EUID == 0 )) || fail "restoring workspaces needs root (the files go back to uid 10001)"
  parent=$(dirname "$workspace")
  # Extraction runs as uid 10001: it must reach the folder, or the old tree would be moved for nothing.
  setpriv --reuid=10001 --regid=10001 --clear-groups test -x "$parent" \
    || fail "uid 10001 cannot reach $parent (needs o+x, as /srv/kodekantor 0755)"
  if [[ -e "$workspace" || -L "$workspace" ]]; then
    [[ -d "$workspace" && ! -L "$workspace" && "$(stat -c %u:%g "$workspace")" == "10001:10001" ]] \
      || fail "$workspace must be a real folder owned by 10001:10001"
    mountpoint -q "$workspace" && mountpoint=1
  fi
  # The old tree stays until the operator deletes it: the extracted copy needs room of its own.
  needed=$(awk '{ sum += $3 } END { printf "%.0f", sum * 1.1 + 1048576 }' "$dir/workspaces.manifest")
  free=$(df -P -B1 "$( (( mountpoint )) && echo "$workspace" || echo "$parent")" | awk 'NR == 2 { print $4 }')
  (( free > needed )) || fail "not enough space for a second copy of the workspaces: need $needed bytes, $free free"
fi

if (( ! yes )); then
  if (( db && ws )); then scope="the database and $workspace"
  elif (( db )); then scope="the database"
  else scope="$workspace"; fi
  read -r -p "This replaces $scope with backup $name. Type RESTORE to continue: " answer || answer=""
  [[ "$answer" == "RESTORE" ]] || fail "not confirmed; nothing changed"
fi
started=$(date +%s)
stamp=$(date -u +%Y%m%dT%H%M%SZ)

# From here on a failure leaves the services stopped; say what is kept and how to go back.
stopped=0 pre_db="" previous=""
# shellcheck disable=SC2329  # invoked by the EXIT trap below
report_failure() {
  local status=$?
  (( status != 0 && stopped )) || return 0
  echo "restore: FAILED; services are stopped." >&2
  if [[ -n "$pre_db" ]]; then
    echo "restore:   the previous database is kept as $pre_db; to go back, in psql on database postgres:" >&2
    echo "restore:   DROP DATABASE IF EXISTS kodekantor WITH (FORCE); ALTER DATABASE $pre_db RENAME TO kodekantor;" >&2
    echo "restore:   GRANT CONNECT ON DATABASE kodekantor TO kodekantor_app;" >&2
  fi
  [[ -n "$previous" ]] && echo "restore:   the previous workspaces are kept at $previous" >&2
  echo "restore:   then start the stack with: make up" >&2
}
trap report_failure EXIT

# --- 2. stop every writer ---------------------------------------------------------------------------------
echo "restore: stopping caddy, web, api, worker, scheduler, sandbox-manager, egress-proxy"
stopped=1
"${compose[@]}" --profile egress stop caddy web api worker scheduler sandbox-manager egress-proxy >/dev/null
docker ps -aq --filter label=kk.managed=1 | xargs -r docker rm -f >/dev/null

# --- 3. database ------------------------------------------------------------------------------------------
if (( db )); then
  role=$(in_pg "$(psql_c postgres) -c \"SELECT 1 FROM pg_roles WHERE rolname = 'kodekantor_app'\"" </dev/null)
  [[ "$role" == "1" ]] || fail "role kodekantor_app missing: start the stack once (make up) so postgres initialises it"
  exists=$(in_pg "$(psql_c postgres) -c \"SELECT 1 FROM pg_database WHERE datname = 'kodekantor'\"" </dev/null)
  if [[ "$exists" == "1" ]]; then
    candidate="kodekantor_pre_restore_${stamp,,}"
    in_pg "$(psql_c postgres) -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'kodekantor'\" -c 'ALTER DATABASE kodekantor RENAME TO $candidate' -c 'REVOKE CONNECT ON DATABASE $candidate FROM kodekantor_app'" </dev/null >/dev/null
    pre_db=$candidate
    echo "restore: previous database kept as $pre_db"
  fi
  in_pg "$(psql_c postgres) -c 'CREATE DATABASE kodekantor OWNER kodekantor' -c 'REVOKE ALL ON DATABASE kodekantor FROM PUBLIC' -c 'GRANT CONNECT ON DATABASE kodekantor TO kodekantor_app'" </dev/null >/dev/null
  echo "restore: loading postgres.dump"
  in_pg "pg_restore --exit-on-error --single-transaction -h 127.0.0.1 -U kodekantor -d kodekantor" < "$dir/postgres.dump"
  # A dump runs SQL as a superuser: it must not leave a new role or a superuser behind.
  roles=$(in_pg "$(psql_c postgres) -c \"SELECT string_agg(rolname || ':' || rolsuper, ',' ORDER BY rolname) FROM pg_roles WHERE rolname !~ '^pg_'\"" </dev/null)
  [[ "$roles" == "kodekantor:true,kodekantor_app:false" ]] || fail "unexpected roles after pg_restore: $roles (the dump changed the cluster)"
  restored_at=$(date -u +%Y%m%dT%H%M%SZ)
  # name and times were checked against fixed patterns above: no quoting surprises in the SQL.
  in_pg "$(psql_c kodekantor) -c \"INSERT INTO audit_events (actor_type, actor_id, action, result, metadata) VALUES ('system', 'restore.sh', 'ops.restore', 'success', jsonb_build_object('backup', '$name', 'backup_time', '$backup_time', 'restored_at', '$restored_at'))\"" </dev/null >/dev/null
fi

# --- 4. workspaces ----------------------------------------------------------------------------------------
if (( ws )); then
  if (( mountpoint )); then
    # A mount point cannot be renamed: keep the old tree in a root-only folder inside it.
    previous="$workspace/.pre-restore-$stamp"
    mkdir -m 0700 "$previous"
    find "$workspace" -mindepth 1 -maxdepth 1 ! -name ".pre-restore-*" -exec mv -t "$previous" -- {} +
  elif [[ -e "$workspace" ]]; then
    previous="$workspace.pre-restore-$stamp"
    mv -- "$workspace" "$previous"
    install -d -m 0750 -o 10001 -g 10001 "$workspace"
  else
    install -d -m 0750 -o 10001 -g 10001 "$workspace"
  fi
  [[ -n "$previous" ]] && echo "restore: previous workspaces kept at $previous"
  # As uid/gid 10001: the archive can only create what the workspace owner could. The archive is opened here
  # (the backup folder is root-only) and streamed in.
  setpriv --reuid=10001 --regid=10001 --clear-groups \
    tar --extract --gzip --preserve-permissions --file - --directory "$workspace" < "$dir/workspaces.tar.gz"
  chmod 0750 "$workspace"
  find "$workspace" -path "$workspace/.pre-restore-*" -prune -o -perm /6000 -exec chmod ug-s {} +
  expected=$(wc -l < "$dir/workspaces.manifest")  # tar escapes newlines in names: one line per entry
  actual=$(cd "$workspace" && find . -path './.pre-restore-*' -prune -o -printf . | wc -c)
  [[ "$expected" == "$actual" ]] || fail "extracted $actual entries, the manifest lists $expected"
  others=$(awk '$2 != "10001/10001"' "$dir/workspaces.manifest" | wc -l)
  (( others == 0 )) || echo "restore: warning: $others archived entries were not owned by 10001; they are now"
fi

# --- 5. start and check -----------------------------------------------------------------------------------
echo "restore: starting the stack"
"${compose[@]}" up -d --wait --wait-timeout 300 >/dev/null
scripts/healthcheck.sh --wait 180
stopped=0
seconds=$(( $(date +%s) - started ))
echo "restore: done in ${seconds}s (RTO); backup taken $backup_time (RPO: data written after it is gone)"
echo "restore: after checking, delete what was kept:"
[[ -n "$pre_db" ]] && echo "restore:   DROP DATABASE $pre_db;  (psql on database postgres)"
[[ -n "$previous" ]] && echo "restore:   rm -rf -- '$previous'"
exit 0
