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
| Audit uv (alat build, 30 Okt) | `uvx pip-audit --strict -r infra/build/uv-requirements.txt` | No known vulnerabilities found. Sebelumnya uv 0.11.32 PYSEC-2026-4321 (EX-01); dinaikkan ke 0.13.4 (rilis 20 Okt) lewat pengecualian umur per paket di `infra/build/lock-uv.sh`. Dengan uv 0.13.4: `uv sync --locked` tidak mengubah kedua `uv.lock`; backend 1243 passed, 1 skipped; sandbox_manager 151 passed; image ter-build; stack `ci.env` healthy, live/ready/`/` 200 lewat Caddy. |
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

**Review WP-1.1f** (`kk-backend-reviewer` + `kk-silent-failure-hunter`). Awalnya BLOCK: backend 1 CRITICAL dan 6 HIGH, silent-failure 5 HIGH. Semua ditutup di 303aa0c:

| Temuan | Perbaikan | Bukti |
|---|---|---|
| CRITICAL: log worker dan CLI `recover` melewati redaktor | sinyal `setup_logging` + `worker_hijack_root_logger=False` + `configure_worker_logging` | `test_worker_logging_is_redacted`; worker Celery sungguhan menulis JSON |
| HIGH: replay tanpa validasi sesi (logout → tetap di-replay); cookie palsu menulis ke Redis | validator sesi disuntikkan dari `main.py`; sesi tidak hidup → diteruskan ke auth route | `test_replay_requires_a_live_session`, `test_invalid_sessions_never_touch_the_store` |
| HIGH: body terpotong dieksekusi; streaming membekukan loop | disconnect → tidak dieksekusi, tidak di-claim; `receive` diteruskan | test ASGI mentah (sebelumnya merah / hang) |
| HIGH: body tanpa batas sebelum auth; query string di luar sidik jari | 413 > 10 MiB; sidik jari = query + content-type + body | 2 test |
| HIGH: gagal simpan / body > 1 MiB / marker kedaluwarsa → eksekusi ulang | marker berpemilik (Lua CAS), diperpanjang selama berjalan; `executed` → 409; gagal simpan → marker dipertahankan | 3 test |
| HIGH/MEDIUM: `claim_lease` mengambil task selesai; `False` ambigu | `ClaimResult` (ACQUIRED/HELD/AT_CAPACITY/BUSY/NOT_CLAIMABLE/NOT_FOUND); hanya `queued` | `test_only_queued_tasks_are_claimable`, `test_duplicate_delivery_single_execution` |
| HIGH/MEDIUM: `running` tanpa lease tidak pernah pulih | lease NULL + tidak berubah > 60 dtk → yatim | `test_active_task_without_a_lease_is_recovered_after_a_grace_period` |
| MEDIUM: 4xx sementara terkunci 24 jam; replay kehilangan header | 401/403/408/422/425/429 tidak disimpan; allowlist header aman | 2 test |
| MEDIUM: sweep tanpa sinyal/timeout; `renew` mudah diabaikan; lock global | `RecoveryIncomplete` menggagalkan job; timeout 25 dtk; `expires` beat; `LeaseLost`; `lock_timeout` → `BUSY` | 4 test |

Hasil akhir: `uv run pytest -q` → **673 passed**; ruff, pyright, dan lint-imports bersih.

Usulan ke Lead (dokumen kontrak, belum diubah):
- **API_CONTRACTS §1:** tambahkan perilaku Idempotency-Key:
  - header `Idempotent-Replay`;
  - 400/409/413/503;
  - status yang tidak disimpan;
  - catatan bahwa keberhasilan yang tidak tercatat menghasilkan 409, bukan eksekusi ulang.
- **SQL claim di ARCHITECTURE §5.3:**
  - hanya task `queued` yang bisa di-claim;
  - kapasitas dihitung dari lease yang hidup;
  - hasil claim bertipe.

Risiko yang dicatat:
- **Versi redis.** kombu 5.6.2 mendeklarasikan `redis<6.5` untuk extra `[redis]`, sedangkan repo memakai redis 8.1. Worker Celery sungguhan berjalan (broker round-trip lewat sweep startup), tetapi kombinasi ini di luar matriks yang didukung kombu.
- **`visibility_timeout`.** Nilai bawaan 1 jam perlu diset di atas `TASK_MAX_DURATION_MINUTES` di WP-3.x.
- **Task `queued` yatim** (pesan broker hilang) belum dipulihkan oleh sweep mana pun (WP-3.x).

### Merge PR #1 ke `main` (24 Okt 2026)

