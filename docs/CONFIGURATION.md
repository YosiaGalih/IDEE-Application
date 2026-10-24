# KodeKantor — Environment configuration contract (PRD §7.6)

Satu daftar kanonik. `backend/app/core/config.py`, `sandbox_manager/…/config.py`, `.env.example`, dan `infra/compose.yml` WAJIB memakai nama ini. Aplikasi gagal start dengan pesan aman bila nilai wajib (★) tidak ada/invalid.

| Variabel | Default | Dipakai | Catatan |
|---|---|---|---|
| `APP_ENV` ★ | `development` | api, worker | `development` \| `test` \| `production` |
| `APP_URL` | `http://localhost:5173` | api | URL publik UI |
| `APP_DOMAIN` | `localhost` | api, caddy | untuk preview `preview-*.<domain>` (P1) |
| `SECRET_KEY` ★ | – | api, worker | ≥ 32 karakter; production menolak nilai contoh |
| `LOG_LEVEL` | `INFO` | semua | |
| `CORS_ORIGINS` | `` | api | hanya berlaku di development |
| `COOKIE_SECURE` | `true` | api | wajib `true` di production |
| `SESSION_TTL_HOURS` | `12` | api | idle timeout |
| `SESSION_ABSOLUTE_DAYS` | `7` | api | |
| `DATABASE_URL` ★ | – | api, worker | `postgresql+asyncpg://user:pass@postgres:5432/kodekantor` |
| `REDIS_URL` ★ | – | api, worker | `redis://:pass@redis:6379/0` |
| `LLM_PROVIDER` | `anthropic` | worker, api | `anthropic` \| `scripted` (dilarang di production, ADR-015) |
| `ANTHROPIC_API_KEY` | – | worker (+api smoke) | wajib bila `LLM_PROVIDER=anthropic`; tidak pernah dicatat |
| `ANTHROPIC_BASE_URL` | – | worker | opsional |
| `CLAUDE_MODEL_DEFAULT` ★ | – | worker | model ID dari katalog resmi, divalidasi saat deployment |
| `CLAUDE_MODEL_FAST` | = default | worker | opsional |
| `CLAUDE_MODEL_COMPLEX` | = default | worker | opsional |
| `CLAUDE_MAX_OUTPUT_TOKENS` | `8192` | worker | |
| `CLAUDE_REQUEST_TIMEOUT_SECONDS` | `120` | worker | |
| `CLAUDE_MAX_TOOL_ITERATIONS` | `40` | worker | per agent run |
| `PRICING_PATH` | `config/pricing.yaml` | worker, api | tarif berversi (ADR-004) |
| `TASK_MAX_COST_USD` | `2.0` | worker | budget default per task |
| `TASK_MAX_TOKENS` | `400000` | worker | |
| `TASK_MAX_DEBUG_ATTEMPTS` | `5` | worker | PRD §5.6 |
| `TASK_AUTO_REMEDIATION_MAX` | `1` | worker | siklus perbaikan otomatis setelah User Proxy gagal |
| `TASK_MAX_DURATION_MINUTES` | `60` | worker | batas waktu total per task (FR-132, ADR-031) |
| `TASK_MAX_ITERATIONS` | `3` | worker, api | batas keras total iterasi per task (PRD §6.2 `max_iterations: 3`) |
| `MAX_ACTIVE_TASKS` | `2` | worker | ADR-010 |
| `WORKSPACE_ROOT` ★ | `/srv/kodekantor/workspaces` | api, worker, sandbox-manager | path identik host↔container |
| `PROJECT_QUOTA_MB` | `2048` | api, worker | |
| `UPLOAD_MAX_MB` | `200` | api | |
| `GIT_IMPORT_ALLOWED_HOSTS` | `github.com,gitlab.com,codeberg.org` | worker | ADR-003 |
| `SANDBOX_MANAGER_URL` ★ | `http://sandbox-manager:8100` | api, worker | |
| `SANDBOX_MANAGER_TOKEN` ★ | – | api, worker, sandbox-manager | ≥ 32 karakter |
| `SANDBOX_IMAGE_PYTHON` | `kodekantor/sandbox-python:1` | sandbox-manager | |
| `SANDBOX_IMAGE_NODE` | `kodekantor/sandbox-node:1` | sandbox-manager | |
| `SANDBOX_CPU_LIMIT` | `1.0` | sandbox-manager | |
| `SANDBOX_MEMORY_LIMIT` | `1g` | sandbox-manager | |
| `SANDBOX_PIDS_LIMIT` | `256` | sandbox-manager | |
| `SANDBOX_TIMEOUT_SECONDS` | `300` | sandbox-manager, worker | default bila profil tidak menentukan |
| `SANDBOX_MAX_OUTPUT_BYTES` | `1048576` | sandbox-manager | |
| `SANDBOX_MAX_WORKSPACE_GROWTH_MB` | `512` | sandbox-manager | batas pertumbuhan disk per run (ADR-033) |
| `LLM_REQUIRE_READINESS` | `true` | worker | tolak panggilan LLM bila readiness gate belum `passed` (ADR-032; selalu true di production) |
| `SANDBOX_EGRESS` | `off` | sandbox-manager, worker | `off` \| `allowlist` (ADR-007) |
| `SANDBOX_EGRESS_NETWORK` | `kk-egress` | sandbox-manager | |
| `SANDBOX_RUNTIME` | `` | sandbox-manager | kosong = runc; `runsc` = gVisor (ADR-009) |
| `SANDBOX_PROFILES_PATH` | `/etc/kodekantor/sandbox-profiles.yaml` | sandbox-manager, api, worker | mount read-only |
| `SANDBOX_DRIVER` | `docker` | sandbox-manager | `docker` \| `fake` (test) |
| `SANDBOX_MAX_CONCURRENT_RUNS` | `4` | sandbox-manager | |
| `DOCKER_HOST` | `tcp://docker-socket-proxy:2375` | sandbox-manager | tidak pernah socket langsung di production |
| `AGENT_REGISTRY_PATH` | `/app/agents/registry.yaml` | api, worker | |
| `CELERY_TASK_ALWAYS_EAGER` | `false` | api, worker | `true` hanya untuk test/dev single-process |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `` | semua | opsional |
| `VITE_API_BASE` | `/api/v1` | frontend build | |

