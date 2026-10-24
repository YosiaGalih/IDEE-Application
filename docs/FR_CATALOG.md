# KodeKantor — Functional Requirements Catalog (FR-001..FR-292)

> **Status: *derived — perlu review PO*** (ADR-020). PRD v1.0 §5 hanya memetakan modul ke rentang FR (`FR-001..FR-159`) tanpa mendefinisikan isinya. Katalog ini **menurunkan** 159 FR dari teks PRD (§3, §4.1–4.3, §5.1–5.7, §6, §7.3, §8–§12) dengan rentang ID **persis** sesuai tabel modul §5. Seluruh isi FR bersifat *(turunan)*; sumber setiap FR dicantumkan pada kolom *Sumber PRD*. Perubahan isi FR setelah review PO dicatat di `docs/DECISIONS.md` dan tidak boleh menggeser ID.
>
> **Perluasan:** §7 = FR-160..FR-220 (v2, KONTRAK v2 — 61 FR); §8 = FR-221..FR-258 (v2.1, KONTRAK v2.1 di `docs/SDLC_SCALE_DESIGN.md` §6 — 38 FR, rancangan); §9 = FR-259..FR-292 (v2.2, KONTRAK v2.2 di `docs/POLYGLOT_INTEGRATION_DESIGN.md` §7 — 34 FR, rancangan). Total katalog: **292 FR**. §1–§6 (FR-001..FR-159) tidak diubah oleh perluasan.

Dokumen terkait: `docs/PRD.md` (sumber), `docs/IMPLEMENTATION_TRACKER.md` (status per FR), `docs/TEST_STRATEGY.md` (AC → test), `docs/ARCHITECTURE.md §3.1` (modul → kode).

## 1. Konvensi

- **Pernyataan**: setiap FR berbentuk *"Sistem harus …"* dan dapat diuji (ada kondisi yang dapat diamati di API/UI/DB/log).
- **Prioritas** mengikuti kolom *Priority* tabel modul PRD §5 beserta sub-catatannya:
  - M07 *P0 registry / P1 admin UI* → FR registry P0, admin UI (enable/disable, model policy) P1.
  - M12 *P0 core tests; E2E P1* → FR E2E/browser (FR-094) P1.
  - M15 *P0 local Git / P2 remote* → FR remote push/PR/merge-conflict agent (FR-119, FR-120) P2.
  - M19 *P0 audit basics / P1 dashboards* → FR dashboard metrics & OpenTelemetry (FR-150, FR-151) P1.
  - Penyesuaian lain *(turunan)*: SSO/OAuth (FR-006) P1 karena §5.1/§7.2.1 menyebut "fase lanjut"; import remote repository (FR-016) P1 dan egress allowlist (FR-073) P1 sesuai ADR-003/ADR-007; preview sandbox (FR-076) P1 sesuai ADR-008; fallback model (FR-131) P1 karena MVP satu provider; kill switch provider + pernyataan data flow (FR-158) P0 walau modul M20 P1, karena §7.3.5 berlaku untuk MVP.
- **AC** = acceptance criteria PRD §12.1 (AC-01..AC-20); **UC** = use case PRD §2.1 (UC-01..UC-10). "—" berarti FR bersifat lintas-fungsi/prasyarat (mis. auth berlaku untuk semua UC).
- **Sumber PRD** memakai penomoran isi PRD (§1–§17), ID agent (§6), dan ADR (`docs/DECISIONS.md`).

## 2. Ringkasan per modul

| Modul | Nama | Prioritas PRD | Rentang FR | Jumlah | P0 | P1 | P2 |
|---|---|---|---|---|---|---|---|
| M01 | Authentication & User Access | P0 | FR-001..FR-006 | 6 | 5 | 1 | 0 |
| M02 | Dashboard | P0 | FR-007..FR-011 | 5 | 5 | 0 | 0 |
| M03 | Project Management | P0 | FR-012..FR-018 | 7 | 6 | 1 | 0 |
| M04 | Repository & Context | P0 | FR-019..FR-025 | 7 | 7 | 0 | 0 |
| M05 | Browser IDE | P0 | FR-026..FR-032 | 7 | 7 | 0 | 0 |
| M06 | AI Chat Workspace | P0 | FR-033..FR-040 | 8 | 8 | 0 | 0 |
| M07 | Agent Registry | P0 registry / P1 admin UI | FR-041..FR-047 | 7 | 6 | 1 | 0 |
| M08 | Orchestrator & Workflow Engine | P0 | FR-048..FR-058 | 11 | 11 | 0 | 0 |
| M09 | Tool Gateway & Policy | P0 security foundation | FR-059..FR-067 | 9 | 9 | 0 | 0 |
| M10 | Sandbox & Runtime | P0 security foundation | FR-068..FR-076 | 9 | 7 | 2 | 0 |
| M11 | Terminal & Command Runner | P0 constrained | FR-077..FR-083 | 7 | 7 | 0 | 0 |
| M12 | Testing & Debugging | P0 core tests; E2E P1 | FR-084..FR-094 | 11 | 10 | 1 | 0 |
| M13 | Diff & Code Review | P0 | FR-095..FR-102 | 8 | 8 | 0 | 0 |
| M14 | User Proxy Acceptance | P0 | FR-103..FR-112 | 10 | 10 | 0 | 0 |
| M15 | Git & Remote Collaboration | P0 local Git / P2 remote | FR-113..FR-120 | 8 | 6 | 0 | 2 |
| M16 | Documentation & Knowledge | P1 | FR-121..FR-127 | 7 | 0 | 7 | 0 |
| M17 | Provider & Model Settings | P0 provider config | FR-128..FR-135 | 8 | 7 | 1 | 0 |
| M18 | Deployment & Environments | P2 | FR-136..FR-143 | 8 | 0 | 0 | 8 |
| M19 | Observability & Audit | P0 audit basics / P1 dashboards | FR-144..FR-151 | 8 | 6 | 2 | 0 |
| M20 | Admin & Governance | P1 | FR-152..FR-159 | 8 | 1 | 7 | 0 |
| **Total** | | | FR-001..FR-159 | **159** | **126** | **23** | **10** |

## 3. Katalog per modul

### M01 — Authentication & User Access (P0)

*Scope PRD §5:* Login, logout, session management, optional OAuth/SSO, password recovery, role-based authorization.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-001 | Sistem harus menyediakan login dengan email/password dan logout yang membuat serta mencabut sesi server-side; kredensial salah menghasilkan error generik yang tidak membocorkan keberadaan akun, dan form login memiliki label yang aksesibel. | P0 | §5 M01; §4.1 #1; §5.1; §9 Auth | AC-01 | — |
| FR-002 | Sistem harus membuat sesi dengan mekanisme aman (token acak yang disimpan sebagai hash, cookie HttpOnly/Secure, opsi remember me) dengan session expiry idle dan absolut; token/sesi tidak pernah masuk ke prompt atau log mentah. | P0 | §5.1; §4.1 #1 | AC-01, AC-15 | — |
| FR-003 | Sistem harus menegakkan RBAC minimum Owner, Admin, Developer, Reviewer, Read-only secara server-side pada setiap endpoint, termasuk role yang di-scope ke project tertentu. | P0 | §5.1; §8 roles/user_roles; §9 | AC-01 | — |
| FR-004 | Sistem harus mengisolasi data per user/project sehingga user tanpa hak akses tidak dapat membaca atau mengubah project, task, file, atau artefak milik user lain; akses lintas scope dijawab 404 tanpa membocorkan keberadaan resource. | P0 | §5.1; §9.1; §10.1 Cross-project access | AC-01 | — |
| FR-005 | Sistem harus menyediakan password recovery dan perubahan password melalui token sekali pakai yang kedaluwarsa, menerapkan rate limiting serta lockout sementara pada login, dan mencatat setiap peristiwa autentikasi di audit trail. | P0 | §5.1; §4.1 #1 | AC-01, AC-19 | — |
| FR-006 | Sistem harus menyediakan titik ekstensi identity (placeholder SSO/OAuth di layar login, nonaktif secara default) sehingga OAuth/SSO dapat ditambahkan setelah private pilot tanpa mengubah model data user. | P1 | §5 M01 (optional OAuth/SSO); §5.1; §7.2.1 Authentication; ADR-002 | AC-01 | — |

### M02 — Dashboard (P0)

*Scope PRD §5:* Metrics project/task, recent projects, agent activity, failed task queue, quick create.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-007 | Sistem harus menampilkan metrics project/task (project aktif, task berjalan, selesai, gagal, menunggu approval) yang dihitung dari data tersimpan dan hanya mencakup project dalam scope user. | P0 | §5 M02; §4.1 #2; §4.2 Dashboard | AC-01 | UC-09 |
| FR-008 | Sistem harus menampilkan recent projects dan recent tasks dengan filter/search dan memungkinkan membuka project atau task langsung dari dashboard. | P0 | §5 M02; §4.1 #2 | — | UC-09 |
| FR-009 | Sistem harus menampilkan agent activity terkini (agent, task, status, durasi) dengan tautan ke detail aktivitas/agent run. | P0 | §5 M02; §4.1 #2 | AC-19 | UC-06 |
| FR-010 | Sistem harus menampilkan antrean failed tasks (failed, interrupted, needs_changes, waiting_approval) beserta aksi lanjutan (buka, resume/retry, putuskan approval) sesuai permission user. | P0 | §5 M02 failed task queue; §4.1 #2 | AC-17 | UC-09, UC-07 |
| FR-011 | Sistem harus menyediakan quick create (project dan task baru) dari dashboard serta ringkasan usage/budget periode berjalan yang membedakan nilai actual, estimate, dan unavailable. | P0 | §5 M02 quick create; §4.2 Dashboard usage/budget; §5.7 | AC-16 | UC-01, UC-10 |

### M03 — Project Management (P0)

*Scope PRD §5:* Create from template/blank, import repository/archive, settings, archive, project metadata.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-012 | Sistem harus menyimpan project dengan ID stabil, nama, slug, stack, path internal (workspace_ref), default branch, settings, status, dan owner. | P0 | §5.2; §8 projects | AC-02 | UC-01, UC-02 |
| FR-013 | Sistem harus membuat blank project dengan validasi input dan repository Git lokal yang terinisialisasi pada default branch. | P0 | §5 M03; §5.2; §3.1 #1; §4.1 #3 | AC-02 | UC-01 |
| FR-014 | Sistem harus membuat project dari template yang dapat dicari dan dipilih, menghasilkan skeleton yang dapat dijalankan beserta instruksi cara menjalankannya. | P0 | §5 M03; §5.2; §4.1 #3; UC-01 | AC-02 | UC-01 |
| FR-015 | Sistem harus mengimpor archive (zip/tar.gz) yang divalidasi: menolak path traversal, symlink/hardlink escape, file atau arsip terlalu besar, archive bomb (rasio kompresi/jumlah file), dan overwrite file sistem/existing, serta menampilkan progres import. | P0 | §5.2; §4.1 #3; §10.1 Workspace escape | AC-02 | UC-02 |
| FR-016 | Sistem harus mengimpor remote repository hanya setelah provider credential/host disetujui (clone read-only dari host allowlist) dan menampilkan status sinkronisasi repository. | P1 | §5.2; §5 M03; ADR-003 | AC-02 | UC-02 |
| FR-017 | Sistem harus menyediakan pengaturan dan metadata project (nama, deskripsi, settings termasuk kewajiban approval plan dan budget default) yang hanya dapat diubah oleh role berwenang. | P0 | §5 M03 settings/metadata; §4.2 Projects | AC-01 | UC-10 |
| FR-018 | Sistem harus mengelola lifecycle project active → archived → deleting_pending_approval → deleted, dengan delete sebagai aksi R3 yang memerlukan approval dan mengikuti kebijakan retention/backup. | P0 | §5.2; §10.2 R3; §10.4; §8.1 | AC-13 | — |

### M04 — Repository & Context (P0)

*Scope PRD §5:* File tree, text/symbol search, stack detection, indexing, entrypoints, context retrieval.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-019 | Sistem harus menampilkan file tree project melalui root yang tervalidasi dengan batas kedalaman dan jumlah entri. | P0 | §5 M04; §9 Repository; §4.1 #4 | AC-03 | UC-02, UC-03 |
| FR-020 | Sistem harus menyediakan pencarian teks (literal/regex, filter glob) di workspace project dengan batas jumlah hasil dan menghormati denylist. | P0 | §5 M04 text search; A04 repo.search_text | AC-03 | UC-03 |
| FR-021 | Sistem harus menyediakan pencarian simbol (fungsi, kelas, dsb.) berdasarkan indeks repository dengan hasil berupa path, baris, dan jenis simbol. | P0 | §5 M04 symbol search; A04 repo.search_symbol | AC-03 | UC-03 |
| FR-022 | Sistem harus mendeteksi stack project (bahasa, framework, package manager, test profile) dan entrypoint secara deterministik setelah create/import. | P0 | §5 M04 stack detection, entrypoints; H41; UC-02 | — | UC-02 |
| FR-023 | Sistem harus mengindeks repository per project (file dan simbol beserta commit sumber) dan memungkinkan pengguna memicu re-index setelah perubahan atau import. | P0 | §5.2; §5 M04 indexing | — | UC-02 |
| FR-024 | Sistem harus membangun context package untuk agent yang berisi referensi file/line spesifik dan ringkasan seperlunya dalam batas context budget, tanpa memuat seluruh repository. | P0 | §5.4; §5 M04 context retrieval; A04; §7.3.5 | AC-04 | UC-03, UC-04 |
| FR-025 | Sistem harus mengecualikan .env, private key, credential, folder dependency, build artifact, file binary/terlalu besar, dan path di .agentignore dari context, serta melakukan secret scanning/redaction sebelum context package dibangun. | P0 | §7.3.5; §5.3; ADR-011 | AC-15 | — |

### M05 — Browser IDE (P0)

*Scope PRD §5:* Monaco editor, tabs, read/write operations, syntax highlight, patch preview, file save state.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-026 | Sistem harus menyediakan layout IDE dengan explorer di kiri, editor di tengah, AI panel di kanan, dan panel bawah yang dapat dipilih untuk terminal/test/logs. | P0 | §5.3; §4.1 #4 | AC-03 | UC-03 |
| FR-027 | Sistem harus menyediakan Monaco editor dengan multi-tab dan syntax highlighting sesuai bahasa file. | P0 | §5 M05; §7.2.1 Code editor | AC-03 | UC-03 |
| FR-028 | Sistem harus membaca file hanya melalui Workspace Service dengan root project tervalidasi, menolak path absolut, segmen '..', karakter NUL, dan symlink yang keluar dari root. | P0 | §5.3; §10.1 Workspace escape | AC-03 | — |
| FR-029 | Sistem harus menyimpan perubahan file pengguna melalui workspace API dengan version check (hash isi) sehingga perubahan konkuren tidak tertimpa diam-diam (respons 409 conflict). | P0 | §5.3; §10.1 Data loss; R-07 | AC-03, AC-09 | UC-03 |
| FR-030 | Sistem harus menampilkan status penyimpanan file per tab (tersimpan, belum tersimpan, menyimpan, gagal/konflik). | P0 | §5 M05 file save state; §4.1 #4 autosave state | AC-03 | — |
| FR-031 | Sistem harus menampilkan patch preview (diff old/new) sebelum patch agent diterapkan ke workspace aktif pada review-first mode, dan menerapkan patch hanya melalui mekanisme apply yang aman. | P0 | §5.3; §4.1 #4 safe apply patch; §4.3 | AC-09 | UC-07 |
| FR-032 | Sistem harus menampilkan file binary atau terlalu besar sebagai metadata (atau download sesuai policy) dan tidak memasukkannya ke prompt secara otomatis. | P0 | §5.3 | AC-03 | — |

### M06 — AI Chat Workspace (P0)

*Scope PRD §5:* Prompt history, attachments/references, streaming, cancellation, context scope, task creation.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-033 | Sistem harus menyimpan prompt history per conversation secara terstruktur (user message, task intent, context refs, assistant output, tool calls, event stream) setelah redaksi. | P0 | §5.4; §5 M06 prompt history; §8 conversations/messages | AC-04 | UC-09 |
| FR-034 | Sistem harus memungkinkan pengguna melampirkan referensi nyata (file, rentang baris, task ID, test report, diff, finding) ke pesan chat dan memvalidasinya terhadap scope project. | P0 | §5 M06 attachments/references; §4.3 | AC-04 | UC-03 |
| FR-035 | Sistem harus men-stream output agent dan progres task ke chat panel secara real-time dengan event yang memiliki task ID dan sequence ID. | P0 | §5 M06 streaming; §4.1 #4 chat streaming; §7.2.1 Browser event streaming | AC-05 | — |
| FR-036 | Sistem harus menyediakan stop/cancel dari chat yang menghentikan orchestration dan proses sandbox terkait, bukan hanya animasi UI. | P0 | §5.4; §5 M06 cancellation | AC-14 | — |
| FR-037 | Sistem harus memungkinkan pengguna menentukan context scope (seluruh project atau file/folder tertentu) dan menegakkan context budget pada context yang dikirim ke model. | P0 | §5 M06 context scope; §5.4 | AC-04 | UC-03 |
| FR-038 | Sistem harus membuat task dari prompt chat yang menghasilkan task ID, requirement, plan, dan status yang dapat dilacak. | P0 | §5 M06 task creation; §3.1 | AC-04 | UC-01, UC-03, UC-04 |
| FR-039 | Sistem harus memisahkan secara visual output AI (saran/narasi) dari bukti tool aktual (test run, diff, artefak) dan tidak pernah menyajikan saran sebagai hasil yang telah dieksekusi. | P0 | §4.3 | AC-11 | UC-06 |
| FR-040 | Sistem harus memperlakukan instruksi yang ditemukan di source code, README, log, atau hasil retrieval sebagai data tidak tepercaya yang tidak dapat menaikkan izin, mengubah policy, atau memicu approval. | P0 | §5.4; §7.3.5; §10.1 Prompt injection; R-02 | AC-06 | — |

### M07 — Agent Registry (P0 registry / P1 admin UI)

*Scope PRD §5:* Catalog, profiles, capabilities, model policy, permissions, readiness/status, invocation logs.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-041 | Sistem harus memuat registry 41 specialist + Orchestrator dari agents/registry.yaml, memvalidasi skemanya, dan menyinkronkannya ke tabel agent_profiles saat seed/startup; registry tidak valid menggagalkan startup. | P0 | §6; §6.2; §8 agent_profiles | AC-06 | — |
| FR-042 | Sistem harus menyimpan untuk setiap agent: agent_id, version, purpose, triggers, capabilities, input/output contract, permission profile, model policy, dan limits. | P0 | §6.2; §8 agent_profiles; §5 M07 | AC-06, AC-10 | — |
| FR-043 | Sistem harus menegakkan bahwa approval_grant setiap profil selalu deny dan tool allowlist konsisten dengan permission profile (mis. reviewer hanya read-only tools); pelanggaran menggagalkan validasi registry. | P0 | §6.2; §7.3.2; §10.4 | AC-06, AC-10, AC-13 | — |
| FR-044 | Sistem harus menampilkan katalog agent dengan search/filter (grup, mode, status) serta detail capability, permission profile, dan model policy setiap agent. | P0 | §5 M07 catalog/profiles; §4.1 #5; §4.2 AI Agents | AC-06 | — |
| FR-045 | Sistem harus menampilkan readiness/status setiap agent (ready, disabled, planned) berdasarkan mvp_mode, flag enabled, dan hasil Claude API readiness gate. | P0 | §5 M07 readiness/status; §7.3.6 | — | — |
| FR-046 | Sistem harus mencatat invocation log setiap agent run (agent_id, versi, model/provider, prompt version, tool calls, token usage, latency, status, biaya bila diketahui) dan menampilkan run history per agent dan per task. | P0 | §5.4; §5 M07 invocation logs; §8 agent_runs | AC-16, AC-19 | — |
| FR-047 | Sistem harus menyediakan admin UI untuk mengaktifkan/menonaktifkan profil agent dan mengatur model policy (tier) per agent, terbatas pada role admin dan tercatat di audit. | P1 | §5 M07 (P1 admin UI); §4.1 #5 | AC-19 | — |

### M08 — Orchestrator & Workflow Engine (P0)

