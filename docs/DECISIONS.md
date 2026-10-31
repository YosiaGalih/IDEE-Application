# KodeKantor — Architecture Decision Records

Format: Konteks → Keputusan → Konsekuensi. Status: `Accepted` (dikunci untuk MVP) atau `Proposed` (default disetujui Product Owner 21 Okt 2026, dapat direvisi sebelum production). **Catatan:** ADR-036..068 (perluasan v2/v2.1/v2.2) berstatus `Proposed` dan **belum diputuskan PO** — keputusan di WP-9.0 (v2), WP-13.0 (v2.1), WP-15.0/WP-16.0 dan WP-6.9 (v2.2). ADR yang mengubah kontrak di `ARCHITECTURE.md`/`API_CONTRACTS.md` wajib memperbarui dokumen tersebut.

| ADR | Judul | Status | PRD ref |
|---|---|---|---|
| 001 | Technology baseline MVP | Accepted | §7.2.1–7.2.2 |
| 002 | Identity: single-owner pilot, skema multi-user + RBAC sejak awal | Proposed | §15.1 #1, §5.1 |
| 003 | Git: lokal dulu, remote write = P2 | Proposed | §15.1 #2 |
| 004 | Claude API key dikelola admin (server-side), model & budget dari konfigurasi | Proposed | §15.1 #3, §7.3.1 |
| 005 | Workspace storage, kuota & retensi | Proposed | §15.1 #4 |
| 006 | Deviasi state machine untuk cancel/clarify/reopen | Accepted | §3.3, AC-14 |
| 007 | Sandbox egress default OFF + proxy allowlist untuk install dependency | Proposed | §15.1 #5, R-08 |
| 008 | Preview di subdomain terpisah | Proposed | §15.1 #6 |
| 009 | Sandbox-manager mengakses Docker hanya via socket proxy; gVisor opsional | Accepted | R-01, §10.3 |
| 010 | Ukuran VPS awal & batas concurrency | Proposed | §15.1 #7 |
| 011 | Kebijakan pengiriman source code ke provider | Proposed | §15.1 #8, §7.3.5 |
| 012 | 41 agent → 6 runtime LLM + tool deterministik | Accepted | §6, §14.1 #7, R-06 |
| 013 | Output agent terstruktur via tool `submit_result` | Accepted | §7.3.2 |
| 014 | Event streaming: PostgreSQL + Redis pub/sub + SSE | Accepted | §7.2, AC-05 |
| 015 | ScriptedProvider untuk test & demo, dilarang di production | Accepted | §7.3.6, §12.2 |
| 016 | Review-first via git worktree per task | Accepted | §5.3, R-03, R-07 |
| 017 | Type checker: Pyright | Accepted | §7.4 |
| 018 | Enum sebagai VARCHAR + CHECK | Accepted | §8.1 |
| 019 | Ingress: Caddy | Accepted | §7.2 |
| 020 | Katalog FR diturunkan karena FR-001..159 tidak didefinisikan di PRD | Proposed | §5 |
| 021 | Tiga transisi state tambahan (apply conflict, R3 ditolak, approval saat planning) | Accepted (desain) | §3.3 |
| 022 | Workflow ↔ services lewat `WorkflowPorts` (DI), tanpa import dinamis | Accepted (desain) | NFR-07 |
| 023 | Encoding nama tool untuk Claude API (`.` → `__`) | Accepted | §7.3.2 |
| 024 | Semantik transaksi approval handler | Accepted | §10.4 |
| 025 | `cost_status` menambah nilai `partial`; `agent_runs.status` menambah `approval_required` | Accepted (desain) | §5.7, §8 |
| 026 | Sandbox menyembunyikan `.git`, image tidak pernah auto-pull, flag socket-proxy minimum | Accepted | R-01, §10.3 |
| 027 | Strategi remediasi: tambah langkah perbaikan gap; re-plan hanya bila requirement berubah | Accepted | §5.5, AC-18 |
| 028 | Dispatcher abstraction; Celery eager mode dilarang dari kode async | Accepted | NFR-05 |
| 029 | Penyelarasan kontrak API v1.1 (field yang dibutuhkan UI) | Accepted (desain) | §9 |
| 030 | Redaksi `key=value` huruf kecil & pola tambahan | Accepted (desain) | §8.1, AC-15 |
| 031 | Pause task = `interrupted` (`paused_by_user`) + batas durasi task | Accepted (desain) | FR-057, FR-132 |
| 032 | Hasil readiness gate disimpan & memblokir agent LLM | Accepted (desain) | §7.3.6, FR-045, FR-135 |
| 033 | Run sandbox read-only untuk `main` + batas pertumbuhan disk | Accepted (desain) | §10.3 |
| 034 | Matriks approval per action_type, izin & self-approval | Accepted (desain) | §10.4, §9 |
| 035 | `test_runs.failure_kind` | Accepted (desain) | FR-087, §5.6 |
| 036 | KodeKantor Platform: tiga portal di atas satu core (modular monolith) | Proposed | perluasan |
| 037 | Provider gateway multi-model per capability (Claude utama) | Proposed | §7.7 |
| 038 | Riset web memakai `web_search`/`web_fetch` Claude; sitasi wajib | Proposed | perluasan |
| 039 | Engine dokumen ganda: Agent Skills vs sandbox lokal | Proposed | perluasan |
| 040 | `ImageProvider` netral; vendor dipilih lewat ADR-040a | Proposed | perluasan |
| 041 | Integrasi GitHub lewat GitHub App (repo privat) | Proposed | M15, ADR-003 |
| 042 | Portal Portfolio Profesional | Proposed | perluasan |
| 043 | ~~pgvector untuk tanya jawab dokumen~~ — **Superseded oleh ADR-046** | Superseded | §7.7 |
| 044 | Capability Module Registry | Proposed | perluasan |
| 045 | Tata kelola data & ancaman T11–T18 | Proposed | §10 |
| 046 | Tanya jawab dokumen tanpa embedding: retrieval leksikal + sitasi Claude (menggantikan ADR-043) | Proposed | perluasan |
| 047 | Agent bersitasi mengakhiri jawaban dengan teks bersitasi, bukan `submit_result` | Proposed | ADR-013 |
| 048 | Katalog agent v2: 11 agent baru + promosi E29 | Proposed | §6 |
| 049 | Tech stack v2 | Proposed | §7.2 |
| 050 | RunScope: run & tool record tanpa task (Portal AI/Portfolio) | Proposed | perluasan |
| 051 | Server tool Claude tetap lewat ToolGateway (`executor: provider`) | Proposed | §14.1 #2 |
| 052 | ApprovalGrant untuk eksekusi R3 multi-langkah | Proposed | ADR-034 |
| 053 | Worker per antrean & secret per role (pem hanya `worker-integrations`) | Proposed | ADR-010, ADR-041 |
| 054 | Workspace job `_jobs/<uuid>` & image `sandbox-docs` | Proposed | ADR-009 |
| 055 | Asset store & URL bertanda tangan | Proposed | ADR-045 |
| 056 | Scope approval v2 & action type `github_push_pr` / `portfolio_publish` | Proposed | ADR-034 |
| 057 | Hierarki kerja Program/Epic/Story/Task + merge queue + release train | Proposed | perluasan v2.1 |
| 058 | Edit manual & take-over worktree task | Proposed | §5.3, ADR-016, ADR-031 |
| 059 | Terminal interaktif sandbox untuk user (pengecualian WebSocket) | Proposed | ADR-014, FR-077 |
| 060 | Tier deployment S/M/L & pool executor | Proposed | §7.7, ADR-010, ADR-036 |
| 061 | Identitas enterprise & tenancy (OIDC, SAML, SCIM, organisasi) | Proposed | ADR-002 |
| 062 | Strategi repo besar & multi-repo | Proposed | §7.7, ADR-046 |
| 063 | Arsitektur language pack (polyglot) | Proposed | perluasan v2.2 |
| 064 | Proxy dependensi per ekosistem | Proposed | ADR-007 |
| 065 | Lingkungan integrasi multi-service ephemeral | Proposed | ADR-009, ADR-060 |
| 066 | Katalog service & integrasi contract-first | Proposed | ADR-057 |
| 067 | Stream usage realtime & rekonsiliasi Usage & Cost Admin API | Proposed | FR-133, ADR-053 |
| 068 | Target non-Linux di luar sandbox (iOS/macOS, Windows) | Proposed | ADR-009 |

