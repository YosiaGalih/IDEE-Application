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

#### Review WP-1.4a @ 87d3aba: `kk-frontend-reviewer` (BLOCK, 4 HIGH), `kk-silent-failure-hunter` (2 HIGH, 7 MEDIUM, 9 LOW), AG-13 `kk-security-reviewer` (**SIGNED OFF** dengan syarat 1 MEDIUM, 4 LOW)

| Temuan | Penutupan | Bukti |
|---|---|---|
| H (dua review) `useTaskEvents` menyimpan event task lama saat `taskId` berganti | state dikunci per `taskId` (adjust-during-render) + `lastSeqRef` direset | test A→B; mutan → merah |
| H (dua review) celah `seq` diterima dan resume melompatinya | frame di atas `lastSeq + 1` tidak dikirim; putus lalu sambung dari `lastSeq` (replay DB). Backend: `seq = tasks.event_seq + 1` dalam transaksi, jadi tanpa lubang | test 1, 3 → `after_seq=1`, hasil `[1,2,3]`; mutan → merah |
| H error stream dibuang; "disconnected" menyesatkan setelah 401/403/404 | status `failed` + `error` (kode dari envelope) + `uxState` + `reconnect()`; fatal 400/401/403/404/410/422 | test 6 status fatal, kode `CAPABILITY_DISABLED`, `retry()` |
| H tanpa `errorElement`: stack trace React Router tampil | `RouteErrorPage` (StateView + muat ulang, `requestId`) di root dan layout | test komponen melempar; mutan → merah |
| M backoff tidak naik (handshake mereset hitungan) | reset hanya setelah frame pertama | test jarak waktu membesar; test 200-lalu-tutup → `disconnected`; mutan → merah |
| M `onEvent` atau handler frame melempar → event hilang / dianggap error jaringan | event disimpan dulu; error handler dicatat sebagai frame invalid; stream berjalan terus | 2 test; 2 mutan → merah |
| M offline → query berhenti diam (`paused`) | `networkMode: "always"` | konfigurasi |
| M `uxStateForError` hanya per kode | fallback status 401/403/404/429/502/503/504 (juga untuk `bad_response`) | 9 baris test; mutan → merah |
| M (dua review) + AG-13 MEDIUM: penjaga path tembus `%2e%2e`, `.%2E`, `\`; SSE tanpa penjaga | `apiUrl` menolak `..`/`%2e`/`\`, memeriksa origin dan prefiks, dipakai juga oleh SSE; `apiPath` meng-encode segmen | 8 varian ditolak; path SSE → `failed` tanpa request; 2 mutan → merah |
| M timeout/abort saat baca body jadi `bad_response` | diklasifikasikan `timeout`/`aborted` | kode |
| M `Idempotency-Key` hilang saat retry | `ApiError.idempotencyKey` | test |
| M `FormData` di-JSON-kan | dikirim multipart | test; mutan → merah |
| M mutasi publik setelah logout gagal `csrf_missing` | satu GET `/auth/me` mengambil cookie pra-sesi | test bootstrap; mutan → merah |
| M cache tidak dibersihkan saat logout; tanpa 401 global | `useLogout` (`queryClient.clear()`); `onUnauthenticated` → `/login` | kode |
| M kontras fokus sidebar 2.0:1; danger gelap 2.0:1 | `--color-focus-on-dark` di `.on-dark`; `--color-on-danger` | token |
| M `Alert`/`Badge` hanya warna; tanpa `<h1>`/judul/fokus | ikon per tone; `<h1>` + `useDocumentTitle`; fokus ke `main` saat navigasi | test heading + judul |
| M fixture auth menyimpang (`project.read`) | `project:read`; nama test diperbaiki | test |
| M template literal lolos lint i18n | `no-restricted-syntax` untuk template literal anak JSX | fixture `lint:guards` |
| L (AG-13 + silent-failure) varian lint (eval, `createElement`, `innerHTML`, `insertAdjacentHTML`, `globalThis.WebSocket`, `EventSource`, `localStorage`, `fetch`) | aturan ditambah; 13 fixture `lint:guards` | `lint:guards` 13/13 |
| L (AG-13) EX-03 untuk image yang kita bangun sendiri | `apk del curl` + `brotli-libs>=1.2.1-r0` di Dockerfile; entri EX-03 `kodekantor/web` dihapus (gate memblokir entri yang tak terpakai, jadi CI membuktikannya) | CI `security-scan` |
| L (AG-13) undici 7.30.1 tidak tercatat | dicatat di tabel pengecualian umur spec | spec |
| L `immutable` pada 404 `/assets/` | `always` dihapus untuk lokasi itu | config |
| L test di luar `__tests__` tidak dijalankan | include `src/**/*.{test,spec}.{ts,tsx}` | vite.config |
| L direktori fixture tertinggal | `.gitignore` + `rmdir` di skrip | skrip |
| L `z.uuid()` menolak id non-RFC; `EmptySchema.strict()` | `z.guid()`; `z.object({})` | test id non-RFC |

Bukti setelah perbaikan: format, lint, `lint:guards` (13/13), dan typecheck bersih; **114 test**; 15 mutan baru semuanya merah; `infra/tests` OK.

Ditunda ke WP-1.4b:
- primitif `CostLabel`/`AiBadge`/`ApprovalDialog`;
- kunci i18n bertipe dan `jsx-a11y`;
- `lazy()` per route;
- respons blob/text;
- skema payload per tipe;
- stream berhenti pada status terminal task;
- batas event di memori;
- matriks browser.

Review delta @ 31aff5b:
- `kk-frontend-reviewer`: **BLOCK dicabut (APPROVE)**. Keempat HIGH diverifikasi tertutup langsung: ganti task, celah seq `[1,2,3,4]` lewat replay, 403 `capability_disabled` tanpa retry, error boundary. Badai backoff turun dari 37 ke 6 koneksi per 400 ms.
- AG-13: **SIGNED OFF**, syarat 1 terpenuhi: CI `security-scan` @ 31aff5b menunjukkan `kodekantor/web:dev: alpine 3.24.2, 1 results, 0 HIGH/CRITICAL findings` dan `gate: PASS`. Perbaikan brotli/curl terbukti, dan EX-03 untuk `web` tidak diperlukan lagi.

Temuan sisa dari kedua review delta, ditutup di commit berikutnya:

| Temuan | Penutupan | Bukti |
|---|---|---|
| AG-13 MEDIUM sisa: tab/LF/CR pada path mentah dibuang parser, sehingga `.\t.` menjadi `..` | `apiUrl` menolak karakter kontrol **dan** setiap path yang ditulis ulang parser (`url.pathname` harus sama dengan `API_BASE + path`) | test `.\t.`, `\n`, `\r`, spasi; query string tetap jalan; `apiPath(".\t.")` → `.%09.` |
| AG-13 LOW: `window.fetch`/`globalThis.fetch` lolos lint | selector `MemberExpression` fetch/XMLHttpRequest di luar `src/api` | fixture ke-14 `lint:guards` |
| frontend M-low: stream idle yang sehat (hanya ping) menumpuk kegagalan; duplikat replay mereset hitungan | koneksi yang hidup ≥ `healthyAfterMs` (15 dtk) dianggap sehat; hanya frame **baru** yang mereset | 2 test; 2 mutan → merah |
| frontend LOW: dua mutasi pra-sesi sekaligus → dua GET → dua cookie berbeda | satu GET bersama (promise in-flight) | test 3 mutasi → 1 GET; mutan → merah |
| frontend LOW: aksi "Masuk" memuat ulang halaman; halaman error tanpa `<h1>`; teks disconnected menjanjikan sambung otomatis | aksi `unauthenticated` → `/login`; `StateView titleAs="h1"`; teks "Periksa jaringan Anda, lalu coba lagi" | test heading; mutan → merah |
| LOW: komentar entri docker-socket-proxy ikut terhapus | dikembalikan | TOML |

Tetap ditunda ke 1.4b:
- `onUnauthenticated` yang sadar route publik dan `?next=`. Ini harus aman dari open redirect.
- Stream berhenti pada status terminal, dan batas event di memori. Keduanya **sebelum layar task dirilis**.
- Item 1.4b lain di tabel sebelumnya.

Bukti setelah perbaikan: lint, `lint:guards` (14/14), typecheck, dan format bersih; **122 test**; 4 mutan baru merah.

### WP-1.4b (🔒 sesi) — layar auth, penjaga sesi, menu pengguna (FR-001–006, AC-01 bagian UI, PRD v2.2 §4 layar 1)

Spec: `docs/superpowers/specs/2026-10-31-wp-1.4b-auth-screens-design.md`. Plan: `docs/superpowers/plans/2026-10-31-wp-1.4b-auth-screens.md`. Cakupan: konflik #63 (layar dashboard/projects/agents/approvals ikut WP backend-nya).

| Pemeriksaan | Hasil aktual |
|---|---|
| `format:check`, `lint` (+ `jsx-a11y` recommended), `typecheck` (kunci i18n bertipe) | bersih |
| `lint:guards` | 14/14 aturan kontrak menolak pelanggarannya |
| `npm test` | **243 test** di 16 file (sebelumnya 122): `safeNext` 36, `zodResolver` 3, login 25, penjaga sesi + 401 global 11, menu + ganti password 13, lupa/reset 13, `useLogout` 3, sesi 4, klien API (+`Retry-After`, bootstrap CSRF) |
| Mutan | putaran 1: 29 mutan, 5 selamat → 3 test ditambah, 2 kode tak berguna dihapus (penjaga `isPending`, `isFetching`); putaran 2 (perbaikan review): 20 mutan + 3 mutan konfigurasi nginx/Caddy; semuanya merah |
| `npx vite build` | JS 635 kB (200 kB gzip), naik dari 441 kB (138 kB gzip): zod 85 kB pertama kali masuk bundle (layar pertama yang memanggil API; zod diwajibkan `frontend.md`), react-hook-form 31 kB, Radix Dialog ±25 kB. Peringatan Vite >500 kB dibiarkan terlihat; pemecahan per route di WP yang membawa Monaco |
| `npm audit --omit=dev --audit-level=high` | 0. Dev: 13 moderate (minimatch lewat ESLint; `jsx-a11y` menambah satu dependen) |
| `python3 -m unittest discover -s infra/tests` | 111 OK (1 skip), termasuk `test_reset_token_hygiene.py` (5) |
| Backend nyata + Vite + Chromium (Playwright), DB `kodekantor_live`, owner `admin@localhost` lewat `create-owner` | `/projects?tab=tasks` tanpa sesi → `/login?next=…`; password salah → pesan generik, fokus ke ringkasan; validasi tanpa request; login → kembali ke `next`; cookie `kk_session` HttpOnly + `kk_csrf`, SameSite=Lax; ganti password 403 lalu sukses; keluar → cookie habis; Back setelah keluar → login lagi; lupa → pesan generik; tautan reset dari log dev → URL menjadi `/reset-password` (REPLACE), 11 karakter ditolak lokal, reset → `/login` dengan pesan → login dengan password baru; tautan bekas → "tidak valid"; **API dimatikan → Keluar: tetap di app dengan "Gagal keluar: sesi masih aktif", cookie sesi masih ada; API hidup → coba lagi → `/login`, cookie habis** |
| nginx `web` (digest dipin) + Caddyfile asli di container | konfigurasi lama: token muncul 2× di access log (request line + Referer aset) → baru: 0×, juga untuk `/RESET-PASSWORD/…`; `Referrer-Policy: no-referrer` di `/reset-password*`, `same-origin` di path lain, header keamanan lain tetap; `caddy validate` → Valid |

Temuan saat menjalankan:
- **Open redirect di draf `safeNext`:** `"/..//evil.test"` lolos pemeriksaan awal dan menjadi `//evil.test` setelah diurai. Tertangkap test merah sebelum commit.
- **Email reset tidak ada di produksi** (konflik #64): teks tidak lagi menjanjikan pengiriman.
- **Validasi lokal lebih ketat dari server akan mengunci owner:** `z.email()` menolak `admin@localhost`; aturan disamakan dengan backend (`@`, ≤ 320). Panjang password dihitung per code point seperti Python.
- **Bug backend di luar cakupan [Pasti]:** `RedactingFilter` (`backend/app/core/logging.py:41`) mengosongkan `record.args`, sedangkan `AccessFormatter` uvicorn membongkarnya, sehingga setiap baris access log menjadi traceback "--- Logging error ---" (terlihat di uvicorn lokal dengan CMD yang sama dengan image). Test `tests/unit/test_errors.py:192` tidak menangkapnya karena memakai pesan tanpa args. Dicatat di HANDOFF untuk WP-1.6.

#### Review WP-1.4b @ f7f21fa/250b716: `kk-frontend-reviewer` (WARN, 4 SHOULD, 5 NIT), `kk-silent-failure-hunter` (2 HIGH, 3 MEDIUM, 8 LOW), AG-13 `kk-security-reviewer` (**SIGNED OFF dengan syarat**: 3 MEDIUM, 3 LOW)

| Temuan | Penutupan | Bukti |
|---|---|---|
| AG-13 M + frontend S + silent L1: password tertinggal di MutationCache 5 menit | `gcTime: 0` pada mutasi login, reset, ganti password | test cache mutasi kosong setelah login dan setelah dialog ditutup; 2 mutan → merah |
| AG-13 M + frontend S + silent H2: logout gagal diam-diam berakhir di `/login` saat sesi masih hidup | tetap di app dengan pesan dan coba lagi; cache tetap; 401 → `/login` polos; `useLogout` membersihkan hanya bila sesi berakhir | 3 kasus (500, `CSRF_FAILED`, jaringan) + 401 + 3 test hook; Chromium dengan API mati; 3 mutan → merah |
| AG-13 M: token reset di access log nginx dan Referer aset same-origin | nginx: `log_format kk_redacted` tanpa Referer, path reset disamarkan (tanpa beda huruf); Caddy: `no-referrer` untuk `/reset-password*`, header global menjadi default `?Referrer-Policy` | container: 2× → 0×; `infra/tests/test_reset_token_hygiene.py`; 3 mutan konfigurasi → merah. **Syarat AG-13 yang masih terbuka:** token pindah ke fragment (`#token`) sebelum jalur pengiriman tautan mana pun (email atau admin/CLI, konflik #64) di-merge |
| frontend S + silent H1: teks masih menjanjikan email | subjudul, tombol, pesan (nada info), `noToken` ditulis ulang | test teks; Chromium |
| frontend S + silent M1: setiap 403 ganti password = "kata sandi salah" (juga `CSRF_FAILED`) | `describeError` memilih teks dari `code`; `FORBIDDEN` → "salah atau dikunci"; reset 400 hanya `BAD_REQUEST`; 409 hanya `CONFLICT` | test `CSRF_FAILED`, `PATH_VIOLATION`; 3 mutan → merah |
| silent M2: tanpa jawaban pada reset/ganti → ulangan menyesatkan | badan pesan "hasil belum pasti: kata sandi mungkin sudah berubah" | 2 test; mutan → merah |
| silent M3: login 200 tanpa cookie tersimpan → kembali ke form kosong tanpa kata | `/auth/me` sesudah login; 401 → "browser tidak menyimpan sesi"; sukses mengisi cache | test; mutan → merah |
| AG-13 L + frontend NIT + silent L7: `/LOGIN`, `/%6Cogin`, `/RESET-PASSWORD/x` lolos `isPublicPath` | decode + huruf kecil; gagal decode = publik | 7 kasus baru; 2 mutan → merah |
| silent L2: kunci error tak dikenal tersembunyi | `KEYS: Record<AuthErrorKey, true>` (kunci baru tanpa entri = typecheck error); `console.error` untuk pesan bukan kunci | test unit; mutan → merah |
| silent L3: bootstrap CSRF menelan error jaringan | kegagalan GET bootstrap → `network`/`timeout` (layar "terputus"), bukan `csrf_missing` | test; mutan → merah |
| silent L4: 429 dari gateway kehilangan `Retry-After` | `retryAfterSeconds` juga pada `bad_response`; teks 429 dari status | test halaman 429 HTML |
| silent L5: sesi habis tanpa penjelasan | pemberitahuan `sessionExpired` (handler 401 global; `/auth/me` `SESSION_EXPIRED`), tidak untuk kunjungan pertama | 3 test; 2 mutan → merah |
| silent L8: pemberitahuan muncul lagi saat muat ulang/Back | dibaca sekali, lalu state entri riwayat dihapus | test; mutan → merah |
| frontend NIT: `zodResolver` membuat kunci datar | `set`/`get` React Hook Form; isu objek → `root` | test bersarang; mutan → merah |
| frontend NIT: fokus hilang setelah sukses lupa password | fokus ke pesan | test; mutan → merah |
| frontend NIT: "1 seconds"; header meluap | kunci plural `rateLimited_one/_other`; nama `truncate` | typecheck; screenshot |
| AG-13 L: "tidak tersimpan di riwayat" | spec: hanya riwayat sesi tab; riwayat global = risiko sisa yang diterima | spec |
| AG-13 L (info): 13 moderate dev | dipantau | — |

Tidak ditutup (alasan):
- **silent L4 sebagian:** halaman 401 HTML dari proxy pada login tampil sebagai "sesi berakhir". Hanya terjadi pada gateway yang salah konfigurasi.
- **silent L6:** 403/404 dari `/auth/me` tanpa tombol aksi, karena `StateView` hanya memberi aksi untuk state yang bisa dipulihkan. Juga hanya pada salah konfigurasi.
- Hitung mundur `Retry-After` dan "ganti email" setelah sukses lupa password: kosmetik.

Review delta AG-13 @ db1872a (`frontend/` dan `infra/` identik dengan 2c0e7b5): **SIGNED OFF** 🔒. Syarat merge dari review pertama terpenuhi.
- **Diverifikasi dengan probe sendiri:**
  - MutationCache kosong begitu app shell terpasang.
  - Logout:
    - 503, `CSRF_FAILED`, atau jaringan putus → tetap di app, alert tampil, cache dipertahankan;
    - 401 atau 200 → `/login`, cache dikosongkan.
  - nginx dan Caddy dengan digest dipin:
    - `/reset-password/<t>`, `/RESET-PASSWORD/<t>`, slash di akhir, dan query → `[redacted]`;
    - `no-referrer` hanya di path reset;
    - CSP dan XFO tetap terpasang.
  - Fuzz diferensial `isPublicPath` terhadap `matchRoutes`: 132.240 varian, 0 bypass.
- **CI @ db1872a:** 9/9 hijau.
  - gitleaks: no leaks found. Fingerprint fixture token sintetis ditambahkan ke `.gitleaksignore`.
  - Semgrep: 272 file, 6 WARNING. Semuanya kode backend/sandbox lama.
  - Trivy: `kodekantor/web:dev` 0 HIGH/CRITICAL; `gate: PASS`.
- **LOW baru:** masking memakai `$request_uri` mentah, jadi tautan yang ditulis ulang (`/%72eset-password/<t>`, `//reset-password/<t>`) tercatat apa adanya. `$uri` tidak bisa dipakai karena `try_files` mengubahnya menjadi `/index.html` sebelum log ditulis. Batas ini kini didokumentasikan di `test_reset_token_hygiene.py`. Server hanya menerbitkan bentuk kanonik. Penutup tuntasnya adalah syarat fragment di bawah.
- **INFO:** mutation reset kini juga punya test cache kosong; mutan `gcTime` reset → merah.

Syarat AG-13 yang terbawa:
- token reset dipindah ke fragment (`/reset-password#<token>`) sebelum jalur pengiriman tautan mana pun (email atau admin/CLI, konflik #64). Ini juga menutup LOW masking nginx dan kebocoran lewat perantara milik operator (CDN/WAF/proxy);
- stream berhenti pada status terminal task dan batas event di memori (dari WP-1.4a), sebelum layar task dirilis.


### WP-2.1a — projects core: create/list/read/update/archive, otorisasi berscope project (FR-012–014, FR-017, FR-018, FR-022, AC-01, AC-02 bagian create)

Spec: `docs/superpowers/specs/2026-11-02-wp-2.1-platform-services-design.md` (pembagian 2.1a–2.1e dan rancangan 2.1a). Plan: `docs/superpowers/plans/2026-11-02-wp-2.1a-projects-core.md`. Cakupan dan penundaan: konflik #65 (`git_https` ditolak sampai egress G4; WP-2.1b–e menyusul).

| Pemeriksaan | Hasil aktual |
|---|---|
| Backend `ruff check .`, `ruff format --check .`, `pyright`, `lint-imports` | bersih; 0 error; 3 kontrak dipertahankan |
| Backend `pytest -q -p no:randomly` (PostgreSQL 16 + Redis 7 lokal) | **1318 passed, 1 skipped** (sebelumnya 1292). Urutan terbalik (`tests/security/test_cross_project.py` lalu `tests/integration/test_projects.py`) → 71 passed |
| Mutan backend (perbaikan review) | 8 mutan: `commit_attempted=False`, kategori tersembunyi hanya `Cc`, rate limit selalu lolos, audit settings tanpa nilai, `sync_state` tanpa masalah repo, `open_tasks` memakai `TERMINAL`, route `/stack` tanpa `require_project_permission` → semuanya merah. Pola `q` di route dilepas → selamat, karena service juga menolak karakter kontrol (pertahanan berlapis, disengaja) |
| Frontend `format:check`, `lint`, `lint:guards`, `typecheck`, `vite build` | bersih; guard menolak pelanggarannya |
| Frontend `vitest run` | **278 test** di 18 file (sebelumnya 243) |
| CI @ bed63ac (sebelum perbaikan review) | 9/9 hijau. `clean-install`: "project smoke: created … from python-basic, first commit e48331a…". Trivy: image backend 50 HIGH tanpa fix (5 baru dari dependensi git: `libcurl3t64-gnutls`, `perl`, `perl-modules`, `libperl`), lolos lewat EX-02 (menunggu PO); `gate: PASS`. Semgrep: 284 file, 6 WARNING lama |
| Backend nyata + Vite + Chromium, DB `kodekantor_live` | create blank dan template, daftar, cari, arsip/aktifkan, `git log` di workspace cocok; user lain melihat daftar kosong dan 404 di URL project |
| CI @ cd34fbc (setelah perbaikan review) | **9/9 hijau.** `clean-install`: smoke yang diperketat → "project smoke: created 6feed4a1-… from python-basic, first commit 030927e4…" (commit di volume = jawaban API, project ada di daftar); restore drill memulihkan repo hasil API, `git fsck` bersih. `security-scan`: `kodekantor/backend:dev` debian 13.7, 50 HIGH, semuanya tanpa fix (perl/libcurl dari git, util-linux, ncurses, systemd, sqlite), lolos lewat EX-02 (menunggu PO), `gate: PASS (0 blocking)`; Semgrep 285 file, 6 WARNING lama; gitleaks "no leaks found"; `npm audit` 0 |

#### Review WP-2.1a @ 0ac5f13: `kk-backend-reviewer` (**BLOCK**: 2 BLOCK, 8 SHOULD, 9 NIT), `kk-frontend-reviewer` (3 SHOULD, 6 NIT), `kk-silent-failure-hunter` (1 HIGH, 7 MEDIUM, 9 LOW), AG-13 `kk-security-reviewer` (**NOT SIGNED OFF**, tanpa temuan pemblokir: 9 LOW; menunggu bukti T8 di CI dan delta review)

| Temuan | Penutupan | Bukti |
|---|---|---|
| backend B1 + silent M7: `open_tasks` menghitung task `completed`/`failed` | `services/task_stats.py`: open = semua status kecuali `completed, failed, cancelled, rejected`. Spec diperbarui ("non-terminal" ≠ `TERMINAL` di `workflow.states`) | `test_open_tasks_counts_only_unfinished_tasks` (satu task per status → 9); mutan `TERMINAL` → merah |
| backend B2 + silent M2 + AG-13 LOW (cleanup): invarian all-or-nothing pecah di 3 jalur | pembersihan sebagai task yang di-shield. Bila `COMMIT` sendiri gagal, sesi baru memutuskan apakah folder disimpan. Bila tidak, `rmtree` dengan `onexc` yang mencatat log. `folder_may_exist` diset sebelum `mkdir`. `close()` sesi yang melempar tetap diikuti pembersihan | 3 test: commit sampai lalu melempar → baris + folder tetap; `close()` melempar → folder hilang, 0 baris; cancel di tengah git → 0 baris, 0 folder. Mutan `commit_attempted=False` → merah |
| backend S1 + AG-13 LOW: `ProjectAccess` tidak dibuat | `api/deps.py`: `require_project_permission(perm)` (404 di luar scope, 403 tanpa izin) di setiap route `{project_id}`; service tetap memeriksa ulang; `current_actor` membawa IP klien | `test_every_project_route_has_the_project_door` menelusuri router, termasuk route tersembunyi (saran AG-13); mutan route tanpa pintu → merah |
| backend S2 + AG-13 LOW (DoS) + silent L6: koneksi DB dan advisory lock dipegang selama git; satu user bisa memperlambat semua | `CreateGate` per app: satu create per owner, 4 per proses, yang menunggu tanpa koneksi. 20 create/user/10 menit (429 + `Retry-After`, Redis mati → 503). `lock_timeout` 10 s → 429. Git pada GET/PATCH/arsip setelah sesi ditutup/commit | `test_creates_are_rate_limited_per_user`; mutan → merah |
| backend S3 + AG-13 (catatan): daftar tanpa pagination | `page`, `page_size` (≤ 200, default 50), `meta {page, page_size, total}`; frontend: `?page=` di URL, navigasi halaman, halaman di luar jangkauan pindah ke halaman terakhir; `meta` tidak valid → error | `test_list_is_paginated`; 3 test layar (pindah halaman, filter → halaman 1, `meta` hilang) |
| backend S4: `q=%00` → 500 | pola `Query` + cek service → 422 | `test_search_refuses_control_characters` |
| backend S5: `list_templates()` blocking di event loop | katalog dibaca sekali saat startup (`load_templates` di lifespan, `to_thread`), lalu cache | review kode; suite |
| backend S6: celah test | 401 di semua route (dengan pasangan CSRF valid), `CSRF_FAILED` di POST/PATCH/arsip, replay `Idempotency-Key` → satu project, invarian di atas | `test_cross_project.py` +12 kasus, `test_projects.py` +12 test |
| backend S7 + AG-13 T8: belum ada bukti image dengan git | CI `clean-install` + Trivy @ bed63ac (tabel atas): 5 HIGH baru tanpa fix dari perl/libcurl, dalam cakupan EX-02 | log CI |
| backend S8 + AG-13 LOW (audit) + silent M4: audit settings tanpa nilai, tanpa IP; create gagal dan `git_https` ditolak tanpa jejak | `project.update` mencatat `{fields, settings:{key: nilai}}` hanya bila berubah. Semua event project membawa `ip`. Create gagal → log `project_create_failed` (tahap, alasan) + audit `result=error`; `git_https` → audit `result=denied` | assert audit di test PATCH, create, dan `git_https`; mutan → merah |
| silent H1 + backend N4: repo hilang/rusak tampil sehat | `repository.sync_state="error"`, `error=<kode>` (`repository_missing/_unreadable/_unsafe/_without_commit`), log level error, `repository_unsafe` diaudit; daftar mengecek folder; badge "Masalah repository" di layar | `test_a_missing_repository_is_reported_not_hidden` (GET, daftar, PATCH); test layar; mutan → merah |
| silent M1 + backend N5: create/update yang sudah commit bisa menjawab 500 | respons dibangun dari data yang sudah dimuat. `updated_at` dimuat di dalam transaksi; git setelah commit tidak pernah melempar | test PATCH dengan repo hilang → 200 |
| silent M3: kegagalan git → 409 disimpan idempotency 24 jam | 500 `GIT_ERROR` (kontrak §1 mengizinkan) | test diperbarui |
| silent M5/M6 + frontend S: hasil tak pasti, daftar basi, kunci idempotency | aturan satu key per body; key disimpan hanya selama hasil belum pasti (jaringan, 2xx tanpa envelope, halaman gateway 502/503/504, 409 `CONFLICT`); daftar di-invalidate `onSettled` tanpa ditunggu | test layar |
| AG-13 LOW: bidi/zero-width/C1 di nama project | Unicode `Cc/Cf/Zl/Zp` ditolak di service, route, dan skema frontend; author commit tanpa karakter itu | 3 kasus 422, test `_author`, test form; mutan → merah |
| AG-13 LOW + backend N2: lock sebelum keputusan izin | izin diputuskan dulu, baru `FOR NO KEY UPDATE` | review kode; matriks 404/403 |
| AG-13 LOW + backend N8: smoke script | password lewat stdin (`--data-binary @-`), id divalidasi UUID sebelum masuk `sh -c`, menolak `APP_DOMAIN` selain `localhost`, `fail` ke stderr, `last_commit` dibandingkan dengan `git log` di volume, project dicek di daftar | `bash -n`; dijalankan dengan domain lain → menolak; CI `clean-install` |
| AG-13 LOW: test bergantung urutan | query audit difilter `resource_id` | urutan terbalik → 71 passed |
| backend N1: author hanya tanda baca | jatuh ke bagian lokal email, lalu "KodeKantor user" | `test_author_is_what_git_accepts_and_a_reader_sees` |
| backend N3: spec `CONFLICT` vs kode `INVALID_TRANSITION` | spec diperbarui ke `INVALID_TRANSITION` | spec |
| backend N6 + silent L1: PATCH no-op diaudit; null dibuang diam-diam | `settings: null` dan kunci `null` → 422; audit hanya bila berubah | test PATCH |
| frontend 3 SHOULD + 6 NIT | sinkron kotak cari dengan URL, loading bukan "kosong", pengumuman arsip + fokus, teks error arsip per kode, ringkasan error field dari server, teks tanpa template, path terpusat (`lib/paths.ts`), panjang per code point | test layar per butir |

Temuan saat menjalankan:
- **Semgrep memblokir perbaikan saya sendiri** (CI @ 37c34f1): `text(f"SET LOCAL lock_timeout = …")` (`avoid-sqlalchemy-text`). Nilainya konstanta modul, jadi tidak bisa diinjeksi, tetapi gate-nya benar. Diganti dengan `set_config('lock_timeout', :timeout, true)` ber-bind (cd34fbc). Test baru menahan advisory lock owner dari koneksi lain: hasilnya 429 dalam batas waktu. Mutan dengan timeout mati → gagal dalam 15 s, tidak menggantung.

Tidak ditutup (alasan):
- **AG-13 LOW, role global di project yang diberi grant** (admin global + `readonly` di project X → bisa menulis X): perilaku persis API_CONTRACTS §2. **Keputusan PO:** biarkan, atau batasi role global dengan grant project. `test_a_global_role_counts_inside_a_project_it_was_granted` mengunci perilaku sekarang sampai PO memutuskan.
- **backend N7:** `repo_path` diturunkan dari id, bukan dari `workspace_ref`. Untuk tier S hasilnya sama; tier M (workspace store) harus membacanya.
- **backend N9:** route tanpa `response_model` (pola repo yang sama dengan `auth.py`).
- **silent L3 sebagian, L8, L9:** "Belum terdeteksi" pada stack kosong, arsip tanpa peringatan task berjalan, slug generik untuk nama non-Latin. Bersifat kosmetik atau informasional; arsip vs task berjalan diatur WP-3.1.
- **AG-13 [Menebak]:** cancel saat `to_thread(materialize)` dapat membiarkan thread salin masih menulis ketika `rmtree` berjalan. Kegagalan hapus di-log per path; penyapu folder yatim menyusul (catatan di bawah).

#### Delta review WP-2.1a: `kk-backend-reviewer` @ 37c34f1 (**WARN**: B1, B2, S1–S8, N1, N2, N6 CLOSED; 1 SHOULD, 6 NIT), AG-13 @ cd34fbc (**SIGNED OFF 🔒**, 2 LOW terbuka)

| Temuan | Penutupan | Bukti |
|---|---|---|
| backend SHOULD: sifat `_settle` (cancel kedua) dan `CreateGate` tidak dites; test cancel sekali juga lolos tanpa shield | `test_a_second_cancel_does_not_stop_the_cleanup`, `tests/unit/test_create_gate.py` (batas proses, satu per owner, cancel acak 100 putaran, holder dibatalkan), test lifespan | mutan `_settle` tanpa task/shield → merah; mutan tanpa drain → merah |
| backend NIT: cleanup yang crash mengganti error asli (juga `CancelledError`) | `_settle` menangkap `Exception`, mencatat `project_create_cleanup_crashed` lewat done-callback, dan mengembalikan False (error asli tetap) | `test_a_crashing_cleanup_never_replaces_the_create_error` |
| backend NIT: cleanup tidak ditunggu saat shutdown | `drain_cleanups()` di lifespan sebelum `aclose` (30 s; sisa di-log) | test lifespan: shutdown menunggu cleanup |
| backend NIT: COMMIT yang ternyata berhasil tetap 500 → retry membuat project kedua, audit success + error | baris dicek dulu. Bila ada → log warning `project_create_commit_reply_lost`, jawab 201 dengan project yang sudah dibangun sebelum COMMIT, tanpa audit error | `test_a_commit_whose_reply_was_lost_answers_with_the_project` (201, `last_commit` = HEAD, audit hanya `success`) |
| backend NIT: f-string di `text()` | sudah ditutup di cd34fbc (Semgrep) | CI |
| backend NIT: dua definisi status "selesai" | `workflow.states.FINISHED` (publik) dipakai `task_stats` | suite |
| backend NIT kecil (`total` bisa selisih satu di READ COMMITTED; `limiter=None` mematikan batas untuk pemanggil non-HTTP; test memakai nama privat) | dibiarkan: perilaku pagination offset yang biasa; `None` disengaja dan dikomentari | — |
| AG-13 LOW: CVE dari git di image backend bergantung pada EX-02 yang teksnya tidak menyebut curl/perl | baris EX-02 di DECISIONS ditambah analisis keterjangkauan untuk libcurl dan perl (tanpa operasi remote, `api` tanpa egress, tanpa perl). Dievaluasi ulang saat `git_https` | DECISIONS EX-02. **Bila PO menolak EX-02, bagian T8 sign-off gugur** |
| AG-13 LOW: role global di project yang diberi grant | keputusan PO (lihat "Tidak ditutup") | test mengunci |

- **Hasil lokal setelah putaran ini:** backend `pytest -q -p no:randomly` → **1325 passed, 1 skipped**; urutan terbalik (security → integration → unit gate) → 78 passed; ruff, pyright, dan lint-imports bersih.
- **Sign-off AG-13** berlaku untuk cd34fbc. Commit putaran ini mengubah `services/projects.py` dan `main.py`, jadi delta review AG-13 diminta lagi.
- **Delta AG-13 @ acdd4b6: SIGNED OFF 🔒.**
  - CI acdd4b6 9/9 hijau. Backend CI "1326 passed"; smoke "created 906e7ca4-… from python-basic"; Trivy `gate: PASS`, backend 50 HIGH tanpa fix (EX-02), 0 CRITICAL.
  - Ekspor bersih dengan DB sendiri: 1325 passed, 1 skipped; urutan terbalik 90 passed.
  - LOW baru: teks EX-02 keliru menyebut `worker` tanpa egress (`worker` ada di `outbound`) dan hanya menyebut `protocol.allow=never` (adapter juga memasang `protocol.https.allow=always`). Teks sudah dikoreksi. Mitigasinya tetap berlaku, karena tidak ada perintah remote dan allowlist config menolak kunci promisor/partial clone/submodule/insteadOf (diuji AG-13). Perbaikan khusus dokumen tidak butuh delta review.
  - Sign-off ini hanya untuk acdd4b6. Commit berikutnya ke `services/projects.py`, `api/deps.py`, `routes/projects.py`, `main.py`, atau `backend/Dockerfile` butuh delta review lagi.

AG-13, catatan di luar WP ini:
- Di Python 3.13, `tests/unit/test_paths.py::test_deep_symlink_chain_is_a_violation` gagal (DID NOT RAISE `PathViolation`). CI memin `UV_PYTHON=3.12`, sedangkan `.claude/rules/backend.md` menyebut "CI juga 3.13" (konflik #66). `backend/` tidak punya `.python-version`.
- `_audit_unsafe` menulis audit di setiap GET untuk repo yang dirusak, dan setiap `GET /projects/{id}` menjalankan git tanpa rate limit. Keduanya belum terjangkau, karena belum ada yang menulis `.git`. Ditinjau ulang saat sandbox mendapat akses repo.

Catatan untuk WP berikutnya (dari review, belum diubah):
- 2.1b/`git_https`: ekstrak `reserve_slug` dari create. Sanitasi `RepositoryOut.error` bila berasal dari remote.
- 2.1c: kunci per repo untuk commit/PUT. Kebijakan status untuk project `archived` dan `deleting_pending_approval` (PATCH masih jalan saat menunggu approval hapus; WP-3.1 harus membuat rename menggantikan approval `project_delete`). Simpan `sync_state=error` secara persisten. Siapkan penyapu folder yatim.
- Hapus project harus menghapus workspace. `stack` tidak pernah diperbarui setelah create.
- Sandbox: `.git` ada di dalam `repo/`. Mount `repo/` ke sandbox harus read-only atau tidak ada. `.kk/` belum di-exclude dari git (`commit_all` memakai `add -A`), bertentangan dengan SECURITY_MODEL §4.

### WP-2.1b (🔒 arsip) — project dari arsip: unggah streaming, job import di worker (FR-015, AC-02, T2, T4, T9, T10)

Spec: `docs/superpowers/specs/2026-11-02-wp-2.1b-archive-import-design.md`. Plan: `docs/superpowers/plans/2026-11-02-wp-2.1b-archive-import.md`. Konflik baru: #67 (sumber `upload`, alasan 409, kode `error`), #68 (HTTP 408 untuk unggahan macet/putus).

| Pemeriksaan | Hasil aktual |
|---|---|
| Backend `ruff check .`, `ruff format --check .`, `pyright`, `lint-imports` | bersih; 0 error; 3 kontrak dipertahankan |
| Backend `pytest -q` (PostgreSQL 16 + Redis 7 lokal) | putaran review: **1402 passed, 1 skipped**; urutan terbalik (`test_archive_import_http` → `test_cross_project` → `test_project_imports` → `test_celery_app`) → 115 passed. Putaran delta: **1415 passed, 1 skipped** |
| Mutan backend (putaran review) | 13 mutan: tanpa `flock`, tanpa sapu, `_is_ready` dimatikan, tanpa `keep_alive` di `finish_import`, tanpa pengembalian `repo/` saat commit gagal, `_abort` walau retry, rate limit sebelum cek status, tanpa batas boundary, tanpa perpanjangan lock saat streaming, tanpa `ClientDisconnect`, tanpa idle timeout → semuanya merah. Dua selamat dengan alasan: cek `lexists(repo)` sebelum publikasi (rename ke folder berisi tetap gagal `ENOTEMPTY`; klaim "tidak menghapus `repo/` asing" tetap terbukti) dan `keep_alive` di route pada test lock-hilang (`finish_import` juga menangkapnya; test perpanjangan lock ditambahkan dan membunuh mutan itu) |
| Frontend `format:check`, `lint`, `lint:guards`, `typecheck`, `vite build` | bersih |
| Frontend `vitest run` | **313 test** di 21 file (sebelumnya 278 di WP-2.1a) |
| CI @ fc52a8f (sebelum putaran review) | 9/9 hijau. `clean-install`: "project smoke: imported 4bdb1cb3-… from smoke.zip through the worker" (lewat Caddy, job worker sungguhan); restore drill semua PASS. `security-scan`: Semgrep 297 file, 6 temuan lama, `gate: PASS (0 blocking)`; `kodekantor/backend:dev` 50 HIGH tanpa fix (EX-02); pip-audit "No known vulnerabilities found"; `npm audit` 0; gitleaks "no leaks found" |
| CI @ 6e4a06e (putaran review) | **9/9 hijau.** `backend-test` "1403 passed"; `frontend` "Tests 313 passed (313)". `clean-install`: "project smoke: imported e626aa45-… from smoke.zip through the worker", restore drill semua PASS. `security-scan`: Semgrep 299 file, 6 temuan lama, `gate: PASS (0 blocking)`; `kodekantor/backend:dev` 50 HIGH tanpa fix, lolos lewat EX-02 yang **masih menunggu persetujuan PO**; pip-audit "No known vulnerabilities found"; `npm audit` 0; gitleaks "no leaks found" |
| Mutan putaran delta | 14 mutan, semuanya merah: claim tanpa row lock, tanpa `_take_back`, sapu tanpa cek penanda, tanpa `_keep` setelah ekstraksi, tanpa `_keep` sebelum publikasi, `_complete` dan `_settle_failure` tanpa `FOR NO KEY UPDATE`, `abort_import` tanpa shield, tanpa `_abort` saat `acquire` melempar, `_TRANSIENT` tanpa `ConnectionError`, tanpa audit job mati, penanda tidak dihapus setelah commit masuk, `rmtree` pada symlink, tanpa batas 30 menit |
| Backend nyata + Vite + Chromium | zip valid → `repo/` dengan commit "Import from archive kalkulator-main.zip", author pengunggah, folder pembungkus di-strip; zip berisi `.git` → alasan `git_dir` tampil dengan saran `git archive`; unggah ulang → workspace |

#### Review WP-2.1b @ fc52a8f: `kk-backend-reviewer` (WARN: 5 SHOULD, 6 NIT), `kk-frontend-reviewer` (**BLOCK**: 1 BLOCK, 6 SHOULD, 7 NIT), `kk-silent-failure-hunter` (8 MEDIUM, 10 LOW), AG-13 `kk-security-reviewer` (**SIGNED OFF 🔒** @ fc52a8f: 1 MEDIUM, 3 LOW, 3 INFO)

| Temuan | Penutupan | Bukti |
|---|---|---|
| AG-13 MEDIUM (T9) + silent B4/B6 + backend S3: worker yang mati setelah `repo/` dipublikasikan mengunci project selamanya; `.part`, staging, dan folder kerja tidak pernah disapu | **publikasi terakhir:** ekstraksi, git, dan deteksi stack di `.import-<job_id>`; rename ke `repo/` di dalam transaksi `ready`. **Sapu** di `begin_import` saat lock dipegang dan project belum `ready`: isi `uploads/`, `.import-*`, `..import-*`, `.repo.importing-*`, `repo/` tanpa commit. Gagal menyapu → 409 `cleanup_failed` | `test_a_new_upload_clears_what_an_interrupted_import_left` (skenario AG-13 + job lama terlambat → `skipped`); `test_a_sweep_that_cannot_clear_refuses_the_upload`; mutan tanpa sapu → merah |
| backend S1: dua salinan satu job merusak import yang berhasil | `flock` eksklusif pada arsip per run. Salinan kedua → `skipped` tanpa menyentuh apa pun; kernel melepasnya saat proses mati, jadi redelivery setelah OOM tetap berjalan (kunci Redis ber-TTL akan memblokirnya, karena itu tidak dipakai) | `test_two_runs_of_one_job_at_once_finish_it_once` (`["ready","skipped"]`, satu audit success); mutan tanpa `flock` → merah |
| backend S2: `rmtree` atas `repo/` berdasarkan dugaan | job hanya menghapus yang dinamai `job_id`-nya. `repo/` yang sudah ada → `import_failed`, tidak disentuh | `test_the_job_never_removes_a_repo_folder_it_did_not_publish`, `test_a_killed_runs_leftovers_go_and_the_job_runs_again` |
| silent B1 + B5 + B13: Redis/DB error → `skipped` dan arsip dibuang; `mark_failed` menelan kegagalan; tanpa batas waktu | `ImportRetry` untuk hasil tak pasti (Redis/DB tidak menjawab, kegagalan tak tertulis, commit tak pasti): arsip dan lock dipertahankan, Celery `autoretry_for` 5× backoff 5–120 s, `time_limit` 40 menit (< TTL lock 45 menit) | `test_when_redis_cannot_answer_the_job_is_retried_with_its_archive`, `test_when_a_failure_cannot_be_recorded_the_job_is_retried`, `test_a_commit_that_fails_takes_the_repository_back_and_retries`; `tests/unit/test_celery_app.py::test_import_job_is_registered_with_retries_and_a_time_limit` |
| silent B2 + AG-13 LOW + backend NIT1: lock tidak pernah diperpanjang atau diperiksa; tanpa idle timeout | `import_lock.refresh` (compare-and-expire). Diperpanjang tiap 60 s selama streaming, di `finish_import` di bawah lock baris (hilang → 409 `import_expired`), saat job mulai, setelah ekstraksi, dan sebelum publikasi. Idle 60 s → 408 `upload_stalled`. Job yang lock-nya kedaluwarsa tanpa pengganti → `error = import_expired` + audit | `test_a_slow_upload_keeps_its_lock_while_it_streams`, `test_an_upload_whose_lock_expired_is_refused_when_it_finishes`, `test_an_upload_that_loses_its_lock_midway_is_refused`, `test_an_upload_that_stalls_is_408_and_the_key_stays_free`, `test_a_job_whose_lock_expired_with_nobody_after_it_fails_visibly` |
| silent B7 + AG-13 LOW (T10): jejak audit berhenti di `import_requested` | kegagalan ditulis bersama audit `project.import result=error` dalam satu transaksi (enqueue gagal `import_not_started`, semua kegagalan job); job yang disalip → audit `import_superseded` | assert audit di `test_rejects_over_http` (26 kasus), enqueue gagal, job basi, lock kedaluwarsa |
| silent B8: jawaban commit hilang → `repo/` dihapus padahal `ready` | status dibaca ulang; `ready` → `repo/` dipertahankan; tidak pasti → `ImportRetry` | `test_a_commit_whose_reply_is_lost_keeps_the_repository`; mutan → merah |
| silent B9: lock tertinggal bila `acquire` melempar setelah SET | `_abort` (compare-and-delete) di jalur error `acquire` | review kode |
| silent B10: thread broker menggantung | `broker_transport_options` socket timeout 5 s / connect 2 s | test unit |
| silent B11 + backend NIT5: `ClientDisconnect` jadi traceback ERROR; penolakan tanpa log; arsip hilang tak terbedakan | `ClientDisconnect` → 408 `client_disconnected` (tidak disimpan idempotency) + log info; penolakan 409 di-log `project_import_refused`; `project_import_archive_missing` terpisah → `import_failed` | `test_a_real_disconnect_mid_upload_leaves_nothing_and_frees_the_key` (ASGI mentah, `http.disconnect`), `test_a_missing_archive_with_the_lock_held_fails_visibly` |
| backend S4: boundary panjang → 500 | `_boundary` menolak > 70 karakter (RFC 2046) → 422 sebelum lock | `test_a_boundary_longer_than_70_is_422_before_the_lock`; mutan → merah |
| backend S5 + AG-13 LOW (T4): celah test | stream idempotency (409 panjang beda, 413 panjang terdeklarasi tanpa membaca body), 413 HTTP (sink dan batas body total), body potongan 7 byte, rate limit 429/503, tar.gz dengan pembungkus lewat HTTP, registrasi task Celery | 8 test baru di `test_project_imports.py` + test unit |
| AG-13 LOW + backend NIT4: batas header multipart dari default library; floor `>=0.0.9` | `max_header_count=8`, `max_header_size=4224` eksplisit; `python-multipart>=0.0.32` | `pyproject.toml`, `uv.lock` |
| backend NIT2: rate limit terpakai sebelum cek status | urutan: status → rate → lock → status lagi | `test_the_upload_rate_limit_counts_only_uploads_that_could_be_taken` |
| backend NIT3: drift spec dan docstring | spec WP-2.1b ditulis ulang (argumen `filename`, alasan 409 baru, sapu, publikasi terakhir, retry, 408); docstring idempotency menyebut mode stream | spec, `core/idempotency.py` |
| backend NIT6: `_stats` hanya melihat `sync_state` | `_awaiting_import`: provider `upload` dan belum `ready` (detail dan daftar) | suite |
| frontend BLOCK: key idempotency terikat objek `File` | key per project + nama/ukuran/`lastModified`; "Coba lagi" mengirim ulang file yang dipegang tanpa memilih ulang | test file baru dengan identitas sama → key sama; file lain → key baru |
| frontend 6 SHOULD + silent F1–F5 | cache detail diisi dari repository 202 (tidak ada kegagalan lama sesudah 202); input kosong = tidak ada file terkirim; layar unggah ulang menampilkan alasan gagal terakhir, import yang berjalan tanpa form, project arsip; progressbar diuji; polling sampai `ready`/`error` termasuk state lain; fokus dan satu pengumuman; teks 408, `import_expired`, `cleanup_failed` | 35 test layar baru (278 → 313) |
| frontend 7 NIT | "lambat" setelah 5 menit, aksi unggah hanya untuk project aktif, daftar di-poll saat ada baris `importing`, `accept` dari `ARCHIVE_SUFFIXES`, "200 MiB", `navigate(..., {replace:true})`, judul dokumen layar impor | test layar |

Tidak ditutup (alasan):
- **silent B3:** Redis error saat membaca daftar/detail → log error, tanpa `import_incomplete`. Redis mati juga mematikan login dan unggah, jadi "unknown" tidak menyesatkan lebih lama dari gangguannya.
- **silent B12 / AG-13 INFO:** key sama + panjang sama + file berbeda → 202 pertama diputar ulang (sha256 di jawaban menyebut file yang dimaksud). Disengaja (Goal 7), tidak lintas sesi.
- **AG-13 INFO:** `user_id` di pesan antrean tidak dicocokkan dengan pelaku `import_requested`; otoritas bertumpu pada Redis privat. Diterima untuk tier S.
- **frontend NIT:** belum ada tombol batal unggahan (XHR tetap jalan bila halaman ditinggal). Server menyapu sisanya; diangkat lagi bila ada keluhan.
- **`flock` per host:** klaim run hanya berlaku bila semua worker satu node (tier S). Tier M/L dengan worker di banyak node butuh klaim lintas host.

#### Delta review WP-2.1b @ 6e4a06e: `kk-backend-reviewer` (WARN: S1–S5, NIT1–6 CLOSED dengan catatan; 3 SHOULD, 5 NIT baru), AG-13 (**SIGNED OFF 🔒** @ 6e4a06e: 4 LOW, 5 INFO)

| Temuan | Penutupan | Bukti |
|---|---|---|
| AG-13 LOW (T9/T2): bila key Redis lenyap antara pemeriksaan lock terakhir job dan commit-nya, sapu unggahan baru menghapus `repo/` yang baru dipublikasikan (direproduksi AG-13) | pemeriksaan ulang dan penyingkiran `repo/` di `begin_import` memakai row lock project yang dipegang job dari `_keep` sampai commit `ready` | `test_an_upload_racing_a_publish_waits_for_its_commit` (probe AG-13 dijadikan test: unggahan menunggu, lalu 409, `repo/` utuh); mutan tanpa row lock → merah |
| backend SHOULD-1: hasil commit tak pasti → retry gagal `import_failed` karena menemukan `repo/` miliknya sendiri | penanda `.import-<job_id>.published` ditulis sebelum rename; retry dengan penanda + project masih menunggu → `repo/` diambil kembali dan import diulang; commit yang ternyata masuk → retry `skipped`, penanda dihapus | `test_a_commit_with_an_unknown_outcome_is_taken_back_by_the_retry`, `test_a_commit_with_an_unknown_outcome_that_landed_is_left_alone` |
| backend SHOULD-3: sapu menghapus `repo/` berdasarkan status DB saja (jebakan WP-2.1c) | hanya `repo/` berpenanda yang disingkirkan; `repo/` lain → 409 `repository_not_empty`, utuh | `test_the_job_never_removes_a_repo_folder_it_did_not_publish` (kini juga unggah ulang → 409); mutan tanpa cek penanda → merah |
| backend SHOULD-2: 13 guard tanpa test | test untuk lock hilang setelah ekstraksi (git tidak jalan), disalip sebelum publikasi, row lock `_complete` dan `_settle_failure`, shield `abort_import`, `_abort` saat `acquire` melempar, `ConnectionRefusedError` → retry | 7 test baru; mutan masing-masing → merah |
| AG-13 LOW (T10): akhir import tanpa audit (time limit, cancel, retry yang tak terantre) | sapu menulis `project.import result=error reason=import_incomplete` untuk setiap job mati yang ditemukan (arsip atau folder kerja) | assert audit di `test_a_new_upload_clears_what_an_interrupted_import_left`; mutan → merah |
| AG-13 LOW (T4) + backend NIT-A: tanpa batas waktu total unggahan | 30 menit (sama dengan klien web) → 408 `upload_too_slow` | `test_an_upload_that_never_ends_is_408`; mutan → merah |
| AG-13 LOW: sifat utama sapu tanpa test regresi | project `ready` → 409 tanpa menyapu; symlink di `uploads/`, folder kerja, dan `repo/` hanya di-unlink | `test_a_ready_project_refuses_an_upload_and_nothing_is_swept`, `test_the_sweep_never_follows_a_link`; mutan `rmtree` pada symlink → merah |
| backend NIT-B: retry karena Redis mati menumpang broker yang sama | didokumentasikan (spec, docstring task): retry yang tak terantre berakhir `import_incomplete`, lalu sapu + audit pada unggahan berikutnya; komentar backoff dikoreksi (jitter acak) | spec |
| backend NIT-C: fd klaim ditutup terakhir di `finally` | `os.close` di `finally` terluar | review kode |
| backend NIT-D, NIT-E: drift spec; 202 vs §3 | spec diperbarui (alasan 409 lengkap, penanda, row lock, 30 menit); konflik #67 mencatat bentuk 202 | spec, §8 |

Tidak ditutup:
- **AG-13 INFO I2:** run terlambat dari job yang disalip bisa menulis `import_superseded` lebih dari sekali, dan sapu menulis `import_incomplete` untuk arsip job yang belum jalan (antrean > 45 menit). Duplikat jejak, bukan jejak yang hilang.
- **AG-13 INFO I3:** job yang dibunuh tepat setelah commit `ready` (tanpa redelivery) meninggalkan arsip ≤ 200 MiB di project `ready`, karena sapu tidak berjalan untuk project `ready`. Penyapu folder yatim WP-2.1c harus mencakup `uploads/` project `ready`.
- **AG-13 INFO I4:** error `_claim` selain `BlockingIOError` (ELOOP, EACCES) tidak bisa dipicu user; berakhir `import_incomplete`.
- **backend NIT6:** `_awaiting_import` setara dengan pemeriksaan lama selama blank/template selalu `ready`; tidak ada test yang membedakan.

#### Delta AG-13 @ 9cb4223: **SIGNED OFF 🔒** (1 MEDIUM laten, 3 LOW, 2 INFO); CI @ 9cb4223 9/9 hijau

- **CI 9cb4223:**
  - `backend-test` "1416 passed"; `frontend` "Tests 313 passed (313)".
  - `clean-install`: "project smoke: imported f159a8e3-… from smoke.zip through the worker", restore drill semua PASS.
  - `security-scan`: Semgrep 299 file, 6 WARNING lama (`insecure-file-permissions`), `gate: PASS (0 blocking)`.
  - Image backend 50 HIGH tanpa fix, lolos lewat EX-02 (menunggu PO); pip-audit, `npm audit`, dan gitleaks bersih.

| Temuan | Penutupan | Bukti |
|---|---|---|
| MEDIUM (T9, laten sampai WP-2.1c): penanda basi di project `ready` (commit masuk, hasil tak pasti, retry tak pernah jalan) mengizinkan sapu menghapus repo nyata begitu `sync_state = error` disimpan (probe P3b AG-13) | penanda hanya berlaku untuk job tanpa audit `project.import result=success` (ditulis di transaksi `ready` yang sama, append-only) | `test_a_committed_jobs_stale_marker_never_sweeps_its_repository` (P3b jadi test: 409, HEAD utuh); mutan → merah |
| LOW (T9): `_take_back` dan `_is_ready` membaca tanpa row lock; commit run 1 yang masih berjalan lalu masuk meninggalkan `ready` tanpa `repo/` (probe P4) | `_take_back` di bawah row lock project, cek ulang `_waiting`, `_keep`, baru rename. `_is_ready` me-rollback sesi gagal lalu membaca dengan `FOR SHARE` (≤ 30 s) | `test_taking_back_waits_for_a_commit_still_settling` (P4 jadi test); mutan `_take_back` tanpa row lock → merah. `FOR SHARE` di `_is_ready`: hanya review kode |
| LOW (T10): dua hasil bertentangan untuk satu job; penanda tertinggal saat rename gagal | rename yang gagal menghapus penanda; sapu melewati job yang sudah punya event `project.import` | `test_a_job_with_an_outcome_is_not_given_a_second_one`; 2 mutan → merah |
| LOW: mutan cabang `.archive` di `_dead_jobs` dan cabang `uploads/` symlink selamat | test untuk keduanya | `test_a_job_whose_retries_ran_out_gets_its_outcome_from_the_next_upload`, `test_an_uploads_folder_that_is_a_link_is_replaced_not_followed`; mutan → merah |

- **Lokal setelah putaran ini:** `pytest -q` → **1420 passed, 1 skipped**; ruff, pyright, dan lint-imports bersih.
- **Tidak ditutup:**
  - INFO M11 (`lexists` vs `exists` untuk `repo` symlink menggantung): akibatnya fail-safe (409/`import_failed`).
  - INFO: audit dari sapu bersifat best-effort; bila DB gagal, hasilnya hanya tercatat di log `project_import_audit_failed`.

#### Delta AG-13 @ b3b1906: **SIGNED OFF 🔒** (4 LOW, tanpa MEDIUM ke atas); CI @ b3b1906 9/9 hijau

- **CI b3b1906:**
  - `backend-test` "1421 passed"; `frontend` "Tests 313 passed (313)".
  - `clean-install`: "project smoke: imported 9bd7c132-… from smoke.zip through the worker", restore drill semua PASS.
  - `security-scan`: Semgrep 299 file, 6 WARNING lama, `gate: PASS (0 blocking)`.
  - Image backend 50 HIGH tanpa fix, lolos lewat EX-02 (menunggu PO); pip-audit, `npm audit`, dan gitleaks bersih.
- AG-13 tidak menemukan deadlock: setiap transaksi mengunci baris `projects` sebelum `repositories`. `_is_ready` juga tidak menunggu sesinya sendiri: rollback lebih dulu, batas waktu ≈ 32 s.

| Temuan | Penutupan | Bukti |
|---|---|---|
| F1 (regresi delta sebelumnya): cancel yang tiba setelah thread rename selesai menghapus penanda `repo/` yang sudah terbit → project macet 409, job tanpa hasil | penanda dihapus hanya bila rename melempar `OSError` | `test_a_cancel_after_the_publish_keeps_the_marker_for_the_sweep` (unggahan berikutnya 202 + `import_incomplete`); mutan `BaseException` → merah |
| F2 (laten sampai WP-2.1c): pembacaan ulang memutuskan dari `sync_state` | `_is_ready` menunggu dengan `FOR SHARE`, lalu memutuskan dari event `project.import` success milik job ini | `test_a_landed_commit_is_kept_even_if_the_state_changed_before_the_read_back`; mutan → merah |
| F3: `_take_back` yang mendapati commit sudah masuk meninggalkan penanda basi | penanda dihapus bila status `ready` | assert tambahan di `test_taking_back_waits_for_a_commit_still_settling`; mutan → merah |
| F4: job yang key-nya lenyap mendapat `import_incomplete` dari sapu, lalu `import_superseded` | audit `import_superseded` dilewati bila job sudah punya event `project.import` | `test_a_job_overtaken_after_the_sweep_gave_it_an_outcome_gets_no_second_one`; mutan → merah |

- **Lokal:** `pytest -q` → **1423 passed, 1 skipped**; ruff, pyright, dan lint-imports bersih.
- **Syarat masuk WP lain** (diminta AG-13, mengikat):
  - **WP-2.1c** (atau siapa pun yang menulis `repositories.sync_state` selain import): perlindungan `repo/` bertumpu pada event `project.import result=success` per job. Penulis status baru tidak boleh membuat `repo/` tanpa event itu terlihat sebagai publikasi yang gagal.
  - **WP retensi audit** (purge 365 hari, tracker §WP-1.1): event `project.import` success harus tetap ada selama project hidup, atau penanda basi dibersihkan dulu. Alternatifnya, saksi dipindah ke kolom tahan lama (mis. `repositories.import_job_id`, butuh migrasi).
- **Tidak ditutup (INFO AG-13):**
  - Mutan yang selamat untuk guard yang gagal dengan aman: `_claim_folder` menganggap semua hasil sebagai commit, `_take_back` tanpa `_keep`, tanpa timeout baca ulang, sapu tetap mengaudit saat hasil tidak terbaca. (Mutan tanpa `FOR SHARE` **tidak** gagal dengan aman; ditutup di delta c7222ee di bawah.)
  - Penantian row lock tanpa `lock_timeout`, sama dengan `_complete` dan `_settle_failure`; dibatasi `time_limit` 40 menit.

#### Delta AG-13 @ c7222ee: **SIGNED OFF 🔒** (F1–F4 tertutup; 1 LOW, 3 INFO); CI @ c7222ee 9/9 hijau

- **CI c7222ee:**
  - `backend-test` "1424 passed"; `frontend` "Tests 313 passed (313)".
  - `clean-install`: "project smoke: imported de03baaf-… from smoke.zip through the worker", restore drill dan egress probe lolos.
  - `security-scan`: Semgrep 299 file, 6 WARNING lama, `gate: PASS (0 blocking)`.
  - Image backend 50 HIGH tanpa fix, lolos lewat EX-02 (menunggu PO); pip-audit, `npm audit`, dan gitleaks bersih.
- AG-13 memastikan `FOR SHARE` di `_is_ready` berada pada baris yang dikunci commit `ready` yang masih berjalan. `audit.write` mem-flush UPDATE `repositories` sebelum COMMIT, dan isolasinya READ COMMITTED.

| Temuan | Penutupan | Bukti |
|---|---|---|
| LOW (T9): `FOR SHARE` di `_is_ready` tanpa test; tracker keliru menyebut mutannya gagal dengan aman (tanpa `FOR SHARE`: `ready` dengan event success tetapi tanpa `repo/`) | probe AG-13 dijadikan test; test menutup sesi pemegang lock di `finally`, jadi mutan gagal tanpa menggantung; baris tracker dikoreksi | `test_the_read_back_waits_for_a_commit_still_settling[commit_lands, commit_rolls_back]`, `test_a_reply_lost_while_the_commit_settles_keeps_the_repository`; mutan tanpa `FOR SHARE` → 3 merah |

- **Lokal:** `pytest -q` → **1426 passed, 1 skipped**; ruff, pyright, dan lint-imports bersih. Putaran ini hanya mengubah test dan dokumen; kode di bawah sign-off c7222ee tidak berubah.
- **Syarat masuk WP-2.1c (tambahan AG-13, INFO-3):** hanya `_complete` yang boleh membawa project upload dari `importing` ke `ready`.
- **Tidak ditutup:**
  - INFO-1: mutan `_has_outcome` fail-closed tidak terdeteksi test. Kebijakannya "duplikat lebih baik daripada hasil hilang".
  - INFO-2: job yang disalip dan sapu unggahan baru yang membaca bersamaan masih bisa menghasilkan dua hasil (duplikat, bukan hilang).


### WP-2.1c (🔒 path/git) — API workspace dan git (FR-019, FR-028, FR-029, FR-031, FR-032, FR-113, FR-115, FR-117, FR-118; AC-03/AC-09 bagian API)

Spec: `docs/superpowers/specs/2026-11-02-wp-2.1c-workspace-git-api-design.md`. Plan: `docs/superpowers/plans/2026-11-02-wp-2.1c-workspace-git-api.md`. Hanya backend; layar IDE tetap WP-2.2.

| Pemeriksaan | Hasil aktual |
|---|---|
| Backend `ruff check .`, `ruff format --check .`, `pyright`, `lint-imports` | bersih; 0 error; 3 kontrak dipertahankan |
| Backend `pytest -q` (PostgreSQL 16 + Redis 7 lokal) | implementasi: **1540 passed, 1 skipped**; setelah putaran review: **1568 passed, 1 skipped**; setelah delta AG-13: **1580 passed, 1 skipped**; setelah delta AG-13 kedua: **1585 passed, 1 skipped** |
| Mutan | implementasi: 6 (Task 3) + 3 (Task 5; mutan "tanpa kunci" selamat dari test balapan, lalu dibunuh test baru `test_a_commit_waits_for_a_write_in_progress`). Putaran review: 13 mutan, semuanya merah. Delta AG-13: 13 mutan, semuanya merah. Delta AG-13 kedua: 6 mutan, semuanya merah |
| Syarat masuk AG-13 dari WP-2.1b | dipenuhi (konfirmasi AG-13 @ 6cabb4f): tidak ada penulis `sync_state` baru, tidak ada yang menghapus `repo/` |
| CI @ 6cabb4f (implementasi) | 9/9 hijau |
| CI @ 2bab00c (putaran review) | 9/9 hijau |
| CI @ 3e5f1c7 (delta AG-13 kedua, head kode) | 9/9 hijau |
| CI @ b733ecc (delta AG-13) | 9/9 hijau (`backend-lint`, `backend-types`, `backend-test`, `frontend`, `sandbox-test`, `sandbox-isolation`, `infra-config`, `security-scan`, `clean-install`) |

#### Review WP-2.1c @ 6cabb4f: `kk-backend-reviewer` (**BLOCK**: 1 BLOCK, 6 SHOULD, 7 NIT), `kk-silent-failure-hunter` (1 HIGH, 6 MEDIUM, 8 LOW), AG-13 (**SIGNED OFF 🔒** @ 6cabb4f: 4 LOW, 8 INFO)

| Temuan | Penutupan | Bukti |
|---|---|---|
| backend BLOCK + silent F1 (HIGH) + AG-13 F1: commit gagal meninggalkan index ter-stage; commit `paths` berikutnya membawa file yang tidak dipilih | `GitAdapter.commit` mengembalikan index ke HEAD sebelum `add` dan setelah kegagalan apa pun (`restore --staged`/`rm --cached`, tanpa `reset`; guard FR-118 tetap); pesan kosong ditolak | `test_a_stale_staged_file_never_rides_a_paths_commit`, `test_a_failed_commit_leaves_nothing_staged`, `test_a_paths_commit_holds_only_its_paths_after_a_failure`, `test_a_failed_add_leaves_nothing_for_the_next_commit` (P11 AG-13); mutan → merah |
| silent F4 + AG-13 I3: git yang dibunuh timeout meninggalkan `index.lock`, semua commit berikutnya gagal | adapter menghapus `index.lock` setelah ia sendiri membunuh git (di bawah kunci workspace); log error. @ 2bab00c baru sebagian (hanya timeout, di luar reset awal); lengkap di delta AG-13 (L1, di bawah) | `test_a_timeout_leaves_no_index_lock`; mutan → merah |
| silent F5 + backend S6 + silent L5: `.kk/...` yang dipilih hilang diam-diam; status/diff menampilkan `.kk` yang tidak bisa di-commit | 422 `path_excluded`; `/.kk/` di `info/exclude`; service menyaring `.kk` dari status dan diff | `test_selected_kk_paths_are_refused`, `test_status_and_diff_hide_kk`, `test_kk_is_ignored_by_status`, `test_the_first_commit_never_stages_kk` (cabang repo tanpa commit, M4) |
| silent F3: 409 sementara (busy, timeout git) disimpan idempotency 24 jam | jawaban dengan `Retry-After` tidak disimpan; `WorkspaceBusy` dan `GitError("timeout")` membawa `Retry-After` | `test_a_busy_workspace_answer_is_not_kept_for_the_key`; mutan → merah |
| silent F6 + backend S5 + silent L7: `exclusive()` memarkir thread executor selama menunggu; cancel lambat | `flock` non-blocking dipoll dari event loop (`asyncio.sleep`); pembukaan file kunci dipakai bersama `_locked` | `test_exclusive_waiters_do_not_park_threads` (40 penunggu, `to_thread` lain tetap < 1 s), `test_exclusive_cancel_is_prompt_and_leaks_nothing`, `test_exclusive_gives_up_with_busy_and_stays_usable`, `test_exclusive_releases_when_the_body_fails` (M15/M16) |
| silent F7 + L8: `open_repo` menyamarkan status | `error` → `repository_error` + kode; baris hilang → 500 + log; `.git` hilang atau symlink → `repository_missing` + log error; status non-arsip → `project_not_active` | `test_a_failed_import_is_reported_as_an_error`, `test_a_symlinked_git_dir_is_a_missing_repository`; mutan → merah |
| silent F2 + backend NIT: audit sukses bisa hilang | penulisan audit di-shield (tahan cancel; @ 2bab00c jendela antara commit dan audit masih terbuka, ditutup di delta AG-13 M1); `PatchRollbackFailed` diaudit `result=error` + path; `git.commit` mencatat file yang benar-benar di-commit (`diff-tree`) | `test_a_patch_that_cannot_be_rolled_back_is_on_the_record`, `test_commit_files_lists_what_the_commit_holds`, ekspektasi audit commit |
| backend S1 + AG-13 F2: branch `kk` memblokir `kk/task-*`; `refs/heads/x` membuat ref aneh | `kk`, `kk/...`, `refs/...`, `HEAD` → 422 | `test_reserved_branch_names`; mutan → merah |
| backend S2 + silent L2 + AG-13 I7: `POST /files` pada file yang ada → `VERSION_CONFLICT` | dipetakan ke `CONFLICT` (file atau folder) | `test_post_on_an_existing_file_is_a_conflict` |
| backend S3: tree terpotong di 500, bukan `MAX_TREE_ENTRIES` | `max_entries=fs.MAX_TREE_ENTRIES` | `test_tree_lists_more_than_500_entries` (600 entri); mutan → merah |
| backend S4 + AG-13 F4: celah test | arsip untuk `git/branch`/`git/commit`, prasyarat tulis pada upload belum siap, field tak dikenal → 422, path T2 lewat body/patch/tree (P4) | `test_git_writes_on_an_archived_project_are_refused`, `test_writes_need_a_ready_repository`, `test_unknown_body_fields_are_refused`, `test_paths_in_bodies_never_leave_the_repository` |
| silent L1 + AG-13 F3: `paths` raksasa → `OSError E2BIG` → 500 | maks 500 path, 128 KiB total, `min_length=1` → 422 | `test_too_many_path_bytes_are_422` |
| backend NIT: `?ref=` dan `?path=` kosong | berarti root/main | `test_empty_ref_and_tree_path_mean_the_root` |
| backend NIT: `from_ref` diterima selain alias `from`; test idempotency di file git | hanya alias `from`; test dipindah ke `test_workspace_api.py` | review kode |

Tidak ditutup (alasan):
- **silent L3:** `commit()` mengembalikan `None` bila `current_commit` gagal setelah commit sukses (kemungkinan sangat kecil; butuh `HEAD` rusak).
- **silent L4:** HEAD rusak menjadi `log: []`/diff terhadap pohon kosong. Kode WP-1.2, baru terjangkau lewat HTTP; `status` sudah gagal keras.
- **Penunggu kunci tulis (`_write_file`) masih memarkir thread** sampai `LOCK_WAIT_S`. Desain WP-1.2; berdampak hanya bila ≥ ukuran pool penyimpanan bersamaan di satu project. Tindak lanjut: pindahkan tulis ke pola `exclusive()`.
- **backend NIT (Lead):** batas body 10 MiB hanya untuk request ber-`Idempotency-Key`; PUT 40 MiB tanpa key dibaca penuh sebelum 413. Usulan: batas di middleware untuk semua mutasi, atau di Caddy.
- **backend NIT / AG-13 I6 (Lead):** nama test di TEST_STRATEGY §AC-03 dan SECURITY_MODEL T9 tidak sama dengan test yang ada; dua file test security baru belum terdaftar.
- **AG-13 I2:** `git/diff` meng-hash file untracked ke `/tmp` di setiap request (reader bisa memicu). Ikut WP rate limit/kuota.
- **AG-13 I4:** `.kk/` di `repo/` bisa ditulis user lewat `PUT`. WP yang membaca `.kk/junit.xml` dari mount `repo/` wajib mengosongkannya dulu.
- **AG-13 I5:** commit tanpa `paths` ikut men-stage `.env`/kunci yang ditolak `USER_WRITE` (sama dengan commit import). Diputuskan sebelum push remote (FR-119/120).
- **Penyapu folder yatim** (project terhapus, sisa `uploads/` di project `ready`, INFO I3 WP-2.1b): menghapus data adalah T9, butuh desain sendiri; tindak lanjut terpisah.
- **Perubahan `commit_all` ke import/template:** `.kk/` dari arsip kini tidak ter-commit (file tetap di disk). Dikonfirmasi AG-13 (delta @ 2bab00c, I-c); `sub/.kk/` bersarang tetap ter-commit, sesuai desain (pola di-anchor ke root).

#### Delta AG-13 @ 2bab00c: **SIGNED OFF 🔒** (1 MEDIUM, 2 LOW, 5 INFO); semua MEDIUM/LOW dan dua INFO ditutup

| Temuan | Penutupan | Bukti |
|---|---|---|
| **M1 (MEDIUM, T10, regresi):** commit sudah di git tetapi tanpa audit `git.commit` dan klien mendapat 409. Penyebabnya `commit_files` melewati batas output 16 MiB atau timeout, `git commit` dihentikan setelah ref ditulis, atau request dibatalkan di antara commit dan audit | (1) `_record_commit`: kegagalan daftar file dicatat `files: null` + `files_error`, event tetap ditulis, request 201; (2) `GitAdapter.commit`: bila langkah commit gagal tetapi HEAD maju, sha baru dikembalikan (log warning `git_commit_landed_despite_error`); (3) setiap tulis service (simpan, buat, hapus, patch, branch, commit) beserta audit-nya berjalan sebagai satu task yang tidak berhenti bila request dibatalkan (`_to_the_end`); commit mengambil kunci workspace di dalam task itu | `tests/security/test_workspace_audit.py`: `test_a_commit_whose_file_list_is_too_large_is_still_audited` (`max_output` 128 nyata), `test_a_commit_git_reported_as_stopped_is_still_audited`, `test_a_commit_cancelled_after_it_landed_is_still_audited`, `test_a_save_cancelled_after_it_reached_the_disk_is_still_audited`; `test_a_commit_that_landed_is_returned_though_git_was_stopped`; `test_a_cancelled_commit_keeps_the_lock_until_git_ends`; mutan (tanpa tangkap, tanpa shield, kunci di luar task, simpan tanpa `_to_the_end`, `_landed` selalu `None`) → merah |
| **L1 (LOW, T9):** `index.lock` yatim setelah reset awal kena timeout, setelah output melewati batas, atau setelah cancel | reset awal dipindah ke dalam `try`; `_killed_git` (timeout, `output_too_large`, `CancelledError`) memicu penghapusan lock, juga di reset setelah kegagalan; cancel langsung naik tanpa menjalankan git lagi | `test_every_kill_leaves_no_index_lock[restore-timeout]`, `[add-output_too_large]`, `test_a_cancelled_commit_leaves_no_index_lock`; 4 mutan → merah |
| **L2 (LOW):** `exclusive()` membocorkan fd file kunci bila di-cancel saat file dibuka di thread | pembukaan di-shield; saat cancel, callback menutup fd yang dikembalikan thread | `test_exclusive_cancel_while_opening_the_lock_file_leaks_no_fd` (3 cancel, jumlah fd `/proc/self/fd` sama); mutan → merah |
| **I-a:** task audit yang di-shield tanpa referensi kuat; exception-nya hilang setelah cancel | `_RUNNING` memegang task sampai selesai; kegagalan task yang tidak lagi ditunggu → log error `workspace_write_failed_after_cancel` | `test_a_write_that_fails_after_the_request_was_cancelled_is_logged`; mutan → merah |
| **I-d:** reset index repo tanpa commit (`rm --cached -r`) ditolak bila isi ter-stage berbeda dari file dan HEAD | `read-tree --empty` | `test_an_unborn_index_that_differs_from_the_file_is_reset`; mutan → merah |

#### Delta AG-13 @ b733ecc: **SIGNED OFF 🔒** (2 LOW, 5 INFO); kelima temuan delta sebelumnya dikonfirmasi tertutup (12 test baru merah terhadap kode 2bab00c), kedua LOW ditutup

| Temuan | Penutupan | Bukti |
|---|---|---|
| **LOW: `_landed` menganggap setiap HEAD yang maju sebagai commit ini** (R-04, laten: belum ada pemanggil yang melanggar aturan satu penulis). Probe: merge penulis lain dikembalikan sebagai commit user, `mine.txt` tidak ter-commit di mana pun | HEAD baru hanya diterima bila parent = HEAD sebelumnya (tanpa parent untuk commit pertama), email author sama, dan pesan sama (whitespace dinormalisasi); selain itu error asli + log error `git_head_moved_by_another_writer` | `test_a_head_moved_by_another_writer_is_not_taken_as_this_commit[another_author / another_message / same_identity_other_parent]`, satu kasus per bukti; 4 mutan → merah |
| **LOW: `_RUNNING` tidak dikuras saat shutdown; log tanpa konteks** (T10; nyata bila `--timeout-graceful-shutdown` dipasang) | `drain_writes(30 s)` dipanggil lifespan setelah `drain_cleanups`, sebelum `dependencies.aclose()`; task diberi nama `<aksi> <project_id>` dan nama itu ikut di setiap log task yang tidak lagi ditunggu | `test_shutdown_waits_for_writes_still_running`, `test_the_app_drains_writes_at_shutdown`; 2 mutan → merah |

INFO delta kedua: I-2 (penumpukan tulis), I-3 (TOCTOU otorisasi), dan I-5 (aman menghapus `index.lock` saat cancel, untuk semua pemanggil sekarang) tidak membutuhkan perubahan. I-1 dan I-4 masuk "Tidak ditutup" di bawah.

Tidak ditutup dari delta kedua (alasan):
- **I-1:** idempotency membebaskan key saat request di-cancel walau tulisnya tetap berjalan; retry dengan key yang sama dijalankan lagi. Efek ganda tetap dicegah `expected_sha256`, `AlreadyExists`, `branch_exists`, `nothing_to_commit`; commit tanpa `paths` bisa membawa perubahan yang muncul belakangan. Jalur cancel ini tidak terpicu oleh disconnect di uvicorn [Kemungkinan Besar, AG-13].
- **I-4:** bila `rev-parse`/`log` di `_landed` sendiri gagal tepat setelah `git commit` yang sudah menulis ref dihentikan, hasilnya 409 tanpa audit (dua kegagalan berurutan).

#### Delta AG-13 @ 3e5f1c7: **SIGNED OFF 🔒** (0 CRITICAL/HIGH/MEDIUM/LOW, 4 INFO baru); kedua LOW @ b733ecc dikonfirmasi tertutup

Bukti AG-13:
- probe merge asing kini ditolak (`git_head_moved_by_another_writer`, error asli naik);
- matriks HEAD asing ditolak semua: merge, orphan, root commit, `.mailmap`;
- commit sendiri tetap diterima untuk spasi, CRLF, baris `#`, NBSP, dan pesan 5000 karakter;
- urutan lifespan asli: `drain_writes` lalu `aclose`;
- 5 test baru merah terhadap b733ecc;
- suite penuh: 1585 passed, 1 skipped.

| INFO | Keputusan |
|---|---|
| **I-8:** urutan `drain_writes` sebelum `aclose()` tidak dikunci test (mutan "drain setelah aclose" selamat) | **ditutup** (hanya test): `test_the_app_drains_writes_at_shutdown` mencatat urutan `["drain_writes", "aclose"]`; mutan itu kini merah |
| **I-6:** `_landed` tidak bisa membedakan commit asing dengan email, pesan, dan parent yang sama | tidak ditutup: butuh dua pelanggaran sekaligus (aturan satu penulis dan identitas+pesan identik). **Syarat masuk** WP pemanggil `merge_branch`/commit worktree pertama: tambahkan bukti keempat "index sama dengan tree commit" (`diff --cached --quiet <sha>`) bersama pemanggil itu |
| **I-7:** git membuang karakter "crud" di ujung email (`;`, `,`, `\`, kutip), sehingga commit sendiri yang mendarat ditolak → 409 tanpa audit | tidak ditutup: arahnya aman (bukan sukses palsu) dan butuh dua kondisi langka (email semacam itu dan git dihentikan tepat setelah ref ditulis). Usulan (Lead): perketat validasi email user (`services/users.py` hanya memeriksa `@`), atau bandingkan email lewat `git var GIT_AUTHOR_IDENT` |
| **I-9:** grace drain (30 s + 30 s) lebih panjang dari `stop_grace_period` default Docker (10 s); sinyal kedua melewati lifespan | risiko sisa, tidak ditutup di WP ini (infra). Usulan (Lead): `stop_grace_period` service `api` ≥ grace drain di `infra/compose.yml`; penutupan penuh butuh outbox audit |

Dibawa ke WP berikutnya (AG-13 I-b, syarat masuk): penghapusan `index.lock` dan reset index mengandaikan satu penulis per index. Pemanggil baru `GitAdapter.commit` di worktree task dan `merge_branch` di `repo/` (WP-3.x, merge queue) **wajib** memegang `Workspace(root).exclusive()`. I-e (I2, I5, I6) tetap di daftar "Tidak ditutup" di atas; I4 [Kemungkinan Besar] sudah dimitigasi desain sandbox (artefak di mount terpisah, `repo/` read-only), dikonfirmasi saat pembaca evidence dibuat.

### WP-2.1d (🔒 path/git) — indeks repository dan pencarian (FR-020, FR-021, FR-023; AC-03 bagian "mencari")

Spec: `docs/superpowers/specs/2026-11-04-wp-2.1d-index-search-design.md`. Plan: `docs/superpowers/plans/2026-11-04-wp-2.1d-index-search.md`. Konflik #69 (parameter tambahan, `sha256` null, `indexing` tidak dipakai). Hanya backend; layar pencarian IDE tetap WP-2.2.

| Pemeriksaan | Hasil aktual |
|---|---|
| Backend `ruff check .`, `ruff format --check .`, `pyright`, `lint-imports` | bersih; 0 error; 3 kontrak dipertahankan |
| Backend `pytest -q` (PostgreSQL 16 + Redis 7 lokal) | implementasi @ 7484e90: **1710 passed, 1 skipped** |
| Migrasi 0005 | `upgrade head → downgrade -1 → upgrade head` lulus (alembic API, DB uji); `compare_metadata` model ↔ skema: 0 perbedaan |
| Bukti T2 git grep | probe git 2.43: symlink file/folder ke luar repo (ter-track maupun untracked) tidak diikuti; `test_symlinks_out_of_the_repository_are_not_searched`, `test_git_dir_is_never_searched` |
| Pola simbol ERE ↔ klasifikasi Python | `test_symbol_patterns_find_every_language_through_git`: 32 contoh definisi di 7 bahasa ditemukan lewat `git grep -o` sungguhan dan diklasifikasikan sama |
| Mutan | 18 mutan pada guard (`-e`, filter policy, glob, escape LIKE, urutan simbol, filter kind, batas hasil, rate limit, kunci job, pelepasan kunci, reuse, shortcut unchanged, batas file, pemicu create/import, `index_soon`, bahasa di `classify`): semua merah. Mutan "tanpa filter glob" sempat selamat; test teks diperkuat (glob `*.md`) lalu merah |
| Syarat masuk AG-13 WP-2.1b/2.1c | tidak ada penulis `sync_state`/`error` baru (`test_index_never_writes_repository_state`); job indeks tidak menulis index git (grep pada object database, `GIT_OPTIONAL_LOCKS=0` untuk working tree) |

| CI @ 48c2ca6 dan sebelumnya | semua job hijau kecuali `security-scan`: Trivy memblokir `kodekantor/web:dev` karena `libexpat 2.9.0-r0 → 2.10.0-r0` (CVE-2026-113351, CVE-2026-117610, CVE-2026-118370). `main` juga merah karena sebab yang sama (run 46bf6cf dan run terjadwal 3 Nov); bukan dari PR ini. Usulan patch satu baris di `frontend/Dockerfile` ada di komentar PR #13. **Keputusan PO (4 Nov): patch masuk PR #13**, karena PR terpisah butuh branch kedua. `libexpat>=2.10.0-r0` dinaikkan di tahap runtime `web`, mengikuti pola brotli-libs WP-1.4a; EX-03 tetap tidak berlaku untuk `web`. hadolint v2.14.0 lokal: exit 0. Bukti bahwa paket terpasang dan Trivy lulus datang dari CI, karena proxy sesi menolak `dl-cdn.alpinelinux.org` |
| CI @ f25bb4e (hanya docs) | `sandbox-isolation` merah sekali: `test_memory_limit` → `('failed', 137, None)`, padahal hijau di 48c2ca6 dengan kode sandbox identik. Bukan dari PR ini; dugaan [Menebak]: `docker_driver.py` membaca `State.OOMKilled` sekali tepat setelah exit, sedangkan event OOM bisa datang belakangan. Usulan patch dan re-run sekali: komentar PR #13 |

#### Review WP-2.1d @ 7484e90: `kk-backend-reviewer` (**APPROVE**: 0 BLOCK, 5 SHOULD, 6 NIT), `kk-silent-failure-hunter` (2 HIGH, 4 MEDIUM, 4 LOW), AG-13 (**NOT SIGNED OFF**: 2 MEDIUM, 3 LOW, 4 INFO)

Penutupan di 84b5ff4 (gitops), 21bdf1a (job indeks), 48c2ca6 (pencarian). Lokal @ 48c2ca6: **1737 passed, 1 skipped**; ruff, pyright, lint-imports bersih; migrasi 0005 bolak-balik lulus, drift 0. 16 mutan pada guard baru semuanya merah; mutan drain L2 merah 2 dari 3 run (hang aslinya tidak deterministik, sekitar 1 dari 8 panggilan pada batas 4 MiB).

| Temuan | Penutupan | Bukti |
|---|---|---|
| AG-13 M1 (T4): regex ERE dari user tanpa batas memori, thread, konkurensi (`((a{1,100}){1,100}){1,100}` → 3 GB RSS; `(a*)\1*b` → 39 s CPU) | regex mode menolak backreference dan `{m,n}` (422 `regex_unsupported`); `--threads=1`; satu pencarian teks per user, dua per proses (429 + `Retry-After`) | `test_regex_features_that_explode_are_refused[*]`, `test_an_escaped_brace_is_a_plain_character`, `test_text_searches_run_one_at_a_time_per_user`; mutan → merah |
| AG-13 M2 (T4): parse + saring 4 MiB menahan event loop 0,6–1,0 s | parse grep di thread (adapter), penyaringan di thread (service), batas output 1 MiB | `test_a_large_search_output_never_blocks_the_loop` (ticker, batas output 8 MiB di test), `test_grep_parses_off_the_event_loop` (16 MiB); mutan parse → jeda 2,08 s, merah |
| AG-13 L1: test bukti T4 hampa | ditulis ulang: jeda antar-tick, jalur timeout lewat HTTP (409 + `Retry-After`), output besar | dua test di atas, `test_a_text_search_past_its_timeout_is_409_and_never_blocks_the_loop` |
| AG-13 L2: setelah output dipotong, `wait()` menunggu sampai timeout (transport stdout di-pause) | `_kill` menguras stdout sebelum `wait()` | `test_a_cut_output_returns_promptly_every_time` (16 panggilan, batas 4 MiB); probe 24 panggilan tanpa hang |
| AG-13 L3: cari simbol tanpa rate limit | 120/menit per user | `test_symbol_searches_are_rate_limited` |
| AG-13 I4: kredensial DB uji di plan | diganti rujukan ke CLAUDE.md | dokumen |
| silent H1: HEAD tidak terbaca → indeks dikosongkan, audit sukses | `head_commit` ketat (`show-ref --exists`); branch terindeks tanpa commit → error; indeks lama dan `last_indexed_at` utuh | `test_head_commit_is_none_only_for_an_unborn_branch`, `test_an_unreadable_head_keeps_the_index_and_is_an_error`, `test_an_unreadable_head_is_an_error_even_before_the_first_index`; mutan → merah |
| silent H2: indeks terpotong tampak lengkap dan tidak pernah dibangun ulang | `repositories.index_truncated`; tidak pernah `unchanged` di atas indeks parsial; `meta.index_truncated` | `test_a_truncated_index_is_flagged_and_rebuilt`, `test_symbol_search_says_when_the_index_is_partial`; 2 mutan → merah |
| silent M1: SET kunci yang balasannya hilang menggantung 30 menit | peminta melepas kunci miliknya pada setiap `BaseException` | `test_a_lock_set_whose_answer_was_lost_is_freed` |
| silent M2 + backend S3: `time_limit` keras tanpa audit, kunci hantu | tenggat 15 menit di dalam run (`asyncio.timeout`): audit `time_limit`, kunci dilepas | `test_a_job_past_its_deadline_is_audited_and_frees_the_lock` |
| silent M4: hit di path non-UTF-8 hilang tanpa tanda | `meta.skipped` | `test_text_search_reports_paths_it_could_not_name` |
| silent L3: `index_soon` tanpa jejak penyebab | `exc_info` untuk kegagalan non-`AppError`; kegagalan git `AppError` kini juga dicatat di log | review kode |
| backend S1: `-i` dan `.` tidak berlaku untuk non-ASCII (`LC_ALL=C`) | grep memakai `LC_ALL=C.UTF-8` | `test_grep_ignores_case_beyond_ascii`; mutan → merah |
| backend S2: 202 dengan `status: completed` | `status: "accepted"` | assert di `test_post_index_is_202_and_reuses_a_running_job` |
| backend S4: permintaan yang di-reuse tidak menjamin commit terbaru terindeks | putaran tambahan lewat Lua (`reuse`/`begin_round`/`finish`), maks 3 | `test_a_request_while_the_job_runs_gets_another_round`; mutan → merah |
| backend S5: alias tipe Go hilang | aturan Go menerima `type X =` | sampel `type Alias =` di unit dan test git |
| backend N1: `sha256` `String(64)` | `CHAR(64)` | drift 0 |
| backend N2: kolom byte dipakai sebagai indeks karakter | dikonversi di parser | `test_grep_column_counts_characters` |
| backend N3: TAB ditolak | pola `q` hanya menolak `\0 \n \r` | `test_a_tab_is_a_valid_query_character` |
| backend N4: dua statement tanpa snapshot; `truncated` palsu tepat di 5000 | `REPEATABLE READ`; ambil `SYMBOL_CANDIDATES + 1` | `test_symbol_truncated_only_when_more_exist` |
| backend N5 (sebagian): memori parse, konkurensi | `max_matches` (2 × `MAX_SYMBOLS`) untuk job indeks; konkurensi seperti AG-13 M1 | review kode |
| backend N6 (sebagian): test kegagalan di tengah swap dan timeout API | ditambah | `test_a_failure_inside_the_swap_keeps_the_old_index`, test timeout di atas |

Tidak ditutup (alasan):
- **AG-13 M1 butir 4 (batas memori proses git, prlimit/setrlimit):** saringan regex dan `--threads=1` menjadi batasnya. Usulan: `prlimit` dievaluasi bersama pencarian di worktree task (WP-3.1).
- **silent M3:** task Celery mengembalikan `"error"` sebagai sukses. Ini sama dengan job import; tidak ada result backend, dan PostgreSQL (audit) serta log error yang menjadi catatan.
- **silent L1:** "skipped" tanpa audit, karena `audit_events.result` hanya `success`/`denied`/`error`; log warning tetap ada.
- **silent L2, L4:** kegagalan audit error hanya di log (`logger.exception`); audit memuat nama kelas, penyebab lengkap ada di log; `job_id` belum menjadi correlation id.
- **backend N5:** create yang meminta indeks bisa tertahan sampai `SEND_TIMEOUT_S` (10 s) bila broker menggantung (publish ke broker yang mati gagal dalam ±2 s).
- **backend N6:** `regex`/`kind` diabaikan pada mode yang tidak relevan; `_replace` tidak memeriksa `project.status`; daftar test di TEST_STRATEGY (Lead).
- **AG-13 I3:** git yatim bila worker di-SIGKILL `time_limit`; tenggat 15 menit membuat jalur itu jarang.

**Syarat masuk WP berikutnya (AG-13, mengikat):**
- **WP-3.x (tool agent `repo.search_text`/`repo.search_symbol`, `AGENT_READ`):** `Budget` total per pencarian untuk `.agentignore` yang tidak tepercaya (200 pola `**/x/**/*.y` ≈ 70 s CPU untuk 226 ribu match); `AGENT_READ_LIMIT` berlaku untuk preview; preview dan nama simbol dibungkus sebagai untrusted dan diredaksi sebelum ke model (T1/T5).
- **WP-3.1 (pencarian di worktree task):** git 2.43 memeriksa folder induk lalu membuka file berdasarkan path (TOCTOU bila sandbox menulis worktree bersamaan): cari hanya saat sandbox task berhenti, atau jalankan pencarian di dalam sandbox.
- **WP-3.1 (AG-13 D1 dan D3, keputusan PO 4 Nov):**
  - Git pencarian mendapat batas memori keras, misalnya RLIMIT_AS sekitar 512 MiB lewat `prlimit`, dibuktikan di image backend. Alternatifnya, pencarian dijalankan di sandbox dengan limit memori sendiri.
  - Timeout mode regex ditinjau ulang, karena memori tumbuh sekitar 70 MB/s dan dua slot bisa dipegang satu pengguna.

#### Delta AG-13 @ 48c2ca6 (f25bb4e hanya docs): **SIGNED OFF 🔒** (0 CRITICAL/HIGH/MEDIUM; 2 LOW, 5 INFO baru)

AG-13 menyatakan M1, M2, L1, L2, L3, dan I4 tertutup, dengan bukti berikut:
- **M1, regex:** HTTP probe 422 untuk pola yang meledak dan untuk 6 upaya bypass lewat escape.
- **M1, CPU dalam kuota 1 CPU:** loop tetap 95/96 tick/s saat 1 git jalan (sebelumnya 26), dan 84 tick/s saat 2 git (p95 27 ms).
- **M2:** jeda loop 12–83 ms pada batas 1 MiB (sebelumnya 620–1001 ms).
- **L2:** 12/12 run `truncated`, tanpa satu pun tunggu ≥ 1 s.

Pemeriksaan AG-13 di DB `kodekantor_test_ag13`: **1737 passed, 1 skipped**. ruff, pyright, dan lint-imports bersih. Migrasi 0005 bolak-balik lulus. Syarat masuk WP-2.1b/2.1c tetap utuh.

Penutupan di bawah dilakukan di commit kode setelah f25bb4e. Lokal: lihat baris bukti di bawah tabel.

| Temuan | Penutupan | Bukti |
|---|---|---|
| D2 LOW (T10): permintaan yang masuk di putaran terakhir (`MAX_ROUNDS`) dijawab `reused`, tetapi tidak ada putaran yang melayaninya (`again` tertinggal, kunci dilepas) | bila `finish` meminta putaran lagi di `MAX_ROUNDS`: kunci dilepas lalu `index_soon` mengirim job baru; worker kini membawa `CeleryJobQueue` | `test_a_request_in_the_last_round_is_handed_to_a_new_job`; mutan tanpa hand-over → merah |
| D4 INFO: `--threads=1` tidak dikunci test | argv git yang sebenarnya diperiksa lewat HTTP, untuk mode teks dan regex | `test_a_text_search_runs_git_on_one_thread`; mutan `threads=None` → merah |

Tidak ditutup (alasan):
- **D1 LOW (T4), memori DFA glibc.** Regex tanpa `{` dan tanpa backreference masih bisa membuat cache state 0,4–0,73 GB per git dalam 10 s. Dua slot per proses bisa melewati limit api 1536M.
  - Menurut probe AG-13, OOM killer cgroup membunuh git (proses terbesar) dan api tetap hidup [Kemungkinan Besar untuk uvicorn nyata]. Jadi yang gagal adalah pencarian si peminta sendiri.
  - Batas keras (RLIMIT_AS sekitar 512 MiB lewat `prlimit` untuk git pencarian) mengubah cara adapter memulai proses dan harus dibuktikan di image. Ini dijadwalkan bersama syarat masuk WP-3.1.
  - Opsi termurah bila PO ingin ditutup sekarang: `MAX_TEXT_SEARCHES = 1`, tetapi itu memperparah D3.
  - **Keputusan PO (4 Nov): ditunda ke WP-3.1**, sesuai rekomendasi; menjadi syarat masuk WP-3.1 di bawah.
- **D3 INFO:** dua pembaca dengan pola 10 s dapat memegang kedua slot, sehingga user lain mendapat 429 untuk pencarian teks. Ini DoS tingkat fitur; tindak lanjutnya bersama D1, karena timeout regex yang lebih pendek meredakan keduanya.
- **D5 INFO:** test regresi L2 bersifat probabilistik (mutan merah 5/6 run), karena hang aslinya memang tidak deterministik.
- **D6 INFO:**
  - Tenggat yang jatuh tepat saat `commit` bisa menghasilkan audit success disusul audit error `time_limit`. Ini duplikat, bukan celah [Menebak, tidak direproduksi].
  - Drain di `_kill` tanpa batas waktu, tetapi hanya menunggu EOF seperti `wait()` sebelumnya.
  - I3 (git yatim) praktis tertutup, karena tenggat 15 menit lebih pendek dari `time_limit` 20 menit.
- **D7 INFO:** cancel melepas slot saat parse di thread masih berjalan. Jalur ini tidak terpicu oleh disconnect di uvicorn.
- **Catatan versi:** `show-ref --exists` butuh git ≥ 2.43; image memakai git Debian trixie [Kemungkinan Besar 2.47]. Pada git yang lebih lama, branch unborn gagal tertutup sebagai `head_unreadable`.

### WP-2.1e (🔒 scope lintas project) — dashboard (FR-007–011; AC-01, AC-16)

Spec: `docs/superpowers/specs/2026-11-04-wp-2.1e-dashboard-design.md`. Plan: `docs/superpowers/plans/2026-11-04-wp-2.1e-dashboard.md`. Konflik #70: `failed_queue`, `project_name`, `meta.unavailable`, `usage.period`, status approval dari `tasks.status`. Backend `GET /dashboard` dan layar `/` ada dalam satu PR (konflik #63).

| Pemeriksaan | Hasil aktual |
|---|---|
| Backend `ruff check .`, `ruff format --check .`, `pyright`, `lint-imports` | bersih; 0 error; 3 kontrak dipertahankan |
| Backend `pytest -q` (PostgreSQL 16 + Redis 7 lokal) @ 19b8e9e | **1753 passed, 1 skipped** (+14 test baru) |
| Frontend `format:check`, `lint`, `lint:guards`, `typecheck`, `npm test`, `vite build` @ 16dd334 | bersih; **333 passed**; build sukses |
| Mutan scope (AC-01) | buang syarat `deleted` dari `visible_projects_filter` → 2 test merah; buang syarat grant → 3 test merah |
| Mutan snapshot | buang `postgresql_readonly` → `test_the_dashboard_reads_in_a_read_only_snapshot` merah. Snapshot tidak bocor ke sesi berikutnya pada pool satu koneksi (`test_the_read_only_snapshot_stays_in_its_session`) |
| Live check (uvicorn + DB uji `kodekantor_test_rev`) | login 200 → create project 201 → task digerakkan ke `failed` lewat `transition` → `GET /dashboard` 200, `Cache-Control: no-store`, `metrics.failed_tasks = 1`, `failed_queue` = task itu dengan `status_reason "tests failed"`, `usage.cost_status = "unavailable"`, `meta = {unavailable: [agent_activity, usage], failed_queue_total: 1}`; tanpa sesi → 401 |
| Layar (Playwright, Vite + API lokal) | 1440 px dan 820 px sesuai mockup sejauh datanya ada. 390 px: dashboard tidak menambah scroll horizontal (32 px, sama dengan `/projects`; sumbernya shell WP-1.4a yang sidebar-nya selalu `w-60`) |

Temuan di luar cakupan: shell aplikasi (`components/layout/Sidebar.tsx`, `w-60` di semua lebar) membuat setiap layar hanya punya ±150 px di ponsel 390 px. Usulan: sidebar yang bisa dilipat di bawah breakpoint `md` (WP-1.4 lanjutan).

#### Review WP-2.1e @ 16dd334/b2a7e02

Hasil awal:
- `kk-backend-reviewer`: **APPROVE** (0 BLOCK, 2 SHOULD, 4 NIT).
- `kk-frontend-reviewer`: **APPROVE with changes** (0 BLOCK, 3 SHOULD, 4 NIT).
- `kk-silent-failure-hunter`: 2 HIGH, 4 MEDIUM, 6 LOW.
- AG-13: **SIGNED OFF 🔒 @ b2a7e02** (0 CRITICAL–LOW, 6 INFO). 11 mutan scope/snapshot yang valid semuanya merah; probe HTTP: `recent_projects` = `GET /projects?page_size=5`; probe XSS bersih; 200 ribu task → 8 statement, 121 ms.

| Temuan | Penutupan | Bukti |
|---|---|---|
| silent H1 + frontend SHOULD 1: angka usage bisa tampil tanpa label | biaya hanya tampil bersama statusnya (`estimasi`/`sebagian`/`aktual`) dan sebagai mata uang; token yang terhitung tampil sebagai angka; biaya yang statusnya `unavailable` selalu "tidak tersedia" | `shows recorded usage with how it was obtained…`, `never prints a cost whose status says it is unknown` |
| silent H2 + frontend SHOULD 2: `meta.unavailable` dibaca lalu diabaikan | aktivitas agent dan usage mengikuti `meta.unavailable`. `AgentRunSummary` divalidasi. Jawaban yang menyebut bagian tidak tersedia tetapi tetap mengirim datanya ditolak (`bad_response`) | `lists agent runs once they are recorded…`; `refuses an answer that contradicts itself` ×3; mutan `isUnavailable={true}` → merah |
| silent M1: project dengan repository rusak tampil hijau "Aktif" | badge status dari `project.status`; badge repository bermasalah atau sedang diimpor | `shows a project whose repository has a problem as such` |
| silent M2 + frontend NIT: filter mengklaim "tidak ada yang cocok" untuk antrean yang belum dimuat | label "…yang ditampilkan"; bila ada item yang belum dimuat, teks menyebut jumlah yang ditampilkan dan jumlah sisanya | `never says nothing matches about queue items it did not load` |
| silent M3: `waiting_approval` turunan dari status | label metrik "Task menunggu persetujuan"; tetap dicatat di #70 sampai tabel approval ada (WP-3.1) | teks UI |
| silent M4 + frontend NIT: undangan project pertama menyembunyikan antrean | undangan hanya bila semua metrik 0, antrean kosong, dan tidak ada daftar | `keeps the attention queue when the only project is archived` |
| silent L1: `active or 0` | `scalar_one()` | review kode |
| backend SHOULD 1: `recent_projects` dari sesi lain | dibaca di snapshot yang sama; pemeriksaan disk (`project_views`, diekstrak dari `list_projects`) dijalankan sesudahnya | `test_counts_and_lists_come_from_one_snapshot` (project diarsipkan dan task ditambah di tengah baca → tetap konsisten); `test_recent_projects_are_the_first_page_of_the_project_list` |
| backend SHOULD 2: test snapshot hanya dua query, tanpa jalur error | test perilaku di atas, plus `test_a_failed_read_leaves_the_pooled_connection_clean` | mutan `READ COMMITTED` → 2 test merah |
| backend NIT 1: tie `updated_at` jatuh ke UUID acak | urut `updated_at desc, created_at desc, id` | `test_ties_in_updated_at_order_by_creation` |
| frontend SHOULD 3: assertion lemah | nilai metrik berbeda per kartu, dicek persis per label; 403 tanpa tombol ulang; filter tanpa beda huruf besar/kecil dan lewat slug | `DashboardPage.test.tsx` (17 test) |
| AG-13 I1: dashboard mengandaikan setiap role punya `task:read` | test invarian yang merah bila ada role tanpa `task:read` | `test_every_role_that_sees_a_project_may_read_its_tasks` |
| AG-13 I6: probe konten berbahaya belum menjadi test | menjadi test | `renders titles, names and reasons as text, never as markup` |

Tidak ditutup (alasan):
- **backend NIT 2** (route mengimpor `_respond`, `EnvDep`, `ProjectOut` dari `routes/projects.py`): pola yang sama sudah dipakai `routes/repository.py`. Memindahkannya ke modul bersama lebih baik dikerjakan sekali untuk semua route.
- **backend NIT 3–4, silent L2:** antrean berisi `waiting_approval` sesuai FR-010. `current_period` memakai jam saat itu; selisihnya hanya di detik pergantian bulan.
- **silent L3:** `agent_activity` tetap berupa daftar sesuai kontrak; layar dan klien memakai `meta.unavailable`.
- **silent L5** (tanpa `statement_timeout` khusus): error tetap diteruskan dan layar menampilkan state error. Batas waktu ikut konfigurasi DB.
- **silent L6** (status yang tidak dikenal menggagalkan dashboard): sengaja ketat; status di luar kontrak adalah bug yang harus terlihat.
- **AG-13 I3:** belum ada index `tasks(project_id, updated_at)`; dipertimbangkan saat volume task naik (WP-3.1).
- **AG-13 I4, I5:** pemiliknya Lead (bukti T6 di SECURITY_MODEL/TEST_STRATEGY, batas org untuk tier L).

**Syarat masuk WP berikutnya (AG-13, mengikat):**
- **WP-3.1 dan setiap perubahan RBAC (I1):** role tanpa `task:read` harus disertai filter izin per project di dashboard. Test invarian di atas memaksa keputusan itu.
- **WP-3.1 (I2):** putuskan apakah `tasks.title` diredaksi saat ditulis. `status_reason` sudah diredaksi, `title` belum; judul buatan agent bisa memuat secret.

**Delta AG-13 @ e109e6e (kode dcfd06d): SIGNED OFF 🔒** (0 CRITICAL–LOW, 1 INFO baru).
- **Probe P4 (HTTP):** project terbaru di dashboard sama persis (JSON) dengan `GET /projects?page_size=5` untuk owner, user ber-grant, dan role global. Kasus yang dicoba: project deleted/archived/`deleting_pending_approval` yang di-grant, project aktif tanpa grant, tie `updated_at`, dan project ke-6 yang terpotong limit.
- **Mutan D1–D8:** semuanya merah, termasuk kebocoran pool yang hanya terjadi di jalur gagal (`test_a_failed_read_leaves_the_pooled_connection_clean`) dan pembacaan di luar snapshot (`test_counts_and_lists_come_from_one_snapshot`).
- **Biaya:** 7 statement per dashboard (sebelumnya 8), 141 ms pada 200 ribu task.
- **CI security-scan @ afbcfbc:** gitleaks, pip-audit, npm audit, Semgrep, dan Trivy semuanya lulus.

- **DI1 INFO (syarat masuk, mengikat):** `project_views` (publik, `services/projects.py`) membangun view tanpa menyaring visibilitas. Setiap pemanggil baru wajib membaca barisnya di bawah `visible_projects_filter`. Kedua pemanggil sekarang sudah begitu [Pasti]. Pertahanan berlapis (membuang baris dengan izin kosong) ditunda ke perubahan berikutnya di file itu, karena perubahan apa pun di sana memerlukan delta review lagi.

### WP-2.2a — layar IDE: explorer, editor multi-tab, simpan dengan cek versi, panel cari (FR-026, FR-027, FR-029, FR-030, FR-032, FR-020/021 layar; AC-03)

Spec: `docs/superpowers/specs/2026-11-04-wp-2.2-ide-design.md`. Plan: `docs/superpowers/plans/2026-11-04-wp-2.2a-ide-editor.md`. Konflik #71: chat pindah ke WP-3.2, terminal ke WP-4.1, simpan eksplisit (bukan autosave). WP ini tidak bertanda 🔒 (tidak ada perubahan backend); isi repo diperlakukan sebagai data tidak tepercaya.

| Pemeriksaan | Hasil aktual |
|---|---|
| Frontend `format:check`, `lint`, `lint:guards`, `typecheck` @ da30a94 | bersih |
| Frontend `npm test` @ da30a94 | **400 passed** (29 file; +55 test baru: klien workspace 7, reducer tab 12, bahasa Monaco 6, `CodeEditor` dengan Monaco palsu 3, editor gagal dimuat 1, layar workspace 26; route map diperbarui untuk layar workspace yang nyata) |
| `npx vite build` @ da30a94 | sukses. `WorkspacePage` 38,1 kB (gzip 10,8 kB); Monaco di chunk sendiri (`CodeEditor` 1,25 MB, `editor.api` 2,7 MB); 5 worker sebagai file terpisah (`editor`, `css`, `html`, `json`, `ts`). Chunk `index` 718,7 kB vs 709,1 kB di `main` (0 referensi Monaco) |
| `npm audit --omit=dev --audit-level=high` | 0 vulnerabilities |
| Live check @ ff29eca dan diulang @ da30a94 (build produksi lewat `vite preview` + uvicorn + DB uji `kodekantor_test_rev`; CSP Caddyfile disuntikkan ke dokumen) | buat file → ketik di Monaco → Ctrl+S → isi server sama; token sintaks tampil; 0 link aktif (`links: false`). Tab B menyimpan lebih dulu, tab A menyimpan → dialog konflik, server tetap berisi versi B. Diff Monaco tampil di dialog. "Timpa" memakai sha yang baru dibaca → server berisi versi A. Cari teks → hit → editor ke baris 2. Worker `editor.worker` berjalan dari origin sendiri. Console: hanya 409 dari konflik (sengaja); 0 pelanggaran CSP |
| Lebar 390 px | sebelum perbaikan scroll horizontal 426 px; setelah panel ditumpuk di bawah `lg`: 32 px (46 px dengan editor terbuka). Sisanya dari shell (sidebar `w-60` tetap, konten ±150 px), temuan WP-2.1e yang sama |

Temuan live check yang ditutup di WP ini:
- **Import worker Monaco tidak ter-resolve** (`monaco-editor/esm/vs/...?worker`; `exports` paket memetakan `./*` ke `./esm/vs/*.js`). `typecheck` tidak menangkapnya karena `vite/client` mendeklarasikan `*?worker`. Sekarang memakai subpath yang diekspor (`monaco-editor/editor/editor.worker`, `monaco-editor/language/<x>/<x>.worker`); `vite build` membuktikannya.
- **Monaco ada di chunk route**, sehingga navigasi ke workspace menunggu beberapa MB. Editor dan diff sekarang `React.lazy` (`lazyEditor.tsx`): explorer, tab, dan panel cari tampil lebih dulu.
- **Ctrl+S tepat sesudah ketikan mengirim teks sebelum ketikan itu.** Handler membaca tab dari ref yang baru diperbarui sesudah render. Ref sekarang menjalankan reducer murni yang sama saat dispatch. Test regresi `saves the text on screen when Ctrl+S lands before the last keystroke has rendered` merah sebelum perbaikan (`x = 1\n` terkirim, seharusnya `x = 1\n!`).
- **Ukuran di explorer basi sesudah simpan** (file baru tetap "0 byte"). Simpan sekarang membaca ulang folder induk; test memeriksa folder dibaca dua kali.
- **Pelanggaran CSP `script-src eval` di setiap pemuatan halaman**, sudah ada sejak WP-1.4a: Zod v4 mencoba `new Function` saat schema dibuat. `z.config({ jitless: true })` di `src/lib/zodConfig.ts` diimpor pertama oleh `main.tsx`. Parsing tidak berubah karena Zod memang jatuh ke jalur tanpa JIT.
- **Peringatan React Router `HydrateFallback`** saat halaman dibuka langsung di URL workspace: route lazy diberi `hydrateFallbackElement` (state loading).

Pengecualian dependensi: `monaco-editor` 0.57.0 mem-pin `dompurify` 3.4.15 yang punya advisory critical (GHSA-p98j-92pf-mc4p dan empat lainnya). `overrides` npm mem-pin `dompurify` 3.4.17. Versi ini dirilis 12 Okt 2026: lewat tanggal `exclude-newer` 2026-10-10, tetapi umurnya sudah lebih dari dua minggu. Preseden: pengecualian umur per paket certifi (backend) dan EX-01. Override dicabut saat Monaco versi berikutnya mem-pin `dompurify` ≥ 3.4.17.

#### Review WP-2.2a @ ff29eca

Hasil awal:
- `kk-frontend-reviewer`: 0 BLOCK, 10 SHOULD, 6 NIT. Verdict: tahan merge sampai S1, S2, S4, S5, dan S7 ditutup.
- `kk-silent-failure-hunter`: 3 HIGH, 8 MEDIUM, 8 LOW.

Penutupan dibagi dalam dua commit:
- c74f1bd: simpan, konflik, siklus hidup halaman, Monaco.
- da30a94: explorer dan pencarian.

Setiap perbaikan yang punya test regresi dibuktikan dengan mutan: perbaikannya dibalik, lalu test itu dipastikan merah. Total 11 mutan, semuanya merah.
- M1–M5: tulis ganda, error refresh, `key`, folder leluhur, versi server non-teks.
- S4: model ditimpa `value`.
- C1–C5: folder kosong vs terpotong, penanda Git basi, "tidak ada hasil", kirim ulang pencarian, sebab error folder.

| Temuan | Penutupan | Bukti |
|---|---|---|
| frontend S1 + silent M1: "Timpa" bisa dua kali (PUT kedua konflik dengan PUT pertama), "Muat versi server" saat simpan masih berjalan | `write` menolak tab yang sedang `saving`; tombol dialog nonaktif selama simpan | `writes once when Overwrite is pressed twice…` (2 PUT, bukan 3) |
| frontend S2: gagal timpa tersembunyi di balik modal | dialog menampilkan alasan gagal dan ID permintaan | test yang sama |
| silent H2: versi server biner/terlalu besar tampil sebagai teks kosong, bisa dimuat ke editor lalu ditimpa | state `notText`: tidak ada diff dan tidak ada muat ulang; "Timpa" tetap tersedia dengan penjelasan | `never offers to load a server version that is not text…` |
| silent M2: semua gagal simpan menjadi "gagal" generik | `SaveError {code, requestId}`: `unknown_outcome` (jawaban tidak sampai, mungkin tersimpan), alasan repository (`project_archived` …), `forbidden`, `not_found`, `rate_limited` | `says when a save may have been written, and why the server refused one` |
| frontend S4 + silent M8: efek sinkron menimpa ketikan yang belum dirender | `setValue` hanya untuk versi baru (`openId:revision`, naik saat buka/muat ulang); di antaranya Monaco sumber kebenaran | `CodeEditor.test.tsx` (Monaco palsu): ketikan "abc" tetap saat render membawa "ab" |
| frontend S5: jawaban simpan yang terlambat menimpa tab yang dibuka ulang | setiap jawaban membawa `openId`; jawaban untuk pembukaan lama diabaikan | `ignores a late answer when the tab was closed and the file opened again` |
| frontend S7 + silent M7: tab project A terbawa ke project B | `<Workspace key={projectId}>` | `starts empty in another project's workspace…` (project B sudah di cache) |
| silent H1: refresh project gagal melepas workspace dan teks yang belum disimpan | halaman error hanya bila belum ada data; setelah itu banner + coba lagi. Repository yang berhenti siap saat tab terbuka: banner, dan simpan dinonaktifkan | `keeps the open tabs and their unsaved text when the project cannot be read again` |
| silent H3: chunk Monaco gagal → halaman error route | loader sendiri + error boundary lokal: alert, isi file tetap terbaca, tombol muat ulang halaman. Bagian "teks hilang" tidak dapat terjadi: edit butuh editor yang sudah dimuat, dan modulnya lalu di-cache untuk semua editor dan diff [Kemungkinan Besar] | `lazyEditor.test.tsx` |
| frontend S3 + silent L6: `revealLine` ikut pindah tab; hit yang sama tidak bisa diklik ulang | permintaan `{path, line, nonce}`, diterapkan sekali dan hanya di file-nya; buka dari explorer menghapus permintaan lama | `CodeEditor.test.tsx` |
| frontend S6 + silent M5: folder baru di path bersarang tidak muncul | invalidasi semua folder leluhur | `reads every folder above a new path again…` |
| frontend S8 + silent M4/M6/L5: error explorer, buka file, dan baca versi server generik tanpa ulang | `workspaceErrorText`: alasan repository atau state layar (izin ditolak, tidak ditemukan …), ID permintaan, tombol coba lagi. Refresh gagal mempertahankan entri yang sudah terbaca. Penanda Git disembunyikan bila status tidak bisa dibaca ulang | `says why the tree could not be read…`, `drops Git markers it can no longer confirm`, `says why a file could not be opened` |
| silent M3: "kosong"/"tidak ada hasil" untuk jawaban terpotong | hanya untuk jawaban utuh (tidak terpotong, tanpa file dilewati, indeks lengkap, mode simbol dengan indeks) | `never calls a cut-off folder with no entries empty`, `says no results only for a whole answer…` |
| frontend N3 + silent L6: kirim ulang pencarian sama memakai cache; kueri kosong diam | percobaan masuk kunci query; tombol cari nonaktif tanpa teks | test pencarian yang sama |
| frontend S9: model bocor saat keluar workspace; akhir baris campuran berubah diam-diam | `disposeProjectModels` saat unmount; tab memberi catatan bila CRLF dan LF bercampur | `CodeEditor.test.tsx`, test reducer `notes a file whose line endings are mixed…` |
| frontend S10: `role=tab` di dalam `div` bersama tombol tutup | file terbuka menjadi daftar tombol dengan `aria-current`; tab panel kanan memakai `aria-controls`, panel berlabel, dan tombol panah | test layar memakai peran baru |
| frontend N2 (bagian sha basi) + N5 + silent L1/L2: hapus/buat tanpa sebab yang jelas | hapus: peringatan perubahan yang belum disimpan, `VERSION_CONFLICT` → "file berubah di server". Buat: 409 tanpa alasan → "sudah ada, atau permintaan yang sama masih diproses" (backend `AlreadyExists` tidak punya `reason`). Keduanya menyebut hasil tidak pasti bila tidak ada jawaban, dengan ID permintaan; explorer dibaca ulang juga sesudah gagal | `warns that deleting a file drops its unsaved text…` |
| frontend N4: tidak ada test untuk edit saat `saving`/`conflict` | test reducer | `keeps the status while saving or in conflict…` |
| silent L7: sha di jawaban PUT tidak divalidasi seketat GET | satu `Sha256Schema` untuk GET, PUT, dan detail 409 | review kode |

Tidak ditutup (alasan):
- **frontend N1** (Ctrl+S saat simpan berjalan diabaikan): status tetap jujur. Sesudah jawaban datang, tab menunjukkan "Belum disimpan" bila ada ketikan baru. Antrean simpan menambah state tanpa mencegah klaim palsu.
- **frontend N2** (hapus file yang tidak dibuka memakai sha yang baru dibaca): pengguna mengonfirmasi lewat nama dan tidak pernah melihat isinya. Sha hanya menjaga jeda antara baca dan hapus. File yang terbuka memakai sha tab.
- **frontend N3** (kunci hit `path:line` bisa bentrok): tidak terjadi. `git grep -n` memberi satu record per baris (kolom = kecocokan pertama, `adapter.py`); hit simbol membawa nama simbol.
- **frontend N5** (fallback hanya di muat pertama): pada navigasi di dalam aplikasi React Router menahan layar sebelumnya sampai chunk 38 kB selesai dimuat.
- **frontend S10** (explorer tanpa `role=tree`): daftar bersarang dengan tombol `aria-expanded` adalah pola disclosure yang valid. `role=tree` mewajibkan model keyboard penuh dan tidak dikerjakan setengah jalan.
- **silent L3** (`write` diam bila tidak boleh menulis): tombol Simpan dan Ulang tidak tampil tanpa izin. Editor hanya-baca memberi catatan. Status project atau repository yang berubah tampil di banner.
- **silent L4** (buang perubahan saat simpan berjalan): yang sedang dikirim adalah teks yang diminta disimpan. Yang dibuang hanya ketikan sesudahnya, dan dialog mengatakan begitu.
- **silent L8** (`disposeModel` no-op sebelum Monaco dimuat): benar menurut invarian. Invarian ditulis di `lazyEditor.tsx`.

Usulan untuk backend (pemilik WP-2.1c, belum dikerjakan): `AlreadyExists` (`POST /files` untuk path yang sudah ada) adalah 409 `CONFLICT` tanpa `details.reason`. Bentuknya sama dengan 409 dari idempotency store ("permintaan dengan key ini masih berjalan"), sehingga UI hanya bisa menyebut keduanya. `details: [{reason: "path_exists"}]` akan membuatnya tegas tanpa mengubah status code.


### WP-2.2b — panel git di workspace: perubahan, diff, commit, branch, log (FR-113, FR-115 bagian layar; FR-118; AC-09 bagian diff)

Spec: `docs/superpowers/specs/2026-11-05-wp-2.2b-git-panel-design.md`. Plan: `docs/superpowers/plans/2026-11-05-wp-2.2b-git-panel.md`. Hanya frontend; API dari WP-2.1c. Tidak bertanda 🔒.

Keputusan Lead:
- Kolom kiri berganti antara Explorer dan Git. Kedua tampilan tetap ter-mount (`hidden`). Branch dan log baru dibaca setelah tampilan Git pertama kali dibuka.
- Diff ditampilkan di dialog lebar sebagai baris unified dengan nomor baris. Monaco tidak dipakai karena API memberi hunk, bukan isi file utuh.
- Commit selalu dengan `paths` eksplisit, tidak pernah `null`. File tercentang saat pertama kali muncul di daftar; pilihan user dipertahankan selama file itu tetap terdaftar.
- Satu `Idempotency-Key` per isi permintaan. Key yang sama dipakai hanya untuk mengulang permintaan yang hasilnya tidak pasti.
- Buat branch hanya membuat ref, tanpa checkout (FR-118). Stage/unstage, discard, dan checkout menunggu v2.1 M26.

| Pemeriksaan | Hasil aktual |
|---|---|
| Frontend `format:check`, `lint`, `lint:guards`, `typecheck` @ c5daed0 | bersih |
| Frontend `npm test` @ c5daed0 | **442 passed** (33 file; +42 test baru: klien git 6, pilihan commit 6, tampilan diff 7, panel git 21, toggle workspace 2) |
| `npx vite build` @ c5daed0 | sukses; `WorkspacePage` 61,6 kB (gzip 15,8 kB); Monaco tetap di chunk sendiri |
| `npm audit --omit=dev --audit-level=high` | 0 vulnerabilities |
| Mutan K1–K8 | semuanya merah: commit semua file terdaftar, key baru setiap kali, tanpa cek nama cadangan, tampilan tersembunyi tidak di-mount, tab belum tersimpan tidak diteruskan, diff terpotong disebut kosong, log tanpa batas, `nothing_to_commit` sebagai gagal |
| Live check @ ec63f8e, diulang @ c5daed0 (build produksi lewat `vite preview` + uvicorn + DB uji `kodekantor_test_rev`; CSP Caddyfile disuntikkan ke dokumen) | README.md diedit lalu disimpan; file baru dibuat. Tombol Git: "Git, 9 perubahan". Diff README.md di dialog memuat baris baru. Commit 1 dari 9 file → "Commit aab37b0 dibuat." Log (UI dan API) memuat commit itu. README.md keluar dari daftar perubahan; file baru tetap belum di-commit. Branch `live-…` dibuat di aab37b0 dan working tree tetap di `main`. `kk/curang` ditolak di UI tanpa request. Console: 0 masalah, 0 pelanggaran CSP |

CI `security-scan` merah sejak b22f1c5, commit yang hanya berisi docs. Penyebabnya advisory baru, bukan diff WP ini: CVE-2026-109518 pada `apt`/`libapt-pkg7.0` 3.0.3 di image Debian `backend`, `sandbox-manager`, `sandbox-node`, dan `sandbox-python`. Perbaikannya 3.0.4 dan sudah ada di arsip. `main` memakai image yang sama.

Perbaikan dimasukkan ke PR ini (3d448e9): setiap image meng-upgrade kedua paket di langkah `RUN` yang sudah ada, tanpa pin revisi (DL3008), seperti `git` di image backend. Image tidak dapat dibangun di sini karena proxy memblokir arsip Debian, jadi CI yang membuktikannya. Komentar di PR #16 mencatat hal yang sama.

#### Review WP-2.2b @ ec63f8e

Hasil awal:
- `kk-frontend-reviewer`: WARN (0 BLOCK, 3 SHOULD, 6 NIT).
- `kk-silent-failure-hunter`: 0 HIGH, 4 MEDIUM, 2 LOW-MEDIUM, 6 LOW.

Penutupan di c5daed0. Setiap perbaikan dibuktikan dengan mutan: perbaikannya dibalik, lalu test-nya dipastikan merah. Mutan R1–R11 semuanya merah. R9 sempat lolos karena test-nya memakai QueryClient baru; test itu ditulis ulang dengan satu client, lalu merah.

| Temuan | Penutupan | Bukti |
|---|---|---|
| frontend S1 + silent SF2: hasil commit hilang bila commit itu mengambil perubahan terakhir (form unmount saat `clean`) | form commit tetap ter-mount; saat bersih hanya hasilnya yang tampil | `keeps the commit's answer when the commit took the last change` (R1) |
| frontend S2 + silent SF1/SF7: key yang disimpan setelah hasil tidak pasti bisa memutar ulang commit lama ("Commit <sha lama> dibuat" untuk edit baru) | key hanya dipakai ulang untuk isi yang sama terhadap HEAD dan daftar perubahan yang sama (branch: daftar branch yang sama). Teks hasil tidak pasti menyebut syarat itu | `never reuses a kept key once the repository moved on` (R2), `retries an unknown commit with the same key…` |
| silent SF3: file yang muncul saat user melihat daftar langsung tercentang, sehingga bisa ikut commit tanpa terlihat | file yang muncul saat tampilan terlihat tidak tercentang dan disebut namanya. Yang muncul saat tersembunyi tercentang. Keputusan Lead 5 di spec direvisi | test pilihan commit (6); `commits only the files that are listed and ticked` (R3) |
| frontend S3: lebih dari 500 path → error generik | tombol nonaktif disertai teks batas; 422 dan 400 `BAD_REQUEST` pada commit punya teks sendiri dengan ID permintaan | `refuses more files than one commit may take…` (R4), `says the server refused the commit's input…` (R5) |
| silent SF6 + SF9: setiap 422 branch disebut "nama ditolak"; teks berhasil dengan branch aktif kosong | penolakan dibedakan: nama (400 `branch`, 422 pada name) atau titik awal (400 `ref`, 409 `unknown_ref`, 422 pada from). Nama di atas 255 karakter ditolak tanpa request. Teks tanpa branch aktif bila branch-nya tidak diketahui | `tells a refused starting point from a refused name` (R6), `does not name a current branch it does not know` (R7) |
| silent SF4 + SF5: error status tidak terlihat di tombol Git; data basi tampil sebagai terkini; status tidak dibaca ulang saat tampilan dibuka | tombol Git: "Git, status tidak dapat dibaca" + ikon. Branch, log, dan diff menyebut "mungkin sudah tidak terbaru". Membuka tampilan Git membaca ulang status | `keeps the explorer when git status cannot be read…` (R11), `reads the status again each time the view is opened` (R9) |
| silent SF8: "muat lebih banyak" gagal menghapus log; tombol hilang saat memuat | daftar pendek yang sudah terbaca tetap tampil bersama teks basi; "Memuat commit lain…" tampil selama memuat | `keeps the commits it read when loading more fails…` (R8) |
| silent SF12: file tanpa hunk tampil tanpa keterangan | "Tidak ada perubahan isi yang dapat ditampilkan" | `says when a file changed without content to show` (R10) |
| frontend NIT: header kolom isi memakai judul dialog; kunci hunk bisa bentrok | kunci `diff.content`; kunci hunk `${index}:${header}` | review kode |

Tidak ditutup (alasan):
- **silent SF10** (gagal mengambil token CSRF dianggap "hasil tidak pasti"): terlalu hati-hati, tidak menyesatkan ke arah berbahaya. Mengulang memakai key yang sama dan aman.
- **silent SF11** (`void handleSubmit`): `catch` hanya memanggil fungsi teks murni. Bila fungsi itu melempar error, `finally` tetap mengembalikan tombol.
- **silent SF8 bagian "tepat 20/50/100/200 commit"**: API tidak memberi total, jadi daftar yang pas di batas tidak bisa dibedakan dari daftar yang terpotong. Teks batas hanya muncul di 200.
- **frontend NIT** (`step` log tidak di-reset, label tombol Git, `from` basi, deskripsi dialog): perilakunya benar dan sudah diberi komentar. Daftar branch dibaca ulang setiap kali branch dibuat. Live check: 0 peringatan Radix.
- **Live check, kosmetik:** tombol "Pilih semua/Kosongkan" terbungkus ke baris baru di kolom 16 rem. Fungsinya tidak terganggu.


### Bukti gate G2 — Exit Phase 2 (keputusan PO)

Kriteria WBS: "User dapat membuka project dan view/edit file dengan uji otorisasi". Bukti yang diminta: "Test cross-project + demo". Bagian ini menyiapkan bukti. **Keputusan G2 ada di tangan PO** (CLAUDE.md: agent menyiapkan bukti dan berhenti di gate). Mandat PO "jalankan P2, P3 dan P4" mengizinkan P3 dimulai sebelum keputusan ini.

Isi Phase 2 yang sudah di `main` (d3f4440):

| WP | PR | Catatan |
|---|---|---|
| WP-2.1a projects core | #10 | otorisasi berscope project |
| WP-2.1b import arsip 🔒 | #11 | AG-13 signed off (tracker WP-2.1b) |
| WP-2.1c API workspace dan git 🔒 | #12 | AG-13 signed off (tracker WP-2.1c) |
| WP-2.1d indeks dan pencarian 🔒 | #13 | AG-13 signed off + delta |
| WP-2.1e dashboard 🔒 | #14 | AG-13 signed off @ b2a7e02 + delta e109e6e |
| WP-2.2a layar IDE (explorer, editor, simpan dengan cek versi, cari) | #15 | review frontend + silent-failure ditutup |
| WP-2.2b panel git | #16 | review frontend + silent-failure ditutup |

| Pemeriksaan | Hasil aktual |
|---|---|
| Test cross-project (`backend/tests/security/test_cross_project.py`) | **117 test**, matriks setiap route `/projects/{id}`: user lain → 404, reader berscope bisa membaca tetapi tidak menulis, project terhapus → 404 bahkan untuk owner, tanpa sesi → 401, write tanpa CSRF ditolak, role global dihitung hanya di project yang di-grant. Bersama test scope/path/input terkait (dashboard, path workspace, input git, input search): **159 test**. Seluruh `tests/security`: **322 test** |
| Backend `pytest -q` @ d3f4440 (PostgreSQL 16 + Redis 7 lokal) | sedang dijalankan; hasilnya ditulis di commit berikutnya |
| Frontend `npm test` @ d3f4440 | **442 passed** (33 file); layar P2 (`features/workspace`, `features/projects`, route map): 160 |
| CI `main` @ d3f4440 (merge PR #16) | 9/9 hijau pada head PR yang sama (4bdab34), termasuk `security-scan` |

Demo otorisasi (HTTP nyata ke uvicorn + DB uji `kodekantor_test_rev`; cookie sesi, CSRF, `Idempotency-Key`). Tiga pengguna dipakai: owner project, `intruder` tanpa grant, dan `reader` dengan role `readonly` berscope project. Keduanya dibuat lewat jalur yang sama dengan helper test.

| Pengguna | `GET /projects` memuat project | `GET /projects/{id}` | `GET /tree` | `GET /files/README.md` | `PUT /files/README.md` | `GET /git/status` | `POST /git/commit` |
|---|---|---|---|---|---|---|---|
| owner | ya | 200 | 200 | 200 | 200 | 200 | 201 |
| intruder (tanpa grant) | tidak | 404 | 404 | 404 | 404 | 404 | 404 |
| reader (readonly di project) | ya | 200 | 200 | 200 | 403 FORBIDDEN | 200 | 403 FORBIDDEN |
| tanpa sesi | – | 401 | – | 401 | – | – | – |

Sesudah demo, isi README.md hanya memuat edit owner. Edit intruder dan reader tidak ada, dan commit terbaru (8adb929) adalah milik owner.

Demo layar (Playwright atas build produksi lewat `vite preview`, CSP Caddyfile disuntikkan; tracker WP-2.2a dan WP-2.2b):
- buka project → explorer → buka file di Monaco → edit → Ctrl+S → isi server sama;
- dua tab menyimpan berurutan → dialog konflik, tidak menimpa diam-diam → diff → timpa dengan sha yang baru dibaca;
- cari → hit membuka file di barisnya;
- panel git: diff, commit 1 file dari daftar, log, buat branch;
- console tanpa pelanggaran CSP.

Hal terbuka yang relevan untuk keputusan G2:
- G0 dan G1 belum diputuskan PO. Bukti G1 (WP-1.6) belum disiapkan.
- U4: sign-off formal AG-13 untuk WP-1.2 dan WP-1.3 dari sesi terpisah masih utang.
- Shell aplikasi belum responsif di ponsel (sidebar selalu `w-60`; tracker WP-2.1e).
- Usulan backend `details.reason` untuk "path sudah ada" (tracker WP-2.2a).
- Konflik #71: WP-SEC-01 bergantung pada WP-INT-03/05, yang berada di luar mandat. Perlu keputusan sebelum P4.

**Status G2: bukti siap, menunggu keputusan PO.**