*Scope PRD §5:* Intent routing, plan DAG, queues, dependencies, retry, checkpoint, timeout, cancel/resume.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-048 | Sistem harus melakukan intent routing yang mengklasifikasikan prompt menjadi workflow_type (create/modify/debug/test/review/docs), kompleksitas, dan risiko, lalu memilih workflow awal. | P0 | §5 M08 intent routing; §3.1 #2; §3.2; A01; §6.1 | AC-04 | UC-01, UC-03, UC-04 |
| FR-049 | Sistem harus menghasilkan requirement eksplisit dan acceptance criteria awal yang dapat diuji dari prompt, dan memindahkan task ke needs_clarification bila ada pertanyaan klarifikasi yang memblokir. | P0 | §3.1 #2; A02; ADR-006 | AC-04, AC-11 | UC-01, UC-06 |
| FR-050 | Sistem harus membuat plan DAG (langkah, dependency, specialist assignment, batas iterasi, perkiraan budget) yang dapat ditinjau, diedit, dan disetujui pengguna sebelum eksekusi bila kebijakan project mewajibkan. | P0 | §5 M08 plan DAG; §3.1 #4; §4.1 #6; A03 | AC-04 | UC-01, UC-03 |
| FR-051 | Sistem harus mengeksekusi langkah plan sesuai urutan topologis dependency di workspace/branch terisolasi milik task. | P0 | §5 M08 dependencies; §3.1 #5; R-03 | AC-04, AC-09 | UC-01, UC-03 |
| FR-052 | Sistem harus mengubah status task hanya melalui state machine tervalidasi (PRD §3.3 + deviasi ADR-006), menolak transisi tidak sah (409), dan mencatat setiap transisi sebagai event dan audit. | P0 | §3.3; ADR-006 | AC-12, AC-14 | — |
| FR-053 | Sistem harus mengantrekan task melalui queue dengan batas concurrency (MAX_ACTIVE_TASKS) dan lease sehingga duplicate delivery tidak menjalankan task yang sama dua kali. | P0 | §5 M08 queues; NFR-05; §7.2.1 Queue | AC-17 | UC-09 |
| FR-054 | Sistem harus menyimpan checkpoint per langkah dan melanjutkan task dari checkpoint terakhir setelah interrupt/resume tanpa mengulang langkah yang sudah selesai atau side effect non-idempoten. | P0 | §5 M08 checkpoint; §7.3.2 #6; UC-09 | AC-17 | UC-09 |
| FR-055 | Sistem harus mendeteksi worker mati atau lease kedaluwarsa dan menandai task running/validating sebagai interrupted (bukan hilang), lalu mengizinkan resume atau retry manual. | P0 | NFR-01; §7.3.3; §3.3 interrupted | AC-17 | UC-09 |
| FR-056 | Sistem harus menerapkan timeout per agent run/tool call dan retry cap dengan exponential backoff + jitter untuk kegagalan sementara (429/5xx/timeout), tanpa mengulang side effect tool tanpa idempotency. | P0 | §5 M08 retry/timeout; §7.3.3 | AC-17 | UC-09 |
| FR-057 | Sistem harus mendukung cancel task (termasuk saat running/validating) yang menghentikan pemanggilan model dan proses sandbox terkait atau menandai secara eksplisit bila tidak dapat dihentikan, serta mendukung pause/resume. | P0 | §5 M08 cancel/resume; §6.1 orchestrator.pause_resume; §4.1 #6 | AC-14 | UC-09 |
| FR-058 | Sistem harus mengonsolidasikan evidence, findings, dan acceptance report, dan menyelesaikan task hanya berdasarkan hasil tool dan evidence tersimpan, tidak pernah berdasarkan klaim teks agent. | P0 | §3.3 (status tidak boleh dibuat-buat); §6.1 orchestrator.collect_evidence/finalize_task | AC-11, AC-12 | UC-06 |

### M09 — Tool Gateway & Policy (P0 security foundation)

*Scope PRD §5:* Typed tool schemas, path validation, allowlisted commands, quota enforcement, approval checks.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-059 | Sistem harus mengeksekusi semua side effect agent (file, perintah, Git) hanya melalui Tool Gateway dengan typed tool schema; input yang tidak valid ditolak sebelum handler dijalankan. | P0 | §5 M09 typed tool schemas; §7.1; §7.3.2 #3 | AC-06 | UC-03 |
| FR-060 | Sistem harus menolak pemanggilan tool yang tidak ada di allowlist profil agent atau tidak memenuhi permission profile, mencatatnya sebagai tool call 'denied' dan audit event. | P0 | §7.3.2; §6.2; AC-06 | AC-06 | — |
| FR-061 | Sistem harus memvalidasi project/path scope setiap tool (canonical path resolution, symlink policy, denylist tulis, aturan path test_only/docs_only). | P0 | §5 M09 path validation; §10.1 Workspace escape | AC-06 | — |
| FR-062 | Sistem harus mengklasifikasikan setiap tool ke risk level R0–R4 dan menerapkan execution policy PRD §10.2; tool R4 tidak pernah didaftarkan atau tersedia. | P0 | §10.2 | AC-06, AC-13 | — |
| FR-063 | Sistem harus mewajibkan approval valid untuk setiap aksi R3: membuat approval request yang terikat payload hash dan mengeksekusi aksi hanya bila ada approval 'approved' yang belum dikonsumsi dengan hash identik (one-time consume). | P0 | §5 M09 approval checks; §10.4; §8.1 | AC-13 | UC-08 |
| FR-064 | Sistem harus menegakkan quota per tool call (iterasi tool per run, budget token/biaya, ukuran output, jumlah hasil) dan memotong output dengan penanda truncation yang eksplisit. | P0 | §5 M09 quota enforcement; NFR-11 | AC-06, AC-16 | UC-10 |
| FR-065 | Sistem harus hanya mengizinkan perintah allowlisted berupa profil tetap (bukan argv bebas) dengan argumen yang divalidasi terhadap pola profil. | P0 | §5 M09 allowlisted commands; §10.3 | AC-07 | — |
| FR-066 | Sistem harus mencatat setiap tool call (agent run, nama tool, risk, input hash, sanitized input, status, output ref, approval, durasi) dan mengembalikan hasil aktual yang telah diredaksi ke agent sebagai tool_result. | P0 | §8 tool_calls; §7.3.2 #4 | AC-19 | — |
| FR-067 | Sistem harus memastikan teks dari LLM, tool output, repository, atau pesan antar-agent tidak dapat membuat keputusan approval atau mengubah policy; approval hanya diputuskan oleh user terautentikasi yang berwenang melalui endpoint keputusan. | P0 | §10.4; §5.5 Final acceptance | AC-06, AC-13 | UC-07 |

### M10 — Sandbox & Runtime (P0 security foundation)

*Scope PRD §5:* Isolated workspace, ephemeral execution, CPU/RAM/disk/time limits, network policy, cleanup.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-068 | Sistem harus mengeksekusi kode project (test, build, lint, preview) hanya di container sandbox per run yang dibuat sandbox-manager, tidak pernah di proses API/worker atau shell host. | P0 | §5 M10; §7.2.2 Execution; §10.1 Host compromise | AC-07 | UC-04, UC-05 |
| FR-069 | Sistem harus menjalankan container sandbox sebagai non-root dengan semua Linux capabilities di-drop, no-new-privileges, tanpa privileged mode, tanpa host PID/network namespace, dan root filesystem read-only. | P0 | §10.3 | AC-07 | — |
| FR-070 | Sistem harus menerapkan batas CPU, memori, PID, disk/tmpfs, waktu eksekusi, dan ukuran output pada setiap run serta membunuh proses saat timeout. | P0 | §5 M10 limits; §10.1 Resource exhaustion; §10.3 | AC-07, AC-08 | — |
| FR-071 | Sistem harus me-mount hanya workspace task (opsional read-only untuk task review-only) dan volume sementara yang eksplisit, serta tidak pernah me-mount Docker socket ke container sandbox. | P0 | §10.3; §7.5 | AC-07 | — |
| FR-072 | Sistem harus menonaktifkan outbound network container sandbox secara default. | P0 | §5 M10 network policy; §10.3; ADR-007 | AC-07 | — |
| FR-073 | Sistem harus menyediakan rute egress terkontrol (proxy dengan allowlist registry paket) untuk instalasi dependency yang hanya aktif bila diaktifkan admin. | P1 | §10.3; R-08; ADR-007 | AC-07 | — |
| FR-074 | Sistem harus membersihkan container sementara dan credential setelah run/task, dan hanya mempertahankan artefak yang disetujui serta file project. | P0 | §5 M10 cleanup; §10.3 | AC-07 | — |
| FR-075 | Sistem harus mengakses Docker API dari sandbox-manager hanya melalui antarmuka terbatas (socket proxy dengan allowlist endpoint dan profil tetap) sehingga konfigurasi container tidak dapat dipengaruhi pemanggil. | P0 | §10.3; §10.1 Host compromise; ADR-009 | AC-07 | — |
| FR-076 | Sistem harus menyediakan preview aplikasi di sandbox preview terisolasi (subdomain terpisah tanpa akses ke cookie aplikasi utama maupun API internal) dengan refresh dan pemilihan viewport. | P1 | §5.3 Run/Preview; §4.1 #7; §15.1 #6; ADR-008 | — | UC-05 |

### M11 — Terminal & Command Runner (P0 constrained)

*Scope PRD §5:* Streaming output, command allowlist/profile, exit codes, timeout, working directory validation.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-077 | Sistem harus menyediakan terminal read-only (xterm.js) yang men-stream stdout/stderr run sandbox melalui channel terautentikasi tanpa membuka shell host. | P0 | §5 M11 streaming output; §7.2.1 Terminal UI; ADR-014 | AC-05, AC-07 | UC-04 |
| FR-078 | Sistem harus hanya menerima pilihan command profile yang tersedia untuk stack project (allowlist), bukan perintah bebas. | P0 | §5 M11 command allowlist/profile | AC-06, AC-07 | — |
| FR-079 | Sistem harus memvalidasi argumen tambahan run terhadap pola allowlist profil (karakter dan jumlah token) dan menolak argumen yang tidak sesuai. | P0 | §5 M11; §10.2 R2 | AC-06 | — |
| FR-080 | Sistem harus memvalidasi working directory run sehingga hanya worktree task atau repo project di bawah WORKSPACE_ROOT (setelah realpath) yang dapat dipakai. | P0 | §5 M11 working directory validation | AC-07 | — |
| FR-081 | Sistem harus mencatat dan menampilkan exit code, status (succeeded/failed/timeout/killed/error), durasi, dan penanda output terpotong untuk setiap run. | P0 | §5 M11 exit codes | AC-08 | — |
| FR-082 | Sistem harus menerapkan timeout per profil (default dan maksimum) dan memungkinkan pengguna membatalkan run yang sedang berjalan. | P0 | §5 M11 timeout | AC-08, AC-14 | — |
| FR-083 | Sistem harus menyimpan log run sebagai artefak dan memungkinkan output ditampilkan ulang setelah browser reconnect. | P0 | §5 M11; §7.2.1 Browser event streaming | AC-05, AC-08 | UC-09 |

### M12 — Testing & Debugging (P0 core tests; E2E P1)

*Scope PRD §5:* Unit/integration/build/E2E runners, failure capture, debugger loop, retry budget.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-084 | Sistem harus mendeteksi command test/lint/build yang didukung dari stack profile project (mis. pytest, npm test, lint, build). | P0 | §5.6 | AC-08 | UC-03 |
| FR-085 | Sistem harus menjalankan lint, unit test, dan build di sandbox sesuai risiko dan stack, baik dipicu workflow maupun pengguna (run/re-run). | P0 | §3.1 #6; §4.1 #8; §5 M12 | AC-07, AC-08 | UC-01, UC-03, UC-04 |
| FR-086 | Sistem harus menyimpan setiap test run dengan command profile, argv, exit code, stdout/stderr yang diredaksi, waktu mulai/selesai, durasi, environment ID (image digest), dan artefak (log/junit). | P0 | §5.6; §8 test_runs | AC-08 | — |
| FR-087 | Sistem harus membedakan status kegagalan install dependency, timeout, runner crash, dan test assertion failure. | P0 | §5.6 | AC-08 | UC-04 |
| FR-088 | Sistem harus melaporkan coverage hanya bila runner menghasilkan data coverage; coverage tidak pernah diestimasi oleh model. | P0 | §5.6 | AC-08 | — |
| FR-089 | Sistem harus menampilkan hasil per suite (tab unit/integration/E2E) dengan hitungan passed/failed/skipped serta drill-down ke failing case dan log. | P0 | §4.1 #8; §4.2 Testing | AC-08 | UC-04 |
| FR-090 | Sistem harus menjalankan debug loop: bila test gagal, Debugger menganalisis bukti nyata (failing cases, log), menerapkan patch dalam scope task, lalu test dijalankan ulang. | P0 | §3.1 #7; §4.1 #8 feed evidence to Debugger; C20 | AC-08 | UC-04 |
| FR-091 | Sistem harus membatasi debug loop dengan jumlah percobaan maksimum yang dapat dikonfigurasi (default pilot 5 per task) dan berhenti dengan status yang jelas saat batas tercapai. | P0 | §5.6; §5 M12 retry budget; R-05 | AC-08 | UC-04 |
| FR-092 | Sistem harus menetapkan status 'tests passed' hanya dari exit code dan artefak test nyata yang dicatat runner. | P0 | §7.4; R-04 | AC-08, AC-11 | — |
| FR-093 | Sistem harus menyertakan test plan dalam plan task yang memetakan setiap requirement ke profil/test yang diharapkan membuktikannya. | P0 | C16 (test.map_requirements_to_tests); §3.1 #6 | AC-11 | UC-06 |
| FR-094 | Sistem harus menjalankan E2E/browser test (Playwright) di sandbox/preview dan menyimpan screenshot, console log, dan network log sebagai evidence. | P1 | §5 M12 (E2E P1); UC-05; §4.1 #7–8; C19 | AC-08 | UC-05 |

### M13 — Diff & Code Review (P0)

*Scope PRD §5:* Changed files, diff hunks, review comments, severity, request fix, apply/reject patch.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-095 | Sistem harus menampilkan daftar file yang berubah pada task (added/modified/deleted/renamed) beserta statistik baris. | P0 | §5 M13 changed files; §4.1 #9 | AC-09 | UC-07 |
| FR-096 | Sistem harus menampilkan diff per hunk (red/green, old/new) untuk file teks dan metadata untuk file binary. | P0 | §5 M13 diff hunks; §4.3 | AC-09 | UC-07 |
| FR-097 | Sistem harus menjalankan code review otomatis yang independen oleh reviewer read-only dan menyimpan findings dengan severity, kategori, lokasi file/baris, evidence, dan rekomendasi. | P0 | §5 M13 severity; D22; §3.1 #8 | AC-10 | UC-07 |
| FR-098 | Sistem harus menjalankan security baseline deterministik (secret scan) pada worktree/diff task dan menampilkan hasilnya di tab security. | P0 | §4.1 #9 security tab; D23; §7.2.1 Security scanning | AC-10, AC-15 | UC-07 |
| FR-099 | Sistem harus memungkinkan pengguna menambah komentar pada hunk/baris dan memberi feedback (accepted/dismissed) pada findings. | P0 | §5 M13 review comments; §4.1 #9 | AC-10 | UC-07 |
| FR-100 | Sistem harus memungkinkan pengguna meminta revisi (request fix) dari komentar atau finding, yang memicu iterasi perbaikan terlacak pada task yang sama. | P0 | §5 M13 request fix; §4.1 #9 request revision | AC-18 | UC-07 |
| FR-101 | Sistem harus menerapkan patch yang disetujui ke default branch hanya setelah approval apply_changes; bila default branch berubah dan terjadi konflik, task kembali ke needs_changes tanpa overwrite. | P0 | §5 M13 apply patch; §4.1 #9 stage/apply approved patch; R-07; ADR-016 | AC-09, AC-13 | UC-07 |
| FR-102 | Sistem harus memungkinkan pengguna menolak patch sehingga workspace aktif tidak berubah dan task berstatus rejected. | P0 | §5 M13 reject patch; §3.3 | AC-09 | UC-07 |

### M14 — User Proxy Acceptance (P0)

*Scope PRD §5:* Requirement baseline, criteria traceability, visual/user journey evidence, gaps, acceptance report.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-103 | Sistem harus menyimpan requirement baseline berversi dengan sumber (message/agent), tingkat wajib/opsional, status, dan metode verifikasi untuk setiap acceptance criterion. | P0 | §5.5 Requirement baseline; §8 requirements/acceptance_criteria | AC-11 | UC-06 |
| FR-104 | Sistem harus mencatat perubahan scope sebagai requirement change yang membuat baseline versi baru (versi lama superseded) dan menautkannya ke iterasi task. | P0 | §5.5 Gap routing | AC-12, AC-18 | UC-06 |
| FR-105 | Sistem harus menjalankan User Proxy setelah implementasi, test, dan review untuk menghasilkan status setiap acceptance criterion (passed/failed/unverified/blocked) beserta rationale dan evidence refs. | P0 | §5.5 Criteria status; §3.1 #8; A07; §6.2 | AC-11 | UC-06, UC-03 |
| FR-106 | Sistem harus memverifikasi evidence secara server-side dan menurunkan status 'passed' menjadi 'unverified' bila evidence tidak ada, tidak valid, milik task lain, atau test run terkait gagal/bukan dari iterasi berjalan. | P0 | §5.5; §3.3 note; R-04 | AC-11 | UC-06 |
| FR-107 | Sistem harus mendukung evidence berupa test result, API response, screenshot, browser trace, diff, dan catatan review dengan referensi yang dapat ditelusuri (screenshot/browser trace bergantung pada E2E P1). | P0 | §5.5 Evidence; §4.1 #10 | AC-11 | UC-06, UC-05 |
| FR-108 | Sistem harus menangani preferensi subjektif dengan menandai kriteria subjektif, menjelaskan penilaian agent, meminta feedback pengguna (ok/not ok), dan mengizinkan pengguna menunda (defer) kriteria tersebut. | P0 | §5.5 Subjective preference; §4.1 #10 defer subjective criterion | AC-11 | UC-06 |
| FR-109 | Sistem harus mengarahkan kegagalan objektif ke iterasi perbaikan yang jelas (gap + suggested fix) dan menjalankan siklus revalidation yang dibatasi setelah perbaikan. | P0 | §5.5 Gap routing; §4.1 #10 request fixes; AC-18 | AC-18 | UC-06 |
| FR-110 | Sistem harus menghasilkan acceptance report berisi total kriteria per status, gap, risiko, confidence, evidence links, dan rekomendasi next action, yang dapat diekspor sebagai Markdown. | P0 | §5.5 Acceptance report; §4.1 #10 export report | AC-11 | UC-06 |
| FR-111 | Sistem harus mencegah task ditandai accepted/completed bila ada kriteria wajib failed tanpa waiver atau keputusan pengguna yang tercatat; status waived_by_user hanya dapat ditetapkan oleh pengguna. | P0 | AC-12; §5.5 Criteria status | AC-12 | UC-06 |
| FR-112 | Sistem harus memastikan rekomendasi User Proxy tidak menggantikan keputusan eksplisit pengguna: apply perubahan dan aksi berisiko tetap memerlukan approval pengguna. | P0 | §5.5 Final acceptance; §10.4 | AC-12, AC-13 | UC-06, UC-07 |

### M15 — Git & Remote Collaboration (P0 local Git / P2 remote)