---

## ADR-001 — Technology baseline MVP
**Keputusan:** Frontend React 19 + TypeScript + Vite, React Router, TanStack Query, Tailwind CSS + komponen gaya shadcn/ui (Radix), Lucide, Monaco (`@monaco-editor/react`, dimuat **lokal** bukan CDN), xterm.js, React Hook Form + Zod, Zustand selektif. Backend Python 3.12 (CI juga 3.13), FastAPI, Pydantic v2, SQLAlchemy 2 async (asyncpg), Alembic, Celery + Redis, PostgreSQL 16, SDK resmi `anthropic`. Testing: Pytest, Ruff, Pyright, Vitest + RTL, Playwright. Deployment: Docker Compose di Ubuntu Server LTS.
**Konsekuensi:** Versi dipin di `uv.lock` / `package-lock.json`; tidak ada auto-upgrade di production.

## ADR-002 — Identity
**Konteks:** Open decision #1 (single-user vs multi-user).
**Keputusan:** Pilot berjalan dalam *single-owner mode*: owner pertama dibuat via `python -m app.cli create-owner`; registrasi publik nonaktif. Skema, RBAC (owner/admin/developer/reviewer/readonly), dan isolasi per project tetap penuh sehingga admin dapat menambah user tanpa migrasi. Login email+password (argon2id), sesi opaque di DB (cookie HttpOnly), CSRF double-submit. SSO/OAuth = P1 setelah pilot.
**Konsekuensi:** Semua query project difilter oleh scope user (AC-01). Reset password via token yang dibuat admin (email = P1).

## ADR-003 — Git
**Keputusan:** Git lokal (init, branch, worktree, diff, commit lokal, merge lokal) = P0. Import `git_https` read-only (clone `--depth 1`) dari host allowlist (`github.com`, `gitlab.com`, `codeberg.org`, dapat diubah admin) = P1. Push/PR/merge remote = P2, R3 (approval), credential sebagai `credential_ref`. Force-push = R4 (ditolak).

## ADR-004 — Claude API key & model
**Keputusan:** `ANTHROPIC_API_KEY` hanya di env/secret file `worker` (dan `api` untuk smoke test), tidak pernah dikirim ke browser/log/sandbox/DB. Tidak ada endpoint untuk menerima API key. Model ID dari `CLAUDE_MODEL_DEFAULT|FAST|COMPLEX` dan `provider_configs.model_policy`; harga dari `backend/config/pricing.yaml` (berversi, nilai awal ditandai *perlu verifikasi* terhadap halaman harga resmi saat deployment). Budget pilot per task: `TASK_MAX_COST_USD=2.00`, `TASK_MAX_TOKENS=400000`, `CLAUDE_MAX_TOOL_ITERATIONS=40` per agent run, `TASK_MAX_DEBUG_ATTEMPTS=5`.
**Konsekuensi:** Readiness gate (`python -m app.cli claude-smoke`) wajib lulus sebelum coding agent diaktifkan (PRD §7.3.6).

## ADR-005 — Workspace storage
**Keputusan:** Bind volume `/srv/kodekantor/workspaces` (path identik host↔container). Kuota 2 GB per project (dicek saat import/tulis), upload arsip ≤ 200 MB, ekstraksi ≤ 1 GB, ≤ 50.000 file. Retensi: artefak log/screenshot 30 hari, task events 90 hari, audit 365 hari, project `deleted` dihapus fisik setelah 7 hari. Backup harian `pg_dump` + `tar` workspace (lihat RUNBOOK).

## ADR-006 — Deviasi state machine
**Konteks:** Tabel PRD §3.3 tidak memuat `running→cancelled`, padahal AC-14 mewajibkan task dapat dibatalkan saat berjalan; juga tidak ada jalur keluar dari `needs_clarification`.
**Keputusan:** Tambah transisi: `running|validating → cancelled`, `running|validating → interrupted` (validating), `needs_clarification → draft`, `draft → needs_clarification|failed`, `needs_changes → waiting_approval` (untuk waiver), `completed → queued` (reopen = iterasi baru, dipetakan dari "reopened"). Tidak ada transisi lain di luar `workflow/states.py::ALLOWED`.

## ADR-007 — Sandbox egress
**Keputusan:** Default `network_mode=none`. Profil `*.deps_install` (P1) memakai network `kk-egress` yang hanya menjangkau container `egress-proxy` (Squid/tinyproxy) dengan allowlist domain registry (`pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`). Aktif hanya bila `SANDBOX_EGRESS=allowlist`. Template project MVP dibuat agar test berjalan **tanpa** network (dependensi test sudah ada di image sandbox).

## ADR-008 — Preview
**Keputusan:** Preview app (P1) dilayani di `preview-<task8>.<APP_DOMAIN>` melalui Caddy → container preview di network terisolasi, sehingga cookie `kk_session` (host-only, tanpa atribut Domain) tidak terkirim ke preview. Preview tanpa akses ke API internal.

## ADR-009 — Docker access & isolation
**Konteks:** PRD melarang docker.sock di container agent, tetapi sandbox-manager tetap memerlukan Docker API — siapa pun yang menguasai Docker API setara root di host.
**Keputusan:** (1) sandbox-manager tidak me-mount socket; ia memanggil `docker-socket-proxy` (allowlist: `CONTAINERS=1, IMAGES=1, POST=1, PING=1, VERSION=1`, `INFO=0` — diperbarui ADR-026; `EXEC, VOLUMES, NETWORKS, SWARM, SERVICES, SECRETS, CONFIGS, BUILD, PLUGINS, SYSTEM=0`). (2) sandbox-manager hanya menerima `profile_id` dari daftar tetap, membangun `HostConfig` sendiri (non-root, cap_drop ALL, no-new-privileges, read-only rootfs, pids/mem/cpu limit, mount tunggal tervalidasi) — tidak ada field HostConfig dari caller. (3) `SANDBOX_RUNTIME=runsc` (gVisor) direkomendasikan di VPS production. (4) Token bearer internal + network `exec` privat.
**Konsekuensi / risiko sisa:** Socket proxy membatasi *endpoint*, bukan isi `HostConfig`; kompromi sandbox-manager tetap kritis → layanan ini kecil, tanpa dependensi LLM, diuji dengan fixture escape. Untuk multi-tenant publik: microVM (PRD §7.7).

