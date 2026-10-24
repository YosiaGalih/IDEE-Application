# KodeKantor — Implementation Tracker

> Snapshot: **21 Oktober 2026**, commit `wave 2 snapshot`. Sumber kebenaran pembagian kerja: `2026-10-21-kodekantor-wbs-agentic.xlsx` (sheet *WBS*). Tracker ini merangkum statusnya per modul dan per acceptance criterion.
>
> **Status program:** rancangan (Phase 0) **selesai disusun dan direview independen** (21 Okt 2026); **penguncian formal menunggu gate G0** (keputusan PO atas 9 ADR *Proposed* dan FR turunan). Implementasi Wave 1–2 sempat berjalan, lalu **dihentikan atas instruksi Product Owner** ("jalankan rancangannya saja, jangan sampai implementasi"). Kode yang sudah ada disimpan sebagai *snapshot* dan **belum diintegrasikan end-to-end**. Tidak ada klaim "MVP selesai".

## Legenda

| Simbol | Arti |
|---|---|
| ✅ | Selesai dan terverifikasi (perintah dijalankan, output dikutip) |
| 🟢 | Kode snapshot ada + test unit/integrasi lulus; **belum** E2E/integrasi penuh |
| 🟡 | Sebagian (ada gap tercatat) |
| ⬜ | Belum dimulai |
| 🔒 | Wajib sign-off Security Reviewer |

## 1. Bukti eksekusi snapshot (dijalankan Lead, 21 Okt 2026)

| Suite | Perintah | Hasil aktual |
|---|---|---|
| Backend (semua) | `cd backend && uv run pytest -q` | **1314 passed, 2 failed** (151 dtk). Gagal: `test_config::test_every_documented_variable_is_a_setting` (CONFIGURATION.md kini memuat variabel infra/`TASK_MAX_ITERATIONS` yang belum ada di `Settings`) dan `test_core_worker_cli::test_celery_configuration` (ekspektasi lama daftar `imports` Celery). Keduanya ketidaksesuaian test↔dokumen, bukan bug runtime; perbaikannya masuk WP-INT-01. |
| Backend lint/type | `ruff check .` · `pyright` · `lint-imports` | All checks passed · 0 errors · 3 contracts kept (catatan: ADR-022 — dua bypass `importlib` belum terdeteksi linter) |
| Sandbox manager | `cd sandbox_manager && uv run pytest tests/unit -q` | **207 passed** |
| Sandbox Docker nyata | `cd sandbox_manager && uv run pytest tests/integration -m docker -q` | **23 passed in 19.32s** dijalankan ulang oleh Lead (raw socket); 0 container `kk.managed` tersisa. Varian via socket-proxy: 23 passed (laporan agent). Sign-off Security Reviewer (AG-13) 🔒 **belum**; bukti: uid 10001, CapEff 0, rootfs EROFS, network unreachable, pids & OOM limit, timeout, `.git` tersembunyi |
| Frontend | `cd frontend && npx vitest --run` | **29 files, 259 tests passed** |
| Registry agent | `python3 scripts/validate_registry.py` | OK: 42 agents; llm 6 / profile 7 / tool 7 / planned 22 |
| Integrasi E2E (Playwright) | – | ⬜ **belum dijalankan** (dihentikan sebelum Wave 3) |
| Claude API readiness gate | `python -m app.cli claude-smoke` | ⬜ **belum** — tidak ada API key di lingkungan build; hanya diuji dengan mock |

## 2. Status per modul