*Scope PRD §5:* Status/diff/branch/commit proposal; remote sync/PR behind credentials and approval.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-113 | Sistem harus menginisialisasi dan mengelola repository Git lokal per project serta menampilkan status (branch, head, perubahan working tree). | P0 | §5 M15 status; §9 Git; E27 | AC-09 | UC-03 |
| FR-114 | Sistem harus membuat branch/worktree terisolasi per task (kk/task-<id>) dari base commit sehingga agent tidak menulis langsung ke default branch. | P0 | §5 M15 branch; §3.1 #5; R-03; ADR-016 | AC-09, AC-19 | UC-03 |
| FR-115 | Sistem harus menampilkan diff Git (working tree serta branch task terhadap base commit) dan log commit. | P0 | §5 M15 diff; E27 git.inspect_diff/inspect_history | AC-09 | UC-07 |
| FR-116 | Sistem harus membuat commit lokal di branch task dengan pesan yang dihasilkan/ditinjau dan mencatat commit tersebut di task record. | P0 | §5 M15 commit proposal; E27 git.prepare_commit | AC-19 | — |
| FR-117 | Sistem harus menjalankan Git CLI hanya melalui adapter terbatas (argumen tetap tanpa shell, hooks dinonaktifkan, protokol dibatasi, tanpa credential di argumen). | P0 | §7.2.1 Git; §7.3.2 | AC-06 | — |
| FR-118 | Sistem harus menolak force-push dan operasi Git destruktif (R4) serta tidak pernah membuang perubahan pengguna. | P0 | E27 (no force-push); §1.4; R-07 | AC-13 | — |
| FR-119 | Sistem harus mendukung push/merge remote dan pembuatan PR hanya dengan credential reference dan approval R3 yang terikat payload. | P2 | §5 M15 (P2 remote); §10.4; E29; ADR-003 | AC-13 | UC-07 |
| FR-120 | Sistem harus menyajikan analisis dan resolusi konflik merge secara transparan (penjelasan, usulan resolusi, test pasca-merge) tanpa overwrite destruktif terhadap data pengguna. | P2 | §5 M15 remote sync; E28 | AC-09 | — |

### M16 — Documentation & Knowledge (P1)

*Scope PRD §5:* README/docs generation, ADR, repository indexing, memory retrieval, stale-index invalidation.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-121 | Sistem harus menghasilkan atau memperbarui README, setup guide, dan dokumentasi arsitektur sebagai patch dokumentasi yang ditinjau melalui diff (profil G35, docs_only). | P1 | §5 M16 README/docs generation; G35 | AC-09 | UC-02 |
| FR-122 | Sistem harus menyimpan ADR/decision project (judul, isi, source commit, pembuat) yang dapat dibuat oleh pengguna atau agent. | P1 | §5 M16 ADR; §8 project_memory; G37 | — | — |
| FR-123 | Sistem harus menampilkan halaman Knowledge yang memuat project context, ADR, docs, dan simbol terindeks. | P1 | §4.2 Knowledge; §5 M16 repository indexing | — | UC-02 |
| FR-124 | Sistem harus mengambil konteks relevan (ADR, decision, riwayat task) untuk agent dengan status freshness (fresh/stale) yang ditandai. | P1 | §5 M16 memory retrieval; G37 memory.retrieve_context | AC-04 | — |
| FR-125 | Sistem harus menandai indeks dan memory sebagai stale ketika source commit berubah, lalu menginvalidasi atau me-refresh indeks. | P1 | §5 M16 stale-index invalidation; G37 memory.detect_stale_context | — | — |
| FR-126 | Sistem harus meredaksi secret dan menolak penyimpanan secret di project memory/ADR. | P1 | G37 (never store secrets); §8 project_memory | AC-15 | — |
| FR-127 | Sistem harus menyediakan riwayat conversation/task per project yang dapat dicari sebagai bagian dari knowledge. | P1 | §4.2 Knowledge (conversation/task history) | — | UC-09 |

### M17 — Provider & Model Settings (P0 provider config)

*Scope PRD §5:* API key secret storage, adapter configuration, model selection, fallback, token/cost limits.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-128 | Sistem harus menyimpan API key provider hanya di secret environment/file server-side; UI dan API hanya menampilkan status configured/missing dan masked identifier. | P0 | §5 M17 API key secret storage; §5.7; §7.3.1 | AC-15 | UC-10 |
| FR-129 | Sistem harus memisahkan business workflow dari API vendor melalui interface LLMProvider dengan implementasi aktif AnthropicClaudeProvider (Messages API via SDK resmi). | P0 | §5 M17 adapter configuration; §5.7; §7.3; NFR-08 | — | UC-10 |
| FR-130 | Sistem harus memilih model dari konfigurasi (CLAUDE_MODEL_DEFAULT/FAST/COMPLEX dan model policy) tanpa hard-code model ID atau tarif di business logic. | P0 | §5 M17 model selection; §7.3.1 | — | UC-10 |
| FR-131 | Sistem harus melakukan fallback model hanya sesuai kebijakan yang dikonfigurasi dan mencatat setiap perubahan model pada agent run. | P1 | §5 M17 fallback; §5.7 | AC-16 | UC-10 |
| FR-132 | Sistem harus menegakkan budget per task dan project (token, biaya nominal, iterasi tool, durasi, retry, concurrency); saat threshold terlampaui pemanggilan model berikutnya dihentikan, status budget_exceeded dicatat, dan pengguna diberi pilihan menghentikan atau menyesuaikan budget. | P0 | §5 M17 token/cost limits; §5.7; §7.3.4; R-05 | AC-16 | UC-10 |
| FR-133 | Sistem harus mencatat usage setiap request model (input/output/cache tokens, model, agent, task, durasi, retry count, request ID) dan menampilkan biaya dengan pembedaan actual/estimate/unavailable tanpa angka palsu. | P0 | §5.7; §7.3.3; §7.3.4; R-09 | AC-16 | UC-10 |
| FR-134 | Sistem harus menyimpan tarif model sebagai konfigurasi bertanggal dan berversi sehingga perubahan tarif tidak memerlukan perubahan kode. | P0 | §7.3.1 | AC-16 | UC-10 |
| FR-135 | Sistem harus menyediakan Claude API readiness gate (API key valid, model accessible, Messages API, streaming/usage, tool-use round-trip, timeout/cancel/error, tidak ada secret di log) dengan fixture sintetis, yang wajib lulus sebelum coding agent diaktifkan. | P0 | §7.3.6 | AC-15, AC-20 | UC-10 |

### M18 — Deployment & Environments (P2)

*Scope PRD §5:* Docker/CI configs, staging deployment, preflight, smoke tests, rollback; production gated.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-136 | Sistem harus menghasilkan dan memvalidasi konfigurasi Docker/Compose dan CI untuk project sebagai patch yang ditinjau. | P2 | §5 M18 Docker/CI configs; F30; F31 | — | UC-08 |
| FR-137 | Sistem harus menjalankan deployment preflight (build, test, validasi konfigurasi dan artefak) dan menyimpan hasilnya sebagai evidence. | P2 | §5 M18 preflight; UC-08; F32 | AC-20 | UC-08 |
| FR-138 | Sistem harus melakukan staging deployment hanya setelah approval deploy_staging yang terikat payload. | P2 | §5 M18 staging deployment; §10.4 | AC-13 | UC-08 |
| FR-139 | Sistem harus menjalankan smoke test pasca-deploy staging dan mencatat hasilnya sebagai evidence. | P2 | §5 M18 smoke tests; F32 release.run_smoke_tests | — | UC-08 |
| FR-140 | Sistem harus menyiapkan rollback plan sebelum deploy dan menyediakan aksi rollback dengan status yang tercatat. | P2 | §5 M18 rollback; UC-08; §13 Phase 7 | — | UC-08 |
| FR-141 | Sistem harus memperlakukan production deployment sebagai aksi high-risk terpisah yang memerlukan permission khusus dan approval eksplisit, dan tidak tersedia secara default. | P2 | §5 M18 production gated; §1.4; §9 Deployments; §10.4 | AC-13 | UC-08 |
| FR-142 | Sistem harus menampilkan environments, release history, status approval, dan status rollback pada halaman Deployments. | P2 | §4.2 Deployments | — | UC-08 |
| FR-143 | Sistem harus mencatat setiap deployment (environment, artefak, approval, actor, hasil) di audit trail. | P2 | §5 M18; §10.1 Audit gaps | AC-19 | UC-08 |

### M19 — Observability & Audit (P0 audit basics / P1 dashboards)

*Scope PRD §5:* Structured logs, task timeline, usage metrics, agent/tool traces, immutable security event trail.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-144 | Sistem harus menulis structured JSON logs dengan konteks request_id, correlation_id, user, dan agent_run serta redaksi secret sebelum log ditulis. | P0 | §5 M19 structured logs; §7.2.1 Logging/observability; §7.3.3 | AC-15, AC-19 | — |
| FR-145 | Sistem harus menyajikan task timeline (event berurutan: status, langkah, agent run, tool call, test, approval) yang dapat di-replay setelah reconnect. | P0 | §5 M19 task timeline; §4.2 Tasks; §7.3.3 | AC-05, AC-19 | UC-09 |
| FR-146 | Sistem harus menyimpan audit events secara append-only (actor, action, resource, result, timestamp, correlation ID) yang tidak dapat diubah atau dihapus melalui aplikasi. | P0 | §5 M19 immutable security event trail; §8 audit_events; §10.1 Audit gaps | AC-19 | — |
| FR-147 | Sistem harus mencatat audit untuk peristiwa keamanan: autentikasi, perubahan role/policy, tool denied, approval requested/decided, project delete, perubahan konfigurasi provider. | P0 | §5 M19; §5.1; §10.4 | AC-13, AC-19 | — |
| FR-148 | Sistem harus menautkan setiap task dari prompt → plan → agent run → tool call → artefak → test → acceptance melalui correlation ID dan referensi tersimpan. | P0 | NFR-06; §1.3 Reproducibility; §5 M19 agent/tool traces | AC-19 | UC-09 |
| FR-149 | Sistem harus menyediakan health/readiness checks untuk semua service (database, Redis, sandbox-manager, migrasi) yang dipakai oleh prosedur deployment. | P0 | §7.5; §13 Phase 1; AC-20 | AC-20 | — |
| FR-150 | Sistem harus menyediakan usage metrics dan dashboard observability (endpoint metrics, ringkasan durasi, tool, token, dan agent/tool traces). | P1 | §5 M19 usage metrics (P1 dashboards); §7.7 | AC-16 | UC-10 |
| FR-151 | Sistem harus mendukung instrumentasi OpenTelemetry (traces/metrics) yang dapat diaktifkan melalui konfigurasi tanpa perubahan kode. | P1 | §7.2.1 Logging/observability (OpenTelemetry-ready) | — | — |

### M20 — Admin & Governance (P1)

*Scope PRD §5:* User/role admin, quotas, policy settings, data retention, provider allowlist, feature flags.

| ID | Requirement | Prio | Sumber PRD | AC | UC |
|---|---|---|---|---|---|
| FR-152 | Sistem harus menyediakan administrasi user dan role (buat, nonaktifkan, assign role global/project) oleh admin dengan audit; pada pilot single-owner, owner pertama dibuat via CLI. | P1 | §5 M20 user/role admin; §4.2 Teams & Settings; ADR-002 | AC-01, AC-19 | — |
| FR-153 | Sistem harus menyediakan pengaturan quota (jumlah project per user, ukuran workspace, ukuran upload) yang ditegakkan saat import dan penulisan file. | P1 | §5 M20 quotas; ADR-005 | — | — |
| FR-154 | Sistem harus menyediakan pengaturan policy keamanan (mis. kewajiban plan approval default, sandbox egress off/allowlist) yang hanya dapat diubah admin dan tercatat di audit. | P1 | §5 M20 policy settings; §4.2 security policy | AC-19 | — |
| FR-155 | Sistem harus menyediakan pengaturan data retention (task events, artefak, audit) dan menjalankan pembersihan terjadwal sesuai kebijakan. | P1 | §5 M20 data retention; §8.1 | — | — |
| FR-156 | Sistem harus menyediakan provider allowlist (provider dan model yang diizinkan) yang ditegakkan saat pemilihan model. | P1 | §5 M20 provider allowlist | — | UC-10 |
| FR-157 | Sistem harus menyediakan feature flags untuk mengaktifkan kapabilitas P1/P2 secara bertahap tanpa deploy ulang. | P1 | §5 M20 feature flags; R-06 | — | — |
| FR-158 | Sistem harus menyediakan kill switch pemanggilan provider per project (provider_calls_enabled) dan menampilkan pernyataan data flow ke Claude Platform API di Settings. | P0 | §7.3.5; §15.1 #8; NFR-11 (kill switches); ADR-011 — prioritas P0 (turunan) karena §7.3.5 berlaku untuk MVP walau M20 = P1 | AC-15 | UC-10 |
| FR-159 | Sistem harus menyediakan audit log viewer dengan filter project, actor, action, dan periode bagi role berwenang. | P1 | §4.2 Teams & Settings (audit); §9 Admin & audit | AC-19 | — |

## 4. NFR → FR (referensi)

NFR PRD §11 tidak diberi ID FR; tabel ini menunjukkan FR yang ikut mewujudkannya *(turunan)*.

| NFR | FR terkait |
|---|---|
| NFR-01 Availability | FR-053, FR-054, FR-055, FR-149 |
| NFR-02 Performance | FR-035, FR-083, FR-145 (+ pagination di semua endpoint list, API_CONTRACTS §1) |
| NFR-03 Scalability | FR-053, FR-068, FR-075 (queue abstraction, workspace refs, sandbox terpisah) |
| NFR-04 Security | FR-003, FR-004, FR-043, FR-059–FR-067, FR-068–FR-075, FR-128 |
| NFR-05 Reliability | FR-053, FR-054, FR-056, FR-057 |
| NFR-06 Observability | FR-046, FR-066, FR-144, FR-145, FR-148 |
| NFR-07 Maintainability | FR-041, FR-042 (typed contracts) — selebihnya ditegakkan CI (TEST_STRATEGY §6) |
| NFR-08 Provider portability | FR-129, FR-130 |
| NFR-09 Accessibility | FR-001, FR-026 (label/keyboard) — diverifikasi lewat a11y scan (TEST_STRATEGY §2, lapisan UX) |
| NFR-10 Data durability | FR-018, FR-155 (+ backup/restore di RUNBOOK; AC-20) |
| NFR-11 Cost control | FR-064, FR-091, FR-132, FR-133, FR-158 |
| NFR-12 Localization | — (persyaratan teknis UI: i18n key, default `id`; CLAUDE.md konvensi frontend) |

## 5. Coverage

### 5.1 AC → FR

| AC | Kriteria (ringkas) | FR | Jumlah |
|---|---|---|---|
| AC-01 | Login/logout; user tanpa hak tidak dapat membuka project user lain | FR-001–FR-007, FR-017, FR-152 | 9 |
| AC-02 | Project dari blank/template; import archive aman | FR-012–FR-016 | 5 |
| AC-03 | IDE membaca, mencari, tab, edit via workspace API | FR-019–FR-021, FR-026–FR-030, FR-032 | 9 |
| AC-04 | Prompt → task dengan requirement, plan, status, ID | FR-024, FR-033, FR-034, FR-037, FR-038, FR-048–FR-051, FR-124 | 10 |
| AC-05 | Stream progress; reconnect mengambil event hilang | FR-035, FR-077, FR-083, FR-145 | 4 |
| AC-06 | Agent hanya memanggil tool yang diizinkan | FR-040–FR-044, FR-059–FR-062, FR-064, FR-067, FR-078, FR-079, FR-117 | 14 |
| AC-07 | Kode jalan di sandbox dengan timeout & resource limit | FR-065, FR-068–FR-075, FR-077, FR-078, FR-080, FR-085 | 13 |
| AC-08 | Test runner menyimpan exit code, log, status, durasi, artefak | FR-070, FR-081–FR-092, FR-094 | 14 |
| AC-09 | Diff file/hunk; user dapat menolak patch | FR-029, FR-031, FR-051, FR-095, FR-096, FR-101, FR-102, FR-113–FR-115, FR-120, FR-121 | 12 |
| AC-10 | Review findings dengan evidence; reviewer read-only | FR-042, FR-043, FR-097–FR-099 | 5 |
| AC-11 | User Proxy: status per kriteria + link evidence | FR-039, FR-049, FR-058, FR-092, FR-093, FR-103, FR-105–FR-108, FR-110 | 11 |
| AC-12 | Requirement wajib gagal → tidak accepted tanpa waiver/decision | FR-052, FR-058, FR-104, FR-111, FR-112 | 5 |
| AC-13 | R3 butuh approval valid; payload berubah → approval lama batal | FR-018, FR-043, FR-062, FR-063, FR-067, FR-101, FR-112, FR-118, FR-119, FR-138, FR-141, FR-147 | 12 |
| AC-14 | Task dapat dibatalkan; worker/sandbox dihentikan | FR-036, FR-052, FR-057, FR-082 | 4 |
| AC-15 | Secret provider tidak muncul di frontend/log/artefak | FR-002, FR-025, FR-098, FR-126, FR-128, FR-135, FR-144, FR-158 | 8 |
| AC-16 | Token/cost aktual atau 'unavailable' | FR-011, FR-046, FR-064, FR-131–FR-134, FR-150 | 8 |
| AC-17 | Restart backend tidak menghilangkan task running | FR-010, FR-053–FR-056 | 5 |
| AC-18 | Request fix atas gap → revalidation terbatas | FR-100, FR-104, FR-109 | 3 |
| AC-19 | Perubahan punya referensi task/workspace & audit | FR-005, FR-009, FR-046, FR-047, FR-066, FR-114, FR-116, FR-143–FR-148, FR-152, FR-154, FR-159 | 16 |
| AC-20 | Deployment terdokumentasi + health checks lulus | FR-135, FR-137, FR-149 | 3 |

**Cek coverage AC:** 20/20 AC memiliki ≥ 1 FR — ✅ semua AC tercakup.

### 5.2 UC → FR

| UC | Use case | FR | Jumlah |
|---|---|---|---|
| UC-01 | Create from prompt | FR-011–FR-014, FR-038, FR-048–FR-051, FR-085 | 10 |
| UC-02 | Import repository | FR-012, FR-015, FR-016, FR-019, FR-022, FR-023, FR-121, FR-123 | 8 |
| UC-03 | Modify existing feature | FR-019–FR-021, FR-024, FR-026, FR-027, FR-029, FR-034, FR-037, FR-038, FR-048, FR-050, FR-051, FR-059, FR-084, FR-085, FR-105, FR-113, FR-114 | 19 |
| UC-04 | Debug failing task | FR-024, FR-038, FR-048, FR-068, FR-077, FR-085, FR-087, FR-089–FR-091 | 10 |
| UC-05 | Run end-to-end tests | FR-068, FR-076, FR-094, FR-107 | 4 |
| UC-06 | Validate against user intent | FR-009, FR-039, FR-049, FR-058, FR-093, FR-103–FR-112 | 15 |
| UC-07 | Review and approve changes | FR-010, FR-031, FR-067, FR-095–FR-102, FR-112, FR-115, FR-119 | 14 |
| UC-08 | Prepare deployment | FR-063, FR-136–FR-143 | 9 |
| UC-09 | Resume interrupted task | FR-007, FR-008, FR-010, FR-033, FR-053–FR-057, FR-083, FR-127, FR-145, FR-148 | 13 |
| UC-10 | Control budget and providers | FR-011, FR-017, FR-064, FR-128–FR-135, FR-150, FR-156, FR-158 | 14 |

**Cek coverage UC:** 10/10 UC memiliki ≥ 1 FR — ✅ semua UC tercakup.

### 5.3 Konsistensi dengan PRD Lampiran A

Untuk setiap baris Lampiran A, setiap AC yang disebut harus didukung minimal satu FR dari modul yang disebut pada baris itu.

