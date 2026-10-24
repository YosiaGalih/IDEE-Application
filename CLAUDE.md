# KodeKantor: aturan kerja agent

## Status repo (baca dulu)

- Repo ini **belum berisi kode produk**. Isinya baru dokumen rancangan (PRD v2.2 dan turunannya) serta konfigurasi pengembangan.
- Dokumen merujuk "kode snapshot 21 Okt 2026" (backend 1316 test, frontend, sandbox_manager, infra), tetapi **snapshot itu tidak ada di repo**. Jangan menganggap file seperti `workflow/states.py` atau `scripts/validate_registry.py` sudah ada.
- Seluruh ADR v2+ berstatus *Proposed*. Gate G0 belum diputuskan PO.
- Kode produk baru boleh ditulis setelah WP-nya punya spec dan plan yang disetujui (alur di bawah) dan PO membuka fasenya.
- Rancangan sistem pengembangan: `docs/superpowers/specs/2026-10-24-development-system-design.md`.

## Sumber kebenaran

Urutan bila ada konflik (makin atas makin kuat):

1. **KONTRAK**: K1–K4 di `docs/PLATFORM_EXPANSION.md` §9b, K5–K13 di `docs/SDLC_SCALE_DESIGN.md` §6, K14–K21 di `docs/POLYGLOT_INTEGRATION_DESIGN.md` §7.
2. `docs/PRD_v2.2.md`. Untuk bagian yang disebut "tetap/tidak berubah", acuannya `docs/PRD.md` (v1): alur v1, NFR-01..12, AC-01..20, T1–T10, quality gate v1.
3. Kontrak teknis: `docs/ARCHITECTURE.md`, `API_CONTRACTS.md`, `DATA_MODEL.md`, `SECURITY_MODEL.md`, `TOOL_CATALOG.md`, `CONFIGURATION.md`, `TEST_STRATEGY.md`, `IMPLEMENTATION_DESIGN_V2.md`.
4. `docs/DECISIONS.md` (ADR-001..068).
5. Workbook WBS `docs/wbs/*.xlsx` untuk urutan, pemilik, dan gate.

Konflik antar-dokumen **dilaporkan**, bukan dipilih diam-diam. Daftar konflik yang sudah diketahui ada di spec di atas, §8.

Bahasa: dokumen dan komunikasi dalam Bahasa Indonesia; identifier kode dalam bahasa Inggris. Pakai label keyakinan `[Pasti]` / `[Kemungkinan Besar]` / `[Menebak]` untuk klaim penting.

## Prinsip produk (dirujuk dokumen sebagai "CLAUDE.md #n")

1. **Evidence over claims.** Status `passed/tested/completed` hanya ditulis kode backend dari hasil tool nyata. Teks model tidak pernah menjadi status.
2. **Satu pintu side effect.** Semua aksi runtime agent lewat `ToolGateway`.
3. **Kode project tidak pernah berjalan di control plane.** Eksekusi hanya di sandbox lewat `sandbox-manager`; `subprocess` di `app/` hanya di `app/gitops/`.
4. **PostgreSQL adalah sumber kebenaran.** Status task hanya berubah lewat `workflow.states.transition`; status work item hanya lewat roll-up deterministik.
5. **Otoritas manusia tidak dapat didelegasikan ke LLM.** Approval R3 dibuat server dan diputuskan manusia lewat HTTP; tidak ada tool yang dapat "approve".
6. **Isolasi provider.** SDK vendor hanya di adapternya (`app/llm/anthropic_provider.py` untuk `anthropic`). Secret hanya di proses yang berhak (ADR-053).
7. **Deterministik sebelum LLM.** Fungsi yang bisa deterministik dibuat sebagai tool, bukan runtime agent LLM.
8. **Inkremental, single-node dulu.** Tier = konfigurasi + topologi; fitur tier tinggi tidak melemahkan kontrol tier S.

Penomoran ini direkonstruksi dari `ARCHITECTURE.md` §0 (P1–P8) karena CLAUDE.md asli tidak ikut diserahkan. Bila CLAUDE.md asli ditemukan, isinya yang berlaku.

## Alur kerja pengembangan

Tiga set skill terpasang. Setiap tahap punya **satu** pemilik supaya skill yang tumpang-tindih tidak saling bersaing:

| Tahap | Pakai | Hasil |
|---|---|---|
| Bahasa domain & keputusan | `/grill-with-docs`, `mattpocock-skills:domain-modeling` | `GLOSSARY.md`, ADR baru di `docs/DECISIONS.md` |
| Peta pekerjaan (fase/gate) | Issue WP dibangkitkan dari `docs/wbs/wbs_data.py`; `/wayfinder` untuk peta keputusan gate; `/to-tickets` hanya untuk memecah WP besar | GitHub Issues: 1 issue = 1 WP, edge blocked-by |
| Desain satu WP | `superpowers:brainstorming` | `docs/superpowers/specs/YYYY-MM-DD-<wp>-design.md` |
| Rencana satu WP | `superpowers:writing-plans` | `docs/superpowers/plans/YYYY-MM-DD-<wp>.md` |
| Eksekusi | `superpowers:subagent-driven-development` + peran dari `docs/agents/delivery-roster.md` | commit di branch WP |
| TDD | `superpowers:test-driven-development`; seam dan lokasi file test dari `docs/TEST_STRATEGY.md` | test merah → hijau |
| Debugging | `superpowers:systematic-debugging` | akar masalah + test regresi |
| Review per task | `superpowers:requesting-code-review` | temuan per severity |
| Review per WP | subagent `kk-backend-reviewer`, `kk-frontend-reviewer`, `kk-silent-failure-hunter`, lalu `/code-review` (Pocock, sumbu standar + spec) | temuan ditutup sebelum PR |
| Sign-off 🔒 | subagent `kk-security-reviewer` (AG-13) | laporan sign-off di PR |
| Klaim selesai | `superpowers:verification-before-completion` | output perintah nyata |
| PR | `mattpocock-skills:pr` | body PR: Summary / Evidence / Merge Danger |
| Retro | `/retro` setelah WP atau gate | usulan perbaikan CLAUDE.md / rules |

- Pakai skill di kolom "Pakai" walaupun skill lain dengan nama serupa juga cocok (`mattpocock-skills:tdd`, `mattpocock-skills:implement`, `/implement-spec`).
- Gate G0–G16 diputuskan PO. Agent menyiapkan bukti dan berhenti di gate.
- Catatan pekerjaan:
  - Rencana ada di `docs/wbs/wbs_data.py`.
  - Status kerja ada di GitHub Issues.
  - Bukti, sign-off 🔒, dan keputusan gate ada di `docs/IMPLEMENTATION_TRACKER.md`. Tracker diperbarui di PR yang sama dengan WP-nya.
- Merge ke `main` hanya lewat PR dengan CI hijau; manusia yang me-merge.

## Perintah

Belum ada: kode belum di-scaffold. Perintah yang direncanakan (`docs/TEST_STRATEGY.md` §6) diisi di sini saat WP Phase 1 selesai.

## Agent skills

### Issue tracker

GitHub Issues di `YosiaGalih/IDEE-Application`; judul diawali ID WP. Lihat `docs/agents/issue-tracker.md`.

### Triage labels

Lima label kanonik default (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). Lihat `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` di root; ADR di `docs/DECISIONS.md`, bukan `docs/adr/`. Lihat `docs/agents/domain.md`.