- **Keputusan PO:** "lanjutkan dan merge ke main". PR [YosiaGalih/IDEE-Application#1](https://github.com/YosiaGalih/IDEE-Application/pull/1) di-merge sebagai merge commit `ae11c5d` (bukan squash), sehingga SHA yang dirujuk tracker ini tetap ada di riwayat `main`.
- **Kondisi saat merge:** head `beb9aad`, 4 check CI sukses (backend-lint, backend-types, backend-test, security-scan), `mergeable_state: clean`.
- **Yang belum terpenuhi:**
  - WP-1.1d (🔒) masuk `main` **tanpa sign-off formal AG-13 di sesi terpisah** (§6 #10 spec sistem pengembangan). Yang ada hanya pra-review `kk-security-reviewer` di sesi yang sama.
  - Sign-off formal tetap wajib sebelum G1 diputuskan.
- **Aturan CLAUDE.md** "manusia yang me-merge": merge dijalankan agent atas perintah eksplisit PO di sesi ini, bukan oleh manusia lewat UI GitHub. Ini dicatat sebagai pengecualian, bukan perubahan aturan.

### WP-1.2a (🔒) — safe path, operasi file, patch atomik (FR-028, FR-029, FR-031 apply, FR-032, T2, T9)

Spec: `docs/superpowers/specs/2026-10-24-wp-1.2-workspace-git-design.md`. Plan: `docs/superpowers/plans/2026-10-24-wp-1.2a-safe-paths-fs-patches.md`.

| Pemeriksaan | Hasil aktual (24 Okt 2026) |
|---|---|
| `uv run pytest -q` (DB `kodekantor_main`) | **882 passed** (sebelum test sintaks glob ditambahkan; test workspace saja: 219 passed) |
| ruff · ruff format · pyright · lint-imports | bersih · 89 file terformat · 0 error · 3 kontrak dipatuhi (layer baru `app.workspace \| app.gitops`) |
| Coverage `app/workspace` (`coverage` 7.x sementara lewat `uv run --with`, tidak masuk lock) | total 96%: `paths.py` 98%, `policy.py` 95%, `fs.py` 95%, `patches.py` 95% (target TEST_STRATEGY ≥ 85% untuk `paths.py`) |
| `tests/security/test_no_host_exec.py` | proses hanya di `app/gitops/`; self-test membuktikan checker bisa gagal |

Mutasi yang membuktikan test bisa gagal (setiap mutan dikembalikan setelahnya):

| Kontrol dihapus | Hasil |
|---|---|
| `O_NOFOLLOW` di walk folder | `test_race_swap_detected` merah |
| cek `commonpath` (containment) | 12 test merah |
| larangan tulis lewat symlink | `test_write_through_symlink_rejected` merah |
| `flock` pada root | `test_concurrent_writers_one_conflict` merah (8 penulis sukses) |
| cek konflik sebelum commit patch | `test_conflict_changes_nothing` merah |
| `O_NONBLOCK` saat membuka file | test FIFO menggantung sampai di-kill `timeout 40` |

Keputusan yang perlu dicek ada di spec bagian "Keputusan saya (cek)" no. 1–5 dan 8. Konflik dokumen #33–#35 ada di `docs/superpowers/specs/2026-10-24-development-system-design.md` §8.

**Review WP-1.2a** (`kk-security-reviewer` pra-review + `kk-backend-reviewer`). Keduanya awalnya **BLOCK**. Tidak ada workspace escape; temuan yang ditutup di 5cb94ea:

| Temuan | Perbaikan | Bukti |
|---|---|---|
| HIGH (keduanya): ReDoS `.agentignore`. Pola 21 byte membekukan proses 13 dtk (PoC reviewer) | matcher per segmen dengan `fnmatch.translate` + walk `**` ber-memo; batas 1000 pola / 256 karakter / 8 run `*` / 4 `**` / kedalaman path 128; melewati batas → gagal tertutup untuk AGENT_* saja | `test_agentignore_patterns_cannot_freeze_matching`, `test_worst_case_policy_is_bounded`, `test_over_cap_agentignore_fails_closed_for_agents_only`. Matcher lama dengan pola `"*a"*8+"b"` tidak selesai dalam 120 dtk |
| HIGH (keduanya): denylist lebih sempit dari kontrak `.env*` (`.envrc`, `.env-prod`, `.envs/` lolos ke LLM) | `.env*` per segmen + `.aws`, `.ssh`, `.docker`, `.kube`, `.gnupg`, `*.ppk`, `.pgpass`, `.htpasswd` | `test_policy_matrix` (mutan `.env` sempit → 18 test merah) |
| HIGH: body hunk melebihi hitungan header dibuang diam-diam | `PatchRejected("malformed")` | `test_body_longer_than_header_counts_is_rejected` |
| HIGH/MEDIUM: path alias (`./a.py`, `a.py/`, `src//b.py`) saling menimpa | dedup pada path hasil resolve → `duplicate_path` | `test_alias_paths_are_one_file` (3 kasus) |
| MEDIUM: `flock` pada root bisa ditahan sandbox | file lock di `<root>/../.kk-locks/` (di luar mount sandbox), `LOCK_NB` dengan batas tunggu → `WorkspaceBusy` | `test_sandbox_lock_on_root_does_not_block_writes`, `test_lock_wait_is_bounded` |
| MEDIUM: hashing tanpa batas (file sparse 4 GiB = 23 dtk) | tanpa hash di atas 64 MiB; `.agentignore` dibatasi `fstat` + baca terbatas; snapshot satu pembacaan terbatas | `test_sparse_file_is_not_hashed`, `test_sparse_agentignore_is_not_read` |
| MEDIUM: commit patch tidak di-stage dulu; celah rollback | semua temp di-stage dulu, lalu swap; rollback menghapus file yang ditambahkan; rollback gagal → `PatchRollbackFailed` (500) | `test_rollback_removes_added_files`, `test_failed_rollback_is_reported` |
| MEDIUM: newline akhir file, EOL campuran, mode, range baris `splitlines`, jenis akses | EOF wajib cocok dengan file; baris tak tersentuh mempertahankan EOL-nya; mode 120000/160000 ditolak dan 100755 diterapkan; range baris hanya memakai `\n`; jenis akses wajib cocok dengan operasi | `test_final_newline_must_agree_with_the_file`, `test_mixed_line_endings_*`, `test_non_regular_modes_rejected`, `test_executable_mode_is_applied`, `test_line_range_counts_newlines_only`, `test_access_kind_must_match_operation` |
| MEDIUM: guard T3 bisa di-bypass; AGENT_WRITE ke `.github/workflows` | checker menangkap alias `os`, `posix_spawn`, `loop.subprocess_*`, `ctypes`, `multiprocessing`, import dinamis; `.github/workflows\|actions` ditolak untuk AGENT_WRITE (TOOL_CATALOG R4) | self-test checker (17 kasus), `test_policy_matrix` |
| LOW (beberapa) | socket, entri yang hilang saat listing, rantai symlink 1200, lone surrogate → error klien; `disk_usage` via `fwalk`+`dir_fd`; `max_bytes` agen di-clamp; `.GIT` case-insensitive; `Access` di-coerce; escape `\#`/`\!` | `tests/security/test_workspace_hostile.py`, `tests/unit/test_paths.py` |

Diserahkan ke WP-2.1/WP-3.1 (usulan ke Lead, belum diubah):
- **Kontrak API.**
  - `TreeResult.truncated` belum punya tempat di respons tree API_CONTRACTS §3 yang berupa list polos. Usulan: `meta.truncated`.
  - Route harus memetakan `?path=` kosong ke `"."`.
  - `FileChange` sudah diselaraskan (`added`, `sha256`) ditambah field `old_sha256` dan `size`.
- **Thread dan lock (WP-3.1).**
  - Tool yang timeout/cancel tidak menghentikan thread `to_thread`. Hasilnya harus dianggap "outcome unknown".
  - Sandbox tidak ikut `flock`, sehingga version check tidak mencegah lost update dari tulisan sandbox.
- **`.agentignore` tidak tepercaya.** File ini dibaca dari worktree yang bisa ditulis sandbox. Pertimbangkan memuat policy dari `repo/` dan mengandalkan content scan WP-3.1.
- **Mutan yang selamat (redundan):**
  - cek ukuran `.agentignore`. Kontrol yang menahan beban adalah `os.read` berbatas;
  - test FIFO yang menggantung (bukan merah) di bawah mutan `O_NONBLOCK`.

### WP-1.2b (🔒) — import arsip aman (FR-015, AC-02, T2, T4)

Plan: `docs/superpowers/plans/2026-10-24-wp-1.2b-archive-import.md`. Commit f2728fd.

| Pemeriksaan | Hasil aktual |
|---|---|
| `tests/security/test_archive_import.py` | **29 passed**. Kasus `test_rejects`: traversal, absolute, symlink (tar dan zip), hardlink, device, fifo, zip_bomb, gzip_bomb, pax_bomb, too_many_files (50.001 entri, < 15 dtk), oversize, total_oversize, overwrite, file_then_dir, git_dir, backslash, not_an_archive, corrupt_gzip, encrypted_zip, size_lie. Setiap kasus memastikan `dest` tidak ada dan tidak ada folder staging tersisa |
| Mutasi | penghitung stream dihapus → `pax_bomb` merah; cek link tar → 2 merah; cek `.git` → merah. Map duplikat dan `O_EXCL` saling menjaga: masing-masing sendiri selamat, keduanya dihapus → merah. Rasio per entri zip dijaga juga oleh penghitung output |
| Coverage `archive.py` | 95% |

Revisi rancangan: folder pembungkus tunggal **tidak** dikupas secara default. Test menemukan bahwa pengupasan default mengubah project yang hanya berisi `src/` secara diam-diam. Spec keputusan #7 diperbarui.

Risiko sisa: header pax besar bisa memakan memori sampai batas penghitung stream, yaitu ≤ 100 × ukuran arsip dan maksimal 1 GiB.

### WP-1.2c (🔒) — GitAdapter ter-hardening (FR-113–116, ADR-016, T3; temuan snapshot ".git/config → eksekusi host")

Plan: `docs/superpowers/plans/2026-10-24-wp-1.2c-git-adapter.md`. Commit 8def94a.

| Pemeriksaan | Hasil aktual |
|---|---|
| `tests/integration/test_git_adapter.py` + `tests/security/test_git_hardening.py` | **56 passed** (git 2.43 nyata di folder tmp). Mencakup: unborn repo; status/log/branch/commit; worktree `kk/task-*` yang tidak menyentuh main; diff working tree (untracked sebagai `added`, biner, nomor baris, truncation, index user tidak berubah); merge bersih/up_to_date/konflik (abort, tree bersih, HEAD tetap)/dirty/untracked-overwrite; remove_worktree; timeout membunuh process group; batas stdout; error tanpa path absolut |
| Keamanan | hook (`pre-commit`, `post-commit`, `post-merge`, …) tidak berjalan. `<worktree>/.git` yang ditulis ulang ke repo jahat (`filter.evil.clean`, `core.fsmonitor`, `core.hooksPath`) tidak dibaca dan marker tidak muncul. Env persis allowlist (tidak mewarisi env proses), flag `-c`, `--git-dir` eksplisit. Input branch/ref/pesan/author ditolak. Tidak ada `push`/`fetch`/`clone`/`reset`/`clean`/`--force` selain `worktree remove` |
| Mutasi | percaya `<worktree>/.git` → merah; tanpa `core.hooksPath` → merah (post-commit); index asli dipakai → merah; tanpa `merge --abort` → merah; `proc.kill()` alih-alih `killpg` → merah (anak proses selamat) |
| Coverage | `adapter.py` 95%, `diffparse.py` 98% |

Ditunda: `clone_https` (FR-016, P1) ke WP yang mengerjakan `git_https`. Flag protokol sudah dipasang di setiap panggilan.

### WP-1.2d — deteksi stack + 6 template (FR-014, FR-022)

Plan dan implementasi dikerjakan bersamaan. Ini deviasi proses: plan terpisah tidak ditulis sebelum kode, dan test ditulis setelah kode, lalu dibuktikan dengan mutasi. Commit 9868bfa.

| Pemeriksaan | Hasil aktual |
|---|---|
| `tests/unit/test_stack.py` + `tests/integration/test_templates.py` | **22 passed**. Deteksi uv/poetry/pipenv/pip, fastapi/flask/django, pnpm/yarn/npm, react/vite/express, monorepo satu level, Go/Rust/Java, marker rusak diabaikan, folder denylist/dependensi tidak dibaca. Enam template (`python-basic`, `python-cli`, `python-fastapi`, `node-basic`, `node-http-api`, `static-site`) di-materialize lalu test-nya lulus **offline**: pytest + `ruff check/format --isolated` untuk Python, `npm test` (`node --test`) untuk Node. Nama project disisipkan literal |
| Mutasi | akses USER (tanpa denylist) → merah; `pip` untuk folder mana pun → 2 merah; skrip test default npm dihitung → merah |

### Ringkasan WP-1.2 (24 Okt 2026)

| Pemeriksaan | Hasil aktual |
|---|---|
| `uv run pytest -q` (DB `kodekantor_main`) | **1109 passed** |
| ruff · format · pyright · lint-imports | bersih · 101 file · 0 error · 3 kontrak dipatuhi |
| Coverage `app/workspace` + `app/gitops` (`coverage` sementara) | total 95%: `paths.py` 93%, `policy.py` 100%, `fs.py` 94%, `patches.py` 96%, `archive.py` 95%, `adapter.py` 95% |
| Re-review putaran 2 | lihat tabel di bawah |

**Re-review putaran 2** (atas 9868bfa). Hasilnya:

| Sub-WP | Security | Backend |
|---|---|---|
| 1.2a | belum lolos (1 HIGH) | BLOCK (sisa H1) |
| 1.2b | lolos bersyarat (1 MEDIUM) | WARN |
| 1.2c | lolos | WARN |
| 1.2d | — | WARN |

Semua temuan berikut ditutup di 0ffc109:

| Temuan | Perbaikan | Bukti (mutan membuat test merah) |
|---|---|---|
| Security HIGH: pola `.agentignore` seragam dalam batas = 1 dtk per `denied()`, diperbesar `tree` menjadi ±82 menit | DP iteratif dengan biaya pasti dibebankan ke `Budget`; `DENY_BUDGET` per panggilan (habis → ditolak); `TREE_POLICY_BUDGET` per `tree()` (habis → `truncated`); batas 200 pola / 2 `**` | `test_uniform_worst_case_single_call_is_bounded`, `test_tree_with_hostile_agentignore_is_bounded` (tanpa budget tree: 14 dtk, merah) |
| Security MEDIUM: path hasil resolve lebih dalam dari batas → `RecursionError` | `resolve()` menolak > `MAX_DEPTH`; DP tanpa rekursi | `test_link_into_a_deeper_tree_is_too_long` |
| Security MEDIUM: pencarian offset O(n²) di bawah lock | anggaran `OFFSET_SEARCH_BUDGET` → `HunkConflict("too_complex")` | `test_offset_search_is_bounded` |
| Security MEDIUM (1.2b): member zip LZMA/BZIP2 di-inflate tanpa batas di RAM | hanya STORED/DEFLATED | `test_rejects[lzma_zip]`, `test_rejects[bzip2_zip]` |
| Backend HIGH (sisa H1): hunk setelah baris kosong/prosa dilewati | `@@` di luar konteks file → `malformed` | `test_hunk_after_blank_or_prose_line_is_rejected` |
| Backend MEDIUM: `disk_usage` `RecursionError` pada tree dalam | walk fd iteratif, maks 256 folder terbuka → `too_deep` | `test_disk_usage_survives_deep_trees` |
| Backend MEDIUM (1.2b): zip korup → 500 | `ValueError`, `struct.error`, `OSError(EINVAL)` → `format`; `.GIT` case-insensitive | `test_corrupt_zips_are_format_rejections`, `test_rejects[git_dir_upper]` |
| Backend MEDIUM (1.2c) | type change memasangkan 2 blok; output besar → diff `truncated`; diff working tree memakai object store sementara (repo tidak ditulis); `merge_branch(into=)` → `wrong_branch`; I/O registry worktree via `to_thread`; karakter kontrol di pesan commit ditolak | 5 test baru di `test_git_adapter.py` + kasus input |
| Backend MEDIUM (1.2d) | parser marker tahan tipe salah dan nesting dalam; nama project di-escape di `.html`; `materialize` lewat staging | `test_hostile_markers_never_crash`, `test_project_name_is_escaped_in_html`, `test_failed_materialize_leaves_nothing` |

Hasil: `uv run pytest -q` → **1140 passed**; ruff, format, pyright, dan lint-imports bersih; gitleaks lokal (v8.24.3) tidak menemukan temuan baru.

**Hand-off ke WP-2.1 / WP-3.1** (usulan ke Lead, belum diubah):

| Area | Hand-off |
|---|---|
| Diff untuk LLM | Tool `git.diff` wajib menyaring file dengan `Policy.denied(AGENT_READ)`; `diff()` mengembalikan isi semua path, termasuk `.env` yang untracked. |
| `commit_all` | `POST /git/commit {paths?}` butuh parameter `paths`; sekarang `commit_all` men-stage semuanya. |
| Lock bersama | Satu serialiser per project untuk tulis IDE, `commit_all`, dan `merge`, karena sandbox tidak ikut `flock`. |
| ARCHITECTURE §4.2 dan SECURITY_MODEL T2 | Signature perlu diperbarui: `TaskWorkspace` async dengan `tree` → `TreeResult`, tanpa `resolve`; `GitTarget`; `merge_branch(..., into=)`; `commit_all -> str \| None`; `ff_only` dihapus; `clone_https` ditunda. |
| Panggilan sync | `import_archive` dan `materialize` sinkron, jadi service memanggilnya lewat `to_thread`. |
| Sisa folder | Folder `.*.importing-*`, `.*.creating-*`, dan file `.kk-locks/*.lock` yang tertinggal saat crash belum di-sweep. |
| `*.env` | Nama dengan akhiran `.env` (mis. `config/prod.env`) tidak tercakup kontrak `.env*`; perlu keputusan Lead. |


**Status review akhir WP-1.2 (atas 0ffc109, dengan LOW ditutup di commit berikutnya):**

| Sub-WP | `kk-security-reviewer` (pra-review) | `kk-backend-reviewer` |
|---|---|---|
| 1.2a 🔒 | **SIGNED OFF** (pra-review): T2 `test_paths.py` + `test_workspace_hostile.py`; T5 `test_policy_matrix`; T9 test konkurensi/konflik; T3 `test_no_host_exec.py` | **APPROVE** |
| 1.2b 🔒 | **SIGNED OFF** (pra-review): T2/T4 `test_archive_import.py` | **APPROVE** |
| 1.2c 🔒 | **SIGNED OFF** (pra-review): T3 + temuan snapshot ".git/config → eksekusi host" ditutup oleh `test_git_hardening.py` | **APPROVE** |
| 1.2d | — | **APPROVE** |

- **LOW yang ditutup setelah verdict:**
  - `tree()` mempertahankan entri yang sudah dicek saat budget habis;
  - path raw yang terpotong di diff yang di-cap dibuang;
  - kegagalan `copytree` tidak meninggalkan folder staging.
- **LOW dan INFO yang tetap terbuka (dicatat, bukan blocker):**
  - **Policy berat.** `.agentignore` besar tanpa jangkar (±200 pola) bisa menolak path agen di kedalaman ≥ 100. Ini gagal tertutup.
  - **Zip.** Central directory zip di-parse penuh sebelum cek jumlah entri; dibatasi oleh cap arsip 200 MiB.
  - **Spec #7.** Keputusan `strip_single_root=False` perlu direkonsiliasi oleh Lead. Spec sudah diperbarui, tetapi rancangan awal berbeda.
  - **Log git.** `_log_failure` meredaksi `tree.parent` saja. Path git dir worktree bisa muncul di log server, tidak pernah di respons klien.
- **Sign-off formal AG-13** untuk WP-1.2 🔒 tetap wajib dari **sesi terpisah** sebelum G1. Pra-review di atas dilakukan di sesi yang sama yang membangun WP ini (§6 #10 spec sistem pengembangan).

### Perbaikan pasca-merge — ADR-026 (4) di GitAdapter (24 Okt 2026)

Celah ini ditemukan agent sendiri saat membaca ADR untuk WP-1.3; tidak ditangkap oleh dua reviewer. WP-1.2c (sudah di `main` lewat PR #2) **belum memenuhi ADR-026 butir (4)**, padahal ADR tersebut berstatus Accepted. Butir itu mewajibkan GitAdapter *menolak*:
- key config di luar allowlist;
- object alternates;
- file `.git` worktree yang dipalsukan.

Implementasi sebelumnya hanya *mengabaikan* `.git` palsu lewat `--git-dir` eksplisit, dan tidak memeriksa config maupun alternates.

| Perbaikan | Bukti |
|---|---|
| Config repo dibaca dengan `git config --file --no-includes --list --name-only`. Key di luar allowlist → `git_metadata_unsafe`. Allowlist: `core.{repositoryformatversion,filemode,bare,logallrefupdates,ignorecase,precomposeunicode,symlinks}`, `remote.*.{url,fetch}`, `branch.*.{remote,merge}` | `test_unsafe_repository_config_is_refused` (9 key: fsmonitor, hooksPath, sshCommand, filter, diff.external, include.path, alias, extensions.worktreeConfig, core.worktree); `test_clone_style_config_is_allowed` |
| `objects/info/{alternates,http-alternates}` → `git_metadata_unsafe` | `test_object_alternates_are_refused` |
| `<worktree>/.git` wajib file reguler `gitdir: <git dir terdaftar>`. Bila tidak → `git_metadata_unsafe`; `remove_worktree` tetap bisa membersihkan | `test_forged_worktree_gitdir_is_refused` |
| Mutasi | allowlist dihapus → 9 merah; cek alternates dihapus → merah; verifikasi gitfile dihapus → merah |

Hasil: `uv run pytest -q` → **1153 passed**; pyright 0 error; lint-imports 3 kontrak dipatuhi.

Catatan proses: checklist kontrak per sub-WP belum memaksa pembacaan semua ADR *Accepted* yang menyebut modul terkait. Usulan retro: spec WP wajib punya tabel "ADR Accepted yang menyebut modul ini → butir → test".

### WP-1.3 (🔒) — sandbox manager + image + klien (FR-068–083 runtime, AC-07, AC-08 bagian runner)

Spec: `docs/superpowers/specs/2026-10-24-wp-1.3-sandbox-manager-design.md` (dengan tabel ADR → test). Plan: `docs/superpowers/plans/2026-10-24-wp-1.3-sandbox-manager.md`. Commit 0961666.

| Pemeriksaan | Hasil aktual |
|---|---|
| `cd sandbox_manager && uv run ruff check . && uv run ruff format --check . && uv run pyright` | bersih; pyright 0 error |
| `uv run pytest -q` (sandbox_manager, tanpa Docker) | **101 passed**, 18 deselected |
| `uv run pytest -q -m docker` (dockerd nyata, image `sandbox-python:1`/`sandbox-node:1` dibangun dari `sandbox-images/`) | **18 passed**: uid 10001 + CapEff/CapBnd 0 + NoNewPrivs; rootfs EROFS (termasuk `/var/tmp` yang 1777); hanya `lo`, tanpa DNS/IP; env tanpa token/secret; fork bomb ≤ pids 64; timeout → kill; kill per task; `.git` folder dan file worktree tersembunyi; output terpotong; run `repo` read-only + artefak; DISK_LIMIT; OOM → 137; profil nyata `python.pytest`/`python.ruff`/`node.npm_test`; tidak ada container/folder artefak tersisa; container yatim dihapus saat start |
| Backend `uv run pytest -q` (PostgreSQL 16 + Redis 7) | **1205 passed** (1153 + 52 baru); pyright 0 error; lint-imports 3 kontrak dipatuhi (layer `app.workspace \| app.gitops \| app.sandbox`) |
| pip-audit | `sandbox_manager/uv.lock` dan `sandbox-images/python/requirements.txt` → no known vulnerabilities (setelah pengecualian certifi yang sama dengan backend, PYSEC-2026-4320) |
| gitleaks `protect --staged` | no leaks found |
| Mutasi (dijalankan, lalu dipulihkan) | kill setelah start dihapus → merah; `CapDrop` kosong → merah (docker); `ReadonlyRootfs` false → merah (docker, setelah probe diperkuat ke EROFS); `.git` tidak disembunyikan → 2 merah; `PidsLimit` 0 → merah; cek realpath subpath dihapus → 2 merah; skema `Bearer` tidak dicek → 2 merah; artefak tanpa `O_NOFOLLOW` → merah. `NetworkMode` saja dibuka → hijau karena `NetworkDisabled` masih menutup; keduanya dibuka → merah |

Bug yang ditemukan saat eksekusi (masing-masing dengan test regresi):
- status final ditulis sebelum container dihapus;
- kill yang datang saat container dibuat hilang, sehingga container jalan sampai timeout (ditemukan test Docker nyata);
- tmpfs `/home/sandbox` milik root, sehingga HOME tidak bisa ditulis (ditemukan test Docker nyata);
- `wait=true` lewat batas → 500;
- `/health/ready` (WP-1.1) memanggil manager tanpa token Bearer.

Deviasi kontrak: konflik #36 (mekanisme artefak ADR-033 tidak bekerja di Docker 29/runc; diganti bind folder per run) dan #37 (klien memakai polling `/logs` karena `on_log`). Keduanya perlu keputusan PO; tercatat di spec sistem pengembangan §8.

Belum dicakup: runsc hanya diuji di level spec (`test_runtime_runsc`); egress (ADR-007) hanya validasi, jaringan egress di WP-1.5; profil `node.npm_test` tanpa JUnit [Menebak].

#### WP-1.3 — review putaran 1 dan perbaikannya (24 Okt 2026)

Tiga reviewer berjalan paralel pada 0961666:
- `kk-backend-reviewer`: REQUEST CHANGES, 4 HIGH;
- `kk-silent-failure-hunter`: 21 temuan, 2 HIGH;
- `kk-security-reviewer` (AG-13 pra-review): **TIDAK DITANDATANGANI**, blocker F1 HIGH.

Perbaikan ada di 0ff8379 (sandbox manager) dan b676c04 (klien backend).

| Temuan (sumber) | Perbaikan | Test |
|---|---|---|
| Folder artefak per run tak terbatas, tak dihitung, bisa tertinggal (AG-13 F1, backend HIGH, SF F2/F12) | Folder pindah ke `<project>/.kk-runs/<run_id>` (kuota per project); dibuat dengan dir fd + `O_NOFOLLOW`; watchdog 1 dtk, > 8 MiB → kill → `DISK_LIMIT`; dihitung dalam pertumbuhan untuk semua run termasuk read-only; `disk.py` mengukur gagal-tertutup dan menghapus secara iteratif (folder terkunci dibuka dulu); sapuan saat start | `test_disk.py` (6, juga lulus sebagai uid 65534); `test_watchdog_kills_on_artifact_folder_growth`; Docker: `test_read_only_run_cannot_flood_the_artifact_folder`, `test_hostile_artifact_tree_is_removed` (folder `chmod 000` + kedalaman 1500), `test_disk_helpers_as_sandbox_uid` (uid 10001 di container) |
| Pengukuran disk buta terhadap folder tak terbaca (backend HIGH, SF F2b) | `tree_bytes` → `Unmeasurable` → `WORKSPACE_UNSAFE`/`DISK_UNMEASURABLE`; setiap entri dihitung ≥ 512 B | `test_locked_folder_is_unmeasurable` |
| Log Docker tak terbatas (backend HIGH, AG-13 F7) | `LogConfig` json-file `max-size` max(4× output, 16 MiB), 2 file | `test_container_log_is_capped`; Docker: `test_container_log_cap_is_accepted_by_docker` |
| Deadline klien menghitung antrean (backend HIGH, SF F9) | `/logs` memuat `status`; deadline mulai saat run keluar dari antrean; batas antre terpisah | `test_queue_time_is_not_run_time`, `test_queue_limit` |
| Status final saat container mungkin masih hidup (AG-13 F2, SF F3) | remove 3×; gagal → `CONTAINER_NOT_REMOVED`, folder artefak tidak disentuh; reaper 60 dtk per instance | `test_container_not_removed_is_an_error`, `test_reaper_removes_only_inactive_own_containers` |
| Registry tak pernah evict, chunk per frame, `/logs` tanpa halaman (AG-13 F3, backend MEDIUM) | eviction (1 jam / 500), gabung frame sampai terkirim, halaman ±256 KiB, `done` hanya setelah semua chunk terkirim | `test_finished_runs_are_evicted`, `test_chunks_merge_only_until_delivered`, `test_logs_are_paged` |
| Kill run antre tidak segera; satu kill gagal menghentikan yang lain (backend MEDIUM) | antre → `killed` seketika; kill per run dengan hitungan `failed`; `DriverError` → 503 | `test_kill_while_queued_is_final_at_once`, `test_one_failed_kill_does_not_stop_the_others`, `test_kill_of_unreachable_container_is_503` |
| Artefak ditolak tak terbedakan dari "tidak ada test" (SF F1) | profil menyatakan `artifacts: [junit]`; `artifact_status`; exit 0 tanpa laporan → `ARTIFACT_MISSING`/`ARTIFACT_REJECTED`; baca sampai EOF | `test_required_junit_missing_is_an_error`, `test_required_junit_too_large_is_rejected`, `test_missing_junit_on_a_failed_run_stays_failed` |
| OOM tampil sebagai test gagal; kode error hilang (SF F5) | `State.OOMKilled` → `OOM_KILLED`; galat Docker dicatat dengan status dan pesan; exit code timeout dipertahankan | `test_oom_is_not_a_test_failure`; Docker: `test_memory_limit` |
| Stream log putus dianggap lengkap; tail sebenarnya potongan awal (SF F4, backend LOW) | `log_incomplete`; tail = 64 KiB terakhir dalam byte; decoder inkremental per stream | `test_broken_log_stream_is_flagged`, `test_tail_is_the_last_bytes`, `test_multibyte_character_split_across_frames` |
| Konfigurasi 0/negatif = tanpa batas; socket langsung di production (backend MEDIUM, SF F6) | semua batas > 0 dan finite; `DOCKER_HOST` default proxy, `unix://` ditolak di production; uid selain 0/10001 ditolak di production; `SANDBOX_TIMEOUT_SECONDS` dipakai | `test_bad_settings_refused` (19 kasus), `test_docker_socket_refused_in_production`, `test_process_user_checked_in_production`, `test_profile_without_timeout_uses_the_setting` |
| Klien menerima record tak koheren; run yatim saat batal; tanpa idempotensi (SF F8/F9, backend MEDIUM) | cek `run_id`, `succeeded ⇒ exit 0`, `failed ⇒ exit ≠ 0`; kill saat batal atau `on_log` gagal; `client_request_id` | `test_incoherent_records_are_not_trusted` (7), `test_cancelled_caller_kills_the_run`, `test_failing_on_log_kills_the_run`, `test_client_request_id_returns_the_same_run`, `test_retry_with_client_request_id` |
| Readiness hijau padahal tak bisa menjalankan run; penyebab tak dicatat (SF F7, backend MEDIUM) | `/healthz` memuat `images`; klien mensyaratkan Docker + semua image, menolak driver fake di production, mencatat alasan | `test_health` (8), `test_fake_driver_allowed_outside_production` |
| Parser JUnit: `<skipped>` menutupi `<error>`; encoding jahat (SF F10, backend MEDIUM) | status terburuk per kasus, error tingkat suite, `inconsistent`, `cases_truncated` dengan kasus gagal di depan; encoding → `JUnitError`; coverage `lines-valid=0` → `None` | `test_worst_outcome_of_a_case_counts`, `test_suite_level_errors_count`, `test_claims_beyond_the_cases_are_flagged`, `test_failing_cases_survive_truncation`, `test_hostile_encoding_declarations`, `test_coverage_of_nothing_is_not_full_coverage` |
| JUnit dapat dipalsukan project (SF F11, AG-13 catatan) | didokumentasikan: laporan = klaim, exit code = bukti; mitigasi di WP-4.x | docstring `junit.py`, spec keputusan #15 |
| Token di `repr`, `/openapi.json` terbuka, `BASE_IMAGE` tak terikat digest, fallback 0777 (AG-13 F5/F6/F9/F8) | `field(repr=False)`, `openapi_url=None`, `FROM ${BASE_REPO}@sha256:…`, uid dicek di production | `test_token_is_not_in_repr`, `test_openapi_is_not_served` |
| `FakeSandboxClient` sukses diam-diam (SF F18) | default `error`/`FAKE_NOT_PROGRAMMED`; fixture junit berisi testcase | `test_unprogrammed_fake_never_succeeds` |

Bukti setelah perbaikan:
- sandbox_manager `uv run pytest -q` → **146 passed, 1 skipped** (skip hanya saat root; test yang sama lulus sebagai uid 65534);
- `-m docker` lokal → **22 passed**; CI `sandbox-isolation` (root) → **22 passed**;
- backend `uv run pytest -q` → **1231 passed**; pyright 0; lint-imports 3 kontrak dipatuhi;
- pip-audit bersih; gitleaks bersih.

Konflik dokumen baru: #38 (API_CONTRACTS §10 tertinggal: field tambahan dan semantik `wait=true`). Tidak dikerjakan: F4 AG-13 (sapuan global saat start) dipertahankan dengan asumsi satu manager per daemon, terdokumentasi di spec.

#### WP-1.3 — review keamanan putaran 2 (24 Okt 2026)

`kk-security-reviewer` pada diff perbaikan (9711b56..b676c04): **TIDAK DITANDATANGANI**.
- F1–F9 putaran 1 tertutup, atau tertutup sebagian dengan sisa yang terdokumentasi.
- Blocker baru: N1 HIGH.

| Temuan | Perbaikan | Test |
|---|---|---|
| N1 HIGH: pohon artefak lebih dalam dari PATH_MAX (≈ 2000 level lewat `chdir`) tidak bisa dihapus, juga oleh sapuan start; disk bocor ≈ 316 MiB per run | `remove_tree` berbasis descriptor folder: isi sub-folder diangkat ke atas dengan `rename` antar-descriptor (nama acak), folder dibuka (`chmod`) sebelum diangkat; tanpa path panjang. Gagal hapus → `ARTIFACTS_NOT_REMOVED` | `test_tree_deeper_than_path_max_is_removed`, `test_name_clashes_do_not_stop_removal`, `test_artifact_folder_not_removed_is_reported`; Docker: `test_artifact_tree_deeper_than_path_max_is_removed`, `test_disk_helpers_as_sandbox_uid` (uid 10001: kedalaman 2100 + folder terdalam terkunci) |
| N2 MEDIUM: `client_request_id` mengembalikan run milik task/args/mode lain | fingerprint lengkap request tervalidasi; beda → `IDEMPOTENCY_CONFLICT` | `test_client_request_id_returns_the_same_run` (5 varian) |
| N3 LOW: batas 8 MiB tidak dicek setelah run | `tree_bytes(artifacts) > 8 MiB` setelah run → `DISK_LIMIT` | `test_artifact_folder_over_cap_after_run` |
| N4 LOW: rotasi log Docker membuang entri tanpa flag | batas dinaikkan ke max(8 × output, 64 MiB); sisa risiko dicatat di spec keputusan #18 | `test_hardened_hostconfig`, `test_container_log_is_capped`; Docker: `test_container_log_cap_is_accepted_by_docker` |
| N5 LOW: reaper memakai snapshot run aktif yang basi | run aktif dibaca setelah listing | `test_reaper_reads_active_runs_after_listing` |
| F4 (sisa): sapuan start global | usulan ADR, konflik #39 | – |

Bukti:
- sandbox_manager `uv run pytest -q` → **151 passed, 1 skipped**;
- test disk sebagai uid 65534 → **8 passed**;
- `-m docker` lokal (root) → **23 passed**;
- ruff dan pyright bersih.

Verifikasi AG-13 atas N1–N5 dilakukan di sesi sign-off formal (rencana lanjutan U4). PR #3 tetap draft sampai sign-off itu ada.

### WP-1.5a (🔒) — compose produksi, image service, role DB runtime (FR-136–137, FR-149, AC-20)

Spec: `docs/superpowers/specs/2026-10-25-wp-1.5a-compose-design.md`. Plan: `docs/superpowers/plans/2026-10-25-wp-1.5a-compose.md`. PR [YosiaGalih/IDEE-Application#4](https://github.com/YosiaGalih/IDEE-Application/pull/4).

| Pemeriksaan | Hasil aktual |
|---|---|
| `python3 -m unittest discover -s infra/tests` | **23 OK** (kebijakan compose 18, `prepare-workspace.sh` 5; 1 skip bila bukan root) |
| Mutasi compose (14) | merah semua: port di api, sandbox-manager di edge, worker di exec, `INFO=1`, `EXEC=1`, socket rw, socket di sandbox-manager, kunci API ke semua backend, runtime sebagai owner, edge tidak internal, path workspace beda, image tanpa digest, worker tanpa pids |
| `docker compose -f infra/compose.yml --env-file infra/tests/ci.env up -d --wait` (container cloud) | 10 service healthy, migrate `Exited (0)` |
| `KK_ENV_FILE=infra/tests/ci.env scripts/healthcheck.sh` | live 200, ready 200 (db, redis, sandbox_manager, migrations ok), `/` 200 lewat Caddy; header CSP/HSTS/XFO ada |
| Probe socket-proxy dari network `exec` | `_ping`, `version`, `containers/json`, `images/json` 200; `info`, `volumes`, `networks`, `events`, `POST exec/x/start` 403 |
| Backend `uv run pytest -q` | **1243 passed, 1 skipped** (13 test role runtime; yang di-skip butuh koneksi superuser dan berjalan di CI); pyright 0; lint-imports 3 kontrak dipatuhi |
| `caddy validate` | Valid configuration |
| CI `clean-install` di runner GitHub ([run 40171760603](https://github.com/YosiaGalih/IDEE-Application/actions/runs/40171760603/job/121104016297), commit fcbdcb6) | `compose up --build --wait` sukses; `healthcheck.sh`: semua container healthy, migrate exit 0, live/ready/`/` 200 lewat Caddy |

Temuan saat menjalankan stack (masing-masing diperbaiki):
- API menolak start sebagai superuser (audit guard WP-1.1b) → role runtime `kodekantor_app` + `grant-runtime-role`;
- socket-proxy mati tanpa IPv6;
- biner caddy ber-file-capability tidak bisa exec sebagai uid 101;
- path jadwal beat;
- parsing status di `healthcheck.sh`;
- CI `infra-config` merah di runner: Compose v2 membuang `create_host_path: false` saat render (v5 membuang default `true`) → test membaca flag dari teks sumber; dua mutasi `create_host_path: true` merah.

Konflik dokumen baru: #40–#43.

#### Review AG-13 (`kk-security-reviewer`) @ de47716 — **SIGNED OFF** [Kemungkinan Besar]

Tanpa CRITICAL/HIGH. Ancaman T3, T5, T8, T10 diuji dengan test dan probe live (proxy, network, Caddy, scan kebocoran, gitleaks, pip-audit). Temuan dan penutupannya:

| Temuan | Penutupan | Bukti |
|---|---|---|
| M1 uv 0.11.32 `PYSEC-2026-4321` (build image dan CI) | Pengecualian **EX-01** di `DECISIONS.md` (menunggu persetujuan PO), kedaluwarsa 2026-11-03; langkah "Build tool audit" di `infra-config` mengabaikannya hanya sampai tanggal itu | simulasi tanggal: 2026-11-03 → exit 0, 2026-11-04 → exit 1 |
| M2 fungsi bisa dieksekusi PUBLIC; guard hanya cek owner; deny-list append-only | `grant_runtime_role` mencabut EXECUTE fungsi `public` dari PUBLIC (juga default privileges global milik owner); guard gagal pada keanggotaan role owner dan pada fungsi SECURITY DEFINER yang bisa dieksekusi; `APPEND_ONLY_TABLES` dicocokkan test dengan trigger `<table>_no_mutation` | 5 test baru; 5 mutan merah (satu butuh superuser, dijalankan lokal dengan superuser sementara); live: runtime role tidak bisa EXECUTE kedua fungsi trigger, trigger tetap jalan |
| L2 password role runtime di argv psql dan log server | `\getenv` + `log_statement=none` + `log_min_error_statement=panic` di sesi init | re-run gagal: password muncul di 0 baris log (skrip lama: 1) |
| L3 secret berlebih ke migrate/scheduler | placeholder non-rahasia (DEPLOYMENT §7); `SECRET_KEY` hanya api dan worker | test kebijakan diperketat, 3 mutan merah; `docker inspect` live |
| L4 `make workspace` bisa chown path apa pun | `scripts/prepare-workspace.sh` (dipakai `make workspace` dan CI): hanya path absolut polos tanpa `.`/`..`; path yang ada harus folder milik 10001 | 5 test, 4 mutan merah; `/srv` ditolak dan tidak berubah |
| L5 checkout menyimpan kredensial; konteks build memuat `.git`/`.env` | `persist-credentials: false`; `.dockerignore` allowlist | konteks build 129 berkas, tanpa `.git`/`.env`/cache; kedua image ter-build |
| L1 `--forwarded-allow-ips "*"` | **belum**: butuh subnet `edge` tetap (konfigurasi operator); lewat Caddy aman (XFF ditimpa) | utang di bawah |

Konflik dokumen dari review: #44 (jaringan token sandbox-manager); spec WP-1.5a diperbarui (image `web`, `pull_policy`, `clean-install`).

Utang terbuka (repo belum punya issue; issue WP dibangkitkan dari `wbs_data.py`, jadi dicatat di sini):
- ~~L1: subnet `edge` tetap + `--forwarded-allow-ips=<subnet>` + test kebijakan~~ → ditutup di WP-1.5b.
- M2 jangka menengah: role owner non-superuser untuk `migrate`.
- Trivy dan Semgrep belum pernah berjalan (unduhan DB/ruleset diblokir proxy cloud); wajib di job security-scan sebelum G1 (SECURITY_MODEL §11).
- ~~hadolint, shellcheck, actionlint~~ → WP-1.5b.

### WP-1.5b (🔒) — egress-proxy, linter CI, IP klien hanya dari Caddy (FR-136–137, FR-149, AC-20)

Spec: `docs/superpowers/specs/2026-10-30-wp-1.5b-egress-lint-design.md`. Plan: `docs/superpowers/plans/2026-10-30-wp-1.5b-egress-lint.md`.

| Pemeriksaan | Hasil aktual |
|---|---|
| `python3 -m unittest discover -s infra/tests` | **35 OK** (kebijakan compose 25, `healthcheck.sh` 5, `prepare-workspace.sh` 5; dirender dengan profil `egress` dan tanpa profil) |
| Mutasi compose/konfigurasi (10) | merah semua: proxy juga di `app`, `kk-egress` tanpa `name`, `kk-egress` tidak internal, proxy tanpa profil, api percaya `*`, image percaya `*`, entri allowlist tanpa jangkar, `ConnectPort 80` tambahan, `Upstream`, caddy tanpa IP tetap |
| Mutasi `healthcheck.sh` | tanpa penjaga egress → merah |
| `tinyproxy.conf` + `allowlist` di tinyproxy 1.11.1 (harness Ubuntu sekali pakai; repo apk Alpine diblokir proxy cloud) | `egress_probe.py` 12/12: tiga registry membuka tunnel; `github.com`, `evilpypi.org`, `pypi.org.evil.com`, IP mentah, port 80/22, HTTP polos → 403; tanpa DNS dan rute langsung; 10 penolakan tercatat di log |
| Probe terhadap proxy yang salah konfigurasi | `FilterDefaultDeny No` → 7 cek gagal; `ConnectPort 80` tambahan tidak tertangkap probe (filter URL tetap menolak), tertangkap test kebijakan |
| Linter lokal (image sama dengan CI) | hadolint 2.14.0, shellcheck 0.11.0, actionlint 1.7.12: bersih; mutan `RUN cd`, `ls $x`, `${{ github.event.pull_request.title }}` di `run` → merah |
| Stack `ci.env` (tanpa profil egress) | healthy; `xff_probe.sh` PASS: XFF palsu dari `web` → kunci `10.231.231.9` (IP web); lewat Caddy → `172.21.0.1` (klien yang dilihat Caddy), bukan IP Caddy |
| Mutan `FORWARDED_ALLOW_IPS="*"` | `xff_probe.sh` FAIL: `198.51.100.9` dipercaya |

Temuan saat menjalankan:
- IP tetap caddy diambil container lain (`Address already in use`), lalu diperbaiki dengan `ip_range` dinamis di luar IP caddy, dan test kebijakan ditambah.
- `xff_probe.sh` versi awal hanya menghapus satu kunci karena `docker compose exec` menelan stdin loop; sekarang dihapus dengan satu perintah di container redis.

Konflik dokumen baru: #45–#49.

CI di runner GitHub:
- Run pertama gagal di build karena versi tebakan `tinyproxy=1.11.2-r1` (alpine 3.24 memuat `1.11.3-r0`). Versi lalu dipin ke 1.11.3-r0.
- Run berikutnya, [job clean-install 123976143238](https://github.com/YosiaGalih/IDEE-Application/actions/runs/41029693176/job/123976143238) (fd6a06e, image alpine asli, `COMPOSE_PROFILES=egress`):
  - 11 service healthy; live/ready/`/` 200;
  - `egress_probe.py --strict-tls` 12/12 (TLS asli ke tiga registry; 403 untuk sisanya; 5 penolakan tercatat);
  - `xff_probe.sh` PASS (kunci `10.231.231.9` dan `172.20.0.1`).

#### Review AG-13 (`kk-security-reviewer`) @ 4f037b1/8c8a7c1 + delta fd6a06e — **SIGNED OFF** [Kemungkinan Besar]

Tanpa CRITICAL/HIGH. Penutupan L1 WP-1.5a terbukti live: XFF palsu dari web dan worker, XFF berganda, `Forwarded`, `X-Real-IP`, dan h2 lewat Caddy semuanya diabaikan. Probe adversarial 24 kasus terhadap tinyproxy 1.11.1/1.11.3 ditolak semua. Trivy: alpine 3.24.3 dan tinyproxy 1.11.3-r0 0 temuan; kontrol 1.11.1 → CVE-2022-40468, CVE-2023-49606. gitleaks bersih.

Syarat sign-off: (1) `clean-install` dengan profil `egress` hijau di head PR, termasuk kedua probe; (2) M1 dan M2 dicatat sebagai item terbuka.

| Temuan | Penutupan | Bukti |
|---|---|---|
| M1 container di `kk-egress` menjangkau host lewat gateway bridge | `inhibit_ipv4` + `enable_ipv6: false` pada `kk-egress`; test kebijakan; `egress_probe.py --host-port` + listener runner di CI (kontrol dari bridge default) | lokal: tanpa opsi → `CONNECTED hello-from-host`; dengan opsi → refused; probe pada mutan → FAIL |
| M2 allowlist CONNECT bukan penghalang eksfiltrasi (SNI/CDN multi-tenant, npm bisa ditulis) | **terbuka**, syarat sebelum profil `egress-allowed` pertama / G4: spec "Risiko sisa", konflik #49 | – |
| L1 penolakan port dan tunnel yang dibuka tidak tercatat | `LogLevel Connect` | 10 target yang diminta tercatat di log (stack lokal) |
| L2 compose mengadopsi `kk-egress` asing | `healthcheck.sh` memeriksa internal, label project, dan `inhibit_ipv4` | network asing ditanam → healthcheck exit 1 dengan ketiga alasan; 3 subtest |
| L3 mematikan egress tidak menghentikan proxy | peringatan di `healthcheck.sh`; `make egress-off` | live: proxy dan `kk-egress` hilang, stack 10 service healthy |
| L4 celah probe | cek DNS eksplisit, cek listener host, kunci jalur Caddy = gateway network `public` | stack lokal dengan profil `egress` (image pengganti tinyproxy Ubuntu, konfigurasi sama): semua PASS |
| L5 tinyproxy menghadapi kode tidak tepercaya | keputusan PO di konflik #45; `egress-proxy` masuk daftar Trivy (#48) | Trivy 1.11.3-r0: 0 |
| L6 URL ditolak dicatat apa adanya (userinfo) | didokumentasikan di `tinyproxy.conf` dan spec: log proxy tidak diteruskan tanpa redaksi | – |

Utang terbuka:
- M2: syarat G4, lihat di atas.
- Keputusan produk proxy: #45.
- Trivy/Semgrep di security-scan sebelum G1.

### WP-1.5c (🔒) — backup, restore, drill restore (NFR-10, PRD §7.5/§8.1, AC-20; T9)

Spec: `docs/superpowers/specs/2026-10-31-wp-1.5c-backup-restore-design.md`. Plan: `docs/superpowers/plans/2026-10-31-wp-1.5c-backup-restore.md`.

| Pemeriksaan | Hasil aktual |
|---|---|
| `python3 -m unittest discover -s infra/tests` | OK, 1 skip sebagai root (pesan non-root). `test_backup_restore.py` 16 test; sebagai non-root 2 test workspace di-skip (butuh root) |
| Mutan unit (5) | merah semua: backup gagal tertinggal, retensi tanpa jangkar akhir, pengecualian project menjangkau lebih dalam, restore tanpa cek checksum, konfirmasi setelah service berhenti |
| `infra/tests/restore_drill.sh` di stack `ci.env` | PASS: backup (berkas, mode 0700/0600, `SHA256SUMS`, counts = DB, pengecualian); setelah dirusak lalu di-restore, DB dan setiap file sama dengan saat backup (mode, owner, mtime, sha256), `git fsck` bersih, `ops.restore` tercatat, `pre-restore` berisi keadaan rusak; backup yang diubah satu byte ditolak dan api tetap berjalan. Restore 36 dtk |
| Mutan drill (3) | tanpa ACL database → api unhealthy, restore gagal; ekstrak di atas workspace lama → jumlah entri tidak cocok; tanpa cek checksum → backup palsu di-restore, drill FAIL |

Temuan saat menjalankan:
- `--exclude='./*/uploads'` di tar juga mencocokkan `repo/uploads` milik pengguna, karena `*` mencocokkan `/` → diperbaiki dengan `--no-wildcards-match-slash`.
- Format tar GNU hanya menyimpan mtime per detik → diganti format pax.
- Ekstraksi sebagai uid 10001 gagal bila folder induk tidak bisa dijangkau 10001, padahal workspace lama sudah dipindah → sekarang dicek sebelum apa pun disentuh.

Konflik dokumen baru: #50–#55. RUNBOOK §7 diselaraskan.

CI di runner GitHub, [job clean-install 124445841969](https://github.com/YosiaGalih/IDEE-Application/actions/runs/41170047303/job/124445841969) (43a4ef6, profil `egress`): drill `all checks passed`, restore 35 dtk.

#### Review AG-13 (`kk-security-reviewer`) @ 43a4ef6 — **SIGNED OFF** [Kemungkinan Besar]

Tanpa CRITICAL/HIGH. Yang terbukti saat review:
- 14 arsip jahat (path absolut, `..`, symlink/hardlink keluar, device) tidak bisa menulis di luar workspace saat diekstrak sebagai 10001.
- Interpolasi SQL `ops.restore` aman.
- 0 secret di 113.296 sampel argv selama drill nyata.

| Temuan | Penutupan | Bukti |
|---|---|---|
| M1 `pg_restore` sebagai superuser; `SHA256SUMS` hanya integritas | sesudah load, role harus tepat `kodekantor:true,kodekantor_app:false`; RUNBOOK dan header skrip menyatakan keaslian datang dari alat off-site; risiko sisa → konflik #55 | test dump yang menambah role/superuser ditolak; mutan tanpa cek → merah |
| M2 DB di-drop sebelum dump terbukti | pre-flight `pg_restore --list` sebelum service berhenti; DB lama di-rename `kodekantor_pre_restore_<utc>` (bukan DROP); trap mencetak langkah kembali | test load gagal → DB lama tersimpan + instruksi; mutan DROP → merah; drill memeriksa DB lama berisi keadaan rusak. **Live:** cek role yang salah di drill menghentikan restore dan mencetak instruksi rollback yang benar |
| M3 arsip melonggarkan mode root / setuid | `chmod 0750` root + buang bit setuid/setgid setelah ekstraksi; RUNBOOK: mount `nosuid,nodev` | test root 0777 + file 4755 → 0750 / 0755; mutan → merah |
| M4 nama file ber-newline mematahkan hitungan | hitung entri dengan `find -printf . \| wc -c` | test nama `two\nlines.txt` lulus; mutan → merah |
| M5 backup live gagal bila file berubah | exit 1 tar diterima, `workspace_consistent: false` + peringatan; exit ≥ 2 tetap gagal; RUNBOOK: alert umur backup > 26 jam | 2 test (status 1 / 2); mutan → merah |
| M6 workspace sebagai mount point; ruang disk | mount point: pohon lama ke `<path>/.pre-restore-<UTC>` (0700, dikecualikan backup); cek folder asli milik 10001 dan ruang untuk salinan kedua sebelum apa pun berhenti | test tmpfs mount point + test ruang kurang; mutan → merah |
| L1 kunci setelah verifikasi / dilewati | `mkdir` + `flock` paling awal | test kunci dipegang → ditolak tanpa panggilan docker |
| L2 folder backup bisa ditulis pihak lain / symlink | wajib milik root, tanpa tulis g/o, file biasa | test 0777 dan symlink ditolak |
| L3 quiesce tanpa sandbox-manager / restart diam | sandbox-manager ikut dihentikan; gagal start dilaporkan | test quiesce |
| L4 pemilik `BACKUP_DIR`, symlink `.lock` | wajib milik user yang menjalankan, tanpa symlink | test folder milik 65534 ditolak |
| L5 symlink keluar workspace bertahan | sudah dimitigasi `sandbox_manager/validation.py` (subpath tidak boleh melintasi symlink); konsumen lain (gitops) wajib memakai cek yang sama | catatan |
| L6 `KK_IMAGE_TAG` tidak divalidasi | divalidasi pola `[A-Za-z0-9._-]` | test setting tidak aman |

Bukti setelah perbaikan:
- `test_backup_restore.py` 28 test: OK sebagai root (1 skip), dan sebagai non-root (5 skip, butuh root).
- 6 mutan baru merah.
- Drill lokal PASS: restore 34 dtk, DB lama tersimpan.

Utang: scanner (gitleaks, semgrep, trivy) belum pernah dijalankan atas diff ini; gate security-scan sebelum G1.

### WP-1.5d (🔒) — Semgrep dan Trivy di `security-scan` (SECURITY_MODEL §11, T8; prasyarat G1)

Spec: `docs/superpowers/specs/2026-10-31-wp-1.5d-security-scan-design.md`. Plan: `docs/superpowers/plans/2026-10-31-wp-1.5d-security-scan.md`.

| Pemeriksaan | Hasil aktual |
|---|---|
| `python3 -m unittest discover -s infra/tests` | OK (1 skip). `test_scan_gate.py`: 30 test (gate Trivy/Semgrep dengan JSON sintetis, daftar image dari compose, kebijakan repo) |
| Mutan gate (16) | merah semua: CRITICAL tanpa perbaikan lolos, EOSL diabaikan, laporan hilang, laporan tanpa OS, laporan liar, CVE exception tidak terpakai, `pkg_path` tidak terpakai, exception berlaku untuk image lain, tanggal DECISIONS tidak dicek, batas 92 hari, kedaluwarsa, file penanda Semgrep, ERROR Semgrep lolos, error Semgrep lolos, daftar image kosong |
| Mutan kebijakan repo (2) | `postgres.user` bukan `70:70` selama EX-04 ada → merah; tanggal EX-03 di DECISIONS berbeda dari TOML → merah |
| Trivy lokal (DB 31 Okt) | 9 dari 10 image; egress-proxy tidak bisa dibangun di container ini (mirror apk diblokir) dan gate memblokir laporan yang hilang itu |
| Semgrep lokal | ruleset registry tidak bisa diunduh (semgrep.dev diblokir proxy); semantik `nosemgrep` diuji dengan rule lokal pada semgrep 1.180.0: ID lengkap dan bagian terakhirnya dikenali, prefix tidak; kontrol tanpa komentar → ditandai |
| `sandbox-isolation` (CI) | hijau dengan base sandbox-node baru (node 22 trixie) |

CI di runner GitHub, [job security-scan 124457098515](https://github.com/YosiaGalih/IDEE-Application/actions/runs/41173808138/job/124457098515) (86b85f8):
- **Semgrep.** 199 rule, 186 file. Hasilnya 1 ERROR, yang dicatat sebagai false positive EX-05 (`sqlalchemy.text` pada GRANT/REVOKE dengan nama role tervalidasi), dan 6 WARNING `chmod` yang disengaja.
- **Trivy.** `scan: 10 images`, semuanya punya laporan.
  - Debian 13: 43–44 HIGH tanpa perbaikan per image, 175 baris "allowed EX-02".
  - 55 baris "excepted" (EX-03, EX-04).
  - egress-proxy: Alpine 3.24.3, 0 temuan.
  - `gate: PASS (0 blocking)`.

Temuan saat menjalankan:
- Perintah literal SECURITY_MODEL §11 merah permanen pada setiap image Debian → konflik #56, exception EX-02.
- Tag nginx-unprivileged `1.29-alpine` tidak lagi diperbarui upstream (47 temuan berperbaikan) → pin `1.30-alpine`.
- Image Node bookworm membawa CRITICAL zlib `will_not_fix` → base `22-trixie-slim`.
- `nosemgrep` dengan prefix ID rule registry tidak berlaku (ID registry mengulang bagian terakhirnya) → ID lengkap.
- Image pihak ketiga terbaru (31 Okt) masih membawa temuan yang hanya bisa diperbaiki upstream → konflik #57, EX-03 (30 hari) dan EX-04.

Konflik dokumen baru: #56, #57. DECISIONS: EX-02..EX-05 (menunggu persetujuan PO). RUNBOOK §12.1: prosedur exception bulanan dan saat merah.

#### Review silent-failure (`kk-silent-failure-hunter`) @ 86b85f8

3 HIGH, 9 MEDIUM, dan 7 LOW, semuanya direproduksi dengan JSON sintetis. Ditutup di aef5e62:

| Temuan | Penutupan | Bukti |
|---|---|---|
| H1 laporan basi menutupi scan yang gagal | skrip menghapus `report-*.json` lama; dua laporan untuk satu image memblokir | test + mutan; uji lokal dengan `report-99.json` yang ditanam: terhapus, egress-proxy yang gagal tetap memblokir |
| H2 error Semgrep level `warn`/tanpa level lolos | semua error memblokir; `--timeout 30` | test 4 bentuk error; mutan |
| H3 severity tak terduga dibuang | memblokir sebagai "unexpected severity" | test `critical`/`UNKNOWN`/kosong; mutan |
| M1 laporan tanpa paket lolos | wajib ada hasil `os-pkgs` (Trivy selalu menulisnya, termasuk redis dengan 0 temuan) | test; mutan |
| M2 umur DB tidak dicek | `trivy version --format json` → blok bila > 2 hari; tanggal dicetak | test basi/tidak terbaca; CI: `trivy DB updated 2026-10-31T01:14:32` |
| M3 `--quiet` menyembunyikan penyebab | tanpa `--quiet`; `::error::` dengan exit code | uji lokal |
| M4 prefix image | cocok persis (digest boleh) | test `caddy:20`/`caddy:2-debug`; mutan |
| M5 baris DECISIONS tertutup diterima; status persetujuan diabaikan | sel kedaluwarsa harus tanggal polos (baris tertutup ditolak); status "menunggu persetujuan PO" dicetak di setiap baris yang dikecualikan. Persetujuan itu sendiri keputusan PO | test EX-01 tertutup; mutan |
| M6 severity Semgrep fail-open | blok kecuali WARNING/INFO | test 5 nilai; mutan |
| M7 cakupan Semgrep menyusut | `--expect` dan `--min-files` wajib | test; mutan |
| M8 CVE mencakup paket lain | `vulns` wajib disertai `pkgs` (go-jose v3 caddy pindah ke EX-02) | test; mutan |
| M9 `nosemgrep` longgar | wajib ID rule lengkap dan EX terbuka yang menyebut file itu; dicek juga di `security-scan` | test; mutan |
| L1 concurrency menjatuhkan run tertunda | grup sendiri per run untuk main/nightly/manual | actionlint |
| L2 nightly merah tanpa alert | tidak diubah: GitHub mengirim email kegagalan scheduled run ke pengubah cron terakhir; langkah "buka issue" butuh `issues: write` | catatan |
| L3 `.semgrepignore` tanpa jangkar | `/docs/`, `/…/tests/` | test daftar terkunci |
| L4 service tanpa image | ditolak | test; mutan |
| L5 spec drift | spec diperbarui | – |
| L6 tanggal lokal di test | `today_utc()` | – |
| L7 Alpine 3.21 EOL 1 Nov | `eosl = true` EX-06 sampai 2026-11-30, konflik #58 | test exception EOSL; CI: "EX-06 … not needed today" |

#### Review AG-13 (`kk-security-reviewer`) @ aef5e62 — **SIGNED OFF** [Kemungkinan Besar]

Tidak ada CRITICAL/HIGH. Yang terverifikasi:
- **Justifikasi:** caddy (curl/brotli tidak dipakai), gosu tidak dieksekusi pada `user: "70:70"`, socket-proxy, dan EX-05 bertahan.
- **Probe silent-green:** 7 probe lolos di 86b85f8 dan ditolak semua di aef5e62.
- **Scanner lain:** gitleaks bersih (89 commit); pip-audit bersih; `-m docker` 23 passed dengan base node trixie.

| Temuan | Penutupan | Bukti |
|---|---|---|
| M1 `# nosem` / `NOSEMGREP` lolos dari test; `.semgrepignore` bisa membuang file diam-diam | test mencari `(?i)nosem` dan mewajibkan `# nosemgrep: <ID lengkap> (EX-nn)`; isi `.semgrepignore` dikunci test; `--min-files 180` | mutan `# nosem`, `NOSEMGREP`, tanpa EX, dan `/backend/app/` di `.semgrepignore` → merah |
| M2 CVE-2026-78669 (HTTP/2 DoS) caddy dapat dipicu di :443 publik; teks EX-03 menyesatkan | teks EX-03 per CVE; **item terbuka: syarat sebelum deploy publik (WP-7.1/G7)**, yaitu caddy dibangun ulang (Go ≥ 1.26.10, x/net 0.60.0) atau `protocols h1 h3` sementara; RUNBOOK §12.1 langkah 7 | DECISIONS EX-03, TOML |
| L brotli di nginx juga dari freetype; npm akan butuh network | teks EX-03; pemicu evaluasi ulang di RUNBOOK §12.1 | – |
| L docker.sock + cache persisten di skrip scan | RUNBOOK §12.1 langkah 8 dan header skrip: hanya CI/mesin pengembang; konflik #59 (bunyi `infra.md`, alternatif `docker save` + `--input`) | – |
| L checkout tanpa `persist-credentials: false` | ditambahkan | actionlint |
| L daftar image hanya profil `egress` | skrip merender semua profil (`config --profiles`) | shellcheck |
| L EX-02 sebagai kelas | usul baseline per image → konflik #59/#56 | – |
| L test EX-04 membaca teks mentah | memakai `services.postgres.user` dari config yang dirender | test |
| L dua digest lebih muda dari aturan umur | konflik #59 | – |
| L semgrep tanpa hash lock; ruleset registry tidak dipin | diterima untuk Phase 1: `exclude-newer` membatasi versi; jumlah rule tercetak di log (199) | catatan |

Item terbuka sebelum G1/G7:
- PO memutuskan EX-02..EX-06 dan konflik #56–#59.
- CVE-2026-78669 caddy ditutup sebelum deploy publik.
- EX-03 kedaluwarsa 2026-11-30.

Review delta AG-13 @ 3977424: **SIGNED OFF**. M1 tertutup di kode. M2 menjadi syarat tertulis sebelum deploy publik. Dua LOW baru dari delta itu ditutup di commit berikutnya, persis seperti usul reviewer:

| Temuan | Penutupan | Bukti |
|---|---|---|
| `config --profiles` lewat process substitution: kegagalannya tertelan, egress-proxy bisa tidak di-scan (regresi commit 3977424) | assignment biasa (`profile_list=$(…)`), sehingga `set -e` menghentikan skrip | stub `docker` yang menggagalkan `config --profiles` → skrip berhenti dengan exit 3 |
| `:include .gitignore` mengeluarkan file ter-track dari SAST | dihapus; Semgrep hanya men-scan file yang di-track git; daftar terkunci test diperbarui | test `.semgrepignore` |

LOW yang tetap terbuka, tidak memblokir:
- cache Trivy di host persisten (didokumentasikan; runner GitHub sekali pakai);
- semgrep dan ruleset registry tidak dipin hash.

### WP-1.4a — fondasi frontend: scaffold, token, UI kit, klien API, SSE, i18n, image `web` (FR-149, PRD v2.2 §4/§8.1)

Spec: `docs/superpowers/specs/2026-10-31-wp-1.4a-frontend-scaffold-design.md`. Plan: `docs/superpowers/plans/2026-10-31-wp-1.4a-frontend-scaffold.md`.

| Pemeriksaan | Hasil aktual |
|---|---|
| `npm ci --no-audit --ignore-scripts` dari nol | 473 paket |
| `format:check`, `lint`, `typecheck` | bersih |
| `lint:guards` | kelima fixture (impor lintas fitur via alias dan relatif, `new WebSocket`, teks literal, `dangerouslySetInnerHTML`) ditolak aturannya; mutan "aturan dimatikan" → skrip gagal |
| `npm test` | 80 test: klien API 25, auth 5, parser SSE 7, stream + hook 12, i18n 2, UI 23, route 6 |
| Mutan SSE | watchdog tanpa `reader.cancel()` → test "fetch yang tidak mengakhiri body saat abort" merah |
| `npx vite build` | JS 440 kB (139 kB gzip), font Inter lokal |
| `npm audit --omit=dev` | 0 setelah zod 4.6.7 (GHSA-mrhx-c7xc-m3w5, HIGH). Dev: 12 moderate, semuanya minimatch lewat ESLint; belum ada perbaikan non-breaking |
| Image `kodekantor/web:dev` | build lokal; dijalankan read-only, `cap_drop ALL`, uid 101: `/healthz` 200, route SPA → app shell `no-cache`, `/assets/*` `immutable`, aset hilang → 404, header Server tanpa versi |
| Stack lokal lewat Caddy + Chromium | `https://localhost/` 200 dengan CSP penuh; 0 pelanggaran CSP / error konsol; font dimuat dari origin sendiri; navigasi, ganti bahasa ke `en`, dan 404 bekerja |
| `python3 -m unittest discover -s infra/tests` | OK (1 skip); compose `web` = image lokal `kodekantor/web` dengan `pull_policy: never` |

Temuan saat menjalankan:
- **npm 10.9.4 crash pada graf peer vitest.** Lockfile ditulis npm 11.21.0. msw 3 membuat `npm ci` npm 10 menolak lockfile, sehingga msw tetap 2.15.
- **MSW 3 mengganti `onUnhandledRequest` menjadi `onUnhandledFrame`.** Typecheck menangkapnya. Sebelumnya opsi itu diam-diam diabaikan: test tidak gagal pada request yang tidak ditangani.
- **Item terbuka untuk WP-1.6 (G1), bug dari WP-1.5a:** `scheduler` menjadi **unhealthy setelah 10 menit**.
  - Penyebab: healthcheck memakai mtime `/tmp/celerybeat-schedule`, tetapi beat (celery 5.6.3, `dbm.gnu`) tidak memperbarui mtime file itu walau terus mengirim task.
  - Terlihat di stack lokal: mtime 05:09, status unhealthy dengan 82 kegagalan beruntun.
  - CI `clean-install` tidak menangkapnya karena selesai sebelum 10 menit.

Konflik dokumen baru: #60–#62. CLAUDE.md bagian "Perintah": frontend.