| Capability | Modules | UC | AC → FR pendukung (dalam modul baris) | Status |
|---|---|---|---|---|
| Create | M03/M04/M06/M08/M10/M14 | UC-01 | AC-02: FR-012–FR-016<br>AC-04: FR-024, FR-033, FR-034, FR-037, FR-038, FR-048–FR-051<br>AC-07: FR-068–FR-075<br>AC-11: FR-039, FR-049, FR-058, FR-103, FR-105–FR-108, FR-110<br>UC-01: FR-012–FR-014, FR-038, FR-048–FR-051 | ✅ |
| Debug | M04/M06/M08/M11/M12/M13/M14 | UC-04 | AC-05: FR-035, FR-077, FR-083<br>AC-08: FR-081–FR-092, FR-094<br>AC-09: FR-051, FR-095, FR-096, FR-101, FR-102<br>AC-11: FR-039, FR-049, FR-058, FR-092, FR-093, FR-103, FR-105–FR-108, FR-110<br>AC-18: FR-100, FR-104, FR-109<br>UC-04: FR-024, FR-038, FR-048, FR-077, FR-085, FR-087, FR-089–FR-091 | ✅ |
| Modify | M04/M05/M08/M09/M12/M13/M14/M15 | UC-03 | AC-03: FR-019–FR-021, FR-026–FR-030, FR-032<br>AC-06: FR-059–FR-062, FR-064, FR-067, FR-117<br>AC-09: FR-029, FR-031, FR-051, FR-095, FR-096, FR-101, FR-102, FR-113–FR-115, FR-120<br>AC-11: FR-049, FR-058, FR-092, FR-093, FR-103, FR-105–FR-108, FR-110<br>UC-03: FR-019–FR-021, FR-024, FR-026, FR-027, FR-029, FR-048, FR-050, FR-051, FR-059, FR-084, FR-085, FR-105, FR-113, FR-114 | ✅ |
| Test | M10/M11/M12/M19 | UC-05 | AC-07: FR-068–FR-075, FR-077, FR-078, FR-080, FR-085<br>AC-08: FR-070, FR-081–FR-092, FR-094<br>AC-14: FR-082<br>UC-05: FR-068, FR-076, FR-094 | ✅ |
| User acceptance | M02/M06/M13/M14 | UC-06 | AC-11: FR-039, FR-103, FR-105–FR-108, FR-110<br>AC-12: FR-104, FR-111, FR-112<br>AC-18: FR-100, FR-104, FR-109<br>UC-06: FR-009, FR-039, FR-103–FR-112 | ✅ |
| Deploy | M18/M19/M09 | UC-08 | AC-13: FR-062, FR-063, FR-067, FR-138, FR-141, FR-147<br>AC-20: FR-137, FR-149<br>UC-08: FR-063, FR-136–FR-143 | ✅ |
| Budget/provider | M17/M19/M20 | UC-10 | AC-15: FR-128, FR-135, FR-144, FR-158<br>AC-16: FR-131–FR-134, FR-150<br>UC-10: FR-128–FR-135, FR-150, FR-156, FR-158 | ✅ |
| Recovery | M08/M10/M19 | UC-09 | AC-14: FR-052, FR-057<br>AC-17: FR-053–FR-056<br>AC-19: FR-144–FR-148<br>UC-09: FR-053–FR-057, FR-145, FR-148 | ✅ |

## 6. Catatan untuk review PO

1. Semua FR adalah turunan; mohon konfirmasi bahwa pembagian isi per rentang ID dapat diterima sebelum tracker dikunci.
2. FR-158 dinaikkan ke P0 walau berada di modul M20 (P1) karena PRD §7.3.5 (pernyataan data flow, opsi disable provider call) berlaku untuk MVP.
3. FR-044 (katalog agent read-only di UI) dianggap P0 karena layar AI Agents ada di mockup MVP; interpretasi "P1 admin UI" = pengelolaan (FR-047). Mohon dikonfirmasi.
4. FR-005: PRD menyebut password recovery tanpa kanal; ADR-002 memakai token yang dibuat admin (email = P1).
5. FR-107: evidence screenshot/browser trace baru tersedia setelah E2E (FR-094, P1); sebelum itu kriteria `browser` berakhir `unverified`.

---

## 7. v2 — Perluasan platform (FR-160..FR-220)

> **Status: rancangan — belum diimplementasikan; *derived — perlu review PO*.** Bagian ini ditambahkan 21 Okt 2026 mengikuti **KONTRAK v2** (`docs/PLATFORM_EXPANSION.md` §9b — mengikat): modul M21–M24, rentang FR K1, UC-11..UC-20, AX-01..AX-11, agent K2, tools K3, tech stack K4. Isi FR diturunkan dari `PLATFORM_EXPANSION.md` §2–§7, §8b, §9b dan ADR-036..049; inferensi yang tidak tertulis eksplisit di sumber ditandai *(turunan)*. Seluruh FR v2 baru berlaku setelah portal IDE lulus gate G3. FR-001..FR-159 (§1–§6) **tidak diubah**; ID v2 tidak boleh digeser setelah review PO.

### 7.1 Konvensi v2

- **Kolom** sama dengan v1 ditambah kolom *Modul*: ID · Modul · Requirement (*"Sistem harus …"*, dapat diuji) · Prio · Sumber · AC/AX · UC.
- **Prio.** `P0ᵉ` = *P0 perluasan* (istilah K1): wajib untuk gate fase perluasan (G9 GitHub, G10 Portal AI inti); **tidak** menambah lingkup MVP IDE/G3. `P1`/`P2` bermakna sama dengan PRD §16 tetapi relatif terhadap perluasan. M21 mengikuti prioritas fitur `PLATFORM_EXPANSION.md` §4.1 (chat, riset, dokumen = P0 → `P0ᵉ`; KB, analisis, gambar, asisten kustom = P1; memori personal = P2). M23 ber-prioritas P1 di K1, sehingga fitur ber-P0 di §6.1 (profil, item, showcase, skill, CV) dipetakan ke `P1` (inti gate G12), fitur P1 tetap `P1`, analitik `P2`. M22 dan M24 seluruhnya `P0ᵉ`.
- **AC/AX.** `AX-xx` = kriteria penerimaan perluasan (`PLATFORM_EXPANSION.md` §8b + K1: AX-10, AX-11). `AC-xx` (PRD §12.1) dicantumkan bila FR v2 juga memperkuat kriteria v1 (mis. AC-13 approval R3, AC-15 secret). "—" = tidak ada kriteria penerimaan langsung.
- **Sumber.** `PE §x` = `docs/PLATFORM_EXPANSION.md`; `§9b K1–K4` = KONTRAK v2; `ADR-0xx` = `docs/DECISIONS.md`; `CONFIGURATION` = `docs/CONFIGURATION.md` (variabel perluasan).
- **Fakta vendor** (Claude Platform, GitHub) hanya dikutip sebagaimana tercatat di `PLATFORM_EXPANSION.md` (sumber §10); tidak ada fakta vendor baru di katalog ini.

### 7.2 Ringkasan modul v2 (kontrak K1)

| Modul | Nama | Rentang FR | Jumlah (K1) | Prioritas modul (K1) | Fase |
|---|---|---|---|---|---|
| M21 | Portal AI (chat, riset web, dokumen, gambar, KB, analisis data, asisten kustom) | FR-160..FR-184 | 25 | P1 (inti P10 = P0ᵉ) | P10–P11 |
| M22 | Integrasi GitHub App | FR-185..FR-196 | 12 | P0ᵉ | P9 |
| M23 | Portal Portfolio Profesional | FR-197..FR-212 | 16 | P1 | P12 |
| M24 | Capability Registry & Provider Gateway | FR-213..FR-220 | 8 | P0ᵉ | P10 |

Jumlah aktual per modul **dihitung** dari tabel di bawah — lihat §7.8.

### 7.3 M21 — Portal AI (chat, riset web, dokumen, gambar, KB, analisis data, asisten kustom) · prioritas P1 (inti P10 = P0ᵉ)

*Scope (PE/K1):* Chat & thread per Ruang, riset web bersitasi (Cepat/Mendalam), pembuatan dokumen (engine ganda), upload & tanya jawab dokumen (retrieval leksikal, ADR-046), analisis data, generate/edit gambar (ImageProvider), asisten kustom, memori personal (P2). Fase P10–P11.

| ID | Modul | Requirement | Prio | Sumber | AC/AX | UC |
|---|---|---|---|---|---|---|
| FR-160 | M21 | Sistem harus menyediakan Ruang (`assistant_spaces`) dan thread (`assistant_threads`) milik user dengan judul, pilihan model tier (`fast`/`default`/`complex`), dan label sensitivitas Ruang (`public`/`internal`/`confidential`), serta pencarian riwayat thread; Ruang/thread di luar scope user dijawab 404. | P0ᵉ | PE §4.1 (Chat & thread), §4.6; ADR-045 | AX-06 | UC-11 |
| FR-161 | M21 | Sistem harus memproses pesan user secara asinkron (`POST /assistant/threads/{id}/messages` → 202) lewat antrean Celery `assistant` dan men-stream jawaban melalui SSE (`assistant.delta`, `citation.added`, `asset.created`, `usage.updated`, `message.completed`) yang dapat dilanjutkan setelah reconnect tanpa kehilangan event persisten *(turunan dari ADR-014)*. | P0ᵉ | PE §4.2, §4.7; ADR-014, ADR-036 | AC-05 | UC-11, UC-12, UC-13 |
| FR-162 | M21 | Sistem harus mendukung edit pesan dan regenerate jawaban (`POST /assistant/messages/{id}/regenerate`) dengan versi jawaban sebelumnya tetap tersimpan *(turunan)*, serta cancel jawaban yang sedang di-stream yang benar-benar menghentikan panggilan provider, bukan hanya animasi UI. | P0ᵉ | PE §4.1, §4.7; PRD §5.4 (cancel) | AC-14 | UC-11 |
| FR-163 | M21 | Sistem harus memeriksa sebelum setiap pesan dikirim ke provider: izin RBAC `assistant:use` (dan `assistant:docs`/`assistant:image` untuk capability terkait), status & kuota capability (M24), serta meredaksi secret pada isi pesan dan lampiran; kegagalan pemeriksaan menghentikan request tanpa panggilan provider. | P0ᵉ | PE §4.2 (auth, kuota capability, redaksi secret), §7 (RBAC); CLAUDE.md #6 | AX-11, AC-15 | UC-11, UC-13, UC-14 |
| FR-164 | M21 | Sistem harus memberi label "Tanpa sumber web" pada setiap jawaban yang dihasilkan tanpa pencarian web, dan menampilkan peringatan bila jawaban tanpa sitasi memuat angka atau tanggal terkini (deteksi deterministik pola angka/tanggal *(turunan)*). | P0ᵉ | PE §4.3 #2; ADR-038 | AX-04 | UC-11 |
| FR-165 | M21 | Sistem harus merutekan permintaan Portal AI melalui I42 Assistant Router hanya ke capability yang aktif dan masih dalam kuota bagi user (hasil `capabilities.list`), mencatat keputusan routing sebagai metadata terstruktur (`submit_result`), dan menyajikan jawaban akhir sebagai teks bersitasi, bukan `submit_result`. | P0ᵉ | PE §9b K1 (AX-11), K2 (I42); ADR-044, ADR-047 | AX-11 | UC-11, UC-12, UC-13, UC-14, UC-15, UC-16 |
| FR-166 | M21 | Sistem harus menyediakan mode riset *Cepat* dan *Mendalam* yang memakai server tool Claude `web_search` dengan versi tool dipin di konfigurasi (`WEB_SEARCH_TOOL_VERSION`) dan `max_uses` per mode (default 3 dan 20 lewat `WEB_SEARCH_MAX_USES_QUICK`/`_DEEP`); jumlah pencarian per jawaban tidak pernah melebihi `max_uses` mode. | P0ᵉ | PE §4.1, §4.3 #5; ADR-038; CONFIGURATION | AX-04 | UC-12 |
| FR-167 | M21 | Sistem harus menjalankan mode *Mendalam* sebagai alur rencana riset → pencarian dan pengambilan halaman (`web_fetch`) → laporan dengan daftar sumber, serta menampilkan rencana dan progres riset kepada user selama berjalan. | P0ᵉ | PE §4.1 (Riset web bersitasi); §9b K2 (I43) | AX-04 | UC-12 |
| FR-168 | M21 | Sistem harus mengekstrak sitasi dari blok teks jawaban (`web_search_result_location`, `search_result_location`, `char_location`/`page_location`) ke `message_citations` (url/sumber, title, cited_text, searched_at, query) dan menampilkan setiap sitasi di samping klaim terkait, termasuk keterangan "dicari pada <tanggal>" untuk sumber web. | P0ᵉ | PE §4.3 #1, #4, §4.6; ADR-038, ADR-047 | AX-04, AX-10 | UC-12, UC-15 |
| FR-169 | M21 | Sistem harus menerapkan daftar allow/block domain dari policy pada setiap pencarian dan pengambilan web serta anggaran pencarian per user per bulan; bila anggaran habis, riset ditolak dengan `429 QUOTA_EXCEEDED` sementara chat tanpa web tetap dapat dipakai. | P0ᵉ | PE §4.1, §4.3 #5; ADR-038 | AX-11 | UC-12 |
| FR-170 | M21 | Sistem harus memperlakukan konten hasil `web_search`/`web_fetch` sebagai data tidak tepercaya: selama mode riset tidak ada tool R3/R4 yang tersedia bagi agent, dan instruksi di dalam konten web tidak dapat mengubah policy, izin, kuota, atau memicu approval. | P0ᵉ | PE §4.3 #3, §7 (T11); §9b K3 (`web.fetch`) | AC-06 | UC-12 |
| FR-171 | M21 | Sistem harus membuat dokumen PPTX, DOCX, XLSX, dan PDF dari prompt atau outline dengan template (template brand default ber-font sans-serif), menampilkan pratinjau thumbnail, dan menyimpan hasil di asset store (`generated_assets`) dengan checksum dan provenance (engine, model, prompt_hash). | P0ᵉ | PE §4.1 (Buat dokumen), §4.4, §4.6; §9b K3 (`assets.store`) | AX-05 | UC-13 |
| FR-172 | M21 | Sistem harus memilih engine dokumen per request: `claude_skills` (Agent Skills via code execution; file diambil via Files API) hanya untuk data non-`confidential`, dan `local_sandbox` (image `kodekantor/sandbox-docs`) wajib bila data, Ruang, atau project berlabel `confidential` atau kill switch provider aktif; engine yang dipakai dicatat di `generated_assets.engine` dan audit. | P0ᵉ | PE §4.4; ADR-039, ADR-045 | AX-06 | UC-13 |
| FR-173 | M21 | Sistem harus menyajikan unduhan file hasil (dokumen, gambar, grafik, CV) hanya melalui URL bertanda tangan berumur pendek (`ASSET_SIGNED_URL_TTL_SECONDS`, default 300 detik) dengan `Content-Disposition: attachment` dan `X-Content-Type-Options: nosniff`; URL kedaluwarsa, dipalsukan, atau milik user lain ditolak. | P0ᵉ | PE §4.7 (`GET /assets/{id}/download`); CONFIGURATION | AX-05 | UC-13, UC-14, UC-16, UC-19 |
| FR-174 | M21 | Sistem harus memvalidasi setiap file dokumen hasil dengan membukanya memakai parser format terkait (python-pptx, python-docx, openpyxl, parser PDF) sebelum ditandai siap; file yang gagal dibuka berstatus gagal dan tidak ditawarkan untuk diunduh *(turunan dari bukti AX-05)*. | P0ᵉ | PE §8b AX-05 (bukti/test); §9b K4 (Dokumen lokal) | AX-05 | UC-13 |
| FR-175 | M21 | Sistem harus menerima upload PDF, DOCX, XLSX, dan CSV ke koleksi pengetahuan (`kb_collections`) milik user dengan validasi tipe dan ukuran (batas dari konfigurasi *(turunan)*), menyimpan mime, ukuran, dan checksum di `assistant_files`, serta mengekstrak teks di sandbox (bukan di proses api/worker) dengan `extraction_status` tercatat. | P1 | PE §4.1 (Upload file), §4.6; CLAUDE.md #3 | AX-10 | UC-15, UC-16 |
| FR-176 | M21 | Sistem harus memotong teks hasil ekstraksi menjadi `kb_chunks` dengan referensi file dan offset sumber, membuat ringkasan kontekstual + kata kunci per potongan memakai Claude saat ingest, dan mengindeksnya dengan PostgreSQL full-text search (`tsvector` konfigurasi `simple`) + `pg_trgm`, tanpa embedding maupun vector database. | P1 | ADR-046; PE §4.6, §9b K4 (Pengetahuan) | AX-10 | UC-15 |
| FR-177 | M21 | Sistem harus menjawab pertanyaan atas dokumen dengan `kb.search` yang dibatasi ke koleksi yang boleh diakses user, mengirim potongan teratas sebagai blok `search_result` dengan `citations.enabled = true` (dokumen kecil ≤ ±100 halaman boleh sebagai blok `document` bersitasi), dan menampilkan sitasi ke potongan sumber; untuk data `confidential` teks potongan dikirim inline, bukan melalui Files API. | P1 | ADR-046, ADR-047; §9b K3 (`kb.search`, `kb.get_chunk`) | AX-10, AX-06 | UC-15 |
| FR-178 | M21 | Sistem harus menyediakan memori personal yang opt-in, dapat dilihat dan dihapus user, serta tidak menyimpan kategori data sensitif. | P2 | PE §4.1 (Memori personal) | — | UC-11 |
| FR-179 | M21 | Sistem harus menjalankan analisis data atas CSV/XLSX dengan kode yang dieksekusi di sandbox lokal (`analysis.run`, R2, tanpa network) atau di code execution Claude (`code.execute_remote`, hanya bila data ≠ `confidential`), lalu menampilkan tabel/grafik hasil beserta kode yang dijalankan agar dapat diperiksa. | P1 | PE §4.1 (Analisis data); §9b K2 (I46), K3 | AX-06 | UC-16 |
| FR-180 | M21 | Sistem harus menampilkan angka, tabel, dan grafik analisis sebagai hasil eksekusi hanya bila berasal dari output run yang tercatat (status sukses beserta artefaknya) dan memisahkan secara visual narasi model dari output eksekusi *(turunan)*. | P1 | PRD §4.3, §7.4 (evidence over claims); PE §4.1 | — | UC-16 |
| FR-181 | M21 | Sistem harus menghasilkan dan mengedit gambar (prompt, ukuran, jumlah, gaya, referensi) hanya melalui adapter `ImageProvider` dengan vendor yang ditetapkan ADR-040a; capability nonaktif selama `IMAGE_PROVIDER` kosong; hasil dengan `safety.flagged = true` tidak pernah ditampilkan maupun disediakan untuk diunduh. | P1 | PE §3, §4.5; ADR-040; CONFIGURATION | AX-07 | UC-14 |
| FR-182 | M21 | Sistem harus menjalankan moderasi prompt gambar (S52 `moderation.check_image_prompt`: tolak tokoh nyata, merek, karakter berhak cipta, konten seksual/minor, kekerasan) sebelum memanggil vendor, menggabungkannya dengan moderasi vendor, dan mencatat setiap penolakan beserta alasannya di audit. | P1 | PE §4.5, §7 (T16); §9b K2 (S52) | AX-07 | UC-14 |
| FR-183 | M21 | Sistem harus menegakkan kuota gambar per user per hari (`IMAGE_DAILY_QUOTA_PER_USER`, default 20), menampilkan estimasi biaya per gambar sebelum generate, dan menyimpan provenance (provider, model, hash prompt, waktu, user; metadata C2PA dipertahankan bila dikirim vendor) yang terlihat di galeri. | P1 | PE §4.5, §4.6 (`generated_assets.provenance`); CONFIGURATION | AX-07 | UC-14 |
| FR-184 | M21 | Sistem harus memungkinkan user membuat asisten kustom (system prompt, subset capability, knowledge collection) yang dapat dibagikan per role, dengan capability asisten selalu dibatasi pada irisan capability aktif dan izin pengguna yang memakainya. | P1 | PE §4.1 (Asisten kustom), §4.6 (`custom_assistants`); ADR-044 | AX-11 | UC-11 |

### 7.4 M22 — Integrasi GitHub App · prioritas P0ᵉ

*Scope (PE/K1):* GitHub App pada repo yang dipilih pengguna: instalasi, daftar repo, import/pull repo privat, push branch & PR lewat approval R3, webhook, penanganan rahasia. Fase P9.

