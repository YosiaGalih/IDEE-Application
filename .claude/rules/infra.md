---
paths:
  - "infra/**"
  - "sandbox-images/**"
  - "**/Dockerfile"
  - "**/compose*.yml"
  - ".github/**"
  - "Makefile"
---
# Infra, sandbox image, CI

Kontrak: `docs/ARCHITECTURE.md` §2/§12, `docs/DEPLOYMENT.md`, `docs/RUNBOOK.md`, `docs/SECURITY_MODEL.md` §4/§11, `docs/TEST_STRATEGY.md` §6.

- Hanya `caddy` yang mempublikasi port (80/443). `docker.sock` hanya di-mount ke `docker-socket-proxy`; hanya `sandbox-manager` yang bergabung ke network `exec`.
- Socket proxy: `CONTAINERS`, `IMAGES`, `POST`, `PING`, `VERSION` = 1; `INFO`, `EXEC`, `VOLUMES`, `NETWORKS`, `BUILD`, `SYSTEM` = 0 (ADR-026). `NETWORKS=1` hanya di node executor tier M/L untuk lingkungan integrasi.
- Container sandbox: uid 10001, `cap_drop: ALL`, `no-new-privileges`, rootfs read-only + tmpfs, `network_mode: none` kecuali profil `*.deps_install` ke proxy dependensi.
- Image dipin (tag + digest untuk base dan sandbox), tanpa auto-pull, SBOM terlampir pada rilis; Trivy HIGH/CRITICAL memblokir kecuali ada pengecualian tertulis di `DECISIONS.md`.
- Secret per peran proses (ADR-053): private key GitHub App dan `ANTHROPIC_ADMIN_API_KEY` hanya di `worker-integrations`; `api` tanpa akses outbound. Secret sebagai file/env, tidak pernah di image atau repo.
- Compose dijalankan dengan `--env-file .env`; `compose.dev.yml` tidak dipakai di server publik.
- CI (GitHub Actions) mengikuti tabel job di `TEST_STRATEGY.md` §6: backend-lint, backend-types, backend-test, sandbox-test, sandbox-isolation, frontend, e2e, security-scan, release, clean-install. Job ditambahkan bersama kode yang diujinya, bukan sebelumnya.
- Perubahan di `.github/workflows/**` selalu lewat PR yang direview manusia.