## ADR-010 — VPS & concurrency
**Keputusan:** Pilot: 4 vCPU / 16 GB RAM / 200 GB SSD. `MAX_ACTIVE_TASKS=2`, `SANDBOX_CPU_LIMIT=1.0`, `SANDBOX_MEMORY_LIMIT=1g`, `SANDBOX_PIDS_LIMIT=256`, `SANDBOX_TIMEOUT_SECONDS=300`, Celery concurrency 2. Pindah worker/sandbox ke node terpisah bila p95 antrian > 5 menit atau RAM control plane > 70% selama 1 minggu.

## ADR-011 — Source code ke provider
**Keputusan:** Hanya snippet relevan (context package A04, maks 60 KB per request); file di denylist (`.env*`, kunci, `node_modules`, build output, `.agentignore`) tidak pernah dikirim; redaksi secret sebelum context dibangun. Setting project `provider_calls_enabled` (default true) — bila false, task tidak dapat memanggil LLM (kill switch). UI Settings menampilkan pernyataan data flow (PRD §7.3.5).

## ADR-012 — Agent runtime mapping
**Keputusan:** 42 profil (41 + orchestrator) ada di registry. Runtime LLM MVP: A02 (+A01), A03 (+C16), B14 (+profil B09/B10/B13/G35), C20 (+C21), D22, A07. Tool-mode: A04, C17, D23 baseline, E27, G37, H41. Sisanya `planned` dengan prioritas P1/P2 dan diaktifkan per wave (Phase 8) bila manfaat terukur.

## ADR-013 — Structured output
**Keputusan:** Setiap agent LLM wajib mengakhiri run dengan tool `submit_result` yang skemanya = output contract Pydantic. Validasi gagal → `tool_result is_error` (dihitung iterasi). Teks bebas tidak dipakai sebagai hasil.

## ADR-014 — Event streaming
**Keputusan:** Event persisten di `task_events` (seq per task) + Redis pub/sub untuk fan-out + SSE dengan `Last-Event-ID`. WebSocket tidak dipakai di MVP karena terminal bersifat read-only (tidak ada input interaktif).

## ADR-015 — ScriptedProvider
**Keputusan:** `LLM_PROVIDER=scripted` menjalankan agent deterministik (skenario di `backend/app/llm/scenarios/`) untuk unit/integration/E2E test dan demo tanpa API key. Config validator menolak `scripted` saat `APP_ENV=production`. UI menampilkan badge "Scripted provider (demo)" agar tidak disalahartikan sebagai hasil model.

## ADR-016 — Review-first worktree
**Keputusan:** Tiap task mendapat `git worktree` di `tasks/<task_id>` pada branch `kk/task-<8hex>` dari `base_commit`. Agent hanya menulis di sana. Apply = merge ke default branch setelah approval `apply_changes`; bila main berubah dan konflik → `needs_changes` (tidak ada overwrite).

## ADR-017 — Pyright
**Keputusan:** Pyright mode `standard` untuk `app/core`, `app/workflow`, `app/tools`, `app/llm`; pengecualian dicatat di `pyproject.toml`.

## ADR-018 — Enum VARCHAR+CHECK
**Keputusan:** Enum Python `StrEnum` disimpan sebagai `VARCHAR` dengan `CHECK`, agar migrasi penambahan nilai cukup mengganti constraint.

## ADR-019 — Caddy
**Keputusan:** Caddy (TLS otomatis, header keamanan, `reverse_proxy` dengan `flush_interval -1` untuk SSE). Nginx tidak dipakai.

## ADR-020 — Katalog FR turunan
**Konteks:** PRD memetakan modul ke FR-001..FR-159 tetapi tidak mendefinisikan isi FR.
**Keputusan:** `docs/FR_CATALOG.md` menurunkan 159 FR dari teks PRD (§4–§12) dengan rentang ID sesuai tabel modul, masing-masing diberi sumber (section PRD) dan AC terkait. Status: *derived — perlu review PO*. Traceability di tracker memakai ID ini.


---

> ADR-021 s.d. ADR-030 ditambahkan 21 Okt 2026 dari temuan build Wave 1–2. Status "Accepted (desain)" berarti keputusan sudah dikunci di rancangan, tetapi implementasinya **belum** dikerjakan (implementasi dihentikan atas instruksi Product Owner). Lihat `IMPLEMENTATION_TRACKER.md`.

## ADR-021 — Transisi state tambahan
**Konteks:** Desain pipeline (§5.2 S10, §5.2 agent results) membutuhkan jalur yang tidak ada di `ALLOWED`: (a) apply ditolak karena konflik merge, (b) aksi R3 ditolak user, (c) budget/approval terjadi saat fase planning (`draft`).
**Keputusan:** Tambah `waiting_approval → needs_changes` (merge conflict, `status_reason=merge_conflict`), `waiting_approval → failed` (aksi R3 ditolak dan tidak ada jalur alternatif), `draft → waiting_approval` (budget_increase/tool_r3 saat planning; approve → kembali `draft`, sehingga perlu juga `waiting_approval → draft`). Diagram di ARCHITECTURE §5.1 diperbarui.
**Konsekuensi:** `workflow/states.py::ALLOWED` dan test matriksnya harus diperbarui sebelum handler approval diaktifkan. Approval `budget_increase` yang ditolak → `cancelled`.

## ADR-022 — WorkflowPorts
**Konteks:** Aturan layering (§3.2) melarang `workflow` meng-import `services`, sedangkan engine butuh testing/acceptance/artifacts/approvals. Wave 2 menemukan dua modul memakai `importlib` sehingga edge tersebut tidak terlihat oleh import-linter.
**Keputusan:** Engine, AgentRuntime, dan builtin tools menerima dependensi lewat dataclass `WorkflowPorts`/`ToolPorts` (callable bertipe) yang dirakit di `services/tasks.build_orchestrator()` dan `agents/factory.py`. `importlib` untuk melompati layer **dilarang**; import-linter menambah kontrak "no dynamic layer bypass" (grep CI untuk `importlib.import_module("app.services`).

## ADR-023 — Encoding nama tool
**Konteks:** Nama tool Claude Platform hanya menerima `^[a-zA-Z0-9_-]{1,64}$`; katalog memakai titik (`repo.read_file`).
**Keputusan:** Nama kanonik tetap bertitik di registry/DB/UI. Hanya `AnthropicClaudeProvider` yang meng-encode `.`→`__` saat request dan men-decode saat response. Nama tool tidak boleh mengandung `__`.

## ADR-024 — Transaksi approval handler
**Keputusan:** `decide()` meng-commit keputusan lebih dulu (keputusan manusia final dan ter-audit), lalu `dispatch()` memanggil handler dengan transaksi sendiri; handler **wajib commit** sendiri dan idempotent (memeriksa status approval/task sebelum bertindak). Kegagalan handler dicatat (`approval.handler_failed` di audit + event `task.log`) tanpa membatalkan keputusan. Kolom `approvals.handler_status/handler_attempts/handler_error` melacak eksekusi; recovery sweep mencoba ulang (maks 5, backoff) **semua** approval terputus dengan `handler_status ∈ {pending, failed}` — termasuk yang tanpa task seperti `project_delete`.

## ADR-025 — Nilai enum tambahan
**Keputusan:** `usage_records`/agregat: `cost_status ∈ {estimated, partial, unavailable}` (`actual` dicadangkan bila provider kelak mengirim biaya). `agent_runs.status` menambah `approval_required` (bukan `failed` + string error). Migrasi `0002` mengganti CHECK constraint (ADR-018).

