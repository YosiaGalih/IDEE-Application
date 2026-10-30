#!/usr/bin/env bash
# Regenerate uv-requirements.txt: the uv release used inside image builds, pinned with hashes for every
# platform. Same release-age cut-off as backend/pyproject.toml; keep the version equal to CI's setup-uv.
# uv itself overrides the cut-off: 0.13.4 (20 Oct) fixes PYSEC-2026-4321, and security fixes win over the
# age rule per package (as certifi in backend/pyproject.toml; DECISIONS.md EX-01, closed).
set -euo pipefail
cd "$(dirname "$0")"
echo "uv==0.13.4" | uv pip compile - --universal --generate-hashes --python-version 3.12 \
  --exclude-newer 2026-10-10T00:00:00Z --exclude-newer-package uv=2026-10-21T00:00:00Z \
  --no-header -o uv-requirements.txt