| Modul | FR | Prio | Desain | Kode snapshot | Integrasi/E2E | Gap & WP lanjutan |
|---|---|---|---|---|---|---|
| M01 Auth & User Access | FR-001–006 | P0 | ✅ | 🟢 login/logout/sesi/CSRF/lockout/rate-limit/reset, RBAC | ⬜ | SSO = fase lanjut |
| M02 Dashboard | FR-007–011 | P0 | ✅ | 🟢 API + UI | ⬜ | – |
| M03 Project Management | FR-012–018 | P0 | ✅ | 🟢 blank/template/git_https/arsip aman, archive, delete via approval | ⬜ | delete belum membatalkan task aktif (WP-INT-04) |
| M04 Repository & Context | FR-019–025 | P0 | ✅ | 🟢 tree, search text/symbol, index, stack detect | ⬜ | – |
| M05 Browser IDE | FR-026–032 | P0 | ✅ | 🟢 explorer, Monaco lokal, save + version conflict, patch | ⬜ | Monaco nyata hanya terverifikasi via build; uji browser di E2E |
| M06 AI Chat Workspace | FR-033–040 | P0 | ✅ | 🟢 conversation → task, streaming delta, cancel | ⬜ | – |
| M07 Agent Registry | FR-041–047 | P0/P1 | ✅ | 🟢 registry 42 profil, sync, katalog UI | ⬜ | admin enable/disable UI P1 |
| M08 Orchestrator & Workflow | FR-048–058 | P0 | ✅ | 🟡 pipeline S1–S10, lease, checkpoint, recovery, cancel | ⬜ | ADR-021 (3 transisi), ADR-022 (ports), ADR-024 (handler commit) → WP-INT-02/03 |
| M09 Tool Gateway & Policy | FR-059–067 | P0 🔒 | ✅ | 🟢 allowlist, schema, permission, R3 approval, idempotensi, redaksi | ⬜ | ADR-023 encoding nama tool sudah; ADR-030 redaksi huruf kecil → WP-INT-05 |
| M10 Sandbox & Runtime | FR-068–076 | P0 🔒 | ✅ | 🟢 terbukti di Docker nyata (dijalankan ulang Lead); sign-off 🔒 belum | ⬜ (via stack penuh) | gVisor belum diuji; TOCTOU bind mount tercatat |
| M11 Terminal & Command Runner | FR-077–083 | P0 | ✅ | 🟢 profil tetap, viewer read-only (tanpa shell) | ⬜ | – |
| M12 Testing & Debugging | FR-084–094 | P0/P1 | ✅ | 🟡 runner + junit/coverage + debug loop | ⬜ | E2E browser QA (C19) = P1, belum |
| M13 Diff & Code Review | FR-095–102 | P0 | ✅ | 🟢 diff terstruktur, findings, apply/reject | ⬜ | field `author` finding (API §11 #4) |
| M14 User Proxy Acceptance | FR-103–112 | P0 | ✅ | 🟢 verifikasi evidence backend, gate, remediasi; skenario `calc`, `calc-debug`, `calc-gap` lulus di level service | ⬜ (UI E2E) | – |
| M15 Git & Remote | FR-113–120 | P0 lokal / P2 | ✅ | 🟡 git lokal + hardening config | ⬜ | push/PR remote = P2 |
| M16 Documentation & Knowledge | FR-121–127 | P1 | ✅ | 🟡 ADR/decision CRUD, freshness, docs generate | ⬜ | FR-127 (cari riwayat percakapan/task) belum |
| M17 Provider & Model Settings | FR-128–135 | P0 | ✅ | 🟡 adapter Claude (streaming, retry, cache), pricing, budget, usage | ⬜ | readiness gate dengan API key nyata (WP-INT-07, butuh Operator) |
| M18 Deployment & Environments | FR-136–143 | P2 | ✅ | 🟡 preflight + staging (build+smoke di sandbox) | ⬜ | routing preview (ADR-008), production gated |
| M19 Observability & Audit | FR-144–151 | P0/P1 | ✅ | 🟡 log JSON + redaksi, audit append-only, `/metrics` | ⬜ | dashboard P1 |
| M20 Admin & Governance | FR-152–159 | P1 | ✅ | 🟢 users/roles/reset token, policies, kill switch provider | ⬜ | – |

## 3. Status acceptance criteria (PRD §12.1)

"Test ada" = test otomatis tersedia di snapshot dan suite-nya lulus (lihat §1); belum berarti AC terpenuhi karena **bukti E2E di CI dan staging (PRD §13.1) belum ada**.

| AC | Kriteria (ringkas) | Desain | Test ada (snapshot) | E2E/CI | Status AC |
|---|---|---|---|---|---|
| AC-01 | Login/logout; isolasi project antar user | ✅ | ✅ auth + cross-project (semua route family) | ⬜ | 🟢 |
| AC-02 | Project blank/template + import arsip aman | ✅ | ✅ template, zip-slip, bomb, symlink | ⬜ | 🟢 |
| AC-03 | IDE baca/cari/tab/edit via workspace API | ✅ | ✅ API + unit UI | ⬜ | 🟢 |
| AC-04 | Prompt → task dengan requirement, plan, status, ID | ✅ | ✅ workflow integration | ⬜ | 🟢 |
| AC-05 | Stream progres; reconnect ambil event hilang | ✅ | ✅ SSE replay `Last-Event-ID` | ⬜ | 🟢 |
| AC-06 | Agent hanya memanggil tool yang diizinkan | ✅ | ✅ gateway policy & denied calls | ⬜ | 🟢 |
| AC-07 | Kode jalan di sandbox dengan limit | ✅ | ✅ Docker nyata (dijalankan ulang Lead) | ⬜ | 🟢 (komponen; menunggu sign-off 🔒) |
| AC-08 | Runner menyimpan exit code/log/status/durasi/artefak | ✅ | ✅ | ⬜ | 🟢 |
| AC-09 | Diff file/hunk; user dapat menolak patch | ✅ | ✅ | ⬜ | 🟢 |
| AC-10 | Code review findings + evidence; reviewer read-only | ✅ | ✅ | ⬜ | 🟢 |
| AC-11 | User Proxy status per kriteria + link evidence | ✅ | ✅ termasuk downgrade tanpa bukti | ⬜ | 🟢 |
| AC-12 | Requirement wajib gagal tidak bisa diterima tanpa waiver | ✅ | ✅ gate matrix | ⬜ | 🟢 |
| AC-13 | R3 butuh approval valid; payload berubah → batal | ✅ | ✅ | ⬜ | 🟢 |
| AC-14 | Task dapat dibatalkan; sandbox dihentikan | ✅ (ADR-006) | ✅ | ⬜ | 🟢 |
| AC-15 | Secret tidak muncul di frontend/log/artefak | ✅ | 🟡 ada gap pola huruf kecil (ADR-030) | ⬜ | 🟡 |
| AC-16 | Laporan token/biaya aktual atau "unavailable" | ✅ (ADR-025) | ✅ | ⬜ | 🟢 |
| AC-17 | Restart backend tidak menghilangkan task running | ✅ | ✅ recovery + resume dari checkpoint | ⬜ | 🟢 |
| AC-18 | Perbaikan gap + revalidasi terbatas | ✅ (ADR-027) | ✅ skenario `calc-gap` | ⬜ | 🟢 |
| AC-19 | Semua perubahan punya referensi task + audit | ✅ | 🟡 sebagian (audit trace test end-to-end belum) | ⬜ | 🟡 |
| AC-20 | MVP jalan dari prosedur deployment terdokumentasi | ✅ | 🟡 compose tervalidasi; clean-install smoke belum diulang setelah Wave 2 | ⬜ | 🟡 |

## 4. Pekerjaan berikutnya (bila Product Owner membuka kembali implementasi)

Urutan dan pemilik ada di sheet *WBS* (Wave 3 → Phase 7). Ringkasnya:

1. **WP-INT-01..06** (Wave 3 — Integration): implementasi ADR-021..030, bootstrap handler approval di API, perbaiki 2 test yang tidak sinkron, stack penuh `make up` dengan scripted provider.
2. **WP-E2E-01..03**: Playwright happy path (login → project → prompt → patch → test → diff → User Proxy → approve) dan skenario remediasi AC-18; clean-install smoke AC-20.
3. **WP-SEC-01**: review keamanan independen + sign-off 🔒 (M09, M10, approvals, redaksi).
4. **WP-INT-07** (butuh Operator manusia): readiness gate Claude API dengan API key dan model ID nyata.
5. **Phase 6–8**: kelengkapan P1, staging di VPS, rollout specialist bertahap.

## 5. Keputusan yang masih menunggu Product Owner

- Review dan persetujuan `FR_CATALOG.md` (159 FR turunan, ADR-020).
- Konfirmasi 9 ADR berstatus *Proposed* (ADR-002, 003, 004, 005, 007, 008, 010, 011, 020) sebelum production.
- Apakah snapshot kode dipakai sebagai basis implementasi lanjutan atau dibuang (tidak dihapus tanpa konfirmasi).

## 6. Lampiran B PRD — checklist review sebelum implementasi

| # | Item Lampiran B | Status rancangan | Bukti / keputusan tertunda |
|---|---|---|---|
| 1 | Owner/identity model disepakati & RBAC dasar | 🟡 didesain (ADR-002 *Proposed*, API §2, ADR-034) | Keputusan PO di G0 |
| 2 | Claude Platform API, model ID, budget per task, secret storage diuji | 🟡 didesain (ADR-004, ADR-032) | Readiness gate dengan key nyata belum (WP-INT-07) |
| 3 | Sandbox model & batas network/resource disepakati | ✅ didesain & terbukti komponen (ADR-007/009/026/033) | Sign-off 🔒 AG-13; ADR-007 *Proposed* |
| 4 | Task statuses, retries, cancellation, checkpoint, event stream disetujui | ✅ didesain (ARCH §5, ADR-006/021/031) | Persetujuan PO di G0 |
| 5 | Kontrak User Proxy & schema evidence dikunci | ✅ (USER_PROXY_ACCEPTANCE, contracts.py, ADR-027) | – |
| 6 | Modul P0, AC, environment test, release gate disetujui | 🟡 (FR_CATALOG turunan, TEST_STRATEGY, Gates) | Review FR oleh PO (ADR-020) |
| 7 | Ukuran VPS awal & batas concurrency | 🟡 (ADR-010 *Proposed*) | Keputusan PO/Operator |
| 8 | PRD disalin ke repo sebagai source of truth | ✅ `docs/PRD.md` | – |

## 7. Hasil review desain independen (21 Okt 2026)

Reviewer independen menemukan 11 isu (4 High, 6 Medium, 1 Low). Semua ditindaklanjuti di dokumen: siklus dependensi WBS & gate G2/G5/G6 (WBS), G3 setelah staging (WBS), klaim status diturunkan (WBS & tracker), keanggotaan network `exec` (ARCH §2, API §10, SECURITY_MODEL), DATA_MODEL selaras ADR-025, matriks approval (API §11.1, ADR-034), izin RBAC (API §2), FR P0 tanpa desain (ADR-031, 032, 035), kontrol sandbox (ADR-033), penyelarasan ID ROADMAP↔WBS, dan label estimasi.

## 8. Pembaruan 21 Okt 2026 (sesi lanjutan)

- **Perbaikan approval (bagian WP-INT-03):** handler approval kini didaftarkan di proses API (`backend/app/main.py`, commit `91e8e7d`); test baru `tests/unit/test_main_approval_handlers.py`. Suite backend: **1316 passed, 2 failed** (2 kegagalan lama sinkronisasi dokumen).
- **Eksekusi nyata (WP-INT-06 sebagian):** stack dev tanpa compose (API + worker + sandbox-manager Docker) menjalankan skenario `calc`, `calc-debug`, `calc-gap` sampai `completed` termasuk merge ke `main`.
- **Live demo:** artifact *KodeKantor Replay* memutar ulang rekaman nyata tersebut (WP-DEMO-01).
- **Perluasan platform (Proposed):** `docs/PLATFORM_EXPANSION.md`, ADR-036..045, modul M21–M24, fase P9–P12, kriteria AX-01..AX-09. Belum ada implementasi; dimulai setelah gate G3.

## 9. Paket dokumen v2 (21 Okt 2026) — rancangan, belum ada implementasi

- **Agent v2:** `agents/registry.v2.yaml` (schema 2, 53 entri: 41 specialist + ORCH + 11 agent baru I42–I47, J48, K49–K51, S52; E29 dipromosikan). `agents/registry.yaml` sengaja tetap schema 1 (42 entri) karena loader backend snapshot hanya menerima schema 1 — saat v2 dicoba dimuat, 14 test backend gagal. Migrasi loader = WP-9.0. Bukti: `python3 scripts/validate_registry.py` → `OK: 53 agents … schema_version 2`; `python3 scripts/validate_registry.py agents/registry.yaml` → `OK: 42 agents` (didelegasikan ke `validate_registry_v1.py`); `pytest scripts/test_validate_registry.py scripts/test_validate_registry_v1.py` → `65 passed`; `pytest tests/unit -k "registry or agent"` (backend) → `52 passed`.
- **Spesifikasi sistem:** `docs/SYSTEM_SPEC.md` (SRS v2), `docs/FR_CATALOG.md` §7 (FR-160..FR-220).
- **Desain implementasi:** `docs/IMPLEMENTATION_DESIGN_V2.md`; kontrak `API_CONTRACTS.md` §12, `DATA_MODEL.md` Ekstensi v2 (migrasi 0002–0006), `TOOL_CATALOG.md` v2, `CONFIGURATION.md` v2; usulan U-01..U-07 dicatat sebagai **ADR-050..056 (Proposed)**.
- **PRD v2.0:** `docs/PRD_v2.md` (+ Word/PDF di outputs) dengan bab Tech Stack v2 dan Desain Mockup; mockup hi-fi di `docs/mockups/` (25 layar + 5 gambar gabungan; layar non-IDE = prototipe data contoh).
- **Rekonsiliasi konflik:** sisa pgvector dihapus (ADR-046); private key GitHub App hanya di `worker-integrations` (ADR-053) di PE, CONFIGURATION, FR-195, registry, WBS; M24 = P0 perluasan; AX-10/AX-11, WP-9.0, WP-0.13 masuk WBS (86 WP, dependensi tanpa siklus); syarat push-PR = task `completed` (API §12); 23 diagram mermaid lolos render.
- **Review konsistensi independen:** 24 temuan (2 High, 9 Medium, 13 Low) — semua High dan Medium ditindaklanjuti; Low ditindaklanjuti kecuali yang dicatat di bawah.
- **Temuan yang sengaja belum diubah:** `infra/compose.yml` `docker-socket-proxy` masih `INFO: 1`, padahal ADR-026 menetapkan `INFO=0` — file infra, tidak diubah karena PO meminta tanpa implementasi; masuk WP keamanan (AG-06/AG-13).
- **Keputusan PO terbuka:** PRD v2 §15.3 (13 butir) dan `SYSTEM_SPEC.md` §11.3–11.4.

## 10. Paket v2.1 (22 Okt 2026) — SDLC end-to-end, Developer Workspace v2, tier S/M/L — rancangan, 0 kode produk

- **Permintaan PO:** Portal IDE untuk SDLC end-to-end dari project kecil sampai mega project enterprise, dan dapat dipakai PO untuk mengedit kode sendiri. Keputusan PO: tier S/M/L bertahap (pilot tetap 1 VPS), demo menjalankan test hasil editan via Pyodide, PRD v2.1 file baru.
- **Kontrak:** `docs/SDLC_SCALE_DESIGN.md` (KONTRAK v2.1 + amandemen K13): modul M25–M27, FR-221..FR-258 (38), UC-21..24, NFR-23..27, AX-12..18, T19–T23, ADR-057..062 (*Proposed*, belum diputuskan PO), fase P13–P14, gate G13–G14. Tidak ada agent baru; 21 agent `planned` dijadwalkan aktif di P13 (8) dan P14 (13) per G8.
- **Dokumen tersinkron:** FR_CATALOG §8, SYSTEM_SPEC §12, IMPLEMENTATION_DESIGN_V2 §13, API_CONTRACTS §13, DATA_MODEL V2.9–V2.10, SECURITY_MODEL T19–T23, CONFIGURATION v2.1, AGENT_CATALOG §13, TOOL_CATALOG v2.1, ARCHITECTURE §14, WBS (112 WP, G0–G14, W0–W12), PRD v2.1 (`docs/PRD_v2.1.md` + Word/PDF).
- **Demo (WP-DEMO-03):** artifact *KodeKantor Replay* versi 3–4: Editor kode atas file project hasil rekaman (rekonstruksi patch commit, sha256 cocok 3/3; 7 file template dari repo rekaman pada commit yang sama), konflik 409 3-way (simulasi), test hasil editan dijalankan **di browser via Pyodide 0.26.4** — tanpa editan 11/11 lulus dan cocok dengan run nyata `b8a1fe83`; editan `a + b` → FAILED `assert 7 == 12`; fallback ke rekaman bila Pyodide gagal dimuat. Papan SDLC 8 fase dari bukti nyata; halaman tier S/M/L berlabel rancangan. Pyodide **bukan** sandbox KodeKantor.
- **Review independen v2.1:** 17 temuan (2 High, 6 Medium, 9 Low) — semua ditindaklanjuti (take-over tanpa `continue_agent`, aktivasi B08/B11/B12/B15, migrasi 0008 di WP-13.0, AX-17 OIDC di G13, pemisahan WP-13.5/WP-13.12, aturan CI K13, cakupan NFR-27, data demo diperbarui).
- **Belum diuji / terbuka:** kompatibilitas WebAssembly & Web Worker di halaman claude.ai (demo jatuh ke rekaman bila diblokir); attach stream Docker lewat socket proxy untuk terminal [Kemungkinan Besar]; OI-25, OI-26, OI-32, OI-34; keputusan PO OQ-01..OQ-03.

## 11. Paket v2.2 (22 Okt 2026) — polyglot language packs, lingkungan integrasi multi-service, monitoring biaya & token — rancangan, 0 kode produk

- **Permintaan PO:** (1) satu halaman monitoring biaya dan token realtime; (2) aplikasi harus dapat menciptakan sistem level enterprise yang terintegrasi dari berbagai teknologi dan bahasa pemrograman secara end-to-end. Keputusan PO: gelombang 1 = Java/Kotlin (Spring), C#/.NET, Go, PHP/Laravel, Python, SQL, Node.js, JavaScript/TypeScript, React, React Native, Flutter, Ruby, C++; integrasi diuji di lingkungan multi-service ephemeral; monitoring = estimasi realtime + rekonsiliasi Admin API.
- **Kontrak:** `docs/POLYGLOT_INTEGRATION_DESIGN.md` (KONTRAK v2.2 K14–K20 + amandemen K21): M28–M30, FR-259..FR-292 (34; total 292), UC-25..27, NFR-28..31, AX-19..24, ADR-063..068 (*Proposed*), T24–T28, fase P15/P16 + tambahan P6/P10, gate G15/G16. Tidak ada agent baru; D24 → P15 (WP-15.10), B11 → P16 (WP-16.6).
- **Dokumen tersinkron:** FR_CATALOG §9, SYSTEM_SPEC §13 (OI-37..51 → K21), IMPLEMENTATION_DESIGN_V2 §14, API_CONTRACTS §14, DATA_MODEL V2.11–V2.12, SECURITY_MODEL T24–T28, CONFIGURATION v2.2 (satu sumber tarif `PRICING_PATH`; filter rekonsiliasi `USAGE_RECONCILE_API_KEY_IDS`/`_WORKSPACE_ID`), AGENT_CATALOG §13–§14, TOOL_CATALOG v2.2 (`deps.audit`), ARCHITECTURE §14, DECISIONS ADR-063..068, WBS (137 WP, G0–G16, W0–W14), PRD v2.2.
- **Demo (WP-DEMO-04):** halaman *Monitoring biaya & token* me-replay 61 event usage dari rekaman nyata (scripted provider → biaya `tidak tersedia`, bukan 0), simulasi tarif dari input user berlabel estimasi, alert budget 50/80/100%, rekonsiliasi "tidak tersedia di demo". Halaman *Language packs & integrasi* berlabel rancangan. Uji Playwright 1662/1662 lulus.
- **Belum diuji / terbuka:** filter `api_key_ids[]`/`workspace_ids[]` belum dicoba terhadap Admin API nyata (butuh akun organisasi); ukuran image toolchain & waktu build per pack [Menebak]; OI-39, OI-45, OI-48 (default usulan, keputusan PO); OI-49 (produk proxy, CI eksternal); OI-50 (LSP/DAP pack baru → backlog WP-14.6).

## 12. Pembaruan 24 Okt 2026 — repo `YosiaGalih/IDEE-Application`, implementasi dibuka PO

- **Keputusan PO (24 Okt 2026, di sesi Claude Code):** "jalankan implementasi sekarang". Instruksi lama "jalankan rancangannya saja" belum dicabut secara formal, tetapi perintah terbaru PO membuka implementasi.
  - Q3: branch `main` boleh dibuat.
  - Q4: ECC dipakai selektif.
  - Q5: batas kredit cloud **$207**.
- **Basis kode:** snapshot Wave 1–2 **tidak tersedia**. ZIP v2 (21 Okt) dan v2.1 (22 Okt) hanya berisi dokumen + `scripts/validate_registry*.py`.
  - Jalur yang dipakai: B (greenfield), lihat `docs/superpowers/specs/2026-10-24-development-system-design.md` §4.1.
  - Status "Snapshot/Selesai" WP kode P1–P5 di WBS tidak berlaku untuk repo ini.
- **Gate G0 tetap belum diputuskan**: 9 ADR v1 *Proposed*, ADR-036..068, dan FR turunan. WP-1.1a hanya memakai keputusan yang sudah *Accepted* (ADR-001, ADR-015, ADR-017, ADR-018, ADR-030 desain).

### WP-1.1a — kerangka backend core (FR-144, FR-149; sebagian AC-15, AC-20)

Spec: `docs/superpowers/specs/2026-10-24-wp-1.1a-backend-core-skeleton-design.md`. Plan: `docs/superpowers/plans/2026-10-24-wp-1.1a-backend-core-skeleton.md`.

| Pemeriksaan | Perintah | Hasil aktual (24 Okt 2026, container cloud, PostgreSQL 16 + Redis lokal) |
|---|---|---|
| Test backend | `cd backend && uv run pytest -q` | **117 passed** setelah perbaikan review, termasuk readiness terhadap DB/Redis nyata; stabil berulang |
| Lint & format | `uv run ruff check .` · `uv run ruff format --check .` | All checks passed · 24 files already formatted |
| Tipe | `uv run pyright` | 0 errors, 0 warnings |
| Layer | `uv run lint-imports` | Contracts: 2 kept, 0 broken |
| Migrasi | `alembic upgrade head` → `downgrade base` → `upgrade head` | exit 0 ×3 (belum ada revisi; `0001_initial` = WP-1.1b) |
| Registry | `python scripts/validate_registry.py agents/registry.v2.yaml` | `OK: 53 agents (41 specialists + ORCH + 11 K2) — schema_version 2` |
| Audit dependensi | `uvx pip-audit --strict -r <uv export>` | No known vulnerabilities found. Sebelumnya `certifi 2026.7.22` PYSEC-2026-4320; dinaikkan ke 2026.10.23 lewat pengecualian umur per paket di `pyproject.toml`. |
| CI | `.github/workflows/backend.yml` | ⬜ belum berjalan — push ke GitHub ditolak 403 (akses Claude GitHub App belum ada) |

Gap yang diketahui:
- `/health/ready` akan `503` (`sandbox_manager: unavailable`) sampai WP-1.3 membangun sandbox-manager.
- Test kontrak "setiap variabel terdokumentasi ada di `Settings`" baru mencakup tabel variabel aplikasi v1. Variabel infra dan v2+ masuk bersama WP-nya.

Review WP-1.1a: `kk-backend-reviewer`, `kk-security-reviewer` (pra-review, bukan sign-off gate), `kk-silent-failure-hunter`; semuanya model Sonnet demi budget.

Awalnya: BLOCK, 4 HIGH. Sudah ditutup dengan test regresi:
- redaksi berdasarkan nama kunci dan nama majemuk (`GITHUB_TOKEN=`);
- nilai non-string (exception/bytes) dan header di tengah baris/JSON;
- JSON ter-escape, nilai multi-kata, PEM terpotong, userinfo tanpa password;
- log stdlib/uvicorn (traceback dan `?ticket=`) lewat filter redaksi + format JSON;
- head Alembic dibaca sekali saat startup (bukan I/O blocking per poll);
- `APP_ENV` wajib eksplisit (fail-closed);
- `repr(settings)` tanpa secret;
- header `Allow` dipertahankan;
- error ≥ 500 di-log;
- `request_id` stabil per request;
- deteksi tabel `alembic_version` lewat `to_regclass`;
- `aclose` berantai `try/finally`;
- OpenAPI tidak disajikan di production;
- import-linter melarang `subprocess`.

Ditunda dengan alasan:
- `ANTHROPIC_API_KEY` wajib bila `LLM_PROVIDER=anthropic` → menunggu `KK_PROCESS_ROLE` (ADR-053), karena `api` memang tidak memegang kunci.
- Literal secret < 8 karakter tidak dicocokkan, untuk menghindari penyensoran kata biasa. `gen-secrets.sh` (WP-1.5) wajib membangkitkan password panjang.
- Logging dan lock pada `alembic/env.py` → WP-1.1b.
- Rate limit `/health/ready` → bersama rate limiter WP-1.1.
- Digest image service CI.

### WP-1.1b — audit append-only (FR-146, sebagian AC-19)

Spec: `docs/superpowers/specs/2026-10-24-wp-1.1b-audit-append-only-design.md`.

| Pemeriksaan | Hasil aktual (24 Okt 2026) |
|---|---|
| `uv run pytest -q` | **138 passed**. Termasuk `test_core_audit.py`: UPDATE/DELETE/TRUNCATE ditolak, role runtime tidak bisa `ALTER … DISABLE TRIGGER`, trigger `ENABLE ALWAYS`, dan redaksi metadata. |
| ruff · pyright · lint-imports | bersih · 0 error · 2 kontrak dipatuhi |
| `alembic downgrade base → upgrade head → check` | exit 0; "No new upgrade operations detected" |

Review `kk-backend-reviewer` (Sonnet): BLOCK 2 HIGH + 3 MEDIUM, semuanya ditutup dengan test.
- **HIGH 1:** role aplikasi bisa mematikan trigger. Perbaikan:
  - trigger `ENABLE ALWAYS`;
  - `REVOKE ALL … FROM PUBLIC`;
  - `audit_guard_problems()` yang membuat app **gagal start di production** bila role runtime adalah superuser, pemilik tabel, atau punya UPDATE/DELETE/TRUNCATE.
- **HIGH 2:** redaksi belum lengkap. Ditambahkan kunci camelCase/hyphen/jamak, `jwt`/`bearer`, pola JWT, kunci dict, dan kolom teks bebas.
- **MEDIUM:** metadata dinormalisasi ke JSON (set → list, NaN → null); panjang kolom divalidasi; `write_detached()` untuk event denied/error agar tidak hilang saat rollback.

Wajib di WP-1.5 (deployment):
- Migrasi dijalankan oleh role pemilik.
- `api`/`worker` memakai role terpisah dengan `INSERT, SELECT ON audit_events` + `USAGE` pada sequence. Tanpa itu, production tidak akan start (fail-closed, disengaja).
- Purge retensi audit 365 hari tetap terbuka (spec sistem pengembangan §8 #17).

### WP-1.1c — skema identitas, RBAC, hash password, `create-owner` (FR-003, dasar FR-001/002/004/005)

Spec: `docs/superpowers/specs/2026-10-24-wp-1.1c-identity-rbac-design.md`. Dibangun di atas ADR-002 (*Proposed*) atas perintah PO.

| Pemeriksaan | Hasil aktual (24 Okt 2026) |
|---|---|
| `uv run pytest -q` | **159 passed**. Mencakup:<br>• `test_rbac.py`: matriks role, global vs project, global-only, owner project<br>• argon2id<br>• skema identitas: email lower-case unik, CHECK role/scope, cascade<br>• `create-owner` sekali + audit<br>• CLI lewat stdin |
| ruff · pyright · lint-imports | bersih · 0 error · 2 kontrak dipatuhi |
| `alembic downgrade base → upgrade head → downgrade -1 → upgrade head → check` | exit 0 |

Catatan:
- Panjang password minimum 12 adalah pilihan saya (dokumen tidak menetapkan) [Kemungkinan Besar, NIST SP 800-63B].
- Endpoint `/auth/*`, CSRF, rate limit, dan lockout = WP-1.1d (🔒).

**Pre-review keamanan (kk-security-reviewer, bukan sign-off gate) @ d837fff:** tidak ada CRITICAL atau HIGH. Ada 2 MEDIUM dan 2 LOW:

| Temuan | Status | Bukti |
|---|---|---|
| MEDIUM T5: `IntegrityError` dari `create-owner` membawa hash argon2id di teks exception/traceback | **ditutup**:<br>• `make_engine(..., hide_parameters=True)`<br>• pola `$argon2…` di `Redactor`<br>• duplikat email → `EmailTakenError` tanpa exception asal<br>• CLI exit 1 dengan pesan polos | `tests/security/test_owner_bootstrap.py`:<br>• `::test_duplicate_email_error_carries_no_password_hash`<br>• `::test_engine_hides_bind_parameters_in_db_errors`<br>• `::test_cli_duplicate_email_prints_no_hash`<br>• `test_redaction.py::test_argon2_hash_is_redacted` |
| MEDIUM ADR-002: `create_owner` check-then-insert, sehingga dua owner global bisa lahir saat bersamaan | **ditutup di service**: `pg_advisory_xact_lock` sebelum cek | `::test_concurrent_bootstrap_creates_exactly_one_owner` (6 panggilan paralel → 1 owner, 5 `OwnerExistsError`). Sebelum fix: 3 test merah. |
| LOW T6: `RoleGrant` menerima scope dengan newline di akhir | **ditutup**: `fullmatch` | `test_rbac.py::test_unknown_role_or_scope_is_rejected` |
| LOW T6: (a) tanpa `project_id`, role global memberi izin project; (b) admin global + grant `readonly` project tetap admin di project itu | **dibawa ke WP-1.2/1.1d**: `require_permission` wajib menerima `project_id` untuk resource project, dengan test. (b) sesuai API_CONTRACTS §2 apa adanya. | — |

Diusulkan ke AG-00 (perubahan kontrak, belum dibuat): partial unique index `user_roles(role_name) WHERE role_name='owner' AND scope='global'`. Tujuannya agar invarian single-owner juga dijaga DB, tidak hanya service. Usulan ini menunggu keputusan desain `owner:transfer`.

Hasil setelah perbaikan: `uv run pytest -q` → **164 passed**; ruff, format, pyright, dan lint-imports bersih.

Scanner belum dijalankan oleh reviewer:
- gitleaks dijalankan di CI;
- pip-audit dijalankan di CI;
- semgrep dan trivy belum ada.

Sign-off formal AG-13 tetap diperlukan sebelum G1.

### WP-1.1d (🔒) — auth, sesi, CSRF, rate limit, lockout, reset password (FR-001–006 dasar, AC-01 backend)

Spec: `docs/superpowers/specs/2026-10-24-wp-1.1d-auth-session-csrf-design.md`. Plan: `docs/superpowers/plans/2026-10-24-wp-1.1d-auth-session-csrf.md`.

Eksekusi inline (native), bukan subagent per task, demi batas kredit. Rulings dicatat di ledger plan.

| Pemeriksaan | Hasil aktual (24 Okt 2026) |
|---|---|
| `uv run pytest -q` | **206 passed** setelah WP-1.1d. Test kontrak:<br>• `test_auth_flow.py::test_login_sets_cookies_and_me`, `::test_logout_revokes_session_and_clears_cookies`, `::test_lockout_after_five_failures`<br>• rate limit, sesi kedaluwarsa, rotasi, CSRF terikat sesi, lockout paralel, fail-closed Redis<br>• forgot/reset/change<br>• `tests/security/test_auth_hardening.py`: 8 test, masing-masing dibuktikan merah lewat sabotase |
| ruff · format · pyright · lint-imports | bersih · 0 error · 2 kontrak dipatuhi |
| alembic upgrade + check | exit 0, "No new upgrade operations detected" (tanpa migrasi baru) |

**Review WP-1.1d** (`kk-backend-reviewer` + `kk-security-reviewer`, pra-review, bukan sign-off gate):

| Temuan | Status | Bukti |
|---|---|---|
| HIGH (kedua reviewer): login gagal memegang 2 koneksi. Sekitar 16 login gagal paralel membuat pool habis selama 30 detik, dan bisa dipicu tanpa autentikasi. | **ditutup**: setiap jalur auth memegang paling banyak 1 koneksi | `test_failed_logins_never_hold_two_connections` (pool 2 koneksi, 9 login paralel) merah → hijau |
| HIGH: argon2 memblokir event loop | **ditutup**: thread pool 4 worker (`averify_password` / `ahash_password`), di luar row lock | `test_argon2_runs_off_the_event_loop` merah → hijau |
| MEDIUM (keamanan): burst paralel mengevaluasi 15 tebakan | **ditutup**: reservasi percobaan atomik sebelum verifikasi | `test_lockout_cannot_be_bypassed_by_a_burst` (20 IP, ≤ 5 dievaluasi) merah → hijau |
| MEDIUM: test race token reset tidak membuktikan `FOR UPDATE` | **ditutup**: test deterministik | `test_reset_waits_for_the_token_row_lock`; mutan tanpa `FOR UPDATE` terbunuh |
| MEDIUM/LOW lain:<br>• lockout lewat change tidak diaudit<br>• counter lockout membengkak<br>• token reset hidup setelah change<br>• 429 tidak di-log<br>• timeout Redis<br>• `no-store`<br>• reset untuk user disabled<br>• forgot bersamaan<br>• timing forgot<br>• `Depends`<br>• kontrak services ↛ fastapi | **ditutup** | test merah → hijau per butir. Timing forgot: perbaikan struktural (token diterbitkan di background setelah respons). |
| MEDIUM: `response_model` / `Envelope[T]` | **ditunda ke WP-1.2** | — |
| MEDIUM (keamanan): kunci IP di belakang proxy (XFF, IPv6 /64, `ip=None`) | **syarat sign-off 🔒 WP-1.5** | — |
| LOW/NIT: prefix `__Host-` (#29), `csrf_token` mentah (#28), parse header ganda, Origin WebSocket (ADR-059) | dicatat | konflik dokumen dilaporkan |

Hasil setelah perbaikan: `uv run pytest -q` → **432 passed** (commit 7713d59). Sign-off formal AG-13 tetap diperlukan sebelum G1.

Keputusan yang perlu dicek PO ada di spec bagian "Keputusan saya (cek)":
- TTL token reset 30 menit [Menebak].
- `remember_me` hanya mengatur `Max-Age`.
- Token reset hanya muncul di log development. Ini **konflik dokumen #27**.

### WP-1.1e — state machine task + task events (FR-048, dasar FR-145, AC-14 bagian state)

Spec: `docs/superpowers/specs/2026-10-24-wp-1.1e-task-state-machine-design.md`. Plan: `docs/superpowers/plans/2026-10-24-wp-1.1e-task-state-machine.md`.

| Pemeriksaan | Hasil aktual (24 Okt 2026) |
|---|---|
| `uv run pytest -q` (DB terpisah `kodekantor_main`) | **412 passed**. Mencakup:<br>• matriks 13×13 `ALLOWED` vs tabel edge diagram §5.1 (ADR-006/021)<br>• `transition`: 409 `INVALID_TRANSITION` / `VERSION_CONFLICT`, writer paralel, objek basi, iterasi, timestamp<br>• event: seq tanpa celah pada 20 emit paralel, publish hanya setelah commit, rollback dibuang, payload diredaksi, ephemeral tidak disimpan<br>• schema `0003_tasks` |
| Penjaga "satu penulis status" | `tests/unit/test_status_writer_guard.py`. Test sanity menemukan celah `.values(**dict)`, lalu detektor diperluas. |
| `alembic downgrade base → upgrade head → downgrade -1 → upgrade head → check` | exit 0 |
| ruff · format · pyright · lint-imports | bersih · 0 error · 2 kontrak dipatuhi (`app.workflow` masuk kontrak layers) |

Catatan:
- Reviewer paralel yang memakai DB uji yang sama membuat test saling mengganggu. Mulai sekarang setiap proses memakai DB sendiri: `kodekantor_main`, `kodekantor_review`.
- `dump.rdb` Redis sempat ter-commit, lalu dihapus dari tracking dan diabaikan.

**Review WP-1.1e** (`kk-silent-failure-hunter` + `kk-backend-reviewer`):

| Temuan | Status | Bukti |
|---|---|---|
| HIGH F1: event dari transaksi atau savepoint yang di-rollback ikut terpublish, dengan seq yang dipakai ulang | **ditutup**: hook session (commit → daftar committed; rollback → buang); `publish_pending` hanya mengirim event yang sudah commit | 4 test merah → hijau |
| MEDIUM F2: audit gagal setelah UPDATE meninggalkan perubahan status tanpa audit | **ditutup**: UPDATE + audit + event dalam satu savepoint; aktor divalidasi dulu | `test_failed_audit_leaves_no_status_change` |
| MEDIUM F3/F4: `reason` tidak diredaksi; reason > 200 karakter menggagalkan transisi | **ditutup**: `audit` wajib, redaktor-nya dipakai di kolom/event/audit; kolom memotong 200 karakter, event menyimpan utuh | 2 test |
| MEDIUM F5/F6: kegagalan publish tanpa konteks; publish terlupa | **ditutup sebagian**: log memuat `type`/`seq`/`error_type`, ditambah `commit_and_publish`. Guard statis untuk pemanggil ditunda ke WP-3.x. | 2 test |
| LOW F8–F11 | **ditutup**:<br>• masuk `queued` menghapus `completed_at`<br>• task yang dihapus → NOT_FOUND; detail versi saat konflik<br>• task dari sesi lain atau status lokal yang diubah ditolak<br>• guard statis diperluas<br>• **trigger DB `tasks_status_guard`** sebagai backstop CLAUDE.md #4 | test per butir |
| LOW F7 (lock/publish timeout); audit transisi yang ditolak | **ditunda ke WP-3.x**: `write_detached` butuh koneksi kedua, bahaya pool yang sama seperti WP-1.1d | — |

Temuan tambahan dari `kk-backend-reviewer`. Review ini dibuat atas commit sebelum fix pass, jadi HIGH-1, HIGH-3, MEDIUM-5, dan sebagian HIGH-2 sudah tertutup di fa30f1b. Yang ditutup di pass berikutnya:

| Temuan | Status | Bukti |
|---|---|---|
| Bug baru dari fix pass sendiri: `after_commit` juga terpicu saat savepoint di-release, sehingga event transisi yang di-rollback tetap terpublish | **ditutup**: hanya commit akar yang dihitung | `test_rolled_back_transition_leaves_nothing` merah → hijau |
| MEDIUM-4: predikat `version` tidak dilindungi test | **ditutup** | `test_aba_stale_version_conflicts`; mutan tanpa predikat terbunuh |
| MEDIUM-6a: `failed → cancelled` menimpa `completed_at` | **ditutup**: `coalesce` | test |
| MEDIUM-6b: aturan iterasi vs API_CONTRACTS §5 | **konflik dokumen #30** | — |
| MEDIUM-7: lokasi test sesuai TEST_STRATEGY | **ditutup**: `tests/integration/test_states_transition.py` menguji 169 pasangan lewat `transition()` | 169 test |
| MEDIUM-8: publish tanpa timeout; kegagalan ephemeral merambat | **ditutup**: timeout 2 dtk per publish; ephemeral di-log, tidak dilempar | 2 test |
| MEDIUM-9: test glue | **ditutup**: subscriber menerima `task.status_changed`; rollback bersih | 2 test |
| LOW-10: indeks FK | **ditutup**: `tasks.parent_task_id`, `conversation_id`, `created_by`, `conversations.user_id` | test `pg_indexes` |
| LOW-12/13: instance kedaluwarsa; payload tidak aman-JSON | **ditutup** | 2 test |
| HIGH-2 sisa: bypass penanda trigger | **ditutup**: hanya `states.py` dan `models/tasks.py` yang boleh menyebut `kk.status_writer`; guard menangkap `Task(status=)` dan `set_committed_value` | 2 test |
| LOW-11 (`now()` = awal transaksi), LOW-14 (urutan publish antar-committer: WP-3.2 wajib mengisi celah seq dari DB), LOW-15 (isolasi DB test), NIT-16/20 (konflik #31, #32) | dicatat | — |

Hasil akhir: `uv run pytest -q` → **630 passed** (DB baru `kodekantor_main`). Migrasi naik-turun dan `check` exit 0.

`0003_tasks` diedit di tempat, karena belum pernah diterapkan di luar DB uji. Akibatnya DB lokal yang sudah berada di 0003 versi lama harus dibuat ulang; CI selalu memakai DB baru.

### WP-1.1f — Celery worker, lease, recovery sweep, Idempotency-Key (NFR-05, AC-17 bagian state, API_CONTRACTS §1)

Spec: `docs/superpowers/specs/2026-10-24-wp-1.1f-worker-lease-recovery-idempotency-design.md`. Plan: `docs/superpowers/plans/2026-10-24-wp-1.1f-worker-lease-recovery-idempotency.md`.

| Pemeriksaan | Hasil aktual (24 Okt 2026) |
|---|---|
| `uv run pytest -q` (DB `kodekantor_main`) | **655 passed**. Mencakup:<br>• lease: satu pemenang dari 6 claim paralel; overlap deterministik `MAX_ACTIVE` (mutan tanpa advisory lock terbunuh); renew dan release hanya oleh pemilik<br>• recovery: kedaluwarsa → `interrupted`/`cancelled`; lease hidup tidak disentuh; 3 sweep paralel tanpa kegagalan (mutan tanpa `SKIP LOCKED` terbunuh); satu task gagal tidak menghentikan sweep; CLI `recover`; job Celery<br>• idempotency: replay, tanpa `Set-Cookie`, beda body → 409, in-flight → 409, scope per sesi (mutan tanpa scope terbunuh), 5xx tidak disimpan, kunci invalid → 400, Redis mati → 503 |
| `celery -A app.worker.celery_app report` | exit 0; `accept_content: ['json']`, `task_acks_late: True`, `include: ['app.worker.jobs']` |
| `pip-audit --strict` (setelah menambah celery 5.6.3) | No known vulnerabilities found |
| ruff · pyright · lint-imports | bersih · 0 error · 3 kontrak dipatuhi (`app.main | app.worker` sejajar) |

Keputusan yang perlu dicek ada di spec bagian "Keputusan saya (cek)":
- scope kunci = sesi;
- 5xx tidak disimpan;
- batas 1 MiB;
- Redis mati → 503;
- `MAX_ACTIVE` dihitung dari semua lease yang hidup (diperketat dari §5.3);
- marker pending 300 dtk [Menebak].
