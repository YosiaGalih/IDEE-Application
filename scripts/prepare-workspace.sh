#!/usr/bin/env bash
# Create the workspace folder the stack bind-mounts: owner 10001:10001, mode 0750 (ADR-005). Needs root.
#
#   scripts/prepare-workspace.sh          (env file: KK_ENV_FILE, default .env)
#
# A typo must never chown a system folder, so only a folder for KodeKantor alone is accepted:
# - WORKSPACE_HOST_PATH is an absolute path of letters, digits and . _ - / with no "." or ".." part
#   (no quotes, spaces or "#": compose would read those differently from this script);
# - an existing path must be a real folder already owned by 10001:10001; anything else is refused.
set -euo pipefail
cd "$(dirname "$0")/.."

env_file=${KK_ENV_FILE:-.env}
fail() { echo "workspace: $*" >&2; exit 1; }

[[ -f "$env_file" ]] || fail "$env_file not found (run make setup)"
path=$(sed -n 's/^WORKSPACE_HOST_PATH=//p' "$env_file" | tail -1)
[[ -n "$path" ]] || fail "WORKSPACE_HOST_PATH missing in $env_file"
[[ "$path" =~ ^/[A-Za-z0-9._/-]*[A-Za-z0-9_-]$ ]] \
  || fail "WORKSPACE_HOST_PATH must be an absolute path of letters, digits and . _ - / (got: $path)"
[[ "/$path/" != */./* && "/$path/" != */../* ]] || fail "WORKSPACE_HOST_PATH must not contain . or .. (got: $path)"

if [[ -e "$path" || -L "$path" ]]; then
  [[ -d "$path" && ! -L "$path" ]] || fail "$path exists and is not a folder"
  owner=$(stat -c %u:%g "$path")
  [[ "$owner" == "10001:10001" ]] \
    || fail "$path exists and belongs to $owner; use a new path, or chown it to 10001:10001 yourself if it is only for KodeKantor"
  chmod 0750 "$path"
else
  install -d -m 0750 -o 10001 -g 10001 "$path"
fi
echo "workspace: $path ready (10001:10001, 0750)"