## ADR-026 — Hardening sandbox lanjutan
**Keputusan:** (1) `.git` di workspace yang di-mount disembunyikan: direktori → tmpfs kosong read-only; file worktree → bind file kosong read-only; `.git` symlink/aneh → run ditolak `GIT_METADATA_UNSAFE`. (2) Image sandbox hanya dari cache lokal; tidak ada pull otomatis (`IMAGE_MISSING`). (3) Flag docker-socket-proxy minimum yang terbukti dibutuhkan: `CONTAINERS=1, IMAGES=1, POST=1, VERSION=1, PING=1`; `INFO=0`. Flag `ALLOW_START/STOP/RESTARTS` tidak efektif pada proxy tersebut sehingga tidak dijadikan kontrol. (4) GitAdapter menolak repo dengan key config di luar allowlist, object alternates, atau file `.git` worktree yang dipalsukan, serta selalu memakai `-c core.fsmonitor=false -c core.hooksPath=/dev/null`.

## ADR-027 — Strategi remediasi
**Keputusan:** Kegagalan objektif dari User Proxy → iterasi remediasi dengan **langkah plan baru** "Perbaiki gap: …" (B14) membawa `gap_feedback`, tanpa re-plan penuh (lebih murah dan dapat dilacak). Re-plan (S1–S3) hanya bila user mengubah baseline requirement (`PUT /tasks/{id}/requirements`). `USER_PROXY_ACCEPTANCE.md` §7 diselaraskan.

## ADR-028 — Dispatcher
**Keputusan:** Semua enqueue lewat `workflow/dispatch.py::Dispatcher` (`CeleryDispatcher` production, `RecordingDispatcher` test). `CELERY_TASK_ALWAYS_EAGER` tidak boleh dipakai dari konteks async (memanggil `asyncio.run` di dalam loop); test memanggil engine secara langsung.

## ADR-029 — Penyelarasan kontrak API v1.1
**Keputusan:** Field/endpoint yang dibutuhkan UI dan dikunci di `API_CONTRACTS.md §11`.

## ADR-030 — Redaksi tambahan
**Keputusan:** `core/redaction.py` wajib juga menangkap `key=value`/`key: value` huruf kecil untuk nama sensitif (`password`, `passwd`, `token`, `secret`, `api_key`, `access_key`), `.netrc` `password`, dan header `X-Api-Key`. Wajib test negatif (tidak menyensor kode biasa seperti `token_count = 3`).

## ADR-031 — Pause & batas durasi
**Konteks:** FR-057 meminta pause/resume; PRD §3.3 tidak punya state `paused`. FR-132 meminta batas durasi.
**Keputusan:** Pause dipetakan ke `interrupted` dengan `status_reason=paused_by_user` (tanpa state baru): `POST /tasks/{id}/pause` men-set `tasks.pause_requested`; worker berhenti di batas langkah berikutnya setelah checkpoint. Batas durasi total `max_duration_minutes` (default `TASK_MAX_DURATION_MINUTES=60`) diperlakukan seperti budget habis.

## ADR-032 — Readiness gate yang mengikat
**Keputusan:** Hasil `claude-smoke`/`POST …/smoke-test` disimpan di `provider_configs.readiness_status/_checked_at/_report`. Dengan `LLM_REQUIRE_READINESS=true` (wajib di production), AgentRuntime menolak panggilan LLM selama status ≠ `passed`; perubahan `model_policy` atau rotasi key mengembalikan status ke `unknown`.

## ADR-033 — Sandbox read-only & disk
**Keputusan:** `SandboxRunRequest.read_only`; `target: main` selalu read-only dengan tmpfs untuk `/workspace/.kk` dan artefak dibaca lewat archive API (sudah diizinkan `CONTAINERS=1`). Pertumbuhan disk per run dibatasi `SANDBOX_MAX_WORKSPACE_GROWTH_MB`; production memakai kuota XFS project per project.
**Konsekuensi:** Kode yang diuji tidak dapat mengubah working tree user tanpa melalui review (R-07).

## ADR-034 — Matriks approval
**Keputusan:** Tabel di API_CONTRACTS §11.1 mengikat: pembuat, payload, handler, kedaluwarsa, izin pemutus per `action_type`, dan aturan self-approval. Izin baru `project:delete` dan `deploy:production` (owner/admin). `waive_criterion` bukan approval. Policy `four_eyes` (P1) untuk tim.
**Catatan pilot:** dalam mode single-owner (ADR-002), PO adalah satu-satunya user manusia sehingga praktis menjadi satu-satunya pemutus; secara formal yang menentukan adalah izin pada tabel.

## ADR-035 — Jenis kegagalan test
**Keputusan:** `test_runs.failure_kind ∈ {none, assertion, lint, build, dependency, runner_crash, timeout, infra}` ditentukan deterministik dari profil, exit code, dan pola output runner (bukan oleh LLM). Status `passed` hanya dari exit code 0.


---

> ADR-036..045 (21 Okt 2026) berasal dari permintaan PO memperluas KodeKantor menjadi Portal IDE + Portal AI + Portal Portfolio, dengan koneksi GitHub privat. Detail di `docs/PLATFORM_EXPANSION.md`. Semua berstatus **Proposed** dan baru berlaku setelah portal IDE lulus gate G3.

## ADR-036 — Tiga portal, satu core
**Keputusan:** Satu codebase *modular monolith*; modul domain baru `assistant/`, `integrations/github/`, `portfolio/`; core (auth, RBAC, audit, usage, provider gateway, tool gateway, approval, asset store) dipakai bersama. Antrean Celery dipisah `ide`, `assistant`, `media`, `integrations`. Frontend satu SPA dengan tiga ruang kerja.
**Alasan:** biaya operasi single-VPS; aturan keamanan & audit tetap satu pintu.

## ADR-037 — Provider gateway per capability
**Keputusan:** Claude tetap provider utama teks/agent. Ditambah interface `ImageProvider` dan `DocumentEngine`; routing per capability di `config/providers.yaml`. Vendor baru wajib ADR + contract test + evaluasi biaya/kualitas (PRD §7.7). Mengubah ADR-004/012 dari "hanya Claude" menjadi "Claude utama, vendor lain per capability".

## ADR-038 — Riset web bersitasi
**Keputusan:** Riset memakai server tool Claude `web_search` (versi dipin di config) + `web_fetch`; sitasi selalu ditampilkan, disimpan dengan waktu pencarian; jawaban tanpa sumber diberi label. Domain allow/block per policy; `max_uses` per mode; anggaran pencarian per user. Konten web = data tidak tepercaya.

## ADR-039 — Engine dokumen ganda
**Keputusan:** `claude_skills` (Agent Skills `pptx/docx/xlsx/pdf` via code execution, file via Files API) untuk data non-sensitif; `local_sandbox` (python-pptx/python-docx/openpyxl di image sandbox) wajib untuk data berlabel `confidential` atau saat kill switch provider aktif. Alasan: Skills tidak tercakup ZDR.

## ADR-040 — Generate gambar lewat adapter netral
**Keputusan:** `ImageProvider` dengan moderasi prompt + moderasi vendor, provenance, kuota. Vendor pertama ditetapkan di ADR-040a saat deployment setelah uji kualitas teks, edit, biaya, dan kebijakan data vendor. Self-hosted ditunda (butuh GPU).