## Variabel infra tambahan (compose/CI)

| Variabel | Default | Catatan |
|---|---|---|
| `POSTGRES_PASSWORD`, `REDIS_PASSWORD` | – | dibuat `scripts/gen-secrets.sh` |
| `WORKSPACE_HOST_PATH` | `/srv/kodekantor/workspaces` | di-mount ke path identik; `WORKSPACE_ROOT` diturunkan darinya |
| `CELERY_WORKER_CONCURRENCY` | `2` | ADR-010 |
| `KK_IMAGE_TAG` | `dev` | tag image lokal |
| `KK_BUILD_CA_FILE` | – | opsional, CA proxy saat build (BuildKit secret `build_ca`), tidak disimpan di image |
| `CADDY_HSTS_MAX_AGE` | `31536000` | |
| `BACKUP_DIR`, `BACKUP_RETENTION_DAYS` | `/var/backups/kodekantor`, `14` | `scripts/backup.sh` |

## Variabel perluasan platform (ADR-036..045, Proposed)

| Variabel | Default | Dipakai | Catatan |
|---|---|---|---|
| `PROVIDERS_CONFIG_PATH` | `config/providers.yaml` | worker, api | routing provider per capability (ADR-037) |
| `CAPABILITIES_CONFIG_PATH` | `config/capabilities.yaml` | worker, api | registry modul (ADR-044) |
| `WEB_SEARCH_TOOL_VERSION` | `web_search_20250305` | worker (assistant) | dipin; naikkan lewat ADR setelah uji |
| `WEB_SEARCH_MAX_USES_QUICK` / `_DEEP` | `3` / `20` | worker | ADR-038 |
| `DOC_ENGINE_DEFAULT` | `claude_skills` | worker | `local_sandbox` wajib untuk `confidential` (ADR-039) |
| `IMAGE_PROVIDER` | `` (nonaktif) | worker (media) | diisi setelah ADR-040a |
| `IMAGE_PROVIDER_API_KEY` | – | worker (media) | secret; tidak pernah ke browser/log |
| `IMAGE_DAILY_QUOTA_PER_USER` | `20` | api, worker | |
| `GITHUB_APP_ID` | – | worker-integrations | ADR-041; penanda tangan JWT app (ADR-053) |
| `GITHUB_APP_SLUG` | – | api | untuk URL instalasi |
| `GITHUB_APP_PRIVATE_KEY_PATH` | `/run/secrets/github_app.pem` | **worker-integrations saja** | secret file read-only; `api` tidak di network `outbound` dan tidak memegang pem (ADR-053) |
| `GITHUB_WEBHOOK_SECRET` | – | api | verifikasi `X-Hub-Signature-256` |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | – | `api` (hanya `CLIENT_ID` untuk URL otorisasi), worker-integrations (tukar code; `CLIENT_SECRET` hanya di sini) | otorisasi user GitHub App (tautan identitas), ADR-053 |
| `PORTFOLIO_PUBLIC_BASE_URL` | `https://portfolio.<domain>` | api | ADR-042 |
| `ASSET_SIGNED_URL_TTL_SECONDS` | `300` | api | unduhan file hasil |