| ID | Modul | Requirement | Prio | Sumber | AC/AX | UC |
|---|---|---|---|---|---|---|
| FR-185 | M22 | Sistem harus menyediakan alur "Hubungkan GitHub" bagi user ber-izin `integrations:github:manage`: membuat URL instalasi GitHub App dengan `state` nonce yang terikat sesi, memverifikasi `state` pada callback (`installation_id`, kode otorisasi user) dan menolak state yang tidak cocok atau kedaluwarsa *(turunan)*, lalu menyimpan `github_installations` dan `github_user_links`. | P0ᵉ | PE §5.3, §5.6, §7 (RBAC); ADR-041 | AX-01 | UC-17 |
| FR-186 | M22 | Sistem harus memakai GitHub App dengan permission minimum — Metadata (read), Contents (read & write), Pull requests (read & write), Checks/Commit statuses (read) — dan tidak meminta Workflows, Administration, Secrets, maupun Actions write; konfigurasi permission yang melebihi daftar ini membuat capability `github_sync`/`github_push_pr` gagal diaktifkan *(turunan)*. | P0ᵉ | PE §5.2; ADR-041 | AX-02 | UC-17, UC-18 |
| FR-187 | M22 | Sistem harus menampilkan daftar instalasi dan repository yang dapat diakses (`GET /integrations/github/installations`, `GET /integrations/github/repos?q=`) memakai token ber-permission metadata read, terbatas pada repo yang dipilih pengguna saat instalasi. | P0ᵉ | PE §5.5 (R0), §5.6 | — | UC-17 |
| FR-188 | M22 | Sistem harus membuat installation access token per operasi melalui JWT RS256 yang ditandatangani private key App, dibatasi ke satu repository dan permission minimum operasi tersebut, berumur ≤ 1 jam, dan tidak pernah menyimpannya di DB, log, event, artefak, sandbox, atau prompt (cache in-memory opsional ≤ 50 menit). | P0ᵉ | PE §5.1, §5.3, §5.4 #2; ADR-041; §9b K3 | AX-01 | UC-17, UC-18 |
| FR-189 | M22 | Sistem harus mengimpor repository privat sebagai project (`POST /projects` dengan `source.type = github_app`) lewat job worker antrean `integrations` yang meng-clone tanpa token di URL atau `.git/config` (kredensial hanya di environment proses git via `GIT_CONFIG_COUNT`/`http.extraheader`; remote disimpan tanpa userinfo), lalu mendeteksi stack dan membangun index; isi repo privat mengikuti kebijakan pengiriman snippet ADR-011 dan kill switch provider project. | P0ᵉ | PE §5.3, §5.4 #3, #7, §5.6; ADR-041, ADR-011 | AX-01 | UC-17 |
| FR-190 | M22 | Sistem harus menyediakan fetch/pull dari GitHub ke default branch lokal (R1, `POST /projects/{id}/git/pull`) yang tidak pernah menimpa perubahan lokal; bila terjadi konflik, perubahan remote ditempatkan di branch `kk/sync-*` dan user diberi tahu. | P0ᵉ | PE §5.5, §5.6 | — | UC-17 |
| FR-191 | M22 | Sistem harus mewajibkan approval R3 untuk push branch `kk/task-*` dan untuk membuka/memperbarui PR, dengan payload yang menampilkan dan mengikat repo, branch, `head_commit`, serta judul/deskripsi PR (pratinjau); approval sekali pakai, hanya dapat diputuskan user ber-izin `github:push`, dan perubahan `head_commit` atau isi PR membatalkan approval lama. | P0ᵉ | PE §5.3, §5.5, §7 (RBAC); ADR-041; PRD §10.4 | AX-02, AC-13 | UC-18 |
| FR-192 | M22 | Sistem harus menjalankan secret scan (gitleaks) atas diff sebelum setiap push dan memblokir push bila ada temuan sampai user menyelesaikannya. | P0ᵉ | PE §5.4 #4; §9b K3 (`github.push_branch`) | AX-02, AC-15 | UC-18 |
| FR-193 | M22 | Sistem harus menolak di ToolGateway setiap perubahan agent pada path `.github/workflows/**` (tulis di worktree maupun push), dan tidak menyediakan force push, penghapusan branch remote, merge PR, atau perubahan workflow (R4). | P0ᵉ | PE §5.2, §5.4 #5, §5.5, §7 (T13); ADR-041 | AX-02, AC-06 | UC-18 |
| FR-194 | M22 | Sistem harus menerima webhook GitHub di `POST /integrations/github/webhook` hanya bila `X-Hub-Signature-256` valid (HMAC-SHA256 atas body mentah, perbandingan constant-time), mengabaikan `X-GitHub-Delivery` yang sudah tercatat di `webhook_deliveries`, menerapkan rate limit, dan memproses event `installation`, `installation_repositories`, `push`, `pull_request` (serta `check_suite` opsional). | P0ᵉ | PE §5.2, §5.4 #6, §5.6, §7 (T14); ADR-041 | AX-03 | UC-17, UC-18 |
| FR-195 | M22 | Sistem harus menyimpan private key GitHub App hanya sebagai secret file read-only yang di-mount **hanya** ke container `worker-integrations` (ADR-053; `api` tidak memegangnya; tidak di DB, log, sandbox, atau prompt), dan meredaksi pola token GitHub (`ghs_`, `ghu_`, `github_pat_`) di semua jalur keluar. | P0ᵉ | PE §5.4 #1, §7 (T12); ADR-053; SECURITY_MODEL Lampiran | AX-01, AC-15 | UC-17 |
| FR-196 | M22 | Sistem harus menampilkan status PR dan status CI (checks/commit statuses) pada review task (`GET /tasks/{id}/github/pr`), dengan judul/deskripsi PR disusun E29 dan eksekusi push/PR oleh J48 (deterministik); merge PR tetap dilakukan manusia di GitHub dan branch protection dihormati. | P0ᵉ | PE §5.4 #5, §5.5, §5.6; §9b K2 (J48, promosi E29) | AX-02 | UC-18 |

### 7.5 M23 — Portal Portfolio Profesional · prioritas P1

*Scope (PE/K1):* Profil, pengalaman/pendidikan, showcase proyek (manual/GitHub/KodeKantor), skill & sertifikat, CV ATS, asisten lamaran, situs publik, publikasi R3, AI draft-only. Fase P12.

| ID | Modul | Requirement | Prio | Sumber | AC/AX | UC |
|---|---|---|---|---|---|---|
| FR-197 | M23 | Sistem harus menyediakan profil portfolio (nama, headline, ringkasan, lokasi, kontak, foto) yang hanya dapat diubah user ber-izin `portfolio:edit`, dengan kontak (email/telepon) default non-publik dan hanya tampil publik bila ditandai eksplisit. | P1 | PE §6.1 (Profil), §6.3, §6.4, §7 (RBAC) | AX-08 | UC-19 |
| FR-198 | M23 | Sistem harus menyimpan pengalaman dan pendidikan sebagai `portfolio_items` (timeline, pencapaian per peran) dengan `visibility` (`draft`/`public`) dan `source` (`manual`/`github`/`kodekantor`/`ai_draft`). | P1 | PE §6.1, §6.4 | — | UC-19 |
| FR-199 | M23 | Sistem harus memungkinkan user menambah, mengubah, dan menghapus showcase proyek secara manual (`GET/POST/PATCH/DELETE /portfolio/items`). | P1 | PE §6.1 (Showcase proyek), §6.4 | — | UC-19 |
| FR-200 | M23 | Sistem harus mengimpor proyek dari repository GitHub terhubung (README, bahasa, topik, bintang, tautan) melalui `POST /portfolio/import/github`; untuk repo privat hanya field metadata yang dipilih user yang disimpan, tanpa isi file. | P1 | PE §6.1, §6.4; §9b K3 (`github.get_repo_summary`) | AX-08 | UC-19 |
| FR-201 | M23 | Sistem harus mengimpor proyek dari project KodeKantor (stack, ringkasan, hasil test) melalui `POST /portfolio/import/project`, dengan hasil test hanya diambil dari `test_runs` nyata project tersebut *(turunan)*. | P1 | PE §6.1, §6.4; PRD §7.4 | — | UC-19 |
| FR-202 | M23 | Sistem harus menyediakan matriks skill (level, bukti/tautan) dan daftar sertifikat (nama, tanggal, ID) sebagai item portfolio. | P1 | PE §6.1 (Skill & sertifikat) | — | UC-19 |
| FR-203 | M23 | Sistem harus memastikan konten portfolio buatan AI (`portfolio.draft_item`) selalu tersimpan sebagai draft dengan `source = ai_draft` dan penanda AI per kalimat sampai user menyetujuinya; konten bertanda AI yang belum disetujui tidak dapat berstatus `public` atau ikut dipublikasikan. | P1 | PE §6.2, §7 (T17); ADR-042; §9b K3 | AX-09 | UC-19 |
| FR-204 | M23 | Sistem harus mencegah AI mengarang pencapaian, angka, sertifikat, atau testimoni: setiap angka/pencapaian dalam draft AI harus merujuk data milik user (item, repo, test run) dan klaim tanpa rujukan ditandai "perlu verifikasi" *(turunan)*; testimoni hanya dapat berasal dari input user yang menyertakan sumber. | P1 | PE §6.2; ADR-042 | AX-09 | UC-19 |
| FR-205 | M23 | Sistem harus membuat snapshot versi portfolio (`portfolio_versions`: snapshot jsonb + pembuat) yang dirujuk oleh setiap publikasi, sehingga isi yang dipublikasikan dapat ditelusuri ke versi tertentu. | P1 | PE §6.4 | AX-08 | UC-19, UC-20 |
| FR-206 | M23 | Sistem harus menghasilkan CV DOCX/PDF dari item terpilih memakai template ramah ATS (sans-serif) melalui DocEngine (`docs.render_local`), menyimpan versi CV per lamaran sebagai aset, dan menyediakannya lewat URL unduhan bertanda tangan. | P1 | PE §6.1 (Generator CV), §6.4 (`POST /portfolio/cv`); §9b K2 (K50) | AX-05 | UC-19 |
| FR-207 | M23 | Sistem harus menyediakan asisten lamaran: URL lowongan diambil dengan `web.fetch` (konten = data tak tepercaya), dianalisis kecocokannya dengan profil disertai sitasi ke teks lowongan, lalu menghasilkan saran penyesuaian CV dan cover letter berstatus draft yang wajib ditinjau user. | P1 | PE §6.1 (Asisten lamaran); §9b K2 (K50); ADR-047 | AX-09, AX-04 | UC-19 |
| FR-208 | M23 | Sistem harus membangun situs portfolio statis di sandbox (`portfolio.build_site`, R2, Jinja2) dari versi tertentu, menyediakan pratinjau dan SEO meta, dengan target publikasi `portfolio.<domain>` atau GitHub Pages sesuai keputusan PO. | P1 | PE §6.1 (Situs publik), §9 #3; §9b K3, K4 | AX-08 | UC-20 |
| FR-209 | M23 | Sistem harus menjalankan pemeriksaan privasi sebelum publish (`portfolio.privacy_check`): email/telepon hanya bila ditandai publik, secret scan, dan konfirmasi bahwa tidak ada isi repo privat selain metadata terpilih; setiap temuan memblokir publish sampai diselesaikan. | P1 | PE §6.3, §7 (T18) | AX-08 | UC-20 |
| FR-210 | M23 | Sistem harus memperlakukan publikasi portfolio sebagai aksi R3: `POST /portfolio/publish` membuat approval dengan payload hash snapshot konten (HTML + daftar aset), hanya user ber-izin `portfolio:publish` yang dapat memutuskan, perubahan snapshot mewajibkan approval baru, dan hasilnya tercatat di `portfolio_publications` (target, url, version_id, approval_id, status). | P1 | PE §6.3, §6.4, §7; ADR-042 | AX-08, AC-13 | UC-20 |
| FR-211 | M23 | Sistem harus menyediakan analitik kunjungan situs portfolio tanpa cookie pelacak pihak ketiga. | P2 | PE §6.1 (Analitik kunjungan) | — | UC-20 |
| FR-212 | M23 | Sistem harus menyediakan ekspor dan penghapusan data portfolio milik user; sebelum penghapusan, sistem menampilkan publikasi aktif yang terdampak *(turunan)*. | P1 | ADR-045 (ekspor & hapus data pengguna) | — | UC-19, UC-20 |

### 7.6 M24 — Capability Registry & Provider Gateway · prioritas P0ᵉ

*Scope (PE/K1):* Registry capability (provider, tools, risiko, budget, izin, flag), routing provider per capability, server tool via ToolGateway, budget & kill switch per capability, usage & biaya, data-flow/ZDR, isolasi kunci vendor & antrean. Fase P10.

| ID | Modul | Requirement | Prio | Sumber | AC/AX | UC |
|---|---|---|---|---|---|---|
| FR-213 | M24 | Sistem harus memuat Capability Module Registry dari `config/capabilities.yaml` (`CAPABILITIES_CONFIG_PATH`) + tabel `capability_settings`, memvalidasi bahwa setiap modul mendefinisikan provider, tools, risiko, budget default, izin, dan feature flag, dan gagal start dengan pesan aman bila registry tidak valid *(turunan, analog FR-041)*. | P0ᵉ | ADR-044; PE §2; CONFIGURATION | AX-11 | UC-11, UC-12, UC-13, UC-14, UC-15, UC-16 |
| FR-214 | M24 | Sistem harus mengekspos hanya modul capability yang aktif bagi user: `GET /capabilities` mengembalikan modul aktif beserta kuota tersisa, UI hanya menampilkan modul tersebut, dan request ke capability nonaktif atau tanpa izin ditolak tanpa memanggil provider. | P0ᵉ | ADR-044; PE §2, §4.7 | AX-11 | UC-11, UC-12, UC-13, UC-14, UC-15, UC-16 |
| FR-215 | M24 | Sistem harus merutekan provider per capability dari `config/providers.yaml` berversi (`text` dan `web_research`: anthropic; `doc_generate`: default `claude_skills`, sensitif `local_sandbox`; `image_generate`: vendor ADR-040a), dan hanya mengaktifkan vendor baru setelah ADR serta contract test interface (`LLMProvider`, `ImageProvider`, `DocumentEngine`) lulus. | P0ᵉ | PE §3; ADR-037; PRD §7.7 | AX-06, AX-07, AX-11 | UC-13, UC-14 |
| FR-216 | M24 | Sistem harus melewatkan server tool provider (`web.search`, `web.fetch`, `code.execute_remote`, `docs.render_skills`) melalui allowlist ToolGateway (izin, kuota, audit) dan mencatatnya di `tool_calls` dengan `executor: provider`, serta menegakkan field permission profile v2 (`network: provider_web`, `portfolio: draft`, `data_sensitivity_max`) sehingga tool provider ditolak untuk data di atas sensitivitas yang diizinkan. | P0ᵉ | PE §9b K2 (catatan *), K3; ADR-048 | AX-06, AX-11, AC-06 | UC-12, UC-13, UC-16 |
| FR-217 | M24 | Sistem harus menegakkan budget per capability per user per bulan serta kill switch admin per capability; budget habis menghasilkan `429 QUOTA_EXCEEDED` sebelum panggilan provider, dan kill switch membuat capability tidak tersedia bagi semua user sampai diaktifkan kembali. | P0ᵉ | PE §7 (budget per capability, kill switch admin); ADR-044 | AX-11 | UC-11, UC-12, UC-13, UC-14, UC-15, UC-16 |
| FR-218 | M24 | Sistem harus mencatat usage per capability di `capability_usage` (token, jumlah pencarian web, jumlah gambar, detik eksekusi) dengan biaya estimasi dari `pricing.yaml` per vendor dan `cost_status` (`estimated`/`partial`/`unavailable`), serta menampilkan "unavailable" bila biaya tidak diketahui, tanpa angka palsu. | P0ᵉ | PE §3, §4.6; ADR-025 | AC-16 | UC-11, UC-12, UC-13, UC-14, UC-15, UC-16 |
| FR-219 | M24 | Sistem harus menampilkan di Settings tabel data-flow per capability (data apa dikirim ke vendor mana, status ZDR — konten Agent Skills dan Files API tidak tercakup ZDR —, retensi lokal per jenis aset) beserta efek label sensitivitas (`public`/`internal`/`confidential`) per Ruang/project. | P0ᵉ | ADR-045, ADR-039, ADR-046; PE §4.4, §7 (T15) | AX-06 | UC-13, UC-15 |
| FR-220 | M24 | Sistem harus menyimpan API key setiap vendor hanya di worker antrean terkait (`assistant`, `media`) dan tidak pernah di browser, log, sandbox, atau DB, serta memisahkan antrean Celery `ide`, `assistant`, `media`, `integrations` sehingga beban dokumen/gambar tidak menahan task IDE. | P0ᵉ | PE §1, §3; ADR-004, ADR-036 | AC-15 | UC-14 |

### 7.7 Catatan untuk review PO (v2)

1. Semua FR v2 adalah turunan dari rancangan berstatus *Proposed*; konfirmasi PO diperlukan sebelum fase P9 dimulai.
2. `P0ᵉ` adalah prioritas perluasan, bukan P0 MVP; gate G3 tidak bergantung pada FR-160..FR-220.
3. FR-174 (validasi file hasil dengan parser) diturunkan dari kolom bukti AX-05; FR-180 (angka analisis hanya dari run tercatat) diturunkan dari prinsip *evidence over claims* PRD §4.3/§7.4 — mohon konfirmasi sebagai requirement.
4. FR-184 (asisten kustom) dan FR-178/FR-211 (P2) belum memiliki work package di WBS; FR-212 (ekspor/hapus data portfolio) diturunkan dari ADR-045 — **diperbarui 21 Okt 2026:** FR-212 kini dipetakan ke WP-12.1.
5. ~~Nama `action_type` approval untuk push/PR GitHub dan publikasi portfolio belum didefinisikan.~~ **Selesai 21 Okt 2026:** `github_push_pr` dan `portfolio_publish` ditetapkan di ADR-056 dan `API_CONTRACTS.md` §12.6.
6. ~~`PLATFORM_EXPANSION.md` §2 (baris `kb_qa`) dan WBS WP-11.3 masih menyebut pgvector/ADR-043.~~ **Selesai 21 Okt 2026 (Lead):** keduanya diselaraskan ke ADR-046; FR-176/FR-177 tidak berubah.

### 7.8 Coverage v2 (dihitung)

Tabel di bawah **dibangkitkan oleh skrip** yang mem-parse baris tabel §7.3–§7.6 file ini (bukan diketik tangan). Snippet verifikasi ulang ada di akhir bagian ini.

#### 7.8.1 Modul → jumlah FR

| Modul | Rentang (K1) | Jumlah K1 | Jumlah terhitung | P0ᵉ | P1 | P2 | Sesuai K1 |
|---|---|---|---|---|---|---|---|
| M21 | FR-160..FR-184 | 25 | 25 | 15 | 9 | 1 | ✅ |
| M22 | FR-185..FR-196 | 12 | 12 | 12 | 0 | 0 | ✅ |
| M23 | FR-197..FR-212 | 16 | 16 | 0 | 15 | 1 | ✅ |
| M24 | FR-213..FR-220 | 8 | 8 | 8 | 0 | 0 | ✅ |
| **Total** | FR-160..FR-220 | **61** | **61** | **35** | **24** | **2** | ✅ |

#### 7.8.2 AX → FR

| AX | Kriteria / use case | FR | Jumlah |
|---|---|---|---|
| AX-01 | Repo privat diimpor lewat GitHub App tanpa token tersimpan di DB/log/`.git/config` | FR-185, FR-188, FR-189, FR-195 | 4 |
| AX-02 | Push branch & PR hanya setelah approval R3 terikat `head_commit`; `.github/workflows/**` tak dapat diubah agent | FR-186, FR-191–FR-193, FR-196 | 5 |
| AX-03 | Webhook bertanda tangan salah ditolak; delivery ganda diabaikan | FR-194 | 1 |
| AX-04 | Setiap klaim dari web bersitasi + tanggal pencarian; jawaban tanpa sumber berlabel | FR-164, FR-166–FR-168, FR-207 | 5 |
| AX-05 | Dokumen PPT/DOCX/XLSX/PDF valid dapat diunduh lewat URL bertanda tangan | FR-171, FR-173, FR-174, FR-206 | 4 |
| AX-06 | Data `confidential` memakai engine lokal; tidak ada panggilan Skills | FR-160, FR-172, FR-177, FR-179, FR-215, FR-216, FR-219 | 7 |
| AX-07 | Generate gambar mematuhi moderasi, kuota, dan menyimpan provenance | FR-181–FR-183, FR-215 | 4 |
| AX-08 | Publikasi portfolio butuh approval R3 + cek privasi; repo privat hanya metadata terpilih | FR-197, FR-200, FR-205, FR-208–FR-210 | 6 |
| AX-09 | AI di portfolio hanya membuat draft bertanda; tidak mengarang pencapaian/testimoni | FR-203, FR-204, FR-207 | 3 |
| AX-10 | Jawaban KB menampilkan sitasi ke potongan dokumen sumber | FR-168, FR-175–FR-177 | 4 |
| AX-11 | Router hanya memanggil capability yang aktif & dalam kuota | FR-163, FR-165, FR-169, FR-184, FR-213–FR-217 | 9 |