## ADR-041 — GitHub App untuk repo privat
**Keputusan:** GitHub App dengan permission minimum (Metadata R, Contents R/W, Pull requests R/W, Checks R; **tanpa** Workflows/Administration/Secrets). Token instalasi dibuat per operasi, dibatasi ke satu repo & permission minimum, tidak disimpan. Clone/push oleh worker tanpa token di URL/config. Push branch & buka PR = R3 (approval terikat repo+branch+head_commit); force push/merge/ubah workflow = R4. Webhook diverifikasi HMAC SHA-256 + dedupe. Menggantikan rencana remote P2 di ADR-003 menjadi fase P9.
**Catatan (21 Okt 2026, ADR-053):** private key dan `GITHUB_CLIENT_SECRET` hanya ada di container `worker-integrations`; `api` hanya memegang `GITHUB_WEBHOOK_SECRET`. Kalimat lama "di-mount ke `worker` dan `api`" tidak berlaku.

## ADR-042 — Portfolio profesional
**Keputusan:** Model konten profil/item/versi/publikasi; impor dari GitHub & project KodeKantor; CV via DocEngine; AI hanya draft (tidak boleh mengarang pencapaian/testimoni); publikasi situs = R3 dengan pratinjau dan cek privasi.

## ADR-043 — pgvector untuk tanya jawab dokumen (Superseded oleh ADR-046)
**Keputusan:** Ekstensi `pgvector` di PostgreSQL yang sama untuk `kb_chunks` (Portal AI). Index repo IDE tetap tanpa vector (PRD §7.7) sampai metrik context selection menuntut.

## ADR-044 — Capability Module Registry
**Keputusan:** `config/capabilities.yaml` + tabel `capability_settings`: tiap modul punya provider, tools, risiko, budget default, izin, feature flag. UI dan API hanya mengekspos modul aktif; usage dicatat di `capability_usage`.

## ADR-045 — Tata kelola data perluasan
**Keputusan:** Label sensitivitas per Ruang/project (`public`, `internal`, `confidential`); tabel data-flow per capability di Settings (data apa ke vendor mana); retensi per jenis aset; ekspor & hapus data pengguna; ancaman T11–T18 masuk SECURITY_MODEL dan wajib diuji di fase masing-masing.


## ADR-046 — Tanya jawab dokumen tanpa embedding (menggantikan ADR-043)
**Konteks:** PO meminta seluruh kemampuan AI memakai Claude Platform API. Anthropic tidak menyediakan model embedding sendiri (dokumentasi Embeddings, Okt 2026), sehingga pgvector membutuhkan provider ketiga.
**Keputusan:** Retrieval leksikal di PostgreSQL (`tsvector` konfigurasi `simple` + `pg_trgm`) atas potongan dokumen; saat ingest, Claude membuat ringkasan kontekstual + kata kunci per potongan untuk menaikkan recall (teknik *contextual retrieval*). Saat menjawab, potongan teratas dikirim sebagai blok `search_result` dengan `citations.enabled = true` sehingga Claude mengutip sumber (`search_result_location`). Dokumen kecil (≤ ±100 halaman) boleh dikirim utuh sebagai blok `document` bersitasi (PDF/teks).
**Konsekuensi:** Tidak ada provider tambahan. Kualitas pencarian semantik lintas sinonim lebih rendah daripada embedding; diukur di WP-11.3. Bila gagal ambang, ADR baru dapat menambah `EmbeddingProvider` (mis. Voyage AI atau `voyage-4-nano` self-hosted) dengan persetujuan PO. Files API tidak eligible ZDR → untuk data `confidential`, teks potongan dikirim inline, bukan via Files API.

## ADR-047 — Jawaban bersitasi
**Konteks:** Citations Claude tidak kompatibel dengan structured outputs (`output_config.format` → 400) dan sitasi melekat pada blok teks.
**Keputusan:** Agent bersitasi (I42 untuk jawaban, I43, I47, K50 saat mengutip lowongan) mengakhiri run dengan **teks bersitasi**; backend mengekstrak `citations` dari blok teks (web_search_result_location, search_result_location, char/page_location) ke tabel `message_citations`. `submit_result` hanya dipakai untuk metadata non-teks (mis. routing I42) atau oleh agent tanpa sitasi. Pengecualian terdokumentasi terhadap ADR-013.

## ADR-048 — Katalog agent v2
**Keputusan:** Registry bertambah 11 agent (I42–I47, J48, K49–K51, S52) dan E29 dipromosikan ke `target_mode: llm` fase P9; semua tetap `mvp_mode: planned`, `enabled: false` sampai fasenya. Prinsip §14.1 #7 tetap: J48, K51, S52 deterministik (tool-mode).
**Catatan penyimpanan:** registry v2 (schema_version 2, 53 entri) disimpan di `agents/registry.v2.yaml` dan divalidasi `scripts/validate_registry.py`. `agents/registry.yaml` tetap v1 (42 entri) karena loader backend snapshot hanya menerima schema 1; `scripts/validate_registry.py` mendelegasikan file schema 1 ke `scripts/validate_registry_v1.py`. Migrasi loader + penggantian file = WP-9.0.

## ADR-049 — Tech stack v2
**Keputusan:** Tabel K4 di `PLATFORM_EXPANSION.md` §9b mengikat. Tambahan utama: fitur Claude (server tools, Skills, Files API, citations), image provider via adapter, PyJWT untuk GitHub App, image sandbox `kodekantor/sandbox-docs`, Jinja2, react-markdown/rehype-sanitize/Shiki/pdf.js. Tidak menambah vector DB, message bus, atau Kubernetes.

## ADR-050 — RunScope: run & tool record tanpa task
**Konteks:** `agent_runs.task_id`/`tool_calls.task_id` NOT NULL dan `AgentRuntime`/`ToolContext` task-sentris, padahal agent Portal AI/Portfolio berjalan di thread tanpa task.
**Keputusan:** `RunScope = TaskScope | ThreadScope`; `ToolContext` membawa task/workspace opsional; `agent_runs` mendapat `thread_id` + CHECK `num_nonnulls(task_id, thread_id) = 1`; `tool_calls` mendapat `thread_id` + CHECK `≤ 1` (job tool-mode tanpa scope, mis. impor repo, publish K51) (migrasi 0003). Rinci: `IMPLEMENTATION_DESIGN_V2.md` §5.0, §7.2 (usulan U-01).
**Konsekuensi:** Satu runtime dan satu ToolGateway untuk tiga portal; event task tetap seperti v1.

## ADR-051 — Server tool Claude tetap lewat ToolGateway
**Konteks:** `web_search`, `web_fetch`, code execution + Skills dieksekusi di sisi Anthropic, sehingga tidak melewati `ToolGateway.execute`. Prinsip CLAUDE.md #2 (satu pintu side effect) harus tetap berlaku.
**Keputusan:** Gateway mendapat jalur `authorize_server_tools` (sebelum request: allowlist agent, kuota, sensitivitas data, kill switch) dan `record_server_tool_calls` (sesudah: setiap `server_tool_use` dicatat ke `tool_calls` dengan `executor = provider` + audit). Permission v2: `network: provider_web`, `data_sensitivity_max`. Rinci: desain v2 §5.15.2 (U-02).
**Konsekuensi:** Server tool tidak dapat dipakai agent yang tidak diizinkan; biaya & jejak tercatat. Isi eksekusi di container Anthropic tetap di luar kendali kita (lihat ADR-039, ZDR).