## Variabel v2 tambahan (IMPLEMENTATION_DESIGN_V2, Rancangan)

> Ditambahkan oleh desain implementasi v2 (`docs/IMPLEMENTATION_DESIGN_V2.md` §3.2, §5, §8). Belum ada di `Settings`; WP-10.1 menambahkannya dengan default aman (atau WP-INT-01 membatasi `test_config::test_every_documented_variable_is_a_setting` ke bagian non-rancangan). Catatan: `GITHUB_APP_PRIVATE_KEY_PATH` hanya dipakai `worker-integrations` (bukan `api`) — ditetapkan ADR-053 (D-01 selesai, status *Proposed* menunggu PO).

| Variabel | Default | Dipakai | Catatan |
|---|---|---|---|
| `KK_PROCESS_ROLE` | `` (= mode compact, hanya non-production) | semua proses backend | `api` \| `worker-ide` \| `worker-assistant` \| `worker-media` \| `worker-integrations` \| `scheduler` \| `migrate`; validator menolak secret yang tidak diizinkan untuk role (desain v2 §3.2) |
| `WORKER_QUEUES` | `ide,assistant,media,integrations` | worker | antrean Celery yang dikonsumsi; production: satu antrean per container |
| `ASSETS_ROOT` | `/srv/kodekantor/assets` | api, worker-media, worker-integrations | asset store filesystem (K4); path identik host↔container |
| `ASSET_MAX_FILE_MB` | `100` | api, worker-media | batas satu file hasil/unduhan provider |
| `ASSET_QUOTA_MB_PER_USER` | `2048` | api, worker-media | kuota total aset per user |
| `ASSISTANT_UPLOAD_MAX_MB` | `50` | api, caddy | batas unggah Portal AI/foto portfolio |
| `WEB_FETCH_TOOL_VERSION` | `web_fetch_20250910` | worker-assistant | dipin; naik lewat ADR + contract test [Kemungkinan Besar nama versi] |
| `WEB_FETCH_MAX_USES_QUICK` / `_DEEP` | `2` / `10` | worker-assistant | ADR-038 |
| `CODE_EXECUTION_TOOL_VERSION` | `code_execution_20250825` | worker-media | code execution + Agent Skills (ADR-039) [Kemungkinan Besar nama versi] |
| `DOC_SKILLS_VERSION` | `latest` | worker-media | versi Agent Skills `pptx/docx/xlsx/pdf`; dipin ke versi spesifik di WP-10.4 setelah uji |
| `PROVIDER_FILES_TTL_MINUTES` | `60` | worker-media | file Claude Files API dihapus setelah unduh; sweep menghapus yang lebih tua (ADR-046, RV-12) |
| `IMAGE_MAX_N` | `4` | api, worker-media | jumlah gambar per permintaan |
| `KB_INLINE_DOCUMENT_MAX_PAGES` | `100` | worker-assistant | dokumen ≤ batas ini boleh dikirim utuh sebagai blok `document` bersitasi (ADR-046) |
| `SANDBOX_IMAGE_DOCS` | `kodekantor/sandbox-docs:1` | sandbox-manager | image profil `docs.*`, `kb.extract`, `analysis.python`, `portfolio.build_site` |
| `GITHUB_WEBHOOK_SECRET_PREVIOUS` | `` | api | rotasi webhook secret tanpa downtime (RUNBOOK) |
| `GITHUB_WEBHOOK_MAX_BYTES` | `5242880` | api, caddy | body webhook lebih besar → 413 sebelum verifikasi |
| `GITHUB_API_BASE_URL` | `https://api.github.com` | worker-integrations | dapat diarahkan ke mock (test) |
| `GITHUB_API_VERSION` | `2022-11-28` | worker-integrations | header `X-GitHub-Api-Version` dipin [Kemungkinan Besar] |
| `PORTFOLIO_SITE_ROOT` | `/srv/kodekantor/portfolio` | worker-integrations | rilis situs statis (`releases/<v>`, symlink `current`) |

