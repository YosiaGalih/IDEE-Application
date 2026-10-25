#!/usr/bin/env bash
# Create the env file (default .env) from .env.example with fresh random secrets (RUNBOOK §2).
#
#   scripts/gen-secrets.sh            → .env
#   KK_ENV_FILE=path scripts/gen-secrets.sh
#
# Refuses to overwrite an existing file: rotating secrets is a deliberate operation (RUNBOOK §10.5), never
# a side effect of `make setup`.
set -euo pipefail
cd "$(dirname "$0")/.."
target=${KK_ENV_FILE:-.env}

if [[ -e "$target" ]]; then
  echo "gen-secrets: $target already exists; not overwriting it" >&2
  exit 1
fi

secret() { od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; }  # 64 hex characters, safe in URLs and configs

umask 077
tmp=$(mktemp "${target}.XXXXXX")
trap 'rm -f "$tmp"' EXIT
while IFS= read -r line || [[ -n "$line" ]]; do
  if [[ "$line" == *">>> GENERATED <<<"* ]]; then
    printf '%s=%s\n' "${line%%=*}" "$(secret)"
  else
    printf '%s\n' "$line"
  fi
done < .env.example > "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$target"
trap - EXIT
echo "gen-secrets: wrote $target (mode 600); now edit the values marked '>>> SET <<<'"