## ADR-052 — ApprovalGrant untuk eksekusi R3 multi-langkah
**Konteks:** Satu approval `github_push_pr` mencakup dua tool R3 (`github.push_branch`, `github.open_pr`); gateway v1 mengikat approval ke satu `{tool, input}`.
**Keputusan:** Approval di-`consume()` **sekali** oleh job server, menghasilkan `ApprovalGrant` dalam memori (TTL 15 menit) yang hanya mengizinkan langkah dengan field terikat payload (repo, branch, head_commit, sha). LLM tidak dapat membuat, memperluas, atau memperpanjang grant. Rinci: desain v2 §5.15.3 (U-03).
**Konsekuensi:** Head berubah → `HEAD_MOVED`, approval `superseded`; tidak ada eksekusi parsial tanpa jejak.

## ADR-053 — Worker per antrean & secret per role
**Konteks:** Antrean `ide/assistant/media/integrations` (ADR-036) berbagi satu container `worker` membuat semua secret ada di satu proses; `api` tidak di network `outbound` (ARCHITECTURE §2) sehingga tidak dapat memanggil GitHub.
**Keputusan:** Production memakai satu container per antrean dengan `KK_PROCESS_ROLE`; secret hanya di role yang butuh: pem GitHub App + `GITHUB_CLIENT_SECRET` hanya `worker-integrations`; `IMAGE_PROVIDER_API_KEY` hanya `worker-media`; `api` hanya `GITHUB_WEBHOOK_SECRET`. Mode compact (satu worker) hanya non-production. Rinci: desain v2 §3.2, §8.1 (U-04).
**Konsekuensi:** RAM pilot ±12,25 GB dari 16 GB (±12,5 GB termasuk scheduler) [Menebak]; kompromi satu worker tidak membocorkan semua kunci.

## ADR-054 — Workspace job & image sandbox-docs
**Konteks:** sandbox-manager hanya menerima workspace `<project>/tasks/<task>` atau `<project>/repo`; pekerjaan Portal AI/Portfolio tidak punya project.
**Keputusan:** Pola workspace ketiga `_jobs/<uuid>` di bawah `WORKSPACE_ROOT` (input `/workspace/in` read-only), image `kodekantor/sandbox-docs` (python-pptx/docx, openpyxl, pandas, matplotlib, LibreOffice headless) dengan profil tetap `docs.*`, `kb.extract`, `analysis.python`, `portfolio.build_site`; tetap tanpa network. Rinci: desain v2 §3.5 (U-05).
**Konsekuensi:** HostConfig tetap tidak dapat dipengaruhi caller (ADR-009); image baru wajib scan & pin digest.

## ADR-055 — Asset store & URL bertanda tangan
**Konteks:** File hasil (dokumen, gambar, CV, bundle situs) perlu diunduh tanpa membuka filesystem.
**Keputusan:** `ASSETS_ROOT` di filesystem dengan metadata di DB (checksum, provenance, sensitivitas, retensi); unduhan lewat URL bertanda tangan HMAC (TTL `ASSET_SIGNED_URL_TTL_SECONDS`) yang tetap mensyaratkan sesi user yang sama; HTML tidak pernah disajikan inline dari origin aplikasi. Rinci: desain v2 §5.9, §9.9 (U-06).
**Konsekuensi:** URL bocor tidak cukup untuk mengunduh; backup mencakup `ASSETS_HOST_PATH`.

## ADR-056 — Scope approval v2 & action type baru
**Konteks:** `approvals.project_id` wajib, padahal publikasi portfolio tidak terikat project; `git_push` (P2) memakai `four_eyes`, sedangkan pilot single-owner hanya punya satu pemutus.
**Keputusan:** `approvals.project_id` nullable + `scope_type` (`project`/`portfolio`/`integration`) + `owner_id`. Action type baru `github_push_pr` (self-approval boleh di pilot; dilarang bila policy `four_eyes` aktif, usulan aktif otomatis bila user > 1) dan `portfolio_publish` (hanya pemilik profil). `git_push` tetap dicadangkan untuk remote generik non-GitHub. Matriks mengikat: API_CONTRACTS §12.6 (U-07).
**Konsekuensi:** Kontrak approval v1 tidak berubah untuk aksi project; migrasi 0003 menambah kolom & CHECK.

## ADR-057 — Hierarki kerja Program/Epic/Story/Task, merge queue, release train
**Konteks:** PO meminta Portal IDE mendukung SDLC end-to-end dari project kecil sampai mega project. Model v1 hanya punya task (unit eksekusi agent) dan apply langsung ke default branch (ADR-016).
**Keputusan:** Tabel `work_items` (program/epic/story + `parent_id`) dengan task tetap sebagai unit eksekusi (`tasks.work_item_id`, opsional di tier S). Status story/epic/program diturunkan deterministik dari task & AC. Dekomposisi oleh A03 (`ProgramPlan`) hanya usulan yang wajib disetujui user. Merge queue per repo menserialisasi apply: rebase ke head terbaru → profil test wajib (+status CI bila sah, `SDLC_SCALE_DESIGN.md` K13) → merge; gagal → `needs_changes`, branch target tidak berubah. Release train (P2) mengelompokkan story `accepted`. Rinci: `SDLC_SCALE_DESIGN.md` §3.
**Konsekuensi:** Branch target dengan merge queue aktif hanya berisi commit yang lulus test wajib (NFR-27); throughput dibatasi antrean dan review manusia — dipantau di G13.

## ADR-058 — Edit manual & take-over worktree
**Konteks:** Edit manual sudah P0 (FR-026..032), tetapi aturan saat user dan agent menyentuh kode yang sama belum ada.
**Keputusan:** Tiga mode kerja: edit repo (commit user ke `kk/user-*`, masuk default branch lewat merge queue bila aktif), pantau task (read-only), take-over task. Take-over diizinkan dari `running` (di checkpoint), `waiting_approval`, `needs_changes`, `failed` → `interrupted` dengan `status_reason=user_takeover` (pola ADR-031, tanpa state baru); perubahan dicommit atas nama user di branch task; bukti lama ditandai stale; "Selesai take-over" → `queued` dengan rencana validasi saja (test → review → User Proxy). Agent tidak pernah menimpa perubahan user; konflik = 409 + diff 3-way.
**Konsekuensi:** Transisi baru di `workflow/states.py` (WP-13.1) dan test transisi; User Proxy tidak pernah menilai kode yang belum diuji ulang setelah edit manusia.

## ADR-059 — Terminal interaktif sandbox untuk user
**Konteks:** FR-077 (terminal read-only) dan ADR-014 (tanpa WebSocket) membuat IDE tanpa shell; developer membutuhkan shell untuk pekerjaan manual.
**Keputusan:** Sesi PTY di container sandbox project (bukan host) hanya untuk manusia, dengan network off default, limit resource sandbox, durasi maks `TERMINAL_SESSION_MAX_MINUTES` dan idle timeout, audit input/output teredaksi. Kanal WebSocket **hanya** untuk endpoint terminal, dengan tiket sekali pakai + pemeriksaan Origin. Default nonaktif; admin mengaktifkan per project (`terminal:use`). Agent tetap hanya profil allowlist (FR-078); tidak ada tool terminal untuk agent.
**Konsekuensi:** Ancaman T19 wajib diuji (WP-13.3, sign-off AG-13); ADR-014 tetap berlaku untuk semua stream lain.

