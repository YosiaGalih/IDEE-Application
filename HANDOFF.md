# HANDOFF — mulai dari sini bila melanjutkan di sesi baru

File ini ditulis untuk melanjutkan pembangunan KodeKantor setelah kredit cloud habis, memakai Claude Code dengan langganan (limit sesi dan limit mingguan). Perbarui bagian "Status" setiap akhir sesi, lalu commit dan push.

Aturan kerja dan antrian unit lengkap ada di `docs/superpowers/plans/2026-10-24-continuation-under-usage-limits.md`. File ini hanya memuat posisi terakhir dan langkah berikutnya.

## Status (30 Okt 2026)

| | |
|---|---|
| `main` | WP-1.1, WP-1.2 (PR #1, #2), WP-1.3 + ADR-026 (4) (PR #3), WP-1.5a (PR #4, di-merge PO 25 Okt) |
| Branch kerja | `claude/gracious-goodall-3hjh19` (dimulai ulang dari `main` setelah PR #3 di-merge) |
| Sedang dikerjakan | PR #5 (uv 0.13.4, menutup EX-01; merge sebelum 2026-11-04). **WP-1.5b** (unit U7): egress-proxy, linter CI, L1. Lihat tracker bagian WP-1.5b |
| Utang terbuka | sign-off formal AG-13 untuk WP-1.2 dan WP-1.3. PR #3 di-merge tanpa itu; review putaran 2 menolak karena N1, lalu N1–N5 diperbaiki tetapi belum diverifikasi reviewer (U4, sesi terpisah). Dari WP-1.5a: L1 `--forwarded-allow-ips`, role owner non-superuser untuk migrate, Trivy/Semgrep sebelum G1 (tracker WP-1.5a) |
| Menunggu PO | konflik dokumen #36–#48 di `docs/superpowers/specs/2026-10-24-development-system-design.md` §8; konflik #45–#48 (WP-1.5b); merge PR #5; gate G0/G1 |

### WP-1.5a: yang sudah ada (di branch kerja)

- `infra/compose.yml`: 10 service v1 (caddy, web placeholder, api, migrate, worker, scheduler, postgres, redis, sandbox-manager, docker-socket-proxy).
  - Jaringan public/edge/app/exec/outbound sesuai ARCHITECTURE §2.
  - Hardening, limit, healthcheck, dan urutan start.
- `backend/Dockerfile`, `sandbox_manager/Dockerfile`:
  - base dipin digest;
  - `uv` dari PyPI dengan hash (`infra/build/uv-requirements.txt`, `infra/build/lock-uv.sh`);
  - uid 10001.
- `infra/caddy/Caddyfile`, `infra/web/` (placeholder nginx-unprivileged sampai WP-1.4), `infra/postgres/10-runtime-role.sh`.
- `.env.example`, `scripts/gen-secrets.sh`, `scripts/healthcheck.sh`, `Makefile` (setup, config, up, down, ps, logs, healthcheck).
- **Role DB terpisah.** migrate memakai owner; api/worker/scheduler memakai `kodekantor_app`. Perintah baru `python -m app.cli grant-runtime-role` dijalankan migrate, dan `audit_events` hanya mendapat SELECT/INSERT (`backend/app/core/db_roles.py`, `backend/tests/integration/test_db_roles.py`).
- `infra/tests/test_compose_policy.py` (18 test kebijakan) dan `infra/tests/test_prepare_workspace.py` (5), stdlib.
- `scripts/prepare-workspace.sh` (`make workspace`): menolak path yang bisa membuat salah chown folder sistem.
- `.dockerignore`: allowlist berkas yang di-COPY Dockerfile.
- Spec: `docs/superpowers/specs/2026-10-25-wp-1.5a-compose-design.md`.

**Bukti lokal** (container cloud, 25 Okt):
- `python3 -m unittest discover -s infra/tests` → 23 OK.
- Stack jalan dengan `docker compose -f infra/compose.yml --env-file infra/tests/ci.env up -d --wait` → 10 service healthy, migrate exit 0.
- `KK_ENV_FILE=infra/tests/ci.env scripts/healthcheck.sh` → live/ready/`/` 200 lewat Caddy. `/health/ready` ok untuk db, redis, sandbox_manager, migrations.
- Socket-proxy menolak `/info`, `/volumes`, `/networks`, `/events`, dan `POST /exec/x/start` (403).
- Backend: 1243 passed, 1 skipped (butuh superuser; jalan di CI); ruff, pyright, dan lint-imports bersih.
- CI PR #4 (runner GitHub): `clean-install` hijau, termasuk healthcheck lewat Caddy.

### WP-1.5a: yang belum (urutan)

Sudah selesai sejak HANDOFF pertama:
- job CI `infra-config` dan `clean-install` (`.github/workflows/infra.yml`);
- 14 mutasi compose semuanya merah;
- suite backend 1239 passed;
- plan doc, konflik #40–#43, tracker.

Review AG-13 WP-1.5a (25 Okt): **SIGNED OFF**. M1 ditutup dengan pengecualian EX-01 yang kedaluwarsa sendiri di CI; M2, L2–L5 diperbaiki dengan test dan mutan; L1 menjadi utang. Rinciannya di tracker bagian WP-1.5a.

Sisa:
1. ~~PR #4~~ di-merge 25 Okt. ~~Naikkan uv ke ≥ 0.13.4 (EX-01)~~: PR [#5](https://github.com/YosiaGalih/IDEE-Application/pull/5), CI hijau, menunggu merge.
2. WP-1.5b: setelah PR #5 di-merge, push commit WP-1.5b dan buka PR. Versi apk `tinyproxy` dipin 1.11.3-r0 dari run CI pertama; pastikan probe egress dengan TLS asli hijau di CI; tutup temuan review AG-13.
3. Berikutnya: WP-1.5c (backup/restore), WP-1.4 (frontend), WP-1.6 (G1: Trivy dan Semgrep wajib berjalan di security-scan).

Catatan build lokal di balik proxy TLS (container cloud): `docker compose ... up --build` tidak bisa membaca CA di luar folder proyek. Build image langsung lalu `up --no-build`:

```bash
B="docker build --network host --build-arg HTTPS_PROXY --build-arg BASE_REPO=mirror.gcr.io/library/python --secret id=build_ca,src=/root/.ccr/ca-bundle.crt"
$B -f backend/Dockerfile -t kodekantor/backend:dev . && $B -f sandbox_manager/Dockerfile -t kodekantor/sandbox-manager:dev .
docker compose -f infra/compose.yml --env-file infra/tests/ci.env up -d --no-build --wait
```

## Cara melanjutkan dengan langganan (bukan kredit cloud)

1. **Di mesin sendiri (disarankan; tanpa biaya cloud).**
   - Pasang git, Docker Engine ≥ 27 + Compose, uv 0.13.x, Python 3.12, PostgreSQL 16, dan Redis 7 (atau jalankan keduanya sebagai container).
   - Clone repo, `git checkout claude/gracious-goodall-3hjh19`, `git pull`.
   - Login Claude Code dengan akun langganan, lalu jalankan `claude` di folder repo. Pemakaian dihitung ke limit sesi dan limit mingguan, bukan kredit.
2. **Cek kuota di awal sesi** (`/usage` atau `/status`), lalu pilih satu unit yang muat (rencana §3).
3. **Satu sesi = satu unit, ditutup dengan commit + push hijau.** Bila limit hampir habis, commit `wip: ...` lalu push. Itu lebih baik daripada pekerjaan hilang.
4. **Perintah yang dibutuhkan** ada di `CLAUDE.md` bagian "Perintah". Untuk stack:

   ```bash
   make setup          # .env (secret acak, mode 600) + image sandbox + folder workspace (butuh root)
   # edit .env: APP_DOMAIN, APP_URL, CADDY_EMAIL, CLAUDE_MODEL_DEFAULT
   make config && make up && make healthcheck
   ```

   Untuk mencoba tanpa `.env` sungguhan: `docker compose -f infra/compose.yml --env-file infra/tests/ci.env up -d --wait`. File itu berisi nilai uji yang bukan rahasia.

## Prompt untuk memulai sesi berikutnya

```text
Baca CLAUDE.md, HANDOFF.md, dan docs/superpowers/plans/2026-10-24-continuation-under-usage-limits.md.
Lanjutkan dari daftar "Sisa" di HANDOFF.md, urut dari nomor 1.
Commit setiap langkah hijau, push sebelum sesi berakhir, dan perbarui bagian Status di HANDOFF.md.
```
