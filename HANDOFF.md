# HANDOFF — mulai dari sini bila melanjutkan di sesi baru

File ini ditulis untuk melanjutkan pembangunan KodeKantor setelah kredit cloud habis, memakai Claude Code dengan langganan (limit sesi dan limit mingguan). Perbarui bagian "Status" setiap akhir sesi, lalu commit dan push.

Aturan kerja dan antrian unit lengkap ada di `docs/superpowers/plans/2026-10-24-continuation-under-usage-limits.md`. File ini hanya memuat posisi terakhir dan langkah berikutnya.

## Status (25 Okt 2026)

| | |
|---|---|
| `main` | WP-1.1, WP-1.2 (PR #1, #2), WP-1.3 + ADR-026 (4) (PR #3, di-merge PO 25 Okt) |
| Branch kerja | `claude/gracious-goodall-3hjh19` (dimulai ulang dari `main` setelah PR #3 di-merge) |
| Sedang dikerjakan | **WP-1.5a**: compose produksi, jaringan privat, socket-proxy (unit U6) |
| Utang terbuka | sign-off formal AG-13 untuk WP-1.2 dan WP-1.3. PR #3 di-merge tanpa itu; review putaran 2 menolak karena N1, lalu N1–N5 diperbaiki tetapi belum diverifikasi reviewer (U4, sesi terpisah) |
| Menunggu PO | konflik dokumen #36–#39 di `docs/superpowers/specs/2026-10-24-development-system-design.md` §8, plus #40–#43 dari WP-1.5a (lihat spec WP-1.5a); gate G0/G1 |

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
- `infra/tests/test_compose_policy.py`: 17 test kebijakan, stdlib.
- Spec: `docs/superpowers/specs/2026-10-25-wp-1.5a-compose-design.md`.

**Bukti lokal** (container cloud, 25 Okt):
- `python3 -m unittest discover -s infra/tests` → 17 OK.
- Stack jalan dengan `docker compose -f infra/compose.yml --env-file infra/tests/ci.env up -d --wait` → 10 service healthy, migrate exit 0.
- `KK_ENV_FILE=infra/tests/ci.env scripts/healthcheck.sh` → live/ready/`/` 200 lewat Caddy. `/health/ready` ok untuk db, redis, sandbox_manager, migrations.
- Socket-proxy menolak `/info`, `/volumes`, `/networks`, `/events`, dan `POST /exec/x/start` (403).
- Backend: test role DB 8 passed; ruff, pyright, dan lint-imports bersih.

### WP-1.5a: yang belum (urutan)

1. Job CI `infra-config` di `.github/workflows/`:
   - `python3 -m unittest discover -s infra/tests`;
   - `caddy validate` dengan image caddy yang dipin;
   - opsional: stack live + `scripts/healthcheck.sh` (butuh membangun image backend, sandbox-manager, dan sandbox).
2. Bukti mutasi untuk test kebijakan. Contoh: beri `ports` ke api, mount docker.sock ke sandbox-manager, set `INFO=1`, beri `ANTHROPIC_API_KEY` ke api. Masing-masing harus membuat test merah.
3. Jalankan suite backend penuh (`uv run pytest -q`, harus ≥ 1239 passed) dan sandbox_manager.
4. Plan doc `docs/superpowers/plans/2026-10-25-wp-1.5a-compose.md`.
5. Konflik #40–#43 di spec sistem pengembangan §8:
   - #40: `ANTHROPIC_API_KEY` hanya di worker, padahal ADR-004 juga menyebut api;
   - #41: `INFO=0` (ADR-026) vs `INFO=1` (DEPLOYMENT §2);
   - #42: job `infra-config` tidak ada di TEST_STRATEGY §6;
   - #43: variabel baru `DB_APP_PASSWORD` dan role `kodekantor_app` belum ada di CONFIGURATION/DATA_MODEL.
6. Review `kk-security-reviewer` atas diff WP-1.5a (satu putaran; jatah review di rencana §2.4), tracker §12, PR (template `.github/pull_request_template.md`).
7. Berikutnya: WP-1.5b (egress-proxy + hadolint/shellcheck/actionlint), WP-1.5c (backup/restore), WP-1.4 (frontend), WP-1.6 (G1).

## Cara melanjutkan dengan langganan (bukan kredit cloud)

1. **Di mesin sendiri (disarankan; tanpa biaya cloud).**
   - Pasang git, Docker Engine ≥ 27 + Compose, uv 0.11.x, Python 3.12, PostgreSQL 16, dan Redis 7 (atau jalankan keduanya sebagai container).
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
Lanjutkan WP-1.5a dari daftar "yang belum" di HANDOFF.md, urut dari nomor 1.
Commit setiap langkah hijau, push sebelum sesi berakhir, dan perbarui bagian Status di HANDOFF.md.
```