| Variabel infra/CI v2 | Default | Catatan |
|---|---|---|
| `ASSETS_HOST_PATH` | `/srv/kodekantor/assets` | bind mount asset store (pemilik 10001, dibackup) |
| `PORTFOLIO_SITE_HOST_PATH` | `/srv/kodekantor/portfolio` | bind rw ke worker-integrations, ro ke caddy |
| `PORTFOLIO_DOMAIN` | – | host situs portfolio di Caddy (origin terpisah dari `APP_DOMAIN`) |
| `GITHUB_APP_PRIVATE_KEY_FILE` | – | path host file `.pem` (0440 root:10001) untuk compose secret `github_app_pem` |
| `EVAL_MAX_USD` | `10` | batas biaya per run eval harness (`backend/evals/`), bukan variabel aplikasi |

## Variabel v2.1 (SDLC, Developer Workspace v2, tier S/M/L — Rancangan)

> Sumber mengikat: `docs/SDLC_SCALE_DESIGN.md` §6 **K11** (daftar variabel v2.1); pemakaian rinci di `docs/IMPLEMENTATION_DESIGN_V2.md` §13. **Belum ada di `Settings`** — WP-13.0 menambahkannya dengan default aman (pola yang sama dengan catatan "Variabel v2 tambahan" di atas: `test_config::test_every_documented_variable_is_a_setting` akan menuntut field ini). Secret (★s) hanya di env/secret file service yang disebut; tidak pernah ke browser, DB plaintext, log, atau sandbox. Validator tier (FR-245) menolak start bila kombinasi variabel tidak konsisten dengan `DEPLOYMENT_TIER`.