#### 7.8.3 UC-11..UC-20 → FR

| UC | Kriteria / use case | FR | Jumlah |
|---|---|---|---|
| UC-11 | Tanya jawab umum | FR-160–FR-165, FR-178, FR-184, FR-213, FR-214, FR-217, FR-218 | 12 |
| UC-12 | Riset web bersitasi | FR-161, FR-165–FR-170, FR-213, FR-214, FR-216–FR-218 | 12 |
| UC-13 | Buat dokumen (PPT/Word/Excel/PDF) | FR-161, FR-163, FR-165, FR-171–FR-174, FR-213–FR-219 | 14 |
| UC-14 | Buat/ubah gambar | FR-163, FR-165, FR-173, FR-181–FR-183, FR-213–FR-215, FR-217, FR-218, FR-220 | 12 |
| UC-15 | Tanya jawab dokumen sendiri | FR-165, FR-168, FR-175–FR-177, FR-213, FR-214, FR-217–FR-219 | 10 |
| UC-16 | Analisis data | FR-165, FR-173, FR-175, FR-179, FR-180, FR-213, FR-214, FR-216–FR-218 | 10 |
| UC-17 | Hubungkan GitHub & impor repo privat | FR-185–FR-190, FR-194, FR-195 | 8 |
| UC-18 | Push branch & buka PR | FR-186, FR-188, FR-191–FR-194, FR-196 | 7 |
| UC-19 | Kelola portfolio & CV | FR-173, FR-197–FR-207, FR-212 | 13 |
| UC-20 | Publikasikan situs portfolio | FR-205, FR-208–FR-212 | 6 |

#### 7.8.4 AC v1 yang diperkuat FR v2

| AC | FR v2 |
|---|---|
| AC-05 | FR-161 |
| AC-06 | FR-170, FR-193, FR-216 |
| AC-13 | FR-191, FR-210 |
| AC-14 | FR-162 |
| AC-15 | FR-163, FR-192, FR-195, FR-220 |
| AC-16 | FR-218 |

#### 7.8.5 Hasil cek

- Jumlah FR v2: **61** (target 61), rentang kontigu FR-160..FR-220 tanpa duplikat: ✅
- Jumlah per modul sesuai K1 (25/12/16/8): ✅
- **Cek coverage AX:** 11/11 AX memiliki ≥ 1 FR — ✅ semua AX tercakup.
- **Cek coverage UC:** 10/10 UC (UC-11..UC-20) memiliki ≥ 1 FR — ✅ semua UC tercakup.
- FR-001..FR-159 (§1–§6) identik byte-per-byte dengan versi sebelum penambahan §7 ✅ (dicek skrip pembangkit).

Snippet verifikasi ulang (jalankan dari root repo):

```python
import re, collections
t = open('docs/FR_CATALOG.md', encoding='utf-8').read()
rows = re.findall(r'^\| (FR-\d{3}) \| (M2[1-4]) \| .+? \| (P0ᵉ|P1|P2) \| .+? \| (.+?) \| (.+?) \|$', t, re.M)
print(len(rows), collections.Counter(m for _, m, *_ in rows))
for k in [f'AX-{i:02d}' for i in range(1, 12)] + [f'UC-{i}' for i in range(11, 21)]:
    assert any(k in (ax + ', ' + uc).split(', ') for *_, ax, uc in rows), k
print('coverage OK')
```

---

## 8. Perluasan v2.1 — SDLC end-to-end, Developer Workspace v2, skala S/M/L (FR-221..FR-258)

> **Status: rancangan / Proposed — belum diimplementasikan; *derived — perlu review PO*.** Bagian ini ditambahkan 22 Okt 2026 mengikuti **KONTRAK v2.1** (`docs/SDLC_SCALE_DESIGN.md` §6 — mengikat): modul M25–M27 dan FR-221..FR-258 (K5), AX-12..AX-18 (K6), NFR-23..NFR-27 (K6b), ADR-057..062 (K7), fase P13/P14 + gate G13/G14 (K8), aktivasi agent (K9), tool (K10), delta data/API/konfigurasi (K11), ancaman T19–T23 (K12). Makna dan prioritas setiap FR **identik** dengan tabel K5; pernyataan boleh sedikit lebih presisi (mis. menyebut nama variabel/tool dari K10/K11) tanpa mengubah cakupan. FR-001..FR-220 (§1–§7) **tidak diubah**; ID v2.1 tidak boleh digeser setelah review PO. Semua angka kapasitas/waktu yang berasal dari kontrak tetap **[Menebak]** sampai diukur di gate G13/G14.

### 8.1 Konvensi v2.1

- **Kolom** sama dengan §7: ID · Modul · Requirement (*"Sistem harus …"*, dapat diuji) · Prio · Sumber · AC/AX · UC.
- **Prio.** Mengikuti K5. `P0ᵉ` = wajib untuk gate fase perluasan (di v2.1: G13), **bukan** lingkup MVP/G3. M25: P1, kecuali merge queue (FR-225) dan traceability (FR-231) = `P0ᵉ` fase P13; release/environment/CODEOWNERS/insiden = P2. M26: `P0ᵉ` untuk FR-235..FR-237, P1/P2 sisanya. M27: P1 untuk kemampuan tier M, P2 untuk tier L.
- **AC/AX.** `AX-12..AX-18` dari K6 (setiap FR membawa AX persis seperti kolom AX K5). `AC-xx` (PRD §12.1) ditambahkan bila FR v2.1 juga memperkuat kriteria v1 (pola §7.1). "—" = tidak ada kriteria penerimaan langsung.
- **Sumber.** `SSD §x` = `docs/SDLC_SCALE_DESIGN.md`; `K5–K12` = KONTRAK v2.1; `K13` = amandemen Lead (22 Okt 2026, SSD §6 K13); `ADR-0xx` = `docs/DECISIONS.md` (ADR-057..062 berstatus *Proposed*, tertulis di `DECISIONS.md`); `FR-xxx` = FR v1/v2 yang dilanjutkan.
- **Use case baru (v2.1):** UC-21..UC-24 (definisi lengkap di `docs/SYSTEM_SPEC.md` §12.4).

| UC | Use case | Aktor utama | Fase |
|---|---|---|---|
| UC-21 | Edit, jalankan & uji kode secara manual di IDE | Developer | P13 (LSP/debugger P14) |
| UC-22 | Kelola program/epic/story & dekomposisi | Program owner / tech lead | P13 |
| UC-23 | Merge queue, CI & release | Tech lead / reviewer, DevOps | P13 (release train P13, P2) |
| UC-24 | Operasi tier M/L (node executor, SSO, tenant) | Admin platform / OPS | P13 (tier M), P14 (tier L) |

### 8.2 Ringkasan modul v2.1 (kontrak K5)

| Modul | Nama | Rentang FR | Jumlah (K5) | Prioritas modul (K5) | Fase |
|---|---|---|---|---|---|
| M25 | SDLC Program & Delivery Management | FR-221..FR-234 | 14 | P1 (merge queue & traceability = P0ᵉ fase P13) | P13 |
| M26 | Developer Workspace v2 — edit, run, test, debug manual | FR-235..FR-244 | 10 | P0ᵉ untuk FR-235..237; P1/P2 sisanya | P13 (LSP/debugger P14) |
| M27 | Scale & Enterprise Platform (tier S/M/L) | FR-245..FR-258 | 14 | P1 tier M; P2 tier L | P13 (tier M), P14 (tier L) |

Jumlah aktual per modul **dihitung** dari tabel di bawah — lihat §8.7.

### 8.3 M25 — SDLC Program & Delivery Management · prioritas P1 (merge queue & traceability = P0ᵉ) · fase P13

*Scope (SSD §2–§3):* hierarki Program → Epic → Story → Task, requirement & AC per story, dekomposisi A03 (`ProgramPlan`), penjadwalan paralel sesuai DAG, merge queue per repo + status CI, papan SDLC 8 fase, artefak desain, release train, environment registry, traceability, dashboard program, CODEOWNERS, insiden → story.

| ID | Modul | Requirement | Prio | Sumber | AC/AX | UC |
|---|---|---|---|---|---|---|
| FR-221 | M25 | Sistem harus menyediakan hierarki work item Program → Epic → Story → Task (`work_items` bertipe `program`/`epic`/`story` dengan `parent_id`, pemilik, status, dan prioritas; task tertaut lewat `tasks.work_item_id`), dengan task tetap menjadi unit eksekusi agent di satu worktree satu repo dan tetap valid tanpa story pada tier S. | P1 | SSD §3.1; K5; ADR-057 | AX-14 | UC-22 |
| FR-222 | M25 | Sistem harus menyimpan requirement baseline berversi dan acceptance criteria per story (disusun A02, disetujui user) serta menghitung status story, epic, dan program secara deterministik dari status task dan status AC, tanpa status yang diketik LLM; story berstatus `accepted` hanya bila semua task-nya `completed` (yang pada repo ber-merge-queue berarti perubahan sudah lolos merge queue) dan semua AC wajib `passed` atau `waived_by_user`. | P1 | SSD §3.1; ADR-057; CLAUDE.md #1, #4 | AX-14, AX-15, AC-12 | UC-22 |
| FR-223 | M25 | Sistem harus menampilkan usulan dekomposisi A03 berupa `ProgramPlan` (epic, story, task, DAG dependensi, repo per task) sebagai diff rencana terhadap pohon work item yang ada, memvalidasinya secara deterministik (kunci unik, DAG tanpa siklus, repo terdaftar di project), dan hanya membuat work item/task setelah user menyetujui (aksi user biasa, bukan approval R3). | P1 | SSD §3.1; K9; K10 (`sdlc.propose_plan`) | AX-14 | UC-22 |
| FR-224 | M25 | Sistem harus menjadwalkan task secara paralel hanya bila semua dependensinya di DAG telah selesai, dalam batas kapasitas tier (`MAX_ACTIVE_TASKS` per node) dan budget program, dengan setiap task berjalan di worktree terpisah yang tidak pernah dipakai bersama (ADR-016). | P1 | SSD §3.2 #1–#2; ADR-016 | AX-14 | UC-22 |
| FR-225 | M25 | Sistem harus menyediakan merge queue per repo yang menyerialisasi apply ke branch target: hanya entri dengan approval `apply_changes` yang valid yang diproses; setiap entri di-rebase/merge ke head terbaru, menjalankan profil test wajib (`MERGE_QUEUE_REQUIRED_PROFILES`) dan status CI bila diwajibkan, lalu di-merge bila lulus; entri yang gagal keluar antrean dengan bukti tersimpan, task kembali `needs_changes`, dan branch target tidak berubah; bila diff task berubah karena rebase/konflik, approval baru wajib diminta. | P0ᵉ | SSD §3.2 #3–#4; ADR-057 | AX-14, AC-09 | UC-23 |
| FR-226 | M25 | Sistem harus menampilkan papan SDLC per story dengan 8 fase (requirement, desain, implementasi, test, run/preview, review & acceptance, release, operate) beserta bukti tersimpan per fase, dan menolak menandai fase selesai bila bukti fase tersebut tidak ada. | P1 | SSD §2 | AX-15 | UC-22 |
| FR-227 | M25 | Sistem harus menghasilkan artefak desain (ADR, diagram, skema API/DB) sebagai patch dokumen yang direview lewat diff (agent A05, B12, G35) dan menautkannya ke story sebagai bukti fase desain. | P1 | SSD §2 fase 2; K9 | — | UC-22 |
| FR-228 | M25 | Sistem harus mengelompokkan story `accepted` ke dalam release, membangkitkan changelog secara deterministik dari task dan commit, dan memperlakukan release notes sebagai patch dokumen yang direview. | P2 | SSD §3.3 | AX-15 | UC-23 |
| FR-229 | M25 | Sistem harus menyediakan registry environment (dev, staging, production) per project dengan promosi bertahap; promosi ke production tetap aksi R3 yang memerlukan approval dan izin `deploy:production` (FR-141). | P2 | SSD §3.3; FR-141 | AC-13 | UC-23 |
| FR-230 | M25 | Sistem harus membaca status CI eksternal (GitHub checks, lewat `ci.get_checks`) sebagai bukti tambahan dan memblokir entri merge queue bila policy project mewajibkan CI lulus sementara status CI belum `success` — dengan aturan K13: CI hanya berlaku untuk `head_commit` yang sudah di-push dan rebase fast-forward (selain itu entri memakai profil lokal wajib saja), penantian CI dibatasi timeout sehingga entri tidak pernah tertahan tanpa batas, dan CI pasca-merge yang gagal di default branch membuat story perbaikan otomatis (jalur FR-234). | P1 | SSD §3.2 #3; M22; K10; K13 (OI-23) | AX-14 | UC-23 |
| FR-231 | M25 | Sistem harus menyediakan traceability requirement → story → task → commit → test run → acceptance report → release yang dibangun hanya dari rekaman tersimpan dan dapat diekspor sebagai CSV, JSON, dan PDF untuk audit. | P0ᵉ | SSD §3.3 | AX-15, AC-19 | UC-22, UC-23 |
| FR-232 | M25 | Sistem harus menampilkan dashboard program: progres per epic, task gagal/blocked, lead time, panjang dan waktu antrean (task & merge queue), serta biaya per program/epic dengan status biaya yang jujur (`estimated`/`partial`/`unavailable`). | P1 | SSD §3; ADR-025 | — | UC-22 |
| FR-233 | M25 | Sistem harus menerapkan code ownership per path (CODEOWNERS) pada tier M+: apply/merge perubahan yang menyentuh path berpemilik memerlukan keputusan reviewer pemilik path tersebut. | P2 | SSD §1 (tier M/L) | — | UC-23 |
| FR-234 | M25 | Sistem harus mengubah insiden dari log/alert (F33/F34) menjadi story perbaikan yang menautkan bukti asalnya (log, alert, run). | P2 | SSD §2 fase 8 | — | UC-22 |

### 8.4 M26 — Developer Workspace v2 — edit, run, test, debug manual · prioritas P0ᵉ (FR-235..237) / P1 / P2 · fase P13 (LSP/debugger P14)

*Scope (SSD §4):* tiga mode kerja IDE (edit repo, pantau task, take-over task), operasi file lengkap dengan version check & undo, matriks konflik (diff 3-way), format/lint on save, run & test oleh user, terminal interaktif sandbox (ADR-059, default nonaktif), language server & debugger di sandbox, Git manual.

| ID | Modul | Requirement | Prio | Sumber | AC/AX | UC |
|---|---|---|---|---|---|---|
| FR-235 | M26 | Sistem harus memungkinkan user membuat, mengedit, me-rename/memindah, dan menghapus file/folder project di IDE melalui workspace API dengan version check (`expected_sha256`), audit untuk buat/rename/hapus, dan undo per sesi editor. | P0ᵉ | SSD §4.1; FR-026..FR-032; ADR-058; K10 (`workspace.*`) | AX-12, AC-03 | UC-21 |
| FR-236 | M26 | Sistem harus menyediakan take-over task: task berhenti di checkpoint dan berstatus `interrupted` dengan `status_reason=user_takeover`, user mengedit worktree task, perubahan dicommit atas nama user di branch task, bukti test & User Proxy sebelumnya ditandai stale, dan apply diblokir sampai test dan User Proxy dijalankan ulang. | P0ᵉ | SSD §4.1; ADR-058; ADR-031 | AX-12, AC-11 | UC-21 |
| FR-237 | M26 | Sistem harus mencegah agent menimpa perubahan user yang belum disimpan atau lebih baru — agent tidak berjalan selama take-over dan penulisan agent ke worktree yang di-take-over ditolak — serta menampilkan setiap konflik versi sebagai diff 3-way (milik user / server / basis) untuk diselesaikan user, tanpa overwrite diam-diam. | P0ᵉ | SSD §4.2; FR-029 | AX-12, AC-09 | UC-21 |
| FR-238 | M26 | Sistem harus menjalankan format dan lint saat simpan (opsional per project) memakai profil sandbox tetap dan menampilkan hasil formatter sebagai patch yang dapat diterima atau ditolak user. | P1 | SSD §4.2 | — | UC-21 |
| FR-239 | M26 | Sistem harus memungkinkan user menjalankan project dari IDE dengan profil `run.*` per stack di sandbox, dengan output di-stream dan tersimpan, serta dev server dilayani lewat preview terisolasi (ADR-008). | P1 | SSD §4.3; FR-076; ADR-008 | AX-13, AC-07 | UC-21 |
| FR-240 | M26 | Sistem harus memungkinkan user menjalankan semua test, satu file test, atau satu test (`tests.run_selected`) di sandbox; hasilnya tersimpan sebagai `test_runs` dengan `initiated_by = user` dan dapat dipakai sebagai bukti story/task. | P1 | SSD §4.3; K10 | AX-13, AC-08 | UC-21 |
| FR-241 | M26 | Sistem harus menyediakan terminal interaktif hanya untuk user manusia (bukan agent) sebagai sesi PTY di container sandbox project dengan network off default, batas CPU/RAM/PID sama dengan sandbox, durasi maksimum (`TERMINAL_SESSION_MAX_MINUTES`) dan idle timeout (`TERMINAL_IDLE_MINUTES`), serta audit input/output teredaksi; fitur nonaktif secara default (`TERMINAL_ENABLED=false`) dan diaktifkan admin per project. | P1 | SSD §4.3; ADR-059 | AX-13, AC-07 | UC-21 |
| FR-242 | M26 | Sistem harus menyediakan language intelligence (autocomplete, go-to-definition, diagnostics) lewat language server yang berjalan di sandbox untuk stack yang didukung, tanpa language server di proses api/worker. | P2 | SSD §4.3 | — | UC-21 |
| FR-243 | M26 | Sistem harus menyediakan debugger (breakpoint, step, inspect) lewat Debug Adapter Protocol yang berjalan di sandbox untuk Python dan Node. | P2 | SSD §4.3 | — | UC-21 |
| FR-244 | M26 | Sistem harus menyediakan Git manual untuk user (stage, commit, branch, diff, discard) pada repo lokal project; push ke remote hanya lewat jalur R3 GitHub App (FR-191). | P1 | SSD §4.3; FR-113..FR-120; FR-191 | AX-12, AC-13 | UC-21 |

### 8.5 M27 — Scale & Enterprise Platform (tier S/M/L) · prioritas P1 (tier M) / P2 (tier L) · fase P13–P14

*Scope (SSD §1, §5):* profil tier deployment, pool node executor + failover dari checkpoint, workspace multi-node, driver microVM, SSO OIDC/SAML, SCIM, organisasi/tenant, repo besar & multi-repo, anggaran organisasi/program/epic, HA & DR, ekspor SIEM, transparansi kapasitas.

