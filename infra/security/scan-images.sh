#!/usr/bin/env bash
# Trivy over every image the stack runs, then the gate (spec WP-1.5d, SECURITY_MODEL §11). The images must already
# be local: built (compose build, make sandbox-images) or pulled (compose pull --ignore-buildable).
#
#   KK_ENV_FILE=infra/tests/ci.env infra/security/scan-images.sh <report-dir>
#
# TRIVY_DOCKER_ARGS adds `docker run` arguments, e.g. a CA bundle behind a TLS proxy. One DB download, then every
# image is scanned against that same DB. A failed scan leaves no report, and the gate blocks on a missing report.
set -euo pipefail
cd "$(dirname "$0")/../.."

out=${1:?usage: infra/security/scan-images.sh <report-dir>}
env_file=${KK_ENV_FILE:-infra/tests/ci.env}
trivy_image=aquasec/trivy:0.75.0@sha256:af6acf9a6b85dfe389a1941505c0ce9efef52a4719635e1a962f022a3d855daa
read -ra extra <<< "${TRIVY_DOCKER_ARGS:-}"

mkdir -p "$out"
out=$(cd "$out" && pwd)
docker compose -f infra/compose.yml --env-file "$env_file" --profile egress config --format json \
  | python3 infra/security/scan_gate.py images > "$out/images.txt"
echo "scan: $(wc -l < "$out/images.txt") images"

trivy() {
  docker run --rm "${extra[@]}" -v /var/run/docker.sock:/var/run/docker.sock -v kk-trivy-cache:/root/.cache \
    -v "$out:/out" "$trivy_image" "$@"
}
trivy image --download-db-only --no-progress

n=0
while IFS= read -r image; do
  n=$((n + 1))
  echo "scan: $image"
  # --image-src docker: only the local image, never a registry copy of something else with the same name.
  trivy image --skip-db-update --no-progress --quiet --image-src docker --scanners vuln \
    --severity HIGH,CRITICAL --ignorefile '' --format json --output "/out/report-$n.json" "$image" \
    </dev/null || echo "scan: trivy failed for $image (the gate will block on its missing report)"
done < "$out/images.txt"

python3 infra/security/scan_gate.py trivy --images "$out/images.txt" --reports "$out"