| Variabel | Default | Dipakai | Catatan |
|---|---|---|---|
| `DEPLOYMENT_TIER` | `S` | api, semua worker, scheduler (validator start) | `S` \| `M` \| `L` (ADR-060). Profil tier = data (`app/cluster/tier.py`), bukan cabang kode domain. Tier M mewajibkan `SANDBOX_RUNTIME=runsc`; tier S menolak node remote aktif |
| `EXECUTOR_NODE_TOKEN_TTL_HOURS` | `24` [Menebak] | api (registry node), node executor (jadwal rotasi) | Umur token node; node merotasi pada 50% TTL dengan masa tenggang singkat; token disimpan sha256 (T21). Tidak dipakai di tier S |
| `MERGE_QUEUE_REQUIRED_PROFILES` | `auto` *(turunan)* | worker-ide kontrol (merge queue) | Daftar `profile_id` dipisah koma yang wajib lulus sebelum merge (FR-225); `auto` = profil lint/unit/build dari `profiles_for_stack()`. Dapat dipersempit/diperluas per repo lewat `projects.settings.merge_queue` (policy), tidak pernah dikosongkan bila merge queue aktif |
| `TERMINAL_ENABLED` | `false` | api, sandbox-manager | Saklar global terminal interaktif (ADR-059, FR-241). `true` **dan** `projects.settings.terminal.enabled` (diset owner/admin) keduanya wajib |
| `TERMINAL_SESSION_MAX_MINUTES` | `30` | api, sandbox-manager | Durasi maksimum sesi; container dihentikan otomatis (close 4409) |
| `TERMINAL_IDLE_MINUTES` | `10` | api | Idle timeout sejak input user terakhir (close 4408) |
| `OIDC_ISSUER` | – | api (URL otorisasi, validasi `iss`), worker-integrations (discovery, JWKS, pertukaran code) | URL issuer IdP tier M (FR-250). Kosong = OIDC nonaktif. Diisi sebagian (issuer tanpa client id) → gagal start |
| `OIDC_CLIENT_ID` | – | api, worker-integrations | Client ID publik; divalidasi sebagai `aud` id_token |
| `OIDC_CLIENT_SECRET` ★s | – | **worker-integrations saja** | Secret file read-only; `api` tidak berada di network `outbound` sehingga pertukaran code dilakukan worker (OI-33); tidak pernah di api, node executor, DB, log |
| `SAML_IDP_METADATA_URL` | – | worker-integrations (mengambil & me-refresh metadata), api (memakai metadata ter-cache di DB untuk verifikasi ACS) | Tier L saja (FR-250); terisi pada tier ≠ L → gagal start |
| `SCIM_TOKEN` ★s | – | api | Bearer token SCIM untuk satu organisasi (tier L, FR-251); dibandingkan sha256 + `compare_digest`. Multi-organisasi memakai tabel `scim_tokens` (token per organisasi, ter-hash). Terisi pada tier ≠ L → gagal start |
| `SPARSE_CHECKOUT_ENABLED` | `false` | worker-ide / node executor (worktree task), worker-integrations (clone/import) | Sparse/partial checkout per task untuk repo besar (FR-253, ADR-062): `--filter=blob:none` + `git sparse-checkout set --cone` dengan pola dari `project_repositories.sparse_patterns` |

Konstanta desain v2.1 yang **sengaja tidak** dijadikan variabel lingkungan sampai diukur (bukan bagian K11; nilai [Menebak], didefinisikan sebagai konstanta kode/`policy_settings`): batas sesi terminal per user (1 aktif/project, 10 baru/jam), batas frame & laju input terminal, `NODE_DEAD_AFTER` (90 dtk), masa tenggang rotasi token node (10 menit), TTL token bootstrap node (1 jam), TTL tiket WebSocket (30 dtk), batas jumlah task per `ProgramPlan` (200). Bila PO/Lead ingin dapat dikonfigurasi, tambahkan lewat perubahan kontrak (K11) terlebih dahulu.

| Variabel infra/CI v2.1 | Default | Catatan |
|---|---|---|
| `OIDC_CLIENT_SECRET_FILE` *(turunan)* | – | path host secret file untuk compose secret `oidc_client_secret` (di-mount hanya ke `worker-integrations`) |
| `SCIM_TOKEN_FILE` *(turunan)* | – | path host secret file untuk compose secret `scim_token` (di-mount hanya ke `api`) |
| `NODE_TOKEN_FILE` *(turunan)* | – | di node executor: lokasi secret file token node (ditulis saat registrasi, 0400) |

## Variabel v2.2 (language packs, lingkungan integrasi, monitoring biaya & token — Rancangan)