| ID | Modul | Requirement | Prio | Sumber | AC/AX | UC |
|---|---|---|---|---|---|---|
| FR-245 | M27 | Sistem harus memvalidasi profil tier deployment (`DEPLOYMENT_TIER` = `S`/`M`/`L`) yang menentukan batas pengguna, task aktif, node, dan fitur yang aktif, dan gagal start dengan pesan aman bila konfigurasi tidak konsisten dengan tier — tanpa mengubah kode domain. | P1 | SSD §1; ADR-060 | AX-16 | UC-24 |
| FR-246 | M27 | Sistem harus mendukung pool node executor (worker + sandbox-manager) dengan registrasi node berbasis token yang dirotasi (`EXECUTOR_NODE_TOKEN_TTL_HOURS`), health/heartbeat, kapasitas, dan penempatan task berdasarkan kapasitas dan afinitas workspace. | P1 | SSD §5; ADR-060 | AX-16 | UC-24 |
| FR-247 | M27 | Sistem harus menandai task di node yang dinyatakan gagal sebagai `interrupted` dan melanjutkannya di node lain dari checkpoint terakhir tanpa mengulang side effect (idempotensi FR-054/FR-056). | P1 | SSD §5; FR-053..FR-056 | AX-16, AC-17 | UC-24 |
| FR-248 | M27 | Sistem harus menyediakan penyimpanan workspace multi-node (volume bersama atau sinkronisasi git bundle per task) dengan verifikasi checksum sebelum task dilanjutkan di node mana pun. | P1 | SSD §5 | AX-16 | UC-24 |
| FR-249 | M27 | Sistem harus mendukung driver sandbox microVM (Firecracker/Kata) untuk tier L dengan profil sandbox yang sama (argv, limit, network) seperti driver Docker. | P2 | SSD §5; ARCHITECTURE §13 | AX-17 | UC-24 |
| FR-250 | M27 | Sistem harus mendukung SSO OIDC (tier M) dan SAML (tier L) dengan pemetaan grup → role yang eksplisit dan opsi mematikan login lokal. | P1 | SSD §5; ADR-061 | AX-17 | UC-24 |
| FR-251 | M27 | Sistem harus mendukung provisioning dan deprovisioning SCIM v2; user yang dinonaktifkan kehilangan semua sesi dan token dalam ≤ 5 menit [Menebak]. | P2 | SSD §5; ADR-061 | AX-17, AC-01 | UC-24 |
| FR-252 | M27 | Sistem harus mengisolasi data, budget, kebijakan, dan audit per organisasi (tenant); akses ke resource milik organisasi lain menghasilkan 404. | P2 | SSD §5; ADR-061 | AX-17, AC-01 | UC-24 |
| FR-253 | M27 | Sistem harus mendukung repo besar dan monorepo dengan sparse/partial checkout per task (`SPARSE_CHECKOUT_ENABLED`), index simbol inkremental di sandbox (`index.symbols_incremental`), dan batas konteks sehingga agent hanya menerima file relevan (tanpa embedding, ADR-046 tetap berlaku). | P1 | SSD §5; ADR-062; ADR-046 | AX-18 | UC-24 |
| FR-254 | M27 | Sistem harus mendukung project multi-repo: story dapat mencakup beberapa repo, setiap task terikat ke satu repo, merge queue berjalan per repo, dan release mengikat versi (commit) lintas repo. | P1 | SSD §3; ADR-062 | AX-14 | UC-22, UC-23 |
| FR-255 | M27 | Sistem harus menerapkan anggaran per organisasi, program, dan epic dengan alert ambang dan kill switch yang menghentikan pemanggilan model berikutnya. | P1 | SSD §5; SSD §8 RS-04 | AX-16, AC-16 | UC-22, UC-24 |
| FR-256 | M27 | Sistem harus menyediakan HA & DR tier L (replika/failover PostgreSQL, backup terenkripsi, RPO ≤ 15 menit, RTO ≤ 4 jam [Menebak]) yang diuji lewat DR drill berkala. | P2 | SSD §1, §5 | AX-17 | UC-24 |
| FR-257 | M27 | Sistem harus mengekspor audit ke SIEM (syslog/OTLP/JSON) dengan rantai hash integritas dan retensi sesuai kebijakan. | P2 | SSD §5 | AX-17, AC-19 | UC-24 |
| FR-258 | M27 | Sistem harus menampilkan kapasitas dan antrean secara transparan: estimasi waktu tunggu task, utilisasi node, dan task yang ditolak karena kuota. | P1 | SSD §5 | AX-16 | UC-24 |

### 8.6 Catatan untuk review PO (v2.1)

