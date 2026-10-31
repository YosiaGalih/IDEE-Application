# HANDOFF — mulai dari sini bila melanjutkan di sesi baru

File ini ditulis untuk melanjutkan pembangunan KodeKantor setelah kredit cloud habis, memakai Claude Code dengan langganan (limit sesi dan limit mingguan). Perbarui bagian "Status" setiap akhir sesi, lalu commit dan push.

Aturan kerja dan antrian unit lengkap ada di `docs/superpowers/plans/2026-10-24-continuation-under-usage-limits.md`. File ini hanya memuat posisi terakhir dan langkah berikutnya.

## Status (31 Okt 2026)

| | |
|---|---|
| `main` | WP-1.1, WP-1.2 (PR #1, #2), WP-1.3 + ADR-026 (4) (PR #3), WP-1.5a (PR #4), WP-1.5b + uv 0.13.4 (PR #5), WP-1.5c backup/restore (PR #6), WP-1.5d Semgrep + Trivy (PR #7, di-merge PO 31 Okt) |
| Branch kerja | `claude/gracious-goodall-3hjh19` (dimulai ulang dari `main` setelah PR #7 di-merge) |
| Sedang dikerjakan | **WP-1.4a** (unit U9): fondasi frontend `frontend/` + image `kodekantor/web` + job CI `frontend`. Lihat tracker bagian WP-1.4a |
| Utang terbuka | sign-off formal AG-13 untuk WP-1.2 dan WP-1.3. PR #3 di-merge tanpa itu; review putaran 2 menolak karena N1, lalu N1–N5 diperbaiki tetapi belum diverifikasi reviewer (U4, sesi terpisah). Dari WP-1.5a: L1 `--forwarded-allow-ips`, role owner non-superuser untuk migrate, Trivy/Semgrep sebelum G1 (tracker WP-1.5a) |
| Menunggu PO | konflik dokumen #36–#59 di `docs/superpowers/specs/2026-10-24-development-system-design.md` §8 (#56/#57 aturan dan cakupan Trivy, #58 Redis 8 atau Valkey, #59 catatan review); persetujuan EX-02..EX-06 di DECISIONS; M2 egress sebelum G4; gate G0/G1 |

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
1. ~~PR #4, #5~~ di-merge. EX-01 ditutup (uv 0.13.4).
2. ~~WP-1.5c, WP-1.5d~~ di-merge (PR #6, #7). WP-1.4a: fondasi frontend (PR berikutnya).
3. Berikutnya: WP-1.4b (layar login, dashboard, projects, agents, approvals), lalu WP-1.6 (bukti G1, berhenti di gate). WP-1.6 harus lebih dulu memperbaiki healthcheck `scheduler`, yang unhealthy setelah 10 menit karena beat dengan `dbm.gnu` tidak memperbarui mtime file jadwal (tracker WP-1.4a). M2 egress (dep-proxy read-only atau SNI splice) adalah syarat sebelum G4.
4. Exception EX-03 kedaluwarsa 2026-11-30: `security-scan` akan merah pada tanggal itu sampai digest dinaikkan atau exception diperpanjang (RUNBOOK §12.1).
5. Sebelum deploy publik (WP-7.1/G7): tutup CVE-2026-78669 (HTTP/2 DoS) di caddy, dengan caddy hasil build ulang (Go ≥ 1.26.10, x/net 0.60.0) atau `protocols h1 h3` sementara (DECISIONS EX-03, review AG-13 WP-1.5d).

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