> Sumber mengikat: `docs/POLYGLOT_INTEGRATION_DESIGN.md` §7 **K19** (daftar variabel v2.2); pemakaian rinci di `docs/IMPLEMENTATION_DESIGN_V2.md` §14. **Belum ada di `Settings`** — WP-6.9 (variabel usage), WP-6.10 (rekonsiliasi), WP-15.0 (pack & proxy), WP-16.0/16.1 (env) menambahkannya dengan default aman (catatan `test_config::test_every_documented_variable_is_a_setting` sama dengan bagian v2/v2.1). Secret (★s) hanya di env/secret file service yang disebut; tidak pernah ke browser, DB, log, event, atau sandbox. Default yang tidak ditetapkan K19 bertanda *(turunan)*.

| Variabel | Default | Dipakai | Catatan |
|---|---|---|---|
| `PRICING_PATH` (v1, diperluas) | `config/pricing.yaml` | api, worker (semua antrean), worker-integrations | Satu-satunya sumber tarif (K21 OI-42; `PRICING_TABLE_PATH` tidak diperkenalkan). v2.2 menambah entri server tool & vendor, tanggal berlaku, dan versi tarif untuk estimasi biaya realtime dan pembanding rekonsiliasi (FR-283, FR-288). Tarif tidak dikenal → biaya `unavailable`, bukan 0 |
| `USAGE_LIVE_ENABLED` | `true` | api (SSE `/usage/live`), worker (publish `usage.event`) | `false` → stream & halaman live mati (403 `POLICY_DENIED`), pencatatan ledger + `usage_events` + rollup tetap berjalan (AX-19 tetap dapat diaudit) |
| `USAGE_RECONCILE_ENABLED` | `false` | worker-integrations (job), api (status panel) | Rekonsiliasi Usage & Cost Admin API (FR-288). `true` tanpa `ANTHROPIC_ADMIN_API_KEY` → start tetap jalan, status `no_credentials` → `RECONCILIATION_UNAVAILABLE` |
| `ANTHROPIC_ADMIN_API_KEY` ★s | – | **worker-integrations saja** | Admin API key organisasi (`sk-ant-admin01-…`); tidak tersedia untuk akun individual. Secret file read-only; validator `KK_PROCESS_ROLE` menolak start bila variabel ini terisi pada role selain `worker-integrations`; dipakai **hanya** untuk `GET /v1/organizations/usage_report/messages` dan `GET /v1/organizations/cost_report`; diredaksi (pola `sk-ant-admin` + literal) dan tidak pernah tampil (tanpa `secret_hint`) — FR-292, T26 |
| `USAGE_RECONCILE_INTERVAL_SECONDS` | `60` | worker-integrations, scheduler (beat) | Minimum **60** (polling ≥ 60 dtk, sesuai saran Admin API 1×/menit); nilai < 60 → gagal start |
| `USAGE_VARIANCE_ALERT_PCT` | `5` | worker-integrations, api | Ambang selisih token/biaya internal vs resmi (%) yang ditandai `variance` (FR-289). Target akurasi NFR-29 (≤ 2%) diukur terpisah; ini ambang alert per menit, NFR-29 target harian (K21 OI-48); ambang final OQ-06 (PO) |
| `USAGE_RECONCILE_API_KEY_IDS` | kosong | worker-integrations | Daftar ID kunci API milik KodeKantor (mis. `apikey_…`) untuk filter `api_key_ids[]` endpoint usage (K21 OI-51). Bukan secret; ID dicatat saat readiness |
| `USAGE_RECONCILE_WORKSPACE_ID` | kosong | worker-integrations | Workspace Claude Console khusus KodeKantor (`wrkspc_…`): filter `workspace_ids[]` endpoint usage dan `group_by[]=workspace_id` endpoint cost. Tanpa filter apa pun → selisih berlabel "informatif", alert selisih dimatikan |
| `LANGUAGE_PACKS_ENABLED` | `python,node` *(turunan — pack snapshot; K19 hanya menyebut "daftar")* | api, worker, sandbox-manager | Daftar `pack_id` dipisah koma yang **boleh** diaktifkan (FR-259): `python`, `node`, `jvm`, `dotnet`, `go`, `php`, `ruby`, `cpp`, `react-native`, `flutter`, `sql`. Pack di luar daftar → `PACK_NOT_ENABLED` walau terdaftar; aktivasi untuk agent tetap butuh eval (FR-266) |
| `DEP_PROXY_URLS` | `` (kosong = tidak ada instalasi dependensi) | worker, sandbox-manager | Proxy repositori **per ekosistem** (ADR-064, FR-263), format `ecosystem=url` dipisah koma, mis. `pypi=http://dep-proxy:8081/repository/pypi/,npm=http://dep-proxy:8081/repository/npm/,maven=…,nuget=…,gomod=…,packagist=…,rubygems=…,pub=…,conan=…`. Hanya profil `*.deps_install` yang menerima URL ini; tetap mensyaratkan `SANDBOX_EGRESS=allowlist`. Tanpa kredensial di URL (validator menolak `user:pass@`) |
| `INTEGRATION_ENV_ENABLED` | `false` | api, worker, sandbox-manager (node), validator tier | Lingkungan integrasi multi-service (ADR-065, FR-275). Hanya tier M/L: `true` pada `DEPLOYMENT_TIER=S` → gagal start *(turunan)* (tier S = mode kontrak + mock, FR-276). Juga syarat `NETWORKS=1` pada docker-socket-proxy node (OI-41) |
| `INTEGRATION_ENV_MAX_SERVICES` | `8` | api, sandbox-manager | Batas jumlah service + pendukung per env (FR-280) |
| `INTEGRATION_ENV_TTL_MINUTES` | `60` | api, sandbox-manager, scheduler (sweep) | TTL maksimum per env; teardown otomatis 100% (NFR-31) |
| `INTEGRATION_ENV_CPU_LIMIT` | `4.0` [Menebak] *(turunan)* | api, sandbox-manager | Total CPU per env (Σ service), ≤ kapasitas node |
| `INTEGRATION_ENV_MEMORY_LIMIT` | `8g` [Menebak] *(turunan)* | api, sandbox-manager | Total RAM per env (Σ service) |