## ADR-060 — Tier deployment S/M/L & pool executor
**Konteks:** Pilot 1 VPS (ADR-010) tidak cukup untuk tim dan enterprise; ADR-036 menolak Kubernetes untuk pilot.
**Keputusan:** Satu basis kode, tier = konfigurasi (`DEPLOYMENT_TIER`) + topologi. Tier M: node kontrol + 1–4 node executor (worker + sandbox-manager) dengan token node terotasi, lease task, failover dari checkpoint, workspace di volume bersama atau git bundle per task. Tier L: pool executor terskala; pilihan VM pool vs Kubernetes diputuskan di WP-14.0 berdasarkan data beban tier M. Angka kapasitas tier = target [Menebak] yang diukur di G13/G14.
**Konsekuensi:** Tier S tetap identik dengan pilot; risiko sisa kredensial di node tier M dicatat (OI-32).

## ADR-061 — Identitas enterprise & tenancy
**Konteks:** ADR-002 menyiapkan skema multi-user + RBAC tetapi tanpa SSO, SCIM, atau organisasi.
**Keputusan:** Tier M: OIDC dengan pemetaan grup → role, login lokal dapat dimatikan. Tier L: SAML, SCIM (deprovision mencabut sesi ≤ 5 menit [Menebak]), organisasi/tenant dengan scope organisasi pada setiap query, path workspace, cache, budget, dan audit. Pertukaran code OIDC dan metadata SAML dilakukan `worker-integrations` karena `api` tidak punya akses outbound (ADR-053).
**Konsekuensi:** Ancaman T20, T22 wajib diuji; JIT provisioning nonaktif default.

## ADR-062 — Repo besar & multi-repo
**Konteks:** Mega project memakai monorepo besar dan/atau banyak repo; konteks model terbatas.
**Keputusan:** Sparse/partial checkout per task, index simbol inkremental (ctags/tree-sitter) di sandbox, batas konteks yang ditegakkan A04; satu story dapat mencakup beberapa repo (task per repo, merge queue per repo, release mengikat versi lintas repo). Embedding tetap dikecualikan (ADR-046) kecuali ADR baru berdasarkan benchmark AX-18.
**Konsekuensi:** Tidak ada janji "seluruh repo dalam konteks"; kualitas pemilihan konteks diukur di WP-14.7.