1. Semua FR v2.1 adalah turunan dari rancangan berstatus *Proposed* (SSD §6); konfirmasi PO diperlukan sebelum WP-13.0. `P0ᵉ` di v2.1 = wajib untuk G13, bukan perubahan lingkup MVP/G3.
2. FR-222: syarat "merge queue lulus" pada SSD §3.1 sudah tercakup karena task baru `completed` setelah perubahan ter-merge (dengan merge queue: setelah entri lulus). Tidak ada syarat tambahan di luar K5.
3. ~~FR-227 menyebut B12 sebagai penghasil artefak desain, tetapi K9/WP-13.11/WP-14.8 tidak mengaktifkan B12.~~ **Diselesaikan K13 (OI-21):** B12 diaktifkan di P13 (K9, WP-13.11); B08, B11, B15 di P14 (WP-14.8). Artefak desain FR-227 dihasilkan A05, B12 (setelah G8) dan G35 (profil aktif) — lihat `AGENT_CATALOG.md` §13.
4. FR-244 vs NFR-27/T23: SSD §4.1 mengizinkan commit user langsung di default branch "bila policy project mengizinkan", sedangkan NFR-27 menuntut 100% commit di branch target melewati merge queue. **Diputuskan K13 (OI-20):** `kk/user-*` → merge queue bila aktif; commit langsung hanya bila merge queue repo nonaktif (tier S, OQ-02). NFR-27 berlaku untuk repo dengan merge queue aktif.
5. FR-250 berprioritas P1 walau bagian SAML-nya tier L (P2 per aturan K5 "P2 tier L"); dipertahankan persis K5.
6. FR-253: index simbol inkremental (ctags/tree-sitter) **bukan** embedding; ADR-046 tetap berlaku.
7. **FR-241 vs FR-077 / ADR-014.** FR-077 (terminal *read-only* yang men-stream output run sandbox) **tetap berlaku** untuk run task/agent. FR-241 menambah terminal interaktif yang terpisah, **hanya untuk manusia**, default nonaktif, sebagai pengecualian terbatas ADR-014 lewat ADR-059: WebSocket hanya untuk endpoint terminal; event lain tetap SSE. FR-077 tidak diubah teksnya.
8. **FR-241 vs FR-078.** Allowlist profil (FR-078) **tetap mengikat agent**: agent tidak mendapat tool terminal maupun perintah bebas (R4). Run/test user (FR-239/FR-240) juga memakai profil tetap (`run.*`, `*.test_select`); hanya sesi terminal manusia yang interaktif, tetap di container sandbox (network off, limit sama).
9. **FR-246/FR-249 vs ADR-036 (tanpa Kubernetes) dan ADR-002/ADR-010 (single-owner, 1 VPS).** Keputusan pilot tidak berubah: tier S = 1 VPS, tanpa Kubernetes. Tier M memakai node executor terdaftar (ADR-060) tanpa orkestrator cluster; pilihan orkestrator tier L (VM pool vs Kubernetes) **ditunda** ke ADR di WP-14.0 dengan data beban tier M (SSD §0 #5, OQ-01).
10. **Amandemen K13 (Lead, 22 Okt 2026)** diterapkan tanpa mengubah ID/prioritas: FR-230 dikerjakan **WP-13.12** (status CI GitHub, butuh G9) terpisah dari merge queue lokal FR-225 (**WP-13.5**, tanpa G9); aturan CI fast-forward + CI pasca-merge (OI-23) dimasukkan ke pernyataan FR-230 agar entri tidak pernah tertahan tanpa batas. AX-17 dipecah (OI-29): OIDC di G13, SAML/SCIM/tenant/DR/SIEM di G14.

### 8.7 Coverage v2.1 (dihitung)

Tabel di bawah dihitung skrip yang mem-parse baris tabel §8.3–§8.5 file ini (snippet di akhir bagian). Output aktual dicantumkan di §8.7.5.

#### 8.7.1 Modul → jumlah FR

| Modul | Rentang (K5) | Jumlah K5 | Jumlah terhitung | P0ᵉ | P1 | P2 | Sesuai K5 |
|---|---|---|---|---|---|---|---|
| M25 | FR-221..FR-234 | 14 | 14 | 2 | 8 | 4 | ✅ |
| M26 | FR-235..FR-244 | 10 | 10 | 3 | 5 | 2 | ✅ |
| M27 | FR-245..FR-258 | 14 | 14 | 0 | 9 | 5 | ✅ |
| **Total v2.1** | FR-221..FR-258 | **38** | **38** | **5** | **22** | **11** | ✅ |

Total FR katalog setelah v2.1: **258** (FR-001..FR-258) = 159 (v1) + 61 (v2) + 38 (v2.1).

#### 8.7.2 AX → FR

| AX | Kriteria (ringkas, K6) | FR | Jumlah |
|---|---|---|---|
| AX-12 | Edit/rename/hapus file dengan version check; konflik tak pernah menimpa diam-diam; take-over menandai bukti stale & memblokir apply | FR-235–FR-237, FR-244 | 4 |
| AX-13 | Run, test, terminal dari IDE; semua eksekusi di sandbox & tersimpan sebagai bukti | FR-239–FR-241 | 3 |
| AX-14 | Story ≥ 5 task [Menebak] paralel di worktree terpisah; merge queue menguji ulang; branch target tak pernah berisi commit gagal | FR-221–FR-225, FR-230, FR-254 | 7 |
| AX-15 | Release dapat ditelusuri requirement → … → acceptance dan diekspor | FR-222, FR-226, FR-228, FR-231 | 4 |
| AX-16 | Tier M: node ditambah tanpa ubah kode domain; node gagal → lanjut di node lain dari checkpoint | FR-245–FR-248, FR-255, FR-258 | 6 |
| AX-17 | Identitas & tenancy (dipecah K13): **tier M** — OIDC + grup → role + opsi matikan login lokal, diverifikasi di G13 (WP-13.8); **tier L** — SAML, deprovision SCIM ≤ 5 menit [Menebak], 404 lintas org, DR drill RPO/RTO, rantai hash SIEM, di G14 (WP-14.1..14.5) | FR-249–FR-252, FR-256, FR-257 | 6 |
| AX-18 | Repo ≥ 1 juta LOC [Menebak]: index inkremental & pemilihan konteks dalam batas waktu PO; agent hanya menerima file relevan | FR-253 | 1 |

#### 8.7.3 UC-21..UC-24 → FR

| UC | Use case | FR | Jumlah |
|---|---|---|---|
| UC-21 | Edit, jalankan & uji kode secara manual di IDE | FR-235–FR-244 | 10 |
| UC-22 | Kelola program/epic/story & dekomposisi | FR-221–FR-224, FR-226, FR-227, FR-231, FR-232, FR-234, FR-254, FR-255 | 11 |
| UC-23 | Merge queue, CI & release | FR-225, FR-228–FR-231, FR-233, FR-254 | 7 |
| UC-24 | Operasi tier M/L (node executor, SSO, tenant) | FR-245–FR-253, FR-255–FR-258 | 13 |

#### 8.7.4 AC v1 yang diperkuat FR v2.1

| AC | FR v2.1 |
|---|---|
| AC-01 | FR-251, FR-252 |
| AC-03 | FR-235 |
| AC-07 | FR-239, FR-241 |
| AC-08 | FR-240 |
| AC-09 | FR-225, FR-237 |
| AC-11 | FR-236 |
| AC-12 | FR-222 |
| AC-13 | FR-229, FR-244 |
| AC-16 | FR-255 |
| AC-17 | FR-247 |
| AC-19 | FR-231, FR-257 |

#### 8.7.5 Hasil cek

Output aktual snippet di bawah (dijalankan 22 Okt 2026 dari root repo):

```text
rows: 38 unik: 38
per modul: {'M25': 14, 'M26': 10, 'M27': 14}
per prio : {'P1': 22, 'P0ᵉ': 5, 'P2': 11}
kontigu FR-221..FR-258: True
coverage AX-12..AX-18 & UC-21..UC-24 OK
```

- Jumlah FR v2.1: **38** (target 38), rentang kontigu FR-221..FR-258 tanpa duplikat: ✅
- Jumlah per modul sesuai K5 (14/10/14): ✅
- **Cek coverage AX:** 7/7 AX (AX-12..AX-18) memiliki ≥ 1 FR — ✅.
- **Cek coverage UC:** 4/4 UC (UC-21..UC-24) memiliki ≥ 1 FR — ✅.
- Seluruh file memuat 258 baris FR unik (FR-001..FR-258); isi §1–§7 tidak disunting (satu-satunya baris lama yang berubah: judul dokumen FR-001..FR-159 → FR-001..FR-258; ditambah catatan perluasan di kepala dokumen) — dicek `git diff`.
- FR tanpa AX/AC langsung (sesuai K5 "—"): FR-227, FR-232, FR-233, FR-234, FR-238, FR-242, FR-243.

Snippet verifikasi ulang (jalankan dari root repo):

```python
import re, collections
t = open('docs/FR_CATALOG.md', encoding='utf-8').read()
sec = t[t.index('## 8. Perluasan v2.1'):]
rows = re.findall(r'^\| (FR-\d{3}) \| (M2[5-7]) \| .+? \| (P0ᵉ|P1|P2) \| .+? \| (.+?) \| (.+?) \|$', sec, re.M)
ids = [r[0] for r in rows]
print('rows:', len(rows), 'unik:', len(set(ids)))
print('per modul:', dict(collections.Counter(r[1] for r in rows)))
print('per prio :', dict(collections.Counter(r[2] for r in rows)))
print('kontigu FR-221..FR-258:', ids == [f'FR-{i}' for i in range(221, 259)])
for k in [f'AX-{i}' for i in range(12, 19)] + [f'UC-{i}' for i in range(21, 25)]:
    assert any(k in (ax + ', ' + uc).split(', ') for *_, ax, uc in rows), k
print('coverage AX-12..AX-18 & UC-21..UC-24 OK')
```

---

## 9. Perluasan v2.2 — Polyglot language packs, lingkungan integrasi multi-service, monitoring biaya & token realtime (FR-259..FR-292)

> **Status: rancangan / Proposed — belum diimplementasikan; *derived — perlu review PO*.** Bagian ini ditambahkan 22 Okt 2026 mengikuti **KONTRAK v2.2** (`docs/POLYGLOT_INTEGRATION_DESIGN.md` §7 — mengikat): modul M28–M30 dan FR-259..FR-292 (K14), AX-19..AX-24 (K15), NFR-28..NFR-31 (K16), ADR-063..068 (K17, *Proposed*), fase/gate/WP (K18), delta data/API/konfigurasi (K19), ancaman T24–T28 (K20); fakta tingkat pack, aturan lingkungan integrasi, dan sumber data monitoring dari PID §1–§4. Makna dan prioritas setiap FR **identik** dengan tabel K14; pernyataan boleh sedikit lebih presisi (mis. menyebut nama tabel/variabel/tool K19) tanpa mengubah cakupan. FR-001..FR-258 (§1–§8) **tidak diubah**; ID v2.2 tidak boleh digeser setelah review PO. Semua angka waktu/ambang dari kontrak tetap **[Menebak]** sampai diukur di gate G6/G15/G16.

### 9.1 Konvensi v2.2

- **Kolom** sama dengan §7/§8: ID · Modul · Requirement (*"Sistem harus …"*, dapat diuji) · Prio · Sumber · AC/AX · UC.
- **Prio.** Mengikuti K14 per FR. `P0ᵉ` = wajib untuk gate fase perluasan terkait — **G6** untuk inti M30 (WP-6.9), **G15** untuk inti M28 (pack L1, WP-15.0) — **bukan** perubahan lingkup MVP/G3. M29 seluruhnya P1 (G16). Rekonsiliasi M30 (FR-288, FR-289) P1 dan opsional; FR-292 (kredensial Admin API) tetap `P0ᵉ` karena berlaku begitu rekonsiliasi diaktifkan.
- **AC/AX.** `AX-19..AX-24` dari K15 (setiap FR membawa AX persis seperti kolom AX K14). `AC-xx` (PRD §12.1) ditambahkan bila FR v2.2 juga memperkuat kriteria v1 (pola §7.1/§8.1) *(turunan)*. "—" = tidak ada kriteria penerimaan langsung (K14: FR-270, FR-281, FR-291).
- **Sumber.** `PID §x` = `docs/POLYGLOT_INTEGRATION_DESIGN.md`; `K14–K20` = KONTRAK v2.2; `ADR-063..068` = K17 (*Proposed*; tertulis di `DECISIONS.md` sejak K21); `FR-xxx` = FR v1/v2/v2.1 yang dilanjutkan.
- **Fase.** M28 = P15 (gate G15), M29 = P16 (gate G16), M30 = P6 (inti + rekonsiliasi opsional, gate G6) dan P10 (biaya capability, WP-10.7, gate G10) — K14/K18.
- **Use case baru (v2.2):** UC-25..UC-27 (definisi lengkap di `docs/SYSTEM_SPEC.md` §13.4).

| UC | Use case | Aktor utama | Fase |
|---|---|---|---|
| UC-25 | Bangun & uji sistem polyglot (banyak bahasa) | Developer / tech lead | P15 |
| UC-26 | Uji integrasi multi-service di lingkungan ephemeral | Tech lead / QA, arsitek service | P16 (tier M/L; tier S mode kontrak + mock) |
| UC-27 | Pantau biaya & token realtime dan rekonsiliasi | Owner / admin / pemegang anggaran | P6 (inti + rekonsiliasi opsional), P10 (biaya capability) |

### 9.2 Ringkasan modul v2.2 (kontrak K14)

| Modul | Nama | Rentang FR | Jumlah (K14) | Prioritas modul (K14) | Fase |
|---|---|---|---|---|---|
| M28 | Polyglot Language Packs | FR-259..FR-270 | 12 | P1 (L1 = P0ᵉ fase P15) | P15 |
| M29 | Lingkungan Integrasi Multi-Service & Kontrak | FR-271..FR-282 | 12 | P1 | P16 |
| M30 | Monitoring Biaya & Token Realtime | FR-283..FR-292 | 10 | P0ᵉ (inti), P1 (rekonsiliasi) | P6 (inti), P10 (capability), P6 opsional rekonsiliasi |

Jumlah aktual per modul **dihitung** dari tabel di bawah — lihat §9.7.

### 9.3 M28 — Polyglot Language Packs · prioritas P1 (L1 = P0ᵉ) · fase P15

*Scope (PID §1):* language pack berversi = image toolchain dipin digest + deteksi stack + profil sandbox tetap (`*.deps_install`, `*.build`, `*.test`, `*.test_select`, `*.lint`, `*.fmt`, `*.run`, `*.package`) + registry dependensi lewat proxy + template + petunjuk agent + set evaluasi; gelombang 1 = `python`, `node`, `jvm`, `dotnet`, `go`, `php` (L1), `ruby`, `cpp`, `react-native`, `flutter` (L2), `sql` (L1 untuk migrasi & test terhadap DB ephemeral). Target non-Linux (iOS/macOS, Windows/.NET Framework, emulator Android) hanya lewat CI eksternal (ADR-068).

| ID | Modul | Requirement | Prio | Sumber | AC/AX | UC |
|---|---|---|---|---|---|---|
| FR-259 | M28 | Sistem harus menyediakan registry language pack berversi (`language_packs`) yang setiap entrinya memuat image toolchain dipin digest, profil sandbox, aturan deteksi stack, registry dependensi, template, petunjuk agent, dan set evaluasi, serta memvalidasi manifest pack (digest, profil, SBOM, scan) sebelum pack berstatus aktif; pack yang belum aktif tidak dapat dipakai project maupun agent. | P0ᵉ | PID §1.1; K14; K19; ADR-063 | AX-21 | UC-25 |
| FR-260 | M28 | Sistem harus mendeteksi bahasa, framework, package manager, dan entrypoint untuk semua pack gelombang 1 (`repo.detect_stack`, H41), termasuk repo polyglot/monorepo dengan beberapa stack per path, dan menyimpan hasilnya per project (`project_stacks`: pack, framework, path). | P0ᵉ | PID §1.1, §4; K14; K19; FR-084 | AX-21 | UC-25 |
| FR-261 | M28 | Sistem harus menyediakan template project per framework untuk pack L1 (Spring Boot, ASP.NET Core, Go service, Laravel, FastAPI, Node/React) sebagai sumber pembuatan project dari template. | P1 | PID §1.1; K14; FR-012, FR-013 | AX-21, AC-02 | UC-25 |
| FR-262 | M28 | Sistem harus menjalankan profil deps/build/test/test terpilih/lint/format/run/package per pack (pack L2: deps/build/test/lint) di sandbox dengan argumen ter-allowlist, tanpa perintah bebas. | P0ᵉ | PID §1.1–§1.2; K14; FR-078, FR-079 | AX-21, AC-06, AC-07 | UC-25 |
| FR-263 | M28 | Sistem harus menginstal dependensi hanya lewat proxy repositori ber-allowlist per ekosistem (hanya profil `*.deps_install`; profil lain tetap `network_mode=none`), dengan lockfile wajib dan namespace tunggal per registry. | P0ᵉ | PID §1.3; K14; ADR-064 (memperluas ADR-007); FR-073 | AX-21, AC-07 | UC-25 |
| FR-264 | M28 | Sistem harus mem-parse hasil test semua pack ke format seragam (JUnit XML atau setara) sehingga status test berasal dari exit code dan laporan nyata, tidak pernah dari klaim agent. | P0ᵉ | PID §1.1; K14; FR-086 | AX-21, AC-08 | UC-25 |
| FR-265 | M28 | Sistem harus memberi agent B14, C17, dan C20 petunjuk ringkas per pack dan memilih profil yang sesuai stack file yang diubah. | P1 | PID §1.1, §4; K14 | AX-22 | UC-25 |
| FR-266 | M28 | Sistem harus mengaktifkan pack untuk agent hanya setelah set evaluasi bahasa itu lulus ambang yang disetujui PO (G8 per pack). | P1 | PID §0 #6, §4; K14; K15 AX-22 | AX-22 | UC-25 |
| FR-267 | M28 | Sistem harus membangun image toolchain secara reproducible (SBOM, scan, rebuild terjadwal, pin digest) dan menolak image yang tidak terdaftar di registry pack. | P1 | PID §1.1; K14; K16 NFR-30; ADR-054 (pola) | AX-21 | UC-25 |
| FR-268 | M28 | Sistem harus mendukung build mobile Android untuk Flutter dan React Native di sandbox serta build web Flutter. | P2 | PID §1.2; K14 | AX-21 | UC-25 |
| FR-269 | M28 | Sistem harus menyediakan pack SQL: migrasi (Flyway/Liquibase), lint SQL, dan test terhadap database ephemeral PostgreSQL/MySQL/SQL Server. | P1 | PID §1.2; K14 | AX-21, AX-23 | UC-25, UC-26 |
| FR-270 | M28 | Sistem harus menandai target yang tidak dapat dibangun di sandbox Linux (iOS/macOS, Windows/.NET Framework, emulator Android) dan mengarahkannya ke CI eksternal dengan status yang dibaca sebagai bukti. | P2 | PID §0 #5, §1.2; K14; ADR-068; FR-230 | — | UC-25 |

### 9.4 M29 — Lingkungan Integrasi Multi-Service & Kontrak · prioritas P1 · fase P16

*Scope (PID §2):* katalog service per program/project, kontrak antar-service di repo (OpenAPI, AsyncAPI, Protobuf/gRPC, GraphQL) sebagai sumber kebenaran, deteksi breaking change yang memblokir merge queue, mock dari kontrak + contract test provider, lingkungan integrasi ephemeral dari spec deklaratif tervalidasi (image terdaftar, network per env tanpa egress, kuota & TTL, tier M/L saja; tier S = mode kontrak + mock), data seed sintetis, E2E lintas service sebagai bukti dan syarat merge queue.

| ID | Modul | Requirement | Prio | Sumber | AC/AX | UC |
|---|---|---|---|---|---|---|
| FR-271 | M29 | Sistem harus menyediakan katalog service per program/project (`service_catalog`: service, pack, repo, interface disediakan/dipakai, dependensi data, pemilik). | P1 | PID §2.1; K14; K19 | AX-23 | UC-26 |
| FR-272 | M29 | Sistem harus menyimpan kontrak antar-service (OpenAPI, AsyncAPI, Protobuf/gRPC, GraphQL) di repo sebagai sumber kebenaran dan memvalidasinya. | P1 | PID §2.1; K14; ADR-066 | AX-24 | UC-26 |
| FR-273 | M29 | Sistem harus mendeteksi breaking change kontrak dan memblokir merge queue sampai semua consumer terdampak diperbarui atau pengecualian disetujui. | P1 | PID §2.1; K14; ADR-066; FR-225 | AX-24 | UC-26 |
| FR-274 | M29 | Sistem harus membangkitkan mock/stub dari kontrak untuk test consumer dan menjalankan contract test provider. | P1 | PID §2.1; K14 | AX-24 | UC-26 |
| FR-275 | M29 | Sistem harus menjalankan lingkungan integrasi ephemeral dari spec deklaratif yang divalidasi policy (image terdaftar saja, tanpa privileged/host mount/host network, tanpa egress). | P1 | PID §2.2 aturan 1–2; K14; ADR-065 | AX-23, AC-07 | UC-26 |
| FR-276 | M29 | Sistem harus pada tier S menyediakan mode kontrak + mock tanpa menjalankan service bersama, dengan label keterbatasan. | P1 | PID §2.2 aturan 4; K14; ADR-060 | AX-24 | UC-26 |
| FR-277 | M29 | Sistem harus menyediakan katalog service pendukung terkunci (PostgreSQL, MySQL, SQL Server dev, Redis, Kafka/RabbitMQ, penyimpanan objek tiruan) dengan versi dipin. | P1 | PID §2.2 aturan 1; K14 | AX-23 | UC-26 |
| FR-278 | M29 | Sistem harus mengisi data seed sintetis dan secret palsu yang dibangkitkan; secret nyata tidak pernah masuk lingkungan integrasi. | P1 | PID §2.2 aturan 2; K14; K20 T28 | AX-23, AC-15 | UC-26 |
| FR-279 | M29 | Sistem harus menjalankan E2E lintas service dan menyimpan hasil, log per service, dan trace sebagai bukti (`test_runs` dengan `suite = 'integration'`). | P1 | PID §2.2 aturan 5; K14 | AX-23, AC-08 | UC-26 |
| FR-280 | M29 | Sistem harus menegakkan kuota per lingkungan (jumlah service, CPU/RAM, TTL) dan teardown otomatis. | P1 | PID §2.2 aturan 3; K14; K19 | AX-23, AC-07 | UC-26 |
| FR-281 | M29 | Sistem harus menampilkan peta service & status lingkungan (service, kesehatan, log, hasil test) di UI. | P1 | PID §2.2; K14 | — | UC-26 |
| FR-282 | M29 | Sistem harus menjadikan contract test dan E2E lintas service syarat merge queue untuk story yang menyentuh lebih dari satu service. | P1 | PID §2.2 aturan 5, §2.3; K14; FR-225, FR-254 | AX-24 | UC-26 |

### 9.5 M30 — Monitoring Biaya & Token Realtime · prioritas P0ᵉ (inti) / P1 (rekonsiliasi) · fase P6 (+P10)

*Scope (PID §3):* event usage per panggilan model/vendor dari event stream (`message_start` + `message_delta` kumulatif) ke stream realtime (`usage.event` via Redis + SSE), halaman `/usage/live` (aliran langsung, kartu ringkasan, grafik per menit, laju & proyeksi, breakdown, rasio cache), alert budget 50/80/100% dengan tautan kill switch, rekonsiliasi opsional dengan Usage & Cost Admin API (usage bucket `1m` ≈ ≤ 5 menit, cost resmi harian `1d`, polling ≥ 60 dtk), label angka `aktual`/`estimasi`/`resmi`/`tidak tersedia`, ekspor; kredensial Admin API hanya di `worker-integrations`.

| ID | Modul | Requirement | Prio | Sumber | AC/AX | UC |
|---|---|---|---|---|---|---|
| FR-283 | M30 | Sistem harus menerbitkan event usage per panggilan model/vendor (token in/out/cache, server tool, model, agent, task, project, capability, estimasi biaya, `cost_status`) ke stream realtime (`usage.event`), dengan token diambil dari `usage` event stream provider (`message_start` + nilai kumulatif `message_delta`) dan dicatat sekali per panggilan (`usage_events`). | P0ᵉ | PID §3.1, §3.3; K14; K19; ADR-067; FR-133, FR-218 | AX-19, AC-16 | UC-27 |
| FR-284 | M30 | Sistem harus menyediakan halaman monitoring biaya & token realtime (`/usage/live`) dengan aliran langsung, kartu ringkasan, dan grafik per menit. | P0ᵉ | PID §3.2; K14; K19; FR-011, FR-150 | AX-19, AC-16 | UC-27 |
| FR-285 | M30 | Sistem harus menghitung laju pemakaian (token/menit, biaya/jam) dan proyeksi akhir periode. | P1 | PID §3.2; K14 | AX-19 | UC-27 |
| FR-286 | M30 | Sistem harus menampilkan breakdown per model, agent, project, task, capability, user, dan rasio cache. | P0ᵉ | PID §3.2; K14 | AX-19, AC-16 | UC-27 |
| FR-287 | M30 | Sistem harus menerapkan alert budget 50/80/100% untuk task, project, program, organisasi, dan capability dengan tautan ke kill switch (FR-217, FR-255), dan menampilkan alert di UI serta mencatatnya di audit. | P0ᵉ | PID §3.2; K14; K19 (`budget_alerts`); FR-132, FR-217, FR-255 | AX-19, AC-16 | UC-27 |
| FR-288 | M30 | Sistem harus merekonsiliasi catatan internal dengan Usage & Cost Admin API (usage bucket `1m`, cost harian `1d`) bila kredensial dikonfigurasi, dengan polling ≥ 60 detik (`USAGE_RECONCILE_INTERVAL_SECONDS`, minimum 60). | P1 | PID §0 #1, §3.2, §6; K14; ADR-067 | AX-20, AC-16 | UC-27 |
| FR-289 | M30 | Sistem harus menampilkan selisih internal vs resmi per model per menit/hari dan menandai selisih di atas ambang (`USAGE_VARIANCE_ALERT_PCT`). | P1 | PID §3.2; K14; K19 | AX-20 | UC-27 |
| FR-290 | M30 | Sistem harus memberi label setiap angka `aktual` / `estimasi` / `resmi` / `tidak tersedia` dan menjelaskan bila rekonsiliasi tidak tersedia (mis. akun individual tanpa Admin API), tanpa angka palsu. | P0ᵉ | PID §0 #2, §3.2; K14; FR-133, FR-218; ADR-025 | AX-19, AX-20, AC-16 | UC-27 |
| FR-291 | M30 | Sistem harus mengekspor data usage & biaya (CSV/JSON) per periode dan dimensi. | P1 | PID §3.2; K14; K19 (`GET /usage/export`) | — | UC-27 |
| FR-292 | M30 | Sistem harus menyimpan kredensial Admin API (`ANTHROPIC_ADMIN_API_KEY`) hanya di `worker-integrations`, nonaktif secara default, dipakai hanya untuk endpoint usage/cost, dan tidak pernah muncul di browser/log. | P0ᵉ | PID §0 #2, §3.3; K14; K19; K20 T26; ADR-053 (pola), ADR-067 | AX-20, AC-15 | UC-27 |

### 9.6 Catatan untuk review PO (v2.2)

1. Semua FR v2.2 adalah turunan dari rancangan berstatus *Proposed* (PID §7); konfirmasi PO diperlukan sebelum WP-6.9 / WP-15.0 / WP-16.0. ~~ADR-063..068 belum tertulis di `DECISIONS.md`; WP v2.2 belum ada di `wbs_data.py`~~ — **selesai (K21 OI-37, 22 Okt 2026)** — keduanya di luar path milik agen dokumentasi; Lead perlu menambahkannya (pola OI-36 v2.1).
2. `P0ᵉ` M30 berarti wajib untuk **G6** (exit P6), bukan G3 — WBS saat ini `WP-G3` tidak bergantung pada `WP-G6`, sehingga lingkup MVP tidak bertambah. `WP-G6` di WBS sudah bergantung pada WP-6.9 dan WP-6.10 (K21 OI-37).
3. **FR-287 vs sumber anggaran yang ada.** Alert 50/80/100% mencakup lima lingkup, tetapi sumber limit sudah tersebar: task (`tasks.budget_limit`, FR-132), project (`/projects/{id}/budget`), capability (`capability_settings.limits`, FR-217), program/organisasi (`cost_budgets`, FR-255, 0008). *(turunan)* FR-287 **tidak** membuat penyimpanan limit baru: `budget_alerts` (K19) mencatat kejadian alert dari ketiga sumber; `GET/POST /budgets` adalah fasad yang menulis ke sumber asli (`API_CONTRACTS.md` §14). Lingkup program/organisasi baru aktif setelah WP-13.7/WP-14.5, capability setelah WP-10.1.
4. **FR-290 vs ADR-025.** `cost_status = actual` dicadangkan (ADR-025) dan tetap tidak dipakai: label `aktual` hanya untuk **token** dari provider; biaya hasil rekonsiliasi diberi label `resmi` dan disimpan di `usage_reconciliation`, tidak menimpa `usage_records.cost_status` *(turunan)*.
5. **FR-263 vs ADR-007 / FR-073.** ADR-007 hanya mengizinkan domain PyPI/npm lewat `egress-proxy`. Resolusi kontrak: **ADR-064 memperluas ADR-007** menjadi proxy repositori per ekosistem; profil selain `*.deps_install` tetap `network_mode=none`; `SANDBOX_EGRESS=allowlist` tetap prasyarat; G4 (WP-6.5) tetap gate wajib sebelum P15. FR-073 tidak diubah teksnya.
6. **FR-262/FR-275 vs FR-078 dan ADR-009.** Allowlist profil (FR-078) **tetap mengikat**: setiap pack hanya menambah profil tetap ber-`args_pattern`; spec lingkungan integrasi bukan compose bebas melainkan **template env tetap** yang divalidasi policy (ADR-065 memperluas ADR-009 dari "profil tetap" menjadi "template env tetap"). Konsekuensi pada izin `docker-socket-proxy` (`NETWORKS=0` di ADR-009) dicatat di `SYSTEM_SPEC.md` §13.10.
7. FR-269 berada di M28 tetapi juga membawa AX-23 (DB ephemeral di lingkungan integrasi) — dipertahankan persis K14; UC-25 dan UC-26.
8. FR-270 dan FR-268: build iOS/macOS, Windows/.NET Framework, dan emulator Android **tidak** dijalankan di sandbox (ADR-068); FR-270 hanya membaca status CI eksternal sebagai bukti (pola FR-230), tanpa runner baru di VPS (OQ-05).

### 9.7 Coverage v2.2 (dihitung)

Tabel di bawah dihitung skrip yang mem-parse baris tabel §9.3–§9.5 file ini (snippet di akhir bagian). Output aktual dicantumkan di §9.7.5.

#### 9.7.1 Modul → jumlah FR

| Modul | Rentang (K14) | Jumlah K14 | Jumlah terhitung | P0ᵉ | P1 | P2 | Sesuai K14 |
|---|---|---|---|---|---|---|---|
| M28 | FR-259..FR-270 | 12 | 12 | 5 | 5 | 2 | ✅ |
| M29 | FR-271..FR-282 | 12 | 12 | 0 | 12 | 0 | ✅ |
| M30 | FR-283..FR-292 | 10 | 10 | 6 | 4 | 0 | ✅ |
| **Total v2.2** | FR-259..FR-292 | **34** | **34** | **11** | **21** | **2** | ✅ |

Total FR katalog setelah v2.2: **292** (FR-001..FR-292) = 159 (v1) + 61 (v2) + 38 (v2.1) + 34 (v2.2).

#### 9.7.2 AX → FR

| AX | Kriteria (ringkas, K15) | FR | Jumlah |
|---|---|---|---|
| AX-19 | Setiap panggilan model/vendor tampil di monitoring ≤ 2 dtk [Menebak] dengan token & estimasi biaya; total halaman = total `usage_records`; alert budget tepat di ambang | FR-283–FR-287, FR-290 | 6 |
| AX-20 | Dengan kredensial Admin API: selisih per model per menit tampil & ditandai; tanpa kredensial: label "estimasi" + alasan; kredensial tak pernah keluar dari worker-integrations | FR-288–FR-290, FR-292 | 4 |
| AX-21 | Setiap pack gelombang 1: template (L1), deps lewat proxy saja, build, test, lint (+run, package L1) di sandbox; hasil test dari laporan nyata | FR-259–FR-264, FR-267–FR-269 | 9 |
| AX-22 | Agent lulus set evaluasi per pack L1 di atas ambang PO [Menebak ≥ 70%] dengan bukti test | FR-265, FR-266 | 2 |
| AX-23 | Lingkungan ≥ 3 service beda bahasa + DB + broker naik terisolasi tanpa egress, E2E lulus, teardown otomatis; spec ilegal ditolak | FR-269, FR-271, FR-275, FR-277–FR-280 | 7 |
| AX-24 | Breaking change kontrak memblokir merge queue sampai consumer diperbarui; story lintas service merge setelah contract test + E2E lulus | FR-272–FR-274, FR-276, FR-282 | 5 |

#### 9.7.3 UC-25..UC-27 → FR

| UC | Use case | FR | Jumlah |
|---|---|---|---|
| UC-25 | Bangun & uji sistem polyglot (banyak bahasa) | FR-259–FR-270 | 12 |
| UC-26 | Uji integrasi multi-service di lingkungan ephemeral | FR-269, FR-271–FR-282 | 13 |
| UC-27 | Pantau biaya & token realtime dan rekonsiliasi | FR-283–FR-292 | 10 |

#### 9.7.4 AC v1 yang diperkuat FR v2.2

| AC | FR v2.2 |
|---|---|
| AC-02 | FR-261 |
| AC-06 | FR-262 |
| AC-07 | FR-262, FR-263, FR-275, FR-280 |
| AC-08 | FR-264, FR-279 |
| AC-15 | FR-278, FR-292 |
| AC-16 | FR-283, FR-284, FR-286–FR-288, FR-290 |

#### 9.7.5 Hasil cek

Output aktual snippet di bawah (dijalankan 22 Okt 2026 dari root repo):

```text
rows: 34 unik: 34
per modul: {'M28': 12, 'M29': 12, 'M30': 10}
per prio : {'P0ᵉ': 11, 'P1': 21, 'P2': 2}
kontigu FR-259..FR-292: True
coverage AX-19..AX-24 & UC-25..UC-27 OK
total baris FR: 292 unik: 292 kontigu FR-001..FR-292: True
```

- Jumlah FR v2.2: **34** (target 34), rentang kontigu FR-259..FR-292 tanpa duplikat: ✅
- Jumlah per modul sesuai K14 (12/12/10): ✅
- **Cek coverage AX:** 6/6 AX (AX-19..AX-24) memiliki ≥ 1 FR — ✅.
- **Cek coverage UC:** 3/3 UC (UC-25..UC-27) memiliki ≥ 1 FR — ✅.
- Seluruh file memuat 292 baris FR unik (FR-001..FR-292); isi §1–§8 tidak disunting (satu-satunya baris lama yang berubah: judul dokumen FR-001..FR-258 → FR-001..FR-292 dan catatan perluasan di kepala dokumen) — dicek `git diff`.
- FR tanpa AX/AC langsung (sesuai K14 "—"): FR-270, FR-281, FR-291.

Snippet verifikasi ulang (jalankan dari root repo):

```python
import re, collections
t = open('docs/FR_CATALOG.md', encoding='utf-8').read()
sec = t[t.index('## 9. Perluasan v2.2'):]
rows = re.findall(r'^\| (FR-\d{3}) \| (M2[89]|M30) \| .+? \| (P0ᵉ|P1|P2) \| .+? \| (.+?) \| (.+?) \|$', sec, re.M)
ids = [r[0] for r in rows]
print('rows:', len(rows), 'unik:', len(set(ids)))
print('per modul:', dict(collections.Counter(r[1] for r in rows)))
print('per prio :', dict(collections.Counter(r[2] for r in rows)))
print('kontigu FR-259..FR-292:', ids == [f'FR-{i}' for i in range(259, 293)])
for k in [f'AX-{i}' for i in range(19, 25)] + [f'UC-{i}' for i in range(25, 28)]:
    assert any(k in (ax + ', ' + uc).split(', ') for *_, ax, uc in rows), k
print('coverage AX-19..AX-24 & UC-25..UC-27 OK')
allrows = re.findall(r'^\| (FR-\d{3}) \|', t, re.M)
print('total baris FR:', len(allrows), 'unik:', len(set(allrows)),
      'kontigu FR-001..FR-292:', sorted(set(allrows)) == [f'FR-{i:03d}' for i in range(1, 293)])
```