Konstanta desain v2.2 yang **sengaja tidak** dijadikan variabel lingkungan sampai diukur (nilai [Menebak], konstanta kode/`policy_settings`; ubah lewat perubahan kontrak K19 bila perlu): throttle `usage.event` progress (≤ 1/dtk/panggilan), jendela laju pemakaian (15 menit), jendela tarik rekonsiliasi (60 menit) dan masa *settling* (≈ 5 menit), retensi `usage_rollups_minute` (90 hari) dan `usage_events` (400 hari), env aktif per project (1) dan per node (2), batas file pindaian deteksi stack (20.000), ukuran laporan test ternormalisasi (5 MB), umur image pack sebelum `stale` (30 hari, NFR-30). Image pack dibaca dari manifest `config/language-packs/<pack>.yaml` (digest dipin), bukan variabel `SANDBOX_IMAGE_*`; `SANDBOX_IMAGE_PYTHON`/`SANDBOX_IMAGE_NODE` v1 tetap berlaku sampai WP-15.0 memindahkannya ke manifest.

| Variabel infra/CI v2.2 | Default | Catatan |
|---|---|---|
| `ANTHROPIC_ADMIN_API_KEY_FILE` *(turunan)* | – | path host secret file untuk compose secret `anthropic_admin_key` (0440 root:10001), di-mount **hanya** ke `worker-integrations` |
| `DEP_PROXY_CACHE_HOST_PATH` *(turunan)* | `/srv/kodekantor/dep-cache` | volume cache dep-proxy (berbatas, tidak di-backup) |
| `DEP_PROXY_CACHE_MAX_GB` *(turunan)* | `20` [Menebak] | batas cache dep-proxy (T27) |
| `PACK_IMAGE_REBUILD_DAYS` *(turunan)* | `30` | jadwal rebuild image pack di CI (NFR-30); bukan variabel aplikasi |