## ADR-063 — Arsitektur language pack
**Konteks:** PO meminta KodeKantor dapat dipakai membangun sistem enterprise dengan banyak bahasa (gelombang 1: Java/Kotlin, C#/.NET, Go, PHP/Laravel, Python, SQL, Node.js/JS/TS/React, React Native, Flutter, Ruby, C++). Snapshot hanya punya `sandbox-python` dan `sandbox-node`.
**Keputusan:** Satu ekosistem = satu *language pack* berversi: image toolchain dipin digest, deteksi stack, profil tetap (`deps_install`, `build`, `test`, `test_select`, `lint`, `fmt`, `run`, `package`), registry dependensi, template, petunjuk agent, set evaluasi. Tingkat L1 (penuh) / L2 (build & test). Pack hanya aktif untuk agent setelah set evaluasinya lulus (G8 per pack). Rinci: `POLYGLOT_INTEGRATION_DESIGN.md` §1.
**Konsekuensi:** Disk & waktu build bertambah (JVM/Android); kualitas per bahasa terukur, bukan diasumsikan.

## ADR-064 — Proxy dependensi per ekosistem
**Konteks:** ADR-007 hanya mengizinkan PyPI dan npm lewat egress proxy.
**Keputusan:** Semua instalasi dependensi lewat proxy repositori ber-allowlist + cache per ekosistem (Maven/Gradle, NuGet, Go proxy, Packagist, RubyGems, pub.dev, Conan/vcpkg, PyPI, npm); lockfile wajib; namespace tunggal per registry untuk mencegah dependency confusion; hanya profil `*.deps_install` yang mendapat network ke proxy. Produk proxy diputuskan di WP-15.0 (OQ-04).
**Konsekuensi:** Gate keamanan G4 berlaku per ekosistem; ancaman T24 diuji.

## ADR-065 — Lingkungan integrasi multi-service ephemeral
**Konteks:** Sistem enterprise terintegrasi tidak terbukti oleh unit test per bahasa; ADR-009 hanya mengenal profil satu container.
**Keputusan:** Spec deklaratif (service dari pack terdaftar + katalog service pendukung terkunci: PostgreSQL, MySQL, SQL Server dev, Redis, Kafka/RabbitMQ, penyimpanan objek tiruan) divalidasi policy: tanpa privileged/host mount/host network/device, tanpa egress, port tidak dipublikasikan; satu network internal per lingkungan; kuota service/CPU/RAM; TTL & teardown otomatis; data seed & secret palsu. Hanya tier M/L; tier S memakai mode kontrak + mock. ADR-009 diperluas dari "profil tetap" menjadi "template lingkungan tetap" — caller tetap tidak dapat memengaruhi HostConfig.
**Konsekuensi:** Ancaman T25, T27, T28 wajib diuji (WP-16.0/16.1, sign-off AG-13).

## ADR-066 — Katalog service & integrasi contract-first
**Keputusan:** Katalog service per program; kontrak OpenAPI/AsyncAPI/Protobuf/GraphQL di repo sebagai sumber kebenaran; diff kontrak mendeteksi breaking change dan memblokir merge queue sampai consumer diperbarui atau pengecualian disetujui; mock consumer dibangkitkan dari kontrak; contract test + E2E lintas service menjadi syarat merge queue untuk story lintas service.
**Konsekuensi:** Integrasi lintas tim/bahasa ditegakkan oleh kontrak, bukan kesepakatan informal.

## ADR-067 — Stream usage realtime & rekonsiliasi
**Konteks:** PO meminta halaman monitoring biaya & token realtime. Usage per event stream Claude tersedia seketika (`message_delta` kumulatif); angka resmi Usage & Cost Admin API muncul ≈ ≤ 5 menit (usage bucket 1m) dan biaya resmi harian; polling disarankan 1×/menit; Admin API tidak tersedia untuk akun individual.
**Keputusan:** Worker menerbitkan `usage.event` per panggilan model/vendor (Redis pub/sub → SSE `/usage/live`), rollup per menit, alert budget 50/80/100%. Rekonsiliasi opsional (job ≥ 60 dtk) di `worker-integrations` dengan kredensial Admin API yang hanya dipakai untuk endpoint usage/cost. Setiap angka berlabel aktual/estimasi/resmi/tidak tersedia.
**Konsekuensi:** Biaya realtime selalu estimasi; selisih terhadap angka resmi ditampilkan; ancaman T26 diuji.

## ADR-068 — Target non-Linux di luar sandbox
**Keputusan:** Build iOS/macOS (Flutter/React Native iOS), Windows/.NET Framework, dan emulator Android untuk E2E tidak dijalankan di sandbox VPS; KodeKantor menandainya dan membaca status CI eksternal (runner macOS/Windows milik pengguna) sebagai bukti. Build Android dan web Flutter tetap di sandbox.
**Konsekuensi:** Tidak ada janji build iOS dari KodeKantor; PO memutuskan runner eksternal bila dibutuhkan (OQ-05).

## Pengecualian keamanan (SECURITY_MODEL §11)

Temuan audit yang belum dapat diperbaiki dicatat di sini: siapa, alasan, mitigasi, tanggal kedaluwarsa. Job CI yang mengabaikan temuan hanya mengabaikannya sampai tanggal kedaluwarsa, lalu merah lagi.

| ID | Temuan | Komponen | Siapa | Alasan | Mitigasi | Kedaluwarsa |
|---|---|---|---|---|---|---|
| EX-01 | `PYSEC-2026-4321` (GHSA-hq85-5cpj-mr62): bundle root bawaan uv masih memuat emSign ECC G3 yang kunci intermediate-nya bocor; diperbaiki di uv 0.13.4 | uv 0.11.32: build image (`infra/build/uv-requirements.txt`) dan job CI (`setup-uv`) | Diusulkan Lead (sesi WP-1.5a, temuan M1 review AG-13, 25 Okt 2026); **menunggu persetujuan PO** | uv 0.13.4 dirilis 20 Okt 2026, belum lolos aturan umur dua minggu (PRD v2.2 §8) | `uv sync --locked` memverifikasi hash setiap paket dari `uv.lock`, sehingga MITM TLS tidak dapat menukar paket; uv sendiri dipasang dengan `--require-hashes`; uv tidak ada di image runtime | ~~2026-11-03~~ **Ditutup 2026-10-30**: uv dinaikkan ke 0.13.4 di `infra/build/lock-uv.sh` dan semua `setup-uv`. Perbaikan keamanan mengalahkan aturan umur per paket (preseden certifi, `backend/pyproject.toml`); langkah "Build tool audit" berjalan tanpa pengecualian |
| EX-02 | Temuan HIGH yang **tidak punya versi perbaikan** di distro/upstream (31 Okt 2026: 44 per image Debian 13: util-linux, mount, login, ncurses, perl-base, libsystemd; 1 di caddy: go-jose v3) | Semua image yang di-scan `security-scan` (`infra/security/scan-exceptions.toml` `[unfixed_high]`) | Diusulkan Lead (sesi WP-1.5d, 31 Okt 2026); **menunggu persetujuan PO** | Memblokir tidak memperbaiki apa pun: belum ada versi perbaikan. Perintah literal SECURITY_MODEL §11 akan merah permanen (konflik #56) | Container non-root, `cap_drop: ALL`, `no-new-privileges`, rootfs read-only (kecuali volume data postgres/redis), sandbox `network_mode: none`; paket terbanyak (util-linux, mount, login) butuh CAP_SYS_ADMIN/setuid yang tidak ada. Setiap run mencetak daftarnya. Begitu ada versi perbaikan, temuan langsung memblokir (lewat nightly), sehingga base digest harus dinaikkan. CRITICAL tanpa perbaikan tetap memblokir | 2027-01-31 |
| EX-03 | Temuan yang **punya perbaikan, tetapi digest upstream terbaru belum membawanya**; daftar CVE + paket per image di `infra/security/scan-exceptions.toml` | Image pihak ketiga yang dipin digest (caddy, web placeholder nginx-unprivileged, docker-socket-proxy) dan dependensi npm bawaan Node di `kodekantor/sandbox-node` | Diusulkan Lead (sesi WP-1.5d, 31 Okt 2026); **menunggu persetujuan PO** | Digest terbaru upstream per 31 Okt belum membawa perbaikan (caddy 2.11.7 21 Okt, socket-proxy v0.5.0 = `latest`, nginx 1.30-alpine 19 Okt, node 22.23.4 28 Okt). Untuk caddy, membangun ulang sendiri (xcaddy, Go ≥ 1.26.10, x/net 0.60.0) bisa dilakukan, tetapi belum dikerjakan | **caddy, per CVE (review AG-13 WP-1.5d):** CVE-2026-78669 (DoS frame HTTP/2 SETTINGS, stdlib + x/net) **dapat dipicu tanpa autentikasi di :443 publik karena h2 aktif**: syarat sebelum deploy publik (WP-7.1/G7) adalah caddy dibangun ulang dengan Go/x-net yang diperbaiki, atau `servers { protocols h1 h3 }` sementara (biaya: SSE di HTTP/1.1 terbatas 6 koneksi per host); 78667 hanya lewat `file_server` (tidak dipakai); 97031 hanya bila ECH dikonfigurasi (tidak); 111943 dan 116587 di sisi klien ACME [Kemungkinan Besar]; brotli/curl hanya dipakai biner `curl`, yang tidak dijalankan (healthcheck `wget` busybox). **nginx:** brotli-libs juga ditarik freetype, tetapi biner nginx hanya me-link pcre2/ssl/crypto/z dan config tidak memuat modul. **socket-proxy:** CVE pcre2 butuh pola regex dari penyerang (config HAProxy tetap); CVE OpenSSL hanya untuk server QUIC (tidak ada). **npm:** berjalan di sandbox tanpa network (hanya profil `node.npm_test`); begitu profil node dengan network (deps_install, ADR-007) masuk, entri npm dievaluasi ulang (`ip-address` relevan untuk SSRF). Dicek ulang bulanan (RUNBOOK §12.1) | 2026-11-30 |
| EX-04 | Semua temuan di `usr/local/bin/gosu` (Go stdlib 1.24.6, 31 Okt: 1 CRITICAL + 25 HIGH) | `postgres:16.15-alpine` | Diusulkan Lead (sesi WP-1.5d, 31 Okt 2026); **menunggu persetujuan PO** | `gosu` hanya dipanggil `docker-entrypoint.sh` saat container berjalan sebagai root. Compose menjalankan postgres sebagai `70:70`, jadi gosu tidak pernah dieksekusi; image terbaru (21 Okt) membawa gosu yang sama | `infra/tests/test_scan_gate.py` memastikan `postgres.user` tetap `70:70` selama EX-04 ada | 2027-01-31 |
| EX-05 | Semgrep `python.sqlalchemy.security.audit.avoid-sqlalchemy-text` (ERROR, rule audit) | `backend/app/core/db_roles.py` `grant_runtime_role` (`# nosemgrep` di baris `conn.execute`) | Diusulkan Lead (sesi WP-1.5d, 31 Okt 2026); **menunggu persetujuan PO** | False positive: GRANT/REVOKE tidak bisa memakai bind parameter untuk identifier. Satu-satunya nilai variabel adalah nama role yang lolos regex allowlist `[a-z_][a-z0-9_]{0,62}` dan harus ada di `pg_roles`; sisanya konstanta | Validasi dan test `backend/tests/integration/test_db_roles.py` (nama role tidak valid ditolak); fungsi hanya dipanggil CLI `grant-runtime-role` oleh migrate, bukan dari request | 2027-01-31 |
| EX-06 | OS image sudah end of life: Alpine 3.21 (dukungan berakhir 1 Nov 2026 [Kemungkinan Besar]) | `redis:7.4.11-alpine` (`infra/security/scan-exceptions.toml`, `eosl = true`) | Diusulkan Lead (sesi WP-1.5d, 31 Okt 2026); **menunggu persetujuan PO** | Image redis 7.4 terbaru (7.4.12, 26 Okt) masih Alpine 3.21. Jalan keluarnya adalah Redis 8 (lisensi AGPL/SSPL/RSAL) atau Valkey (BSD), dan pilihan ini keputusan PO (konflik #58) | Redis hanya di network privat `app`, `requirepass`, tanpa port publik, non-root, `cap_drop: ALL`; data dapat dibuang (RUNBOOK §10.3). Dicek ulang bulanan | 2026-11-30 |
