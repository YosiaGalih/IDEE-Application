#!/usr/bin/env bash
# Regenerate uv-requirements.txt: the uv release used inside image builds, pinned with hashes for every
# platform. Same release-age cut-off as backend/pyproject.toml; keep the version equal to CI's setup-uv.
set -euo pipefail
cd "$(dirname "$0")"
echo "uv==0.11.32" | uv pip compile - --universal --generate-hashes --python-version 3.12 \
  --exclude-newer 2026-10-10T00:00:00Z --no-header -o uv-requirements.txt
