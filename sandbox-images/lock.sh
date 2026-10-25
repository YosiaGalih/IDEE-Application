#!/usr/bin/env bash
# Regenerate python/requirements.txt (exact versions + hashes for every platform) from requirements.in.
# Same release-age cut-off and security exceptions as backend/pyproject.toml.
set -euo pipefail
cd "$(dirname "$0")/python"
uv pip compile requirements.in --universal --generate-hashes --python-version 3.12 \
  --exclude-newer 2026-10-10T00:00:00Z --exclude-newer-package certifi=2026-10-24T00:00:00Z \
  --upgrade --no-header -o requirements.txt
