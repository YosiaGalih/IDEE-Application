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
