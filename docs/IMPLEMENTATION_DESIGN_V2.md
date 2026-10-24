# KodeKantor Platform — Implementation Design v2

> **Status: Rancangan (Proposed) — belum diimplementasikan.** Disusun 21 Okt 2026 oleh agent *Implementation Design v2* (arsitek), untuk Lead Architect (AG-00) dan Product Owner.
> **Kontrak mengikat:** `docs/PLATFORM_EXPANSION.md` §9b **KONTRAK v2** (K1 modul & FR, K2 agent, K3 tools, K4 tech stack) dan ADR-036..049. Bila dokumen ini bertentangan dengan §9b, §9b yang berlaku; perbedaan yang ditemukan dicatat di §1.4 dan §12.
> **Hubungan dokumen:** melengkapi `ARCHITECTURE.md` (baseline MVP, tidak diubah), `API_CONTRACTS.md` §12 (endpoint v2), `DATA_MODEL.md` "Ekstensi v2" (tabel), `CONFIGURATION.md` (variabel v2 tambahan). Semua berlaku **setelah gate G3** (ADR-036).
> **Label keyakinan:** **[Pasti]** dari dokumentasi resmi/kode di repo · **[Kemungkinan Besar]** inferensi kuat, wajib dibuktikan oleh contract test sebelum dipakai · **[Menebak]** estimasi. Bagian bertanda *(turunan)* adalah keputusan desain dokumen ini yang tidak tertulis eksplisit di kontrak.
> **v2.1 (22 Okt 2026):** desain implementasi SDLC end-to-end, Developer Workspace v2, dan tier M/L ada di **§13** (kontrak mengikat: `docs/SDLC_SCALE_DESIGN.md` §6 KONTRAK v2.1). §1–§12 tidak diubah.
> **v2.2 (22 Okt 2026):** desain implementasi polyglot language packs, lingkungan integrasi multi-service & kontrak, dan monitoring biaya & token realtime ada di **§14** (kontrak mengikat: `docs/POLYGLOT_INTEGRATION_DESIGN.md` §7 KONTRAK v2.2). §1–§13 tidak diubah.

---

## 1. Ringkasan & prinsip

### 1.1 Ringkasan

1. KodeKantor v2 tetap **satu modular monolith** (FastAPI + Celery + PostgreSQL + Redis) dengan tiga ruang kerja di satu SPA: **Portal IDE**, **Portal AI**, **Portal Portfolio**, plus integrasi **GitHub App** (ADR-036). Tidak ada microservice, vector DB, message bus, atau Kubernetes baru (ADR-049).
2. Empat **pool worker Celery** per antrean — `ide`, `assistant`, `media`, `integrations` — memisahkan beban **dan rahasia**: kunci Claude tidak pernah ada di worker GitHub; private key GitHub App tidak pernah ada di worker AI *(turunan, §3.2)*.
3. Semua kemampuan AI teks, riset web, dokumen, dan tanya jawab dokumen memakai **Claude Platform API**: Messages API streaming, server tools `web_search`/`web_fetch`, code execution + Agent Skills, Files API, citations, blok `search_result` (ADR-037/038/039/046). Satu-satunya vendor kedua adalah **ImageProvider** (ADR-040, vendor via ADR-040a).
4. Setiap kemampuan adalah **capability** terdaftar (ADR-044): diaktifkan per flag, dibatasi izin RBAC, kuota/anggaran per user, dan **label sensitivitas data** (`public|internal|confidential`) yang menentukan engine/provider (ADR-045, §9.5).
5. Tiga perubahan kontrak inti dibutuhkan agar Portal AI/Portfolio memakai pintu yang sama dengan IDE: **RunScope** (agent tanpa task), **ToolGateway server-tool path** (tool yang dieksekusi provider tetap lewat allowlist/kuota/audit), dan **ApprovalGrant** (satu approval R3 → beberapa langkah terikat). Ketiganya diusulkan sebagai ADR baru (§12.3) dan dikunci di **WP-9.0** sebelum W7.

### 1.2 Prinsip v2 (melanjutkan ARCHITECTURE §0 P1–P8)

| # | Prinsip | Konsekuensi teknis |
|---|---|---|
| P9 | **Claude-only untuk teks & pengetahuan (ADR-046)** | Tidak ada model embedding/vector DB. KB = PostgreSQL FTS (`tsvector` `simple`) + `pg_trgm` + ringkasan kontekstual buatan Claude; jawaban dikirim sebagai blok `search_result` bersitasi. Bila retrieval gagal ambang WP-11.3 → ADR baru `EmbeddingProvider` (bukan diam-diam menambah vendor). |
| P10 | **Jawaban bersitasi = teks, bukan `submit_result` (ADR-047)** | Registry memakai field yang sudah ada `output_contract`: nilai `CitedAnswer` (AGENT_CATALOG v2, validator V13) ⇒ runtime mengakhiri run dengan teks bersitasi, selain itu `submit_result`. I42 (jawaban), I43, I47, K50 (saat mengutip lowongan) berakhir dengan teks bersitasi; backend (`CitationExtractor`) mengekstrak `citations` ke `message_citations`. `submit_result` hanya untuk metadata non-teks (routing I42) dan agent tanpa sitasi. |
| P11 | **Capability-gated** | Tidak ada panggilan provider tanpa `CapabilityRegistry.check()` yang lulus (flag aktif, izin, kuota, sensitivitas, kill switch) — AX-11. Router gagal tertutup (`CAPABILITY_DISABLED`/`QUOTA_EXCEEDED`), tanpa panggilan HTTP keluar. |
| P12 | **Provider-executed tools tetap lewat ToolGateway** | `web.search`, `web.fetch`, `code.execute_remote`, `docs.render_skills` diotorisasi sebelum request (allowlist profil + permission `network: provider_web` + sensitivitas + kuota → `max_uses`) dan dicatat sesudahnya sebagai `tool_calls.executor = provider` + `capability_usage` + audit (K3). |
| P13 | **Sensitivitas menentukan rute** | `confidential` → engine lokal, tanpa Skills/Files API/code execution remote/web/gambar (ADR-039, ADR-046, T15). Diuji AX-06. |
| P14 | **Segregasi rahasia per worker** | Setiap secret hanya di container yang memakainya (tabel §3.2). Private key GitHub App hanya di `worker-integrations` (lihat konflik §1.4 #3). |
| P15 | **Konten eksternal = data tidak tepercaya** | Halaman web, dokumen unggahan, repo GitHub, lowongan kerja, prompt asisten kustom: tidak dapat memperluas tool, mode, izin, atau membuat approval (SECURITY_MODEL §9, T11). Mode riset dan mode KB eksklusif secara default (§9.8). |
| P16 | **Aksi keluar = R3 dengan approval payload-bound** | Push branch/PR (`github_push_pr`) dan publikasi portfolio (`portfolio_publish`) hanya setelah keputusan manusia via `POST /approvals/{id}/decision`; eksekusi memakai `ApprovalGrant` yang hanya dapat dibuat kode server setelah `consume()` atomik. |
| P17 | **File tak tepercaya diparse di sandbox** | Validasi/konversi/ekstraksi PPTX/DOCX/XLSX/PDF, analisis data, dan build situs portfolio berjalan di image `kodekantor/sandbox-docs` (network none). Worker hanya memindahkan byte dan memeriksa magic bytes (CLAUDE.md #3 diperluas ke file pengguna). |
| P18 | **Single-node first, tetap** | Pilot 4 vCPU/16 GB (ADR-010). Mode *compact* (satu worker untuk semua antrean) hanya untuk dev; production memakai mode *split* demi segregasi rahasia (§3.2). |

### 1.3 Lingkup & non-lingkup

**Lingkup:** M21 Portal AI (FR-160..184), M22 GitHub App (FR-185..196), M23 Portfolio (FR-197..212), M24 Capability Registry & Provider Gateway (FR-213..220); UC-11..UC-20; AX-01..AX-11; agent I42–I47, J48, K49–K51, S52 + promosi E29 (K2); tools K3; stack K4.
**Non-lingkup:** `voice` (P2), memori personal (P2), analitik kunjungan portfolio (P2), multi-tenant publik (butuh microVM, ARCH §13), self-hosted image model (butuh GPU, ADR-040), merge PR dari KodeKantor (R4), GitHub Enterprise Server (dapat ditambah lewat `GITHUB_API_BASE_URL`, belum diuji).

### 1.4 Konflik dengan kontrak/snapshot yang ditemukan & resolusinya

| # | Temuan | Sumber | Resolusi di desain ini |
|---|---|---|---|
| 1 | Tabel capability `kb_qa` masih "Claude + pgvector (ADR-043)"; WBS WP-11.3 berjudul "(pgvector)" dan roster AG-19 "KB pgvector" | PLATFORM_EXPANSION §2, `docs/wbs/wbs_data.py` | **Ikuti ADR-046 + K4** (mengikat): tanpa embedding. Lead perlu memperbarui §2 dan `wbs_data.py` (di luar path milik agent ini). |
| 2 | Nama tool di tabel §2 (`docs.generate`, `github.import`, `github.pull`) berbeda dari K3 (`docs.render_skills`/`docs.render_local`, `github.import_repo`, `github.pull`) | PLATFORM_EXPANSION §2 vs §9b K3 | **K3 berlaku.** |
| 3 | Private key GitHub App "di-mount ke `worker` dan `api`"; `GITHUB_APP_PRIVATE_KEY_PATH` dipakai "api, worker" | PLATFORM_EXPANSION §5.4 #1, CONFIGURATION | `api` **tidak** berada di network `outbound` (ARCH §2, mengikat) sehingga tidak dapat memanggil GitHub. Desain: semua panggilan GitHub (verifikasi instalasi, token, clone/push/PR) di `worker-integrations`; `api` hanya memegang `GITHUB_WEBHOOK_SECRET` dan `GITHUB_APP_SLUG`. Pem **hanya** di `worker-integrations` (least privilege). Diusulkan sebagai ADR (§12.3 U-04); bila Lead mempertahankan mount di `api`, desain lain tidak berubah. |
| 4 | DATA_MODEL mencantumkan kolom ADR-024 (`approvals.handler_*`), ADR-031 (`tasks.pause_requested`), ADR-032 (`provider_configs.readiness_*`), ADR-033/035 (`test_runs.read_only`, `failure_kind`) tetapi migrasi `0001_initial.py` belum memuatnya; CHECK `cost_status`/`agent_runs.status` belum memuat nilai ADR-025 | `backend/alembic/versions/0001_initial.py` (diperiksa) | Migrasi **0002** menambah semuanya (bukan hanya ADR-021/025/031/035). ADR-021 tidak butuh DDL (transisi di kode). §7.1. |
| 5 | `approvals.project_id NOT NULL` — `portfolio_publish` tidak punya project | 0001 | 0003: `project_id` nullable + `scope_type` + `owner_id` + CHECK (§7.2). |
| 6 | `agent_runs.task_id`/`tool_calls.task_id NOT NULL`; `AgentRuntime.run(task=…)`, `ToolContext(task_id, project_id, workspace)` dan `emit_task_event` task-sentris | 0001, `agents/runtime.py`, `tools/base.py`, `tools/gateway.py` | **RunScope** (`TaskScope`/`ThreadScope`) + kolom `thread_id` + CHECK `num_nonnulls(task_id, thread_id) = 1` (§5.0, §7.2). Usulan ADR U-01. |
| 7 | `SandboxRunRequest.project_id` wajib; sandbox-manager hanya menerima `workspace_subpath` `<project>/tasks/<task>` atau `<project>/repo` | `sandbox/client.py`, `sandbox_manager/…/validation.py` | Workspace **job** `_jobs/<uuid>` di bawah `WORKSPACE_ROOT` (regex ketiga) untuk pekerjaan Portal AI/Portfolio + image `sandbox-docs` (§3.5). Usulan ADR U-05. |
| 8 | Penomoran migrasi brief (0003 assistant, 0004 github, 0005 portfolio, 0006 kb) tidak sama dengan urutan wave (W7 GitHub sebelum W8 assistant; W9 KB sebelum W10 portfolio) | brief vs WBS | Nomor dipertahankan; rantai Alembic linear 0002→…→0006. **0003** di-land *schema-first* di WP-9.0 (W7, fitur di belakang flag); **0005** di-land oleh WP-12.1 yang menurut WBS hanya bergantung pada WP-G10 sehingga dapat berjalan paralel W9 sebelum 0006; bila 0006 siap lebih dulu, Lead me-rebase `down_revision` (§7.0). |
| 9 | `git.push` (P2, disabled) dan `approvals.action_type = git_push` dari ADR-003 vs jalur GitHub App ADR-041 | TOOL_CATALOG, API §11.1 | Action type baru **`github_push_pr`**; `git.push`/`git_push` tetap dicadangkan dan nonaktif (remote generik non-GitHub). |
| 10 | Satu approval `github_push_pr` vs dua tool R3 (`github.push_branch`, `github.open_pr`); gateway saat ini mengikat approval ke `{tool, input}` satu tool | K3, `tools/gateway.py::_approval_gate` | **ApprovalGrant** (§5.15.3): approval di-`consume()` sekali oleh job server, lalu grant dalam memori mengizinkan langkah-langkah yang field-nya terikat payload. Usulan ADR U-03. |
| 11 | ADR-013 (`submit_result` wajib) vs ADR-047 | DECISIONS | `output_contract: CitedAnswer` di registry (selaras AGENT_CATALOG v2 V13) menentukan mode akhir runtime (§5.16). |
| 12 | Test `test_config::test_every_documented_variable_is_a_setting` sudah gagal karena variabel perluasan belum ada di `Settings`; variabel v2 yang ditambahkan ke CONFIGURATION memperpanjang daftar itu | `backend/tests/unit/test_config.py` | WP-10.1 menambah field `Settings` untuk variabel v2 (default aman, nonaktif) **atau** WP-INT-01 membatasi test pada bagian non-Proposed. Dicatat di §11. |
| 13 | CSP Caddy global `frame-ancestors 'none'` vs pratinjau situs portfolio di iframe | `infra/caddy/Caddyfile` | Override header khusus route pratinjau (`frame-ancestors 'self'` + `sandbox`), §8.2. |
| 14 | *(temuan samping, di luar scope)* compose `docker-socket-proxy` memakai `INFO: 1`, ADR-026 menetapkan `INFO=0` | `infra/compose.yml` | Dilaporkan ke Lead/AG-06; tidak diubah di sini. |
| 15 | WBS tidak punya WP untuk penguncian kontrak v2 sebelum W7 | `wbs_data.py` | Usulan **WP-9.0** (AG-00) di §11. |

> **Tindak lanjut Lead (21 Okt 2026):** #1 selesai (PE §2 + WBS diselaraskan ke ADR-046); #2 selesai (PE §2 memakai nama K3); #3 selesai (ADR-053; PE §5.4 #1, §5.3 dan CONFIGURATION diperbarui); #5–#7, #9–#10 dikunci sebagai ADR-050..056 (*Proposed*); #14 **sengaja belum diubah** karena PO meminta tanpa implementasi — dicatat di tracker sebagai temuan untuk WP keamanan; #15 selesai (WP-9.0 dan WP-0.13 masuk WBS).

---

## 2. Tech stack v2

Memperluas K4 (mengikat). Kolom *Status*: **snapshot** = sudah ada di kode snapshot 21 Okt 2026 · **rencana** = belum ada.

### 2.1 Tabel stack per layer

| Layer | Teknologi | Kebijakan versi (pin) | Alasan | Status |
|---|---|---|---|---|
| Frontend inti | React 19, TypeScript strict, Vite, React Router 7, TanStack Query 5, Tailwind CSS 4 + Radix (gaya shadcn/ui), Lucide, Monaco lokal, xterm.js, React Hook Form + Zod, Zustand selektif, i18next | **Exact pin** di `package.json` (tanpa `^`) + `package-lock.json` — praktik snapshot [Pasti] | Sudah dipakai; satu SPA tiga ruang kerja (ADR-036) | snapshot |
| Frontend — render jawaban | `react-markdown` + `remark-gfm` + `rehype-sanitize` (schema allowlist; tanpa HTML mentah) | Exact pin; versi dipilih saat WP-10.5 mulai, rilis berumur ≥ 2 minggu; `npm audit` bersih | Markdown jawaban model = untrusted (SECURITY_MODEL §2.1 XSS) | rencana |
| Frontend — kode di chat | Shiki (bahasa dimuat *lazy*) dirender via `codeToHast` → `hast-util-to-jsx-runtime` (tanpa `dangerouslySetInnerHTML`) [Kemungkinan Besar API Shiki] | Exact pin; tema & grammar dibundel lokal (CSP tanpa CDN) | Highlight konsisten tanpa membuka jalur HTML mentah | rencana |
| Frontend — pratinjau PDF | `pdfjs-dist` dengan worker lokal, `isEvalSupported: false`, scripting PDF dimatikan | Exact pin; worker di `public/` | Pratinjau dokumen hasil & CV | rencana |
| Frontend — grafik | SVG inline (sudah); `recharts` hanya bila grafik usage/analisis butuh interaksi | Exact pin bila dipakai | Hindari dependensi bila tidak perlu | snapshot / rencana |
| Backend inti | Python 3.12 (CI juga 3.13), FastAPI, Pydantic v2, SQLAlchemy 2 async + asyncpg, Alembic, Celery 5 + Redis, structlog, httpx, prometheus-client, PyYAML | Batas bawah di `pyproject.toml`, **pin penuh di `uv.lock`**; upgrade lewat PR + CI | ADR-001 | snapshot |
| Backend — antrean v2 | Celery `task_routes` ke `ide`, `assistant`, `media`, `integrations`; 4 container worker | Konfigurasi kode | ADR-036; isolasi beban & rahasia | rencana |
| AI — teks/agent | SDK resmi `anthropic` (hanya di `app/llm/anthropic_provider.py`): Messages streaming, tool use, prompt caching | `uv.lock`; model ID dari `CLAUDE_MODEL_*` (ADR-004) | Sudah | snapshot |
| AI — fitur v2 | Server tools `web_search` (`WEB_SEARCH_TOOL_VERSION`), `web_fetch` (`WEB_FETCH_TOOL_VERSION`), code execution (`CODE_EXECUTION_TOOL_VERSION`) + Agent Skills `pptx/docx/xlsx/pdf` (`DOC_SKILLS_VERSION`), Files API, citations (`web_search_result_location`, `search_result_location`, `char_location`, `page_location`), blok `search_result` dan `document` | **Versi tool & beta header dipin di konfigurasi**, naik hanya lewat ADR + contract test (ADR-038). Beta header diturunkan dari versi tool di provider, tidak di domain code | ADR-037/038/039/046/047 | rencana |
| Gambar | `ImageProvider` adapter; implementasi vendor via **httpx REST** (tanpa SDK vendor bila memungkinkan) di `app/llm/image_<vendor>.py` | Endpoint & model dipin di `config/providers.yaml`; harga di `pricing.yaml` (`verify_before_production`) | ADR-040; satu modul per vendor seperti aturan P6 | rencana |
| Pengetahuan (KB) | PostgreSQL FTS (`tsvector` konfigurasi `simple`, kolom *generated*), `pg_trgm` (GIN), ringkasan kontekstual + kata kunci oleh Claude tier `fast` | Ekstensi PG bawaan image `postgres:16.x-alpine` (contrib) [Kemungkinan Besar] | ADR-046; tanpa vendor tambahan | rencana |
| Dokumen lokal | Image sandbox **`kodekantor/sandbox-docs:1`**: Python 3.12, python-pptx, python-docx, openpyxl, pdfplumber, pypdf, pandas, matplotlib, Pillow, Jinja2, LibreOffice headless (konversi PDF/thumbnail), poppler-utils (`pdftoppm`), font sans-serif (Inter, Noto Sans, Liberation Sans) | Base image Debian stable dipin **digest**; paket Python di `requirements.lock` (hash); paket apt dicatat di SBOM; rebuild bulanan + Trivy | ADR-039, P17; template default sans-serif (PLATFORM_EXPANSION §4.1) | rencana |
| GitHub | GitHub App (REST API `X-GitHub-Api-Version` dipin, webhooks), `PyJWT[crypto]` (RS256) — hanya diimpor `app/integrations/github/app_auth.py`, httpx, git CLI ≥ 2.31 (untuk `GIT_CONFIG_COUNT`) [Kemungkinan Besar] | `uv.lock`; versi API di `GITHUB_API_VERSION` | ADR-041 | rencana |
| Portfolio | Jinja2 (autoescape) **di dalam sandbox-docs** untuk situs statis; Caddy `file_server` di `PORTFOLIO_DOMAIN`; opsi GitHub Pages (branch `gh-pages`) | Template berversi di repo (`backend/app/portfolio/templates/`, dibaca sandbox sebagai input) | ADR-042; situs tanpa JavaScript | rencana |
| Data | PostgreSQL 16, Redis 7, asset store filesystem `ASSETS_ROOT` (bind, dibackup); S3-compatible = opsi kemudian lewat `AssetStore` | Tag image dipin (snapshot: `postgres:16.15-alpine`, `redis:7.4.11-alpine`) | Sederhana, satu VPS | snapshot / rencana |
| Eksekusi | Docker sandbox profil tetap, sandbox-manager + docker-socket-proxy, gVisor opsional, egress proxy (dependency, P1) | Image sandbox dipin; tidak ada auto-pull (ADR-026) | ADR-007/009/026/033 | snapshot |
| Infra | Docker Compose, Caddy 2 (TLS, header keamanan, SSE), Ubuntu Server LTS | Tag image dipin | ADR-019 | snapshot |
| Kualitas | pytest, pytest-asyncio, **respx** (mock Claude/GitHub/vendor), Ruff, Pyright, import-linter, Vitest + RTL, Playwright, gitleaks, Semgrep, Trivy; **baru**: fixture webhook GitHub, contract test provider (LLM server tools, ImageProvider, DocumentEngine), eval harness sitasi & retrieval | `uv.lock`/`package-lock.json` | TEST_STRATEGY + §10 | snapshot + rencana |
| Observabilitas | structlog JSON + redaksi, `audit_events`, Prometheus `/metrics`, OTel opsional; metrik baru per capability/antrean | – | M19 | snapshot + rencana |

### 2.2 Yang secara eksplisit TIDAK dipakai

| Tidak dipakai | Alasan | Kapan dipertimbangkan ulang |
|---|---|---|
| Vector DB / pgvector / embedding provider | ADR-046 (Anthropic tidak menyediakan embedding; PO meminta semua AI lewat Claude) | Metrik WP-11.3 di bawah ambang → ADR `EmbeddingProvider` |
| Kubernetes / orchestrator cluster | Single VPS (ADR-010, ADR-036) | Scale-out ARCH §13 terbukti tidak cukup |
| Message bus (Kafka, RabbitMQ, NATS) | Redis + Celery + PostgreSQL event table cukup (ADR-014) | Throughput event > kemampuan Redis pub/sub |
| WebSocket | SSE + `Last-Event-ID` cukup; tidak ada input interaktif dua arah (ADR-014) | Voice (P2) |
| Microservice baru | Modular monolith (ADR-036); sandbox-manager tetap satu-satunya service terpisah | – |
| Framework agent pihak ketiga (LangChain, LlamaIndex, dll.) *(turunan)* | Runtime sendiri sudah ada (ADR-012/013); menambah permukaan supply-chain & menyembunyikan tool loop dari ToolGateway | – |
| Elasticsearch/OpenSearch | FTS PostgreSQL cukup untuk volume pilot | Volume KB > ±5 juta chunk [Menebak] |
| Model gambar self-hosted (Flux/SDXL) | Butuh GPU (ADR-040) | Node GPU tersedia |
| Headless browser untuk render dokumen/situs | LibreOffice + Jinja2 cukup; Playwright hanya untuk test E2E | – |
| CDN pihak ketiga (Monaco, pdf.js, font) | CSP `default-src 'self'` (ADR-001) | – |
| Cookie/pelacak pihak ketiga di situs portfolio | Privasi (PLATFORM_EXPANSION §6.1) | Analitik P2 tanpa cookie |
| S3/object storage | Filesystem + backup cukup untuk pilot | Multi-node (ARCH §13) |

---

## 3. Arsitektur komponen v2

### 3.1 Container diagram

```mermaid
flowchart TB
  subgraph inet["Internet"]
    U["Browser pengguna<br/>(SPA: IDE · AI · Portfolio)"]
    PV["Pengunjung situs portfolio"]
    GHW["GitHub (pengirim webhook)"]
    AN[("api.anthropic.com<br/>Messages, server tools,<br/>code execution + Skills, Files")]
    GHA[("api.github.com / github.com<br/>REST + git HTTPS")]
    IV[("Image vendor API<br/>ADR-040a")]
  end
  subgraph pub["network public"]
    caddy["caddy :443<br/>site APP_DOMAIN + site PORTFOLIO_DOMAIN"]
  end
  subgraph edge["network edge (internal)"]
    web["web<br/>static SPA"]
    api["api (FastAPI)<br/>REST, SSE task+thread,<br/>webhook ingress, asset download"]
  end
  subgraph appnet["network app (internal)"]
    wide["worker-ide<br/>Q ide: task, test, index, E29"]
    wasst["worker-assistant<br/>Q assistant: I42 I43 I47 K49 K50"]
    wmedia["worker-media<br/>Q media: I44 I45 I46, KB ingest,<br/>render CV, build situs"]
    wint["worker-integrations<br/>Q integrations: J48 GitHub,<br/>webhook proc, K51 publish"]
    beat["scheduler (beat)"]
    pg[("postgres 16<br/>+ pg_trgm, FTS")]
    rd[("redis 7<br/>broker, pub/sub, kuota")]
    sm["sandbox-manager"]
  end
  subgraph execnet["network exec (internal)"]
    dsp["docker-socket-proxy"]
  end
  subgraph sbx["container sandbox (network none)"]
    spy[["sandbox-python / sandbox-node<br/>task runs"]]
    sdoc[["sandbox-docs<br/>render, inspect, extract,<br/>analysis, build situs"]]
  end
  vws[("WORKSPACE_ROOT<br/>projects + _jobs")]
  vas[("ASSETS_ROOT")]
  vpf[("PORTFOLIO_SITE_ROOT")]

  U --> caddy
  PV --> caddy
  GHW -->|"POST /api/v1/integrations/github/webhook"| caddy
  caddy --> web
  caddy --> api
  caddy -->|"file_server read-only"| vpf
  api --> pg
  api --> rd
  api -->|"kill / status"| sm
  wide & wasst & wmedia & wint --> pg
  wide & wasst & wmedia & wint --> rd
  beat --> rd
  wide -->|"Bearer"| sm
  wmedia -->|"Bearer"| sm
  sm --> dsp --> spy & sdoc
  wide -->|"HTTPS"| AN
  wasst -->|"HTTPS"| AN
  wmedia -->|"HTTPS"| AN
  wmedia -->|"HTTPS"| IV
  wint -->|"HTTPS (JWT, token instalasi)"| GHA
  api --- vas
  wmedia --- vas
  wint --- vas
  wide --- vws
  wmedia --- vws
  wint --- vws
  api --- vws
  sm --- vws
  wint --- vpf
```

Catatan: worker-assistant **tidak** memegang `SANDBOX_MANAGER_TOKEN` (tidak menjalankan sandbox); pekerjaan yang butuh sandbox diserahkan ke antrean `media` *(turunan)*. `api` tidak memanggil vendor eksternal apa pun (tetap tanpa network `outbound`, ARCH §2).

### 3.2 Antrean, worker, dan segregasi rahasia

| Container | Antrean | Job (modul `app/worker/…`) | Secret yang dipegang | Mount | Concurrency awal [Menebak] | Memori limit [Menebak] |
|---|---|---|---|---|---|---|
| `worker-ide` | `ide` (default) | `jobs.*` (run_task, recovery, retention), `platform_jobs.*` (index, import arsip/git_https), `ide_jobs.draft_pr` (E29) | `ANTHROPIC_API_KEY`, `SANDBOX_MANAGER_TOKEN` | workspaces rw | 2 (ADR-010) | 2 GB |
| `worker-assistant` | `assistant` | `assistant_jobs.respond`, `.regenerate`, `.portfolio_curate` (K49), `.job_fit` (K50) | `ANTHROPIC_API_KEY` | – (tidak butuh file) | 3 (I/O-bound streaming) | 1 GB |
| `worker-media` | `media` | `media_jobs.render_document`, `.generate_image`, `.edit_image`, `.ingest_kb_document`, `.run_analysis`, `.render_cv`, `.build_portfolio_site`, `.provider_files_cleanup` | `ANTHROPIC_API_KEY`, `IMAGE_PROVIDER_API_KEY`, `SANDBOX_MANAGER_TOKEN` | workspaces rw (`_jobs`), assets rw | 1–2 (LibreOffice berat) | 2 GB |
| `worker-integrations` | `integrations` | `integration_jobs.verify_installation`, `.sync_installation`, `.handle_webhook`, `.import_repo`, `.pull_repo`, `.push_pr`, `.refresh_pr_status`, `.publish_portfolio`, `.snapshot_repos_for_portfolio` | GitHub App private key (secret file), `GITHUB_CLIENT_SECRET` | workspaces rw (clone/push), assets ro (bundle situs), portfolio site rw | 1 | 768 MB |
| `scheduler` | – | beat: sweeps lama + sweep v2 (§8.3) | – (placeholder key seperti snapshot) | – | – | 256 MB |
| `api` | – (produsen) | – | `SECRET_KEY`, `GITHUB_WEBHOOK_SECRET` (+`_PREVIOUS`), `SANDBOX_MANAGER_TOKEN`; `ANTHROPIC_API_KEY` hanya untuk smoke (ADR-004) | workspaces rw, assets rw | – | 1.5 GB |

- **Mode compact** (dev/CI): satu container `worker` tanpa `KK_PROCESS_ROLE`, `WORKER_QUEUES=ide,assistant,media,integrations`, dan semua secret — **dilarang** di `APP_ENV=production` (validator config menolak production bila `KK_PROCESS_ROLE` kosong atau bila satu role mengonsumsi antrean milik role lain) *(turunan)*.
- Anggaran RAM pilot: total limit container v2 ≈ 2+1+2+0.75 (worker) + 1.5 (api) + 2 (pg) + 0.5 (redis) + 0.5 (sm) + sandbox runs (2×1 GB) ≈ 12.25 GB (≈ 12.5 GB termasuk scheduler 256 MB) dari 16 GB → sisa ±3 GB untuk OS/page cache [Menebak]. Bila RAM > 70% selama 1 minggu → pisahkan worker-media + sandbox ke node executor (ADR-010, ARCH §13).
- Prioritas: antrean `assistant` latency-sensitive (chat); pekerjaan panjang (dokumen Skills bisa beberapa menit, LibreOffice, KB ingest) selalu di `media` sehingga tidak menahan chat atau task IDE.

### 3.3 Egress (keluar dari VPS)

| Dari | Tujuan | Protokol / auth | Data yang keluar | Kontrol |
|---|---|---|---|---|
| worker-ide | `api.anthropic.com` | HTTPS, `x-api-key` | snippet kode relevan (ADR-011), diff ringkas untuk E29 | redaksi, kill switch project |
| worker-assistant | `api.anthropic.com` | HTTPS | pesan user, riwayat thread, potongan KB (`search_result`), dokumen kecil inline (`document`) | capability check, sensitivitas, redaksi secret |
| worker-assistant (via Anthropic) | web publik | dieksekusi **di infrastruktur Anthropic** (server tool) | query pencarian buatan model, URL yang di-fetch | `allowed_domains`/`blocked_domains`, `max_uses`, nonaktif untuk `confidential` & mode KB (§9.7) |
| worker-media | `api.anthropic.com` | HTTPS + beta Files/Skills/code execution | outline/konten dokumen, data analisis non-confidential | hanya bila `sensitivity ≤ internal` (ADR-039) |
| worker-media | image vendor | HTTPS, API key vendor | prompt gambar (sudah dimoderasi), gambar referensi milik user | kuota, moderasi, `sensitivity ≤ internal` |
| worker-integrations | `api.github.com`, `github.com` | HTTPS; JWT RS256 → token instalasi ≤ 1 jam | git objects (push branch `kk/task-*`), judul/isi PR, file situs (GitHub Pages) | approval R3, secret scan, path policy |
| caddy | ACME (Let's Encrypt/ZeroSSL) | HTTPS | – | snapshot |

**Penguatan opsional (P1, *turunan*):** saat ini network `outbound` adalah bridge tanpa filter. Usulan: `HTTPS_PROXY` per pool worker ke egress proxy dengan allowlist per pool (ide/assistant → `api.anthropic.com`; media → + host vendor gambar; integrations → `api.github.com`, `github.com`, `codeload.github.com`). Membatasi dampak worker yang terkompromi. Keputusan terbuka D-07.

### 3.4 Keanggotaan network (mengikat untuk compose v2)

| Service | public | edge | app | exec | outbound | kk-egress | egress-out |
|---|---|---|---|---|---|---|---|
| caddy | ✓ | ✓ | | | | | |
| web | | ✓ | | | | | |
| api | | ✓ | ✓ | | | | |
| worker-ide | | | ✓ | | ✓ | | |
| worker-assistant | | | ✓ | | ✓ | | |
| worker-media | | | ✓ | | ✓ | | |
| worker-integrations | | | ✓ | | ✓ | | |
| scheduler, migrate, postgres, redis | | | ✓ | | | | |
| sandbox-manager | | | ✓ | ✓ | | | |
| docker-socket-proxy | | | | ✓ | | | |
| egress-proxy (P1) | | | | | | ✓ | ✓ |
| container sandbox-docs | — `network_mode=none` (tidak ada profil egress untuk docs) | | | | | | |

Aturan ARCH §2 tetap: hanya sandbox-manager dan docker-socket-proxy di `exec`; tidak ada worker baru yang boleh bergabung ke `exec`.

### 3.5 Workspace job & image `sandbox-docs`

**Workspace job** *(turunan, usulan ADR U-05)*: pekerjaan Portal AI/Portfolio tidak punya project. Worker-media membuat direktori `WORKSPACE_ROOT/_jobs/<job_uuid>/` (uid 10001) berisi `in/` (input yang disalin worker: spec JSON, file unggahan, template), `out/` (keluaran), `.kk/` (artefak run); dihapus setelah keluaran dipindah ke asset store (sweep yatim setiap jam, umur > 2 jam).

- `SandboxRunRequest`: `project_id: UUID | None`, `job_id: UUID | None` (tepat satu dari `task_id`/`job_id`/`workspace_subpath=…/repo`), `workspace_subpath = "_jobs/<job_uuid>"`.
- sandbox-manager: regex ketiga `^_jobs/[0-9a-f-]{36}$` + `realpath` di bawah `WORKSPACE_ROOT/_jobs`; label container `kk.job_id`; profil `docs.*`/`kb.*`/`analysis.*`/`portfolio.*` **hanya** menerima subpath job (profil menyatakan `workspace_kinds: [job]`), profil IDE hanya `task|repo`.
- `/workspace/in` di-mount **read-only** (bind kedua, path tervalidasi), `/workspace/out` rw dengan batas pertumbuhan `SANDBOX_MAX_WORKSPACE_GROWTH_MB`; network selalu `none`.

**Profil sandbox v2** (ditambahkan ke `sandbox-images/sandbox-profiles.yaml`, image key `docs` → `SANDBOX_IMAGE_DOCS`):

| profile_id | argv (exec-form, tanpa shell) | timeout default/max (s) | Keluaran | Dipakai oleh |
|---|---|---|---|---|
| `docs.render_pptx` / `docs.render_docx` / `docs.render_xlsx` | `python -m kkdocs.render --kind <pptx/docx/xlsx> --spec /workspace/in/spec.json --out /workspace/out` | 120/300 | file OOXML | `docs.render_local`, CV |
| `docs.convert_pdf` | `python -m kkdocs.convert_pdf --in /workspace/in/source --out /workspace/out` (LibreOffice `--headless --convert-to pdf`, `HOME=/home/sandbox`) | 120/300 | PDF | dokumen PDF lokal, CV PDF |
| `docs.inspect` | `python -m kkdocs.inspect --in /workspace/in/file --report /workspace/.kk/inspect.json` | 60/120 | laporan validasi (format, jumlah slide/halaman/sheet, makro?, rasio zip) | AX-05 |
| `docs.thumbnail` | `python -m kkdocs.thumbnail --in /workspace/in/file --out /workspace/out/thumb.png --max-px 640` | 90/180 | PNG | pratinjau |
| `kb.extract` | `python -m kkdocs.extract --in /workspace/in/file --out /workspace/out/blocks.jsonl` | 180/600 | blok teks + nomor halaman | KB ingest |
| `analysis.python` | `python -I /workspace/in/analysis.py` (kode buatan I46, ditulis worker sebagai file, tidak lewat argv) | 120/300 | CSV/PNG di `out/` + `stdout` | `analysis.run` |
| `portfolio.build_site` | `python -m kkdocs.site --snapshot /workspace/in/snapshot.json --templates /workspace/in/templates --out /workspace/out/site` | 60/180 | HTML/CSS/gambar statis | `portfolio.build_site` |

Paket `kkdocs` adalah kode KodeKantor yang dibundel di image (bukan kode pengguna), di `sandbox-images/docs/kkdocs/` (milik AG-03). Batas: `SANDBOX_MEMORY_LIMIT` 1 GB cukup untuk LibreOffice pada dokumen pilot [Menebak]; profil docs boleh meminta `memory_class: large` (1.5 GB) bila Lead menyetujui.

---

## 4. Struktur paket v2

### 4.1 Backend

```
backend/
├── config/
│   ├── pricing.yaml                 # + anthropic.server_tools, code_execution, image vendors (verify_before_production)
│   ├── providers.yaml               # BARU — routing per capability (ADR-037), versi tool, model gambar
│   └── capabilities.yaml            # BARU — registry modul (ADR-044)
├── alembic/versions/
│   ├── 0001_initial.py              # snapshot
│   ├── 0002_ide_backlog.py          # ADR-021..035 (DDL yang hilang)        — WP-INT-02 (AG-01)
│   ├── 0003_platform_assistant.py   # core v2 + assistant + capability      — WP-9.0 (AG-00/AG-01)
│   ├── 0004_github.py               # github_*                              — WP-9.2 (AG-18)
│   ├── 0005_portfolio.py            # portfolio_*                           — WP-12.1 (AG-21)
│   └── 0006_kb.py                   # kb_* + FTS/trigram                    — WP-11.3 (AG-19)
└── app/
    ├── core/            # + rbac (permission v2), redaction (pola v2), config (Settings v2)          [AG-00]
    ├── models/          # + capability.py, asset.py, assistant.py, kb.py, github.py, portfolio.py,
    │                    #   moderation.py; perubahan agent.py/approval.py/provider.py (scope v2)   [AG-00 + delegasi eksplisit]
    ├── schemas/         # + assistant.py, assets.py, capabilities.py, kb.py, github.py, portfolio.py
    ├── api/routes/      # + assistant.py, assets.py, capabilities.py, kb.py, integrations_github.py,
    │                    #   portfolio.py, admin_capabilities.py  (route tipis)
    ├── services/        # komposisi & use-case lintas domain (satu-satunya yang merakit Ports)
    │   ├── assistant.py, assets.py, capabilities.py, kb.py, github.py, portfolio.py, moderation.py
    │   └── composition.py           # build_tool_ports(), build_assistant_ports(), register_v2_approval_handlers()
    ├── capabilities/    # BARU (M24) registry.py (CapabilityRegistry), router.py (ProviderRouter),
    │                    #   quota.py (Redis + SQL ledger), dataflow.py (tabel data-flow Settings)      [AG-07]
    ├── llm/             # + base.py v2 (server tools, citations, container, stream events)          [AG-00]
    │   ├── anthropic_provider.py    # + server tools, Skills container, Files API (ProviderFileStore)  [AG-07]
    │   ├── scripted_provider.py, scenarios/assistant_*.py   # skenario sitasi/skills/gambar palsu    [AG-07/AG-19]
    │   ├── image_base.py            # ImageProvider Protocol + DTO                                   [AG-00]
    │   ├── image_fake.py            # vendor palsu deterministik (test/E2E)                           [AG-19]
    │   └── image_<vendor>.py        # SATU modul per vendor ADR-040a (satu-satunya import SDK/REST vendor) [AG-19]
    ├── assets/          # BARU store.py (AssetStore FS), signing.py (HMAC URL), sniff.py (magic bytes) [AG-19]
    ├── docengine/       # BARU base.py, claude_skills.py, local_sandbox.py, specs.py (DocSpec), templates/ [AG-19]
    ├── kb/              # BARU chunker.py, ingest.py (KBIngestor), retriever.py (KBRetriever), sql.py   [AG-19]
    ├── moderation/      # BARU policy.py (leksikon id/en), classifier.py (Claude fast), service.py (S52) [AG-19]
    ├── assistant/       # BARU (M21) research.py (ResearchService), citations.py (CitationExtractor),
    │                    #   respond.py (pipeline I42→I43/I47), handoff.py (ke antrean media), scope.py (ThreadScope),
    │                    #   prompts/ (I42–I47)                                                         [AG-19]
    ├── integrations/
    │   └── github/      # BARU (M22) app_auth.py (JWT — satu-satunya import `jwt`), client.py (GitHubAppClient),
    │                    #   credentials.py (GitCredentialInjector), webhooks.py (WebhookVerifier),
    │                    #   ops.py (import/pull/push/PR), events.py (pemroses webhook)                [AG-18]
    ├── portfolio/       # BARU (M23) snapshot.py, privacy.py (PrivacyChecker), site.py (PortfolioSiteBuilder),
    │                    #   cv.py, templates/ (Jinja2 situs & CV), prompts/ (K49, K50)                  [AG-21]
    ├── agents/          # + scope.py (RunScope, TaskScope)  [AG-00]; runtime.py CitedAnswer-mode + RunScope [AG-07]
    ├── tools/
    │   ├── base.py      # v2: ToolContext/ToolSpec/PermissionProfile/ApprovalGrant/ToolPorts          [AG-00]
    │   ├── gateway.py   # + authorize_server_tools, record_server_tool_calls, grant check            [AG-07]
    │   └── builtin/     # + capabilities.py, web.py (spec provider), docs.py, assets.py, image.py, kb.py,
    │                    #   moderation.py, analysis.py, github.py, portfolio.py                       [AG-07/AG-19/AG-18/AG-21]
    ├── gitops/          # + clone/fetch/push dengan extra_env (tanpa kredensial di argv/config)       [AG-02/AG-18]
    ├── sandbox/         # client.py v2 (job_id) [AG-00]; profiles.py (+docs) [AG-03]
    ├── workflow/        # + approval_handlers untuk github_push_pr tidak di sini (domain integrations)  [AG-08]
    └── worker/          # celery_app.py (task_routes, queues, beat v2) [AG-01]; assistant_jobs.py [AG-19],
                         #   media_jobs.py [AG-19], integration_jobs.py [AG-18], ide_jobs.py (E29) [AG-08]
sandbox-images/
├── Dockerfile.docs                  # BARU                                                             [AG-03]
├── docs/kkdocs/                     # BARU render/convert/inspect/thumbnail/extract/site                 [AG-03 + AG-19/AG-21]
└── sandbox-profiles.yaml            # + profil §3.5, image key `docs`, `workspace_kinds`                  [AG-03]
sandbox_manager/src/sandbox_manager/validation.py   # + SUBPATH_JOB_RE, workspace_kinds per profil          [AG-03]
```

### 4.2 Frontend

```
frontend/src/
├── app/routes.tsx                  # + /ai/**, /portfolio/**, /settings/integrations/github, /settings/capabilities
├── components/
│   ├── markdown/Markdown.tsx       # BARU react-markdown + remark-gfm + rehype-sanitize + CodeBlock (Shiki)  [AG-04→AG-20]
│   ├── pdf/PdfPreview.tsx          # BARU pdf.js (worker lokal)                                              [AG-20]
│   ├── capability/QuotaBadge.tsx, SensitivityBadge.tsx, DataFlowNotice.tsx                                  [AG-20]
│   └── layout/…                    # Sidebar: switcher ruang kerja IDE · AI · Portfolio                     [AG-04]
├── api/
│   ├── hooks/assistant.ts, assets.ts, capabilities.ts, kb.ts, github.ts, portfolio.ts                        [pemilik fitur]
│   ├── sse/threadStream.ts         # EventSource + Last-Event-ID, union bertipe AssistantEvent               [AG-20]
│   └── types.ts                    # + tipe §12 API_CONTRACTS
├── features/
│   ├── assistant/                  # AG-20: AssistantLayout, SpaceList, ThreadList, ThreadView, Composer (mode:
│   │                               #   chat/riset cepat/riset mendalam/dokumen/gambar/KB/analisis), MessageView,
│   │                               #   CitationMarker, SourcesPanel ("dicari pada …"), UncitedLabel, ToolActivity,
│   │                               #   DocumentDialog, AssetCard, ImageStudio (estimasi biaya), Gallery,
│   │                               #   KnowledgePage (koleksi, unggah, status ingest), AnalysisResult, CustomAssistants
│   ├── portfolio/                  # AG-21: ProfileEditor, ItemsBoard, ItemEditor (label "Draft AI"), ImportGithub,
│   │                               #   ImportProject, CvBuilder, JobFitPanel, SitePreview (iframe sandbox), PublishDialog
│   ├── integrations/github/        # AG-11 (Settings) + AG-10 (task): ConnectGithub, InstallationList, RepoPicker,
│   │                               #   ImportFromGithub (CreateProjectPage), PushPrDialog, PrStatusCard
│   └── settings/capabilities/      # AG-11: CapabilitiesAdmin (flag, limit, kill switch), DataFlowTable
└── i18n/locales/{id,en}.json       # namespace baru: assistant.*, portfolio.*, github.*, capabilities.*
```

### 4.3 Aturan layering backend v2 (memperluas ARCHITECTURE §3.2)

```
api.routes   →  services, schemas, core
services     →  assistant | portfolio | integrations.github | workflow   (dan semua di bawahnya)
assistant    →  agents, tools, capabilities, docengine, kb, moderation, assets, llm, core, models
portfolio    →  agents, tools, capabilities, docengine, moderation, assets, sandbox, llm, core, models
integrations.github → gitops, assets, core, models          (tanpa llm, tanpa agents)
workflow     →  agents, tools, sandbox, llm, core, models   (tetap)
agents       →  tools, llm, capabilities, core, models
tools        →  workspace, gitops, sandbox, assets, capabilities, llm(base), core, models
               (handler tool v2 memakai fungsi domain HANYA lewat ToolPorts — ADR-022)
docengine | kb | moderation  →  sandbox, assets, capabilities, llm, core, models   (saling independen)
capabilities →  llm (Protocol), core, models
assets       →  core, models
llm          →  core          (anthropic hanya di llm/anthropic_provider.py; vendor gambar hanya di llm/image_<vendor>.py)
core, models →  (tidak boleh import layer di atasnya)
```

Prinsip: domain `assistant`, `portfolio`, `integrations.github` **saling independen**; data lintas domain (mis. ringkasan repo GitHub untuk portfolio, status PR untuk task) dirakit di `services/` dan dioper lewat Ports/DTO. Tidak ada `importlib` untuk melompati layer (ADR-022).

**Kontrak import-linter yang ditambahkan** (di `backend/pyproject.toml`, WP-9.0):

```toml
[[tool.importlinter.contracts]]
name = "Layered architecture v2 (IMPLEMENTATION_DESIGN_V2 §4.3)"
type = "layers"
exhaustive = false
exclude_type_checking_imports = true
layers = [
    "app.api",
    "app.services",
    "(app.assistant) : (app.portfolio) : (app.integrations) : app.workflow",
    "app.agents",
    "app.tools",
    "(app.docengine) : (app.kb) : (app.moderation)",
    "(app.capabilities)",
    "(app.workspace) : (app.gitops) : (app.sandbox) : (app.assets) : app.llm",
    "app.core : app.models",
]

[[tool.importlinter.contracts]]
name = "Domain v2 saling independen"
type = "independence"
modules = ["app.assistant", "app.portfolio", "app.integrations"]

[[tool.importlinter.contracts]]
name = "Tools tidak mengimpor domain/services (pakai ToolPorts)"
type = "forbidden"
source_modules = ["app.tools"]
forbidden_modules = ["app.services", "app.workflow", "app.agents", "app.assistant", "app.portfolio",
                     "app.integrations", "app.docengine", "app.kb", "app.moderation"]
exclude_type_checking_imports = true

[[tool.importlinter.contracts]]
name = "Hanya app.integrations.github.app_auth yang mengimpor PyJWT"
type = "forbidden"
source_modules = ["app"]
forbidden_modules = ["jwt"]
ignore_imports = ["app.integrations.github.app_auth -> jwt"]

[[tool.importlinter.contracts]]
name = "SDK/REST vendor gambar hanya di app.llm.image_<vendor>"
type = "forbidden"
source_modules = ["app"]
forbidden_modules = ["openai", "google.genai"]          # daftar disesuaikan ADR-040a
ignore_imports = ["app.llm.image_* -> openai", "app.llm.image_* -> google.genai"]

[[tool.importlinter.contracts]]
name = "integrations.github tidak memanggil LLM"
type = "forbidden"
source_modules = ["app.integrations"]
forbidden_modules = ["app.llm", "app.agents", "anthropic"]
```

Tambahan CI grep (ADR-022): tidak ada `importlib.import_module("app.` di `app/`; tidak ada `subprocess` di modul baru kecuali `app/gitops/` (git CLI); tidak ada `requests`/`urllib` keluar di `app/` (hanya httpx di adapter provider, GitHub client).

### 4.4 Aturan batas frontend *(turunan)*

- `features/*` hanya mengimpor `api/`, `components/`, `lib/`, `i18n/`, `stores/`; **tidak** mengimpor feature lain (ESLint `no-restricted-imports` dengan pola `@/features/*` dari dalam feature berbeda).
- Konten model/web/dokumen hanya dirender lewat `components/markdown/Markdown.tsx`; `react/no-danger` tetap error; URL sitasi dirender sebagai `<a rel="noopener noreferrer nofollow" target="_blank">` hanya untuk skema `https:`/`http:`.
- Gambar hasil hanya dari `GET /assets/{id}/preview` (raster) — tidak pernah SVG/HTML dari model.
- Server state lewat TanStack Query; stream thread lewat satu hook `useThreadStream(threadId)` yang menyatukan replay + live (pola `api/hooks/events.ts` snapshot).

---

## 5. Interface level kode

Semua signature di bawah adalah **kontrak rancangan** (Python 3.12, `from __future__ import annotations`). Pemilik file kontrak: AG-00 (WP-9.0). Nama DTO boleh disesuaikan implementer hanya lewat catatan eksplisit di laporan + persetujuan Lead.

### 5.0 Perubahan kontrak inti

#### 5.0.1 `app/llm/base.py` v2 (tambahan kompatibel-mundur)

```python
Sensitivity = Literal["public", "internal", "confidential"]
ServerToolKind = Literal["web_search", "web_fetch", "code_execution"]

@dataclass(slots=True, frozen=True)
class ServerToolSpec:
    """Tool yang dieksekusi provider. Versi dipin di config (WEB_SEARCH_TOOL_VERSION, …)."""
    kind: ServerToolKind
    version: str                                   # mis. "web_search_20250305"
    max_uses: int | None = None
    allowed_domains: tuple[str, ...] = ()          # API tidak menerima allowed+blocked sekaligus [Kemungkinan Besar]
    blocked_domains: tuple[str, ...] = ()
    citations: bool = True                         # web_fetch
    max_content_tokens: int | None = None          # web_fetch
    user_location: Mapping[str, str] | None = None # {"type": "approximate", "country": "ID", "timezone": "Asia/Jakarta"}

@dataclass(slots=True, frozen=True)
class SkillRef:
    source: Literal["anthropic", "custom"]
    skill_id: str                                  # "pptx" | "docx" | "xlsx" | "pdf"
    version: str = "latest"                        # dipin lewat DOC_SKILLS_VERSION

@dataclass(slots=True, frozen=True)
class ContainerSpec:
    skills: tuple[SkillRef, ...] = ()
    container_id: str | None = None                # reuse container antar-turn (hanya non-confidential)

CitationLocation = Literal["web_search_result_location", "search_result_location",
                           "char_location", "page_location", "content_block_location"]

@dataclass(slots=True, frozen=True)
class Citation:
    location: CitationLocation
    cited_text: str
    url: str | None = None                         # web
    title: str | None = None
    source: str | None = None                      # search_result.source, mis. "kb:chunk:<uuid>"
    search_result_index: int | None = None
    document_index: int | None = None
    start: int | None = None                       # char / page / block index awal
    end: int | None = None
    opaque: Mapping[str, Any] = field(default_factory=dict)  # encrypted_index dsb. — disimpan apa adanya untuk replay

# TextBlock (snapshot) mendapat field baru:  citations: list[Citation] = field(default_factory=list)

@dataclass(slots=True)
class ServerToolUseBlock:
    id: str                                        # "srvtoolu_…"
    name: str                                      # web_search | web_fetch | code_execution | bash_code_execution | text_editor_code_execution
    input: dict[str, Any]
    type: Literal["server_tool_use"] = "server_tool_use"

@dataclass(slots=True)
class ServerToolResultBlock:
    tool_use_id: str
    kind: str                                      # web_search_tool_result | web_fetch_tool_result | bash_code_execution_tool_result | …
    raw: dict[str, Any]                            # blok provider verbatim (encrypted_content dipertahankan untuk multi-turn)
    is_error: bool = False
    error_code: str | None = None                  # max_uses_exceeded | too_many_requests | url_not_accessible | …
    type: Literal["server_tool_result"] = "server_tool_result"

@dataclass(slots=True)
class SearchResultBlock:                           # input: top-level user content ATAU di dalam ToolResultBlock.content
    source: str
    title: str
    texts: list[str]
    citations: bool = True
    type: Literal["search_result"] = "search_result"

@dataclass(slots=True)
class DocumentBlock:                               # input: dokumen kecil bersitasi (ADR-046)
    media: Literal["text", "pdf"]
    data: str                                      # teks atau base64 PDF — TIDAK pernah file_id untuk confidential
    title: str | None = None
    context: str | None = None
    citations: bool = True
    type: Literal["document"] = "document"

@dataclass(slots=True, frozen=True)
class ProviderFileRef:                             # keluaran code execution / Skills
    file_id: str
    filename: str | None = None
    mime: str | None = None

# ToolResultBlock.content (snapshot: str) diperluas menjadi:  str | list[TextBlock | SearchResultBlock]
ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock | ServerToolUseBlock | ServerToolResultBlock | SearchResultBlock | DocumentBlock

# Usage mendapat:  server_tool_use: dict[str, int] = field(default_factory=dict)   # {"web_search_requests": n, "web_fetch_requests": n}
# LLMRequest mendapat (semua default → perilaku MVP tidak berubah):
#   server_tools: list[ServerToolSpec] = []
#   container: ContainerSpec | None = None
#   sensitivity: Sensitivity = "internal"        # provider MENOLAK (LLMError bad_request "SENSITIVITY_VIOLATION")
#                                                 # bila confidential + (container | code_execution | web_* ) — pertahanan berlapis
#   max_continuations: int = 0                     # loop pause_turn di provider; 0 = kembalikan ke caller
# LLMResponse mendapat:
#   content: list[TextBlock | ToolUseBlock | ServerToolUseBlock | ServerToolResultBlock]
#   container_id: str | None = None ;  provider_files: list[ProviderFileRef] = []

@dataclass(slots=True, frozen=True)
class TextDelta:
    block_index: int
    text: str

@dataclass(slots=True, frozen=True)
class CitationDelta:
    block_index: int
    citation: Citation

@dataclass(slots=True, frozen=True)
class ServerToolStarted:
    tool_use_id: str
    name: str
    input_summary: str                             # query / url (sudah diredaksi)

@dataclass(slots=True, frozen=True)
class ServerToolFinished:
    tool_use_id: str
    name: str
    ok: bool
    result_summary: str                            # "5 hasil" / error_code

StreamEvent = TextDelta | CitationDelta | ServerToolStarted | ServerToolFinished
StreamEventCallback = Callable[[StreamEvent], Awaitable[None]]
# LLMProvider.create_message(req, *, on_text_delta=None, on_event: StreamEventCallback | None = None, cancel=None)

@runtime_checkable
class ProviderFileStore(Protocol):                 # diimplementasikan AnthropicClaudeProvider (aturan P6 tetap)
    async def download(self, file_id: str, *, max_bytes: int) -> tuple[bytes, ProviderFileRef]: ...
    async def delete(self, file_id: str) -> None: ...
    async def upload(self, data: bytes, *, filename: str, mime: str, sensitivity: Sensitivity) -> str:
        """Menolak sensitivity == 'confidential' (Files API tidak eligible ZDR — ADR-046)."""
```

Pemetaan nama: tool kanonik bertitik (`web.search`) ↔ server tool provider (`web_search`) hanya di `anthropic_provider.py` (melengkapi ADR-023). Beta header (`web-fetch-…`, `code-execution-…`, `skills-…`, `files-api-…`) dihitung dari versi tool di provider [Kemungkinan Besar nama header; dibuktikan contract test].

#### 5.0.2 `app/agents/scope.py` — RunScope (BARU, usulan ADR U-01)

```python
class RunScope(Protocol):
    kind: Literal["task", "thread", "job"]   # job = tool-mode tanpa task/thread (impor repo J48, publish K51)
    owner_id: UUID
    project_id: UUID | None          # task: wajib; thread: project yang dilampirkan (read-only) atau None
    task_id: UUID | None
    thread_id: UUID | None
    sensitivity: Sensitivity
    correlation_id: str              # task_id atau "thread:<id>"

    async def emit(self, db: AsyncSession, type: str, payload: dict[str, Any], *, ephemeral: bool = False) -> int | None:
        """task → emit_task_event (snapshot); thread → emit_thread_event (assistant_events + kk:thread:{id})."""
    async def check_budget(self, db: AsyncSession, *, capability: str, model_tier: str) -> None:
        """task → BudgetGuard (snapshot); thread → CapabilityRegistry.check(..) (raise QuotaExceeded)."""
    def workspace(self) -> TaskWorkspace | RepoReadView | None: ...

# RepoReadView (BARU, app/workspace/fs.py — AG-02): tampilan read-only atas WORKSPACE_ROOT/<project>/repo untuk
# project yang dilampirkan ke thread (mis. K49 membaca README/kode). Hanya read_text/tree; resolve() menolak
# for_write=True; denylist kirim-ke-LLM (ARCH §7) tetap berlaku; akses butuh project:read milik owner thread.

class TaskScope(RunScope): ...      # pembungkus perilaku snapshot (tanpa perubahan semantik IDE)
class ThreadScope(RunScope): ...    # app/assistant/scope.py; dibuat oleh assistant_jobs
class JobScope(RunScope): ...       # emit → event domain (repository.sync_state / portfolio_publications) + log;
                                    # check_budget → CapabilityRegistry; tool_calls tanpa task_id/thread_id
```

`AgentRuntime.run(*, db, agent_id, scope: RunScope, agent_input, step, cancel, sandbox=None, on_event=None, server_tools_requested=())` — signature lama `task=…, workspace=…` dipertahankan sebagai adaptor (`TaskScope`) agar snapshot IDE tidak berubah.

#### 5.0.3 `app/tools/base.py` v2

```python
class PermissionProfile(BaseModel):                # K3: field baru
    filesystem: FsPermission = FsPermission.READ_ONLY
    sandbox: Literal["deny", "allow"] = "deny"
    git: GitPermission = GitPermission.READ
    network: Literal["deny", "allowlist", "provider_web"] = "deny"
    deployment: Literal["deny", "staging_with_approval"] = "deny"
    portfolio: Literal["none", "draft"] = "none"
    data_sensitivity_max: Sensitivity = "internal"
    approval_grant: Literal["deny"] = "deny"

ToolExecutor = Literal["backend", "sandbox", "provider", "worker"]

@dataclass(slots=True)
class ToolSpec:                                    # field snapshot tetap; tambahan berdefault
    ...
    executor: ToolExecutor = "backend"
    server_tool: ServerToolKind | Literal["skills"] | None = None   # wajib bila executor == "provider" (kecuali image.*)
    requires_network: Literal["provider_web"] | None = None
    requires_workspace: bool = False               # tool IDE lama: True (gateway menolak di ThreadScope tanpa workspace)
    requires_portfolio: Literal["draft"] | None = None
    max_sensitivity: Sensitivity = "confidential"  # docs.render_skills/code.execute_remote/web.*/image.* = "internal"
    unit: str | None = None                        # unit capability_usage (web_search, image, …)
    approval_binding: Callable[[BaseModel], Mapping[str, Any]] | None = None   # R3: field yang wajib == payload approval

@dataclass(slots=True, frozen=True)
class ApprovalGrant:                               # usulan ADR U-03 — hanya dibuat oleh job server setelah consume()
    approval_id: UUID
    action_type: str                               # github_push_pr | portfolio_publish
    payload_hash: str
    bound: Mapping[str, Any]                       # subset payload yang mengikat (repo_id, head_commit, head_branch, …)
    tools: frozenset[str]                          # mis. {"github.push_branch", "github.open_pr"}
    expires_at: datetime                           # ≤ 15 menit setelah consume

@dataclass(slots=True)
class ToolContext:                                 # task_id/project_id/workspace menjadi Optional
    task_id: UUID | None
    project_id: UUID | None
    agent_run_id: UUID | None
    agent_id: str
    actor_user_id: UUID
    workspace: TaskWorkspace | RepoReadView | None
    permissions: PermissionProfile
    correlation_id: str
    cancel: CancelToken
    db: AsyncSession
    sandbox: SandboxClient | None = None
    extras: dict[str, Any] = field(default_factory=dict)
    scope: RunScope | None = None                  # sumber emit()/budget; None hanya di test lama
    thread_id: UUID | None = None
    sensitivity: Sensitivity = "internal"
    ports: ToolPorts | None = None
    approval_grant: ApprovalGrant | None = None

@dataclass(slots=True, frozen=True)
class ToolPorts:                                   # dirakit di services/composition.py (ADR-022)
    capabilities: CapabilityRegistry
    assets: AssetStore
    kb: KBRetriever | None
    moderation: ModerationService | None
    images: ImageProvider | None
    doc_engines: Mapping[str, DocumentEngine]
    analysis: AnalysisRunner | None
    github: GitHubOps | None                       # fasad service-level (hanya di worker-integrations)
    portfolio: PortfolioOps | None
    defer: Callable[[str, dict[str, Any]], Awaitable[UUID]] | None   # enqueue job antrean lain → job_id
```

#### 5.0.4 `app/sandbox/client.py` v2

```python
@dataclass(slots=True)
class SandboxRunRequest:
    project_id: UUID | None                        # None untuk job
    task_id: UUID | None
    workspace_subpath: str                         # '<project>/tasks/<task>' | '<project>/repo' | '_jobs/<job>'
    profile_id: str
    args: list[str] = field(default_factory=list)
    timeout_s: int | None = None
    network: Network = "none"
    read_only: bool = False                        # ADR-033 (snapshot belum punya field ini)
    job_id: UUID | None = None                     # wajib bila subpath '_jobs/…'
```

### 5.1 `CapabilityRegistry` (app/capabilities/registry.py)

```python
class CapabilityName(StrEnum):
    CHAT = "chat"; WEB_RESEARCH = "web_research"; DOC_GENERATE = "doc_generate"; DATA_ANALYSIS = "data_analysis"
    KB_QA = "kb_qa"; IMAGE_GENERATE = "image_generate"; IMAGE_EDIT = "image_edit"
    GITHUB_SYNC = "github_sync"; GITHUB_PUSH_PR = "github_push_pr"; PORTFOLIO_PUBLISH = "portfolio_publish"; VOICE = "voice"

Unit = Literal["web_search", "web_fetch", "code_exec_seconds", "image", "document", "kb_ingest_page", "analysis_run"]

@dataclass(frozen=True, slots=True)
class CapabilityLimits:
    monthly_usd: Decimal | None = None             # anggaran biaya estimasi per user/bulan (token + unit)
    monthly_units: Mapping[Unit, int] = field(default_factory=dict)
    daily_units: Mapping[Unit, int] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class DataFlowEntry:                               # tabel data-flow di Settings (ADR-045)
    data: str                                      # "pesan & riwayat", "query pencarian", "outline dokumen", …
    recipient: str                                 # "Anthropic (Messages API)", "Anthropic (Skills container)", "vendor gambar"
    zdr_eligible: bool | None                      # None = tidak diketahui → tampil "belum diverifikasi"
    retention_note: str

@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    name: CapabilityName
    portals: frozenset[Literal["ide", "ai", "portfolio"]]
    engines: tuple[str, ...]                       # urutan preferensi, mis. ("claude_skills", "local_sandbox")
    tools: tuple[str, ...]                         # nama kanonik K3
    risk: RiskLevel
    permission: str                                # mis. "assistant:docs"
    flag: str                                      # feature flag (policy_settings.feature_flags)
    max_sensitivity: Sensitivity                   # di atas ini → SENSITIVITY_BLOCKED atau engine lokal
    default_limits: CapabilityLimits
    data_flow: tuple[DataFlowEntry, ...]
    phase: Literal["P9", "P10", "P11", "P12", "P2"]

DecisionCode = Literal["OK", "CAPABILITY_DISABLED", "PERMISSION_DENIED", "QUOTA_EXCEEDED",
                       "SENSITIVITY_BLOCKED", "KILL_SWITCH", "PROVIDER_UNAVAILABLE"]

@dataclass(frozen=True, slots=True)
class CapabilityDecision:
    allowed: bool
    code: DecisionCode
    reason: str
    remaining: Mapping[str, float | None]          # "usd", "web_search", "image", … (None = tanpa batas)

@dataclass(frozen=True, slots=True)
class CapabilityUsageItem:
    capability: CapabilityName
    unit: Unit
    quantity: Decimal
    estimated_cost: Decimal | None
    cost_status: Literal["estimated", "partial", "unavailable"]
    provider: str
    model: str | None = None
    thread_id: UUID | None = None
    message_id: UUID | None = None
    task_id: UUID | None = None
    request_id: str | None = None

@dataclass(frozen=True, slots=True)
class Actor:
    user_id: UUID
    permissions: frozenset[str]
    roles: frozenset[str]

class CapabilityRegistry(Protocol):
    def spec(self, name: CapabilityName) -> CapabilitySpec: ...
    def all(self) -> Sequence[CapabilitySpec]: ...
    async def effective_for(self, db: AsyncSession, actor: Actor) -> list[EffectiveCapability]: ...
    async def check(self, db: AsyncSession, actor: Actor, name: CapabilityName, *,
                    sensitivity: Sensitivity, requested_units: Mapping[Unit, Decimal] = {},
                    estimated_usd: Decimal | None = None) -> CapabilityDecision: ...
    async def reserve(self, actor: Actor, name: CapabilityName, units: Mapping[Unit, int], *, ttl_s: int = 600) -> str:
        """Reservasi Redis (INCRBY + TTL) agar request paralel tidak melampaui kuota; dilepas di record_usage."""
    async def record_usage(self, db: AsyncSession, actor: Actor, items: Sequence[CapabilityUsageItem], *,
                           reservation: str | None = None) -> None: ...
```

Urutan `check()`: flag global → `capability_settings` (global → role → user, yang paling spesifik menang) → kill switch → izin RBAC → sensitivitas vs `max_sensitivity` (rute lokal dicoba dulu oleh router) → kuota unit (harian/bulanan, ledger SQL + reservasi Redis) → anggaran USD (Σ `usage_records.estimated_cost` ber-`capability` + Σ `capability_usage.estimated_cost`). Biaya `unavailable` tidak dihitung sebagai 0: bila harga tidak diketahui dan `monthly_usd` di-set, keputusan memakai kuota unit saja dan UI menampilkan "biaya tidak tersedia" (R-09).

### 5.2 `ProviderRouter` (app/capabilities/router.py)

```python
EngineName = Literal["anthropic", "claude_skills", "local_sandbox", "code_execution_remote",
                     "image_vendor", "kb_lexical", "github_app"]

@dataclass(frozen=True, slots=True)
class RouteContext:
    actor: Actor
    capability: CapabilityName
    sensitivity: Sensitivity
    thread_id: UUID | None = None
    project_id: UUID | None = None
    prefer_engine: EngineName | None = None        # pilihan user (mis. "local_sandbox"), tidak bisa MENAIKKAN risiko
    allow_local_fallback: bool = True

@dataclass(frozen=True, slots=True)
class Route:
    capability: CapabilityName
    engine: EngineName
    provider: str                                  # "anthropic" | "<vendor>" | "local"
    model: str | None
    server_tools: tuple[ServerToolSpec, ...]
    container: ContainerSpec | None
    reason: str                                    # "confidential → local_sandbox (ADR-039)"
    degraded: bool = False                         # True bila fallback karena outage

class CapabilityUnavailable(AppError):             # 403 POLICY_DENIED / 429 QUOTA_EXCEEDED / 503 DEPENDENCY_UNAVAILABLE
    decision: CapabilityDecision

class ProviderRouter(Protocol):
    async def route(self, db: AsyncSession, rc: RouteContext) -> Route: ...
    def llm(self) -> LLMProvider: ...
    def files(self) -> ProviderFileStore: ...
    def image(self) -> ImageProvider: ...          # raise CapabilityUnavailable bila IMAGE_PROVIDER kosong
    def document_engine(self, engine: Literal["claude_skills", "local_sandbox"]) -> DocumentEngine: ...
    def health(self) -> Mapping[str, ProviderHealth]: ...   # circuit breaker per provider (Redis)
```

Aturan routing ada di `config/providers.yaml` (berversi) dan tabel §9.5. Router **tidak pernah** memilih engine dengan sensitivitas lebih longgar daripada data; fallback hanya ke engine yang **lebih ketat** (Skills → lokal), dan selalu ditandai `degraded=True` + label UI. Circuit breaker: 5 kegagalan `transient/overloaded/timeout` dalam 120 dtk → `open` 60 dtk → `half-open` satu percobaan [Menebak angka; dikonfigurasi].

### 5.3 `ImageProvider` (app/llm/image_base.py — mengikuti PLATFORM_EXPANSION §3)

```python
ImageSize = Literal["1024x1024", "1024x1536", "1536x1024"]   # disesuaikan vendor ADR-040a

@dataclass(frozen=True, slots=True)
class ImageRequest:
    prompt: str                                    # sudah lulus moderasi pra-generate
    size: ImageSize
    n: int = 1                                     # ≤ IMAGE_MAX_N (default 4)
    style: str | None = None
    seed: int | None = None
    references: tuple[bytes, ...] = ()             # byte milik user (bukan URL — tidak ada fetch di backend)
    user_ref: str = ""                             # hash user untuk abuse tracking vendor (bukan email)

@dataclass(frozen=True, slots=True)
class ImageEditRequest:
    image: bytes
    instruction: str
    mask: bytes | None = None
    size: ImageSize | None = None
    user_ref: str = ""

@dataclass(frozen=True, slots=True)
class ImageSafety:
    flagged: bool
    reasons: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class ImageMeta:
    width: int
    height: int
    c2pa_present: bool

@dataclass(frozen=True, slots=True)
class ImageResult:
    images: list[bytes]
    mime: Literal["image/png", "image/jpeg", "image/webp"]
    provider: str
    model: str
    revised_prompt: str | None
    usage: Mapping[str, int]                       # {"images": n, "tokens": m?}
    request_id: str | None
    safety: ImageSafety
    image_meta: list[ImageMeta] = field(default_factory=list)

class ImageProvider(Protocol):
    name: str
    async def generate(self, req: ImageRequest, *, cancel: CancelToken | None = None) -> ImageResult: ...
    async def edit(self, req: ImageEditRequest, *, cancel: CancelToken | None = None) -> ImageResult: ...
    def estimate_cost(self, size: ImageSize, n: int, *, edit: bool = False) -> tuple[Decimal | None, str]:
        """(usd, pricing_version) dari pricing.yaml; None → 'unavailable'."""
```

Error vendor dinormalisasi ke `LLMError` kind yang sama (`rate_limited`, `refusal` untuk penolakan safety vendor, …). **Contract test** (`tests/contract/test_image_provider.py`) dijalankan untuk `image_fake` dan vendor terpilih (respx): bentuk respons, error mapping, `safety.flagged`, retensi C2PA (byte tidak diubah), timeout/cancel.

### 5.4 `DocumentEngine` (app/docengine/base.py)

```python
DocKind = Literal["pptx", "docx", "xlsx", "pdf"]

@dataclass(frozen=True, slots=True)
class DocTheme:
    font_family: str = "Inter"                     # default sans-serif (PLATFORM_EXPANSION §4.1)
    primary_color: str = "#1F4E79"
    template_id: str | None = None                 # template brand terdaftar (backend/app/docengine/templates/)

@dataclass(frozen=True, slots=True)
class DocSpec:                                     # keluaran terstruktur I44 untuk engine lokal (JSON → spec.json)
    kind: DocKind
    title: str
    slides: list[SlideSpec] = field(default_factory=list)       # pptx
    sections: list[SectionSpec] = field(default_factory=list)   # docx/pdf
    sheets: list[SheetSpec] = field(default_factory=list)       # xlsx (nilai, bukan formula eksekusi)

@dataclass(frozen=True, slots=True)
class DocRequest:
    kind: DocKind
    owner_id: UUID
    thread_id: UUID | None
    sensitivity: Sensitivity
    prompt: str | None = None
    outline: str | None = None
    spec: DocSpec | None = None                    # wajib untuk local_sandbox (dibuat I44 atau deterministik: CV)
    theme: DocTheme = DocTheme()
    locale: Literal["id", "en"] = "id"
    source_asset_ids: tuple[UUID, ...] = ()        # file lampiran (teks diekstrak, bukan dikirim mentah ke Skills bila confidential)
    container_id: str | None = None                # iterasi di container Skills yang sama (non-confidential)

@dataclass(frozen=True, slots=True)
class DocValidation:
    opened_ok: bool
    checker: str                                   # "docs.inspect@<image digest>"
    page_count: int | None
    warnings: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class DocResult:
    asset_id: UUID
    kind: DocKind
    engine: Literal["claude_skills", "local_sandbox"]
    filename: str
    size_bytes: int
    sha256: str
    validation: DocValidation
    thumbnail_asset_id: UUID | None
    usage: tuple[CapabilityUsageItem, ...]
    container_id: str | None                       # untuk iterasi berikut (Skills)

class DocumentEngine(Protocol):
    name: Literal["claude_skills", "local_sandbox"]
    def supports(self, kind: DocKind) -> bool: ...
    async def generate(self, req: DocRequest, *, scope: RunScope, cancel: CancelToken,
                       on_progress: Callable[[str], Awaitable[None]] | None = None) -> DocResult: ...
```

- `ClaudeSkillsEngine`: `LLMRequest(server_tools=[code_execution], container=ContainerSpec(skills=(SkillRef("anthropic", kind),)), sensitivity=req.sensitivity, max_continuations=8)`; ambil `ProviderFileRef` dari blok hasil code execution [Kemungkinan Besar bentuk blok]; `ProviderFileStore.download(max_bytes=ASSET_MAX_FILE_MB)` → `AssetStore.put` → `docs.inspect` + `docs.thumbnail` di sandbox → `ProviderFileStore.delete(file_id)` (best-effort; sweep `provider_files_cleanup` mengulang). Menolak `sensitivity == "confidential"` (AX-06) sebelum membuat request.
- `LocalSandboxEngine`: tulis `spec.json` ke `_jobs/<id>/in` → `docs.render_<kind>` (atau `docs.render_docx` + `docs.convert_pdf` untuk PDF) → `docs.inspect` → `docs.thumbnail` → `AssetStore.put_from_job`.

### 5.5 `ResearchService` (app/assistant/research.py)

```python
@dataclass(frozen=True, slots=True)
class DomainPolicy:
    allowed: tuple[str, ...] = ()                  # policy_settings "web_research.domains" (admin)
    blocked: tuple[str, ...] = ()                  # admin + per-user; bila allowed tidak kosong, allowed menang
    fetch_enabled: bool = True

@dataclass(frozen=True, slots=True)
class ResearchConfig:
    mode: Literal["quick", "deep"]
    search_max_uses: int                           # min(WEB_SEARCH_MAX_USES_QUICK|DEEP, sisa kuota)
    fetch_max_uses: int                            # WEB_FETCH_MAX_USES_QUICK|DEEP; 0 = web_fetch tidak dideklarasikan
    allowed_domains: tuple[str, ...]
    blocked_domains: tuple[str, ...]
    user_location: Mapping[str, str]               # {"type": "approximate", "country": "ID", "timezone": "Asia/Jakarta"}
    model_tier: Literal["default", "complex"]      # quick=default, deep=complex
    max_tokens: int

@dataclass(frozen=True, slots=True)
class SearchRecord:                                # dari server_tool_use + web_search_tool_result
    tool_use_id: str
    query: str
    searched_at: datetime                          # waktu blok hasil diterima worker (UTC)
    results: tuple[tuple[str, str, str | None], ...]   # (url, title, page_age)
    error_code: str | None

@dataclass(frozen=True, slots=True)
class ResearchOutcome:
    content: list[ContentBlock]                    # untuk replay multi-turn (encrypted_content dipertahankan)
    answer_text: str
    citations: list[ExtractedCitation]
    searches: list[SearchRecord]
    grounding: GroundingReport
    usage: Usage
    container_id: str | None

class ResearchService(Protocol):
    def config_for(self, mode: Literal["quick", "deep"], policy: DomainPolicy, remaining_searches: int) -> ResearchConfig: ...
    def server_tools(self, cfg: ResearchConfig) -> list[ServerToolSpec]: ...
    async def run(self, db: AsyncSession, scope: ThreadScope, *, history: list[Message], question: str,
                  cfg: ResearchConfig, cancel: CancelToken, on_event: StreamEventCallback) -> ResearchOutcome: ...
```

Mode **Cepat**: satu turn I43 (server tools web_search ≤ 3, web_fetch ≤ 2) dengan `max_continuations` untuk `pause_turn`. Mode **Mendalam**: I43 menulis rencana riset singkat (teks, ditampilkan) lalu satu loop agentik dengan web_search ≤ 20 dan web_fetch ≤ 10, laporan berstruktur dengan daftar sumber; dijalankan di antrean `assistant` dengan batas waktu job 10 menit [Menebak]. Sistem prompt I43 mewajibkan: setiap klaim faktual dari web disitasi, tanggal pencarian disebut, konflik antarsumber dilaporkan, dan "Saya tidak menemukan sumber" bila tidak ada.

### 5.6 `CitationExtractor` (app/assistant/citations.py)

```python
@dataclass(frozen=True, slots=True)
class SourceIndex:                                 # dibangun per request oleh pemanggil
    kb_sources: Mapping[str, UUID]                 # search_result.source ("kb:chunk:<uuid>") → chunk_id
    kb_by_position: Sequence[UUID]                 # search_result_index → chunk_id (urutan blok yang dikirim)
    documents: Sequence[UUID]                      # document_index → asset_id
    web_searches: Sequence[SearchRecord]           # untuk memetakan url → (query, searched_at)

@dataclass(frozen=True, slots=True)
class ExtractedCitation:
    ordinal: int
    kind: Literal["web", "kb", "document"]
    location: CitationLocation
    cited_text: str
    url: str | None
    title: str | None
    query: str | None
    searched_at: datetime | None
    chunk_id: UUID | None
    asset_id: UUID | None
    page_start: int | None
    page_end: int | None
    char_start: int | None
    char_end: int | None
    answer_start: int                              # offset di answer_text (penanda superscript di UI)
    answer_end: int
    verified: bool | None                          # kb/document: cited_text ⊂ teks sumber (normalisasi spasi); web: None

@dataclass(frozen=True, slots=True)
class GroundingReport:
    status: Literal["cited", "partially_cited", "uncited", "not_applicable"]
    web_used: bool
    uncited_spans: tuple[tuple[int, int, str], ...]    # (start, end, alasan: "angka"|"tanggal"|"kata 'terkini'")
    searched_at_min: datetime | None
    searched_at_max: datetime | None

class CitationExtractor(Protocol):
    def extract(self, content: Sequence[ContentBlock], sources: SourceIndex, *,
                mode: str, now: datetime) -> tuple[str, list[ExtractedCitation], GroundingReport]: ...
    async def verify(self, db: AsyncSession, citations: Sequence[ExtractedCitation]) -> list[ExtractedCitation]: ...
```

Pemetaan: `web_search_result_location` → `kind=web` (url, title, cited_text; `query`/`searched_at` dari `SearchRecord` yang memuat url tersebut — bila url muncul di beberapa pencarian, ambil yang paling awal); `search_result_location` → `kind=kb` (via `source` lebih dulu, `search_result_index` sebagai cadangan); `char_location`/`page_location` → `kind=document` (via `document_index`). `GroundingReport`: mode riset tanpa satu pun sitasi web → `uncited` + label "Tanpa sumber web"; blok teks tanpa sitasi yang memuat angka/tanggal/kata waktu-terkini (regex id/en) → `uncited_spans` (peringatan UI, ADR-038 #2). Kegagalan pemetaan **tidak** menghapus sitasi: disimpan dengan `verified=false` dan dicatat metrik `citation_unmapped_total`.

### 5.7 `KBIngestor` & `KBRetriever` (app/kb/)

```python
@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    target_tokens: int = 700                       # [Menebak] dikalibrasi di WP-11.3
    overlap_tokens: int = 100
    max_chars: int = 6000
    respect_headings: bool = True                  # potong di batas heading/halaman bila memungkinkan

@dataclass(frozen=True, slots=True)
class IngestReport:
    document_id: UUID
    status: Literal["indexed", "failed"]
    pages: int
    chunks: int
    context_tokens_in: int
    context_tokens_out: int
    estimated_cost: Decimal | None
    error: str | None

class KBIngestor(Protocol):
    async def ingest(self, db: AsyncSession, document_id: UUID, *, scope: RunScope, cancel: CancelToken) -> IngestReport:
        """kb.extract (sandbox) → chunk (pure Python) → ringkasan kontekstual + kata kunci (Claude fast, structured
        output, prompt cache dokumen/bagian; inline untuk confidential) → INSERT kb_chunks (tsv = kolom generated)."""
    async def reindex(self, db: AsyncSession, document_id: UUID, *, scope: RunScope, cancel: CancelToken) -> IngestReport: ...
    async def delete(self, db: AsyncSession, document_id: UUID) -> None: ...

@dataclass(frozen=True, slots=True)
class KBQuery:
    collection_ids: tuple[UUID, ...]
    text: str                                      # ≤ 500 karakter; boleh dari agent I47 (query buatan model)
    top_k: int = 8
    per_document_max: int = 3
    min_score: float = 0.05

@dataclass(frozen=True, slots=True)
class KBHit:
    chunk_id: UUID
    document_id: UUID
    collection_id: UUID
    title: str                                     # "<nama file> — hlm 3–4"
    source: str                                    # "kb:chunk:<uuid>" (dipakai search_result.source)
    text: str
    page_start: int | None
    page_end: int | None
    score: float
    score_lexical: float
    score_trgm: float

class KBRetriever(Protocol):
    async def search(self, db: AsyncSession, actor: Actor, q: KBQuery) -> list[KBHit]:
        """Scope: koleksi milik actor atau space-nya (404 bila tidak). Skor = 0.7·norm(ts_rank_cd(tsv,
        websearch_to_tsquery('simple', q), 32)) + 0.3·greatest(word_similarity(q, keywords),
        word_similarity(q, context_summary)); diversifikasi per dokumen."""
    async def get_chunk(self, db: AsyncSession, actor: Actor, chunk_id: UUID, *, neighbors: int = 1) -> list[KBHit]: ...
    def to_search_results(self, hits: Sequence[KBHit]) -> list[SearchResultBlock]: ...
```

Catatan bahasa: konfigurasi `simple` tidak melakukan stemming Bahasa Indonesia (me-/-kan/di-); prompt ringkasan kontekstual meminta **kata dasar + sinonim id/en** di `keywords` untuk menaikkan recall; agent I47 dianjurkan memanggil `kb.search` 2–3 kali dengan variasi istilah. Diukur di eval §10.4.

### 5.8 `ModerationService` (app/moderation/service.py — S52)

```python
ModerationStage = Literal["policy", "classifier", "vendor_pre", "vendor_post"]
ModerationCategory = Literal["real_person", "minor", "sexual", "violence_graphic", "hate", "self_harm",
                             "weapons", "brand_logo", "copyrighted_character", "personal_data", "other"]

@dataclass(frozen=True, slots=True)
class ModerationVerdict:
    decision: Literal["allow", "deny", "review"]   # review = tampil ke user dengan alasan, tidak dieksekusi otomatis
    stage: ModerationStage
    categories: tuple[ModerationCategory, ...]
    reasons: tuple[str, ...]                       # teks aman untuk UI (id/en)
    subject_hash: str                              # sha256 prompt/gambar — dipakai pengikatan ke image.generate

class ModerationService(Protocol):
    async def check_text(self, db: AsyncSession, actor: Actor, text: str, *,
                         purpose: Literal["chat", "kb_upload", "portfolio", "job_post"]) -> ModerationVerdict: ...
    async def check_image_prompt(self, db: AsyncSession, actor: Actor, prompt: str, *,
                                 reference_asset_ids: Sequence[UUID] = ()) -> ModerationVerdict: ...
    async def check_image_output(self, db: AsyncSession, actor: Actor, result: ImageResult) -> list[ModerationVerdict]: ...
```

Pipeline (§9.4): leksikon deterministik id/en (tolak cepat) → classifier Claude tier `fast` dengan **structured output** (aman: tidak ada sitasi) → moderasi vendor (pre/post) → hasil `flagged` tidak pernah disimpan sebagai aset maupun ditampilkan; hanya `moderation_events` (hash + kategori). `image.generate`/`image.edit` menerima `moderation_verdict_id` (TOOL_CATALOG v2) dan menolak (`POLICY_DENIED`) bila verdict itu bukan `allow`, milik user lain, berumur > 10 menit, atau `subject_hash`-nya ≠ sha256 prompt/instruksi yang dikirim.

### 5.9 `AssetStore` (app/assets/store.py)

```python
AssetKind = Literal["upload", "generated", "derived", "site_bundle"]

@dataclass(frozen=True, slots=True)
class AssetRef:
    id: UUID
    owner_id: UUID
    kind: AssetKind
    mime: str
    filename: str
    size_bytes: int
    sha256: str
    storage_ref: str                               # relatif ASSETS_ROOT: "<owner>/<yyyy>/<mm>/<asset_id>"
    sensitivity: Sensitivity
    created_at: datetime

@dataclass(frozen=True, slots=True)
class SignedUrl:
    url: str                                       # "/api/v1/assets/<id>/download?exp=<unix>&disp=<a|i>&sig=<b64url>"
    expires_at: datetime

class AssetStore(Protocol):
    async def put(self, db: AsyncSession, *, owner_id: UUID, data: bytes | AsyncIterator[bytes], filename: str,
                  declared_mime: str, kind: AssetKind, sensitivity: Sensitivity, source: str,
                  provenance: Mapping[str, Any], retention_days: int | None, max_bytes: int) -> AssetRef:
        """Tulis ke file sementara (O_EXCL, 0640) → sha256 + ukuran → sniff magic bytes (allowlist mime; SVG/HTML
        ditolak) → rename atomik → INSERT assets. Kuota ASSET_QUOTA_MB_PER_USER dicek sebelum rename."""
    async def put_from_job(self, db: AsyncSession, job_dir: Path, rel: str, **kw: Any) -> AssetRef:
        """rel divalidasi safe-path di bawah job_dir/out (tolak symlink)."""
    async def open(self, db: AsyncSession, actor: Actor, asset_id: UUID) -> tuple[AssetRef, AsyncIterator[bytes]]: ...
    def sign(self, asset_id: UUID, actor_id: UUID, *, disposition: Literal["attachment", "inline"], ttl_s: int) -> SignedUrl:
        """sig = HMAC-SHA256(K_assets, f"{asset_id}|{actor_id}|{exp}|{disp}"), K_assets = HKDF(SECRET_KEY, info=b"kk-assets-v1")."""
    def verify(self, asset_id: UUID, actor_id: UUID, exp: int, disposition: str, sig: str) -> bool: ...   # compare_digest
    async def delete(self, db: AsyncSession, asset_id: UUID, *, reason: str) -> None: ...
    async def sweep(self, db: AsyncSession, now: datetime) -> int: ...   # retention_until lewat & tidak pinned
```

Unduhan mewajibkan **sesi aktif user yang sama** dengan `actor_id` di tanda tangan **dan** tanda tangan valid (pertahanan berlapis; URL bocor di log tidak berguna bagi orang lain). Header: `Content-Disposition: attachment` (default), `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox`, `Cache-Control: private, no-store`; `inline` hanya untuk `image/png|jpeg|webp` dan `application/pdf` (pratinjau pdf.js membaca byte via fetch, bukan navigasi).

### 5.10 `GitHubAppClient` (app/integrations/github/client.py)

```python
GHPermission = Literal["metadata", "contents", "pull_requests", "checks", "statuses"]   # TIDAK ada "workflows"
GHAccess = Literal["read", "write"]

@dataclass(frozen=True, slots=True)
class InstallationToken:
    token: SecretStr                               # "ghs_…" — tidak pernah disimpan/di-log (redaksi gh[pousr]_)
    expires_at: datetime                           # ≤ 1 jam [Pasti]
    repository_ids: tuple[int, ...]
    permissions: Mapping[GHPermission, GHAccess]

class GitHubAppClient(Protocol):
    def app_jwt(self, *, now: datetime | None = None) -> str:
        """RS256; iat = now − 60 dtk, exp = now + 9 menit (maks 10 menit [Pasti]); iss = GITHUB_CLIENT_ID (atau
        GITHUB_APP_ID). Hanya di app_auth.py; key dibaca dari GITHUB_APP_PRIVATE_KEY_PATH saat start, disimpan di memori."""
    async def mint_installation_token(self, installation_id: int, *, repository_ids: Sequence[int],
                                      permissions: Mapping[GHPermission, GHAccess]) -> InstallationToken:
        """POST /app/installations/{id}/access_tokens {repository_ids, permissions} — SELALU dibatasi 1 repo
        (kecuali sinkron daftar repo: metadata:read tanpa repository_ids). Validasi: token yang dikembalikan
        tidak boleh punya permission di luar yang diminta (fail closed)."""
    async def get_installation(self, installation_id: int) -> InstallationInfo: ...
    async def list_installation_repos(self, installation_id: int, *, page: int = 1) -> Page[RepoInfo]: ...
    async def get_repo(self, token: InstallationToken, repo_id: int) -> RepoInfo: ...
    async def get_readme(self, token: InstallationToken, full_name: str, *, max_bytes: int = 65536) -> str | None: ...
    async def create_pull_request(self, token: InstallationToken, *, full_name: str, head: str, base: str,
                                  title: str, body: str, draft: bool) -> PullRequestInfo: ...
    async def get_pull_request(self, token: InstallationToken, full_name: str, number: int) -> PullRequestInfo: ...
    async def get_combined_checks(self, token: InstallationToken, full_name: str, sha: str) -> ChecksSummary: ...
    async def exchange_user_code(self, code: str) -> SecretStr:
        """OAuth user-to-server ("ghu_…"), dipakai sekali untuk verifikasi lalu dibuang."""
    async def user_installation_ids(self, user_token: SecretStr) -> set[int]: ...
    async def user_identity(self, user_token: SecretStr) -> tuple[int, str]: ...   # (github_user_id, login)
```

Semua request: `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: ${GITHUB_API_VERSION}`, timeout 20 dtk, retry hanya untuk 5xx/secondary rate limit (hormati `Retry-After`/`X-RateLimit-Reset`), tanpa retry untuk 4xx. Log hanya method, path templat, status, `X-GitHub-Request-Id`.

### 5.11 `GitCredentialInjector` (app/integrations/github/credentials.py)

```python
class GitCredentialInjector(Protocol):
    def env_for(self, token: InstallationToken, *, host: str = "github.com") -> Mapping[str, str]:
        """Mengembalikan env proses git SAJA (tidak pernah argv/config):
             GIT_CONFIG_COUNT=3
             GIT_CONFIG_KEY_0=http.https://github.com/.extraheader
             GIT_CONFIG_VALUE_0=AUTHORIZATION: basic <b64("x-access-token:" + token)>
             GIT_CONFIG_KEY_1=credential.helper   GIT_CONFIG_VALUE_1=        (kosong — matikan helper)
             GIT_CONFIG_KEY_2=http.followRedirects GIT_CONFIG_VALUE_2=false
             GIT_TERMINAL_PROMPT=0  GIT_ASKPASS=/bin/false  GCM_INTERACTIVE=never
           [Kemungkinan Besar — butuh git ≥ 2.31; diuji di image backend]."""
    def assert_clean(self, repo: Path) -> None:
        """Setelah setiap operasi: .git/config tidak memuat 'extraheader', 'credential', userinfo di url remote,
        atau nilai token (raise CredentialLeak → operasi gagal + audit)."""
```

`GitAdapter` (snapshot, AG-02) mendapat parameter `extra_env: Mapping[str, str] | None` di `clone_https`, `fetch`, `push_branch`; env tidak pernah di-log; `push_branch(repo, branch, remote_url, *, expected_remote_sha: str | None)` memakai refspec `refs/heads/<b>:refs/heads/<b>` **tanpa `+`** dan `--porcelain`, menolak bila remote sudah ada dengan SHA berbeda (tidak ada force push — R4).

### 5.12 `WebhookVerifier` (app/integrations/github/webhooks.py)

```python
ALLOWED_EVENTS = frozenset({"installation", "installation_repositories", "push", "pull_request",
                            "check_suite", "github_app_authorization"})

@dataclass(frozen=True, slots=True)
class WebhookAcceptResult:
    status: Literal["accepted", "duplicate", "ignored"]
    delivery_id: str
    event: str

class WebhookVerifier(Protocol):
    def verify(self, raw_body: bytes, signature_header: str | None) -> bool:
        """Header 'sha256=<64 hex>'; HMAC-SHA256(secret, raw_body) dibandingkan hmac.compare_digest terhadap
        GITHUB_WEBHOOK_SECRET lalu (bila di-set) GITHUB_WEBHOOK_SECRET_PREVIOUS (rotasi tanpa downtime)."""
    async def accept(self, db: AsyncSession, headers: Mapping[str, str], raw_body: bytes) -> WebhookAcceptResult:
        """Urutan: ukuran ≤ GITHUB_WEBHOOK_MAX_BYTES (413) → verify (401, tanpa insert DB) → event allowlist
        (200 ignored) → parse JSON → INSERT webhook_deliveries(delivery_id) ON CONFLICT DO NOTHING
        (0 baris → 200 duplicate) → enqueue integration_jobs.handle_webhook(delivery_id) setelah commit → 202."""
```

Payload disimpan sementara di Redis (`kk:gh:delivery:<id>`, TTL 1 jam) untuk diproses job; DB hanya menyimpan `payload_sha256` (bukan isi), sehingga data repo dari webhook tidak menumpuk di DB.

### 5.13 `PortfolioSiteBuilder` (app/portfolio/site.py)

```python
@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:                           # dibekukan dari profil + item visibility=public & AI-approved
    profile_id: UUID
    version: int
    data: Mapping[str, Any]                        # hanya field publik; kontak hanya bila public=true
    asset_ids: tuple[UUID, ...]                    # foto/gambar yang dipakai (EXIF GPS dihapus saat build)
    sha256: str                                    # sha256(canonical_json(data) + asset sha256 terurut)

@dataclass(frozen=True, slots=True)
class SiteFile:
    path: str                                      # relatif root situs, safe-path
    sha256: str
    size_bytes: int
    mime: str

@dataclass(frozen=True, slots=True)
class SiteBundle:
    bundle_asset_id: UUID                          # tar.gz di asset store (kind=site_bundle)
    sha256: str
    files: tuple[SiteFile, ...]
    entry: str                                     # "index.html"

class PortfolioSiteBuilder(Protocol):
    async def build(self, db: AsyncSession, snapshot: PortfolioSnapshot, *, theme: str, base_url: str,
                    scope: RunScope, cancel: CancelToken) -> SiteBundle:
        """portfolio.build_site (R2) di sandbox-docs: Jinja2 autoescape=True, tanpa <script>, CSS lokal,
        meta SEO/OG dari field publik; keluaran diperiksa: tidak ada tag script/iframe/on* handler (fail closed)."""
```

### 5.14 `PrivacyChecker` (app/portfolio/privacy.py)

```python
FindingKind = Literal["email", "phone", "address", "national_id", "private_repo_content", "secret",
                      "exif_gps", "ai_unapproved", "non_public_item", "external_tracker"]

@dataclass(frozen=True, slots=True)
class PrivacyFinding:
    path: str                                      # "items[3].data.achievements[1]" atau "site/index.html"
    kind: FindingKind
    severity: Literal["block", "warn"]
    detail: str

@dataclass(frozen=True, slots=True)
class PrivacyReport:
    ok: bool                                       # tidak ada finding "block"
    findings: tuple[PrivacyFinding, ...]
    public_contact_fields: tuple[str, ...]
    private_repo_items: tuple[tuple[str, tuple[str, ...]], ...]   # (full_name, field metadata yang dipilih)
    sha256: str

class PrivacyChecker(Protocol):
    def check(self, snapshot: PortfolioSnapshot, bundle: SiteBundle | None = None,
              bundle_texts: Mapping[str, str] | None = None) -> PrivacyReport:
        """Murni + secret scanner (core.redaction pola + tools/builtin/security). Block: email/telepon tanpa
        flag public, pola NIK/NPWP, secret, teks AI belum disetujui (AX-09), isi repo privat di luar field
        metadata terpilih (AX-08), EXIF GPS tersisa, skrip/pelacak eksternal."""
```

### 5.15 Registrasi tool v2 di ToolGateway

#### 5.15.1 Katalog v2 (K3) → `ToolSpec`

| Tool | Risk | executor | server_tool | requires_* / max_sensitivity | unit | Handler / modul | Antrean tempat dieksekusi |
|---|---|---|---|---|---|---|---|
| `capabilities.list` | R0 | backend | – | – | – | `builtin/capabilities.py` → ports.capabilities | assistant |
| `web.search` | R0 | provider | `web_search` | `network: provider_web`; ≤ internal | web_search | spec provider (tanpa handler) | assistant |
| `web.fetch` | R0 | provider | `web_fetch` | `network: provider_web`; ≤ internal | web_fetch | spec provider | assistant |
| `code.execute_remote` | R2 | provider | `code_execution` | `sandbox: allow` + `network: provider_web`; ≤ internal | code_exec_seconds | spec provider | media |
| `docs.render_skills` | R1 | provider | `skills` | `network: provider_web`; ≤ internal | document | `ClaudeSkillsEngine` | media |
| `docs.render_local` | R2 | sandbox | – | `sandbox: allow` | document | `builtin/docs.py` → `LocalSandboxEngine` (di assistant: *deferred* → media) | media |
| `assets.store` | R1 | backend | – | – | – | ports.assets | media |
| `image.generate` / `image.edit` | R1 | provider | – (ImageProvider) | ≤ internal; verdict moderasi terikat | image | `builtin/image.py` → ports.images | media |
| `moderation.check_text` / `moderation.check_image_prompt` | R0 | backend | – | – | – | ports.moderation | assistant/media |
| `kb.search` / `kb.get_chunk` | R0 | backend | – | scope koleksi | – | ports.kb → `ToolOutput.content` = list `SearchResultBlock` | assistant |
| `analysis.run` | R2 | sandbox | – | `sandbox: allow` | analysis_run | ports.analysis (`analysis.python`) | media |
| `github.list_repos`, `github.get_pr_status`, `github.get_repo_summary` | R0 | backend | – | `git: read` | – | ports.github (cache DB; `get_pr_status` menyegarkan via job) | integrations / assistant (cache) |
| `github.import_repo`, `github.pull` | R1 | worker | – | `git: read` | – | ports.github | integrations |
| `github.push_branch`, `github.open_pr` | **R3** | worker | – | `git: branch_commit`; `approval_action="github_push_pr"`; `approval_binding` | – | ports.github | integrations |
| `portfolio.get_profile` | R0 | backend | – | – | – | ports.portfolio | assistant |
| `portfolio.draft_item` | R1 | backend | – | `portfolio: draft` | – | ports.portfolio (selalu `visibility=draft`, `ai_generated=true`) | assistant |
| `portfolio.privacy_check` | R0 | backend | – | – | – | PrivacyChecker | media |
| `portfolio.build_site` | R2 | sandbox | – | `sandbox: allow` | – | PortfolioSiteBuilder | media |
| `portfolio.publish` | **R3** | backend (K3; proses worker antrean integrations) | – | `approval_action="portfolio_publish"`; `approval_binding`; hanya K51 | – | ports.portfolio | integrations |

`build_default_registry()` v2: `CATALOG_TOOL_NAMES` + `CATALOG_TOOL_NAMES_V2`; tool v2 didaftarkan **disabled** dengan alasan `"capability <x> nonaktif"` kecuali flag capability aktif (dievaluasi saat start + saat `capability_settings` berubah via pub/sub `kk:capabilities`). `FORBIDDEN_PREFIXES` tetap; `web.` dan `image.` tidak termasuk prefix terlarang karena dieksekusi provider/adapter, bukan fetch backend.

#### 5.15.2 Jalur server tool (provider-executed)

```python
class ToolGateway:  # tambahan v2
    async def authorize_server_tools(self, ctx: ToolContext, requested: Sequence[str], *,
                                     route: Route) -> ServerToolGrant:
        """Per tool: terdaftar & enabled → allowlist profil (registry authoritative) → permission
        (network: provider_web / sandbox) → ctx.sensitivity ≤ spec.max_sensitivity → CapabilityRegistry.check
        + reserve(max_uses). Hasil: ServerToolGrant(specs=[ServerToolSpec dgn max_uses = min(config, sisa)],
        denied=[(tool, reason)], reservation). Penolakan → tool_calls(status=denied, executor=provider) + audit
        tool.denied. Tidak ada request ke provider bila semua tool & capability inti ditolak."""

    async def record_server_tool_calls(self, ctx: ToolContext, grant: ServerToolGrant,
                                       content: Sequence[ContentBlock], usage: Usage) -> list[UUID]:
        """Satu tool_calls per ServerToolUseBlock: executor=provider, tool_name kanonik (web.search…),
        sanitized_input {query|url}, status ok|error (dari error_code hasil), output_summary (n hasil, domain),
        idempotency_key = sha256(agent_run_id|srvtoolu_id). capability_usage dari usage.server_tool_use
        (sumber kebenaran jumlah, bukan hitungan blok). Audit tool.executed untuk SEMUA panggilan provider
        (data keluar ke pihak ketiga, ADR-045). Blok server tool yang tidak di-grant → audit
        tool.provider_unexpected + pesan ditandai (anomali; seharusnya mustahil)."""
```

Siklus di `AgentRuntime` (scope thread): `authorize_server_tools` → `scope.check_budget` → `LLMProvider.create_message(server_tools=grant.specs, on_event=…)` → `record_server_tool_calls` → tool klien (bila ada `ToolUseBlock`) lewat `execute()` seperti snapshot → ulangi sampai `end_turn` (cited_text) atau `submit_result`.

#### 5.15.3 Jalur R3 dengan `ApprovalGrant`

1. Service (aksi user) membangun payload kanonik → `approvals.request(action_type, payload)` (status `pending`, `payload_hash`).
2. User memutuskan via `POST /approvals/{id}/decision` (CSRF, izin §12 API).
3. Handler terdaftar (`register_handler("github_push_pr", …)`) hanya **enqueue** job ke antrean `integrations` setelah commit (ADR-024: handler idempotent, `handler_status=succeeded` saat job ter-enqueue).
4. Job: `consume()` atomik (`status=approved AND payload_hash=:h AND belum kedaluwarsa`) → membangun `ApprovalGrant(bound=payload subset, tools={…}, expires_at=now+15m)` → menjalankan agent tool-mode (J48/K51) dengan `ctx.approval_grant`.
5. Gateway langkah (6) R3: bila `ctx.approval_grant` ada, `spec.approval_action == grant.action_type`, `spec.name ∈ grant.tools`, belum kedaluwarsa, input `approval_id` (TOOL_CATALOG v2: `{task_id, approval_id}` / `{version_id, target, approval_id}`) sama dengan `grant.approval_id`, dan `spec.approval_binding(validated) ⊆ grant.bound` (kesetaraan per field) → izinkan tanpa approval baru, `tool_calls.approval_id = grant.approval_id`; selain itu perilaku snapshot (minta approval baru). LLM tidak pernah dapat membuat grant (tidak ada tool/field yang dapat diisi model; J48/K51 deterministik).

### 5.16 Pemetaan agent v2 ke runtime

| Agent | Mode / `output_contract` (registry) | Antrean | Scope | Tier | Allowlist (K2) | Permission profile v2 |
|---|---|---|---|---|---|---|
| I42 Concierge | llm; routing `submit_result` (`RouteDecision{route: answer/delegate/clarify, capability, target_agent, …}` — AGENT_CATALOG v2) divalidasi *assistant service*; `route: answer` → jawaban teks (bersitasi bila memakai `kb.search`) | assistant | thread | fast (routing) / default (jawaban) | `capabilities.list`, `kb.search`, `submit_result` | fs none, network deny, sens ≤ confidential |
| I43 Web Research | llm; `CitedAnswer` | assistant | thread | default (cepat) / complex (mendalam) | `web.search`*, `web.fetch`* | network provider_web, sens ≤ internal |
| I44 Document Builder | llm; `submit_result` (hasil dokumen) — dokumen bukan teks bersitasi | media | thread | default | `docs.render_skills`*, `docs.render_local`, `assets.store`, `submit_result` | sandbox allow, network provider_web (untuk Skills), sens ≤ confidential (Skills dibatasi spec ≤ internal; Ruang confidential hanya `docs.render_local`) |
| I45 Visual Prompt | llm; `submit_result` | media | thread | fast | `moderation.check_image_prompt`, `image.generate`, `image.edit`, `submit_result` | sens ≤ internal |
| I46 Data Analyst | llm; `submit_result` (`AnalysisResult{summary, tables, charts, code_asset_id}`) | media | thread | default | `analysis.run` atau `code.execute_remote`*, `assets.store`, `submit_result` | sandbox allow, sens ≤ confidential (remote ≤ internal + network provider_web) |
| I47 Knowledge Base | llm; `CitedAnswer` | assistant | thread | default | `kb.search`, `kb.get_chunk` | sens ≤ confidential |
| J48 GitHub Integration | tool (deterministik) | integrations | task (push/PR) / none (import) | – | `github.*` | git branch_commit |
| K49 Portfolio Curator | llm; `submit_result` | assistant | thread kind `portfolio` | default | `portfolio.get_profile`, `github.get_repo_summary`, `repo.read_file` (RepoReadView read-only), `portfolio.draft_item`, `submit_result` | fs read_only, portfolio draft |
| K50 CV & Job-fit | llm; teks bersitasi saat mengutip lowongan (ADR-047), `submit_result` untuk draft item | assistant | thread kind `portfolio` | default | `web.fetch`*, `portfolio.get_profile`, `docs.render_local` (*deferred* ke media), `portfolio.draft_item`, `submit_result` | network provider_web, portfolio draft, sens ≤ internal |
| K51 Portfolio Publisher | tool (deterministik) | media (privacy, build) + integrations (publish) | none | – | `portfolio.privacy_check`, `portfolio.build_site`, `portfolio.publish` | sandbox allow |
| S52 Content Safety | tool (deterministik + classifier) | assistant/media | – | fast (classifier) | `moderation.*` | – |
| E29 PR & Collaboration (promosi) | llm; `submit_result` (`PRDraft{title ≤ 72, body_markdown, checklist, risk_notes, evidence_refs, …}` — AGENT_CATALOG v2; user boleh menyunting judul sampai 256 karakter di API) | ide | task | default | `review.get_task_diff`, `git.log`, `acceptance.list_criteria` (R0) | fs read_only, git read |

\* = server tool (K2). *Deferred tool*: handler mengembalikan `{"job_id": "...", "status": "queued"}` dan hasil tiba lewat SSE `asset.created` — model diberi tahu bahwa render berjalan di latar.

---

## 6. Alur detail

### 6.1 Chat + riset web bersitasi (UC-11, UC-12) — streaming SSE

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser (ThreadView)
  participant API as api
  participant R as Redis
  participant DB as PostgreSQL
  participant WA as worker-assistant
  participant CAP as CapabilityRegistry
  participant GW as ToolGateway
  participant CL as Claude Messages API
  B->>API: POST /assistant/threads/{tid}/messages {content, mode: research_quick}
  API->>API: sesi + CSRF + assistant:use, redaksi secret, moderasi teks (leksikon)
  API->>CAP: check(web_research, sensitivity=thread, requested_units={web_search: 3})
  CAP-->>API: OK + sisa kuota
  API->>DB: INSERT pesan user + pesan assistant (queued) + event message.started
  API->>R: enqueue assistant_jobs.respond(message_id) setelah commit
  API-->>B: 202 {user_message, assistant_message, events_url}
  B->>API: GET /assistant/threads/{tid}/events (Last-Event-ID)
  API->>DB: replay assistant_events dengan seq lebih besar
  API->>R: SUBSCRIBE kk:thread:{tid}
  WA->>DB: klaim pesan (status streaming), bangun ThreadScope
  WA->>GW: authorize_server_tools([web.search, web.fetch], route)
  GW->>CAP: check + reserve(max_uses)
  GW-->>WA: ServerToolGrant (search 3, fetch 2)
  WA->>CL: messages.stream(system I43, riwayat, server_tools)
  CL-->>WA: server_tool_use web_search {query}
  WA->>R: PUBLISH tool.activity (started, query)
  CL-->>WA: web_search_tool_result (url, title, page_age)
  WA->>R: PUBLISH tool.activity (finished, jumlah hasil)
  CL-->>WA: text delta + citations delta
  WA->>R: PUBLISH assistant.delta (ephemeral)
  WA->>DB: event citation.added (seq) per sitasi baru
  CL-->>WA: message_stop + usage.server_tool_use
  WA->>WA: CitationExtractor.extract → GroundingReport
  WA->>GW: record_server_tool_calls (executor=provider, audit)
  WA->>DB: pesan completed, message_citations, usage_records, capability_usage
  WA->>DB: event usage.updated + message.completed
  R-->>API: event baru
  API-->>B: SSE assistant.delta, tool.activity, citation.added, usage.updated, message.completed
```

Aturan: (1) `searched_at` = waktu worker menerima blok hasil pencarian (UTC) dan ditampilkan "dicari pada <tanggal>"; (2) tanpa pencarian → label **"Tanpa sumber web"**; (3) `pause_turn` → worker mengirim ulang konten assistant sebagai lanjutan (maks `max_continuations`), event tetap satu pesan; (4) cancel (`POST /assistant/messages/{id}/cancel`) membatalkan stream via `CancelToken`, status `cancelled`, sitasi yang sudah tersimpan dipertahankan; (5) error provider → event `error` + `message.completed{status: failed}`; reservasi kuota dilepas sesuai pemakaian aktual.

### 6.2 Pembuatan dokumen (UC-13) — Skills vs lokal

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser
  participant API as api
  participant WM as worker-media
  participant RT as ProviderRouter
  participant CL as Claude (code execution + Skills)
  participant FA as Claude Files API
  participant SM as sandbox-manager
  participant SD as sandbox-docs
  participant AS as AssetStore
  B->>API: POST /assistant/documents {kind: pptx, prompt atau outline, template_id, engine?, thread_id}
  API->>API: assistant:docs, sensitivitas efektif = max(space, lampiran)
  API-->>B: 202 {job_id, message_id, engine_preview}
  API->>WM: enqueue media_jobs.render_document
  WM->>RT: route(doc_generate, sensitivity)
  alt sensitivity ≤ internal dan Skills aktif
    RT-->>WM: Route(claude_skills, container skills pptx)
    WM->>CL: I44 (code_execution + container.skills), loop pause_turn
    CL-->>WM: hasil code execution berisi file_id
    WM->>FA: download(file_id, max_bytes)
    FA-->>WM: byte .pptx
    WM->>AS: put(generated, source=claude_skills, provenance)
    WM->>FA: delete(file_id)
  else confidential, kill switch, atau Skills gagal (degraded)
    RT-->>WM: Route(local_sandbox)
    WM->>CL: I44 tanpa container → submit_result DocSpec
    Note over WM,CL: Bila kill switch provider aktif, langkah ini dilewati dan DocSpec dibangun deterministik dari outline user
    WM->>SM: run docs.render_pptx (_jobs/id, network none)
    SM->>SD: python -m kkdocs.render
    SD-->>SM: out/deck.pptx
    WM->>AS: put_from_job(out/deck.pptx, source=local_sandbox)
  end
  WM->>SM: run docs.inspect dan docs.thumbnail
  SM-->>WM: inspect.json (opened_ok, jumlah slide) + thumb.png
  WM->>API: event asset.created + message.completed (Redis + assistant_events)
  B->>API: POST /assets/{id}/signed-url
  API-->>B: {url, expires_at}
  B->>API: GET /assets/{id}/download?exp, disp, sig (cookie sesi)
  API-->>B: 200 attachment, nosniff, CSP sandbox
```

Validasi wajib sebelum `asset.created`: `docs.inspect` `opened_ok = true`, ekstensi cocok dengan kind (tolak `.pptm/.docm/.xlsm`), rasio dekompresi ≤ 100, ukuran ≤ `ASSET_MAX_FILE_MB`. Gagal → pesan `failed` dengan alasan, aset dihapus (AX-05).

### 6.3 Generate gambar + moderasi + kuota (UC-14)

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser (ImageStudio)
  participant API as api
  participant CAP as CapabilityRegistry
  participant WM as worker-media
  participant MOD as ModerationService (S52)
  participant CL as Claude (classifier fast)
  participant IS as services.images
  participant IP as ImageProvider (vendor)
  participant AS as AssetStore
  B->>API: GET /capabilities (image_generate: sisa hari ini, estimasi biaya per gambar)
  B->>API: POST /assistant/images {prompt, size, n, style, reference_asset_ids, thread_id}
  API->>CAP: check(image_generate, sensitivity, units image=n, estimated_usd)
  alt nonaktif, kuota habis, atau confidential
    CAP-->>API: deny
    API-->>B: 403 POLICY_DENIED atau 429 QUOTA_EXCEEDED (tanpa panggilan vendor)
  else diizinkan
    API->>CAP: reserve(image=n)
    API-->>B: 202 {job_id, estimated_cost, cost_status}
    API->>WM: enqueue media_jobs.generate_image
    WM->>MOD: check_image_prompt(prompt)
    MOD->>MOD: leksikon id/en (stage policy)
    MOD->>CL: classifier structured output
    CL-->>MOD: {decision, categories}
    alt deny atau review
      MOD-->>WM: verdict deny (moderation_events)
      WM-->>B: SSE error MODERATION_BLOCKED, reservasi dilepas
    else allow
      WM->>IS: generate (langsung, atau lewat ToolGateway image.generate untuk agent I45)
      IS->>IS: cek verdict allow untuk subject_hash ≤ 10 menit, sensitivitas, kuota
      IS->>IP: generate(ImageRequest)
      IP-->>IS: ImageResult(images, safety, request_id)
      IS->>MOD: check_image_output (flag vendor)
      alt flagged
        IS-->>WM: gambar dibuang, hanya hash di moderation_events
      else bersih
        IS->>AS: put per gambar (provider, model, prompt_hash, request_id, c2pa_present)
        IS-->>WM: asset ids
      end
      WM->>CAP: record_usage(image = jumlah terkirim, estimated_cost)
      WM-->>B: SSE asset.created per gambar, usage.updated, message.completed
    end
  end
```

Dua pintu masuk (endpoint langsung dan agent I45) berujung di `services.images` yang sama; jalur agent tambahan melewati allowlist/permission ToolGateway. Biaya ditagihkan per gambar yang **diterima dari vendor** (termasuk yang kemudian di-flag) karena vendor menagihnya [Kemungkinan Besar].

### 6.4 KB ingest & tanya jawab bersitasi (UC-15)

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser (KnowledgePage)
  participant API as api
  participant AS as AssetStore
  participant WM as worker-media
  participant SD as sandbox-docs (kb.extract)
  participant CL as Claude (fast)
  participant DB as PostgreSQL (kb_chunks)
  participant WA as worker-assistant (I47)
  B->>API: POST /assistant/files (multipart, sensitivity)
  API->>AS: put(upload) dengan sniff magic bytes dan kuota
  API-->>B: 201 AssistantFile
  B->>API: POST /assistant/kb/collections/{cid}/documents {file_id}
  API->>DB: kb_documents (pending)
  API->>WM: enqueue media_jobs.ingest_kb_document
  WM->>SD: kb.extract (_jobs/id/in/file, network none)
  SD-->>WM: blocks.jsonl (teks + halaman)
  WM->>WM: chunk (±700 token, overlap 100, batas heading/halaman)
  loop per kelompok chunk (prompt cache konteks dokumen)
    WM->>CL: ringkasan kontekstual + keywords (structured output, inline)
    CL-->>WM: daftar {ordinal, summary, keywords}
  end
  WM->>DB: INSERT kb_chunks (tsv generated), status indexed, capability_usage kb_ingest_page
  WM-->>B: event dokumen indexed
  B->>API: POST /assistant/threads/{tid}/messages {mode: kb, attachments: kb_collection}
  API->>WA: enqueue respond
  WA->>CL: I47 (tools kb.search, kb.get_chunk, tanpa web)
  CL-->>WA: tool_use kb.search {query}
  WA->>DB: KBRetriever.search (ts_rank_cd + word_similarity, top 8, maks 3 per dokumen)
  WA->>CL: tool_result berisi blok search_result (citations enabled)
  CL-->>WA: teks + search_result_location
  WA->>WA: CitationExtractor (source → chunk_id) + verifikasi cited_text di dalam chunk
  WA->>DB: message_citations (kind kb, chunk_id, halaman) + events
  WA-->>B: SSE citation.added → panel sumber (nama file, halaman, kutipan)
```

Dokumen tunggal kecil yang dilampirkan langsung ke pesan (≤ `KB_INLINE_DOCUMENT_MAX_PAGES`) dikirim utuh sebagai blok `document` bersitasi (char/page_location), **inline** (base64/teks) — Files API tidak dipakai untuk `confidential` (ADR-046).

### 6.5 GitHub: instal → impor repo privat → task → push & PR (UC-17, UC-18)

**6.5a Instalasi & penautan identitas**

```mermaid
sequenceDiagram
  autonumber
  participant U as Browser
  participant API as api
  participant R as Redis
  participant GH as GitHub
  participant WI as worker-integrations
  participant DB as PostgreSQL
  U->>API: GET /integrations/github/install-url
  API->>R: SET state nonce (TTL 10 menit, terikat hash sesi, sekali pakai)
  API-->>U: {url ke github.com/apps/slug/installations/new?state=…}
  U->>GH: pilih akun + repo privat, Install, otorisasi user
  GH-->>U: redirect ke /api/v1/integrations/github/callback?installation_id, setup_action, state, code
  U->>API: GET callback (cookie sesi, navigasi top-level)
  API->>R: GETDEL state lalu cocokkan hash sesi
  API->>DB: github_link_requests (installation_id klaim, pending)
  API->>R: enqueue verify_installation (code terenkripsi di Redis, TTL 5 menit)
  API-->>U: 302 /settings/integrations/github?link=id
  WI->>GH: tukar code → token user ghu (sekali pakai)
  WI->>GH: GET /user dan GET /user/installations (token user)
  alt installation_id bukan milik user GitHub
    WI->>DB: link rejected + audit integration.github.link_rejected
  else valid
    WI->>GH: JWT app → GET /app/installations/{id}
    WI->>DB: github_installations, github_user_links, github_installation_grants
    WI->>GH: token metadata read → GET /installation/repositories
    WI->>DB: github_repositories (cache)
  end
  WI->>WI: token user dibuang (tidak disimpan)
  GH->>API: webhook installation_repositories (X-Hub-Signature-256, X-GitHub-Delivery)
  API->>API: verifikasi HMAC constant-time, dedupe delivery, 202
  API->>R: enqueue handle_webhook → sinkron cache repo
```

`installation_id` di callback dapat dipalsukan oleh penyerang; karena itu tautan hanya dibuat setelah verifikasi lewat token user GitHub (langkah 10–12) [Kemungkinan Besar sesuai peringatan dokumentasi GitHub].

**6.5b–c Impor, task, push & PR**

```mermaid
sequenceDiagram
  autonumber
  participant U as Browser
  participant API as api
  participant WI as worker-integrations (J48)
  participant GH as GitHub
  participant WS as Workspace (repo dan tasks)
  participant WIDE as worker-ide (task, E29)
  participant AP as Approvals
  U->>API: POST /projects {name, source: {type: github_app, repo_id, branch}}
  API->>API: izin grant user atas repo, ukuran repo ≤ PROJECT_QUOTA_MB
  API-->>U: 202 Project (sync_state importing) + meta.job_id
  API->>WI: enqueue import_repo
  WI->>GH: JWT → access_tokens {repository_ids: [X], permissions: {contents: read}}
  WI->>WS: git clone --single-branch (token via env extraheader, remote tanpa userinfo)
  WI->>WI: assert_clean(.git/config), token dibuang
  WI->>WIDE: enqueue index + deteksi stack (antrean ide)
  Note over U,WIDE: Task berjalan seperti MVP (S1–S9) di worktree kk/task-xxxx tanpa akses GitHub
  U->>API: POST /tasks/{id}/github/pr-draft
  API->>WIDE: E29 → submit_result PRDraft dari diff dan kriteria
  WIDE-->>U: SSE task event github.pr_draft_ready {title, body}
  U->>API: POST /tasks/{id}/github/push-pr {title, body, draft, base}
  API->>API: github:push, status task, path policy .github/workflows, secret scan diff
  API->>AP: request github_push_pr (repo, head_branch, head_commit, base, diff_sha256, pr)
  API-->>U: 202 {approval}
  U->>API: POST /approvals/{aid}/decision {approve, payload_hash}
  API->>WI: handler → enqueue push_pr setelah commit
  WI->>AP: consume() atomik → ApprovalGrant 15 menit
  WI->>WS: verifikasi head kk/task-xxxx = head_commit, ulang path policy + secret scan
  WI->>GH: access_tokens {repository_ids: [X], permissions: {contents: write, pull_requests: write}}
  WI->>GH: git push refs/heads/kk/task-xxxx (tanpa tanda plus, tanpa force)
  WI->>GH: POST /repos/owner/repo/pulls {head, base, title, body, draft}
  GH-->>WI: PR nomor n (html_url)
  WI->>API: github_pull_requests + audit + task event github.pr_opened
  GH->>API: webhook pull_request dan check_suite → refresh status PR
```

Pull (`POST /projects/{id}/git/pull`, R1): fetch default branch remote; bila `repo/` bersih dan dapat fast-forward → ff; bila tidak → branch `kk/sync-<yyyymmddhhmm>` di commit remote + event `repository.sync_conflict` (tidak pernah menimpa perubahan lokal). Push-PR tidak mengubah status task (tidak butuh transisi baru di `workflow/states.py`).

### 6.6 Portfolio: impor → CV → publish (UC-19, UC-20)

```mermaid
sequenceDiagram
  autonumber
  participant U as Browser (Portfolio)
  participant API as api
  participant WI as worker-integrations
  participant WA as worker-assistant (K49, K50)
  participant WM as worker-media (CV, privacy, build)
  participant AP as Approvals
  participant ST as Situs (PORTFOLIO_SITE_ROOT atau GitHub Pages)
  U->>API: POST /portfolio/import/github {repo_ids, fields}
  API->>WI: enqueue snapshot_repos_for_portfolio
  WI->>WI: token read per repo → README ≤ 64 KB, bahasa, topik, bintang (hanya field terpilih untuk repo privat)
  WI->>WA: enqueue portfolio_curate(snapshot_id)
  WA->>WA: K49 → portfolio.draft_item (draft, ai_generated)
  WA-->>U: SSE item "Draft AI, perlu persetujuan"
  U->>API: PATCH /portfolio/items/{id} lalu POST /portfolio/items/{id}/approve-ai
  U->>API: POST /portfolio/cv {template: ats_sans, format: pdf, item_ids, locale}
  API->>WM: enqueue render_cv (DocSpec deterministik dari item disetujui, tanpa LLM)
  WM->>WM: docs.render_docx → docs.convert_pdf → docs.inspect (sandbox-docs)
  WM-->>U: SSE asset.created (CV), unduh via signed URL
  U->>API: POST /portfolio/preview
  API->>WM: snapshot versi → privacy_check → build_site (sandbox)
  WM-->>U: bundle + PrivacyReport + URL pratinjau (iframe sandbox)
  U->>API: POST /portfolio/publish {target: static, version_id}
  API->>API: PrivacyReport ok (tanpa finding block), bundle_sha256 cocok
  API->>AP: request portfolio_publish (version_id, snapshot_sha256, bundle_sha256, target, url, privacy_report_sha256)
  API-->>U: 202 {approval, publication}
  U->>API: POST /approvals/{aid}/decision {approve, payload_hash}
  API->>WI: handler → enqueue publish_portfolio
  WI->>AP: consume() → ApprovalGrant
  WI->>WI: K51 portfolio.publish → verifikasi sha bundle, ekstraksi aman
  alt target static
    WI->>ST: tulis releases/version lalu swap symlink current (atomik)
  else target github_pages
    WI->>ST: token contents write repo Pages → push branch gh-pages (tanpa force)
  end
  WI-->>U: publication published + url
```

Unpublish (`POST /portfolio/publications/{id}/unpublish`) mengurangi eksposur sehingga R1 tanpa approval: symlink `current` diarahkan ke halaman kosong `maintenance/` dalam satu operasi atomik; untuk GitHub Pages, commit kosong-situs **juga** butuh token contents:write → tetap job integrations, tanpa approval *(turunan)*.

---

## 7. Rencana migrasi database

### 7.0 Rantai & kebijakan

```
0001_initial (snapshot) → 0002_ide_backlog → 0003_platform_assistant → 0004_github → 0005_portfolio → 0006_kb
```

| Migrasi | Di-land oleh | Wave | Catatan urutan |
|---|---|---|---|
| 0002 | WP-INT-02 (AG-01) | W3 | prasyarat semua v2 |
| 0003 | WP-9.0 (AG-00 + AG-01) | awal W7 | *schema-first*: tabel assistant/capability/asset inert sampai flag P10 aktif; perubahan scope (`agent_runs`, `tool_calls`, `approvals`) dibutuhkan P9 |
| 0004 | WP-9.2 (AG-18) | W7 | |
| 0005 | WP-12.1 (AG-21) | setelah G10 (paralel W9) | WBS: WP-12.1 hanya bergantung WP-G10 → di-land sebelum 0006 |
| 0006 | WP-11.3 (AG-19) | W9 | bila siap sebelum 0005, Lead me-rebase `down_revision` (0006 → 0004) dan 0005 → 0006; nomor file tetap |

Kebijakan: enum = `VARCHAR + CHECK` (ADR-018; menambah nilai = `DROP CONSTRAINT` + `ADD CONSTRAINT … NOT VALID` + `VALIDATE CONSTRAINT`); kolom baru di tabel besar ditambahkan nullable/berdefault konstan (tanpa rewrite tabel di PG 16); index besar `CREATE INDEX CONCURRENTLY` di migrasi terpisah bila tabel > 1 juta baris [Menebak ambang]; setiap migrasi punya `downgrade()` yang diuji (`alembic upgrade head && alembic downgrade -1 && alembic upgrade head`) di CI integrasi. Tabel baru ber-`id uuid` (aplikasi), `created_at`, dan `updated_at` bila mutable (DATA_MODEL konvensi). Detail kolom: `DATA_MODEL.md` → "Ekstensi v2".

### 7.1 0002_ide_backlog (ADR-021/024/025/031/032/033/035)

| Tabel | Perubahan | ADR |
|---|---|---|
| `tasks` | + `pause_requested bool not null default false` | 031 |
| `agent_runs` | CHECK `status` + `approval_required` | 025 |
| `usage_records` | CHECK `cost_status` + `partial` | 025 |
| `approvals` | + `handler_status varchar(16) not null default 'pending'` CHECK (`pending`,`succeeded`,`failed`,`not_required`), + `handler_attempts int not null default 0`, + `handler_error text null`; index parsial `(handler_status) WHERE handler_status IN ('pending','failed') AND status IN ('approved','rejected')` (recovery sweep) | 024 |
| `test_runs` | + `failure_kind varchar(20) not null default 'none'` CHECK (`none`,`assertion`,`lint`,`build`,`dependency`,`runner_crash`,`timeout`,`infra`), + `read_only bool not null default false` | 035, 033 |
| `provider_configs` | + `readiness_status varchar(16) not null default 'unknown'` CHECK (`unknown`,`passed`,`failed`), + `readiness_checked_at timestamptz null`, + `readiness_report jsonb not null default '{}'` | 032 |
| — | ADR-021: **tanpa DDL** (status task sama; transisi di `workflow/states.py`) | 021 |

### 7.2 0003_platform_assistant (core v2 + Portal AI + capability)

**Perubahan tabel existing**

| Tabel | Perubahan | Alasan |
|---|---|---|
| `agent_runs` | `task_id` → nullable; + `thread_id uuid null fk assistant_threads(id) on delete cascade`, + `message_id uuid null fk assistant_messages(id) on delete set null`, + `owner_id uuid null fk users on delete set null`; CHECK `num_nonnulls(task_id, thread_id) = 1`; index `(thread_id)` | RunScope (U-01) |
| `tool_calls` | `task_id` → nullable; + `thread_id uuid null fk assistant_threads on delete cascade`, + `executor varchar(12) not null default 'backend'` CHECK (`backend`,`sandbox`,`provider`,`worker`), + `capability varchar(40) null`, + `provider_request_id varchar(120) null`; CHECK `num_nonnulls(task_id, thread_id) <= 1` (job tool-mode tanpa scope, mis. K51, diizinkan dengan `agent_run_id` null) ; index `(thread_id)` | K3 executor provider |
| `usage_records` | + `owner_id uuid null fk users set null`, + `thread_id uuid null fk assistant_threads set null`, + `message_id uuid null`, + `capability varchar(40) null`, + `server_tool_use jsonb not null default '{}'`; index `(owner_id, created_at)`, `(capability, created_at)` | anggaran per capability |
| `approvals` | `project_id` → nullable; + `owner_id uuid null fk users set null`, + `scope_type varchar(16) not null default 'project'` CHECK (`project`,`portfolio`,`integration`); CHECK `scope_type <> 'project' OR project_id IS NOT NULL`; index `(owner_id, status)` | U-07 (action type baru ditambah di 0004/0005) |
| `policy_settings` | data (bukan DDL): kunci baru `web_research.domains`, `retention.assistant`, `provider_kill_switch` (per capability), `feature_flags.capabilities` | ADR-044/045 |

**Tabel baru:** `capability_settings`, `capability_usage`, `assets`, `assistant_spaces`, `assistant_threads`, `assistant_messages` (+ kolom `search_tsv` generated + GIN untuk "cari riwayat"), `assistant_events`, `message_citations` (`chunk_id` tanpa FK sampai 0006), `assistant_files`, `generated_assets`, `custom_assistants`, `moderation_events`. Constraint penting:
- `message_citations`: CHECK `kind <> 'web' OR (url IS NOT NULL AND searched_at IS NOT NULL)` — AX-04 ditegakkan di DB.
- `assistant_events`: UNIQUE `(thread_id, seq)`; `assistant_threads.event_seq` sebagai counter (pola `tasks.event_seq`).
- `capability_usage`: CHECK `quantity >= 0`; index `(owner_id, capability, created_at)`.
- `assets`: UNIQUE `storage_ref`; index parsial `(retention_until) WHERE deleted_at IS NULL AND pinned = false`.

### 7.3 0004_github

| Objek | Isi |
|---|---|
| Tabel baru | `github_installations` (UNIQUE `installation_id`), `github_installation_grants` (UNIQUE `(installation_pk, user_id)`), `github_user_links` (UNIQUE `github_user_id`; UNIQUE `(user_id, github_user_id)`), `github_link_requests` (state verifikasi, TTL logis 1 jam), `github_repositories` (UNIQUE `(installation_pk, repo_id)`; index `full_name`), `webhook_deliveries` (PK `delivery_id`), `github_pull_requests` (UNIQUE `(github_repository_id, number)`; index `task_id`), `github_repo_snapshots` (metadata + README terpotong untuk portfolio; TTL 30 hari) |
| `repositories` | CHECK `provider` + `github_app`; + `github_repository_id uuid null fk github_repositories on delete set null`; CHECK `provider <> 'github_app' OR github_repository_id IS NOT NULL` |
| `approvals` | CHECK `action_type` + `github_push_pr` |
| Retensi | `webhook_deliveries` 30 hari; `github_link_requests` 7 hari; tidak ada kolom token di skema mana pun (diuji AX-01) |

### 7.4 0005_portfolio

| Objek | Isi |
|---|---|
| Tabel baru | `portfolio_profiles` (UNIQUE `owner_id`, UNIQUE `slug`), `portfolio_items` (index `(profile_id, type, sort_order)`), `portfolio_versions` (UNIQUE `(profile_id, version)`), `portfolio_publications` (index `(profile_id, status)`) |
| Constraint AX-09 | `portfolio_items`: CHECK `NOT (visibility = 'public' AND ai_generated AND ai_approved_at IS NULL)` |
| `approvals` | CHECK `action_type` + `portfolio_publish` |
| Retensi | versi: 20 terakhir + yang dirujuk publikasi; bundle situs (asset `site_bundle`): 5 terakhir + yang aktif |

### 7.5 0006_kb

| Objek | Isi |
|---|---|
| Ekstensi | `CREATE EXTENSION IF NOT EXISTS pg_trgm` — **wajib** di sini (di 0001 opsional); gagal → migrasi berhenti dengan pesan jelas (RUNBOOK) |
| Tabel baru | `kb_collections` (UNIQUE `(owner_id, name)`), `kb_documents` (UNIQUE `(collection_id, file_id)`), `kb_chunks` (UNIQUE `(document_id, ordinal)`) |
| Kolom FTS | `kb_chunks.tsv tsvector GENERATED ALWAYS AS (setweight(to_tsvector('simple', coalesce(heading_path,'') ‖ ' ' ‖ coalesce(context_summary,'') ‖ ' ' ‖ coalesce(keywords,'')), 'A') ‖ setweight(to_tsvector('simple', text), 'B')) STORED` — `to_tsvector` dengan regconfig eksplisit bersifat immutable [Pasti] |
| Index | `GIN (tsv)`; `GIN (keywords gin_trgm_ops)`; `GIN (context_summary gin_trgm_ops)`; `BTREE (collection_id)` |
| FK susulan | `message_citations.chunk_id → kb_chunks(id) ON DELETE SET NULL` |
| Retensi | chunk mengikuti dokumen; dokumen mengikuti file/koleksi |

### 7.6 Retensi default v2 *(turunan; dapat diubah via `policy_settings.retention`, ADR-045)*

| Data | Default | Penghapusan |
|---|---|---|
| `assistant_events` | 30 hari | beat `retention_cleanup` |
| `assistant_messages`, `message_citations` | sampai thread dihapus user (`retention.assistant_messages_days` = null) | hapus thread → cascade |
| Aset upload & generated | 180 hari sejak dibuat, kecuali `pinned` | `AssetStore.sweep` (file + baris) |
| Bundle situs portfolio | 5 terakhir + aktif | sweep |
| File di Claude Files API | ≤ `PROVIDER_FILES_TTL_MINUTES` (60) | delete setelah unduh + sweep |
| `_jobs/<id>` | ≤ 2 jam | sweep |
| `webhook_deliveries` | 30 hari | sweep |
| `moderation_events` | 365 hari | sweep |
| `capability_usage` | 400 hari | sweep |
| `github_repo_snapshots` | 30 hari | sweep |
| Audit | 365 hari (tetap) | – |

---

## 8. Deployment & operasi

### 8.1 Perubahan `infra/compose.yml` (sketsa — milik AG-06 di WP terkait)

```yaml
# Anchor baru (pola sama dengan *workspace-mount snapshot: bind, path identik, create_host_path: false)
#   *assets-mount         ${ASSETS_HOST_PATH:-/srv/kodekantor/assets}            -> /srv/kodekantor/assets (rw)
#   *assets-mount-ro      idem, read_only: true
#   *portfolio-site-rw    ${PORTFOLIO_SITE_HOST_PATH:-/srv/kodekantor/portfolio} -> /srv/kodekantor/portfolio (rw)

x-worker-command: &worker-command
  - celery
  - --app=app.worker.celery_app
  - worker
  - --loglevel=${LOG_LEVEL:-INFO}
  - --optimization=fair
  - --max-tasks-per-child=50
  - --without-gossip
  - --without-mingle
  # --queues / --concurrency / --hostname dibaca dari WORKER_QUEUES, CELERY_WORKER_CONCURRENCY, nama service
  #   (entrypoint kecil `app/worker/entry.py` menyusun argv; tidak lewat shell)

services:
  worker-ide:                      # pengganti service `worker` snapshot
    <<: *backend
    command: *worker-command
    environment:
      <<: *backend-env
      KK_PROCESS_ROLE: worker-ide
      WORKER_QUEUES: ide
      ANTHROPIC_API_KEY:
    networks: [app, outbound]
    volumes: [*workspace-mount]

  worker-assistant:
    <<: *backend
    command: *worker-command
    environment:
      <<: *backend-env
      KK_PROCESS_ROLE: worker-assistant
      WORKER_QUEUES: assistant
      CELERY_WORKER_CONCURRENCY: "3"
      ANTHROPIC_API_KEY:
      WEB_SEARCH_TOOL_VERSION:
      WEB_FETCH_TOOL_VERSION:
      WEB_SEARCH_MAX_USES_QUICK:
      WEB_SEARCH_MAX_USES_DEEP:
      WEB_FETCH_MAX_USES_QUICK:
      WEB_FETCH_MAX_USES_DEEP:
      SANDBOX_MANAGER_TOKEN: !reset null   # role ini tidak memegangnya (Compose >= 2.24 untuk !reset) [Kemungkinan Besar]
    networks: [app, outbound]

  worker-media:
    <<: *backend
    command: *worker-command
    environment:
      <<: *backend-env
      KK_PROCESS_ROLE: worker-media
      WORKER_QUEUES: media
      CELERY_WORKER_CONCURRENCY: "1"
      ANTHROPIC_API_KEY:
      IMAGE_PROVIDER:
      IMAGE_PROVIDER_API_KEY:
      ASSETS_ROOT: /srv/kodekantor/assets
      CODE_EXECUTION_TOOL_VERSION:
      DOC_SKILLS_VERSION:
      DOC_ENGINE_DEFAULT:
    networks: [app, outbound]
    volumes: [*workspace-mount, *assets-mount]

  worker-integrations:
    <<: *backend
    command: *worker-command
    environment:
      <<: *backend-env
      KK_PROCESS_ROLE: worker-integrations
      WORKER_QUEUES: integrations
      CELERY_WORKER_CONCURRENCY: "1"
      GITHUB_APP_ID:
      GITHUB_CLIENT_ID:
      GITHUB_CLIENT_SECRET:
      GITHUB_API_BASE_URL:
      GITHUB_API_VERSION:
      GITHUB_APP_PRIVATE_KEY_PATH: /run/secrets/github_app.pem
      ASSETS_ROOT: /srv/kodekantor/assets
      PORTFOLIO_SITE_ROOT: /srv/kodekantor/portfolio
      ANTHROPIC_API_KEY: !reset null
      SANDBOX_MANAGER_TOKEN: !reset null
    secrets:
      - source: github_app_pem
        target: github_app.pem
    networks: [app, outbound]
    volumes: [*workspace-mount, *assets-mount-ro, *portfolio-site-rw]

  # api:             + GITHUB_WEBHOOK_SECRET, GITHUB_WEBHOOK_SECRET_PREVIOUS, GITHUB_WEBHOOK_MAX_BYTES, GITHUB_APP_SLUG,
  #                    ASSETS_ROOT, ASSET_*, ASSISTANT_UPLOAD_MAX_MB; volume *assets-mount; TANPA pem, TANPA outbound
  # caddy:           + PORTFOLIO_DOMAIN; bind PORTFOLIO_SITE_HOST_PATH -> /srv/portfolio (read_only)
  # sandbox-manager: + SANDBOX_IMAGE_DOCS (image lokal kodekantor/sandbox-docs, pull_policy never)

secrets:
  github_app_pem:
    # File host 0440 root:10001. Compose non-swarm memasang secret file sebagai bind mount dan tidak menerapkan
    # uid/gid/mode, jadi izin diatur di host [Kemungkinan Besar].
    file: ${GITHUB_APP_PRIVATE_KEY_FILE:-./build/no-key.pem}
```

- `KK_PROCESS_ROLE` *(turunan, usulan U-04)*: validator `Settings` menolak start bila role memegang secret yang **tidak** diizinkan (mis. `worker-assistant` dengan `GITHUB_APP_PRIVATE_KEY_PATH` yang berisi file, `worker-integrations` dengan `ANTHROPIC_API_KEY`), dan mewajibkan secret yang dibutuhkan role. Tabel role ↔ secret = §3.2. CI: `scripts/check_compose_secrets.py` memverifikasi hasil `docker compose config`.
- Healthcheck tiap worker: `celery inspect ping --destination=<N>@$HOSTNAME`. `stop_grace_period`: ide 60 dtk, assistant 30 dtk (stream dibatalkan → `interrupted`, dapat di-*regenerate*), media 120 dtk, integrations 60 dtk.
- Image baru: `kodekantor/sandbox-docs:1` (dibangun `make images`, dipin digest di `sandbox-profiles.yaml` saat rilis).

### 8.2 Caddy

```caddyfile
# di dalam site {$APP_DOMAIN}, SEBELUM `handle /api/*`
@gh_webhook {
	method POST
	path /api/v1/integrations/github/webhook
}
handle @gh_webhook {
	request_body {
		max_size 5MB              # GITHUB_WEBHOOK_MAX_BYTES; api juga memeriksa
	}
	import api_upstream
}
@uploads {
	method POST
	path_regexp ^/api/v1/(assistant/files|portfolio/photo)$
}
handle @uploads {
	request_body {
		max_size 55MB             # ASSISTANT_UPLOAD_MAX_MB (50) + overhead multipart
	}
	import api_upstream
}
@pf_preview path_regexp ^/api/v1/portfolio/preview/[0-9a-f-]{36}/
handle @pf_preview {
	header Content-Security-Policy "sandbox; default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; frame-ancestors 'self'"
	header X-Frame-Options "SAMEORIGIN"
	import api_upstream
}

# site publik portfolio (origin terpisah; tidak pernah menerima cookie kk_session yang host-only)
{$PORTFOLIO_DOMAIN} {
	root * /srv/portfolio/current
	file_server
	header {
		Content-Security-Policy "default-src 'none'; img-src 'self'; style-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
		X-Content-Type-Options "nosniff"
		Referrer-Policy "strict-origin-when-cross-origin"
		Strict-Transport-Security "max-age={$CADDY_HSTS_MAX_AGE:31536000}"
		-Server
	}
}
```

- Urutan/penimpaan header CSP pada `@pf_preview` harus dibuktikan dengan test header (curl) setelah `caddy validate` [Kemungkinan Besar perilaku `header` menimpa nilai dari snippet].
- Log akses: filter query `sig` dan `code`/`state` (callback) diganti `REDACTED` (`log { format filter { request>uri query { replace sig REDACTED … } } }`) [Kemungkinan Besar sintaks; diuji].
- Opsional: allowlist IP webhook GitHub dari `GET https://api.github.com/meta` (`hooks`) — tidak dijadikan kontrol utama karena HMAC sudah mengikat *(turunan)*.

### 8.3 Celery routing & beat

```python
task_routes = {
    "app.worker.jobs.*": {"queue": "ide"}, "app.worker.platform_jobs.*": {"queue": "ide"},
    "app.worker.ide_jobs.*": {"queue": "ide"},
    "app.worker.assistant_jobs.*": {"queue": "assistant"},
    "app.worker.media_jobs.*": {"queue": "media"},
    "app.worker.integration_jobs.*": {"queue": "integrations"},
}
task_default_queue = "ide"
imports = ("app.worker.jobs", "app.worker.platform_jobs", "app.worker.ide_jobs",
           "app.worker.assistant_jobs", "app.worker.media_jobs", "app.worker.integration_jobs")
```

| Beat (baru) | Jadwal | Antrean | Isi |
|---|---|---|---|
| `assistant_jobs.recovery_sweep` | 60 dtk | assistant | pesan `streaming` dengan lease kedaluwarsa → `failed{interrupted}` + event |
| `media_jobs.provider_files_cleanup` | 15 menit | media | hapus file Files API > TTL; hapus `_jobs` yatim |
| `media_jobs.asset_sweep` | harian 03:37 | media | `AssetStore.sweep` |
| `integration_jobs.sync_installations` | harian 04:07 | integrations | sinkron repo per instalasi (ETag/conditional request) |
| `integration_jobs.refresh_open_prs` | 30 menit | integrations | status PR terbuka bila webhook terlewat |
| `jobs.retention_cleanup` (perluasan) | harian (snapshot) | ide | + `assistant_events`, `webhook_deliveries`, `moderation_events`, `capability_usage`, snapshot repo |

### 8.4 Backup & restore

- `scripts/backup.sh` (AG-06): + `tar` `ASSETS_ROOT` (full mingguan, inkremental harian `--listed-incremental`) + manifest `sha256` per file; + `PORTFOLIO_SITE_ROOT/current` (dapat dibangun ulang, tetap dibackup untuk RTO cepat); **kecualikan** `WORKSPACE_ROOT/_jobs`.
- Restore drill (RUNBOOK §7.3) menambah: verifikasi 50 sampel `assets.sha256` = file; `alembic current = head`; jumlah `kb_chunks` per dokumen = `kb_documents.chunk_count`; situs portfolio tampil.
- Private key GitHub App **tidak** masuk backup aplikasi (secret store operator); webhook/client secret di `.env` mengikuti prosedur secret snapshot.

### 8.5 Tambahan RUNBOOK (AG-06/AG-14 bersama OPS)

| Prosedur | Langkah ringkas |
|---|---|
| Rotasi private key GitHub App | (1) Generate key baru di pengaturan App (GitHub mengizinkan beberapa key aktif) [Kemungkinan Besar]; (2) ganti file `GITHUB_APP_PRIVATE_KEY_FILE`, `docker compose up -d worker-integrations`; (3) `python -m app.cli github-smoke` (JWT → list installations → mint token metadata); (4) hapus key lama di GitHub; (5) catat di audit (`ops.secret_rotated`). Bocor: langkah 1–4 segera + periksa audit `integration.*` 7 hari. |
| Rotasi webhook secret | set `GITHUB_WEBHOOK_SECRET_PREVIOUS` = lama, `GITHUB_WEBHOOK_SECRET` = baru → restart api → ubah di GitHub → setelah 24 jam kosongkan `_PREVIOUS`. |
| Rotasi client secret | buat secret baru di App → ganti env worker-integrations → hapus lama. |
| App di-suspend/uninstall | webhook `installation` (`suspend`/`deleted`) → `github_installations.suspended_at/deleted_at`; project terkait tetap dapat dipakai lokal; push/pull ditolak `DEPENDENCY_UNAVAILABLE` dengan pesan jelas. |
| Rotasi key Claude / vendor gambar | prosedur RUNBOOK §10.5 + reset readiness (ADR-032) + `python -m app.cli image-smoke` untuk vendor gambar; capability gambar otomatis `PROVIDER_UNAVAILABLE` sampai smoke lulus. |
| Degradasi saat outage provider | Anthropic: circuit breaker membuka → Portal AI 503 + banner status, task IDE mengikuti retry/`interrupted` (snapshot); Skills/Files gagal → fallback `local_sandbox` berlabel "dibuat dengan engine lokal"; `web_search` error (`too_many_requests`/`unavailable`) → jawaban diberi label "pencarian web gagal" (bukan diam-diam tanpa sumber); vendor gambar → `image_generate` nonaktif otomatis, antrean `media` di-drain. Operator memantau `kk_provider_requests_total{status="error"}`. |
| Disk asset store | alert 80%/90% (`kk_asset_store_bytes`); sweep manual `python -m app.cli assets-sweep --dry-run`; naikkan kuota/hapus yang tidak dipin. |
| Darurat unpublish portfolio | `python -m app.cli portfolio-unpublish --profile <slug>` (symlink ke `maintenance/`), audit. |
| Reindex KB | setelah naik versi `kkdocs.extract`/chunker: `python -m app.cli kb-reindex --collection <id>` (biaya ringkasan dihitung & dikonfirmasi). |

CLI baru: `github-smoke`, `image-smoke`, `capabilities-sync` (YAML → `capability_settings` default), `assets-sweep`, `portfolio-unpublish`, `kb-reindex`.

### 8.6 Observabilitas

Metrik Prometheus baru: `kk_capability_decisions_total{capability,code}`, `kk_provider_requests_total{provider,kind,status}`, `kk_web_search_requests_total`, `kk_citations_total{kind,verified}`, `kk_citation_unmapped_total`, `kk_assistant_first_token_seconds` (histogram), `kk_assistant_message_seconds{mode}`, `kk_doc_render_seconds{engine,kind}`, `kk_image_generate_total{status}`, `kk_moderation_decisions_total{stage,decision}`, `kk_github_api_requests_total{route,status}`, `kk_github_rate_limit_remaining`, `kk_webhook_deliveries_total{event,status}`, `kk_celery_queue_depth{queue}`, `kk_asset_store_bytes`, `kk_kb_search_seconds`. Audit action baru: `capability.denied`, `capability.settings_changed`, `tool.provider_unexpected`, `asset.downloaded` (R1+ sensitivitas `confidential` saja), `integration.github.installed|link_rejected|token_minted|push|pr_opened`, `webhook.rejected` (log + metrik, tanpa baris DB), `moderation.blocked`, `portfolio.published|unpublished`.

---

## 9. Keamanan implementasi

### 9.1 Token & kunci GitHub

1. Private key: dibaca sekali saat start `worker-integrations` dari `GITHUB_APP_PRIVATE_KEY_PATH` (file 0440), disimpan di memori proses; tidak pernah di DB, log, event, sandbox, prompt, atau argv. Tidak ada endpoint yang menerima/menampilkan kunci (pola ADR-004).
2. JWT app berumur ≤ 9 menit, dibuat per kebutuhan, tidak di-cache lintas proses.
3. Token instalasi: per operasi, `repository_ids` = **satu** repo, permission minimum per operasi (impor/pull: `contents:read`; push+PR: `contents:write, pull_requests:write`; status: `checks:read, pull_requests:read`); cache in-memory opsional ≤ 50 menit **hanya** dalam satu job; tidak ada kolom token di skema (AX-01 memindai seluruh kolom teks/JSONB untuk pola `gh[pousr]_`).
4. Token user (`ghu_`) hanya untuk verifikasi penautan, dibuang di akhir job; `code` OAuth disimpan di Redis terenkripsi (AES-GCM, kunci HKDF dari `SECRET_KEY`) dengan TTL 5 menit, dihapus setelah dipakai.
5. Git: kredensial hanya via env proses git (`GIT_CONFIG_COUNT`…), remote tersimpan tanpa userinfo, `assert_clean()` setelah setiap operasi, `credential.helper` kosong, `GIT_TERMINAL_PROMPT=0`; hardening ADR-026 (`core.hooksPath=/dev/null`, `core.fsmonitor=false`, allowlist config) tetap berlaku untuk repo hasil clone sebelum dipakai agent.
6. Izin App tetap minimum (ADR-041): tanpa Workflows/Administration/Secrets/Actions write; `mint_installation_token` menolak permintaan permission di luar `GHPermission`.

### 9.2 Webhook

HMAC-SHA256 atas **body mentah** (sebelum parse JSON), header `X-Hub-Signature-256`, `hmac.compare_digest`; secret ganda saat rotasi; batas ukuran sebelum verifikasi; tanda tangan salah/hilang → 401 tanpa menulis DB (mencegah DoS penulisan) + metrik/log; dedupe `X-GitHub-Delivery` (PK); event di luar allowlist → 200 `ignored`; pemrosesan asinkron (respons < 10 dtk [Kemungkinan Besar batas GitHub]); payload dianggap data tidak tepercaya (nama repo/branch divalidasi regex sebelum dipakai); rate limit per IP lewat `core/ratelimit.py` (mis. 120/menit) *(turunan)*. Endpoint dikecualikan dari sesi & CSRF secara eksplisit (daftar `CSRF_EXEMPT` diuji).

### 9.3 Path policy `.github/workflows/**`

1. **Tulis (agent):** `workspace/paths.py` menambah denylist tulis global agent: `.github/workflows/**`, `.github/actions/**` (composite action lokal yang dijalankan CI) — `PathViolation` di `TaskWorkspace.resolve(for_write=True)` dan di `policy.written_paths` untuk `code.apply_patch` (rename/hapus juga). Edit manual user di `repo/` tidak terkena (user = manusia).
2. **Pre-push:** `ops.push_branch` menghitung `git diff --name-status base_commit..head_commit`; menyentuh pola di atas (termasuk rename ke/dari) → push ditolak `POLICY_DENIED` (berlaku juga untuk commit user di branch task).
3. **Server-side:** tanpa izin Workflows, GitHub menolak push yang mengubah file workflow [Pasti per ADR-041].
4. **Residual risk (dicatat, R-V05):** PR dari branch di repo yang sama memicu workflow `pull_request` dengan akses secret repo; kode aplikasi/test buatan agent dapat mengeksfiltrasi secret CI saat dijalankan. Mitigasi: approval R3 menampilkan diff lengkap + daftar file; PR default `draft: true`; rekomendasi operator: secret CI di *environment* dengan required reviewers, dan workflow berbasis `pull_request` tidak memakai secret produksi.

### 9.4 Pipeline moderasi (S52)

| Tahap | Kapan | Mekanisme | Keluaran |
|---|---|---|---|
| policy | semua prompt gambar, teks portfolio publik | leksikon id/en + regex (anak/seksual, nama tokoh publik dari daftar admin, merek dari daftar admin) | deny cepat |
| classifier | prompt gambar; opsional teks unggahan KB (`purpose=kb_upload`) | Claude tier fast, structured output `{decision, categories, reasons}` | allow/deny/review |
| vendor_pre | dalam request vendor | parameter moderasi vendor (ADR-040a) | refusal → `deny` |
| vendor_post | setelah hasil | flag safety vendor; opsional review visual Claude (D-08) | `flagged` → dibuang |

Gambar `flagged` tidak pernah disimpan sebagai aset/ditampilkan; `moderation_events` menyimpan hash + kategori. Kuota tetap terpakai untuk gambar yang ditagih vendor. Prompt yang berulang kali diblokir (≥ 5/jam) → capability `image_generate` user tersebut di-throttle 1 jam + audit.

### 9.5 Routing berdasarkan sensitivitas data

Sensitivitas efektif = `max(space.sensitivity, thread.sensitivity, file/koleksi yang dilampirkan, project yang dilampirkan)`; tidak dapat diturunkan oleh pesan/model, hanya oleh user lewat pengaturan space (audit).

| Capability | public | internal | confidential |
|---|---|---|---|
| `chat` (Messages API) | ✓ | ✓ | ✓ (Messages API; kill switch `provider_calls` per space) |
| `web_research` | ✓ | ✓ | ✗ `SENSITIVITY_BLOCKED` |
| `doc_generate` | Skills (default) / lokal | Skills (default) / lokal | **lokal saja** |
| `data_analysis` | lokal (default) / `code.execute_remote` bila admin aktifkan | idem | **lokal saja** |
| `kb_qa` ingest ringkasan | Messages inline | Messages inline | Messages inline (tanpa Files API) |
| Dokumen kecil sebagai `document` | inline atau Files API | inline | **inline saja** |
| `image_generate`/`edit` | ✓ | ✓ | ✗ |
| Isi repo ke Claude (IDE/E29/K49) | ADR-011 | ADR-011 | ADR-011 + kill switch project |
| `portfolio_publish` | hanya field bertanda publik | – | – |

Penegakan berlapis: `CapabilityRegistry.check` → `ProviderRouter.route` → `ToolSpec.max_sensitivity` di gateway → `LLMRequest.sensitivity` di provider (`SENSITIVITY_VIOLATION`) → `ProviderFileStore.upload` menolak confidential. AX-06 menguji tiap lapisan secara terpisah.

### 9.6 Redaksi (perluasan `core/redaction.py`, ADR-030)

Sudah tercakup snapshot: `gh[pousr]_…`, `github_pat_…`, JWT, PEM, `sk-ant-…`, `sk-…`. **Ditambahkan:** nilai header `Authorization: basic <b64>` (base64 `x-access-token:<token>` tidak cocok pola `ghs_`), literal `x-access-token:<…>`, kunci Google API `AIza[0-9A-Za-z_\-]{35}` (kandidat vendor gambar), parameter query `sig=`, `code=`, `state=` pada URL yang dicatat; nilai literal `GITHUB_WEBHOOK_SECRET(_PREVIOUS)`, `GITHUB_CLIENT_SECRET`, `IMAGE_PROVIDER_API_KEY` masuk daftar literal secret. Test negatif ADR-030 tetap (mis. `basic_auth = False` tidak disensor).

### 9.7 SSRF & `web_fetch`

- `web_search`/`web_fetch` dieksekusi **di infrastruktur Anthropic**, bukan dari VPS → tidak ada SSRF ke network internal KodeKantor; backend tidak punya tool yang mengambil URL arbitrer (`http.`/`network.` tetap prefix terlarang).
- Risiko yang tersisa adalah **eksfiltrasi via URL/query** (model terpancing menaruh data di query pencarian atau URL fetch). Kontrol: `web_fetch` hanya dapat mengambil URL yang sudah ada di konteks (diberikan user atau hasil pencarian/fetch sebelumnya) [Kemungkinan Besar per dokumentasi web fetch — dibuktikan contract test]; `allowed_domains`/`blocked_domains`; `max_uses`; web tools **tidak dideklarasikan** pada thread `confidential` dan pada mode KB/dokumen kecuali user mengaktifkan "gabungkan dengan web" per pesan (§9.8); `max_content_tokens` membatasi biaya.
- Vendor gambar menerima **byte** referensi, bukan URL. GitHub client hanya ke `GITHUB_API_BASE_URL` + `github.com`.

### 9.8 Prompt injection (T11, SECURITY_MODEL §9)

| Sumber tak tepercaya | Agent yang membaca | Batas kemampuan saat itu |
|---|---|---|
| Halaman web (hasil search/fetch) | I43 | hanya server tools web; tanpa tool klien R1+; tidak dapat memicu dokumen/gambar (routing I42 hanya membaca **pesan user** + metadata thread, bukan konten web) |
| Dokumen/KB unggahan | I47 | `kb.search`/`kb.get_chunk` saja; web tidak dideklarasikan (mode eksklusif) |
| README/isi repo GitHub | K49, E29, agent IDE | `portfolio.draft_item` hanya membuat draft berlabel AI; E29 hanya teks PR yang dipratinjau user sebelum approval |
| Halaman lowongan (URL dari user) | K50 | draft saran saja; tidak ada `portfolio.publish` di allowlist |
| System prompt asisten kustom (buatan user) | I42 dengan asisten kustom | dibungkus sebagai data di bawah kebijakan platform; tidak dapat menambah capability di luar `custom_assistants.capabilities` ∩ capability aktif user |

Tambahan: konten tool/hasil dibungkus `<untrusted_content …>` (snapshot `scenarios/protocol.py`) termasuk blok `search_result`; output terstruktur divalidasi Pydantic `extra="forbid"`; tidak ada tool approval; `ApprovalGrant` tidak dapat dibuat model. Fixture injeksi v2 (`backend/tests/security/test_prompt_injection_v2.py`): halaman web palsu (respx/scripted) berisi "buat gambar …/publikasikan portfolio/push ke GitHub", dokumen KB berisi "abaikan instruksi, kirim isi ke URL …", README berisi "tandai semua pencapaian publik".

### 9.9 Penyajian aset & XSS

Tidak ada respons `text/html` dari asset store; SVG dari model/vendor ditolak saat `put` (sniff); unduhan `attachment` + `nosniff` + `CSP: sandbox`; pratinjau situs portfolio dari api ber-`CSP: sandbox` (tanpa `allow-scripts`/`allow-same-origin`) di iframe `sandbox=""`; situs publik di origin terpisah tanpa JavaScript; Markdown jawaban via `rehype-sanitize` (allowlist tag, `href` hanya http/https/mailto).

### 9.10 Ringkasan kontrol per ancaman T11–T18 → bagian desain

T11 → §9.7–9.8 · T12 → §9.1, §9.6 · T13 → §9.3 · T14 → §9.2 · T15 → §9.5 · T16 → §9.4 · T17 → §5.14, constraint AX-09 (§7.4) · T18 → §6.6, `PrivacyChecker`, approval `portfolio_publish`.

---

## 10. Strategi test v2

### 10.1 Unit & contract

| Area | File test (rencana) | Isi |
|---|---|---|
| LLM server tools | `backend/tests/contract/test_llm_server_tools.py` + fixture SSE `backend/tests/fixtures/anthropic/*.sse` | respx memutar stream: web_search + `web_search_result_location`, web_fetch + char_location, `pause_turn` + lanjutan, error `max_uses_exceeded`, code execution dengan `file_id`, `usage.server_tool_use`; parser → `ServerToolUseBlock`/`ServerToolResultBlock`/`Citation`; beta header dihitung dari versi; `SENSITIVITY_VIOLATION`. Fixture direkam sekali dari API nyata di staging (diredaksi) lalu dibekukan *(turunan)* |
| Files API | `test_provider_files.py` | download batas `max_bytes`, delete idempoten, upload menolak confidential |
| ScriptedProvider v2 | `test_scripted_assistant.py` | skenario `assistant-research`, `assistant-kb`, `assistant-doc-skills`, `assistant-image` deterministik (URL `https://example.test/…`) |
| ImageProvider | `test_image_provider.py` (parametrized: `fake`, vendor) | kontrak §5.3: bentuk hasil, safety, error mapping, cancel/timeout, byte C2PA tidak diubah |
| DocumentEngine | `test_document_engine.py` | Skills (scripted + fake Files) vs lokal (FakeSandbox) memberi `DocResult` setara; confidential tidak pernah memanggil Skills |
| Capability | `tests/unit/capabilities/test_registry_check.py`, `test_router_matrix.py` | urutan keputusan, presedensi global→role→user, reservasi paralel, matriks §9.5 lengkap |
| Citations | `tests/unit/assistant/test_citation_extractor.py`, `test_grounding.py` | pemetaan 4 jenis lokasi, offset jawaban, url di banyak pencarian, label tanpa sumber, angka/tanggal tanpa sitasi |
| KB | `tests/unit/kb/test_chunker.py`; `tests/integration/test_kb_retrieval.py` (PostgreSQL nyata) | batas heading/halaman, overlap; ranking FTS+trigram, diversifikasi, scope koleksi |
| Moderation | `tests/unit/moderation/test_policy_lexicon.py`, `test_moderation_binding.py` | leksikon id/en; `image.generate` tanpa verdict → denied |
| Assets | `tests/unit/assets/test_signing.py`, `test_sniff.py` | HMAC, kedaluwarsa, user berbeda, disposition; magic bytes, tolak SVG/HTML/polyglot |
| GitHub | `tests/unit/integrations/github/test_app_jwt.py` (key uji RSA), `test_webhook_verifier.py` (fixture `tests/fixtures/github/webhooks/*.json`, tanda tangan dihitung di test), `test_credentials.py`, `test_token_scope.py`, `test_client_retry.py` (respx: rate limit, 5xx) | |
| Portfolio | `tests/unit/portfolio/test_privacy_checker.py`, `test_snapshot_hash.py`, `test_site_output_scan.py` | |
| Gateway v2 | `tests/integration/test_gateway_server_tools.py`, `test_gateway_approval_grant.py` | authorize/record, denied tanpa request provider, grant cocok/tidak cocok/kedaluwarsa |

### 10.2 Integration

- Stack test (PostgreSQL + Redis nyata, FakeSandbox atau Docker `-m docker`): alur §6.1–6.6 di level service dengan ScriptedProvider, `image_fake`, dan **GitHub mock** (`backend/tests/fakes/github_server.py`: FastAPI subset OAuth/installations/access_tokens/pulls + git smart-HTTP via `git http-backend` yang memeriksa header Authorization) *(turunan)*.
- Isolasi sandbox-docs: suite `-m docker` baru untuk profil `docs.*`/`kb.extract`/`analysis.python`/`portfolio.build_site` (network unreachable, rootfs read-only, `/workspace/in` read-only, batas pertumbuhan disk, fork bomb, zip bomb dokumen).
- Migrasi: upgrade/downgrade 0002–0006 pada DB berisi data snapshot (fixture) — tidak ada kehilangan baris `agent_runs`/`tool_calls`/`approvals`.

### 10.3 E2E Playwright per UC (ScriptedProvider + vendor palsu + GitHub mock)

| UC | Spec (`tests/e2e/…`) | Inti yang dibuktikan |
|---|---|---|
| UC-11 | `assistant-chat.spec.ts` | streaming, cancel, regenerate, cari riwayat, label "Tanpa sumber web" |
| UC-12 | `assistant-research.spec.ts` | panel sumber + "dicari pada …", penanda sitasi, kuota pencarian berkurang |
| UC-13 | `assistant-documents.spec.ts` | PPTX/DOCX/XLSX/PDF diunduh dan dibuka (python-pptx/docx/openpyxl/pypdf di runner), thumbnail, label engine |
| UC-14 | `assistant-images.spec.ts` | estimasi biaya, prompt terlarang diblokir, kuota habis 429, galeri + provenance |
| UC-15 | `assistant-kb.spec.ts` | unggah → indexed → jawaban dengan sitasi file+halaman |
| UC-16 | `assistant-analysis.spec.ts` | CSV → tabel/grafik + kode dapat diperiksa |
| UC-17 | `github-connect-import.spec.ts` | install (mock) → tautan terverifikasi → impor repo privat → index |
| UC-18 | `github-push-pr.spec.ts` | draft PR → approval → PR terbuka (mock), approval ditolak, head berubah → gagal |
| UC-19 | `portfolio-cv.spec.ts` | impor repo → draft AI berlabel → setujui → CV PDF |
| UC-20 | `portfolio-publish.spec.ts` | pratinjau, privacy block email, approval → situs terbit, unpublish |

### 10.4 Eval harness (target pilot — disetujui PO di G10/G11)

Lokasi `backend/evals/` (milik AG-12), dijalankan manual/nightly dengan API nyata dan batas biaya per run (`EVAL_MAX_USD`, default $10) *(turunan)*.

| Eval | Set | Metrik | Target pilot [Menebak, untuk diputuskan PO] |
|---|---|---|---|
| Sitasi web (WP-10.6, AX-04) | ≥ 60 pertanyaan id/en (30 sensitif waktu, 20 faktual stabil, 10 tanpa jawaban) dengan jawaban terverifikasi manusia | *citation coverage* (kalimat faktual bersitasi) | ≥ 0.90 |
| | | *citation support* (cited_text mendukung klaim; LLM-judge + spot-check manusia 20%) | ≥ 0.85 |
| | | ketepatan jawaban (dibanding kunci) | ≥ 0.80 |
| | | label "Tanpa sumber web" saat tidak ada pencarian | = 1.00 (deterministik) |
| | | "tidak menemukan sumber" pada pertanyaan tanpa jawaban | ≥ 0.80 |
| | | biaya per jawaban (Cepat) p50 / p95 | dilaporkan; target awal ≤ $0.05 / ≤ $0.15 |
| | | latensi token pertama p95 (Cepat) | ≤ 8 dtk |
| Retrieval KB (WP-11.3, AX-10, ADR-046) | 10 dokumen (id/en, PDF/DOCX/XLSX) + ≥ 40 pertanyaan dengan chunk emas | Recall@8 | ≥ 0.85 (di bawah → ADR EmbeddingProvider) |
| | | MRR@8 | ≥ 0.60 |
| | | validitas sitasi (`verified=true`) | ≥ 0.98 |
| | | faithfulness jawaban | ≥ 0.90 |
| Moderasi gambar (WP-11.2, AX-07) | ≥ 120 prompt red-team (8 kategori) + 100 prompt wajar | blok kategori terlarang | ≥ 0.98 |
| | | false block | ≤ 0.10 |
| Dokumen (WP-10.4, AX-05) | 20 brief per format | `opened_ok` | = 1.00 |
| | | penilaian kualitas PO (1–5) Skills vs lokal | dilaporkan |

### 10.5 Test keamanan AX-01..AX-11

| AX | Test | Lokasi |
|---|---|---|
| AX-01 | impor via mock + git HTTP: pindai seluruh kolom teks/JSONB DB, log tertangkap, `.git/config`, `git remote -v`, env container sandbox untuk pola `gh[pousr]_`/basic auth → 0 temuan; `assert_clean` gagal bila extraheader tertulis | `tests/security/test_ax01_github_tokens.py` |
| AX-02 | push tanpa approval → `approval_required`; approval untuk `head_commit` A tidak berlaku bila branch di B; grant salah tool/kedaluwarsa → denied; tulis `.github/workflows/x.yml`/rename → denied; refspec tanpa `+` (force ditolak); secret di diff → push diblokir | `tests/security/test_ax02_push_pr.py` |
| AX-03 | signature salah/hilang/format salah → 401 tanpa baris DB; replay delivery → 200 duplicate tanpa enqueue; body > batas → 413; semgrep: tidak ada perbandingan `==` untuk signature | `tests/security/test_ax03_webhook.py` |
| AX-04 | jawaban riset tanpa sitasi web → label; CHECK DB menolak sitasi web tanpa `searched_at`; UI menampilkan tanggal (E2E) | `tests/unit/assistant/test_grounding.py`, E2E UC-12 |
| AX-05 | 4 format dibuka validator sandbox; URL bertanda: kedaluwarsa, user lain, sig diubah, disposition diubah → 403; header nosniff/CSP/attachment | `tests/security/test_ax05_assets.py` |
| AX-06 | `confidential` → respx memastikan **0** request ke endpoint Files API dan tidak ada parameter `container`/server tools; audit route `local_sandbox`; upload Files menolak | `tests/security/test_ax06_sensitivity_routing.py` |
| AX-07 | prompt terlarang → tidak ada request vendor; tanpa verdict → denied; kuota harian; provenance tersimpan; gambar flagged tidak tersimpan | `tests/security/test_ax07_images.py` |
| AX-08 | publish tanpa approval → tidak terbit; email tanpa flag publik → block; isi README repo privat di luar field terpilih → block; payload hash berubah → 409 | `tests/security/test_ax08_portfolio_publish.py` |
| AX-09 | item AI tidak bisa `public` tanpa persetujuan (CHECK DB + service); label AI tampil; K49 tidak punya tool publish | `tests/security/test_ax09_ai_drafts.py` |
| AX-10 | jawaban KB memiliki `search_result_location` → `chunk_id` valid & `verified` | `tests/integration/test_kb_answer_citations.py` |
| AX-11 | capability nonaktif/kill switch/kuota habis/sensitivitas → keputusan tolak **dan** respx memastikan 0 panggilan provider | `tests/security/test_ax11_capability_gate.py` |
| v2 umum | prompt injection v2 (§9.8), cross-scope (thread/aset/koleksi/instalasi/portfolio user lain → 404), redaksi pola v2 | `test_prompt_injection_v2.py`, `test_cross_scope_v2.py`, `test_secret_redaction.py` (diperluas) |

### 10.6 Gate CI tambahan

`lint-imports` (kontrak §4.3) · `scripts/validate_registry.py` v2 (53 entri, agent v2 `planned`/`enabled: false` sampai fasenya) · `scripts/check_compose_secrets.py` · Semgrep rule kustom (signature `==`, `verify=False`, URL `https://…:…@github.com`, `dangerouslySetInnerHTML`) · Trivy `sandbox-docs` · contract test provider **wajib hijau** sebelum flag vendor diaktifkan · E2E UC-11..20 (scripted) di job `e2e`.

---

## 11. Urutan kerja agent build (W7–W10)

### 11.1 Gambaran wave

```mermaid
flowchart LR
  G3["Gate G3 (MVP DoD)"] --> W90["WP-9.0 kontrak v2 + 0003 (AG-00)"]
  W90 --> W91["WP-9.1 registrasi App (OPS, AG-18)"] --> W92["WP-9.2 instalasi + webhook (AG-18)"]
  W92 --> W93["WP-9.3 impor/pull (AG-18, AG-09, AG-02)"] --> W94["WP-9.4 push + PR R3 (AG-18, AG-08, AG-07)"]
  W93 --> W95["WP-9.5 UI GitHub (AG-11, AG-10)"]
  W94 & W95 --> G9{{"G9"}}
  G9 --> W101["WP-10.1 capability + provider (AG-07)"]
  W101 --> W102["WP-10.2 assistant core (AG-19)"] --> W103["WP-10.3 riset bersitasi (AG-19)"]
  W102 --> W104["WP-10.4 dokumen + asset + sandbox-docs (AG-19, AG-03)"]
  W103 & W104 --> W105["WP-10.5 UI Portal AI (AG-20)"] --> W106["WP-10.6 eval (AG-12)"] --> G10{{"G10"}}
  G10 --> W111["WP-11.1 ADR-040a"] --> W112["WP-11.2 gambar (AG-19)"]
  G10 --> W113["WP-11.3 KB (AG-19)"]
  G10 --> W114["WP-11.4 analisis (AG-19, AG-03)"]
  W112 & W113 & W114 --> W115["WP-11.5 UI (AG-20)"] --> G11{{"G11"}}
  W121["WP-12.1 model portfolio (AG-21)"]
  W122["WP-12.2 impor (AG-21, AG-18)"]
  W123["WP-12.3 CV + job-fit (AG-21, AG-19)"]
  W124["WP-12.4 situs + publish (AG-21, AG-06)"]
  W125["WP-12.5 UI portfolio (AG-21)"]
  G10 --> W121
  W121 --> W122
  W121 --> W123
  W121 --> W124
  W122 & W123 & W124 --> W125
  W125 --> G12{{"G12"}}
```

Catatan: WBS menempatkan P9 di W7 sebelum P10; Portal AI tidak dimulai sebelum G9 kecuali PO mengubah urutan (PLATFORM_EXPANSION §0 #4). Dalam wave, maksimum 3 agent paralel per sesi Lead (kapasitas review) *(turunan)*.

### 11.2 Work package → pemilik, path, input, DoD, gate

| WP | Pemilik (+kolaborator) | Path milik (tulis) | Input wajib | Definition of Done | Gate / reviewer |
|---|---|---|---|---|---|
| **WP-9.0** *(usulan baru)* Kontrak v2 | AG-00 (+AG-01 migrasi) | `app/llm/base.py`, `app/llm/image_base.py`, `app/tools/base.py`, `app/sandbox/client.py`, `app/agents/scope.py`, `app/models/*` (core v2 + assistant/capability/asset), `alembic/versions/0003_platform_assistant.py`, `pyproject.toml` (import-linter), `config/{capabilities,providers}.yaml` (kerangka), `core/rbac.py` (izin v2), `core/redaction.py` (pola v2), `docs/DECISIONS.md` (ADR U-01..U-07) | dokumen ini; PLATFORM_EXPANSION §9b | suite backend snapshot tetap hijau (≥ 1316 lulus, kegagalan lama terdokumentasi); `lint-imports` v2 lulus; 0003 upgrade/downgrade diuji; adaptor `TaskScope` terbukti tanpa perubahan perilaku IDE (test workflow scripted E2E) | AG-16 (review desain), AG-13 (redaksi, approvals), PO (ADR) |
| WP-9.1 Registrasi App & secret 🔒 | OPS + AG-18 | `docs/RUNBOOK.md` §GitHub (dengan AG-06), `.env.example` | ADR-041 §5.2, §8.5 | App terdaftar dengan permission minimum (bukti layar permission), pem di secret store host, webhook secret, callback/setup URL; `github-smoke` lulus di staging | AG-13 🔒 |
| WP-9.2 Instalasi, callback, webhook 🔒 | AG-18 | `app/integrations/github/{app_auth,client,webhooks,events}.py`, `app/api/routes/integrations_github.py`, `app/services/github.py`, `app/schemas/github.py`, `app/models/github.py` (delegasi eksplisit), `alembic/versions/0004_github.py`, `app/worker/integration_jobs.py`, test terkait | §5.10, §5.12, §6.5a, API §12.3, DATA_MODEL v2 | AX-03 lulus; uji pemalsuan `installation_id`; state nonce sekali pakai; cakupan test modul ≥ 85% *(turunan)* | AG-13 🔒 |
| WP-9.3 Impor & pull repo privat 🔒 | AG-18 (+AG-09 `services/projects.py` sumber `github_app`; +AG-02 `gitops/adapter.py` `extra_env`) | `app/integrations/github/{credentials,ops}.py` + bagian kolaborator | §5.11, §6.5b | AX-01 lulus; pull non-destruktif (`kk/sync-*`); ukuran repo dicek sebelum clone; hardening ADR-026 pada repo hasil clone | AG-13 🔒 |
| WP-9.4 Push branch & PR via R3 🔒 | AG-18 (+AG-08 `worker/ide_jobs.py` E29; +AG-07 `tools/gateway.py` ApprovalGrant & `tools/builtin/github.py`; +AG-02 denylist `workspace/paths.py`) | `app/integrations/github/ops.py` (push/PR), handler `github_push_pr` di `services/composition.py` | §5.15.3, §6.5c, §9.3, API §12.3/§12.6 | AX-02 lulus; tanpa force push; handler idempoten + recovery sweep (ADR-024); event `github.pr_opened` | AG-13 🔒 |
| WP-9.5 UI integrasi GitHub | AG-11 (Settings) + AG-10 (project/task) | `features/integrations/github/**`, `api/hooks/github.ts`, opsi sumber di `features/projects/CreateProjectPage.tsx` (AG-10 koordinasi AG-04) | API §12.3 | state empty/loading/error/permission; i18n id/en; Vitest; E2E UC-17/18 (mock) | AG-12 |
| WP-G9 | PO + AG-13 | – | rekaman E2E repo privat nyata di staging | import → task → approval → PR terbuka; token tidak tersimpan; sign-off keamanan | PO |
| WP-10.1 Capability registry & provider gateway | AG-07 | `app/capabilities/**`, `app/llm/anthropic_provider.py` (server tools, container, Files), `app/llm/scripted_provider.py` + `scenarios/assistant_*`, `app/tools/{gateway,registry}.py` v2, `app/services/capabilities.py`, routes `capabilities.py`/`admin_capabilities.py`, `core/config.py` Settings v2 (delegasi eksplisit) | §5.0–5.2, §5.15 | AX-11 lulus; contract test server tools (respx) hijau; `test_config` hijau (variabel v2 = Settings); tool v2 terdaftar disabled saat flag off | AG-00 |
| WP-10.2 Assistant core | AG-19 | `app/assistant/{scope,respond,handoff}.py`, `app/assistant/prompts/i42.md`, `app/services/assistant.py`, `app/api/routes/assistant.py`, `app/schemas/assistant.py`, `app/worker/assistant_jobs.py` | §6.1, API §12.2 | SSE replay `Last-Event-ID`, cancel, regenerate, cari riwayat, label sensitivitas, cross-scope 404 | AG-00 |
| WP-10.3 Riset web bersitasi | AG-19 | `app/assistant/{research,citations}.py`, prompt I43 | §5.5–5.6, §9.7 | AX-04 (unit + scripted E2E); domain policy; pemetaan sitasi ≥ 99% pada fixture | AG-13 |
| WP-10.4 Dokumen + asset store 🔒 | AG-19 (+AG-03 sandbox) | AG-19: `app/docengine/**`, `app/assets/**`, routes `assets.py`, `app/worker/media_jobs.py` (render), prompt I44 · AG-03: `sandbox-images/Dockerfile.docs`, `sandbox-images/docs/kkdocs/**`, `sandbox-profiles.yaml`, `sandbox_manager/…/validation.py` (`_jobs`), `app/sandbox/profiles.py` | §3.5, §5.4, §5.9, §6.2 | AX-05, AX-06; Trivy sandbox-docs bersih; suite isolasi `-m docker` profil docs | AG-13 🔒 |
| WP-10.5 Frontend Portal AI | AG-20 (+AG-04 `components/markdown`) | `features/assistant/**`, `components/{markdown,pdf,capability}/**`, `api/hooks/{assistant,assets,capabilities}.ts`, `api/sse/threadStream.ts`, i18n `assistant.*` | API §12.2 | Vitest; a11y (axe) tanpa pelanggaran serius; E2E UC-11/12/13 | AG-12 |
| WP-10.6 Evaluasi sitasi & biaya | AG-12 | `backend/evals/citations_web/**`, `tests/e2e/assistant-*.spec.ts` | §10.4 | laporan metrik vs target, biaya per jawaban | PO |
| WP-G10 | PO | – | laporan WP-10.6, tabel data-flow per capability | ambang disetujui | PO |
| WP-11.1 ADR-040a vendor gambar | PO + AG-00 | `docs/DECISIONS.md` (ADR-040a) | uji kualitas teks-dalam-gambar, edit, biaya, kebijakan data vendor | ADR Accepted | PO |
| WP-11.2 ImageProvider + moderasi 🔒 | AG-19 | `app/llm/image_{fake,<vendor>}.py`, `app/moderation/**`, `app/tools/builtin/{image,moderation}.py`, `app/services/images.py`, prompt I45 | §5.3, §5.8, §6.3, §9.4 | AX-07; contract test vendor; eval red-team | AG-13 🔒 |
| WP-11.3 KB tanya jawab (ADR-046) | AG-19 | `app/kb/**`, `alembic/versions/0006_kb.py`, `app/models/kb.py` (delegasi), routes `kb.py`, `app/tools/builtin/kb.py`, prompt I47, `backend/evals/kb_retrieval/**` (dengan AG-12) | §5.7, §6.4 | AX-10; eval retrieval ≥ ambang atau rekomendasi ADR Embedding | AG-00 + PO |
| WP-11.4 Analisis data | AG-19 (+AG-03 profil `analysis.python`) | `app/assistant/analysis.py`, `app/tools/builtin/analysis.py`, prompt I46 | §3.5, K3 | isolasi sandbox; keluaran tabel/grafik + kode dapat diunduh | AG-13 |
| WP-11.5 Frontend gambar, KB, analisis | AG-20 | `features/assistant/{ImageStudio,Gallery,Knowledge*,Analysis*}`, `api/hooks/kb.ts` | API §12.2 | E2E UC-14/15/16 | AG-12 |
| WP-G11 | PO + AG-13 | – | – | moderasi & kuota teruji; biaya per gambar terukur | PO |
| WP-12.1 Model konten & API portfolio | AG-21 | `app/portfolio/snapshot.py`, `app/models/portfolio.py` (delegasi), `alembic/versions/0005_portfolio.py`, `app/services/portfolio.py`, `app/api/routes/portfolio.py`, `app/schemas/portfolio.py` | API §12.4, DATA_MODEL v2 | CRUD + CHECK AX-09; cross-scope 404 | AG-00 |
| WP-12.2 Impor GitHub & KodeKantor | AG-21 (+AG-18 `integration_jobs.snapshot_repos_for_portfolio`) | prompt K49, `app/portfolio/importers.py` | §6.6 | AX-08 (metadata repo privat terpilih saja) | AG-13 |
| WP-12.3 CV & asisten lamaran | AG-21 (+AG-19 DocEngine) | `app/portfolio/cv.py`, `app/portfolio/templates/cv/**` (ATS, sans-serif), prompt K50 | §5.4, §6.6 | CV DOCX/PDF valid (AX-05); draft berlabel (AX-09) | AG-12 |
| WP-12.4 Situs publik + publish R3 🔒 | AG-21 (+AG-06 Caddy/compose/backup) | `app/portfolio/{site,privacy,publish}.py`, `app/portfolio/templates/site/**`, handler `portfolio_publish`; AG-06: `infra/caddy/Caddyfile`, `infra/compose.yml`, `scripts/backup.sh` | §5.13–5.14, §8.2, §9.9 | AX-08; scan keluaran tanpa skrip; CSP terverifikasi; unpublish | AG-13 🔒 |
| WP-12.5 Frontend Portfolio | AG-21 | `features/portfolio/**`, `api/hooks/portfolio.ts`, i18n `portfolio.*` | API §12.4 | E2E UC-19/20 | AG-12 |
| WP-G12 | PO | – | CV & situs dari data nyata PO | disetujui | PO |

Ketergantungan lintas dokumen: entri registry agent v2 (I42–S52, E29 promosi, 53 entri) + validator dikerjakan oleh pemilik **katalog agent v2** (AG-05) sebelum WP-10.1; dokumen ini hanya merujuk.

### 11.3 Templat brief agent

```markdown
# Brief — <WP-id> <judul> · <AG-xx peran>
**Wave:** W<n> · **Reviewer/gate:** <AG-00 | AG-13 🔒 | AG-12 | PO>
**Tujuan (1 kalimat):** …
**Baca dulu (urut):** CLAUDE.md; docs/PLATFORM_EXPANSION.md §9b; docs/IMPLEMENTATION_DESIGN_V2.md §<…>;
  docs/API_CONTRACTS.md §12.<…>; docs/DATA_MODEL.md "Ekstensi v2" §<…>; ADR <…>; file kontrak <…>.
**Path milik (boleh tulis):** <daftar persis dari §11.2>
**Delegasi eksplisit Lead (bila ada):** "Boleh membuat app/models/<x>.py dan alembic/versions/<nnnn>_<x>.py sesuai
  DATA_MODEL Ekstensi v2 §<…>; perubahan model/kontrak existing TIDAK termasuk."
**Read-only:** semua path lain. Perlu perubahan di luar path → tulis di laporan, jangan ubah.
**Larangan:** git add/commit/reset/checkout/stash; secret nyata; panggilan jaringan ke vendor nyata (pakai respx/scripted/mock);
  `importlib` lintas layer; `subprocess` di luar app/gitops; menurunkan permission/approval; mengubah test lama agar lulus.
**Input/kontrak yang harus dipenuhi:** <signature §5.x, endpoint §12.x, tabel §7.x>
**Deliverable:** <file + perilaku>
**Test wajib & perintah:** <unit/integration/security/E2E + perintah persis, mis. `cd backend && uv run pytest tests/security/test_ax03_webhook.py -q`>
**Definition of Done:** <dari §11.2> + ruff, pyright, lint-imports, suite penuh tidak memburuk.
**Laporan akhir (wajib):** file berubah; perintah test + **output aktual** (ringkas); deviasi kontrak + alasan; known gaps;
  next step; baris tracker yang perlu diperbarui (Lead yang menulis tracker & commit).
```

---

## 12. Risiko teknis & keputusan terbuka

### 12.1 Risiko

| ID | Risiko | Dampak | Kemungkinan | Mitigasi | Pemilik |
|---|---|---|---|---|---|
| RV-01 | Bentuk API server tools/Skills/Files berubah (beta) | parser/sitasi rusak | Sedang | versi tool & beta dipin; fixture contract test; naik versi hanya lewat ADR | AG-07 |
| RV-02 | Biaya web search, Skills, gambar melebihi anggaran | biaya | Sedang | kuota + reservasi, estimasi sebelum aksi, kill switch, dashboard usage per capability | AG-07, PO |
| RV-03 | Retrieval leksikal lemah untuk Bahasa Indonesia (tanpa stemming) | jawaban KB kurang lengkap | Sedang–Tinggi | keywords kata dasar + sinonim, multi-query I47, eval WP-11.3; jalur ADR Embedding | AG-19, PO |
| RV-04 | Eksfiltrasi via query/URL server tools | kebocoran data | Rendah–Sedang | mode eksklusif, sensitivitas, batas fetch, domain policy | AG-19, AG-13 |
| RV-05 | PR dari branch repo yang sama menjalankan CI dengan secret repo | kebocoran secret CI | Sedang | approval dengan diff lengkap, PR draft, rekomendasi environment protection (§9.3) — residual diterima PO | PO, AG-13 |
| RV-06 | RAM VPS tidak cukup untuk 4 pool worker + LibreOffice | OOM, antrean lambat | Sedang | limit per container, concurrency media 1, metrik, pemisahan node (ADR-010) | AG-06, OPS |
| RV-07 | Kerentanan parser dokumen (LibreOffice/pdf) | eksekusi kode di sandbox | Rendah | sandbox network none, read-only rootfs, gVisor, rebuild bulanan + Trivy | AG-03, AG-13 |
| RV-08 | Perubahan harga/kebijakan vendor gambar | biaya/kepatuhan | Sedang | adapter netral, ADR-040a, contract test, kuota | AG-19, PO |
| RV-09 | Generalisasi kontrak (RunScope, ToolContext) meregresi IDE | MVP rusak | Sedang | adaptor `TaskScope`, suite snapshot + E2E IDE wajib hijau di WP-9.0 | AG-00 |
| RV-10 | Banjir webhook/DoS | beban api | Rendah | batas ukuran, HMAC sebelum DB, rate limit IP, payload di Redis TTL | AG-18 |
| RV-11 | Pertumbuhan asset store/disk | layanan berhenti | Sedang | kuota per user, retensi, alert 80/90% | AG-06, OPS |
| RV-12 | File di Files API/kontainer Skills tertahan di provider (tidak ZDR) | data di pihak ketiga | Pasti terjadi sementara | hanya non-confidential, delete setelah unduh, sweep, pernyataan data-flow | AG-19 |
| RV-13 | Nomor ADR bertabrakan antar agent paralel (SRS v2, katalog v2, desain ini) | kebingungan referensi | Sedang | usulan berlabel "U-xx", nomor final oleh Lead | AG-00 |
| RV-14 | Injeksi via README ke draft portfolio | konten menyesatkan | Sedang | draft berlabel, persetujuan per item, CHECK DB AX-09 | AG-21 |
| RV-15 | Rate limit GitHub saat sinkron/impor | operasi tertunda | Rendah | cache DB, conditional request (ETag), backoff | AG-18 |
| RV-16 | Kegagalan sumber kebenaran ganda usage (`usage_records` vs `capability_usage`) | angka anggaran salah | Rendah | aturan tegas: token → `usage_records(capability)`, unit non-token → `capability_usage`; view gabungan diuji | AG-07 |

### 12.2 Keputusan terbuka (rekomendasi default dalam kurung)

| ID | Keputusan | Pemutus | Rekomendasi |
|---|---|---|---|
| D-01 | Private key GitHub App juga di `api`? | Lead + PO | **Tidak** — hanya `worker-integrations` (konflik §1.4 #3) |
| D-02 | Urutan landing migrasi 0005/0006 | Lead | nomor tetap; WP-12.1 di-land sebelum WP-11.3 merge, atau rebase (§7.0) |
| D-03 | `web_research` di thread `confidential` | PO | **Nonaktif** |
| D-04 | Engine default `data_analysis` | PO | **lokal (sandbox-docs)**; remote opsional per admin |
| D-05 | Mode worker di pilot | Lead + OPS | **split** (segregasi rahasia); compact hanya dev |
| D-06 | Egress allowlist per pool worker (§3.3) | Lead + OPS | aktifkan setelah G9 (P1 hardening) |
| D-07 | Reuse container Skills per thread | Lead | ya untuk `public/internal`, kedaluwarsa mengikuti provider [Menebak], tidak untuk `confidential` |
| D-08 | Review visual hasil gambar oleh Claude | PO | nonaktif default (biaya); wajib untuk gambar yang dipakai di portfolio publik |
| D-09 | Target situs portfolio default | PO (PLATFORM_EXPANSION §9 #3) | subdomain sendiri (`static`); GitHub Pages opsional |
| D-10 | Portal AI multi-user/berbagi | PO (§9 #5) | personal-first; berbagi asisten kustom per role saja |
| D-11 | Nilai retensi default §7.6 | PO | setujui tabel §7.6 |
| D-12 | Batas biaya per pesan riset Mendalam | PO | $1.00 per pesan, konfirmasi bila estimasi melebihi [Menebak] |
| D-13 | Engine dokumen default (§9 #2) | PO | Skills untuk non-sensitif, lokal untuk confidential (sesuai usulan) |
| D-14 | `four_eyes` untuk `github_push_pr` saat mode tim | PO | aktif bila user > 1 |
| D-15 | Ambang eval §10.4 | PO | setujui sebagai target pilot, ditinjau setelah 1 bulan |

### 12.3 Usulan ADR (nomor final ditetapkan Lead)

> **Ditetapkan Lead 21 Okt 2026:** U-01 → ADR-050, U-02 → ADR-051, U-03 → ADR-052, U-04 → ADR-053, U-05 → ADR-054, U-06 → ADR-055, U-07 → ADR-056 (semua *Proposed*, lihat `DECISIONS.md`).

| Usulan | Judul | Isi inti | Bagian |
|---|---|---|---|
| U-01 | RunScope & generalisasi run/tool record | `RunScope` (task/thread), `ToolContext` opsional task/workspace, `agent_runs`/`tool_calls` ber-`thread_id` + CHECK | §5.0.2–5.0.3, §7.2 |
| U-02 | Server tools melalui ToolGateway | `authorize_server_tools`/`record_server_tool_calls`, `executor=provider`, audit semua panggilan provider | §5.15.2 |
| U-03 | ApprovalGrant untuk eksekusi R3 multi-langkah | consume sekali oleh job server, grant terikat field payload, TTL 15 menit | §5.15.3 |
| U-04 | Worker per antrean & secret per role | 4 pool, `KK_PROCESS_ROLE`, pem hanya `worker-integrations` | §3.2, §8.1 |
| U-05 | Workspace job & image sandbox-docs | `_jobs/<uuid>`, profil `workspace_kinds`, `/workspace/in` read-only | §3.5 |
| U-06 | Asset store & URL bertanda tangan | HMAC + sesi user yang sama, tanpa HTML inline | §5.9, §9.9 |
| U-07 | Scope approvals & action type v2 | `project_id` nullable, `scope_type`, `owner_id`, `github_push_pr`, `portfolio_publish` | §7.2–7.4, API §12.6 |

---

## 13. Desain v2.1 — SDLC, Developer Workspace v2, tier M/L

> **Status: Rancangan (Proposed) — belum diimplementasikan.** Disusun 22 Okt 2026. **Kontrak mengikat:** `docs/SDLC_SCALE_DESIGN.md` (SSD) §6 **KONTRAK v2.1** — K5 (M25–M27, FR-221..FR-258), K6 (AX-12..AX-18), K6b (NFR-23..NFR-27), K7 (ADR-057..062, *Proposed*), K8 (P13/P14, G13/G14, WP), K9 (aktivasi agent, tanpa agent baru), K10 (tool), K11 (data/API/config), K12 (T19–T23). Bila bagian ini bertentangan dengan K5–K12, kontrak yang berlaku; temuan dicatat di §13.0.3 dan `SYSTEM_SPEC.md` §12.9.
> **Prinsip lama tetap mengikat:** satu pintu side effect agent = `ToolGateway`; tidak ada eksekusi kode project di api/worker (semua lewat sandbox-manager); status task hanya lewat `workflow/states.py::transition`; approval hanya diputuskan user via `POST /approvals/{id}/decision`; secret tidak pernah di prompt/log/event/artefak/sandbox. Semua angka kapasitas/waktu di bagian ini **[Menebak]** kecuali disebut lain.

### 13.0 Ringkasan, prinsip tambahan, dan temuan

#### 13.0.1 Ringkasan

1. **M25 SDLC:** domain baru `app/sdlc/` (work item, `ProgramPlan`, roll-up status deterministik, penjadwal DAG, papan 8 fase, traceability, release/environment) dan `app/merge_queue/` (antrean apply per repo yang me-rebase, menguji ulang, lalu memajukan branch target secara compare-and-swap). Tidak ada `action_type` approval baru: merge queue mengonsumsi approval `apply_changes` yang ada.
2. **M26 Developer Workspace v2:** operasi file lengkap + diff 3-way + undo sesi (`app/workspace/edits.py`, `merge3.py`), take-over worktree task (`app/workspace/takeover.py` + orkestrasi di `app/services/takeover.py`), run/test oleh user (`app/runs/`), terminal PTY di sandbox lewat **satu-satunya** endpoint WebSocket (`app/terminal/`, ADR-059).
3. **M27 Tier:** profil tier sebagai data (`app/cluster/tier.py`), registry node executor + lease ber-epoch (`app/cluster/`), penyimpanan workspace multi-node (`WorkspaceStore`), SSO OIDC/SAML + SCIM + scope organisasi (`app/identity/`), anggaran organisasi/program/epic (`app/budgets/`).
4. Dua migrasi: **0007_sdlc** dan **0008_scale**, keduanya di-land *schema-first* di **WP-13.0** (AG-00 + AG-01; amandemen K13 OI-27/OI-28); tabel tier L (organisasi/SSO/SCIM) inert sampai WP-14.1/14.2; penegakan budget program aktif setelah WP-13.7. Kolom rinci: `DATA_MODEL.md` V2.9/V2.10.

#### 13.0.2 Prinsip v2.1 (melanjutkan P1–P18)

| # | Prinsip | Konsekuensi teknis |
|---|---|---|
| P19 | **Tier = konfigurasi + topologi** (ADR-060) | `TierProfile` adalah data yang divalidasi saat start; kode domain tidak bercabang `if tier == …`; fitur tier dibuka lewat `TierProfile.features` + feature flag |
| P20 | **Status work item dihitung, bukan diketik** (ADR-057) | `work_items.status` hanya ditulis `app/sdlc/rollup.py::apply_rollup` (analog `transition`); LLM tidak punya field/tool untuk status |
| P21 | **Branch target hanya dimajukan merge queue** (T23, NFR-27) | `GitAdapter.advance_target_ref` menuntut objek `MergeQueueLease` yang hanya dibuat `MergeQueue.process_next`; jalur apply lama dan git manual menolak menulis default branch bila merge queue repo aktif |
| P22 | **User menulis lewat service, agent lewat ToolGateway** | Operasi file/run/test/terminal user melalui `services/*` (audit, version check); agent tetap hanya `ToolGateway`. Selama take-over, agent tidak dijalankan dan gateway menolak tulisan ke worktree itu |
| P23 | **Interaktif tetap di sandbox** (ADR-059) | Terminal, run, LSP/DAP berjalan di container sandbox; `api` hanya meneruskan byte. WebSocket **hanya** `GET /api/v1/terminal-sessions/{id}/ws` |
| P24 | **Lease ber-epoch (fencing)** (ADR-060) | Setiap penulisan status/checkpoint/workspace dari node membawa `lease_epoch`; epoch basi ditolak di DB (transaksi bersyarat) dan di workspace store |
| P25 | **Scope organisasi di setiap query** (ADR-061) | `OrgScope` dari sesi; SQLAlchemy `with_loader_criteria` untuk semua model ber-`organization_id`; di luar org → 404; kunci Redis berawalan `kk:org:<id>:` |

#### 13.0.3 Temuan terhadap kontrak & snapshot (resolusi di desain ini)

| # | Temuan | Resolusi desain | Rujukan |
|---|---|---|---|
| 1 | SSD §4.1 mengizinkan commit langsung ke default branch, sedangkan NFR-27/T23 menuntut 100% commit lewat merge queue | **Diputuskan K13 (OI-20):** commit user → `kk/user-*` → merge queue bila aktif (default tier M/L); commit langsung hanya bila merge queue repo nonaktif (opsi tier S, OQ-02). NFR-27 berlaku untuk repo dengan merge queue aktif. Mode *Edit repo* memakai worktree user `kk/user-<user8>` dan masuk default branch lewat entri merge queue `user_branch` | K13 OI-20, OQ-02 |
| 2 | Take-over "tanpa state baru" tetapi state machine tidak punya transisi dari `waiting_approval`/`needs_changes`/`failed` ke `interrupted` | **Diputuskan K13 (OI-22, ADR-058):** take-over dari `running` (di checkpoint), `waiting_approval`, `needs_changes`, `failed` → `interrupted/user_takeover`; status lain (termasuk `validating`, `interrupted`) → 409 `INVALID_TRANSITION`; selesai take-over → `queued` dengan rencana validasi saja (test → review → User Proxy). Transisi dicatat di `workflow/states.py` pada WP-13.1 (§13.3.5) | K13 OI-22 |
| 3 | CI hanya ada untuk commit yang sudah di-push (R3) | **Diputuskan K13 (OI-23):** status CI sah hanya untuk `head_commit` yang sudah di-push (UC-18) **dan** bila rebase fast-forward (commit tidak berubah); selain itu merge queue memakai profil test lokal wajib saja (langkah `waiting_ci` dilewati). CI pasca-merge di default branch dipantau; gagal → story perbaikan otomatis (jalur FR-234) + event `merge_queue.post_merge_ci_failed`. Push branch kandidat tidak dipakai | K13 OI-23 |
| 4 | Run user tidak punya tabel di K11 | `test_runs.suite` + `run`; `initiated_by = user` | OI-24 |
| 5 | `TakeoverService` di `app/workspace/` (layer bawah) perlu `workflow.transition` (layer atas) | Dipecah: `app/workspace/takeover.py` = kunci worktree & commit atas nama user (tanpa workflow); `app/services/takeover.py` = orkestrasi status/checkpoint/event | ADR-022 |
| 6 | Failover node perlu `transition` tetapi `app/cluster/` berada di bawah workflow | `app/cluster/` murni registry/lease/penempatan; sweep failover di `app/services/cluster_ops.py` | ADR-022 |
| 7 | A03 merencanakan work item tanpa task/thread; CHECK ADR-050 `num_nonnulls(task_id, thread_id) = 1` | 0007: `agent_runs.work_item_id` + CHECK `num_nonnulls(task_id, thread_id, work_item_id) = 1` *(turunan)* | OI-26 |
| 8 | Kanal LSP/DAP (FR-242/243) vs WebSocket hanya untuk terminal | P14: rancang LSP lewat HTTP request/response + SSE notifikasi; DAP diputuskan ADR di WP-14.6 | OI-25 |
| 9 | `api` tanpa network `outbound`: tidak dapat menukar code OIDC/mengambil metadata SAML | Pertukaran code OIDC & pengambilan metadata/JWKS di `worker-integrations`; `api` memverifikasi SAML secara lokal dengan metadata ter-cache | OI-33 |
| 10 | Node tier M butuh kredensial DB/Redis + `ANTHROPIC_API_KEY` (antrean `ide`) — lebih dari "rahasia task-scoped" | Risiko sisa tier M didokumentasikan (role DB `kk_node` tanpa DDL/TRUNCATE, TLS); keputusan API node untuk tier L di WP-14.0 | OI-32 |
| 11 | ADR-014 tidak memakai WebSocket (terminal MVP read-only), sedangkan FR-241 butuh kanal dua arah | Pengecualian terbatas lewat ADR-059: **satu** route WS (`/terminal-sessions/{id}/ws`), tiket sekali pakai + cek `Origin`; event lain tetap SSE; lint frontend membatasi `new WebSocket` (§13.6) | K7 ADR-059 |
| 12 | FR-077 (terminal read-only) dan FR-078 (allowlist profil) vs terminal interaktif | FR-077 tetap untuk stream run task/agent; terminal interaktif = sesi manusia terpisah, default off, PTY di sandbox; agent tetap hanya profil allowlist — tidak ada tool terminal (R4 untuk agent) | K5 FR-241, K7 ADR-059 |
| 13 | ADR-036 menolak Kubernetes untuk pilot (§2.2), sedangkan tier L butuh pool executor terskala | Tier S/M tanpa orkestrator cluster (node executor terdaftar + lease); orkestrator tier L (VM pool vs Kubernetes) diputuskan ADR di WP-14.0 berbasis data beban tier M; kode domain tidak bergantung pada pilihan itu (`app/cluster/` hanya registry/lease/penempatan) | K7 ADR-060, OQ-01 |

### 13.1 Topologi tier M & L

```mermaid
flowchart TB
  subgraph CTRL["Node kontrol - tier M satu node, tier L HA"]
    caddy["caddy :443"]
    api["api xN - REST, SSE, WS terminal"]
    sched["scheduler beat + node watchdog"]
    wint["worker-integrations - GitHub, OIDC exchange, SAML metadata"]
    wmq["worker-ide kontrol - merge queue, sdlc jobs"]
    pg[("PostgreSQL - HA di tier L")]
    rd[("Redis - HA di tier L")]
    wss[("Workspace store - bundle atau NFS")]
  end
  subgraph N1["Node executor 1"]
    w1["worker-ide - antrean ide.node.1"]
    sm1["sandbox-manager"]
    dsp1["docker-socket-proxy"]
    sbx1[["sandbox gVisor"]]
  end
  subgraph N2["Node executor 2..n"]
    w2["worker-ide - antrean ide.node.n"]
    sm2["sandbox-manager"]
    sbx2[["sandbox gVisor atau microVM tier L"]]
  end
  caddy --> api
  api --> pg
  api --> rd
  sched --> pg
  wmq --> pg
  w1 -->|"TLS, role kk_node"| pg
  w1 -->|"TLS"| rd
  w2 -->|"TLS, role kk_node"| pg
  w1 -->|"heartbeat, token node, mTLS"| api
  w2 -->|"heartbeat, token node, mTLS"| api
  w1 --> sm1 --> dsp1 --> sbx1
  w2 --> sm2 --> sbx2
  w1 -->|"unggah dan unduh bundle checkpoint, URL terikat lease"| wss
  w2 -->|"unggah dan unduh bundle checkpoint, URL terikat lease"| wss
  api -->|"WS terminal internal, Bearer"| sm1
```

| Peran | Lokasi | Secret yang dipegang | Network |
|---|---|---|---|
| api | node kontrol | `SECRET_KEY`, `GITHUB_WEBHOOK_SECRET`, `SANDBOX_MANAGER_TOKEN` (lokal), hash token node & SCIM (DB), `OIDC_CLIENT_ID` | public via caddy, internal kontrol; listener internal node (mTLS) [Kemungkinan Besar] |
| worker-integrations | node kontrol | pem GitHub App, `GITHUB_CLIENT_SECRET`, **`OIDC_CLIENT_SECRET`** | outbound (GitHub, IdP) |
| worker-ide kontrol (merge queue) | node kontrol | `SANDBOX_MANAGER_TOKEN` lokal; tanpa kunci LLM (merge queue deterministik) | internal |
| worker-ide node | node executor | token node (TTL `EXECUTOR_NODE_TOKEN_TTL_HOURS`), `ANTHROPIC_API_KEY`, kredensial DB role `kk_node`, Redis, token sandbox-manager lokal | internal (WireGuard/VPC) + outbound Claude API |
| sandbox-manager node | node executor | token sandbox-manager lokal | `exec` lokal node |
| Tidak pernah di node executor | – | pem GitHub, `OIDC_CLIENT_SECRET`, `SCIM_TOKEN`, `SECRET_KEY` | – |

Tier S tetap topologi `IMPLEMENTATION_DESIGN_V2` §3 (satu VPS; node lokal implisit `local`, `executor_nodes` kosong).

### 13.2 Struktur paket v2.1

#### 13.2.1 Backend

```
backend/
├── alembic/versions/
│   ├── 0007_sdlc.py                 # work_items, merge queue, release, environment, ci, terminal   — WP-13.0 (AG-00/AG-01)
│   └── 0008_scale.py                # organisasi, SSO, SCIM, node, lease, multi-repo, anggaran      — WP-13.0 (AG-00/AG-01, schema-first)
└── app/
    ├── sdlc/                        # BARU (M25) [AG-08]
    │   ├── types.py                 # enum & DTO kontrak (WorkItemType/Status, SdlcPhase)          [AG-00]
    │   ├── work_items.py            # WorkItemService (CRUD, tree, close, link, baseline story)
    │   ├── plan.py                  # validate_plan, PlanDiff, accept_plan → work item + task
    │   ├── rollup.py                # compute_status (murni) + apply_rollup (satu-satunya penulis status)
    │   ├── scheduler.py             # DagScheduler (siap → enqueue, kapasitas tier, anggaran)
    │   ├── board.py                 # SdlcBoard: bukti per fase (8 fase)
    │   ├── traceability.py          # TraceabilityExporter (json/csv; pdf via job media)        [AG-09]
    │   ├── releases.py, environments.py, codeowners.py, incidents.py                          [AG-08, P2]
    ├── merge_queue/                 # BARU [AG-08]
    │   ├── queue.py                 # MergeQueue (enqueue, process_next, cancel, retry, pause)
    │   ├── candidate.py             # CandidateBuilder: worktree mq/<entry>, rebase, patch-id
    │   ├── checks.py                # RequiredChecks: profil wajib + CI (+ CODEOWNERS P2)
    │   └── reconcile.py             # rekonsiliasi commit branch target ↔ entri merged (NFR-27)
    ├── workspace/
    │   ├── edits.py                 # WorkspaceEditService primitif (create/rename/delete, journal undo) [AG-02]
    │   ├── merge3.py                # Merge3View via `git merge-file --diff3` (GitAdapter)              [AG-02]
    │   └── takeover.py              # WorktreeTakeover: kunci worktree, commit atas nama user           [AG-02/AG-09]
    ├── runs/                        # BARU UserRunService, TestSelector → profil select                [AG-03]
    ├── terminal/                    # BARU [AG-03]
    │   ├── manager.py               # TerminalSessionManager
    │   ├── tickets.py               # tiket WS sekali pakai (Redis, sha256)
    │   ├── protocol.py              # frame klien/server, validasi ukuran & laju
    │   └── transcript.py            # perekam teredaksi → artefak `log`
    ├── cluster/                     # BARU (M27) [AG-01, AG-03]
    │   ├── tier.py                  # TierProfile + validate_tier                                      [AG-00]
    │   ├── registry.py              # ExecutorRegistry (node, token bootstrap/rotasi, heartbeat)
    │   ├── placement.py             # NodeScheduler (penempatan, lease, ETA)
    │   ├── leases.py                # NodeLease, fencing epoch, LeaseLost
    │   └── workspace_store.py       # WorkspaceStore (local/shared_volume/git_bundle)                  [AG-03]
    ├── identity/                    # BARU [AG-01]
    │   ├── org_scope.py             # OrgScope + global criteria
    │   ├── sso/oidc.py, sso/saml.py, sso/mapping.py, sso/jobs.py
    │   └── scim/router_service.py   # SCIM v2 (tier L)
    ├── budgets/                     # CostBudgetService (org/program/epic, alert, kill switch)       [AG-01]
    ├── services/                    # + sdlc.py, takeover.py, workspace_edits.py, runs.py, terminal.py,
    │                                #   cluster_ops.py (failover sweep), identity.py, budgets.py
    ├── api/routes/                  # + work_items.py, merge_queue.py, releases.py, environments.py, runs.py,
    │                                #   terminal.py (REST + WS), admin_nodes.py, internal_nodes.py, admin_sso.py,
    │                                #   auth_sso.py, scim.py, orgs.py, capacity.py
    ├── tools/builtin/               # + sdlc.py, merge_queue.py, tests_select.py, index.py, ci.py
    ├── gitops/adapter.py            # + rebase, patch_id, merge_file, worktree user/mq, sparse, bundle,
    │                                #   advance_target_ref (dijaga MergeQueueLease)                    [AG-02]
    ├── sandbox/client.py            # + terminal API, subpath users/mq/repos                             [AG-00]
    └── worker/                      # + sdlc_jobs.py, mq_jobs.py, node_jobs.py, identity_jobs.py
sandbox_manager/src/sandbox_manager/
├── terminals.py                     # BARU: create/attach/resize/kill container terminal (Tty)          [AG-03]
├── validation.py                    # + SUBPATH_USER_RE, SUBPATH_MQ_RE, SUBPATH_REPOS_RE                 [AG-03]
└── drivers/microvm.py               # P14 (WP-14.3)
sandbox-images/sandbox-profiles.yaml # + run.*, *.test_select, fmt.*, lint.*_file, index.ctags_incremental, terminal.shell
```

Pola subpath sandbox baru (validasi regex + `realpath` di bawah `WORKSPACE_ROOT`): `^<uuid>/users/<uuid>$` (worktree user), `^<uuid>/mq/<uuid>$` (kandidat merge queue), `^<uuid>/repos/[a-z0-9][a-z0-9-]{0,39}$` (repo tambahan multi-repo). Pola lama (`tasks/<uuid>`, `repo`, `_jobs/<uuid>`) tetap.

**Import-linter v2.1** (tambahan kontrak §4.3): layer baru `"(app.sdlc) : (app.merge_queue) : (app.terminal) : (app.runs)"` di antara `app.services` dan layer domain v2; `"(app.cluster) : (app.identity) : (app.budgets)"` sejajar `app.capabilities`. Kontrak `forbidden`: `app.cluster`, `app.workspace` tidak mengimpor `app.workflow`; hanya `app.merge_queue` yang boleh memanggil `GitAdapter.advance_target_ref` (cek grep CI + test).

#### 13.2.2 Frontend

```
frontend/src/
├── features/
│   ├── sdlc/          # ProgramTree, WorkItemDetail, StoryRequirements, PlanDiffView, SdlcBoard (8 fase),
│   │                  #   ProgramDashboard, TraceabilityExport                                   [AG-10, AG-11]
│   ├── merge-queue/   # MergeQueuePanel, EntryDetail (bukti, test_runs, CI), PolicyEditor (admin)   [AG-10]
│   ├── releases/      # ReleaseList, ReleaseEditor, EnvironmentRegistry                             [AG-11]
│   ├── workspace/     # + FileOpsMenu, ConflictMerge3 (Monaco diff 3-way), UndoStack, FormatPatchDialog,
│   │                  #   TakeoverBanner, RunPanel, TestSelector, GitPanel                         [AG-10]
│   ├── terminal/      # TerminalSession (xterm.js interaktif), TerminalDisabledState               [AG-10]
│   ├── admin/nodes/   # NodeList, NodeDetail (drain/revoke/rotate), CapacityPage                   [AG-11]
│   ├── admin/sso/     # SsoConnections, GroupRoleMap, ScimTokens                                    [AG-11]
│   └── orgs/          # OrgSwitcher, Members, Budgets                                               [AG-11]
├── api/sse/projectStream.ts      # event work_item.* / merge_queue.* (tanpa replay; refetch saat reconnect)
└── api/ws/terminal.ts            # klien WebSocket terminal (satu-satunya WS di SPA)
```

### 13.3 Interface level kode

Signature = **kontrak rancangan** (Python 3.12, `from __future__ import annotations`). Pemilik file kontrak: AG-00 (WP-13.0).

#### 13.3.1 `WorkItemService` & kontrak `ProgramPlan` (FR-221..FR-223)

```python
# app/sdlc/types.py
class WorkItemType(StrEnum):
    PROGRAM = "program"; EPIC = "epic"; STORY = "story"

class WorkItemStatus(StrEnum):
    DRAFT = "draft"; READY = "ready"; IN_PROGRESS = "in_progress"; BLOCKED = "blocked"
    IN_REVIEW = "in_review"; ACCEPTED = "accepted"; CLOSED = "closed"

SdlcPhase = Literal["requirement", "design", "implementation", "testing", "run", "review", "release", "operate"]
ALLOWED_PARENT: Mapping[WorkItemType, frozenset[WorkItemType | None]] = {
    WorkItemType.PROGRAM: frozenset({None}),
    WorkItemType.EPIC: frozenset({WorkItemType.PROGRAM, None}),
    WorkItemType.STORY: frozenset({WorkItemType.EPIC, None}),      # story tanpa epic sah (tier S/M)
}

@dataclass(frozen=True, slots=True)
class WorkItemCreate:
    project_id: UUID
    type: WorkItemType
    title: str                                   # ≤ 200
    parent_id: UUID | None = None
    description: str = ""
    owner_id: UUID | None = None
    priority: Literal["low", "normal", "high"] = "normal"
    mandatory: bool = True                       # dipakai roll-up parent (SSD §3.1 "wajib")
    repo_aliases: tuple[str, ...] = ()           # repo terlibat (FR-254); default = repo primer

class WorkItemService(Protocol):
    async def create(self, db: AsyncSession, actor: Actor, data: WorkItemCreate) -> WorkItem: ...
    async def update(self, db: AsyncSession, actor: Actor, work_item_id: UUID, patch: WorkItemPatch, *,
                     expected_version: int) -> WorkItem: ...            # status TIDAK dapat di-patch
    async def close(self, db: AsyncSession, actor: Actor, work_item_id: UUID, *, reason: str) -> WorkItem: ...
    async def tree(self, db: AsyncSession, actor: Actor, root_id: UUID, *, depth: int = 3) -> WorkItemTree: ...
    async def link(self, db: AsyncSession, actor: Actor, work_item_id: UUID, link: WorkItemLinkCreate) -> WorkItemLink: ...
    async def set_story_baseline(self, db: AsyncSession, actor: Actor, story_id: UUID,
                                 baseline: RequirementBaselineIn) -> int: ...     # versi baru; lama superseded
    async def request_plan(self, db: AsyncSession, actor: Actor, work_item_id: UUID, *,
                           instructions: str | None = None) -> UUID: ...        # job_id; izin workitem:plan
    async def get_plan(self, db: AsyncSession, actor: Actor, plan_id: UUID) -> PlanProposal: ...
    async def accept_plan(self, db: AsyncSession, actor: Actor, work_item_id: UUID, *, plan_id: UUID,
                          plan_sha256: str, selection: Sequence[str] | None = None) -> PlanAcceptResult:
        """Aksi user biasa (bukan approval R3). plan_sha256 ≠ hash tersimpan → PlanStale (409 PLAN_STALE).
        Satu transaksi: work item epic/story baru, requirement & AC story (status draft, perlu persetujuan
        baseline), task (status draft, tasks.work_item_id, tasks.project_repository_id), task_dependencies(blocks)
        dari depends_on/cross_dependencies. Idempoten per (plan_id) — plan berstatus accepted tidak diproses ulang."""
```

```python
# app/agents/contracts.py — output_contract baru A03 (WP-13.4; registry versi berikutnya, tetap 53 entri)
class PlannedCriterion(BaseModel, extra="forbid"):
    key: Annotated[str, StringConstraints(pattern=r"^AC-\d{1,3}$")]
    criterion: Annotated[str, StringConstraints(max_length=1000)]
    verification_method: Literal["test", "api", "browser", "review", "manual"]
    mandatory: bool = True

class PlannedTask(BaseModel, extra="forbid"):
    key: Annotated[str, StringConstraints(pattern=r"^T\d{1,3}$")]
    title: Annotated[str, StringConstraints(max_length=200)]
    prompt: Annotated[str, StringConstraints(max_length=4000)]   # → tasks.prompt; intake A02 per task (S1)
    repo_alias: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")]  # tier S: "repo"
    workflow_type: Literal["create", "modify", "debug", "test", "review", "docs"]
    depends_on: list[str] = []                   # key task dalam plan yang sama
    proves: list[str] = []                       # key AC story yang dibuktikan
    specialist_hint: str | None = None           # hanya implementer enabled (B14/B09/B10/B13/G35)
    complexity: Literal["low", "medium", "high"]
    est_cost_usd: Decimal | None = None          # estimasi; UI berlabel "estimasi model"

class PlannedStory(BaseModel, extra="forbid"):
    key: Annotated[str, StringConstraints(pattern=r"^S\d{1,3}$")]
    title: str; description: str
    acceptance_criteria: Annotated[list[PlannedCriterion], Field(min_length=1, max_length=30)]
    tasks: Annotated[list[PlannedTask], Field(min_length=1, max_length=20)]     # [Menebak]
    mandatory: bool = True

class PlannedEpic(BaseModel, extra="forbid"):
    key: Annotated[str, StringConstraints(pattern=r"^E\d{1,3}$")]
    title: str; description: str
    stories: Annotated[list[PlannedStory], Field(min_length=1, max_length=30)]

class PlannedDependency(BaseModel, extra="forbid"):
    from_key: str; to_key: str                   # to_key menunggu from_key (blocks)

class ProgramPlan(BaseModel, extra="forbid"):
    schema_version: Literal[1] = 1
    target_work_item_id: UUID
    target_type: Literal["program", "epic", "story"]
    epics: list[PlannedEpic] = []                # wajib (dan hanya ini) bila target program
    stories: list[PlannedStory] = []             # wajib bila target epic
    tasks: list[PlannedTask] = []                # wajib bila target story
    cross_dependencies: list[PlannedDependency] = []
    assumptions: list[str] = []; risks: list[str] = []; open_questions: list[str] = []
    # Tidak ada field status, owner, budget, approval — LLM tidak dapat menetapkannya (CLAUDE.md #1, #5).
```

`app/sdlc/plan.py::validate_plan(plan, project) -> PlanValidation` (deterministik, juga dipakai tool `sdlc.propose_plan` R0 sebagai *dry-run*): satu-satunya daftar terisi sesuai `target_type`; kunci unik global; semua `depends_on`/`cross_dependencies`/`proves` merujuk kunci yang ada; DAG tanpa siklus (algoritma Kahn, error memuat siklus); `repo_alias` ∈ `project_repositories.alias`; total task ≤ 200 [Menebak]; `specialist_hint` ∈ implementer enabled. `PlanDiff` = `added` / `changed` (judul/deskripsi/AC beda pada kunci yang sudah ditautkan) / `untouched` (item lama yang tidak ada di plan — **tidak pernah dihapus otomatis**). Alur A03: (1) panggil `sdlc.propose_plan` (validasi + diff, tanpa menulis) sampai valid; (2) akhiri run dengan `submit_result(ProgramPlan)`; (3) service menyimpan proposal sebagai artefak `plan` + `work_item_links(link_type='plan_proposal', metadata.status='proposed', sha256)`. Run A03 tercatat dengan `agent_runs.work_item_id` (temuan #7).

#### 13.3.2 Roll-up status & papan SDLC (FR-222, FR-226)

```python
@dataclass(frozen=True, slots=True)
class RollupInput:
    item: WorkItemSnapshot                      # type, mandatory, closed_at, baseline_approved_version
    tasks: Sequence[TaskSnapshot]               # status, status_reason (story)
    criteria: Sequence[CriterionSnapshot]       # mandatory, status (story)
    children: Sequence[WorkItemSnapshot]        # status, mandatory (epic/program)
    budget_blocked: bool

def compute_status(inp: RollupInput) -> tuple[WorkItemStatus, str]:
    """Fungsi murni (property test). Story: closed → CLOSED; tanpa baseline & tanpa task → DRAFT;
    budget_blocked atau task failed/cancelled/rejected tanpa pengganti atau interrupted (selain user_takeover/
    paused_by_user) → BLOCKED; semua task completed + semua AC wajib passed/waived_by_user → ACCEPTED;
    semua task completed tetapi AC wajib belum → IN_REVIEW; ada task queued/running/validating/
    waiting_approval/needs_changes/interrupted(takeover|pause) → IN_PROGRESS; selain itu READY.
    Epic: semua story wajib ACCEPTED (non-wajib boleh CLOSED) → ACCEPTED. Program: semua epic wajib
    ACCEPTED atau CLOSED dengan alasan → ACCEPTED. Alasan (str) dicatat di status_reason."""

async def apply_rollup(db: AsyncSession, work_item_id: UUID, *, cause: str) -> WorkItemStatus:
    """SATU-SATUNYA penulis work_items.status. Optimistic lock (version), audit work_item.status_changed,
    event SSE, lalu enqueue roll-up parent. Dipicu job sdlc_jobs.rollup (debounce 2 dtk per item) setelah
    task.status_changed, perubahan AC, plan diterima, atau close — tidak ada import workflow → sdlc."""
```

Bukti fase (`SdlcBoard.phases(story) -> list[PhaseStatus{phase, state: done|pending|not_applicable, evidence_refs}]`), dihitung dari rekaman, tanpa input LLM:

| Fase | Bukti yang membuat `done` |
|---|---|
| requirement | baseline story disetujui (versi ≥ 1) dan ≥ 1 AC wajib |
| design | tautan `artifact` desain (patch dokumen ter-apply) **atau** ADR di `project_memory` ditautkan |
| implementation | semua task story punya commit di branch task dan tidak ada task aktif |
| testing | untuk setiap task: `test_runs` lulus pada head akhir setelah take-over terakhir; profil wajib lengkap |
| run | `test_runs(suite='run')` sukses atau sesi preview tercatat untuk story |
| review | tidak ada finding `critical` terbuka; laporan acceptance terbaru `passed` untuk semua AC wajib |
| release | semua entri merge queue task story `merged` (+ CI bila diwajibkan); item release bila release train dipakai |
| operate | tautan insiden/observasi (opsional; tidak memblokir `accepted`) |

`not_applicable` hanya untuk fase design/run/operate, ditetapkan user dengan alasan (audit) — tidak pernah `done` tanpa bukti *(turunan; OQ di §13.9)*.

#### 13.3.3 `DagScheduler` (FR-224)

```python
class DagScheduler(Protocol):
    async def ready_tasks(self, db: AsyncSession, root_work_item_id: UUID) -> list[UUID]:
        """Task status draft (belum pernah dijalankan) yang semua task_dependencies(blocks) sumbernya completed."""
    async def tick(self, db: AsyncSession) -> SchedulingReport:
        """Beat 15 dtk + dipicu task completed. Urutan deterministik: prioritas task → prioritas work item → created_at.
        Kapasitas: Σ task running|validating per node < TierProfile.max_active_tasks_per_node (tier S: node lokal).
        Anggaran: CostBudgetService.check(program, epic, org) — over → tidak dijadwalkan + event budget.blocked.
        Enqueue lewat services/tasks.enqueue (jalur v1; worktree terpisah ADR-016). Tidak pernah melompati DAG."""
```

#### 13.3.4 `MergeQueue` (FR-225, FR-230, FR-233, FR-254)

```python
class EntryStatus(StrEnum):
    QUEUED = "queued"; PREPARING = "preparing"; TESTING = "testing"; WAITING_CI = "waiting_ci"
    MERGING = "merging"; MERGED = "merged"; FAILED = "failed"; NEEDS_REAPPROVAL = "needs_reapproval"
    CANCELLED = "cancelled"

FailureCode = Literal["REBASE_CONFLICT", "DIFF_CHANGED", "REQUIRED_PROFILE_FAILED", "CI_FAILED", "CI_TIMEOUT",
                      "CODEOWNERS_PENDING", "TARGET_MOVED", "INFRA_ERROR", "CANCELLED_BY_USER"]

@dataclass(frozen=True, slots=True)
class EnqueueRequest:
    source_type: Literal["task", "user_branch"]
    repository_id: UUID                          # repositories.id (per repo — FR-254)
    source_branch: str                           # kk/task-<8hex> | kk/user-<user8>
    approved_head: str                           # task: head_commit payload apply_changes
    approved_patch_id: str                       # git patch-id --stable dari diff yang disetujui
    target_branch: str
    requested_by: UUID
    task_id: UUID | None = None
    approval_id: UUID | None = None              # wajib untuk source_type=task (sudah consume)

class MergeQueue(Protocol):
    async def enqueue(self, db: AsyncSession, req: EnqueueRequest) -> MergeQueueEntry:
        """task: dipanggil handler approval apply_changes SETELAH consume() atomik (approval → entri, sekali pakai).
        user_branch: aksi user (workspace:write + git:branch) — entri tetap melewati profil wajib (CI tidak berlaku
          kecuali head sudah ter-push dan rebase fast-forward, K13 OI-23).
        Partial unique: satu entri aktif per (repository_id, source_branch). Posisi = sequence per repo."""
    async def process_next(self, db: AsyncSession, repository_id: UUID, *, worker_id: str) -> MergeQueueEntry | None:
        """pg_advisory_xact_lock(repo) → entri queued teratas (FOR UPDATE SKIP LOCKED) →
        1 preparing: worktree mq/<entry> --detach di head target H; rebase source ke H (GitAdapter, tanpa hooks);
          konflik → FAILED(REBASE_CONFLICT, bukti: file konflik) → transition(waiting_approval→needs_changes).
        2 patch-id kandidat ≠ approved_patch_id → NEEDS_REAPPROVAL: approval apply_changes baru (payload dengan
          head kandidat), approval lama superseded, entri keluar antrean.
        3 testing: MERGE_QUEUE_REQUIRED_PROFILES (atau profil stack bila 'auto') via SandboxClient pada subpath mq;
          test_runs(initiated_by='system', merge_queue_entry_id, head_commit); gagal → FAILED(REQUIRED_PROFILE_FAILED).
        4 waiting_ci (policy require_ci, aturan K13 OI-23): CI sah HANYA bila approved_head sudah di-push (UC-18)
          DAN rebase fast-forward (kandidat == approved_head, commit tidak berubah) → ci.get_checks(approved_head);
          gagal → FAILED(CI_FAILED), timeout → FAILED(CI_TIMEOUT). Selain itu (belum di-push / rebase membuat
          commit baru) langkah waiting_ci DILEWATI: entri hanya memakai profil lokal wajib (langkah 3) dan
          entri.ci_required=false + ci_skip_reason ('not_pushed'|'not_fast_forward') dicatat sebagai bukti.
          Tidak pernah menunggu CI tanpa batas: ci_timeout_minutes selalu berlaku.
        5 merging: advance_target_ref(expected_old=H, new=kandidat) CAS; head berubah → TARGET_MOVED → ulang dari 1.
        6 merged: result_commit, transition(waiting_approval→completed), event + audit merge_queue.merged;
          result_commit didaftarkan ke pemantau CI pasca-merge (watch_post_merge_ci).
        Setiap langkah = checkpoint entri (idempoten saat worker restart)."""
    async def watch_post_merge_ci(self, db: AsyncSession, entry_id: UUID) -> None:
        """K13 OI-23: CI pasca-merge di default branch. ci_check_results untuk result_commit (webhook check_suite/
        check_run atau poll, WP-13.12) → conclusion gagal → event merge_queue.post_merge_ci_failed + story perbaikan
        otomatis lewat jalur FR-234 (work item story 'draft' dengan tautan bukti ci_check_result + entri), tanpa
        revert otomatis (revert = perubahan baru lewat merge queue). Tanpa GitHub App (sebelum G9) → no-op."""
    async def cancel(self, db: AsyncSession, actor: Actor, entry_id: UUID, *, reason: str) -> MergeQueueEntry: ...
    async def retry(self, db: AsyncSession, actor: Actor, entry_id: UUID) -> MergeQueueEntry: ...  # hanya INFRA_ERROR/CI_TIMEOUT
    async def pause(self, db: AsyncSession, actor: Actor, repository_id: UUID, *, paused: bool) -> None: ...

@dataclass(frozen=True, slots=True)
class MergeQueueLease:                          # objek in-process; tidak dapat dibuat dari input API/LLM
    entry_id: UUID; repository_id: UUID; expected_old: str; candidate: str

class TargetRefGuard(Protocol):                 # bagian GitAdapter (T23)
    async def advance_target_ref(self, repo: RepoHandle, *, branch: str, lease: MergeQueueLease) -> None:
        """git update-ref refs/heads/<branch> <candidate> <expected_old> (compare-and-swap, tanpa force)."""
```

Status task selama antrean tetap `waiting_approval` (tanpa transisi baru); `completed` hanya setelah `merged`. E28 (setelah aktivasi P13) hanya mengusulkan patch resolusi konflik di branch terpisah sebagai task baru — tidak pernah memajukan branch target.

#### 13.3.5 `TakeoverService` (FR-236, FR-237; ADR-058)

```python
@dataclass(frozen=True, slots=True)
class TakeoverState:                            # tasks.checkpoint["takeover"] (tanpa kolom baru, OI-34)
    active: bool
    pending: bool                               # menunggu worker berhenti di batas langkah
    by_user_id: UUID
    epoch: int                                  # naik setiap take-over
    started_at: datetime
    base_head: str                              # head branch task saat take-over aktif
    released_at: datetime | None = None
    user_commits: tuple[str, ...] = ()

class TakeoverService(Protocol):                # app/services/takeover.py
    async def start(self, db: AsyncSession, actor: Actor, task_id: UUID) -> TakeoverState:
        """Izin task:control + workspace:write; take-over aktif oleh user lain → 409 TAKEOVER_ACTIVE.
        Status awal yang diizinkan (K13 OI-22, ADR-058):
        running → pause_requested=true + takeover.pending (pola ADR-031); worker di batas langkah berikutnya
          menyimpan checkpoint lalu transition(running→interrupted, reason='user_takeover').
        waiting_approval|needs_changes|failed → transition langsung ke interrupted/user_takeover
          (approval apply_changes pending → superseded).
        Status lain — termasuk validating dan interrupted, serta draft|planned|queued|completed|cancelled|
          rejected → 409 INVALID_TRANSITION."""
    async def release(self, db: AsyncSession, actor: Actor, task_id: UUID, *, commit_message: str | None,
                      resume_mode: Literal["revalidate", "stay_interrupted"]) -> TakeoverState:
        """WorktreeTakeover.commit_as_user (author = user, committer KodeKantor, trailer `KK-Takeover: <task>/<epoch>`,
        tanpa hooks). Ada commit baru → bukti stale: completed_steps tanpa S5–S9, stale_before=released_at,
        event task.evidence_stale. revalidate ("Selesai take-over") → interrupted→queued dengan rencana validasi
        saja: S5 (test) → S7 (review) → S8 (User Proxy) → S9 (gate) — TANPA langkah implementasi agent (K13 OI-22);
        stay_interrupted → commit perubahan tetapi take-over TETAP dipegang (task tetap interrupted/user_takeover);
        pemegang kembali mengedit lalu memilih revalidate, atau membatalkan task. Take-over baru dari `interrupted`
        tidak diizinkan (409 INVALID_TRANSITION), sehingga task tidak pernah tertinggal tanpa pemegang."""
    def is_evidence_fresh(self, task: Task, ref: EvidenceRef) -> bool:
        """test_run/acceptance_report sah hanya bila dibuat setelah takeover.released_at terakhir DAN untuk head
        branch saat ini. Dipakai gate S9, handler apply_changes (409 EVIDENCE_STALE), dan verifikasi FR-106."""
```

Transisi ADR-058 (diputuskan K13 OI-22; dicatat di `workflow/states.py` pada WP-13.1): `running → interrupted` (di checkpoint), `waiting_approval → interrupted`, `needs_changes → interrupted`, `failed → interrupted` — semuanya dengan `status_reason=user_takeover` (diaudit) — dan `interrupted → queued` saat selesai take-over dengan rencana validasi saja. `validating` dan `interrupted` **tidak** dapat di-take-over (409 `INVALID_TRANSITION`). Pertahanan berlapis: (1) worker menolak mengklaim lease task dengan `takeover.active`; (2) `ToolGateway` menolak tool tulis (`code.*`, `git.commit_task`) untuk worktree dengan `takeover.active` → `tool_calls.status=denied`, audit `tool.denied` (alasan `TAKEOVER_ACTIVE`); (3) `POST /tasks/{id}/resume` → 409 `TAKEOVER_ACTIVE` selama aktif.

#### 13.3.6 `WorkspaceEditService` — operasi file, 3-way, undo (FR-235, FR-237)

```python
EditTarget = Annotated[str, StringConstraints(pattern=r"^(repo|user|task:[0-9a-f-]{36})$")]
# repo = <project>/repo (read-only bila merge queue repo aktif) · user = <project>/users/<user_id> branch kk/user-<user8>
# task:<id> = worktree task, hanya saat take-over aktif oleh actor

@dataclass(frozen=True, slots=True)
class Merge3View:
    path: str; base: str | None; server: str; mine: str
    auto_merged: str | None                     # None bila ada konflik
    conflicts: list[tuple[int, int]]            # rentang baris konflik pada hasil diff3

class WorkspaceEditService(Protocol):           # app/services/workspace_edits.py (+ primitif app/workspace/edits.py)
    async def write(self, db, actor, project_id: UUID, target: EditTarget, path: str, content: bytes, *,
                    expected_sha256: str | None, editor_session: str) -> FileVersion:
        """Version check atomik (lock per path); beda → VersionConflict(Merge3View) → 409 VERSION_CONFLICT.
        Isi lama & baru ditulis juga sebagai blob git (`git hash-object -w`) agar basis 3-way & undo tersedia."""
    async def create(self, db, actor, project_id, target, path: str, kind: Literal["file", "dir"],
                     content: bytes | None, *, editor_session: str) -> FileVersion: ...   # audit workspace.file_created
    async def rename(self, db, actor, project_id, target, src: str, dst: str, *,
                     expected_sha256: str | None, editor_session: str) -> FileVersion: ...  # audit workspace.file_renamed
    async def delete(self, db, actor, project_id, target, path: str, *, expected_sha256: str | None,
                     recursive: bool, editor_session: str) -> None: ...                     # audit workspace.file_deleted
    async def undo(self, db, actor, project_id, *, editor_session: str, op_id: str) -> FileVersion | None:
        """Jurnal per (user, editor_session) di Redis (maks 200 operasi [Menebak], TTL = umur sesi editor), berisi
        sha blob sebelum/sesudah (bukan isi). Undo = operasi kebalikan dengan version check; file berubah sejak → 409."""
    async def merge3(self, db, actor, project_id, target, path: str, *, base_sha256: str, mine: bytes) -> Merge3View:
        """`git merge-file -p --diff3` atas file sementara (GitAdapter; pengecualian git CLI terdokumentasi)."""
```

Semua path lewat `TaskWorkspace.resolve(for_write=True)` (ADR-016/033): absolut, `..`, NUL, symlink keluar root, `.git/**` ditolak. Simpan biasa (bukan buat/rename/hapus) hanya dicatat di log terstruktur, sesuai FR-235.

#### 13.3.7 `UserRunService` & `tests.run_selected` (FR-239, FR-240)

```python
@dataclass(frozen=True, slots=True)
class TestSelector:
    scope: Literal["all", "file", "test"]
    path: str | None = None                     # relatif root; harus cocok glob test stack
    test_id: str | None = None                  # pytest: ^[\w./-]+(::[\w\[\].=-]+){0,3}$ · node: ^[\w .:-]{1,120}$

class UserRunService(Protocol):                 # app/runs/ + app/services/runs.py
    async def start_run(self, db, actor, project_id: UUID, *, target: EditTarget, profile_id: str,
                        args: Sequence[str]) -> TestRun:
        """profil run.* (mis. python.run_module, node.dev_server); test_runs(suite='run', initiated_by='user',
        initiated_by_user_id); dev server → preview-<run8>.<APP_DOMAIN> (ADR-008), network none ke luar."""
    async def run_tests(self, db, actor, project_id: UUID, *, target: EditTarget, selector: TestSelector) -> TestRun:
        """Pemetaan selector → profil *.test_select (argv exec-form, token tervalidasi; tanpa shell).
        test_runs(initiated_by='user', selector). Bukti story/task hanya bila head target = head task/story."""
    async def stop(self, db, actor, run_id: UUID) -> TestRun: ...
```

Jalur agent: tool `tests.run_selected` (R2, sandbox) memakai handler yang sama lewat `ToolGateway` dengan `initiated_by='agent'` (C17). Jalur user memanggil handler lewat service — bukan lewat ToolGateway — dan diaudit `test.run_started` (actor user).

#### 13.3.8 `TerminalSessionManager` (FR-241, ADR-059, T19)

```python
@dataclass(frozen=True, slots=True)
class TerminalLimits:
    max_minutes: int                            # TERMINAL_SESSION_MAX_MINUTES (30)
    idle_minutes: int                           # TERMINAL_IDLE_MINUTES (10)
    max_active_per_user_project: int = 1        # [Menebak]
    max_new_per_user_hour: int = 10             # [Menebak]
    max_input_bytes_per_s: int = 4096           # [Menebak]
    max_frame_bytes: int = 65536
    transcript_max_bytes: int = 5 * 1024 * 1024 # [Menebak]; sisa dipotong dengan penanda

class TerminalSessionManager(Protocol):         # app/terminal/manager.py
    async def create(self, db, actor, project_id: UUID, *, target: EditTarget, cols: int, rows: int) -> TerminalTicket:
        """Cek: TERMINAL_ENABLED, projects.settings.terminal.enabled (hanya admin yang dapat mengubah), terminal:use,
        tier & rate limit, target (task:<id> hanya saat take-over aktif oleh actor). Insert terminal_sessions(pending);
        sandbox-manager POST /v1/terminals; tiket 32 byte acak → Redis kk:term:ticket:<sha256> TTL 30 dtk
        {session_id, user_id, sha256(token sesi)}. Return ws_url /api/v1/terminal-sessions/<sid>/ws?ticket=<t>."""
    async def attach(self, ws: WebSocket, session_id: UUID, ticket: str) -> None:
        """Origin == origin APP_URL; cookie kk_session valid; GETDEL tiket; sha256(sesi) cocok → active.
        Pipa dua arah api ↔ sandbox-manager (WS internal + Bearer). Validasi frame (tipe, ukuran, laju), timer
        idle & durasi, rekam transcript teredaksi (core/redaction + nilai literal secret)."""
    async def close(self, db, session_id: UUID, *, reason: CloseReason) -> None:
        """Close WS dengan kode API §13.6, POST /v1/terminals/<tid>/kill, transcript → artefak `log`
        (retensi 30 hari, akses: pemilik sesi + admin project), audit terminal.session_closed."""
```

Endpoint sandbox-manager baru (internal, Bearer, network `app`/`exec` seperti §10 API v1):

| Method | Path | Isi |
|---|---|---|
| POST | `/v1/terminals` | `{project_id, workspace_subpath, profile_id: "terminal.shell", cols, rows, max_seconds}` → container `Tty=true`, `OpenStdin=true`, argv `["/bin/bash","--noprofile","--norc","-i"]`, user 10001, `cap_drop ALL`, rootfs read-only + tmpfs, **network none**, limit CPU/RAM/PID = profil sandbox, label `kk.terminal_id`, auto-kill `max_seconds` |
| GET | `/v1/terminals/{tid}/attach` | WebSocket internal; manager memakai *attach stream* Docker lewat socket proxy — **tidak** memakai `docker exec` (`EXEC=0` tetap). Dukungan hijack koneksi attach di docker-socket-proxy [Kemungkinan Besar — wajib diuji di WP-13.3] |
| POST | `/v1/terminals/{tid}/resize` | `{cols, rows}` |
| POST | `/v1/terminals/{tid}/kill` | hentikan & hapus container |

Tier M: sesi dibuat di sandbox-manager node pemegang workspace (afinitas); `api` terhubung lewat jaringan internal + token node (mTLS [Kemungkinan Besar]). Agent tidak punya tool terminal (R4 untuk agent).

#### 13.3.9 `TierProfile`, `ExecutorRegistry`, `NodeScheduler` (FR-245..FR-247, FR-258)

```python
@dataclass(frozen=True, slots=True)
class TierProfile:                              # app/cluster/tier.py — data, bukan cabang kode domain
    tier: Literal["S", "M", "L"]
    max_users: int | None                       # S 3 · M 50 · L None [Menebak]
    remote_nodes: bool                          # S False
    max_active_tasks_per_node: int              # = MAX_ACTIVE_TASKS
    sandbox_runtime_required: Literal["any", "runsc", "microvm"]   # S any · M runsc · L microvm (multi-tenant)
    features: frozenset[str]                    # work_items, merge_queue, oidc, saml, scim, orgs, siem, …
    workspace_modes: frozenset[Literal["local", "shared_volume", "git_bundle", "object_store"]]

def validate_tier(settings: Settings, profile: TierProfile) -> list[ConfigIssue]:
    """Gagal start (pesan aman) bila mis.: tier S dengan node remote aktif; tier M tanpa SANDBOX_RUNTIME=runsc;
    SAML_IDP_METADATA_URL atau SCIM_TOKEN terisi pada tier ≠ L; OIDC_* terisi sebagian; TERMINAL_ENABLED=true
    tanpa profil terminal.shell. Daftar lengkap = test_tier_validator (parametrized)."""

class NodeStatus(StrEnum):
    PENDING = "pending"; ACTIVE = "active"; DRAINING = "draining"; OFFLINE = "offline"; REVOKED = "revoked"

class ExecutorRegistry(Protocol):               # app/cluster/registry.py
    async def create_node(self, db, actor, *, name: str, labels: Mapping[str, str], capacity_slots: int,
                          organization_id: UUID | None = None) -> tuple[ExecutorNode, str]:
        """admin:nodes. Token bootstrap sekali pakai (tampil sekali, TTL 1 jam [Menebak], disimpan sha256)."""
    async def register(self, db, *, bootstrap_token: str, info: NodeInfo,
                       client_cert_sha256: str | None) -> NodeCredentials:
        """Bootstrap → token node (TTL EXECUTOR_NODE_TOKEN_TTL_HOURS, disimpan sha256), status active."""
    async def heartbeat(self, db, node: AuthenticatedNode, hb: Heartbeat) -> HeartbeatAck:
        """Slot bebas, lease berjalan (+epoch), runtime sandbox, digest image. Ack memuat lease yang wajib dihentikan."""
    async def rotate_token(self, db, node: AuthenticatedNode) -> NodeCredentials:
        """Dipanggil node pada 50% TTL; token lama sah sampai grace 10 menit [Menebak]."""
    async def drain(self, db, actor, node_id: UUID) -> None: ...   # tidak menerima lease baru
    async def revoke(self, db, actor, node_id: UUID) -> None:
        """Token dicabut seketika, lease → revoked (epoch naik), task terdampak → failover (cluster_ops)."""

@dataclass(frozen=True, slots=True)
class Placement:
    node_id: UUID; lease_id: UUID; lease_epoch: int; workspace_mode: str; reason: str

class NodeScheduler(Protocol):                  # app/cluster/placement.py
    async def place(self, db, task_id: UUID) -> Placement | None:
        """Node active, slot bebas; skor: afinitas workspace > label stack > slot bebas > id. Insert node_leases
        (epoch = max epoch task + 1; partial unique task aktif). None → task tetap antre; ETA untuk FR-258."""
    async def renew(self, db, lease_id: UUID, *, epoch: int) -> datetime: ...     # tiap 20 dtk
    async def release(self, db, lease_id: UUID, *, epoch: int, reason: str) -> None: ...

class ClusterOps(Protocol):                     # app/services/cluster_ops.py (boleh memanggil transition)
    async def sweep_dead_nodes(self, db) -> FailoverReport:
        """Beat 15 dtk: heartbeat > NODE_DEAD_AFTER (90 dtk [Menebak]) → offline; lease → expired; task
        running|validating → transition(interrupted, reason='node_lost'); enqueue resume_on_node ke node baru
        (WorkspaceStore.materialize + verify dulu; gagal → transition(interrupted→failed, WORKSPACE_INTEGRITY))."""
```

Fencing: penulisan penting dari worker node (`transition`, insert `tool_calls`, simpan checkpoint, unggah bundle) memeriksa `node_leases.epoch = :epoch AND status = 'active'` dalam transaksi yang sama → `LeaseLost` (abort, tanpa side effect). Antrean Celery per node `ide.node.<node_id>`; worker node hanya mengonsumsi antreannya sendiri.

#### 13.3.10 `WorkspaceStore` & repo besar (FR-248, FR-253, FR-254)

```python
WorkspaceMode = Literal["local", "shared_volume", "git_bundle"]      # object_store = tier L (WP-14.0)

@dataclass(frozen=True, slots=True)
class CheckpointRef:
    task_id: UUID; seq: int; commit: str        # commit pada refs/kk/checkpoints/<task>/<seq> (termasuk WIP)
    bundle_sha256: str | None; size_bytes: int | None; epoch: int

class WorkspaceStore(Protocol):                 # app/cluster/workspace_store.py
    mode: WorkspaceMode
    async def checkpoint(self, task_id: UUID, step: str, *, epoch: int) -> CheckpointRef:
        """`git add -A` + commit ke ref tersembunyi (bukan branch task, tanpa hooks); git_bundle: `git bundle create`
        inkremental dari checkpoint sebelumnya → sha256 → unggah ke store kontrol lewat URL bertanda tangan yang
        terikat (task, lease_epoch) dan berumur pendek. Disimpan di tasks.checkpoint.workspace."""
    async def materialize(self, task_id: UUID, node_id: UUID, *, epoch: int) -> Path:
        """shared_volume: verifikasi worktree ada & ref checkpoint = commit tercatat. git_bundle: unduh rantai bundle,
        verifikasi sha256 + `git bundle verify`, fetch ke mirror node, worktree add di commit checkpoint.
        Gagal → WorkspaceIntegrityError (task tidak dilanjutkan)."""
    async def verify(self, ref: CheckpointRef) -> bool: ...
    async def release(self, task_id: UUID, node_id: UUID) -> None: ...
```

Repo besar: import/clone dengan `--filter=blob:none` dan worktree task dengan `git sparse-checkout set --cone <pola>` bila `SPARSE_CHECKOUT_ENABLED=true` (pola = `project_repositories.sparse_patterns` ∪ petunjuk context A04); `index.symbols_incremental` (R2, sandbox, network none) mengindeks hanya path yang berubah sejak `repositories.index_commit` ke `repo_index_entries` — **tanpa embedding** (ADR-046 berlaku). Multi-repo: `tasks.project_repository_id` menentukan repo worktree; merge queue per `repository_id`; release mengikat `release_items(repo_version)` per repo.

#### 13.3.11 Identitas: SSO, SCIM, `OrgScope` (FR-250..FR-252)

```python
class OidcFlow(Protocol):                       # api — app/identity/sso/oidc.py
    async def start(self, db, connection: SsoConnection, *, return_to: str) -> AuthRedirect:
        """state (32 byte) + nonce + PKCE verifier di Redis (TTL 10 menit) terikat cookie pra-login kk_sso
        (HttpOnly, SameSite=Lax); endpoint dari discovery yang di-cache worker."""
    async def callback(self, db, *, state: str, code: str, pre_login_cookie: str) -> SsoRequest:
        """GETDEL state + cocokkan cookie → enqueue identity_jobs.exchange_code (antrean integrations) → SsoRequest
        pending; browser polling /auth/sso/requests/<id> (api tanpa network outbound, OI-33)."""

class OidcExchanger(Protocol):                  # worker-integrations — satu-satunya pemegang OIDC_CLIENT_SECRET
    async def exchange(self, req: SsoRequest) -> VerifiedIdentity:
        """Token endpoint (PKCE). id_token: tanda tangan JWKS (alg allowlist RS256/ES256; tolak none/HS*),
        iss == OIDC_ISSUER, aud ∋ OIDC_CLIENT_ID, exp/iat/nbf (skew ≤ 60 dtk), nonce cocok. Ambil sub + klaim grup."""

class SamlVerifier(Protocol):                   # api — verifikasi lokal; metadata IdP diambil worker & di-cache di DB
    async def verify_response(self, db, connection: SsoConnection, saml_response_b64: str,
                              relay_state: str) -> VerifiedIdentity:
        """Assertion wajib bertanda tangan dengan sertifikat IdP dari metadata; tolak unsigned dan XML signature
        wrapping (library terawat, mode strict) [Kemungkinan Besar pilihan library]; Audience == SP entity ID;
        Destination/Recipient == ACS; NotBefore/NotOnOrAfter; InResponseTo cocok; cache ID assertion (anti replay)."""

class GroupRoleMapper(Protocol):
    def map(self, connection: SsoConnection, groups: Sequence[str]) -> list[RoleGrant]:
        """Hanya pemetaan eksplisit sso_connections.group_role_map; grup tak dikenal diabaikan; tidak pernah memberi
        owner platform; hasil kosong → login ditolak (audit auth.sso_denied, alasan NO_ROLE)."""

class ScimService(Protocol):                    # app/identity/scim (tier L)
    async def authenticate(self, db, bearer: str) -> ScimClient: ...     # sha256 + compare_digest (scim_tokens/SCIM_TOKEN)
    async def deactivate(self, db, org_id: UUID, user_id: UUID) -> None:
        """membership disabled; cabut semua sessions (org/user) + token API; publish kk:revoke:<user> agar SSE &
        WS terminal ditutup ≤ 60 dtk; target efektif ≤ 5 menit [Menebak] (NFR-26)."""

@dataclass(frozen=True, slots=True)
class OrgScope:                                 # FastAPI dependency dari sesi (organization_id aktif)
    organization_id: UUID; user_id: UUID; org_roles: frozenset[str]
# SQLAlchemy with_loader_criteria(OrgScoped, lambda cls: cls.organization_id == scope.organization_id) untuk semua
# model ber-organization_id; lookup di luar org → NotFound (404); penautan lintas org → OrgScopeError (409 ORG_SCOPE)
```

Login lokal dapat dimatikan per organisasi (`sso_connections.enforce_sso`); akun owner *break-glass* lokal tetap ada dengan audit khusus *(turunan)*. JIT provisioning nonaktif default (T22); pencocokan identitas memakai `(sso_connection_id, external_subject)`, tidak pernah email saja.

#### 13.3.12 Registrasi tool v2.1 di ToolGateway (K10)

| Tool | Risk | executor | Syarat | Handler / modul | Antrean | Pemanggil |
|---|---|---|---|---|---|---|
| `workspace.create_file`, `workspace.rename`, `workspace.delete` | R1 | backend | aksi **user** (`workspace:write`) | `services/workspace_edits.py` | – (api) | user; **tidak** di allowlist agent mana pun |
| `sdlc.propose_plan` | R0 | backend | – | `tools/builtin/sdlc.py` → `validate_plan` (dry-run) | ide | A03 |
| `merge_queue.enqueue` | R1 | backend | approval `apply_changes` valid & ter-consume | `tools/builtin/merge_queue.py` → `MergeQueue.enqueue` | ide | handler approval (ORCH/E27 tool-mode); tidak pernah agent llm |
| `tests.run_selected` | R2 | sandbox | `sandbox: allow` | `tools/builtin/tests_select.py` → `UserRunService` handler | ide | C17, user |
| `index.symbols_incremental` | R2 | sandbox | `sandbox: allow`; network none | `tools/builtin/index.py` | ide | job indexer sistem (rekomendasi); A04 memakai hasilnya lewat `repo.search_symbol` — memberi A04 tool ini berarti `sandbox: deny → allow` (V8, perluasan akses; `AGENT_CATALOG.md` §13.5) |
| `ci.get_checks` | R0 | worker integrations | GitHub App `checks: read` | `tools/builtin/ci.py` → `integrations/github/checks.py` | integrations | J48, merge queue (WP-13.12, setelah G9; hanya head ter-push + rebase fast-forward, plus CI pasca-merge — K13 OI-23) |

### 13.4 Alur detail

#### 13.4.1 Take-over task (FR-236)

```mermaid
sequenceDiagram
  autonumber
  participant U as Browser IDE
  participant API as api
  participant DB as PostgreSQL
  participant W as worker-ide
  participant WT as Worktree task
  U->>API: POST /tasks/{tid}/takeover
  API->>API: task:control dan workspace:write, cek status, cek take-over aktif
  API->>DB: pause_requested true, checkpoint.takeover pending
  API-->>U: 202 takeover pending
  W->>W: batas langkah berikutnya, simpan checkpoint
  W->>DB: transition running ke interrupted (user_takeover)
  W->>DB: checkpoint.takeover active, base_head
  DB-->>U: SSE task.takeover_started
  U->>API: PUT /projects/{pid}/files/{path} target task, expected_sha256
  API->>WT: tulis file (resolve for_write, version check)
  Note over W,WT: Agent tidak berjalan, ToolGateway menolak tulisan (TAKEOVER_ACTIVE)
  U->>API: POST /tasks/{tid}/takeover/release resume_mode revalidate
  API->>WT: commit atas nama user (trailer KK-Takeover)
  API->>DB: released_at, bukti lama stale, S5 sampai S9 dihapus dari completed_steps
  API->>DB: transition interrupted ke queued
  W->>W: rencana validasi saja tanpa langkah implementasi agent
  W->>W: S5 test, S7 review, S8 User Proxy, S9 gate
  W->>DB: waiting_approval apply_changes dengan bukti baru
```

Take-over hanya dapat dimulai dari `running` (alur di atas, berhenti di checkpoint) atau langsung dari `waiting_approval`, `needs_changes`, `failed`; `validating` dan `interrupted` → 409 `INVALID_TRANSITION` (K13 OI-22).

#### 13.4.2 Merge queue (FR-225, FR-230)

```mermaid
sequenceDiagram
  autonumber
  participant U as Reviewer
  participant API as api
  participant AP as Approvals
  participant MQ as worker merge queue
  participant G as GitAdapter
  participant SM as sandbox-manager
  participant WI as worker-integrations
  participant DB as PostgreSQL
  U->>API: POST /approvals/{aid}/decision approve dan payload_hash
  API->>AP: decide, handler apply_changes
  AP->>MQ: enqueue setelah commit
  MQ->>AP: consume atomik
  MQ->>DB: entri queued, posisi per repo, patch-id disetujui
  MQ->>DB: advisory lock repo, ambil entri teratas
  MQ->>G: worktree mq dari head target, rebase branch task
  alt konflik rebase
    MQ->>DB: entri failed REBASE_CONFLICT dengan bukti
    MQ->>DB: transition waiting_approval ke needs_changes
  else rebase bersih
    MQ->>G: hitung patch-id kandidat
    alt patch-id berbeda
      MQ->>DB: entri needs_reapproval, approval apply_changes baru
    else patch-id sama
      MQ->>SM: POST /v1/runs profil wajib pada subpath mq
      SM-->>MQ: hasil run
      MQ->>DB: test_runs initiated_by system
      alt require_ci dan head ter-push dan rebase fast-forward
        MQ->>WI: ci.get_checks approved_head
        WI-->>MQ: status checks
      else selain itu
        MQ->>DB: waiting_ci dilewati, catat ci_skip_reason, profil lokal wajib saja
      end
      MQ->>G: advance_target_ref compare-and-swap
      MQ->>DB: entri merged, transition waiting_approval ke completed
    end
  end
  MQ-->>U: SSE merge_queue.entry_merged atau entry_failed
  WI->>DB: ci_check_results untuk result_commit di default branch
  opt CI pasca-merge gagal
    MQ->>DB: story perbaikan otomatis jalur FR-234
    MQ-->>U: SSE merge_queue.post_merge_ci_failed
  end
```

#### 13.4.3 Sesi terminal (FR-241, ADR-059)

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser xterm.js
  participant C as Caddy
  participant API as api
  participant R as Redis
  participant SM as sandbox-manager
  participant T as Container terminal network none
  B->>API: POST /projects/{pid}/terminal-sessions target user, cols, rows
  API->>API: TERMINAL_ENABLED, setting project, terminal:use, rate limit
  API->>SM: POST /v1/terminals profil terminal.shell
  SM->>T: buat container Tty, limit sandbox, auto-kill durasi
  SM-->>API: terminal_id
  API->>R: SET hash tiket TTL 30 dtk
  API-->>B: 201 session dan ws_url sekali pakai
  B->>C: GET ws_url (Upgrade websocket, cookie sesi, Origin)
  C->>API: proxy upgrade
  API->>API: cek Origin, cek sesi, GETDEL tiket
  API->>SM: attach WS internal (Bearer)
  SM->>T: attach stdin dan stdout
  loop sesi aktif
    B->>API: frame input atau resize
    API->>SM: teruskan (batas ukuran dan laju)
    T-->>SM: output
    SM-->>API: output
    API->>API: rekam transcript teredaksi
    API-->>B: frame output
  end
  API->>SM: kill (idle, durasi habis, atau ditutup user)
  API->>API: transcript ke artefak, audit terminal.session_closed
  API-->>B: close code (mis. 4408 idle)
```

#### 13.4.4 Failover node (FR-247, FR-248)

```mermaid
sequenceDiagram
  autonumber
  participant N1 as Node 1
  participant CP as Kontrol api dan scheduler
  participant DB as PostgreSQL
  participant WSS as Workspace store
  participant N2 as Node 2
  N1->>CP: heartbeat (token node, lease epoch 3)
  N1->>WSS: bundle checkpoint langkah S4 (sha256, epoch 3)
  Note over N1: node mati, heartbeat berhenti
  CP->>DB: sweep, node tanpa heartbeat lebih dari 90 dtk
  CP->>DB: node offline, lease expired
  CP->>DB: transition running ke interrupted (node_lost)
  CP->>DB: pilih N2, lease baru epoch 4
  CP->>N2: enqueue resume_on_node ke antrean ide.node.N2
  N2->>WSS: unduh bundle, verifikasi sha256 dan git bundle verify
  alt checksum cocok
    N2->>DB: transition interrupted ke queued lalu running (epoch 4)
    N2->>N2: lanjut dari checkpoint, langkah selesai dilewati
  else checksum tidak cocok
    N2->>DB: transition interrupted ke failed (WORKSPACE_INTEGRITY)
  end
  N1->>CP: node hidup lagi, heartbeat epoch 3
  CP-->>N1: ack, lease dicabut, hentikan container task
  N1->>DB: tulisan epoch 3 ditolak (LeaseLost)
```

### 13.5 Migrasi 0007_sdlc & 0008_scale

```
… → 0006_kb → 0007_sdlc → 0008_scale
```

| Migrasi | Di-land oleh | Isi ringkas | Catatan urutan |
|---|---|---|---|
| 0007_sdlc | WP-13.0 (AG-00 + AG-01) | tabel `work_items`, `work_item_links`, `merge_queue_entries`, `releases`, `release_items`, `environments`, `ci_check_results`, `terminal_sessions`; `tasks.work_item_id`, `test_runs.initiated_by` (+`initiated_by_user_id`, `selector`, suite `run`); *(turunan)* `agent_runs.work_item_id` + CHECK scope, `requirements`/`acceptance_criteria.work_item_id`, `deployments.environment_id`/`release_id` | schema-first; fitur di belakang flag P13 |
| 0008_scale | WP-13.0 (AG-00 + AG-01; K13 OI-27/OI-28) | `organizations`, `org_memberships`, `sso_connections`, `scim_tokens`, `executor_nodes`, `node_leases`, `project_repositories`, `cost_budgets`; kolom `organization_id` (backfill org default, kecuali `audit_events` yang append-only); `tasks.project_repository_id`; UNIQUE `repositories.project_id` dilepas; `users.auth_source`, `password_hash` nullable; `sessions.organization_id`, `auth_method`; FK `terminal_sessions.node_id` | *schema-first* bersama 0007 (K13); tabel tier L (organisasi/SSO/SCIM) inert sampai WP-14.1/14.2; tabel node/lease/anggaran dipakai logika WP-13.7; penegakan budget program (FR-224/FR-255) aktif setelah WP-13.7 — sebelumnya budget per task v1 |

**Kolom kunci & constraint per tabel K11** (ringkas; definisi kolom lengkap, FK `ON DELETE`, dan retensi di `DATA_MODEL.md` V2.9/V2.10 — bila berbeda, `DATA_MODEL.md` yang berlaku):

| Migrasi | Tabel / perubahan | Kolom kunci | Constraint & index utama |
|---|---|---|---|
| 0007 | `work_items` | `project_id`, `type` (`program`/`epic`/`story`), `parent_id`, `key`, `status` (7 nilai; ditulis hanya `apply_rollup`), `mandatory`, `repo_aliases`, `phase_overrides`, `version` | UNIQUE `(project_id, key)`; CHECK `status <> 'closed' or closed_reason is not null`; CHECK `parent_id <> id`; constraint trigger tipe induk (epic → program, story → epic); index `(project_id, type, status)`, `(parent_id)` |
| 0007 | `work_item_links` | `work_item_id`, `link_type`, `target_type`, `target_id`/`target_ref`, `sdlc_phase` | UNIQUE `(work_item_id, link_type, target_type, target_ref)`; index `(target_type, target_id)` |
| 0007 | `merge_queue_entries` | `repository_id`, `source_type`, `task_id`, `approval_id`, `approved_head`, `approved_patch_id`, `candidate_patch_id`, `base_head`, `result_commit`, `position`, `status`, `failure_code`, `ci_required`, `ci_skip_reason`, `post_merge_ci` (K13) | CHECK `source_type <> 'task' or (task_id and approval_id not null)`; CHECK `status <> 'merged' or result_commit is not null`; UNIQUE parsial `(repository_id, source_branch)` untuk status aktif; index `(repository_id, status, position)`; FK `approval_id` `ON DELETE RESTRICT` |
| 0007 | `releases` · `release_items` | `version`, `status`, `changelog`, `target_environment_id` · `item_type`, `work_item_id`, `repository_id`, `commit` | UNIQUE `(project_id, version)`; FK melingkar ke `environments` `DEFERRABLE INITIALLY DEFERRED` · CHECK bentuk per `item_type`; UNIQUE parsial story dan `repo_version` per release |
| 0007 | `environments` | `name`, `kind` (`dev`/`staging`/`production`), `requires_approval`, `current_release_id` | UNIQUE `(project_id, name)`; CHECK `kind <> 'production' or requires_approval` |
| 0007 | `ci_check_results` | `repository_id`, `commit_sha`, `check_name`, `status`, `conclusion`, `source` | UNIQUE parsial `(repository_id, external_id)`; index `(repository_id, commit_sha)` |
| 0007 | `terminal_sessions` | `project_id`, `user_id` (manusia), `target`, `task_id`, `status`, `close_reason`, `max_duration_s`, `idle_timeout_s`, `transcript_artifact_id` | index parsial `(user_id, project_id)` untuk status `pending`/`active` (batas sesi); index `(project_id, status)`; tiket WS **tidak** di DB |
| 0007 | perubahan tabel lama | `tasks.work_item_id`; `test_runs.initiated_by` (+`initiated_by_user_id`, `selector`, `target`, `head_commit`, `merge_queue_entry_id`, `preview_url`, suite `run`); `agent_runs.work_item_id`; `requirements`/`acceptance_criteria.work_item_id`; `deployments.environment_id`/`release_id` | CHECK `initiated_by <> 'user' or initiated_by_user_id is not null`; CHECK `num_nonnulls(task_id, thread_id, work_item_id) = 1` (`agent_runs`); CHECK `num_nonnulls(task_id, work_item_id) = 1` (`acceptance_criteria`); `approvals.action_type` **tidak berubah** |
| 0008 | `organizations` · `org_memberships` | `slug`, `is_default`, `settings` · `organization_id`, `user_id`, `org_role`, `source`, `sso_connection_id`, `external_subject`, `scim_external_id` | UNIQUE `slug`; UNIQUE parsial `is_default` (tepat satu org default) · UNIQUE `(organization_id, user_id)`; UNIQUE parsial `(sso_connection_id, external_subject)` (T22: cocokkan subjek, bukan email); UNIQUE parsial `(organization_id, scim_external_id)` |
| 0008 | `sso_connections` · `scim_tokens` | `protocol`, `issuer`, `client_id`, `client_secret_ref` (**nama** secret), `metadata_url`, `group_role_map`, `jit_provisioning` (default false), `enforce_sso` · `token_hash` (sha256), `token_prefix`, `expires_at`, `revoked_at` | CHECK `protocol <> 'oidc' or (issuer and client_id not null)`; CHECK `protocol <> 'saml' or metadata_url is not null` · UNIQUE `token_hash`; token mentah tidak pernah disimpan |
| 0008 | `executor_nodes` · `node_leases` | `status`, `capacity_slots`, `sandbox_runtime`, `bootstrap_token_hash`, `token_hash`, `previous_token_hash`, `token_expires_at`, `last_heartbeat_at` · `node_id`, `task_id`, `epoch`, `status`, `expires_at` | UNIQUE `name`, `token_hash`, `bootstrap_token_hash`; CHECK `capacity_slots > 0`; index `(status, last_heartbeat_at)` · UNIQUE parsial `(task_id) where status = 'active'`; UNIQUE `(task_id, epoch)` (fencing); index `(status, expires_at)` |
| 0008 | `project_repositories` · `cost_budgets` | `alias`, `is_primary`, `checkout_path`, `sparse_patterns` · `scope_type`, `scope_id`, `period`, `limit_usd`, `action_on_exceed`, `status` | UNIQUE `(project_id, alias)`, `(project_id, repository_id)`, parsial primer per project · UNIQUE `(scope_type, scope_id, period)`; CHECK `limit_usd > 0`; pemakaian dihitung dari ledger (`usage_records` + `capability_usage`), tidak disimpan |
| 0008 | perubahan tabel lama | UNIQUE `repositories.project_id` dilepas (+ backfill `project_repositories` primer); `tasks.project_repository_id`, `tasks.organization_id`; `organization_id` di tabel domain; `users.password_hash` nullable + `auth_source`; `sessions.organization_id`, `auth_method`; `terminal_sessions.node_id` | CHECK `auth_source <> 'local' or password_hash is not null`; backfill org default per batch lalu `NOT NULL` (kecuali `audit_events`, `usage_records`, `capability_usage`: nullable, `NULL` = org default) |

Kebijakan sama dengan §7.0 (enum `VARCHAR + CHECK`, `NOT VALID` + `VALIDATE`, kolom baru nullable/berdefault konstan, `downgrade()` diuji). Backfill `organization_id` dilakukan per batch 10.000 baris [Menebak] di migrasi data terpisah; `audit_events` tidak di-backfill (trigger menolak UPDATE) — `NULL` = organisasi default. Kolom rinci: `DATA_MODEL.md` V2.9 (0007) dan V2.10 (0008).

### 13.6 Keamanan implementasi v2.1

| Ancaman | Kontrol desain | Bagian |
|---|---|---|
| T19 terminal | default off (global + per project oleh admin); container terpisah per sesi, network none, limit sandbox, gVisor wajib tier M; tiket WS sekali pakai (TTL 30 dtk, hash, terikat sesi) + cek Origin; durasi/idle; batas frame & laju; rate limit sesi; transcript teredaksi; tanpa `docker exec` | §13.3.8, §13.4.3 |
| T20 lintas tenant | `OrgScope` + global criteria; 404; kunci Redis `kk:org:<id>:`; prefix workspace store per org; WS/SSE ditutup saat deprovision | §13.3.11 |
| T21 node | token bootstrap sekali pakai → token node ter-hash & terotasi; mTLS [Kemungkinan Besar]; lease ber-epoch (fencing); URL workspace terikat lease; tanpa pem GitHub/secret IdP di node; drain & revoke; attestation image (tier L, WP-14.0) | §13.1, §13.3.9 |
| T22 SSO/SCIM | validasi tanda tangan/iss/aud/nonce/state/PKCE; SAML strict + anti replay; JIT off; pemetaan grup eksplisit tanpa owner; token SCIM ter-hash + compare_digest | §13.3.11 |
| T23 bypass merge queue | `advance_target_ref` hanya dengan `MergeQueueLease`; git manual/apply lama menolak default branch saat merge queue aktif; rekonsiliasi berkala commit ↔ entri `merged` (alert bila ada commit tanpa entri) | §13.3.4 |

**Pengecualian WebSocket (ADR-014 → ADR-059):** hanya route `GET /api/v1/terminal-sessions/{id}/ws`; CSRF header tidak tersedia pada handshake WS sehingga digantikan tiket sekali pakai yang diterbitkan `POST` ber-CSRF + cek `Origin` (anti *cross-site WebSocket hijacking*); Caddy `reverse_proxy` meneruskan upgrade [Kemungkinan Besar perilaku default Caddy 2] dan log akses wajib meredaksi query `ticket`; CSP `connect-src` menambah `wss://{APP_DOMAIN}`; tidak ada WS lain di SPA (lint: hanya `api/ws/terminal.ts` yang boleh `new WebSocket`).

### 13.7 Rencana test AX-12..AX-18

| AX | Test | Lokasi (rencana) |
|---|---|---|
| AX-12 | operasi file + version check (409 + Merge3View); undo dengan version check; take-over: worker berhenti di batas langkah, gateway menolak tulisan agent, bukti stale → apply 409 `EVIDENCE_STALE`, validasi ulang memulihkan; E2E edit/konflik/take-over | `tests/integration/test_workspace_edits.py`, `tests/integration/test_takeover.py`, `tests/security/test_takeover_gateway.py`, `tests/e2e/workspace-v2.spec.ts` |
| AX-13 | run `run.*` + preview; test semua/file/satu (`initiated_by=user`); isolasi terminal (network unreachable, rootfs read-only, PID/CPU/RAM, durasi & idle, tiket dipakai ulang → ditolak, Origin asing → ditolak, transcript tanpa secret sintetis) pada runc dan runsc | `tests/integration/test_user_runs.py`, `sandbox_manager/tests/test_terminals_docker.py` (`-m docker`), `tests/security/test_t19_terminal.py`, `tests/e2e/run-test-terminal.spec.ts` |
| AX-14 | program kecil (≥ 5 task [Menebak]) paralel di worktree terpisah; DAG dihormati; merge queue: konflik → needs_changes tanpa perubahan target; patch-id berubah → reapproval; profil gagal → target tak berubah; CAS head berpindah; rekonsiliasi NFR-27; CI (WP-13.12): sah hanya untuk head ter-push + rebase fast-forward, selain itu `waiting_ci` dilewati dengan `ci_skip_reason`; CI pasca-merge gagal → story perbaikan + event `merge_queue.post_merge_ci_failed`; timeout CI tidak pernah memblokir selamanya | `tests/integration/test_dag_scheduler.py`, `tests/integration/test_merge_queue.py`, `tests/integration/test_mq_ci_rule.py`, `tests/security/test_t23_merge_queue_bypass.py`, `tests/e2e/program-small.spec.ts` |
| AX-15 | ekspor traceability JSON/CSV/PDF memuat rantai lengkap untuk release fixture; tidak ada tautan tanpa rekaman | `tests/integration/test_traceability_export.py` |
| AX-16 | tambah node tanpa ubah kode (compose node kedua); chaos: kill node saat running → resume ≤ 5 menit [Menebak] di node lain, 0 tool_call duplikat (idempotency_key), epoch basi ditolak; checksum bundle rusak → tidak dilanjutkan | `tests/chaos/test_node_failover.py`, `tests/integration/test_node_placement.py`, `tests/unit/cluster/test_fencing.py` |
| AX-17 | **Tier M, G13 (WP-13.8):** OIDC dengan IdP mock + pemetaan grup → role + login lokal dapat dimatikan. **Tier L, G14 (WP-14.1..14.5):** SAML dengan IdP mock (tanda tangan salah, aud salah, nonce/InResponseTo replay, XSW fixture → ditolak); SCIM deprovision → sesi/WS/SSE dicabut ≤ 5 menit [Menebak]; cross-tenant parametrized (404) atas semua endpoint; laporan DR drill; verifikasi rantai hash ekspor SIEM | `tests/security/test_t22_sso.py`, `tests/integration/test_scim.py`, `tests/security/test_t20_cross_tenant.py`, `docs/RUNBOOK.md` (DR drill) |
| AX-18 | benchmark repo ≥ 1 juta LOC [Menebak]: waktu index inkremental & pemilihan konteks vs ambang PO; konteks agent hanya file relevan (ukuran ≤ batas ADR-011) | `backend/evals/scale/large_repo/**` (WP-14.7) |

Unit tambahan: `test_rollup_properties.py` (property-based `compute_status`), `test_program_plan_validator.py` (siklus, kunci ganda, repo tak dikenal), `test_tier_validator.py`, `test_terminal_protocol.py` (frame, ukuran, laju), `test_patch_id_identity.py`. CI gate tambahan: grep `advance_target_ref` hanya di `app/merge_queue/`; grep `new WebSocket` hanya di `frontend/src/api/ws/terminal.ts`; `lint-imports` v2.1.

### 13.8 Work package → pemilik, path, DoD, gate (K8)

Pemilik & reviewer mengikuti K8; kolaborator di luar K8 ditandai "delegasi Lead".

| WP | Pemilik (+kolaborator) | Path milik (tulis) | Input wajib | Definition of Done | Gate / reviewer |
|---|---|---|---|---|---|
| WP-13.0 Kunci kontrak v2.1 | AG-00 (+AG-01 migrasi) | `docs/DECISIONS.md` (ADR-057..062), `app/sdlc/types.py`, `app/agents/contracts.py` (`ProgramPlan`, planned), `app/cluster/tier.py`, `app/models/sdlc.py`, `app/models/{cluster,identity}.py` (skema saja, delegasi AG-01), `alembic/versions/0007_sdlc.py`, `alembic/versions/0008_scale.py` (*schema-first*, K13 OI-27), `scripts/validate_registry.py` + loader registry (nilai `phase` P13/P14, K13 OI-31), `core/rbac.py` (izin v2.1), `core/config.py` (variabel K11), `pyproject.toml` (import-linter) | SSD §6; §13 | suite lama hijau; 0007 **dan** 0008 upgrade/downgrade diuji; tabel tier L inert; registry 53 entri tetap lulus validator; tier S tidak mengubah perilaku pilot; `lint-imports` hijau | AG-13; PO |
| WP-13.1 Workspace v2 | AG-09, AG-10, AG-02 | AG-09: `services/{workspace_edits,takeover}.py`, routes `files.py`/`tasks.py` (takeover) · AG-02: `workspace/{edits,merge3,takeover}.py`, `gitops/adapter.py` (merge-file, worktree user, guard target ref) · AG-10: `features/workspace/**` · delegasi Lead: transisi ADR-058 di `workflow/states.py` + cek kesegaran bukti S9 | §13.3.5–13.3.6, §13.4.1 | AX-12; NFR-23 diukur; gateway menolak tulisan saat take-over | AG-13 |
| WP-13.2 Run & test + format/lint | AG-03, AG-10 | AG-03: `sandbox-images/sandbox-profiles.yaml` (run.*, *.test_select, fmt.*, lint.*_file), `app/runs/**`, `services/runs.py`, `tools/builtin/tests_select.py`, `sandbox_manager/…/validation.py` · AG-10: `features/workspace/{RunPanel,TestSelector,FormatPatchDialog}` | §13.3.7 | AX-13 (run/test); preview ADR-008; `initiated_by=user` | AG-08 |
| WP-13.3 Terminal sandbox 🔒 | AG-03, AG-10 | AG-03: `app/terminal/**`, `routes/terminal.py`, `sandbox_manager/…/terminals.py` · AG-10: `features/terminal/**`, `api/ws/terminal.ts` · delegasi Lead: AG-06 untuk redaksi `ticket` di log Caddy | §13.3.8, §13.4.3 | AX-13 (terminal); suite T19 lulus di runc & runsc; default off terbukti | AG-13 🔒 |
| WP-13.4 Work item, A03, papan SDLC | AG-08, AG-10 | AG-08: `app/sdlc/{work_items,plan,rollup,scheduler,board}.py`, `services/sdlc.py`, `routes/work_items.py`, `worker/sdlc_jobs.py`, `tools/builtin/sdlc.py`, `agents/prompts/A03.md` (bagian ProgramPlan) · AG-10: `features/sdlc/**` (kecuali dashboard/ekspor) | §13.3.1–13.3.3 | AX-14 (paralel), AX-15 (baseline story & papan SDLC); property test roll-up; plan tidak membuat task tanpa persetujuan | AG-00 |
| WP-13.5 Merge queue lokal | AG-08 | `app/merge_queue/**` (tanpa langkah CI), `worker/mq_jobs.py`, handler `apply_changes` → enqueue, `routes/merge_queue.py`, `tools/builtin/merge_queue.py`, `gitops/adapter.py::advance_target_ref` (guard T23, bersama AG-02) | WP-13.4; §13.3.4 langkah 1–3, 5–6; §13.4.2 | AX-14 (merge queue lokal, **tanpa** G9); NFR-27 rekonsiliasi; test T23 | AG-13 |
| WP-13.6 Traceability + dashboard | AG-09, AG-11 | AG-09: `app/sdlc/traceability.py`, route ekspor, job PDF (media) · AG-11: `features/sdlc/{ProgramDashboard,TraceabilityExport}` | API §13.1 | AX-15 | AG-08 |
| WP-13.7 Pool executor tier M 🔒 | AG-06, AG-03, AG-01 | AG-06: `infra/compose.node.yml`, listener internal Caddy, RUNBOOK node · AG-01: `app/cluster/{registry,placement,leases}.py`, `services/cluster_ops.py`, `app/budgets/**` (penegakan budget program/epic, K13 OI-28), routes `admin_nodes.py`/`internal_nodes.py`/`capacity.py` (logika node/lease saja; skema 0008 sudah di WP-13.0) · AG-03: `app/cluster/workspace_store.py`, mode node sandbox-manager | §13.3.9–13.3.10, §13.4.4 | AX-16 (chaos); NFR-25; FR-255 (budget program aktif) & FR-258 | AG-13 🔒 |
| WP-13.8 SSO OIDC 🔒 | AG-01, AG-11 | AG-01: `app/identity/sso/{oidc,mapping,jobs}.py`, `app/identity/org_scope.py` (kerangka, org default), routes `auth_sso.py`/`admin_sso.py` · AG-11: `features/admin/sso/**`, tombol SSO di Login | §13.3.11 | **AX-17 bagian OIDC (G13)**: test OIDC (IdP mock) lulus; JIT off; login lokal dapat dimatikan dengan break-glass | AG-13 🔒 |
| WP-13.9 Repo besar & multi-repo | AG-02, AG-07 | AG-02: `gitops/adapter.py` (sparse, partial clone, bundle), `services/project_repositories.py`, `workspace/index.py` (inkremental) · AG-07: `tools/builtin/index.py`, batas konteks A04 | §13.3.10 | AX-14 (multi-repo, FR-254); FR-253/254 teruji; persiapan benchmark AX-18 | AG-00 |
| WP-13.10 Release + environment | AG-08, AG-14 | AG-08: `app/sdlc/{releases,environments,codeowners,incidents}.py`, routes `releases.py`/`environments.py` · AG-14: promosi staging (deployments), RUNBOOK rilis | API §13.2 | AX-15 (release); production tetap R3 | AG-00 |
| WP-13.11 Aktivasi agent SDLC tier M (A05, A06, B12, C18, C19, E28, F30, F32) | AG-15 | `agents/registry.v2.yaml` (versi berikutnya, `phase: P13`), `agents/prompts/{A05,A06,B12,C18,C19,E28,F30,F32}.md`, `backend/evals/sdlc_agents/**` | AGENT_CATALOG §10, §13 | 8 kriteria aktivasi per agent; registry tetap 53 | PO |
| WP-13.12 Status CI GitHub sebagai syarat merge queue | AG-18, AG-08 | AG-18: `tools/builtin/ci.py`, `integrations/github/checks.py`, webhook `check_suite`/`check_run` → `ci_check_results` · AG-08: langkah `waiting_ci` + aturan head ter-push & fast-forward di `app/merge_queue/checks.py`, `watch_post_merge_ci` (story perbaikan via jalur FR-234, event `merge_queue.post_merge_ci_failed`) | WP-13.5, WP-G9; §13.3.4 langkah 4, §13.4.2 | AX-14 (CI); CI tidak pernah memblokir tanpa batas (timeout + skip non-FF); test CI gagal pasca-merge → story | AG-13 |
| WP-G13 | PO + AG-13 | – | WP-13.1..13.12 | AX-12..AX-16 + AX-17 (OIDC); program kecil end-to-end tier M dengan merge queue & traceability | PO |
| WP-14.0 ADR orkestrator tier L | AG-00 + PO | `docs/DECISIONS.md` | data beban WP-13.7 | ADR: VM pool vs Kubernetes; API node vs DB langsung (OI-32) | AG-13 |
| WP-14.1 Organisasi/tenant 🔒 | AG-01, AG-09 | AG-01: `app/identity/org_scope.py` (penuh), `app/models/identity.py` (delegasi), `routes/orgs.py`, backfill · AG-09: scope di `services/*` | §13.3.11 | AX-17 (404 lintas org), NFR-26 | AG-13 🔒 |
| WP-14.2 SAML + SCIM 🔒 | AG-01 | `app/identity/sso/saml.py`, `app/identity/scim/**`, `routes/scim.py` | §13.3.11, API §13.8 | AX-17 (SAML, SCIM ≤ 5 menit [Menebak]) | AG-13 🔒 |
| WP-14.3 microVM 🔒 | AG-03 | `sandbox_manager/…/drivers/microvm.py`, profil `runtime_class` | ADR WP-14.0 | suite isolasi lulus di microVM dengan profil sama | AG-13 🔒 |
| WP-14.4 HA & DR | AG-06 + OPS | `infra/` (replika PostgreSQL, Redis HA), `scripts/backup.sh` (enkripsi), RUNBOOK DR | – | DR drill memenuhi RPO/RTO [Menebak] | AG-13 |
| WP-14.5 SIEM + biaya org | AG-01, AG-11 | `app/core/audit_export.py` (rantai hash), `app/budgets/**` (org), UI budget org | – | AX-17 (rantai hash SIEM): ekspor SIEM terverifikasi hash; FR-255 org | AG-13 |
| WP-14.6 LSP + debugger | AG-03, AG-10 | `sandbox-images/lsp/**`, `app/lsp/**`, `features/workspace/{Lsp,Debugger}` | ADR kanal LSP/DAP (OI-25) | FR-242/243 di sandbox | AG-13 |
| WP-14.7 Benchmark | AG-12 | `backend/evals/scale/**`, `tests/load/**` | WP-13.9, WP-14.0 | laporan AX-18, NFR-24 | PO |
| WP-14.8 Aktivasi agent tier L (B08, B11, B15, D24, D25, D26, F31, F33, F34, G36, H38–H40) | AG-15 | registry & prompt B08, B11, B15, D24, D25, D26, F31, F33, F34, G36, H38–H40 | AGENT_CATALOG §13 | kriteria §10 per agent | PO |
| WP-G14 | PO + AG-13 | – | WP-14.1..14.8 | AX-17, AX-18; sign-off keamanan; DR drill | PO |

> *Amandemen K13 (Lead, OI-35):* WP-13.5 kini hanya merge queue lokal (AG-08, tidak menunggu G9); integrasi status CI GitHub (`ci.get_checks`, J48, webhook checks, CI pasca-merge) dipindah ke **WP-13.12** (AG-18 + AG-08, setelah WP-13.5 dan G9). Penyelarasan AX ↔ WP (OI-30): AX-14 = WP-13.4, 13.5, 13.9, 13.12; AX-15 = WP-13.4, 13.6, 13.10; AX-17 = WP-13.8 (OIDC, G13) + WP-14.1..14.5 (G14).

### 13.9 Risiko & keputusan terbuka v2.1

| ID | Risiko | Mitigasi | Pemilik |
|---|---|---|---|
| RV-17 | Merge queue menjadi bottleneck (RS-02) | ukur waktu antre di G13; batching entri yang tidak bersinggungan path (pasca G13) | AG-08 |
| RV-18 | docker-socket-proxy tidak mendukung hijack attach | uji dini di WP-13.3; alternatif: endpoint attach terbatas di proxy dengan ADR, tanpa membuka `EXEC` | AG-03, AG-13 |
| RV-19 | Biaya model naik linear dengan task paralel (RS-04) | `cost_budgets` + kill switch; ETA & biaya tampil di dashboard | AG-01, PO |
| RV-20 | Node tier M memegang kredensial DB (OI-32) | role `kk_node` minimal, TLS, revoke cepat; keputusan tier L WP-14.0 | AG-06, AG-13 |
| RV-21 | Bundle git besar untuk monorepo | sparse + bundle inkremental; shared volume untuk repo sangat besar | AG-02 |
| RV-22 | Kerentanan XML signature (SAML) | library terawat, mode strict, fixture XSW di test | AG-01, AG-13 |
| RV-23 | Backfill `organization_id` pada tabel besar | migrasi data per batch; `audit_events` tidak di-backfill | AG-01 |
| RV-24 | Badai roll-up pada program besar | debounce per item, roll-up inkremental ke parent | AG-08 |
| RV-25 | Transcript terminal memuat secret yang diketik user | redaksi pola + literal; retensi 30 hari; akses terbatas; risiko sisa diterima PO | AG-03, PO |

| ID | Keputusan | Pemutus | Rekomendasi |
|---|---|---|---|
| D-16 | Mode branch kerja user (OQ-02, OI-20) | PO | **Diputuskan K13:** `kk/user-*` → merge queue bila aktif (default tier M/L); commit langsung hanya bila merge queue repo nonaktif (opsi tier S) — menunggu konfirmasi PO |
| D-17 | Semantik CI di merge queue (OI-23) | PO + Lead | **Diputuskan K13:** CI sah hanya untuk head ter-push + rebase fast-forward; selain itu profil lokal wajib; CI pasca-merge gagal → story perbaikan otomatis |
| D-18 | Transisi take-over (OI-22) | Lead (ADR-058) | **Diputuskan K13:** daftar §13.3.5 (dari `running`/`waiting_approval`/`needs_changes`/`failed`; selesai → validasi saja) |
| D-19 | Penyimpanan run user (OI-24) | Lead | **Disetujui K13:** `test_runs.suite = 'run'` |
| D-20 | Egress di terminal | PO | **tidak ada** di v2.1 (network none); allowlist hanya lewat ADR baru |
| D-21 | Kanal LSP/DAP (OI-25) | Lead + AG-13 | HTTP + SSE; WS kedua hanya lewat ADR |
| D-22 | `NODE_DEAD_AFTER`, grace rotasi token, TTL bootstrap | Lead + OPS | 90 dtk / 10 menit / 1 jam [Menebak] sebagai konstanta kode sampai diukur |
| D-23 | Fase SDLC `not_applicable` | PO | diizinkan untuk design/run/operate dengan alasan teraudit |

---

## 14. Desain v2.2 — Polyglot language packs, lingkungan integrasi multi-service, monitoring biaya & token realtime

> **Status: Rancangan (Proposed) — belum diimplementasikan.** Disusun 22 Okt 2026. **Kontrak mengikat:** `docs/POLYGLOT_INTEGRATION_DESIGN.md` (PID) §7 **KONTRAK v2.2** — K14 (M28–M30, FR-259..FR-292), K15 (AX-19..AX-24), K16 (NFR-28..NFR-31), K17 (ADR-063..068, *Proposed*), K18 (fase/gate/WP), K19 (data/API/konfigurasi), K20 (T24–T28); fakta tingkat pack L1/L2, aturan lingkungan integrasi, sumber data monitoring (termasuk fakta Usage & Cost Admin API), dan perubahan aktivasi agent di PID §1–§4. KONTRAK v2.1 (SSD §6 + K13) dan KONTRAK v2 (PE §9b) tetap berlaku. Bila bagian ini bertentangan dengan K14–K20, kontrak yang berlaku; temuan dicatat di §14.0.3 dan `SYSTEM_SPEC.md` §13.9.
> **Prinsip lama tetap mengikat:** satu pintu side effect agent = `ToolGateway`; tidak ada eksekusi kode project di api/worker (build, test, validasi/diff kontrak, parse laporan berisiko — semua di sandbox); status task hanya lewat `workflow/states.py::transition`; approval hanya user; secret tidak pernah di prompt/log/event/artefak/sandbox; hanya `app/llm/anthropic_provider.py` yang `import anthropic`. Angka waktu/kapasitas di bagian ini **[Menebak]** kecuali disebut lain.

### 14.0 Ringkasan, prinsip tambahan, dan temuan

#### 14.0.1 Ringkasan

1. **M30 monitoring (P6, +P10):** provider meneruskan `usage` dari `message_start` dan `message_delta` (kumulatif) sebagai `StreamEvent` baru `UsageProgress`; `app/usage/` mencatat **satu** `usage_records` (atau `capability_usage` untuk vendor/unit) + **satu** `usage_events` per panggilan, meng-upsert `usage_rollups_minute` di transaksi yang sama, lalu menerbitkan `usage.event` lewat Redis pub/sub → SSE `GET /usage/live`. `BudgetAlerter` menulis `budget_alerts` pada ambang 50/80/100%. `UsageReconciler` (hanya `worker-integrations`) menarik Usage & Cost Admin API (usage bucket `1m`, cost `1d`, polling ≥ 60 dtk) ke `usage_reconciliation`.
2. **M28 language packs (P15):** pack = **data** (`config/language-packs/<pack>.yaml`, manifest tervalidasi) → `LanguagePackRegistry`; `StackDetector` (H41, polyglot/monorepo) → `project_stacks`; `PackProfileRunner` menggantikan `profiles_for_stack` v1; `TestReportNormalizer` → JUnit XML; `DependencyProxyConfig` + container `dep-proxy` per ekosistem di network `kk-egress`. `sandbox-profiles.yaml` tetap satu-satunya daftar profil runtime sandbox-manager, **dibangkitkan** dari manifest (cek CI diff = 0) sehingga ADR-009 "daftar tetap" tidak berubah.
3. **M29 integrasi (P16):** `app/integration/` (katalog service, `ContractChecker`, `IntegrationEnvSpec` + `EnvSpecPolicy`, orkestrasi env) dan perluasan sandbox-manager `IntegrationEnvRunner` (`/v1/envs*`, network internal per env, katalog service pendukung terkunci); merge queue mendapat langkah `contract_check` dan `integration_e2e` (FR-273, FR-282).
4. Dua migrasi: **0009_polyglot_usage** (usage + pack) dan **0010_integration** (katalog, kontrak, env). Karena tabel usage dibutuhkan di P6 — sebelum 0003..0008 — urutan landing mengikuti §14.5 (OI-38).

#### 14.0.2 Prinsip v2.2 (melanjutkan P1–P25)

| # | Prinsip | Konsekuensi teknis |
|---|---|---|
| P26 | **Token dari provider, biaya dari tabel tarif, angka resmi dari Admin API** | Token hanya dari `usage` stream/respons provider; model tidak pernah menulis angka usage; biaya realtime = estimasi berversi (`pricing_version`); biaya `resmi` hanya dari `usage_reconciliation`; `usage_records.cost_status` tidak pernah `actual` (ADR-025) |
| P27 | **Satu panggilan = satu baris ledger + satu `usage_events`** | `usage_events.call_id` UNIQUE; rollup diturunkan dan dapat dibangun ulang; tidak ada baris dihitung dua kali (aturan ledger V2.2) |
| P28 | **Pack = data, bukan cabang kode** | Manifest YAML tervalidasi; kode domain tidak bercabang `if language == …` di luar adapter format laporan yang dipilih manifest |
| P29 | **Dependensi hanya lewat proxy, hanya di `*.deps_install`** | Profil lain `network_mode=none`; lockfile wajib; namespace terpin; daftar blokir (T24) |
| P30 | **Env = template tetap tervalidasi, divalidasi dua kali** | `EnvSpecPolicy` di api (umpan balik cepat) **dan** validasi ulang otoritatif di sandbox-manager; HostConfig dan network selalu dibangun sandbox-manager (ADR-065 memperluas ADR-009) |
| P31 | **Kredensial admin di satu tempat dengan allowlist path** | `ANTHROPIC_ADMIN_API_KEY` hanya di `worker-integrations`; `AdminUsageClient` hanya mengenal dua path GET usage/cost; header & body tidak pernah dilog (T26) |

#### 14.0.3 Temuan terhadap kontrak & snapshot (resolusi di desain ini)

| # | Temuan | Resolusi desain | Rujukan |
|---|---|---|---|
| 1 | Snapshot `anthropic_provider._stream_once` hanya meneruskan delta teks; `usage` hanya dibaca dari pesan final [Pasti — kode] | Tambah `UsageProgress` ke `StreamEvent` (§5.0.1, kompatibel-mundur); provider memancarkan fase `start`/`delta`/`final`; nilai `message_delta` **ditimpa** per field (kumulatif [Pasti — dokumentasi Streaming]), tidak dijumlahkan | §14.3.8 |
| 2 | `core/redaction.py` sudah memuat pola `sk-ant-[A-Za-z0-9_\-]{10,}` yang juga menutup `sk-ant-admin01-…` [Pasti — kode] | Tetap; ditambah pola berlabel eksplisit (urutan sebelum pola umum) + pencocokan literal `ANTHROPIC_ADMIN_API_KEY` + test khusus (T26) | §14.6, `SECURITY_MODEL.md` Lampiran v2.2 |
| 3 | Tabel usage (0009) dibutuhkan di P6, sedangkan 0003..0008 baru di-land P9–P13; kolom `organization_id`/`thread_id`/`project_repository_id`/`capability_usage_id` merujuk tabel yang belum ada | 0009 di-land *schema-first* di WP-6.9 dengan `down_revision` = revisi terakhir saat itu; kolom rujukan dibuat **tanpa FK**, FK ditambahkan migrasi pemilik tabel (0003/0008); tabel pack inert sampai WP-15.0 *(turunan, OI-38)* | §14.5 |
| 4 | WP-6.10 (P6) mensyaratkan `worker-integrations`, peran yang baru ada di mode *split* P9 (ADR-053) | WP-6.10 memperkenalkan container `worker-integrations` lebih awal: antrean `integrations`, hanya job rekonsiliasi, `KK_PROCESS_ROLE=worker-integrations`; validator role menolak kunci admin di role lain *(turunan, OI-40)* | §14.1 |
| 5 | ADR-009: `docker-socket-proxy` `NETWORKS=0`; ADR-065 butuh network per env | `NETWORKS=1` **hanya** pada socket proxy node executor tier M/L saat `INTEGRATION_ENV_ENABLED=true`; sandbox-manager hanya membuat network `internal: true` berlabel `kk.env_id`, tanpa opsi dari caller; tidak pernah `host`/`bridge` default untuk env *(turunan, OI-41; sign-off AG-13 WP-16.0)* | §14.3.6 |
| 6 | `PRICING_TABLE_PATH` (K19) vs `PRICING_PATH` (v1) untuk file yang sama | **Diputuskan K21:** hanya `PRICING_PATH`; `PRICING_TABLE_PATH` tidak diperkenalkan | `CONFIGURATION.md` Variabel v2.2 |
| 7 | `usage_events` ber-FK `usage_records` (K19), tetapi panggilan vendor/unit tercatat di `capability_usage` | `usage_events.usage_record_id` **atau** `capability_usage_id`, CHECK tepat satu *(turunan, OI-43)* | §14.5 |
| 8 | Pack SQL (WP-15.9, P15) menguji terhadap DB ephemeral, padahal env runner baru ada di P16 | P15: image `sandbox-sql` memuat server PostgreSQL & MySQL yang dijalankan sebagai proses lokal di container yang sama (network none, socket lokal); SQL Server dev & skenario multi-DB lewat env runner P16 (WP-16.4) *(turunan)* | §14.3.3 |
| 9 | `repo.detect_stack` v1 mengembalikan satu `Stack` | `StackReport.primary` mempertahankan bentuk v1; `stacks[]` per path untuk polyglot/monorepo (kompatibel-mundur) | §14.3.2 |
| 10 | Admin API melaporkan usage **seluruh organisasi**; bila kunci organisasi dipakai sistem lain, selisih palsu muncul | Rekonsiliasi memakai filter `api_key_ids[]`/`workspace_ids[]` endpoint usage [Pasti — dokumentasi Usage and Cost API; K21 OI-51] dengan ID kunci KodeKantor yang dicatat saat readiness; tanpa filter → panel menandai "lingkup organisasi" dan ambang selisih tidak dipakai untuk alert *(turunan; D-26)* | §14.3.10 |
| 11 | D24 (P15) butuh tool audit dependensi di luar K10/K19 | Usulan `deps.audit` *(turunan)* di `TOOL_CATALOG.md` v2.2; tanpa itu D24 tidak dapat diaktifkan (OI-47) | §14.3.11 |

### 14.1 Penempatan komponen v2.2

```mermaid
flowchart LR
  subgraph CTRL["Node kontrol"]
    api["api - REST, SSE usage live, SSE env"]
    rd[("Redis pub/sub usage dan env")]
    pg[("PostgreSQL - usage, pack, katalog, env")]
    beat["scheduler - rollup rebuild, sweep env, pengingat rebuild image"]
    wint["worker-integrations - UsageReconciler, satu-satunya kunci admin"]
    wide["worker-ide - agent run, UsageEventPublisher"]
    dproxy["dep-proxy - proxy repositori per ekosistem"]
  end
  subgraph NODE["Node executor tier M atau L"]
    sm["sandbox-manager - profil pack dan EnvRunner"]
    dsp["docker-socket-proxy - NETWORKS hanya bila env aktif"]
    sbx[["sandbox profil pack - network none"]]
    deps[["sandbox deps_install - network kk-egress"]]
    envnet["network env internal tanpa egress"]
  end
  ADM[("Usage and Cost Admin API")]
  UP[("Registry upstream allowlist")]
  wide -->|"publish usage.event"| rd
  wide -->|"ledger dan usage_events"| pg
  rd --> api
  api --> pg
  beat --> pg
  wint -->|"GET usage dan cost"| ADM
  wint --> pg
  wide -->|"Bearer"| sm
  sm --> dsp
  dsp --> sbx
  dsp --> deps
  dsp --> envnet
  deps -->|"hanya proxy"| dproxy
  dproxy -->|"domain allowlist"| UP
```

| Peran | Secret v2.2 yang dipegang | Network | Catatan |
|---|---|---|---|
| api | – (tidak pernah kunci admin) | app | SSE `/usage/live`, `/integration-envs/{eid}/events`; validasi awal `EnvSpecPolicy` |
| worker-ide / node | `ANTHROPIC_API_KEY` (sudah ada) | app (+ outbound Claude) | `UsageEventPublisher` di proses yang memanggil provider |
| worker-assistant / worker-media | kunci yang sudah ada (ADR-053) | idem | `record_final(units=…)` untuk server tool & vendor (WP-10.7) |
| worker-integrations | **`ANTHROPIC_ADMIN_API_KEY`** (secret file, opsional) + secret GitHub yang sudah ada | outbound | Hanya `AdminUsageClient` yang membaca kunci; muncul sejak WP-6.10 (OI-40) |
| dep-proxy | – (tanpa kredensial upstream di v2.2) | `kk-egress` (masuk dari sandbox) + outbound (upstream allowlist) | Produk: OQ-04/OI-49; cache volume terbatas (T27) |
| sandbox-manager node | token sandbox-manager lokal | exec | `NETWORKS=1` di socket proxy hanya bila `INTEGRATION_ENV_ENABLED=true` (OI-41) |
| Container env | secret **palsu** yang dibangkitkan per env | network env `internal` saja | tidak pernah secret nyata (T28) |

Tier S: dep-proxy dan worker-integrations di VPS yang sama; env runner nonaktif (validator tier menolak `INTEGRATION_ENV_ENABLED=true` pada tier S); M29 berjalan sebagai mode kontrak + mock (FR-276).

### 14.2 Struktur paket v2.2

#### 14.2.1 Backend, sandbox-manager, image, config

```
backend/
├── alembic/versions/
│   ├── 0009_polyglot_usage.py      # usage_events, usage_rollups_minute, budget_alerts, usage_reconciliation,
│   │                                #   language_packs, project_stacks — WP-6.9 schema-first (OI-38)          [AG-07 + Lead]
│   └── 0010_integration.py         # service_catalog, service_interfaces, service_dependencies, integration_envs,
│                                    #   integration_env_services, contract_checks — WP-16.0                  [AG-00]
├── app/
│   ├── usage/                      # BARU (M30) [AG-07]
│   │   ├── types.py                # UsageCallContext, UsageEventPayload, FigureStatus, Dim                 [AG-00 kontrak]
│   │   ├── publisher.py            # UsageEventPublisher (progress ephemeral, final ber-ledger)
│   │   ├── rollup.py               # UsageRollup (upsert inkremental, rebuild, burn rate, proyeksi)
│   │   ├── budgets.py              # BudgetAlerter + BudgetDirectory (fasad /budgets, OI-44)
│   │   ├── reconcile.py            # UsageReconciler — HANYA diimpor worker/usage_jobs.py (antrean integrations) [AG-18]
│   │   └── export.py               # ekspor CSV/JSON streaming (FR-291)
│   ├── integrations/anthropic_admin.py   # AdminUsageClient (httpx, allowlist 2 path)                        [AG-18]
│   ├── packs/                      # BARU (M28) [AG-03]
│   │   ├── manifest.py             # LanguagePackManifest + validate_manifest                              [AG-00 kontrak]
│   │   ├── registry.py             # LanguagePackRegistry
│   │   ├── detect.py               # StackDetector (dipakai repo.detect_stack, H41)
│   │   ├── runner.py               # PackProfileRunner (pengganti sandbox/profiles.py::profiles_for_stack)
│   │   ├── reports.py              # TestReportNormalizer (→ JUnit XML)
│   │   └── deps.py                 # DependencyProxyConfig + preflight lockfile/namespace/blocklist
│   ├── integration/                # BARU (M29) [AG-09, AG-08, AG-03]
│   │   ├── spec.py                 # IntegrationEnvSpec (Pydantic, extra=forbid)                          [AG-00 kontrak]
│   │   ├── policy.py               # EnvSpecPolicy (umpan balik awal; aturan sama dengan sandbox-manager)
│   │   ├── envs.py                 # IntegrationEnvService (status, test_runs, teardown, SSE)
│   │   ├── catalog.py              # ServiceCatalog
│   │   ├── contracts.py            # ContractChecker (validasi, diff, mock — eksekusi di sandbox)
│   │   └── seed.py                 # FakeSecretGenerator, SeedPlan (WP-16.4)
│   ├── llm/base.py                 # + UsageProgress (StreamEvent)                                         [AG-00]
│   ├── llm/anthropic_provider.py   # + teruskan usage message_start/message_delta (satu-satunya import anthropic)
│   ├── llm/pricing.py              # PRICING_PATH (satu sumber), + entri server tool & vendor (WP-10.7)
│   ├── merge_queue/checks.py       # + langkah contract_check, integration_e2e (FR-273, FR-282)            [AG-08]
│   ├── services/                   # + usage_live.py, budgets_facade.py, packs.py, integration.py
│   ├── api/routes/                 # + usage_live.py, budgets.py, language_packs.py, services_catalog.py,
│   │                               #   contracts.py, integration_envs.py
│   ├── tools/builtin/              # + packs.py (profil pack), deps.py (install via proxy, audit), envs.py,
│   │                               #   contracts.py, usage.py
│   └── worker/                     # + usage_jobs.py (rebuild rollup, reconcile), env_jobs.py, pack_jobs.py
sandbox_manager/src/sandbox_manager/
├── envs.py                         # IntegrationEnvRunner (/v1/envs*)                                      [AG-03]
├── env_policy.py                   # validasi ulang OTORITATIF (fixture bersama dengan app/integration/policy.py)
├── support_catalog.py              # baca config/support-images.yaml (image@digest, env allowlist, health)
└── validation.py                   # + SUBPATH_ENV_RE ^<uuid>/envs/<uuid>/[a-z][a-z0-9-]{1,30}$ (turunan)
sandbox-images/
├── packs/<pack>/Dockerfile         # jvm, dotnet, go, php, ruby, cpp, react-native, flutter, sql (+python, node lama)
├── sandbox-contracts/Dockerfile    # alat validasi/diff/mock kontrak, versi dipin (turunan)
└── sandbox-profiles.yaml           # DIBANGKITKAN scripts/render_pack_profiles.py dari manifest (--check di CI)
config/
├── language-packs/<pack>.yaml      # manifest pack (§14.3.1)
├── support-images.yaml             # katalog service pendukung terkunci (FR-277)
├── dep-proxy.yaml                  # ekosistem → upstream allowlist, namespace terpin, blocklist (T24)
└── pricing.yaml                    # (ada) + server tool, vendor, versi bertanggal
infra/compose.yml                   # + dep-proxy, + worker-integrations sejak WP-6.10 (OI-40)
infra/compose.node.yml              # + NETWORKS=1 pada docker-socket-proxy node bila env aktif (OI-41)
.github/workflows/packs.yml         # build image pack, SBOM, scan, matriks test per pack (AX-21)
backend/evals/packs/<pack>/         # set evaluasi per bahasa (AX-22)
```

**Import-linter v2.2** (tambahan §4.3/§13.2.1): `app.usage`, `app.packs`, `app.integration` sejajar layer domain v2.1; `app.usage.reconcile` dan `app.integrations.anthropic_admin` hanya boleh diimpor `app.worker.usage_jobs`; `app.packs` tidak mengimpor `app.workflow`; grep CI: `ANTHROPIC_ADMIN_API_KEY` hanya di `core/config.py` dan `integrations/anthropic_admin.py`; `import anthropic` tetap hanya di `llm/anthropic_provider.py`.

#### 14.2.2 Frontend

```
frontend/src/
├── features/usage-live/     # LiveStream, SummaryCards, BurnRateCard, MinuteChart, Breakdown (model/agent/project/task/
│                            #   capability/user, rasio cache), BudgetAlerts (+tautan kill switch), ReconciliationPanel,
│                            #   ExportDialog; setiap angka menampilkan FigureBadge aktual/estimasi/resmi/tidak tersedia [AG-11]
├── features/packs/          # StackPanel (project), LanguagePackAdmin, PackNotEnabledState                         [AG-10]
├── features/integration/    # ServiceMap, ServiceCatalogEditor, ContractCheckView, EnvLauncher (spec form, bukan YAML
│                            #   bebas), EnvStatus (SSE status/log per service), TierSContractMode                       [AG-10]
└── api/sse/usageStream.ts   # EventSource /usage/live — tanpa replay; refetch ringkasan & timeseries saat reconnect
```

### 14.3 Interface level kode

Signature = **kontrak rancangan** (Python 3.12, `from __future__ import annotations`). Pemilik file kontrak: AG-00 (WP-6.9 untuk `app/usage/types.py`, WP-15.0 untuk `app/packs/manifest.py`, WP-16.0 untuk `app/integration/spec.py`).

#### 14.3.1 `LanguagePackRegistry` & skema manifest (FR-259, FR-261, FR-266, FR-267)

```python
# app/packs/manifest.py — skema config/language-packs/<pack>.yaml (divalidasi di CI dan saat registrasi)
PackId = Literal["python", "node", "jvm", "dotnet", "go", "php", "ruby", "cpp", "react-native", "flutter", "sql"]
PackLevel = Literal["L1", "L2"]
ProfileKind = Literal["deps_install", "build", "test", "test_select", "lint", "fmt", "run", "package", "migrate"]
REQUIRED_KINDS: Mapping[tuple[str, PackLevel], frozenset[ProfileKind]] = {
    ("app", "L1"): frozenset({"deps_install", "build", "test", "test_select", "lint", "fmt", "run", "package"}),
    ("app", "L2"): frozenset({"deps_install", "build", "test", "lint"}),
    ("database", "L1"): frozenset({"migrate", "lint", "test"}),          # pack sql (turunan): tanpa registry
}

@dataclass(frozen=True, slots=True)
class ImageRef:
    repository: str                 # "kodekantor/sandbox-jvm"
    tag: str
    digest: str                     # "sha256:<64 hex>" — wajib; tanpa digest manifest ditolak (FR-267, NFR-30)
    sbom_ref: str                   # SBOM SPDX/CycloneDX hasil pipeline image
    scan_critical: int              # wajib 0 untuk status active
    built_at: datetime              # > 30 hari → pack 'stale' (NFR-30), pengingat di hari ke-25 [Menebak]

@dataclass(frozen=True, slots=True)
class ReportSpec:
    format: Literal["junit_xml", "go_test_json", "trx", "tap", "ctest_xml", "dart_json"]
    path_glob: str                  # di /workspace/.kk/reports/**
    convert_in_sandbox: str | None  # profil konversi (mis. go test -json → JUnit) atau None bila parse backend

@dataclass(frozen=True, slots=True)
class PackProfile:
    profile_id: str                 # "<pack>.<kind>[_<varian>]", mis. "jvm.gradle_test", "go.test_select"
    kind: ProfileKind
    suite: Literal["lint", "unit", "integration", "build", "e2e", "custom", "run"]
    argv: tuple[str, ...]           # exec-form; placeholder hanya {args}, {path}, {test_id}
    args_pattern: str = r"^[A-Za-z0-9_./:\-\[\]=]{1,200}$"
    max_args: int = 10
    network: Literal["none", "egress"] = "none"     # "egress" HANYA untuk kind deps_install (validator)
    timeout_s: int = 300
    max_timeout_s: int = 900
    cpu: float | None = None        # override ≤ batas tier; pack heavy hanya di node berlabel heavy (T27)
    memory: str | None = None
    report: ReportSpec | None = None              # wajib untuk test/test_select
    lockfiles: tuple[str, ...] = ()               # deps_install: minimal satu harus ada (FR-263)
    when: Mapping[str, str] = field(default_factory=dict)   # predikat stack, mis. {"package_manager": "gradle"}

@dataclass(frozen=True, slots=True)
class DetectionRule:
    markers: tuple[str, ...]        # "pom.xml", "build.gradle*", "*.csproj", "go.mod", "composer.json", "pubspec.yaml", …
    framework_hints: Mapping[str, tuple[str, ...]]   # {"spring-boot": ("org.springframework.boot",)} dicari di file penanda
    package_managers: Mapping[str, str]              # {"build.gradle.kts": "gradle", "pom.xml": "maven"}
    entrypoint_globs: tuple[str, ...]
    excludes: tuple[str, ...] = ("node_modules/**", "vendor/**", "build/**", "target/**", "bin/**", "obj/**", ".kk/**")

@dataclass(frozen=True, slots=True)
class RegistrySpec:
    ecosystem: Literal["pypi", "npm", "maven", "gradle_plugins", "nuget", "gomod", "packagist", "rubygems", "pub",
                       "conan", "vcpkg"]
    proxy_key: str                  # kunci di DEP_PROXY_URLS
    pinned_namespaces: tuple[str, ...] = ()       # namespace yang HANYA boleh dari registry ini (anti confusion, T24)

@dataclass(frozen=True, slots=True)
class LanguagePackManifest:
    schema_version: Literal[1]
    pack_id: PackId
    version: str                    # semver pack
    level: PackLevel
    image: ImageRef
    detection: DetectionRule
    profiles: tuple[PackProfile, ...]
    category: Literal["app", "database"] = "app"
    languages: tuple[str, ...] = ()
    registries: tuple[RegistrySpec, ...] = ()     # database: ()
    templates: tuple[TemplateRef, ...] = ()       # L1 app: ≥ 1 (FR-261) — {id, framework, path, description}
    agent_hints_path: str | None = None           # markdown ≤ 4 KB [Menebak], konteks ringkas B14/C17/C20 (FR-265)
    eval_set: str | None = None                   # backend/evals/packs/<pack>/ — L1 wajib (AX-22)
    unsupported_targets: tuple[Literal["ios", "macos", "windows", "dotnet_framework", "android_emulator"], ...] = ()
    resource_class: Literal["standard", "heavy"] = "standard"     # jvm/android/cpp = heavy (T27)

def validate_manifest(m: LanguagePackManifest, *, existing_profile_ids: frozenset[str]) -> list[ManifestIssue]:
    """Deterministik (CI + registrasi). Gagal bila: digest kosong/tidak cocok pola; kind wajib per (category, level)
    hilang; network='egress' di luar deps_install; deps_install tanpa registries atau lockfiles; argv memuat shell
    ('sh', '-c', '&&', '|', ';', '$(' , '`'); placeholder tak dikenal; args_pattern tidak terkompilasi atau menerima
    spasi/';'; test/test_select tanpa report; profile_id tidak berawalan pack_id atau bentrok global; L1 app tanpa
    template/eval_set; override CPU/RAM di atas batas tier; scan_critical > 0 atau built_at > 30 hari untuk active."""

class PackStatus(StrEnum):
    DRAFT = "draft"; VALIDATED = "validated"; ACTIVE = "active"; STALE = "stale"; DISABLED = "disabled"

class LanguagePackRegistry(Protocol):          # app/packs/registry.py
    def manifest(self, pack_id: PackId) -> LanguagePackManifest: ...
    async def list(self, db: AsyncSession, *, include_inactive: bool = False) -> list[LanguagePackView]: ...
    async def register(self, db: AsyncSession, actor: Actor, manifest: LanguagePackManifest) -> LanguagePackView:
        """admin:policies. validate_manifest → language_packs(status='validated'). Tidak mengaktifkan."""
    async def activate(self, db: AsyncSession, actor: Actor, pack_id: PackId, version: str) -> LanguagePackView:
        """Syarat: validated, pack ∈ LANGUAGE_PACKS_ENABLED, digest tersedia (tier S lokal; tier M/L dilaporkan
        heartbeat node image_digests). Versi aktif sebelumnya → disabled (satu versi aktif per pack). Audit pack.activated."""
    async def set_agent_enabled(self, db: AsyncSession, actor: Actor, pack_id: PackId, *, eval_report_id: UUID) -> LanguagePackView:
        """FR-266: hanya bila laporan eval WP-15.10 untuk versi aktif ≥ ambang PO (OI-48) dan keputusan G8 per pack
        tercatat; audit pack.agent_enabled. Tanpa ini agent tidak dijadwalkan untuk file stack pack tersebut."""
    def require_active(self, pack_id: PackId, *, for_agent: bool = False) -> LanguagePackManifest:
        """PackNotEnabled (409 PACK_NOT_ENABLED) bila tidak aktif / stale / tidak di LANGUAGE_PACKS_ENABLED / (for_agent) belum agent_enabled."""
```

Template project (FR-261) disimpan di `templates/<pack>/<framework>/` dengan test yang lulus **tanpa network** (dependensi test di image; pola ADR-007); `POST /projects` `source.type = "template"` menyalin template lalu menjalankan `StackDetector`.

#### 14.3.2 `StackDetector` — polyglot/monorepo (FR-260, FR-265, H41)

```python
@dataclass(frozen=True, slots=True)
class DetectedStack:
    path: str                       # relatif root repo; "." untuk root
    pack_id: PackId
    framework: str | None           # "spring-boot", "aspnetcore", "laravel", "fastapi", "react", "flutter", …
    package_manager: str | None     # "maven", "gradle", "dotnet", "go", "composer", "bundler", "cmake", "pub", "npm", "pnpm", "uv", …
    entrypoints: tuple[str, ...]
    lockfile: str | None
    confidence: Literal["high", "medium", "low"]
    unsupported_targets: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class StackReport:
    stacks: tuple[DetectedStack, ...]      # urut path lalu pack_id; satu path boleh > 1 pack (mis. Gradle + frontend Node)
    primary: DetectedStack | None          # kompatibilitas Stack v1 (repo.detect_stack lama, profiles_for_stack)
    monorepo_tool: str | None              # "nx", "turborepo", "pnpm-workspace", "gradle-multiproject", "go-work", "dotnet-sln"
    scanned_files: int
    truncated: bool                        # batas 20.000 file [Menebak]; truncated → confidence turun

class StackDetector(Protocol):             # app/packs/detect.py
    def detect(self, root: RepoReadView | TaskWorkspace, *, packs: Sequence[LanguagePackManifest], commit: str) -> StackReport:
        """Murni & deterministik: hanya nama file + isi file penanda kecil (≤ 256 KB) lewat resolver workspace
        (denylist berlaku); tidak pernah menjalankan build tool (CLAUDE.md #3). Path anak menimpa induk untuk file di bawahnya."""
    async def persist(self, db: AsyncSession, project_id: UUID, report: StackReport, *,
                      project_repository_id: UUID | None) -> None:
        """Upsert project_stacks (detected_commit). Baris detected_by='detector' yang hilang dihapus; baris
        detected_by='user' (override) dipertahankan."""

def stacks_for_paths(report: StackReport, changed_paths: Sequence[str]) -> list[DetectedStack]:
    """FR-265: stack dengan path terdalam yang menaungi setiap file berubah — dipakai C17 memilih profil dan B14
    memuat petunjuk pack (hanya pack agent_enabled)."""
```

#### 14.3.3 `PackProfileRunner` & `TestReportNormalizer` (FR-262, FR-264, FR-268, FR-269)

```python
@dataclass(frozen=True, slots=True)
class PlannedRun:
    pack_id: PackId
    profile_id: str
    kind: ProfileKind
    workdir: str                    # relatif worktree = DetectedStack.path (subpath tervalidasi di sandbox-manager)

class PackProfileRunner(Protocol):         # app/packs/runner.py — di atas SandboxClient
    def plan(self, stacks: Sequence[DetectedStack], *, kinds: frozenset[ProfileKind]) -> list[PlannedRun]:
        """Pengganti profiles_for_stack: Python/Node menghasilkan profil yang sama dengan snapshot (python.ruff,
        python.pytest, node.npm_*) sehingga perilaku v1 tidak berubah; pack L2 tidak pernah merencanakan run/package."""
    async def run(self, db: AsyncSession, scope: RunScope, planned: PlannedRun, *, args: Sequence[str] = (),
                  selector: TestSelector | None = None,
                  initiated_by: Literal["agent", "user", "system"]) -> TestRun:
        """require_active(pack) → SandboxRunRequest(profile_id, workdir, network='none' kecuali deps_install →
        'egress' + DependencyProxyConfig.sandbox_env) → artefak laporan → TestReportNormalizer → test_runs
        (language_pack, report_status, report_source_format) + artefak junit."""

@dataclass(frozen=True, slots=True)
class NormalizedReport:
    junit_xml: bytes                # selalu JUnit XML setelah normalisasi (FR-264); ≤ 5 MB [Menebak]
    tests: int
    failures: int
    errors: int
    skipped: int
    failing_cases: tuple[FailingCase, ...]        # ≤ 50; pesan diredaksi & dipotong
    source_format: str
    status: Literal["parsed", "unparsed", "missing"]

class TestReportNormalizer(Protocol):      # app/packs/reports.py
    def normalize(self, spec: ReportSpec, artifacts: Mapping[str, bytes], *, exit_code: int) -> tuple[NormalizedReport, TestStatus]:
        """JUnit/TRX/CTest di-parse dengan defusedxml (tanpa DTD/entity, ukuran dibatasi); go test -json, dart json,
        TAP dikonversi di sandbox (convert_in_sandbox) lalu di-parse sebagai JUnit. Aturan status (P26):
        passed ⇔ exit_code == 0 dan (status != 'parsed' atau failures + errors == 0); laporan 'lulus' dengan
        exit_code ≠ 0 → failed (exit code menang); laporan hilang → status dari exit code + report_status 'missing'."""
```

Pack SQL di P15: profil `sql.migrate_flyway`/`sql.migrate_liquibase`, `sql.lint_sqlfluff`, `sql.test_postgres`/`sql.test_mysql` di image `sandbox-sql` yang menjalankan server DB sebagai proses lokal di container yang sama (network none, socket lokal, data di tmpfs) — DB ephemeral per run *(turunan, temuan #8)*. SQL Server dev dan skenario multi-DB hanya lewat env runner (P16, WP-16.4). Pack mobile: `flutter.build_web`, `flutter.build_apk`, `react-native.build_android` (`resource_class = heavy`); target `ios`/`macos`/`android_emulator` hanya dibaca dari check CI eksternal (`ci.get_checks`, FR-270).

#### 14.3.4 `DependencyProxyConfig` (FR-263, ADR-064, T24)

```python
@dataclass(frozen=True, slots=True)
class EcosystemProxy:
    ecosystem: str                         # "maven", "nuget", "gomod", "packagist", "rubygems", "pub", "conan", "pypi", "npm", …
    url: str                               # dari DEP_PROXY_URLS, mis. http://dep-proxy:8081/repository/maven/
    upstream_domains: tuple[str, ...]      # allowlist upstream di dep-proxy (config/dep-proxy.yaml)
    client_env: Mapping[str, str]          # env non-secret yang di-inject ke profil deps_install: GOPROXY + GOFLAGS=-mod=readonly,
                                           #   GONOSUMDB kosong, npm_config_registry, PIP_INDEX_URL, NUGET config, COMPOSER repo, …
    install_flags: tuple[str, ...] = ()    # mis. npm --ignore-scripts, pip --require-hashes bila lock berhash
    pinned_namespaces: Mapping[str, str] = field(default_factory=dict)   # namespace → satu-satunya upstream
    blocklist_ref: str | None = None       # config/deps-blocklist/<ecosystem>.txt (dikelola D24/AG-13)

class DependencyProxyConfig(Protocol):     # app/packs/deps.py
    def for_pack(self, manifest: LanguagePackManifest) -> tuple[EcosystemProxy, ...]: ...
    def sandbox_env(self, manifest: LanguagePackManifest) -> Mapping[str, str]:
        """Hanya URL proxy & flag; tanpa kredensial (mirror privat ber-kredensial di luar v2.2)."""
    def preflight(self, workspace: TaskWorkspace, stack: DetectedStack) -> DepsPreflight:
        """Di backend, hanya membaca file: lockfile wajib (→ 409 LOCKFILE_REQUIRED); entri lockfile yang menunjuk
        registry selain proxy, paket di blocklist, atau namespace terpin dari upstream lain → 409 DEPENDENCY_REJECTED
        dengan daftar temuan (FR-263, T24). Lolos preflight ≠ aman: dep-proxy menegakkan ulang allowlist upstream."""
```

`dep-proxy` (produk diputuskan OQ-04/OI-49): satu container per VPS/node kontrol, network `kk-egress` (sisi sandbox; hanya `*.deps_install`) + outbound (upstream allowlist dari `config/dep-proxy.yaml`), cache terbatas (T27), log akses diredaksi. `egress-proxy` Squid ADR-007 tetap untuk PyPI/npm sampai digantikan dep-proxy di WP-15.0 (keputusan Lead). Network sandbox lain tetap `none`.

#### 14.3.5 `IntegrationEnvSpec` & `EnvSpecPolicy` (FR-275, FR-277, FR-278, FR-280, ADR-065)

```python
SupportKey = Literal["postgres-16", "mysql-8", "mssql-2022-dev", "redis-7", "kafka-3", "rabbitmq-3", "s3-mock"]   # versi [Kemungkinan Besar], dipin di WP-16.1

class EnvServiceSpec(BaseModel, extra="forbid"):
    name: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{1,30}$")]       # juga nama DNS internal
    kind: Literal["service", "support"]
    service_id: UUID | None = None                 # service: dari katalog (pack aktif + repo project)
    commit: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")] | None = None
    run_profile: str | None = None                 # profil tetap <pack>.run / <pack>.env_serve — bukan command bebas
    support_key: SupportKey | None = None          # support: hanya dari katalog terkunci
    env: dict[str, str] = {}                       # hanya kunci ter-allowlist per pack/support; nilai literal non-secret
                                                   #   atau rujukan "${fake:<name>}" ke fake_secrets
    depends_on: list[str] = []
    cpu: float | None = None
    memory: str | None = None

class FakeSecretSpec(BaseModel, extra="forbid"):
    name: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,40}$")]
    kind: Literal["password", "token", "api_key", "jwt_signing_key"]
    length: int = 32                               # dibangkitkan runner per env; tidak pernah dari input user

class EnvTestSpec(BaseModel, extra="forbid"):
    service: str                                   # container test bergabung ke network env
    profile_id: str                                # <pack>.test / <pack>.e2e / contracts.provider_test
    selector: TestSelector | None = None

class IntegrationEnvSpec(BaseModel, extra="forbid"):
    schema_version: Literal[1] = 1
    project_id: UUID
    work_item_id: UUID | None = None
    template: str | None = None                    # template env tetap (F31) sebagai basis
    services: Annotated[list[EnvServiceSpec], Field(min_length=1)]
    seed: SeedSpec | None = None                   # skrip seed/migrasi di repo dijalankan profil pack (data sintetis)
    fake_secrets: list[FakeSecretSpec] = []
    tests: list[EnvTestSpec] = []
    ttl_minutes: int | None = None                 # ≤ INTEGRATION_ENV_TTL_MINUTES
    # Tidak ada field image bebas, privileged, volumes/host path, network_mode, ports, devices, cap_add,
    # security_opt, user, command/entrypoint — extra="forbid" menolak di tingkat skema.

@dataclass(frozen=True, slots=True)
class EnvPolicyLimits:
    max_services: int                  # INTEGRATION_ENV_MAX_SERVICES (8)
    cpu_total: float                   # INTEGRATION_ENV_CPU_LIMIT
    memory_total_bytes: int            # INTEGRATION_ENV_MEMORY_LIMIT
    ttl_max_minutes: int               # INTEGRATION_ENV_TTL_MINUTES (60)
    max_active_per_project: int = 1    # [Menebak]
    max_active_per_node: int = 2       # [Menebak]

ViolationCode = Literal["TIER_UNSUPPORTED", "PACK_NOT_ENABLED", "SERVICE_NOT_IN_CATALOG", "SUPPORT_NOT_IN_CATALOG",
                        "IMAGE_NOT_REGISTERED", "FIELD_FORBIDDEN", "PRIVILEGED", "HOST_MOUNT", "HOST_NETWORK",
                        "PORT_PUBLISH", "EGRESS", "ENV_KEY_NOT_ALLOWED", "SECRET_PATTERN", "UNKNOWN_DEPENDENCY",
                        "DEPENDENCY_CYCLE", "REPO_NOT_IN_PROJECT", "QUOTA_SERVICES", "QUOTA_CPU", "QUOTA_MEMORY",
                        "TTL_TOO_LONG", "QUOTA_ACTIVE_ENVS"]

@dataclass(frozen=True, slots=True)
class EnvValidation:
    ok: bool
    violations: tuple[EnvViolation, ...]           # {path, code, detail} — semua pelanggaran sekaligus
    spec_sha256: str                               # sha256 JSON kanonik spec ternormalisasi
    resolved: ResolvedEnvPlan | None               # image@digest per service, argv profil, urutan start topologis, CPU/RAM

class EnvSpecPolicy(Protocol):                     # app/integration/policy.py ≡ sandbox_manager/env_policy.py (otoritatif)
    def validate(self, raw: Mapping[str, Any], *, packs: PackLookup, catalog: ServiceLookup,
                 support: SupportCatalog, limits: EnvPolicyLimits, tier: Literal["S", "M", "L"]) -> EnvValidation:
        """Deterministik. Urutan: (1) kunci mentah berisiko (privileged, volumes, network_mode, ports, devices,
        cap_add, security_opt, image, command, entrypoint) → FIELD_FORBIDDEN/PRIVILEGED/HOST_MOUNT/HOST_NETWORK/
        PORT_PUBLISH (defense in depth sebelum Pydantic); (2) skema; (3) tier S → TIER_UNSUPPORTED; (4) service ∈
        katalog, pack aktif, repo ∈ project; support ∈ katalog terkunci; (5) env key ∈ allowlist; nilai cocok pola
        secret (pola core/redaction + entropi tinggi [Menebak]) → SECRET_PATTERN (T28); (6) DAG depends_on;
        (7) kuota & TTL → QUOTA_*/TTL_TOO_LONG. Pelanggaran kuota saja → 429 ENV_QUOTA_EXCEEDED; lainnya → 422
        ENV_SPEC_REJECTED. Fixture spec bersama (tests/fixtures/env_specs/*.json) wajib ditolak identik oleh
        kedua implementasi."""
```

#### 14.3.6 `IntegrationEnvRunner` — perluasan sandbox-manager (FR-275, FR-279, FR-280, T25, T27)

```python
class EnvStatus(StrEnum):
    REQUESTED = "requested"; VALIDATING = "validating"; REJECTED = "rejected"; PROVISIONING = "provisioning"
    RUNNING = "running"; TESTING = "testing"; PASSED = "passed"; FAILED = "failed"
    TEARING_DOWN = "tearing_down"; TORN_DOWN = "torn_down"; EXPIRED = "expired"; ERROR = "error"

class IntegrationEnvRunner(Protocol):      # sandbox_manager/envs.py — node executor tier M/L saja
    async def create(self, plan: ResolvedEnvPlan, *, env_id: UUID, project_id: UUID, expires_at: datetime) -> EnvHandle:
        """(1) validasi ulang OTORITATIF (env_policy): digest ∈ image pack terdaftar/katalog pendukung, profil ∈
        sandbox-profiles, kuota; (2) network create kk-env-<env8> dengan internal=true (tanpa gateway egress),
        label kk.env_id & kk.expires_at, tanpa opsi network dari caller; (3) per service (urutan topologis):
        HostConfig standar sandbox — user non-root, cap_drop ALL, no-new-privileges, rootfs read-only + tmpfs,
        pids/cpu/mem, tanpa port publish, tanpa bind host selain worktree service read-only di subpath
        <project>/envs/<env>/<svc>; NetworkMode = network env saja; alias DNS = nama service; secret palsu
        di-inject sebagai env saat create (tidak ditulis ke disk/DB/log); (4) label kk.env_id untuk sweep."""
    async def wait_healthy(self, handle: EnvHandle, *, timeout_s: int) -> Mapping[str, ServiceHealth]: ...
    async def run_in_env(self, handle: EnvHandle, *, profile_id: str, args: Sequence[str]) -> RunResult:
        """Container runner test (profil tetap) bergabung ke network env; hasil + artefak JUnit (FR-279)."""
    async def logs(self, handle: EnvHandle, *, service: str, offset: int) -> LogChunk: ...   # diredaksi, ≤ SANDBOX_MAX_OUTPUT_BYTES per service
    async def teardown(self, handle: EnvHandle, *, reason: Literal["completed", "ttl", "user", "failed", "admin"]) -> TeardownReport:
        """Kill & hapus semua container berlabel kk.env_id, hapus network dan subpath envs/<env>; idempoten."""
    async def sweep_expired(self) -> list[UUID]:
        """Tiap 60 dtk: container/network berlabel kk.expires_at < now atau tanpa baris integration_envs aktif →
        teardown paksa (NFR-31: 100%)."""
```

Endpoint internal sandbox-manager (Bearer, network `exec`/`app` seperti API §10): `POST /v1/envs`, `GET /v1/envs/{env_id}`, `GET /v1/envs/{env_id}/services/{name}/logs?offset=`, `POST /v1/envs/{env_id}/runs`, `DELETE /v1/envs/{env_id}`, `GET /v1/support-images` (kontrak: `API_CONTRACTS.md` §14.8). Izin docker-socket-proxy node: `NETWORKS=1` hanya bila `INTEGRATION_ENV_ENABLED=true` (OI-41); `EXEC=0` tetap.

`IntegrationEnvService` (`app/integration/envs.py`, api + worker-ide node): `create` (izin `env:run`, `EnvSpecPolicy`, insert `integration_envs(requested, spec_sha256, expires_at)`, enqueue `env_jobs.provision` ke antrean node dengan afinitas workspace) → `provision` (build service dengan profil pack di sandbox biasa, worktree service per commit, `IntegrationEnvRunner.create`, seed) → `test` (`run_in_env` per `EnvTestSpec` → `test_runs(suite='integration', integration_env_id)` + artefak log per service) → `teardown`. Event `integration_env.status_changed` / `integration_env.service_health` / `integration_env.log` di kanal Redis `kk:env:{id}` → SSE.

#### 14.3.7 `ServiceCatalog` & `ContractChecker` (FR-271..FR-274, FR-276, FR-282, ADR-066)

```python
ContractType = Literal["openapi", "asyncapi", "protobuf", "graphql"]

class ServiceCatalog(Protocol):            # app/integration/catalog.py
    async def list(self, db: AsyncSession, actor: Actor, program_id: UUID) -> list[ServiceView]: ...
    async def upsert(self, db: AsyncSession, actor: Actor, program_id: UUID, data: ServiceUpsert) -> ServiceView:
        """catalog:write. Validasi: kind='internal' ⇒ pack aktif + repo ∈ project_repositories; path kontrak ada di
        head default branch; slug unik per program; interface consumes merujuk interface provides yang ada atau
        kontrak eksternal bertanda; dependensi data ∈ katalog pendukung."""
    async def graph(self, db: AsyncSession, actor: Actor, program_id: UUID) -> ServiceGraph: ...   # peta service (FR-281)
    def services_touched(self, program: ProgramView, changed: Mapping[UUID, Sequence[str]]) -> frozenset[UUID]:
        """Peta (repo, path berubah) → service lewat project_repository_id + root_path; dipakai merge queue (FR-282)."""

@dataclass(frozen=True, slots=True)
class ContractChange:
    kind: Literal["breaking", "non_breaking", "info"]
    rule: str                          # mis. "openapi.response.property_removed", "proto.field_number_changed"
    location: str                      # JSON pointer / elemen proto / channel AsyncAPI / tipe GraphQL
    detail: str

@dataclass(frozen=True, slots=True)
class ContractCheckResult:
    interface_id: UUID
    contract_type: ContractType
    base_commit: str
    head_commit: str
    result: Literal["compatible", "breaking", "invalid", "error"]
    changes: tuple[ContractChange, ...]
    affected_consumers: tuple[UUID, ...]          # service consumer yang interface-nya merujuk interface ini
    tool: str
    tool_version: str                              # alat diff dipin di image sandbox-contracts [Kemungkinan Besar pilihan alat]
    report_artifact_id: UUID | None

class ContractChecker(Protocol):           # app/integration/contracts.py — parse/diff/mock di sandbox (network none)
    async def validate(self, db: AsyncSession, interface_id: UUID, *, commit: str) -> ContractValidation: ...
    async def check(self, db: AsyncSession, interface_id: UUID, *, base_commit: str, head_commit: str,
                    task_id: UUID | None = None, merge_queue_entry_id: UUID | None = None) -> ContractCheckResult:
        """Dokumen kontrak = data tidak tepercaya (P15/P17): divalidasi & di-diff di sandbox-contracts. Hasil →
        contract_checks (idempoten per interface, base, head). 'breaking' memblokir merge queue sampai setiap
        affected_consumer punya commit yang lulus contract test terhadap head ATAU pengecualian (OI-45)."""
    async def generate_mock(self, db: AsyncSession, interface_id: UUID, *, commit: str) -> MockBundle:
        """Stub/mock dari kontrak untuk test consumer dan mode tier S (FR-274, FR-276)."""
    async def grant_exception(self, db: AsyncSession, actor: Actor, check_id: UUID, *, reason: str) -> ContractCheckResult:
        """merge_queue:manage; alasan wajib; terikat (check_id, head_commit); audit contract.exception_granted
        (OI-45). Bukan approval R3; tidak dapat dipanggil agent."""
```

Aturan breaking change deterministik (ringkas; daftar lengkap = fixture WP-16.2) [Kemungkinan Besar, selaras alat diff umum]:

| Tipe | Breaking | Non-breaking |
|---|---|---|
| OpenAPI | hapus path/operasi; hapus properti respons atau ubah tipenya; parameter/properti request baru yang **wajib**; hapus nilai enum yang diterima request; ubah skema keamanan | tambah path/operasi; properti respons opsional baru; parameter request opsional baru |
| AsyncAPI | hapus channel/operasi; hapus field payload yang dibaca consumer atau ubah tipenya; ubah format pesan/binding | tambah channel; field payload opsional baru |
| Protobuf/gRPC | ubah/pakai ulang nomor field; hapus field tanpa `reserved`; ubah tipe/cardinality; ubah nama package/service/RPC | field baru dengan nomor baru; RPC baru |
| GraphQL | hapus tipe/field; field output non-null → nullable atau ubah tipe; argumen menjadi wajib | tipe/field baru; argumen opsional baru |

Integrasi merge queue (`app/merge_queue/checks.py`): untuk entri yang menyentuh interface terdaftar → langkah `contract_check` (gagal → `failure_code = CONTRACT_BREAKING`); untuk story yang menyentuh > 1 service (`services_touched`) → langkah `integration_e2e` memakai bukti `test_runs(suite='integration')` atas `head_commit` kandidat dari env yang lulus, atau menjalankan env (tier M/L) — gagal/tidak ada bukti → `INTEGRATION_FAILED` *(turunan)*. Tier S: bukti = contract test provider + test consumer terhadap mock (OI-39).

#### 14.3.8 `UsageEventPublisher` (FR-283, FR-286, FR-290, ADR-067)

```python
# app/llm/base.py — tambahan StreamEvent (kompatibel-mundur; on_event sudah dirancang di §5.0.1)
@dataclass(slots=True, frozen=True)
class UsageProgress:
    phase: Literal["start", "delta", "final"]
    model: str
    request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None
    server_tool_use: Mapping[str, int]             # {"web_search_requests": n, "web_fetch_requests": n}

StreamEvent = TextDelta | CitationDelta | ServerToolStarted | ServerToolFinished | UsageProgress

def merge_cumulative(prev: UsageProgress, delta_usage: Mapping[str, Any]) -> UsageProgress:
    """message_delta.usage bersifat KUMULATIF [Pasti — dokumentasi Streaming]: setiap field yang ada MENIMPA nilai
    sebelumnya, tidak pernah dijumlahkan; field yang tidak ada dipertahankan dari message_start [Kemungkinan Besar
    field mana yang muncul — dibuktikan contract test]. Usage pesan final (get_final_message) = sumber otoritatif."""

@dataclass(frozen=True, slots=True)
class UsageCallContext:                    # dibangun AgentRuntime / assistant job / adapter vendor
    call_id: UUID                          # uuid7; kunci idempotensi
    provider: str                          # "anthropic" | "<image vendor>"
    model: str | None
    pricing_version: str
    organization_id: UUID | None = None
    project_id: UUID | None = None
    task_id: UUID | None = None
    thread_id: UUID | None = None
    agent_run_id: UUID | None = None
    agent_id: str | None = None
    user_id: UUID | None = None
    capability: str | None = None          # None sebelum WP-10.1; diisi WP-10.7 untuk Portal AI & vendor

FigureStatus = Literal["actual", "estimated", "official", "unavailable"]   # UI: aktual / estimasi / resmi / tidak tersedia

class UsageEventPublisher(Protocol):       # app/usage/publisher.py — di setiap worker yang memanggil provider/vendor
    async def on_progress(self, ctx: UsageCallContext, p: UsageProgress) -> None:
        """Ephemeral, tanpa DB: throttle ≤ 1 publish/dtk per call_id [Menebak]; PUBLISH kk:usage:org:<org|default>
        usage.event phase='progress' (token kumulatif sejauh ini, estimasi biaya parsial)."""
    async def record_final(self, db: AsyncSession, ctx: UsageCallContext, usage: Usage | None, *,
                           units: Mapping[str, Decimal] = {}, status: Literal["ok", "error", "cancelled"],
                           duration_ms: int | None) -> UsageEvent:
        """Satu transaksi: usage_records (LLM) ATAU capability_usage (vendor/unit, ledger V2.2) + usage_events
        (call_id UNIQUE, FK ke salah satu) + UsageRollup.add. Biaya = estimasi tabel tarif (pricing_version);
        tarif tak dikenal → estimated_cost NULL, cost_status 'unavailable' (bukan 0). Panggilan batal/gagal setelah
        message_start → token = nilai kumulatif terakhir, cost_status 'partial'. Setelah commit: PUBLISH
        usage.event phase='final' lalu enqueue BudgetAlerter.evaluate(ctx). Retry worker dengan call_id sama →
        mengembalikan baris yang ada (tanpa hitung ganda, P27)."""
```

Pemasangan: `AgentRuntime` membuat `UsageCallContext` per iterasi model dan meneruskan `on_event` yang memanggil `on_progress`; menggantikan pencatatan `usage_records` langsung di runtime snapshot (perilaku `BudgetGuard` tidak berubah). Vendor tanpa streaming (gambar) hanya memanggil `record_final(units={"image": n})` setelah respons vendor. Kanal Redis per organisasi; filter scope dilakukan api per koneksi SSE (bukan per kanal user) agar satu publish melayani semua pelanggan.

#### 14.3.9 `UsageRollup` & `BudgetAlerter` (FR-284..FR-287)

```python
Dim = Literal["model", "agent", "project", "task", "capability", "user", "provider"]

@dataclass(frozen=True, slots=True)
class RollupKey:
    bucket_start: datetime                 # date_trunc('minute', ts) UTC
    organization_id: UUID | None
    project_id: UUID | None
    user_id: UUID | None
    agent_id: str | None
    capability: str | None
    provider: str
    model: str | None
    def dims_hash(self) -> str: ...        # sha256 tuple kanonik (NULL-aman) — PK (bucket_start, dims_hash)

class UsageRollup(Protocol):               # app/usage/rollup.py
    async def add(self, db: AsyncSession, ev: UsageEvent) -> None:
        """INSERT … ON CONFLICT (bucket_start, dims_hash) DO UPDATE SET kolom = kolom + EXCLUDED.kolom (transaksi
        record_final). task tidak masuk kunci rollup (kardinalitas); breakdown per task dihitung dari usage_events."""
    async def rebuild(self, db: AsyncSession, *, since: datetime, until: datetime) -> RebuildReport:
        """Beat harian + on-demand usage:admin: hitung ulang dari usage_events; selisih wajib 0 (AX-19: total
        halaman = total usage_records) — selisih ≠ 0 → metrik + log + perbaikan."""
    async def summary(self, db: AsyncSession, scope: UsageScope, *, group_by: Sequence[Dim], period: Period) -> UsageSummary: ...
    async def timeseries(self, db: AsyncSession, scope: UsageScope, *, bucket: Literal["1m", "1h", "1d"],
                         start: datetime, end: datetime, group_by: Sequence[Dim] = ()) -> list[SeriesPoint]: ...
    def burn_rate(self, points: Sequence[SeriesPoint], *, window_minutes: int = 15) -> BurnRate:
        """token/menit & USD/jam dari jendela bergulir [Menebak 15 menit]; proyeksi akhir bulan = terpakai + rata-rata
        harian 7 hari × sisa hari (FR-285); biaya 'unavailable' tidak diproyeksikan (label tidak tersedia)."""

BudgetScope = Literal["task", "project", "program", "epic", "organization", "capability"]
THRESHOLDS: tuple[Decimal, ...] = (Decimal("0.50"), Decimal("0.80"), Decimal("1.00"))

@dataclass(frozen=True, slots=True)
class BudgetRef:
    scope_type: BudgetScope
    scope_key: str                         # uuid, atau nama capability
    source: Literal["task_budget", "project_budget", "cost_budget", "capability_setting"]
    limit_usd: Decimal
    period_key: str                        # "task", "total", atau "YYYY-MM"
    kill_switch_ref: str                   # rute UI/endpoint kill switch (FR-132, FR-217, FR-255)

class BudgetAlerter(Protocol):             # app/usage/budgets.py
    async def evaluate(self, db: AsyncSession, ctx: UsageCallContext) -> list[BudgetAlert]:
        """BudgetDirectory.refs_for(ctx): task → project → epic/program → organisasi; capability. used = Σ ledger
        (usage_records + capability_usage, estimated_cost) per period_key; baris unavailable tidak dihitung 0 —
        alert membawa cost_status 'partial'. Untuk setiap ambang t dengan used/limit ≥ t (Decimal, tepat di ambang)
        yang belum tercatat → INSERT budget_alerts (UNIQUE scope_type, scope_key, period_key, threshold) + audit
        budget.threshold_reached + PUBLISH budget.alert. Alerter tidak memblokir apa pun: penegakan tetap di
        BudgetGuard (task), CapabilityRegistry (capability), CostBudgetService (program/epic/organisasi)."""

class BudgetDirectory(Protocol):           # fasad /budgets (OI-44) — tidak ada penyimpanan limit kedua
    async def list(self, db: AsyncSession, actor: Actor, *, scope_type: BudgetScope | None = None) -> list[BudgetView]: ...
    async def upsert(self, db: AsyncSession, actor: Actor, data: BudgetUpsert) -> BudgetView:
        """project → projects budget default; program/epic/organization → cost_budgets (tier M+); capability →
        capability_settings.limits.monthly_usd (WP-10.1+); task → 422 (budget task hanya lewat pembuatan task atau
        approval budget_increase, ADR-034). Lingkup yang sumbernya belum ada di fase berjalan → 409 dengan alasan."""
```

#### 14.3.10 `UsageReconciler` (FR-288, FR-289, FR-292, T26)

```python
class AdminUsageClient(Protocol):          # app/integrations/anthropic_admin.py — httpx, BUKAN SDK anthropic
    """Base https://api.anthropic.com; allowlist path TETAP: GET /v1/organizations/usage_report/messages dan
    GET /v1/organizations/cost_report [Pasti — PID §6]. Header x-api-key = ANTHROPIC_ADMIN_API_KEY (secret file),
    anthropic-version dipin. Path/metode lain → AdminClientMisuse (bug, bukan input). Log hanya status HTTP +
    request-id; logger httpx/httpcore di role ini dipaksa WARNING agar header tidak pernah tercetak."""
    async def usage_messages(self, *, starting_at: datetime, ending_at: datetime,
                             bucket_width: Literal["1m", "1h", "1d"], group_by: Sequence[str] = ("model",),
                             page: str | None = None) -> UsagePage: ...
    async def cost_report(self, *, starting_at: datetime, ending_at: datetime,
                          group_by: Sequence[str] = ("description",), page: str | None = None) -> CostPage: ...
    # Nama parameter query & bentuk respons [Kemungkinan Besar] — dibuktikan contract test dengan mock di WP-6.10.

ReconStatus = Literal["settling", "matched", "variance", "unavailable", "error"]

class UsageReconciler(Protocol):           # app/usage/reconcile.py — HANYA worker-integrations
    async def availability(self) -> tuple[bool, Literal["ok", "disabled", "no_credentials", "individual_account", "forbidden"]]: ...
    async def run_once(self, db: AsyncSession, *, now: datetime) -> ReconcileReport:
        """Beat tiap USAGE_RECONCILE_INTERVAL_SECONDS (validator: < 60 → gagal start). Tidak tersedia → tidak ada
        panggilan jaringan; status disimpan untuk 409 RECONCILIATION_UNAVAILABLE. Langkah: (1) usage bucket 1m per model
        untuk jendela [now − 60 mnt, now] [Menebak]; (2) internal = Σ usage_events provider 'anthropic' per (menit,
        model); (3) bucket lebih muda dari 5 mnt [Pasti ≈ ≤ 5 mnt] atau berubah sejak tarikan lalu → 'settling';
        |selisih token| / resmi × 100 > USAGE_VARIANCE_ALERT_PCT → 'variance' + audit usage.variance_flagged +
        PUBLISH usage.reconciliation_updated; (4) cost_report 1d untuk kemarin & hari ini → baris bucket_width '1d'
        (biaya resmi vs Σ estimasi); (5) upsert usage_reconciliation (UNIQUE bucket_width, bucket_start, model, scope_key).
        Komponen yang tidak tersedia per menit (biaya code execution hanya di endpoint cost; Priority Tier tidak di
        endpoint cost [Pasti]) ditandai pada kolom status_reason. 401/403 → availability 'forbidden' (tanpa retry
        agresif); 429/5xx → backoff eksponensial, status 'error' tanpa angka karangan."""
```

Lingkup: Admin API melaporkan usage **organisasi**. Bila filter per API key/workspace tersedia [Kemungkinan Besar], reconciler membatasi ke ID kunci KodeKantor yang dicatat saat readiness (`provider_configs.readiness_report.api_key_id`, *(turunan)*); bila tidak, `scope_key = 'organization'` dan selisih hanya informatif (D-26).

#### 14.3.11 Registrasi tool v2.2 di ToolGateway

Detail kolom: `TOOL_CATALOG.md` v2.2. Ringkasan handler:

| Tool | Risk | executor | Handler / modul | Antrean | Pemanggil |
|---|---|---|---|---|---|
| `repo.detect_stack` (diperluas) | R0 | backend | `tools/builtin/repo.py` → `StackDetector.detect` | ide | H41, A04 |
| `<pack>.build` / `.test` / `.test_select` / `.lint` / `.fmt` / `.run` / `.package` (profil) | R2 | sandbox | `tools/builtin/packs.py` → `PackProfileRunner.run` (lewat `tests.run`/`tests.run_selected`) | ide | C17, B14 (lint/fmt), user lewat service |
| `deps.install` (diperluas) | R2 | sandbox (`kk-egress` → dep-proxy) | `tools/builtin/deps.py` → `preflight` + `<pack>.deps_install` | ide | C17, B14 |
| `deps.audit` *(turunan, OI-47)* | R2 | sandbox (network none; snapshot advisory read-only yang disinkronkan job OPS harian) | `tools/builtin/deps.py` | ide | D24 |
| `env.up` / `env.status` / `env.down` | R2 / R0 / R1 | worker → sandbox-manager | `tools/builtin/envs.py` → `IntegrationEnvService` | ide (node) | C18, C19 (setelah G16) |
| `catalog.list_services` *(turunan)* | R0 | backend | `tools/builtin/contracts.py` → `ServiceCatalog.list` | ide | A05, B11, C18 |
| `contract.check` / `contract.mock` | R2 / R2 | sandbox (`sandbox-contracts`, network none) | `tools/builtin/contracts.py` → `ContractChecker` | ide | B11, C18 (+ merge queue lewat service) |
| `usage.query` | R0 | backend | `tools/builtin/usage.py` → `UsageRollup.summary` (scope task/project agent) | ide | ORCH, A03 (estimasi biaya rencana) |

### 14.4 Alur detail

#### 14.4.1 Event usage → SSE → UI dan rekonsiliasi (FR-283..FR-290)

```mermaid
sequenceDiagram
  autonumber
  participant UI as Halaman Monitoring
  participant API as api SSE
  participant R as Redis
  participant P as AnthropicProvider
  participant W as worker AgentRuntime
  participant UP as UsageEventPublisher
  participant DB as PostgreSQL
  participant WI as worker-integrations
  participant ADM as Usage and Cost Admin API
  UI->>API: GET /usage/live dengan sesi dan izin usage read
  API->>R: SUBSCRIBE kanal usage organisasi
  P->>W: message_start dengan usage awal
  W->>UP: on_progress fase start
  UP->>R: PUBLISH usage.event progress
  R-->>API: pesan
  API-->>UI: SSE usage.event progress sesuai scope user
  P->>W: message_delta dengan usage kumulatif
  W->>UP: on_progress fase delta, nilai ditimpa bukan dijumlah
  P->>W: pesan akhir dengan usage final
  W->>UP: record_final
  UP->>DB: usage_records, usage_events, upsert usage_rollups_minute dalam satu transaksi
  UP->>R: PUBLISH usage.event final setelah commit
  R-->>API: pesan
  API-->>UI: SSE usage.event final, token aktual, biaya estimasi
  UP->>DB: BudgetAlerter evaluate, insert budget_alerts bila ambang baru
  UP->>R: PUBLISH budget.alert
  API-->>UI: SSE budget.alert dengan tautan kill switch
  loop setiap interval rekonsiliasi minimal 60 detik
    WI->>ADM: GET usage_report messages bucket 1m per model
    ADM-->>WI: token resmi per bucket
    WI->>ADM: GET cost_report harian
    ADM-->>WI: biaya resmi per hari
    WI->>DB: bandingkan dengan usage_events per menit per model
    WI->>DB: upsert usage_reconciliation matched, variance, atau settling
    WI->>R: PUBLISH usage.reconciliation_updated
  end
  UI->>API: GET /usage/reconciliation
  API-->>UI: selisih per model per menit dan per hari berlabel resmi
```

Tanpa kredensial: langkah loop tidak terjadi; `GET /usage/reconciliation` → 409 `RECONCILIATION_UNAVAILABLE` dengan alasan, panel menampilkan label "estimasi".

#### 14.4.2 Siklus hidup lingkungan integrasi (FR-275..FR-280)

```mermaid
sequenceDiagram
  autonumber
  participant U as Tech lead
  participant API as api
  participant POL as EnvSpecPolicy
  participant DB as PostgreSQL
  participant W as worker-ide node
  participant SM as sandbox-manager node
  participant NET as Network env internal
  participant BEAT as scheduler
  U->>API: POST /projects/{pid}/integration-envs dengan spec
  API->>API: izin env run, INTEGRATION_ENV_ENABLED, tier M atau L
  API->>POL: validasi spec, pack, katalog pendukung, kuota, TTL, pola secret
  alt spec ilegal
    POL-->>API: daftar pelanggaran
    API-->>U: 422 ENV_SPEC_REJECTED tanpa container
  else kuota terlampaui
    API-->>U: 429 ENV_QUOTA_EXCEEDED
  else valid
    API->>DB: integration_envs requested, spec_sha256, expires_at
    API-->>U: 202 env dan tautan SSE status
    W->>DB: klaim job, status provisioning
    W->>SM: POST /v1/envs dengan rencana tervalidasi
    SM->>SM: validasi ulang otoritatif, digest terdaftar, HostConfig standar
    SM->>NET: buat network internal berlabel kk.env_id
    SM->>NET: jalankan DB dan broker lalu service dengan alias DNS
    SM-->>W: kesehatan per service
    W->>SM: migrasi dan seed sintetis dengan secret palsu
    W->>DB: status testing
    W->>SM: POST /v1/envs/{eid}/runs contract test dan E2E
    SM-->>W: exit code, JUnit, log per service teredaksi
    W->>DB: test_runs suite integration, artefak log, status passed atau failed
    W->>SM: DELETE /v1/envs/{eid}
    SM->>NET: hapus container dan network
    W->>DB: status torn_down
  end
  loop setiap 60 detik
    BEAT->>DB: cari env aktif yang melewati expires_at
    BEAT->>SM: sweep berlabel kk.expires_at
    SM->>NET: teardown paksa
    BEAT->>DB: status expired
  end
```

### 14.5 Migrasi 0009_polyglot_usage & 0010_integration

```
… → 0002_ide_backlog → 0009_polyglot_usage (WP-6.9, P6) → 0003 … 0008 → 0010_integration (WP-16.0, P16)
```

Rantai Alembic ditentukan `down_revision`, bukan nomor nama (pola §7.0 dan K13 OI-27). Karena tabel usage dibutuhkan di P6, **0009 di-land lebih dulu** *(turunan, OI-38 — keputusan Lead di WP-6.9)*; Lead me-rebase `down_revision` migrasi berikutnya (0003). Kolom yang merujuk tabel yang belum ada saat 0009 di-land dibuat **tanpa FK** dan FK-nya ditambahkan migrasi pemilik tabel: `organization_id` (FK oleh 0008), `thread_id` & `capability_usage_id` (FK oleh 0003), `project_repository_id` (FK oleh 0008), `budget_alerts.cost_budget_id` (FK oleh 0008). Tabel `language_packs`/`project_stacks` inert (kosong) sampai WP-15.0. Bila Lead memilih memecah 0009, nama K19 tetap dipakai untuk bagian pertama dan perubahan dicatat sebagai amandemen kontrak.

| Migrasi | Di-land oleh | Isi ringkas | Catatan urutan |
|---|---|---|---|
| 0009_polyglot_usage | WP-6.9 (AG-07 + Lead) | `usage_events`, `usage_rollups_minute`, `budget_alerts`, `usage_reconciliation`, `language_packs`, `project_stacks`; `test_runs.language_pack`, `report_status`, `report_source_format` | *schema-first* P6; tabel pack inert sampai WP-15.0; FK tertunda seperti di atas |
| 0010_integration | WP-16.0 (AG-00) | `service_catalog`, `service_interfaces`, `service_dependencies`, `integration_envs`, `integration_env_services`, `contract_checks`; `test_runs.integration_env_id`; CHECK `merge_queue_entries.failure_code` + `CONTRACT_BREAKING`, `INTEGRATION_FAILED`; `merge_queue_entries.contract_check_ids`, `integration_env_id` | setelah 0007/0008 (butuh `work_items`, `project_repositories`, `executor_nodes`, `merge_queue_entries`) |

**Kolom kunci & constraint per tabel K19** (ringkas; kolom lengkap, FK `ON DELETE`, dan retensi di `DATA_MODEL.md` V2.11/V2.12 — bila berbeda, `DATA_MODEL.md` yang berlaku):

| Migrasi | Tabel / perubahan | Kolom kunci | Constraint & index utama |
|---|---|---|---|
| 0009 | `usage_events` | `call_id`, `usage_record_id` \| `capability_usage_id`, `ts`, dimensi (`organization_id`, `project_id`, `task_id`, `thread_id`, `agent_run_id`, `agent_id`, `user_id`, `capability`, `provider`, `model`), token in/out/cache, `server_tool_use`, `units`, `estimated_cost`, `cost_status`, `pricing_version`, `status` | UNIQUE `call_id`; UNIQUE `usage_record_id`; UNIQUE `capability_usage_id`; CHECK `num_nonnulls(usage_record_id, capability_usage_id) = 1`; CHECK token ≥ 0; index `(ts)`, `(organization_id, ts)`, `(project_id, ts)`, `(task_id)`, `(model, ts)` |
| 0009 | `usage_rollups_minute` | `bucket_start`, `dims_hash`, dimensi, `calls`, jumlah token, `server_tool_use`, `estimated_cost`, `cost_unavailable_calls` | PK `(bucket_start, dims_hash)`; CHECK `bucket_start = date_trunc('minute', bucket_start)`; index `(organization_id, bucket_start)`, `(project_id, bucket_start)` |
| 0009 | `budget_alerts` | `scope_type`, `scope_key`, `source`, `period_key`, `threshold`, `used_usd`, `limit_usd`, `cost_status`, `kill_switch_ref`, `acknowledged_by/at` | UNIQUE `(scope_type, scope_key, period_key, threshold)`; CHECK `threshold in (0.50, 0.80, 1.00)`; CHECK `scope_type` 6 nilai; index `(triggered_at)` |
| 0009 | `usage_reconciliation` | `bucket_width`, `bucket_start`, `model`, `scope_key`, token internal/resmi, `estimated_cost`, `official_cost`, `token_variance_pct`, `cost_variance_pct`, `status`, `status_reason`, `fetched_at` | UNIQUE `(bucket_width, bucket_start, model, scope_key)`; CHECK `bucket_width in ('1m','1d')`; CHECK `bucket_width = '1d' or official_cost is null` (biaya resmi hanya harian); index `(status, bucket_start)` |
| 0009 | `language_packs` | `pack_id`, `version`, `level`, `category`, `image_digest`, `status`, `agent_enabled`, `manifest`, `manifest_sha256`, `scan_critical`, `built_at` | UNIQUE `(pack_id, version)`; UNIQUE parsial `(pack_id) where status = 'active'`; CHECK digest `^sha256:[0-9a-f]{64}$`; CHECK `status <> 'active' or scan_critical = 0`; CHECK `not agent_enabled or status = 'active'` |
| 0009 | `project_stacks` | `project_id`, `project_repository_id`, `path`, `pack_id`, `framework`, `package_manager`, `entrypoints`, `lockfile`, `confidence`, `detected_by`, `detected_commit` | UNIQUE index `(project_id, coalesce(project_repository_id, nil-uuid), path, pack_id)`; CHECK `path` relatif tanpa `..` |
| 0010 | `service_catalog` | `project_id`, `program_id`, `slug`, `kind`, `pack_id`, `project_repository_id`, `root_path`, `owner_id`, `status` | UNIQUE index `(project_id, coalesce(program_id, nil-uuid), slug)`; CHECK `kind <> 'internal' or (pack_id and project_repository_id not null)` |
| 0010 | `service_interfaces` | `service_id`, `direction`, `contract_type`, `path`, `provider_interface_id`, `version`, `contract_sha256`, `validation_status` | UNIQUE `(service_id, direction, contract_type, path)`; CHECK `direction <> 'provides' or path is not null`; CHECK `direction <> 'consumes' or provider_interface_id is not null or path is not null` |
| 0010 | `service_dependencies` | `service_id`, `kind`, `depends_on_service_id`, `support_key`, `required` | CHECK `(kind = 'service') = (depends_on_service_id is not null)`; CHECK `kind = 'service' or support_key is not null`; CHECK `depends_on_service_id <> service_id` |
| 0010 | `integration_envs` | `project_id`, `spec`, `spec_sha256`, `status`, `node_id`, `network_name`, `quotas`, `ttl_minutes`, `expires_at`, `teardown_reason`, `violations`, `test_run_ids` | CHECK `ttl_minutes > 0`; CHECK `status not in ('torn_down','expired') or torn_down_at is not null`; index parsial `(expires_at) where status in ('requested','validating','provisioning','running','testing','tearing_down')`; index `(project_id, created_at)` |
| 0010 | `integration_env_services` | `env_id`, `name`, `kind`, `service_id`, `pack_id`, `support_key`, `image_digest`, `commit`, `container_id`, `status`, `log_artifact_id` | UNIQUE `(env_id, name)`; CHECK `(kind = 'service' and pack_id is not null) or (kind = 'support' and support_key is not null)` |
| 0010 | `contract_checks` | `interface_id`, `base_commit`, `head_commit`, `task_id`, `merge_queue_entry_id`, `result`, `changes`, `affected_consumers`, `exception_*`, `tool_version` | UNIQUE `(interface_id, base_commit, head_commit)`; CHECK `exception_granted_by is null or (result = 'breaking' and exception_reason is not null)`; index `(merge_queue_entry_id)`, `(task_id)` |

Kebijakan sama dengan §7.0 (enum `VARCHAR + CHECK`, `NOT VALID` + `VALIDATE`, kolom baru nullable/berdefault konstan, `downgrade()` diuji). Tidak ada kolom yang menyimpan kunci Admin API, secret palsu env, atau kredensial registry.

### 14.6 Keamanan implementasi v2.2

| Ancaman | Kontrol desain | Bagian |
|---|---|---|
| T24 dependency confusion / typosquatting | hanya `*.deps_install` ber-network (`kk-egress` → dep-proxy); preflight lockfile/namespace/blocklist; dep-proxy menegakkan allowlist upstream + namespace terpin; `npm --ignore-scripts` dan setara; D24 (`deps.audit`) setelah P15; laporan audit sebagai findings | §14.3.4 |
| T25 lateral movement / escape env | spec `extra=forbid` + validasi ganda; network `internal` per env; HostConfig standar; tanpa host mount/port/privileged; image hanya dari pack/katalog dipin digest; `NETWORKS=1` hanya node env (OI-41); kuota & TTL; sweep berlabel; gVisor wajib tier M (ADR-060) | §14.3.5, §14.3.6 |
| T26 kredensial Admin API bocor | hanya `worker-integrations` (validator role menolak di role lain); `AdminUsageClient` allowlist 2 path GET; log httpx dipaksa WARNING; redaksi pola `sk-ant-admin01-…` + literal; tidak ada endpoint yang menerima/menampilkan kunci (status `configured`/`missing` saja); rotasi lewat RUNBOOK | §14.3.10 |
| T27 resource exhaustion toolchain berat | `resource_class = heavy` → node berlabel heavy; override CPU/RAM ≤ batas tier; timeout per profil; cache dep-proxy & build terbatas; kuota env; `SANDBOX_MAX_CONCURRENT_RUNS` tetap | §14.3.1, §14.3.6 |
| T28 secret nyata di spec/seed | secret hanya `fake_secrets` yang dibangkitkan runner; nilai env diperiksa pola secret + entropi → `SECRET_PATTERN`; file seed di-scan `security.scan_secrets` sebelum dipakai; log env diredaksi; secret palsu tidak disimpan di DB | §14.3.5 |

Monitoring tidak membuka data lintas scope: SSE `/usage/live` memfilter per koneksi (project yang dapat dibaca + resource Portal AI milik sendiri; lintas project butuh `usage:read_all`); test cross-project/tenant T20 diperluas ke endpoint usage, budget, katalog, dan env.

### 14.7 Rencana test AX-19..AX-24

| AX | Test | Lokasi (rencana) |
|---|---|---|
| AX-19 | `merge_cumulative` (property test: delta kumulatif tidak pernah dijumlahkan); 1 panggilan → tepat 1 `usage_records` + 1 `usage_events`, retry `call_id` sama tidak menggandakan; Σ rollup = Σ `usage_records` (rebuild selisih 0); alert tepat di 50/80/100% (Decimal, tanpa duplikat, 5 lingkup); label angka (FR-290); E2E provider mock streaming → kartu & aliran UI ≤ 2 dtk [Menebak] + histogram NFR-28; WP-10.7: server tool & gambar menghasilkan event dengan `capability` | `tests/unit/usage/test_cumulative_merge.py`, `tests/integration/test_usage_publisher.py`, `tests/integration/test_usage_rollup.py`, `tests/integration/test_budget_alerts.py`, `tests/unit/usage/test_figure_labels.py`, `tests/e2e/usage-live.spec.ts`, `tests/integration/test_capability_usage_events.py` |
| AX-20 | mock Admin API: matched, variance > ambang, settling (< 5 mnt), 429/5xx backoff, cost `1d`; interval < 60 → gagal start; tanpa kredensial → `RECONCILIATION_UNAVAILABLE` + alasan; kunci sintetis `sk-ant-admin01-…` tidak muncul di log/event/SSE/respons/artefak; `AdminUsageClient` menolak path lain; env role selain `worker-integrations` tidak memuat kunci | `tests/integration/test_usage_reconcile.py`, `tests/unit/usage/test_reconcile_config.py`, `tests/security/test_t26_admin_key.py`, `tests/unit/core/test_redaction.py` (diperluas) |
| AX-21 | matriks CI per pack: template (L1) → deps lewat proxy (tanpa proxy gagal) → build → test → lint → run/package (L1); validator manifest; detektor atas fixture repo per pack + monorepo polyglot; normaliser laporan (JUnit/TRX/go json/TAP/CTest/dart; exit code menang; XXE ditolak); profil pack di Docker nyata; T24 (lockfile hilang, namespace terpin dari upstream lain, blocklist, domain di luar allowlist) | `.github/workflows/packs.yml`, `tests/unit/packs/test_manifest_validator.py`, `tests/unit/packs/test_detector_fixtures.py`, `tests/unit/packs/test_report_normalizer.py`, `sandbox_manager/tests/test_pack_profiles_docker.py` (`-m docker`), `tests/security/test_t24_dependency_confusion.py` |
| AX-22 | laporan eval per pack L1 (implementasi + bug seeded, bukti `test_runs`), skor ≥ ambang PO [Menebak ≥ 70%]; agent tidak dijadwalkan untuk pack tanpa `agent_enabled`; petunjuk pack dimuat hanya untuk stack file yang diubah | `backend/evals/packs/<pack>/`, `tests/unit/packs/test_agent_enablement.py`, `tests/unit/packs/test_stacks_for_paths.py` |
| AX-23 | env ≥ 3 service beda bahasa + PostgreSQL + broker: DNS internal, E2E lulus, egress tidak terjangkau dari setiap container, tanpa host mount, teardown menghapus container + network, sweep TTL; fixture spec ilegal ditolak identik oleh api & sandbox-manager; secret pola nyata ditolak (T28); startup p95 (NFR-31) | `sandbox_manager/tests/test_envs_docker.py` (`-m docker`), `tests/unit/integration/test_env_policy.py`, `tests/security/test_t25_env_isolation.py`, `tests/security/test_t28_env_secrets.py`, `tests/load/test_env_startup.py` |
| AX-24 | fixture OpenAPI/AsyncAPI/Protobuf/GraphQL breaking vs non-breaking; merge queue: breaking → `CONTRACT_BREAKING`, consumer diperbarui → lulus, pengecualian ber-izin + audit; story multi-service tanpa E2E lulus → tidak merge; tier S memakai bukti kontrak + mock (OI-39) | `tests/unit/integration/test_contract_rules.py`, `tests/integration/test_mq_contract_gate.py`, `tests/integration/test_mq_integration_gate.py`, `tests/e2e/integration-env.spec.ts` |

Tambahan: T27 (`tests/security/test_t27_heavy_toolchain.py`: limit & label heavy); cross-tenant/project T20 diperluas ke endpoint v2.2. CI gate tambahan: `render_pack_profiles --check`; grep `ANTHROPIC_ADMIN_API_KEY` & import `app.usage.reconcile`; `lint-imports` v2.2; image pack tanpa digest → gagal.

### 14.8 Work package → pemilik, path, DoD, gate (K18)

Pemilik & reviewer mengikuti K18; kolaborator di luar K18 ditandai "delegasi Lead".

| WP | Pemilik (+kolaborator) | Path milik (tulis) | Input wajib | Definition of Done | Gate / reviewer |
|---|---|---|---|---|---|
| WP-6.9 Monitoring biaya & token realtime | AG-07, AG-11 | AG-07: `app/usage/{types,publisher,rollup,budgets,export}.py`, `app/llm/anthropic_provider.py` (usage stream), `services/usage_live.py`, `services/budgets_facade.py`, `api/routes/{usage_live,budgets}.py`, `worker/usage_jobs.py` (rebuild) · AG-11: `features/usage-live/**`, `api/sse/usageStream.ts` · delegasi Lead: `app/llm/base.py` (`UsageProgress`), `alembic/versions/0009_polyglot_usage.py` (OI-38), `core/rbac.py` (`usage:read`, `usage:admin`, `budget:manage`), `core/config.py` (variabel usage) | WP-6.4; §14.3.8–14.3.9 | AX-19; NFR-28 diukur; Σ halaman = Σ `usage_records` | G6 / AG-13 |
| WP-6.10 Rekonsiliasi Admin API (opsional) 🔒 | AG-07, AG-18 | AG-18: `app/integrations/anthropic_admin.py`, job `reconcile` di `worker/usage_jobs.py` · AG-07: `app/usage/reconcile.py`, route `/usage/reconciliation` · delegasi Lead: `core/redaction.py` (pola `sk-ant-admin`), AG-06: compose `worker-integrations` + secret `anthropic_admin_key` (OI-40) | WP-6.9; §14.3.10 | AX-20; kunci hanya di worker-integrations (test T26) | G6 / AG-13 🔒 |
| WP-10.7 Biaya capability ke monitoring | AG-07, AG-19 | AG-19: `record_final(units=…)` di `app/assistant/**`, adapter `ImageProvider`, jalur server tool · AG-07: `config/pricing.yaml` (web search, gambar, code execution), dimensi `capability` di rollup | WP-10.1, WP-6.9 | AX-19 (capability); biaya tak dikenal = `unavailable` | G10 / AG-00 |
| WP-15.0 Framework language pack 🔒 | AG-03, AG-06 | AG-03: `app/packs/**`, `tools/builtin/{repo,packs,deps}.py` (detect_stack, profil, install), `scripts/render_pack_profiles.py`, `sandbox_manager/…/validation.py` · AG-06: `.github/workflows/packs.yml`, `infra/compose.yml` (dep-proxy), `config/dep-proxy.yaml`, `config/deps-blocklist/**`, RUNBOOK rebuild image · delegasi Lead: `app/packs/manifest.py` (kontrak), `scripts/validate_registry.py` (phase P15/P16, OI-46) | WP-6.5, WP-13.0; §14.3.1–14.3.4 | AX-21 untuk `python`/`node` lewat framework baru (perilaku v1 sama); T24 suite hijau; NFR-30 pipeline | G15 / AG-13 🔒 |
| WP-15.1 Pack JVM | AG-03 | `config/language-packs/jvm.yaml`, `sandbox-images/packs/jvm/**`, `templates/jvm/spring-boot/**`, `backend/tests/packs/jvm/**` | WP-15.0 | AX-21 (jvm, L1) | G15 / AG-08 |
| WP-15.2 Pack .NET | AG-03 | idem untuk `dotnet` (`templates/dotnet/aspnetcore/**`) | WP-15.0 | AX-21 (dotnet, L1) | G15 / AG-08 |
| WP-15.3 Pack Go | AG-03 | idem untuk `go` (`templates/go/service/**`) | WP-15.0 | AX-21 (go, L1) | G15 / AG-08 |
| WP-15.4 Pack PHP/Laravel | AG-03 | idem untuk `php` (`templates/php/laravel/**`) | WP-15.0 | AX-21 (php, L1) | G15 / AG-08 |
| WP-15.5 Pack Ruby (L2) | AG-03 | idem untuk `ruby` (tanpa template/run) | WP-15.0 | AX-21 (ruby, L2) | G15 / AG-08 |
| WP-15.6 Pack C++ (L2) | AG-03 | idem untuk `cpp` | WP-15.0 | AX-21 (cpp, L2) | G15 / AG-08 |
| WP-15.7 Perluasan Node/JS/TS/React + Python | AG-03, AG-10 | AG-03: `config/language-packs/{python,node}.yaml`, template FastAPI & Node/React · AG-10: `features/packs/**` (StackPanel, admin pack) | WP-15.0 | AX-21 (python, node); profil snapshot identik | G15 / AG-08 |
| WP-15.8 Pack mobile + target non-Linux | AG-03, AG-06 | AG-03: `config/language-packs/{flutter,react-native}.yaml`, `sandbox-images/packs/{flutter,react-native}/**`, penanda `unsupported_targets` · AG-06: label node heavy, pembacaan CI eksternal (FR-270) | WP-15.0 | AX-21 (L2 web/Android); target non-Linux berstatus CI eksternal | G15 / AG-13 |
| WP-15.9 Pack SQL | AG-03, AG-09 | AG-03: `config/language-packs/sql.yaml`, `sandbox-images/packs/sql/**` (DB lokal di container) · AG-09: `config/support-images.yaml` (entri DB, dipakai ulang P16) | WP-15.0 | AX-21 (sql: migrasi, lint, test DB ephemeral) | G15 / AG-08 |
| WP-15.10 Eval per bahasa + petunjuk + aktivasi D24 | AG-12, AG-15 | AG-12: `backend/evals/packs/**`, laporan eval · AG-15: `agents/prompts/hints/<pack>.md`, registry versi berikutnya (D24 `phase: P15`, `deps.audit` setelah disetujui — OI-47) | WP-15.1..15.9 | AX-22; D24 lulus 8 kriteria §10 | G15 / PO |
| WP-G15 | PO + AG-13 | – | WP-15.0..15.10 | AX-21, AX-22 | PO |
| WP-16.0 ADR & desain keamanan env 🔒 | AG-00, AG-13 | `docs/DECISIONS.md` (ADR-065 termasuk izin `NETWORKS`, OI-41), `app/integration/spec.py` (kontrak), `alembic/versions/0010_integration.py`, `core/rbac.py` (`env:run`, `catalog:write`), kerangka test T25/T28 | WP-G15, WP-13.7 | ADR & threat model disetujui; 0010 upgrade/downgrade diuji | G16 / PO |
| WP-16.1 Env runner 🔒 | AG-03 | `sandbox_manager/…/{envs,env_policy,support_catalog}.py`, `/v1/envs*`, `app/integration/{policy,envs}.py`, `worker/env_jobs.py`, `config/support-images.yaml` · delegasi Lead: AG-06 untuk `infra/compose.node.yml` (`NETWORKS`) | WP-16.0; §14.3.5–14.3.6 | AX-23 (isolasi, kuota, TTL); NFR-31 diukur | G16 / AG-13 🔒 |
| WP-16.2 Katalog service & kontrak | AG-09, AG-08 | AG-09: `app/integration/catalog.py`, `api/routes/{services_catalog,contracts}.py`, `sandbox-images/sandbox-contracts/**` · AG-08: `app/integration/contracts.py` (aturan diff, mock, pengecualian) | WP-16.0; §14.3.7 | AX-24 (diff & mock); mode tier S | G16 / AG-00 |
| WP-16.3 Contract test & E2E + syarat merge queue | AG-08, AG-12 | AG-08: `app/merge_queue/checks.py` (`contract_check`, `integration_e2e`, kode gagal) · AG-12: `tests/integration_env/**`, `tests/e2e/integration-env.spec.ts` | WP-16.1, WP-16.2 | AX-23 (E2E lintas service), AX-24 (merge queue) | G16 / AG-13 |
| WP-16.4 Seed data & migrasi DB di env | AG-09 | `app/integration/seed.py` (secret palsu, rencana seed), profil migrasi di env, entri SQL Server dev | WP-16.1, WP-15.9 | AX-23 (DB + seed); T28 hijau | G16 / AG-08 |
| WP-16.5 UI peta service & env | AG-10 | `features/integration/**` | WP-16.1 | FR-281; state empty/loading/error; SSE status/log | G16 / AG-08 |
| WP-16.6 Aktivasi B11 + contract test C18 | AG-15 | registry versi berikutnya (B11 `phase: P16`), `agents/prompts/{B11,C18}.md` (bagian kontrak), `backend/evals/integration_agents/**` | WP-16.3 | 8 kriteria §10 per agent | G16 / PO |
| WP-G16 | PO + AG-13 | – | WP-16.0..16.6 | AX-23, AX-24 | PO |

### 14.9 Risiko & keputusan terbuka v2.2

| ID | Risiko | Mitigasi | Pemilik |
|---|---|---|---|
| RV-26 | Image toolchain besar (JVM, Android, C++) membebani disk & waktu build (RP-01) | cache layer, node heavy, pack dipasang sesuai `LANGUAGE_PACKS_ENABLED` | AG-06 |
| RV-27 | Dependency confusion lintas ekosistem (RP-02, T24) | proxy per ekosistem, namespace terpin, lockfile, blocklist, D24 | AG-03, AG-13 |
| RV-28 | Env menjadi jalur lateral movement (RP-03, T25) | network internal per env, validasi ganda, `NETWORKS` terbatas, gVisor | AG-03, AG-13 |
| RV-29 | Kunci Admin API bocor = akses admin organisasi (RP-04, T26) | opsional, satu role, allowlist path, redaksi, rotasi | AG-18, AG-13 |
| RV-30 | Selisih estimasi vs resmi karena tarif berubah (RP-05) | `pricing.yaml` bertanggal & berversi; alert selisih; ambang OQ-06 | AG-07, PO |
| RV-31 | Beban publish usage pada task paralel | throttle progress ≤ 1/dtk/panggilan; final selalu terkirim; Redis pub/sub tanpa persistensi | AG-07 |
| RV-32 | Kardinalitas rollup tinggi (banyak project × agent × model) | task di luar kunci rollup; retensi per menit 90 hari [Menebak]; agregat panjang dari `usage_events` | AG-07 |
| RV-33 | Kualitas agent berbeda per bahasa (PID §0 #6) | aktivasi per pack lewat eval (FR-266); pack tetap dapat dipakai manual | AG-12, PO |

| ID | Keputusan | Pemutus | Rekomendasi |
|---|---|---|---|
| D-24 | Urutan landing 0009 (OI-38) | Lead | 0009 di WP-6.9 *schema-first*, FK tertunda; alternatif: pecah 0009 dengan amandemen kontrak |
| D-25 | Kemunculan `worker-integrations` di P6 (OI-40) | Lead + AG-13 | container khusus sejak WP-6.10, hanya antrean `integrations` |
| D-26 | Lingkup rekonsiliasi organisasi vs kunci KodeKantor | Lead + PO | **Diputuskan K21 OI-51:** filter `api_key_ids[]`/`workspace_ids[]` (usage) dan `group_by[]=workspace_id` (cost) dengan workspace & kunci khusus KodeKantor; tanpa filter selisih informatif saja |
| D-27 | Izin `NETWORKS` socket proxy (OI-41) | Lead + AG-13 (ADR-065) | hanya node env tier M/L, network `internal` berlabel |
| D-28 | Produk dep-proxy (OQ-04) | Lead + OPS (WP-15.0) | satu produk multi-ekosistem bila mendukung namespace pinning; selain itu per ekosistem |
| D-29 | Syarat merge queue tier S (OI-39) | PO | kontrak + mock berlabel keterbatasan |
| D-30 | Pemutus pengecualian breaking change (OI-45) | PO | `merge_queue:manage` + alasan + audit; R3 hanya lewat ADR baru |
| D-31 | Ambang eval & selisih (OQ-06, OI-48) | PO | eval ≥ 70% [Menebak]; alert selisih 5% (K19) sambil mengukur NFR-29 ≤ 2% |

---

## Lampiran A — Sumber

Kontrak repo: `docs/PLATFORM_EXPANSION.md` (§9b), `docs/DECISIONS.md` (ADR-001..049), `docs/ARCHITECTURE.md`, `docs/DATA_MODEL.md`, `docs/API_CONTRACTS.md` (§11), `docs/CONFIGURATION.md`, `docs/SECURITY_MODEL.md`, `docs/TOOL_CATALOG.md`, `docs/IMPLEMENTATION_ROADMAP.md`, `docs/IMPLEMENTATION_TRACKER.md`, `docs/wbs/wbs_data.py`; kode snapshot yang diperiksa: `backend/app/{llm/base.py, llm/anthropic_provider.py, tools/base.py, tools/gateway.py, tools/registry.py, tools/policy.py, sandbox/client.py, agents/contracts.py, agents/runtime.py, workflow/steps.py (WorkflowPorts), workflow/approval_handlers.py, services/approval_handlers.py, worker/celery_app.py, core/redaction.py, workspace/paths.py}`, `backend/alembic/versions/0001_initial.py`, `backend/pyproject.toml` (import-linter), `sandbox_manager/src/sandbox_manager/validation.py`, `sandbox-images/sandbox-profiles.yaml`, `infra/compose.yml`, `infra/caddy/Caddyfile`, `frontend/package.json`, `frontend/src/**`.
**v2.2 (§14):** kontrak `docs/POLYGLOT_INTEGRATION_DESIGN.md` §7 (K14–K20) dan §1–§4, `docs/SDLC_SCALE_DESIGN.md` §6 (K13); kode snapshot yang diperiksa untuk §14: `backend/app/llm/{base.py, anthropic_provider.py, pricing.py}` (streaming hanya meneruskan delta teks; usage dari pesan final), `backend/app/core/redaction.py` (pola `sk-ant-…`), `backend/alembic/versions/` (hanya `0001_initial.py`). Fakta Usage & Cost Admin API dan streaming usage: PID §6 (diakses 22 Okt 2026).
Dokumentasi eksternal: daftar di PLATFORM_EXPANSION §10 (diakses 21 Okt 2026). Klaim rinci tentang bentuk blok/beta header Claude dan perilaku GitHub yang ditandai [Kemungkinan Besar] wajib dibuktikan oleh contract test (§10.1) sebelum implementasi dianggap selesai.
