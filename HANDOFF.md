# HANDOFF — mulai dari sini bila melanjutkan di sesi baru

File ini ditulis untuk melanjutkan pembangunan KodeKantor setelah kredit cloud habis, memakai Claude Code dengan langganan (limit sesi dan limit mingguan). Perbarui bagian "Status" setiap akhir sesi, lalu commit dan push.

Aturan kerja dan antrian unit lengkap ada di `docs/superpowers/plans/2026-10-24-continuation-under-usage-limits.md`. File ini hanya memuat posisi terakhir dan langkah berikutnya.

## Status (13 Nov 2026, sore)

| | |
|---|---|
| `main` | WP-1.1, WP-1.2 (PR #1, #2), WP-1.3 + ADR-026 (4) (PR #3), WP-1.5a (PR #4), WP-1.5b + uv 0.13.4 (PR #5), WP-1.5c (PR #6), WP-1.5d (PR #7), WP-1.4a (PR #8), WP-1.4b layar auth (PR #9), WP-2.1a projects core (PR #10), WP-2.1b import arsip (PR #11), WP-2.1c API workspace dan git (PR #12), WP-2.1d indeks dan pencarian (PR #13), WP-2.1e dashboard (PR #14), WP-2.2a layar IDE (PR #15), WP-2.2b panel git (PR #16), bukti G2 (PR #17; keputusan gate di PO). WP-3.1a port LLM (PR #18), WP-3.1b registry agent (PR #19), WP-3.1c1 approval + hapus project backend (PR #20), WP-3.1c2 layar `/approvals` (PR #21; AG-13 C1 terpenuhi, CI 9/9 di 68db3db), WP-3.1d1 ToolGateway inti (PR #22; AG-13 C1 terpenuhi, CI 9/9 di 3e1839a; C3 milik 3.1f), EX-03 caddy/socket-proxy + keputusan PO 7 Nov + late-OOM sandbox (PR #23, merged di ef377bd sebelum syarat AG-13 M1 ditutup; lanjutan PR #24, AG-13 SIGNED OFF, CI 9/9), WP-3.1d2a builtin R0 + `<untrusted_content>` (PR #25, AG-13 SIGNED OFF, CI 9/9 di 547d489), WP-3.1d2b builtin R1 + port simbol (PR #26, AG-13 SIGNED OFF, CI 9/9 di b7742c5, merge ee6e032), WP-3.1e1 readiness gate + kill switch (PR #27, AG-13 SIGNED OFF WITH CONDITIONS, CI 9/9 di dd099af, merge 4fcc31f), WP-3.1e2 layar provider (PR #28, AG-13 SIGNED OFF WITH CONDITIONS, CI 9/9 di 208bdee, merge 3cf872a), WP-3.1f1 ledger + BudgetGuard + usage/budget (PR #29, di-merge PO 13 Nov di a170e0a sebelum putaran review selesai; lanjutan PR #30, AG-13 SIGNED OFF, CI 9/9, merge 5508183), WP-3.1f2a AgentRuntime (PR #31, AG-13 SIGNED OFF WITH CONDITIONS, di-merge PO 14 Nov di 6d20bd2 dengan `security-scan` merah oleh gitleaks PEM sintetis) |
| Mandat PO | "jalankan P2, P3 dan P4" (2 Nov): WP-2.1, 2.2, 3.1, 3.2, 4.1, 4.2, 4.3 (berhenti di gate G4 untuk PO), SEC-01, bukti G2. Satu sub-WP = satu PR |
| Branch kerja | `claude/tender-maxwell-wbmsrg` (PR perbaikan CVE-2026-126360 di atas `main` 6d20bd2). Worktree lokal: `/home/user/IDEE-3.1f` (`wp-3.1f-sec`, DB `kodekantor_test_f1`, Redis 15; runner `scratchpad/f1-pytest.sh`), `/home/user/IDEE-3.1f-review` (checkout baca-saja untuk reviewer, DB `kodekantor_test_rv1..3`, Redis 11–13; runner `frv1..3-pytest.sh`). Satu DB = satu run pytest pada satu waktu |
| Sedang dikerjakan | **PR perbaikan `main` merah**: `security-scan` di 6d20bd2 gagal di pip-audit (`opentelemetry-api 1.45.1` CVE-2026-126360, advisory baru). Dinaikkan ke 1.45.2 di backend, sandbox-manager, dan image sandbox Python lewat pengecualian umur per paket (preseden certifi). Run CI PR ini juga run Semgrep/Trivy pertama atas kode WP-3.1f2a, karena di PR #31 langkah-langkah itu dilewati sesudah gitleaks merah. Tracker § WP-3.1f2a "Sesudah merge PR #31" |
| Berikutnya | **WP-3.1f2c** pengerasan redaksi (`Redactor` R-1..R-3 MEDIUM: kunci camelCase, nilai berkutip sesudah header, PGP private key; R-4..R-6 LOW; sisa delta D2-L1 reset run-wide kuadratik, D2-L2, D2-I1, D2-I2; review AG-13 utuh; Keputusan Lead #49) dan **3.1f2b** recovery `agent_runs`/`tool_calls` (`interrupted`), stale sweep, `RUNTIMES_AVAILABLE`, `agent_activity` dashboard, auth/404 → readiness `unknown`, `pricing_version` di predikat, AG-13 LOW-3, baris `running` yatim (silent N5); 3.1f2c selesai sebelum pemanggil produksi pertama `build_agent_runtime`. Lalu 3.1f3 (skenario `calc*`) bila perlu, WP-3.2 (engine: transisi task dari hasil run, `verify_report` + UPA-02 + D2-I3, salin budget efektif ke `tasks.budget_limit`, validasi `budget_increase`, INFO-5 AG-13), P4 (WP-4.1, WP-4.2, SEC-01, WP-4.3 berhenti di G4). Di luar cakupan: shell aplikasi belum responsif di ponsel |
| Utang terbuka | sign-off formal AG-13 untuk WP-1.2 dan WP-1.3 (U4). Dari WP-1.5a: role owner non-superuser untuk migrate. Dari WP-1.4b: token reset dipindah ke fragment sebelum ada jalur pengiriman tautan; stream task berhenti di status terminal. Dari WP-1.6: access log uvicorn rusak (`RedactingFilter`), healthcheck `scheduler`. Dari WP-3.1e1 (AG-13 N1): handler 422 memantulkan kunci dict dan nama field asing di `loc` (semua route) |
| Menunggu PO | konflik #36–#65, #69, #70 di `docs/superpowers/specs/2026-10-24-development-system-design.md` §8 (#66–#68, #71 dan butir 3.1d #72 diputuskan 7 Nov); EX-02, EX-04..EX-06 di DECISIONS, EX-03 npm `sandbox-node` sampai 2026-11-30 (postgres keluar dari EX-03 pada 13 Nov: image dinaikkan ke `16.16-alpine`) (EX-02 kini juga mencakup 5 HIGH tanpa fix dari git di image backend); role global di project yang diberi grant (tracker WP-2.1a); M2 egress sebelum G4; konflik #72 (agentic core: registry v1/v2, kontrak agent, skenario, tarif awal; butir 3.1d diputuskan 7 Nov lewat ADR-070; butir 3.1e dan ADR-073 Proposed, terutama #6 pemegang kill switch; butir 3.1f1 dan ADR-074 Proposed: budget project = default, satu baris ledger per percobaan, `usage:read_all` tidak memperluas, smoke di luar ledger, presisi `numeric(20,10)`, token `null`); gate G0/G1/G2; **temuan gitleaks PR #31** (fingerprint `.gitleaksignore` untuk PEM sintetis b35c3c7 ditolak pengklasifikasi izin sebagai "CI Bypass"; alternatif force-push); ratifikasi ADR-075 dan butir 3.1f2 di #72 |

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
3. Berikutnya: setelah PR WP-1.4b, WP-1.6 (bukti G1, berhenti di gate). Layar dashboard/projects/agents/approvals dibangun bersama WP backend-nya (konflik #63); selalu pakai `apiPath` untuk parameter route (syarat AG-13). WP-1.6 juga harus memperbaiki access log uvicorn yang rusak: `RedactingFilter` (`backend/app/core/logging.py:41`) mengosongkan `record.args`, sehingga setiap baris access log menjadi traceback "Logging error" (tracker WP-1.4b). WP-1.6 harus lebih dulu memperbaiki healthcheck `scheduler`, yang unhealthy setelah 10 menit karena beat dengan `dbm.gnu` tidak memperbarui mtime file jadwal (tracker WP-1.4a). M2 egress (dep-proxy read-only atau SNI splice) adalah syarat sebelum G4.
4. Exception EX-03 (sekarang hanya npm `kodekantor/sandbox-node`) kedaluwarsa 2026-11-30: `security-scan` akan merah pada tanggal itu sampai base node dinaikkan atau exception diperpanjang (RUNBOOK §12.1).
5. ~~Sebelum deploy publik: tutup CVE-2026-78669 di caddy~~ ditutup 7 Nov 2026 oleh `kodekantor/caddy` (Go 1.26.10, x/net 0.62.0; PR #23). Saat menaikkan Caddy: `go get` di `infra/caddy/build/`, lalu build dan scan ulang (RUNBOOK §12.1).

Catatan build lokal di balik proxy TLS (container cloud): `docker compose ... up --build` tidak bisa membaca CA di luar folder proyek. Build image langsung lalu `up --no-build`:

```bash
B="docker build --network host --build-arg HTTPS_PROXY --build-arg BASE_REPO=mirror.gcr.io/library/python --secret id=build_ca,src=/root/.ccr/ca-bundle.crt"
$B -f backend/Dockerfile -t kodekantor/backend:dev . && $B -f sandbox_manager/Dockerfile -t kodekantor/sandbox-manager:dev .
docker build --network host --build-arg HTTPS_PROXY --secret id=build_ca,src=/root/.ccr/ca-bundle.crt \
  --build-arg GO_REPO=mirror.gcr.io/library/golang --build-arg BASE_REPO=mirror.gcr.io/library/alpine \
  -f infra/caddy/Dockerfile -t kodekantor/caddy:dev .
# kodekantor/socket-proxy tidak bisa dibangun di container cloud: dl-cdn.alpinelinux.org diblokir proxy (403); CI yang membangunnya.
docker compose -f infra/compose.yml --env-file infra/tests/ci.env up -d --no-build --wait
```

## Cara melanjutkan dengan langganan (bukan kredit cloud)

1. **Di mesin sendiri (disarankan; tanpa biaya cloud).**
   - Pasang git, Docker Engine ≥ 27 + Compose, uv 0.13.x, Python 3.12, PostgreSQL 16, dan Redis 7 (atau jalankan keduanya sebagai container).
   - Clone repo, `git checkout claude/tender-maxwell-wbmsrg` (atau branch WP berikutnya), `git pull`.
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
