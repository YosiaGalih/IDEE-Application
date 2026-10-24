# KodeKantor — Data Model (PostgreSQL 16)

Konvensi (PRD §8.1): PK `id UUID` (uuid4, dibuat aplikasi) kecuali dinyatakan lain; semua tabel punya `created_at timestamptz default now()`; tabel mutable punya `updated_at`; enum disimpan sebagai `VARCHAR` + `CHECK` (lebih mudah dimigrasi daripada `CREATE TYPE`); FK `ON DELETE` eksplisit; isi file workspace **tidak** disimpan di DB (hanya referensi `storage_ref` relatif terhadap `WORKSPACE_ROOT`).

ORM: `backend/app/models/*.py` (SQLAlchemy 2.0 typed `Mapped[...]`). Migrasi: `backend/alembic/versions/0001_initial.py`.

## Identity & access (M01, M20)

### users
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| email | varchar(320) unique, lower-cased | |
| display_name | varchar(120) | |
| password_hash | varchar(255) | argon2id |
| status | varchar(16) check in (`active`,`disabled`) | |
| last_login_at | timestamptz null | |
| failed_login_count | int default 0 | lockout sementara |
| locked_until | timestamptz null | |
| created_at, updated_at | timestamptz | |

### user_roles
| id uuid pk | user_id fk users cascade | role_name check in (`owner`,`admin`,`developer`,`reviewer`,`readonly`) | scope varchar(64) — `global` atau `project:<uuid>` | created_at |
Unique `(user_id, scope)`.

### sessions
| id uuid pk | user_id fk cascade | token_hash char(64) unique (sha256 token) | csrf_token varchar(64) | created_at | expires_at | last_seen_at | ip varchar(64) | user_agent varchar(255) | revoked_at null |

### password_reset_tokens
| id | user_id fk cascade | token_hash char(64) unique | expires_at | used_at null | created_by (uuid null, admin) | created_at |

### policy_settings  (M20)
| key varchar(100) pk | value jsonb | description text | updated_by uuid null | updated_at |
Kunci awal: `feature_flags`, `quotas` (max_projects_per_user, max_workspace_mb), `retention` (artifacts_days, task_events_days, audit_days), `provider_allowlist`, `sandbox_egress` (`off`|`allowlist`), `plan_approval_required_default`.

## Projects & repositories (M03, M04)

### projects
| id | owner_id fk users restrict | name varchar(120) | slug varchar(140) | description text | stack jsonb (hasil detect: languages, frameworks, package_managers, test_profiles) | workspace_ref varchar(255) (`<project_id>`) | default_branch varchar(100) default `main` | status check in (`active`,`archived`,`deleting_pending_approval`,`deleted`) | settings jsonb (`plan_approval_required`, `auto_remediation`, budget default) | created_at, updated_at |
Unique `(owner_id, slug)`.

### repositories
| id | project_id fk cascade unique | provider check in (`local`,`template`,`upload`,`git_https`) | remote_url varchar(500) null | branch varchar(100) | credential_ref varchar(200) null (nama secret, bukan nilai) | sync_state check in (`ready`,`importing`,`indexing`,`error`) | index_commit varchar(64) null | last_indexed_at null | error text null | created_at, updated_at |

### repo_index_entries (M04/M16)
| id bigserial pk | project_id fk cascade | path varchar(1000) | kind check in (`file`,`symbol`) | symbol varchar(300) null | symbol_kind varchar(40) null | line int null | language varchar(40) null | size_bytes int null | sha256 char(64) null | commit varchar(64) | 
Index `(project_id, kind)`, `(project_id, symbol)`; trigram index pada `path` bila ekstensi `pg_trgm` tersedia (opsional).

## Requirements & acceptance (M14)

### requirements
| id | project_id fk cascade | task_id fk tasks cascade null | baseline_version int | source_message_id uuid null | key varchar(20) (`REQ-1`…) | title varchar(300) | description text | priority check in (`must`,`should`,`could`) | status check in (`draft`,`approved`,`superseded`,`changed`) | created_by varchar(80) (`user:<id>` / `agent:A02`) | created_at, updated_at |

### acceptance_criteria
| id | requirement_id fk cascade | task_id fk cascade | key varchar(20) (`AC-1`…) | criterion text | verification_method check in (`test`,`api`,`browser`,`review`,`manual`) | mandatory bool | status check in (`pending`,`passed`,`failed`,`unverified`,`waived_by_user`,`blocked`) | evidence_refs jsonb (list string `test_run:<id>`, `artifact:<id>`, `finding:<id>`, `diff:<task_id>`) | rationale text null | waived_by uuid null | waived_at null | updated_at |

## Conversations (M06)

### conversations
| id | project_id fk cascade | user_id fk | title varchar(200) | created_at, updated_at |

### messages
| id | conversation_id fk cascade | project_id | user_id null | role check in (`user`,`assistant`,`system`,`agent`) | agent_id varchar(40) null | content text (diredaksi) | context_refs jsonb (`[{type:"file",path,line_start,line_end}|{type:"task",id}|{type:"test_run",id}|{type:"diff",task_id}]`) | task_id null | created_at |

## Tasks & workflow (M08)

### tasks
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| project_id | fk projects cascade | |
| parent_task_id | fk tasks null | sub-task / remediation |
| conversation_id | fk null | |
| title | varchar(200) | |
| prompt | text | diredaksi |
| workflow_type | check in (`create`,`modify`,`debug`,`test`,`review`,`docs`) | |
| status | varchar(32) check (lihat ARCHITECTURE §5.1) | ditulis HANYA oleh `workflow.states.transition` |
| status_reason | varchar(200) null | mis. `budget_exceeded` |
| current_step | varchar(40) null | `intake`,`context`,`plan`,`implement`,`test`,`debug`,`review`,`user_proxy`,`gate`,`apply` |
| priority | check in (`low`,`normal`,`high`) | |
| budget_limit | jsonb | `{max_cost_usd, max_tokens, max_tool_iterations}` |
| checkpoint | jsonb default `{}` | |
| branch_name | varchar(120) null | `kk/task-<8hex>` |
| base_commit | varchar(64) null | commit default branch saat worktree dibuat |
| iteration | int default 1 | naik pada remediation / reopen |
| debug_attempts | int default 0 | |
| cancel_requested | bool default false | |
| pause_requested | bool default false | ADR-031 |
| lease_owner | varchar(120) null | |
| lease_expires_at | timestamptz null | |
| event_seq | bigint default 0 | counter event |
| version | int default 1 | optimistic lock |
| error_code / error_message | varchar(64) / text null | |
| created_by | fk users | |
| created_at, updated_at, started_at, completed_at | timestamptz | |
Index `(project_id, status)`, `(status, lease_expires_at)`.

### task_dependencies
| id | task_id fk cascade | depends_on_task_id fk cascade | dependency_type check in (`blocks`,`relates`) | unique(task_id, depends_on_task_id) |
> Catatan: langkah plan di dalam satu task disimpan di `tasks.checkpoint.plan` (DAG langkah); `task_dependencies` untuk relasi antar-task (sub-task/remediation).

### task_events
| id bigserial pk | task_id fk cascade | seq bigint | type varchar(60) | payload jsonb (diredaksi) | created_at | unique(task_id, seq) |

## Agents & tools (M07, M09)

### agent_profiles
| agent_id varchar(40) pk (mis. `A07`) | slug varchar(60) unique (`user_proxy`) | name | group varchar(40) | version int | enabled bool | mvp_mode check in (`llm`,`profile`,`tool`,`planned`) | priority check in (`P0`,`P1`,`P2`) | description text | capabilities jsonb (list string) | tools jsonb (allowlist nama tool) | permission_profile jsonb | model_tier check in (`fast`,`default`,`complex`) | limits jsonb (`max_iterations`, `timeout_seconds`) | runtime_agent varchar(40) null (untuk mode `profile`) | updated_at |

### agent_runs
| id | task_id fk cascade | agent_id fk agent_profiles | agent_version int | step varchar(40) | model_provider varchar(40) | model_name varchar(100) | prompt_version char(12) | status check in (`running`,`succeeded`,`failed`,`cancelled`,`budget_exceeded`,`approval_required`) (ADR-025) | input jsonb (ringkas, diredaksi) | output jsonb null | iterations int | input_tokens / output_tokens / cache_read_tokens / cache_creation_tokens int null | estimated_cost numeric(12,6) null | error text null | started_at | finished_at null | duration_ms int null |

### tool_calls
| id | task_id fk cascade | agent_run_id fk null | tool_name varchar(80) | risk_level smallint | idempotency_key char(64) unique null | input_hash char(64) | sanitized_input jsonb | status check in (`ok`,`error`,`denied`,`timeout`,`approval_required`) | output_summary text (≤ 2 KB) | output_ref uuid null (artifact) | approval_id fk null | duration_ms int | created_at |

## Evidence (M12, M13)

### artifacts
| id | project_id | task_id fk cascade null | type check in (`patch`,`diff`,`log`,`test_report`,`junit`,`coverage`,`screenshot`,`acceptance_report`,`context_package`,`plan`,`other`) | storage_ref varchar(500) (relatif WORKSPACE_ROOT) | checksum char(64) | size_bytes bigint | mime varchar(100) | metadata jsonb | created_at |

### test_runs
| id | project_id | task_id fk cascade null | suite check in (`lint`,`unit`,`integration`,`build`,`e2e`,`custom`) | command_profile varchar(80) | argv jsonb | sandbox_run_id varchar(80) | status check in (`running`,`passed`,`failed`,`error`,`timeout`,`cancelled`) | failure_kind check in (`none`,`assertion`,`lint`,`build`,`dependency`,`runner_crash`,`timeout`,`infra`) (ADR-035) | read_only bool | exit_code int null | passed / failed / skipped / errors int null | coverage_percent numeric(5,2) null (HANYA dari file coverage) | log_artifact_id fk null | junit_artifact_id fk null | environment_id varchar(200) (image@digest) | triggered_by varchar(80) | started_at | finished_at null | duration_ms int null |

### findings
| id | task_id fk cascade | project_id | reviewer_type check in (`code_review`,`security`,`user_proxy`,`static`) | author_type check in (`agent`,`user`,`scanner`) | author_id varchar(80) | agent_run_id null | severity check in (`info`,`low`,`medium`,`high`,`critical`) | category varchar(60) | title varchar(300) | file_path varchar(1000) null | line_start / line_end int null | evidence text | recommendation text | status check in (`open`,`accepted`,`dismissed`,`fixed`) | user_feedback text null | created_at, updated_at |

### acceptance_reports
| id | task_id fk cascade | iteration int | status check in (`passed`,`needs_changes`,`blocked`,`unverified`) | summary text | confidence check in (`low`,`medium`,`high`) | criteria_results jsonb (`[{criterion_id,key,status,evidence_refs,rationale,downgraded:bool,downgrade_reason,subjective:bool}]`) | gaps jsonb | risks jsonb | next_actions jsonb | agent_run_id fk null | artifact_id fk null | created_at |

## Approvals (PRD §10.4)

### approvals
| id | project_id fk cascade | task_id fk cascade null | action_type check in (`apply_changes`,`budget_increase`,`tool_r3`,`project_delete`,`deploy_staging`,`deploy_production`,`git_push`,`waive_criterion`) | risk_level smallint | summary text | payload jsonb | payload_hash char(64) | requested_by varchar(80) | status check in (`pending`,`approved`,`rejected`,`expired`,`consumed`,`superseded`) | handler_status check in (`pending`,`succeeded`,`failed`,`not_required`) | handler_attempts int | handler_error text null (ADR-024) | decided_by fk users null | decided_at null | decision_note text null | expires_at null | consumed_at null | created_at |
Index `(project_id, status)`.

## Provider & usage (M17)

### provider_configs
| id | provider check in (`anthropic`) | base_url varchar(300) null | model_policy jsonb (`{default, fast, complex}` model ID) | secret_ref varchar(100) (nama env/secret file, mis. `ANTHROPIC_API_KEY`) | enabled bool | pricing_version varchar(40) | readiness_status check in (`unknown`,`passed`,`failed`) | readiness_checked_at null | readiness_report jsonb (ADR-032) | created_by uuid null | created_at, updated_at |

### usage_records
| id | project_id | task_id fk null | agent_run_id fk null | provider | model | request_id varchar(120) null | input_tokens / output_tokens / cache_creation_tokens / cache_read_tokens int null | estimated_cost numeric(12,6) null | actual_cost numeric(12,6) null | currency char(3) default `USD` | cost_status check in (`estimated`,`partial`,`actual`,`unavailable`) — `actual` dicadangkan (ADR-025) | pricing_version varchar(40) null | retry_count int | duration_ms int | status varchar(20) | created_at |

## Audit (M19)

### audit_events  (append-only — trigger `audit_events_no_mutation` menolak UPDATE/DELETE)
| id bigserial pk | ts timestamptz default now() | actor_type check in (`user`,`agent`,`system`) | actor_id varchar(80) | action varchar(80) (mis. `auth.login`, `tool.denied`, `approval.decided`) | resource_type varchar(40) | resource_id varchar(80) | project_id uuid null | result check in (`success`,`denied`,`error`) | metadata jsonb (diredaksi) | correlation_id varchar(80) | ip varchar(64) null |

## Knowledge (M16)

### project_memory
| id | project_id fk cascade | type check in (`adr`,`decision`,`summary`,`context`,`doc`) | title | content text (tidak boleh berisi secret — redaksi saat simpan) | content_ref null | source_commit varchar(64) null | freshness check in (`fresh`,`stale`) | created_by varchar(80) | created_at, updated_at |

## Deployments (M18, P2)

### deployments
| id | project_id | task_id null | environment check in (`staging`,`production`) | status check in (`preflight_running`,`preflight_failed`,`ready`,`deploying`,`running`,`failed`,`rolled_back`,`stopped`) | preflight jsonb | approval_id null | sandbox_run_id null | url varchar(300) null | rollback_of uuid null | created_by | created_at, updated_at |

## Retention (default, dapat diubah via `policy_settings.retention`)
- `task_events` 90 hari, artefak log/screenshot 30 hari, artefak patch & acceptance report mengikuti umur task, `audit_events` 365 hari (tidak dihapus oleh user biasa), workspace project terhapus 7 hari setelah `deleted` (soft delete + approval).

---

## Ekstensi v2 — Portal AI, GitHub App, Portfolio, Capability (Rancangan)

> **Status: rancangan, belum diimplementasikan; berlaku setelah gate G3.** Sumber: `docs/PLATFORM_EXPANSION.md` §4.6/§5.6/§6.4/§9b, `docs/IMPLEMENTATION_DESIGN_V2.md` §7 (rantai migrasi, urutan landing, retensi). Konvensi bagian atas tetap berlaku (UUID dibuat aplikasi, `created_at`/`updated_at`, enum `VARCHAR + CHECK` per ADR-018, FK `ON DELETE` eksplisit). Isi file **tidak** disimpan di DB: aset di `ASSETS_ROOT` (kolom `storage_ref` relatif), workspace di `WORKSPACE_ROOT`. Tidak ada kolom yang menyimpan token GitHub, API key, atau private key (diuji AX-01).
> Singkatan tipe: `sens` = `varchar(16) check in ('public','internal','confidential')`; `tier` = `varchar(16) check in ('fast','default','complex')`.

### V2.1 Migrasi 0002_ide_backlog — kolom yang didokumentasikan di atas tetapi belum ada di `0001_initial`

| Tabel | Kolom / constraint | ADR |
|---|---|---|
| `tasks` | `pause_requested bool not null default false` | 031 |
| `agent_runs` | CHECK `status` + `'approval_required'` | 025 |
| `usage_records` | CHECK `cost_status` + `'partial'` | 025 |
| `approvals` | `handler_status varchar(16) not null default 'pending'` check in (`pending`,`succeeded`,`failed`,`not_required`); `handler_attempts int not null default 0`; `handler_error text null`; index parsial `(handler_status) where handler_status in ('pending','failed') and status in ('approved','rejected')` | 024 |
| `test_runs` | `failure_kind varchar(20) not null default 'none'` check in (`none`,`assertion`,`lint`,`build`,`dependency`,`runner_crash`,`timeout`,`infra`); `read_only bool not null default false` | 035, 033 |
| `provider_configs` | `readiness_status varchar(16) not null default 'unknown'` check in (`unknown`,`passed`,`failed`); `readiness_checked_at timestamptz null`; `readiness_report jsonb not null default '{}'` | 032 |

ADR-021 tidak membutuhkan DDL (nilai status task tidak berubah).

### V2.2 Migrasi 0003_platform_assistant — perubahan tabel existing

| Tabel | Perubahan |
|---|---|
| `agent_runs` | `task_id` → **nullable**; + `thread_id uuid null fk assistant_threads(id) on delete cascade`; + `message_id uuid null fk assistant_messages(id) on delete set null`; + `owner_id uuid null fk users on delete set null`; CHECK `ck_agent_runs_scope`: `num_nonnulls(task_id, thread_id) = 1`; index `(thread_id)` |
| `tool_calls` | `task_id` → **nullable**; + `thread_id uuid null fk assistant_threads(id) on delete cascade`; + `executor varchar(12) not null default 'backend'` check in (`backend`,`sandbox`,`provider`,`worker`); + `capability varchar(40) null`; + `provider_request_id varchar(120) null`; CHECK `num_nonnulls(task_id, thread_id) <= 1` (job tool-mode tanpa scope, mis. impor repo/publish); index `(thread_id)` |
| `usage_records` | + `owner_id uuid null fk users on delete set null`; + `thread_id uuid null fk assistant_threads(id) on delete set null`; + `message_id uuid null`; + `capability varchar(40) null`; + `server_tool_use jsonb not null default '{}'` (`{web_search_requests, web_fetch_requests}`); index `(owner_id, created_at)`, `(capability, created_at)` |
| `approvals` | `project_id` → **nullable**; + `owner_id uuid null fk users on delete set null` (pemilik resource); + `scope_type varchar(16) not null default 'project'` check in (`project`,`portfolio`,`integration`); CHECK `scope_type <> 'project' or project_id is not null`; index `(owner_id, status)`. Nilai `action_type` baru ditambahkan di 0004 (`github_push_pr`) dan 0005 (`portfolio_publish`) |
| `policy_settings` | kunci baru (data): `web_research.domains` (`{allowed:[], blocked:[]}`), `retention.assistant` (§V2.8), `provider_kill_switch` (`{capability: bool}`), `feature_flags.capabilities` |

Aturan pemakaian ledger: token LLM (IDE maupun Portal AI) → `usage_records` (+`capability`); unit non-token (pencarian web, gambar, detik code execution, dokumen, halaman KB) → `capability_usage`. Anggaran USD per capability = jumlah keduanya; tidak ada baris yang dicatat dua kali.

### V2.3 Migrasi 0003 — tabel baru: capability & aset

#### capability_settings
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| capability | varchar(40) check `capability ~ '^[a-z][a-z0-9_]{1,39}$'` | divalidasi juga terhadap `config/capabilities.yaml` |
| scope | varchar(64) | `global` \| `role:<role>` \| `user:<uuid>` (yang paling spesifik menang) |
| enabled | bool null | null = mewarisi scope lebih umum |
| kill_switch | bool not null default false | |
| limits | jsonb not null default '{}' | `{monthly_usd, monthly_units:{unit:n}, daily_units:{unit:n}}` |
| config | jsonb not null default '{}' | mis. engine default, domain policy khusus capability |
| updated_by | uuid null fk users on delete set null | |
| created_at, updated_at | timestamptz | |
Unique `(capability, scope)`.

#### capability_usage
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| owner_id | uuid null fk users on delete set null | |
| capability | varchar(40) | |
| unit | varchar(24) check in (`web_search`,`web_fetch`,`code_exec_seconds`,`image`,`document`,`kb_ingest_page`,`analysis_run`) | |
| quantity | numeric(14,4) check `quantity >= 0` | |
| estimated_cost | numeric(12,6) null | |
| currency | char(3) default `USD` | |
| cost_status | varchar(16) check in (`estimated`,`partial`,`actual`,`unavailable`) | `actual` dicadangkan (ADR-025) |
| provider | varchar(40) | `anthropic` \| `<image vendor>` \| `local` |
| model | varchar(100) null | |
| pricing_version | varchar(40) null | |
| thread_id | uuid null fk assistant_threads on delete set null | |
| message_id | uuid null fk assistant_messages on delete set null | |
| task_id | uuid null fk tasks on delete set null | |
| project_id | uuid null | |
| request_id | varchar(120) null | |
| created_at | timestamptz | |
Index `(owner_id, capability, created_at)`, `(thread_id)`. Retensi 400 hari.

#### assets  (registry objek biner; satu-satunya sumber `storage_ref` untuk unggahan & hasil)
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| owner_id | uuid fk users on delete cascade | |
| kind | varchar(16) check in (`upload`,`generated`,`derived`,`site_bundle`) | `derived` = thumbnail/konversi |
| mime | varchar(100) | hasil sniff magic bytes (bukan header klien) |
| filename | varchar(255) | disanitasi |
| size_bytes | bigint | |
| sha256 | char(64) | |
| storage_ref | varchar(500) unique | relatif `ASSETS_ROOT`: `<owner>/<yyyy>/<mm>/<asset_id>` |
| sensitivity | sens default `internal` | |
| source | varchar(24) check in (`user`,`claude_skills`,`local_sandbox`,`image_provider`,`analysis`,`kb_extract`,`portfolio_builder`,`github`) | |
| provenance | jsonb not null default '{}' | `{provider, model, engine, prompt_hash, request_id, c2pa_present, agent_id}` |
| pinned | bool not null default false | |
| retention_until | timestamptz null | |
| deleted_at | timestamptz null | |
| created_at | timestamptz | |
Index `(owner_id, created_at)`; index parsial `(retention_until) where deleted_at is null and pinned = false`.

#### moderation_events
| id uuid pk | owner_id uuid null fk users set null | subject_type varchar(24) check in (`image_prompt`,`image_output`,`text`,`portfolio_content`,`kb_upload`) | subject_hash char(64) | stage varchar(16) check in (`policy`,`classifier`,`vendor_pre`,`vendor_post`) | decision varchar(12) check in (`allow`,`deny`,`review`,`flagged`) | categories jsonb default '[]' | thread_id uuid null fk assistant_threads set null | message_id uuid null | provider varchar(40) null | created_at |
Index `(owner_id, created_at)`, `(subject_hash)`. Retensi 365 hari. Tidak menyimpan prompt/gambar mentah.

### V2.4 Migrasi 0003 — tabel baru: Portal AI

#### assistant_spaces
| id uuid pk | owner_id uuid fk users on delete cascade | name varchar(120) | description text null | sensitivity sens default `internal` | default_model_tier tier default `default` | archived_at timestamptz null | created_at, updated_at |
Unique `(owner_id, name)`.

#### custom_assistants
| id uuid pk | owner_id uuid fk users on delete cascade | name varchar(120) | description text null | system_prompt text (≤ 8000, diredaksi; diperlakukan sebagai data) | capabilities jsonb default '[]' | kb_collection_ids jsonb default '[]' | model_tier tier default `default` | shared_with_roles jsonb default '[]' | enabled bool default true | version int default 1 | created_at, updated_at |
Unique `(owner_id, name)`.

#### assistant_threads
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| owner_id | uuid fk users on delete cascade | |
| space_id | uuid null fk assistant_spaces on delete set null | |
| kind | varchar(16) check in (`chat`,`portfolio`,`system`) default `chat` | `portfolio` = run K49/K50 |
| title | varchar(200) | |
| model_tier | tier default `default` | |
| assistant_id | uuid null fk custom_assistants on delete set null | |
| sensitivity | sens default `internal` | tidak boleh lebih rendah dari space (dicek service) |
| event_seq | bigint not null default 0 | counter `assistant_events.seq` |
| provider_state | jsonb not null default '{}' | `{container_id, container_expires_at}` (Skills; hanya non-confidential) |
| last_message_at | timestamptz null | |
| archived_at | timestamptz null | |
| created_at, updated_at | timestamptz | |
Index `(owner_id, last_message_at desc)`, `(space_id)`.

#### assistant_messages
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| thread_id | uuid fk assistant_threads on delete cascade | |
| owner_id | uuid fk users on delete cascade | |
| role | varchar(12) check in (`user`,`assistant`,`system`) | |
| parent_id | uuid null fk assistant_messages on delete set null | pesan user yang dijawab / versi sebelumnya |
| superseded_by | uuid null | regenerate / edit |
| mode | varchar(20) check in (`chat`,`research_quick`,`research_deep`,`kb`,`analysis`,`document`,`image`) | |
| status | varchar(12) check in (`queued`,`streaming`,`completed`,`failed`,`cancelled`,`blocked`) | |
| content_text | text | diredaksi; untuk assistant = teks final (offset sitasi mengacu ke sini) |
| content_blocks | jsonb not null default '[]' | blok provider-netral untuk replay multi-turn (termasuk hasil server tool dengan `encrypted_content`); dibatasi 512 KB, dipangkas ke teks+sitasi untuk turn lama |
| attachments | jsonb not null default '[]' | `[{type, id}]` |
| grounding | jsonb not null default '{}' | `{status, web_used, uncited_spans, searched_at_min, searched_at_max}` |
| model | varchar(100) null | |
| agent_id | varchar(40) null | |
| usage | jsonb not null default '{}' | ringkasan (sumber kebenaran: `usage_records`/`capability_usage`) |
| error_code | varchar(64) null | |
| error_message | text null | |
| lease_owner | varchar(120) null | klaim worker (recovery sweep) |
| lease_expires_at | timestamptz null | |
| search_tsv | tsvector generated always as (`to_tsvector('simple', coalesce(content_text,''))`) stored | "cari riwayat" |
| created_at | timestamptz | |
| completed_at | timestamptz null | |
Index `(thread_id, created_at)`, GIN `(search_tsv)`, `(status, lease_expires_at)`.

#### assistant_events
| id bigserial pk | thread_id uuid fk assistant_threads on delete cascade | seq bigint | type varchar(60) | payload jsonb (diredaksi) | created_at |
Unique `(thread_id, seq)`. Event ephemeral (`assistant.delta`) tidak disimpan. Retensi 30 hari.

#### message_citations
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| message_id | uuid fk assistant_messages on delete cascade | |
| ordinal | int | unik per pesan |
| kind | varchar(12) check in (`web`,`kb`,`document`) | |
| location | varchar(32) check in (`web_search_result_location`,`search_result_location`,`char_location`,`page_location`,`content_block_location`) | |
| url | varchar(2048) null | |
| title | varchar(500) null | |
| cited_text | text | ≤ 1000 karakter |
| query | text null | query pencarian yang menghasilkan url (web) |
| searched_at | timestamptz null | waktu hasil pencarian diterima |
| chunk_id | uuid null | FK → `kb_chunks(id) on delete set null` ditambahkan di 0006 |
| asset_id | uuid null fk assets on delete set null | dokumen sumber (`document`) |
| page_start, page_end | int null | |
| char_start, char_end | int null | posisi di dokumen sumber |
| answer_start, answer_end | int | posisi di `assistant_messages.content_text` |
| verified | bool null | kb/document: `cited_text` ditemukan di sumber; web: null |
| created_at | timestamptz | |
Unique `(message_id, ordinal)`; CHECK `ck_message_citations_web`: `kind <> 'web' or (url is not null and searched_at is not null)` (AX-04); index `(message_id)`, `(chunk_id)`.

#### assistant_files
| id uuid pk | asset_id uuid unique fk assets on delete cascade | owner_id uuid fk users on delete cascade | space_id uuid null fk assistant_spaces set null | thread_id uuid null fk assistant_threads set null | original_filename varchar(255) | sensitivity sens | page_count int null | extraction_status varchar(16) check in (`pending`,`extracting`,`ready`,`failed`,`unsupported`) default `pending` | extraction_error text null | created_at, updated_at |
Index `(owner_id, created_at)`.

#### generated_assets
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| asset_id | uuid unique fk assets on delete cascade | |
| owner_id | uuid fk users on delete cascade | |
| thread_id | uuid null fk assistant_threads on delete set null | |
| message_id | uuid null fk assistant_messages on delete set null | |
| type | varchar(12) check in (`image`,`pptx`,`docx`,`xlsx`,`pdf`,`chart`,`csv`) | |
| purpose | varchar(16) check in (`assistant`,`cv`,`analysis`,`portfolio`) default `assistant` | |
| engine | varchar(24) check in (`claude_skills`,`local_sandbox`,`image_provider`,`analysis_sandbox`,`code_execution_remote`) | |
| provider | varchar(40) null | |
| model | varchar(100) null | |
| prompt_hash | char(64) null | sha256 prompt (prompt tidak disimpan di sini) |
| template_id | varchar(80) null | |
| validation | jsonb not null default '{}' | `{opened_ok, checker, page_count, warnings}` |
| thumbnail_asset_id | uuid null fk assets on delete set null | |
| moderation_status | varchar(16) check in (`not_required`,`passed`,`flagged`) default `not_required` | `flagged` tidak pernah punya asset (dibuang) — nilai dicadangkan untuk audit |
| created_at | timestamptz | |
Index `(owner_id, type, created_at)`, `(thread_id)`.

### V2.5 Migrasi 0004_github

#### github_installations
| id uuid pk | installation_id bigint unique | account_login varchar(100) | account_id bigint | account_type varchar(16) check in (`User`,`Organization`) | repository_selection varchar(12) check in (`all`,`selected`) | permissions jsonb (snapshot izin yang diberikan) | events jsonb | suspended_at timestamptz null | deleted_at timestamptz null | linked_by uuid null fk users set null | created_at, updated_at |

#### github_installation_grants  (user KodeKantor yang terverifikasi boleh memakai instalasi)
| id uuid pk | installation_pk uuid fk github_installations on delete cascade | user_id uuid fk users on delete cascade | verified_at timestamptz | created_at |
Unique `(installation_pk, user_id)`.

#### github_user_links
| id uuid pk | user_id uuid fk users on delete cascade | github_user_id bigint unique | github_login varchar(100) | verified_at timestamptz | created_at, updated_at |
Unique `(user_id, github_user_id)`. Tidak ada kolom token.

#### github_link_requests
| id uuid pk | user_id uuid fk users on delete cascade | claimed_installation_id bigint | status varchar(12) check in (`pending`,`verified`,`rejected`,`expired`) | reason varchar(200) null | created_at | resolved_at null |
Retensi 7 hari. (`code` OAuth hanya di Redis terenkripsi, TTL 5 menit — tidak pernah di DB.)

#### github_repositories
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| installation_pk | uuid fk github_installations on delete cascade | |
| repo_id | bigint | |
| full_name | varchar(200) | divalidasi `^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$` |
| private | bool | |
| default_branch | varchar(100) | |
| archived | bool default false | |
| size_kb | int null | dicek terhadap `PROJECT_QUOTA_MB` sebelum clone |
| language | varchar(60) null | |
| topics | jsonb default '[]' | |
| html_url | varchar(300) | |
| project_id | uuid null fk projects on delete set null | |
| removed_at | timestamptz null | dikeluarkan dari instalasi |
| synced_at | timestamptz | |
Unique `(installation_pk, repo_id)`; index `(full_name)`.

#### github_repo_snapshots  (metadata untuk impor portfolio; tanpa isi kode)
| id uuid pk | github_repository_id uuid fk github_repositories on delete cascade | requested_by uuid fk users on delete cascade | fields jsonb (field yang dipilih user) | data jsonb (`{description, readme_excerpt (≤ 64 KB, hanya bila dipilih), languages, topics, stars, url}`) | created_at |
Retensi 30 hari.

#### webhook_deliveries
| delivery_id varchar(64) pk (`X-GitHub-Delivery`) | event varchar(60) | action varchar(60) null | installation_id bigint null | payload_sha256 char(64) | status varchar(12) check in (`received`,`processed`,`ignored`,`failed`) | error text null | received_at timestamptz | processed_at timestamptz null |
Index `(received_at)`. Retensi 30 hari. Isi payload tidak disimpan (Redis TTL 1 jam untuk pemrosesan). Delivery dengan tanda tangan salah **tidak** dicatat di tabel ini.

#### github_pull_requests
| id uuid pk | task_id uuid fk tasks on delete cascade | project_id uuid fk projects on delete cascade | github_repository_id uuid fk github_repositories on delete restrict | number int | html_url varchar(300) | title varchar(256) | head_branch varchar(120) | head_commit char(40) | base_branch varchar(100) | state varchar(8) check in (`open`,`closed`,`merged`) | draft bool | checks_status varchar(12) check in (`unknown`,`pending`,`success`,`failure`,`neutral`) default `unknown` | approval_id uuid null fk approvals on delete set null | created_at, updated_at |
Unique `(github_repository_id, number)`; index `(task_id)`.

#### Perubahan existing (0004)
- `repositories.provider` CHECK + `github_app`; + `github_repository_id uuid null fk github_repositories on delete set null`; CHECK `provider <> 'github_app' or github_repository_id is not null`. `remote_url` disimpan tanpa userinfo; `credential_ref` tidak dipakai untuk `github_app`.
- `approvals.action_type` CHECK + `github_push_pr`.

### V2.6 Migrasi 0005_portfolio

#### portfolio_profiles
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| owner_id | uuid unique fk users on delete cascade | satu profil per user |
| slug | varchar(80) unique | `^[a-z0-9][a-z0-9-]{2,79}$` |
| display_name | varchar(120) | |
| headline | varchar(200) null | |
| summary | text null | |
| location | varchar(120) null | |
| contact | jsonb not null default '{}' | `{email:{value, public}, phone:{value, public}, links:[{label, url, public}]}` — default `public:false` |
| photo_asset_id | uuid null fk assets on delete set null | |
| locale | varchar(5) check in (`id`,`en`) default `id` | |
| theme | varchar(40) default `clean-sans` | |
| seo | jsonb not null default '{}' | |
| ai_fields | jsonb not null default '{}' | `{field: {ai_generated, approved_at}}` |
| created_at, updated_at | timestamptz | |

#### portfolio_items
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| profile_id | uuid fk portfolio_profiles on delete cascade | |
| type | varchar(16) check in (`experience`,`education`,`project`,`skill`,`certification`,`publication`,`link`) | |
| visibility | varchar(8) check in (`draft`,`public`) default `draft` | |
| source | varchar(12) check in (`manual`,`github`,`kodekantor`,`ai_draft`) | |
| ai_generated | bool not null default false | |
| ai_approved_at | timestamptz null | |
| ai_approved_by | uuid null fk users on delete set null | |
| title | varchar(200) | |
| organization | varchar(200) null | |
| start_date, end_date | date null | |
| data | jsonb not null default '{}' | spesifik tipe: `achievements[]`, `level`, `evidence_url`, `issuer`, `credential_id`, `repo:{full_name, private, fields_selected[]}`, `project_id` |
| source_ref | varchar(200) null | id repo GitHub / project |
| sort_order | int default 0 | |
| created_at, updated_at | timestamptz | |
CHECK `ck_portfolio_items_ai_public`: `not (visibility = 'public' and ai_generated and ai_approved_at is null)` (AX-09). Index `(profile_id, type, sort_order)`.

#### portfolio_versions
| id uuid pk | profile_id uuid fk portfolio_profiles on delete cascade | version int | snapshot jsonb (hanya field publik + item publik disetujui) | snapshot_sha256 char(64) | created_by uuid fk users | created_at |
Unique `(profile_id, version)`. Retensi: 20 terakhir + yang dirujuk publikasi.

#### portfolio_publications
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| profile_id | uuid fk portfolio_profiles on delete cascade | |
| version_id | uuid fk portfolio_versions on delete restrict | |
| target | varchar(16) check in (`static`,`github_pages`) | |
| url | varchar(500) | |
| bundle_asset_id | uuid fk assets on delete restrict | asset `site_bundle` |
| bundle_sha256 | char(64) | |
| privacy_report | jsonb not null | `PrivacyReport` lengkap |
| privacy_report_sha256 | char(64) | |
| github_repository_id | uuid null fk github_repositories on delete set null | target `github_pages` |
| approval_id | uuid null fk approvals on delete set null | |
| status | varchar(20) check in (`pending_approval`,`publishing`,`published`,`failed`,`rejected`,`superseded`,`unpublished`) | |
| published_at | timestamptz null | |
| error | text null | |
| created_at, updated_at | timestamptz | |
Index `(profile_id, status)`; paling banyak satu `published` per `(profile_id, target)` (unique index parsial `where status = 'published'`).

#### Perubahan existing (0005)
- `approvals.action_type` CHECK + `portfolio_publish` (dengan `scope_type = 'portfolio'`, `project_id` null, `owner_id` = pemilik profil).

### V2.7 Migrasi 0006_kb (ADR-046 — tanpa embedding)

`CREATE EXTENSION IF NOT EXISTS pg_trgm` wajib (di 0001 opsional).

#### kb_collections
| id uuid pk | owner_id uuid fk users on delete cascade | space_id uuid null fk assistant_spaces set null | name varchar(120) | description text null | sensitivity sens default `internal` | status varchar(12) check in (`active`,`archived`) default `active` | document_count int default 0 | chunk_count int default 0 | created_at, updated_at |
Unique `(owner_id, name)`.

#### kb_documents
| id uuid pk | collection_id uuid fk kb_collections on delete cascade | file_id uuid fk assistant_files on delete cascade | title varchar(300) | status varchar(16) check in (`pending`,`extracting`,`contextualizing`,`indexed`,`failed`) | page_count int null | chunk_count int default 0 | extractor_version varchar(20) | context_model varchar(100) null | ingest_cost jsonb default '{}' | error text null | indexed_at timestamptz null | created_at, updated_at |
Unique `(collection_id, file_id)`.

#### kb_chunks
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| document_id | uuid fk kb_documents on delete cascade | |
| collection_id | uuid fk kb_collections on delete cascade | denormalisasi untuk filter |
| ordinal | int | |
| text | text | ≤ 8000 karakter |
| heading_path | varchar(500) null | "Bab 2 › 2.1 Ruang lingkup" |
| page_start, page_end | int null | |
| char_start, char_end | int | posisi di teks hasil ekstraksi |
| context_summary | text null | ≤ 600 karakter, dibuat Claude (contextual retrieval) |
| keywords | text null | kata dasar + sinonim id/en, dipisah spasi |
| token_estimate | int | |
| tsv | tsvector generated always as (`setweight(to_tsvector('simple', coalesce(heading_path,'') \|\| ' ' \|\| coalesce(context_summary,'') \|\| ' ' \|\| coalesce(keywords,'')), 'A') \|\| setweight(to_tsvector('simple', text), 'B')`) stored | |
| created_at | timestamptz | |
Unique `(document_id, ordinal)`; index GIN `(tsv)`, GIN `(keywords gin_trgm_ops)`, GIN `(context_summary gin_trgm_ops)`, btree `(collection_id)`.

#### Perubahan existing (0006)
- `message_citations.chunk_id` → FK `kb_chunks(id) on delete set null`.

### V2.8 Retensi v2 (default, `policy_settings.retention`)

`assistant_events` 30 hari · pesan & sitasi sampai thread dihapus user · aset upload/generated 180 hari kecuali `pinned` · bundle situs 5 terakhir + aktif · `_jobs` ≤ 2 jam · file Claude Files API ≤ `PROVIDER_FILES_TTL_MINUTES` · `webhook_deliveries` 30 hari · `github_link_requests` 7 hari · `github_repo_snapshots` 30 hari · `moderation_events` 365 hari · `capability_usage` 400 hari · audit tetap 365 hari.

> **Ekstensi v2.1 (22 Okt 2026, rancangan / Proposed — belum diimplementasikan).** Sumber mengikat: `docs/SDLC_SCALE_DESIGN.md` §6 K11 (delta data); desain `docs/IMPLEMENTATION_DESIGN_V2.md` §13 (§13.5 urutan landing). Konvensi bagian atas dan Ekstensi v2 tetap berlaku. Kolom/perubahan bertanda *(turunan)* tidak disebut eksplisit di K11 tetapi dibutuhkan FR v2.1 (dicatat sebagai OI-26 di `SYSTEM_SPEC.md` §12.9; menunggu pengesahan Lead di WP-13.0). Tidak ada kolom yang menyimpan token node, token SCIM, secret IdP, token GitHub, atau API key dalam bentuk mentah (hanya hash sha256 atau nama referensi secret); tidak ada kolom embedding/vektor (ADR-046 tetap berlaku). Rantai: `0006_kb → 0007_sdlc → 0008_scale`.

### V2.9 Migrasi 0007_sdlc (P13, WP-13.0, schema-first)

#### work_items
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| project_id | uuid fk projects on delete cascade | program tidak lintas project (v2.1) |
| type | varchar(8) check in (`program`,`epic`,`story`) | |
| parent_id | uuid null fk work_items on delete restrict | tipe induk sah: epic → program, story → epic; epic/story boleh tanpa induk (dicek service + constraint trigger `work_items_parent_type`) |
| key | varchar(20) | `PRG-<n>` / `EP-<n>` / `ST-<n>` per project |
| title | varchar(200) | |
| description | text | diredaksi |
| status | varchar(12) check in (`draft`,`ready`,`in_progress`,`blocked`,`in_review`,`accepted`,`closed`) default `draft` | ditulis HANYA oleh `sdlc/rollup.py::apply_rollup` (turunan deterministik, ADR-057) |
| status_reason | varchar(200) null | |
| priority | varchar(8) check in (`low`,`normal`,`high`) default `normal` | |
| mandatory | bool not null default true | "wajib" untuk roll-up parent (SSD §3.1) |
| owner_id | uuid null fk users on delete set null | |
| repo_aliases | jsonb not null default '[]' | repo yang terlibat (FR-254) |
| scheduling_enabled | bool not null default false | penjadwal DAG untuk subtree |
| phase_overrides | jsonb not null default '{}' | `{phase: {state: "not_applicable", reason, by, at}}` — hanya `design`/`run`/`operate` |
| closed_reason | text null | |
| closed_at | timestamptz null | |
| version | int not null default 1 | optimistic lock |
| created_by | uuid fk users | |
| created_at, updated_at | timestamptz | |
Unique `(project_id, key)`; CHECK `ck_work_items_closed`: `status <> 'closed' or closed_reason is not null`; CHECK `parent_id is null or parent_id <> id`; index `(project_id, type, status)`, `(parent_id)`.

#### work_item_links
| id uuid pk | work_item_id uuid fk work_items on delete cascade | link_type varchar(20) check in (`commit`,`test_run`,`acceptance_report`,`artifact`,`release`,`requirement`,`incident`,`plan_proposal`,`depends_on`,`relates`) | target_type varchar(20) check in (`commit`,`test_run`,`acceptance_report`,`artifact`,`release`,`work_item`,`requirement`,`log`,`alert`,`ci_check_result`,`merge_queue_entry`) (dua nilai terakhir *(turunan, K13)*: bukti story perbaikan CI pasca-merge) | target_id uuid null | target_ref varchar(200) (uuid atau sha commit) | sdlc_phase varchar(16) null check in (`requirement`,`design`,`implementation`,`testing`,`run`,`review`,`release`,`operate`) | metadata jsonb not null default '{}' (`plan_proposal`: `{status, sha256, agent_run_id}`) | created_by varchar(80) (`user:<id>` / `agent:<id>` / `system`) | created_at |
Unique `(work_item_id, link_type, target_type, target_ref)`; index `(target_type, target_id)`. Relasi task → story memakai `tasks.work_item_id`, bukan tabel ini.

#### merge_queue_entries
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| project_id | uuid fk projects on delete cascade | |
| repository_id | uuid fk repositories on delete cascade | antrean per repo (FR-225, FR-254) |
| source_type | varchar(12) check in (`task`,`user_branch`) | |
| task_id | uuid null fk tasks on delete cascade | |
| work_item_id | uuid null fk work_items on delete set null | story (denormalisasi) |
| source_branch | varchar(120) | `kk/task-<8hex>` / `kk/user-<user8>` |
| target_branch | varchar(100) | |
| position | bigint | `nextval('merge_queue_position_seq')`; FIFO per repo |
| status | varchar(20) check in (`queued`,`preparing`,`testing`,`waiting_ci`,`merging`,`merged`,`failed`,`needs_reapproval`,`cancelled`) | |
| approval_id | uuid null fk approvals on delete restrict | approval `apply_changes` yang sudah di-consume |
| approved_head | char(40) | |
| approved_patch_id | char(40) | `git patch-id --stable` diff yang disetujui |
| candidate_patch_id | char(40) null | |
| base_head | char(40) null | head target yang dipakai kandidat (CAS) |
| result_commit | char(40) null | |
| required_profiles | jsonb not null default '[]' | snapshot kebijakan saat diproses |
| test_run_ids | jsonb not null default '[]' | |
| ci_required | bool not null default false | true hanya bila policy `require_ci` **dan** `approved_head` sudah di-push **dan** rebase fast-forward (K13 OI-23) |
| ci_skip_reason | varchar(20) null check in (`not_pushed`,`not_fast_forward`,`ci_not_required`) | *(turunan, K13)* alasan langkah `waiting_ci` dilewati; entri memakai profil lokal wajib saja |
| post_merge_ci | jsonb null | *(turunan, K13)* `{status, conclusion, check_result_ids, repair_work_item_id}` — CI pasca-merge atas `result_commit` di default branch; gagal → story perbaikan (jalur FR-234) |
| failure_code | varchar(32) null check in (`REBASE_CONFLICT`,`DIFF_CHANGED`,`REQUIRED_PROFILE_FAILED`,`CI_FAILED`,`CI_TIMEOUT`,`CODEOWNERS_PENDING`,`TARGET_MOVED`,`INFRA_ERROR`,`CANCELLED_BY_USER`) | |
| failure_detail | jsonb not null default '{}' | diredaksi (mis. daftar file konflik) |
| evidence_artifact_id | uuid null fk artifacts on delete set null | |
| attempts | int not null default 0 | |
| lease_owner | varchar(120) null | pemroses (satu per repo, advisory lock) |
| enqueued_by | uuid fk users | |
| enqueued_at | timestamptz | |
| started_at, finished_at | timestamptz null | |
| updated_at | timestamptz | |
CHECK `source_type <> 'task' or (task_id is not null and approval_id is not null)`; CHECK `status <> 'merged' or result_commit is not null`; unique index parsial `(repository_id, source_branch) where status in ('queued','preparing','testing','waiting_ci','merging')`; index `(repository_id, status, position)`, `(task_id)`. Kebijakan merge queue per repo disimpan sebagai data di `projects.settings.merge_queue` (lihat perubahan existing).

#### releases
| id uuid pk | project_id uuid fk projects on delete cascade | version varchar(40) (`^[0-9A-Za-z.+-]{1,40}$`) | name varchar(120) null | status varchar(12) check in (`draft`,`frozen`,`released`,`cancelled`) | changelog jsonb not null default '[]' (dibangkitkan deterministik saat freeze) | notes_artifact_id uuid null fk artifacts on delete set null | notes_task_id uuid null fk tasks on delete set null | target_environment_id uuid null fk environments on delete set null | frozen_at, released_at timestamptz null | created_by uuid fk users | created_at, updated_at |
Unique `(project_id, version)`. FK melingkar `releases.target_environment_id` ↔ `environments.current_release_id` dibuat setelah kedua tabel ada (`ALTER TABLE … ADD CONSTRAINT`, `DEFERRABLE INITIALLY DEFERRED`).

#### release_items
| id uuid pk | release_id uuid fk releases on delete cascade | item_type varchar(16) check in (`story`,`repo_version`) | work_item_id uuid null fk work_items on delete restrict | repository_id uuid null fk repositories on delete restrict | commit char(40) null | created_at |
CHECK `(item_type = 'story' and work_item_id is not null) or (item_type = 'repo_version' and repository_id is not null and commit is not null)`; unique index parsial `(release_id, work_item_id) where item_type = 'story'` dan `(release_id, repository_id) where item_type = 'repo_version'` (versi lintas repo, FR-254). Story wajib `accepted` saat ditambahkan (dicek service).

#### environments
| id uuid pk | project_id uuid fk projects on delete cascade | name varchar(60) | kind varchar(12) check in (`dev`,`staging`,`production`) | sort_order int | url varchar(300) null | requires_approval bool not null default false | config jsonb not null default '{}' (non-secret) | current_release_id uuid null fk releases on delete set null | created_at, updated_at |
Unique `(project_id, name)`; CHECK `kind <> 'production' or requires_approval` (production tetap R3, FR-141/FR-229).

#### ci_check_results
| id uuid pk | project_id uuid fk projects on delete cascade | repository_id uuid fk repositories on delete cascade | github_repository_id uuid null fk github_repositories on delete set null | external_id bigint null (id check run GitHub) | commit_sha char(40) | check_name varchar(200) | app_slug varchar(100) null | status varchar(12) check in (`queued`,`in_progress`,`completed`) | conclusion varchar(16) null check in (`success`,`failure`,`neutral`,`cancelled`,`skipped`,`timed_out`,`action_required`,`stale`) | details_url varchar(500) null | started_at, completed_at timestamptz null | source varchar(8) check in (`webhook`,`poll`) | fetched_at timestamptz | created_at |
Unique index parsial `(repository_id, external_id) where external_id is not null`; index `(repository_id, commit_sha)`. Retensi 180 hari [Menebak]. Tidak menyimpan token GitHub (dibaca `worker-integrations` lewat `ci.get_checks`). Juga menyimpan check untuk `result_commit` di default branch (CI pasca-merge, K13 OI-23); diisi mulai WP-13.12 (setelah G9) — sebelum itu tabel kosong dan merge queue memakai profil lokal saja.

#### terminal_sessions
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| project_id | uuid fk projects on delete cascade | |
| user_id | uuid fk users on delete cascade | hanya manusia (agent tidak punya terminal) |
| target | varchar(48) | `repo` / `user` / `task:<uuid>` |
| task_id | uuid null fk tasks on delete set null | hanya saat take-over aktif |
| sandbox_terminal_id | varchar(80) null | id dari sandbox-manager |
| status | varchar(12) check in (`pending`,`active`,`closed`,`expired`,`killed`,`failed`) | |
| close_reason | varchar(16) null check in (`user`,`idle_timeout`,`max_duration`,`admin_kill`,`policy`,`node_lost`,`error`) | |
| max_duration_s | int | dari `TERMINAL_SESSION_MAX_MINUTES` saat dibuat |
| idle_timeout_s | int | dari `TERMINAL_IDLE_MINUTES` |
| input_bytes, output_bytes | bigint not null default 0 | |
| transcript_artifact_id | uuid null fk artifacts on delete set null | transcript teredaksi (artefak `log`, retensi 30 hari) |
| client_ip | varchar(64) null | |
| user_agent | varchar(255) null | |
| started_at, last_activity_at, ended_at | timestamptz null | |
| created_at | timestamptz | |
Index `(project_id, status)`, `(user_id, created_at)`; index parsial `(user_id, project_id) where status in ('pending','active')` (batas sesi aktif). Tiket WebSocket **tidak** disimpan di DB (Redis, sha256, TTL 30 dtk). Retensi baris 365 hari [Menebak].

#### Perubahan existing (0007)
- `tasks`: + `work_item_id uuid null fk work_items on delete set null`; index `(work_item_id)`. Task tanpa story tetap valid (tier S).
- `test_runs`: + `initiated_by varchar(8) not null default 'agent' check in ('agent','user','system')`; + `initiated_by_user_id uuid null fk users on delete set null`; CHECK `initiated_by <> 'user' or initiated_by_user_id is not null`; + `selector jsonb null` (`{scope, path, test_id}`); + `target varchar(48) null`; + `head_commit char(40) null` (dasar kesegaran bukti setelah take-over); + `merge_queue_entry_id uuid null fk merge_queue_entries on delete set null`; + `preview_url varchar(300) null`; CHECK `suite` + `'run'` *(turunan, OI-24: run `run.*` user)*. Kolom lama `triggered_by` (teks bebas) dipertahankan; `initiated_by` adalah enum bertipe untuk aturan bukti.
- `agent_runs`: + `work_item_id uuid null fk work_items on delete cascade`; CHECK `ck_agent_runs_scope` diganti menjadi `num_nonnulls(task_id, thread_id, work_item_id) = 1` *(turunan — run A03 `ProgramPlan` tanpa task/thread)*.
- `requirements`: + `work_item_id uuid null fk work_items on delete cascade` (baseline story); CHECK `task_id is null or work_item_id is null` *(turunan, FR-222)*.
- `acceptance_criteria`: `task_id` → **nullable**; + `work_item_id uuid null fk work_items on delete cascade`; CHECK `num_nonnulls(task_id, work_item_id) = 1` *(turunan, FR-222)*.
- `deployments`: + `environment_id uuid null fk environments on delete set null`; + `release_id uuid null fk releases on delete set null` *(turunan, FR-228/229)*. CHECK `environment` tetap (`staging`,`production`); environment `dev` hanya entri registry.
- `approvals.action_type`: **tidak berubah** — merge queue memakai `apply_changes`, promosi production memakai `deploy_production`.
- `projects.settings` (data, tanpa DDL): `merge_queue: {<repository_id>: {enabled, required_profiles, require_ci, ci_scope, ci_timeout_minutes, merge_method, codeowners_enforced, paused}}`, `terminal: {enabled}` (hanya owner/admin), `user_branch_mode: "user_branch"|"default_branch"`, `format_on_save: {enabled, lint}`.
- `tasks.checkpoint` (data, tanpa DDL): `takeover` (`{active, pending, by_user_id, epoch, started_at, base_head, released_at, user_commits}`), `workspace` (`CheckpointRef` untuk tier M).

Retensi 0007: `merge_queue_entries` 365 hari [Menebak]; `ci_check_results` 180 hari [Menebak]; `terminal_sessions` 365 hari (transcript 30 hari); work item, link, release, environment mengikuti umur project.

### V2.10 Migrasi 0008_scale (schema-first di WP-13.0 bersama 0007 — amandemen K13 OI-27/OI-28; tabel tier L inert sampai WP-14.1/14.2)

#### organizations
| id uuid pk | slug varchar(64) unique (`^[a-z0-9][a-z0-9-]{2,63}$`) | name varchar(200) | status varchar(12) check in (`active`,`suspended`) | is_default bool not null default false | settings jsonb not null default '{}' (`local_login_enabled`, `four_eyes`, `retention`, `allowed_email_domains`) | created_at, updated_at |
Unique index parsial `(is_default) where is_default` (tepat satu organisasi default). Migrasi membuat organisasi `default`; tier S/M hanya memakai organisasi ini.

#### org_memberships
| id uuid pk | organization_id uuid fk organizations on delete cascade | user_id uuid fk users on delete cascade | org_role varchar(12) check in (`org_owner`,`org_admin`,`member`) | source varchar(8) check in (`local`,`scim`,`sso_jit`) | status varchar(10) check in (`active`,`disabled`) | sso_connection_id uuid null fk sso_connections on delete set null | external_subject varchar(255) null (OIDC `sub` / SAML NameID) | scim_external_id varchar(255) null | groups jsonb not null default '[]' | deactivated_at timestamptz null | created_at, updated_at |
Unique `(organization_id, user_id)`; unique index parsial `(sso_connection_id, external_subject) where external_subject is not null` (identitas SSO dicocokkan dengan subjek, bukan email — T22); unique index parsial `(organization_id, scim_external_id) where scim_external_id is not null`.

#### sso_connections
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| organization_id | uuid fk organizations on delete cascade | |
| protocol | varchar(4) check in (`oidc`,`saml`) | OIDC = tier M, SAML = tier L |
| slug | varchar(64) unique | |
| display_name | varchar(120) | |
| issuer | varchar(500) null | OIDC (default `OIDC_ISSUER`) |
| client_id | varchar(255) null | OIDC (default `OIDC_CLIENT_ID`) |
| client_secret_ref | varchar(100) null | **nama** secret (mis. `OIDC_CLIENT_SECRET`), bukan nilai |
| metadata_url | varchar(500) null | SAML (default `SAML_IDP_METADATA_URL`) |
| idp_metadata | text null | XML metadata ter-cache (diambil worker-integrations), ≤ 512 KB |
| idp_cert_sha256 | jsonb not null default '[]' | sidik jari sertifikat penandatangan yang diterima |
| jwks_cache | jsonb not null default '{}' | kunci publik OIDC ter-cache |
| group_claim | varchar(100) not null default `groups` | |
| group_role_map | jsonb not null default '{}' | `{group: role}` eksplisit; validasi service: tidak boleh memetakan ke owner platform |
| jit_provisioning | bool not null default false | T22 |
| enforce_sso | bool not null default false | matikan login lokal (owner *break-glass* tetap) |
| status | varchar(10) check in (`active`,`disabled`,`error`) | |
| metadata_fetched_at | timestamptz null | |
| created_by | uuid fk users | |
| created_at, updated_at | timestamptz | |
CHECK `protocol <> 'oidc' or (issuer is not null and client_id is not null)`; CHECK `protocol <> 'saml' or metadata_url is not null`.

#### scim_tokens
| id uuid pk | organization_id uuid fk organizations on delete cascade | name varchar(120) | token_hash char(64) unique (sha256) | token_prefix varchar(12) (tampilan) | created_by uuid fk users | expires_at timestamptz null | last_used_at timestamptz null | revoked_at timestamptz null | created_at |
Token mentah hanya ditampilkan sekali saat dibuat; tidak pernah di DB/log. Token lama dicabut → ditolak seketika.

#### executor_nodes
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| name | varchar(80) unique | |
| organization_id | uuid null fk organizations on delete set null | null = pool bersama |
| status | varchar(10) check in (`pending`,`active`,`draining`,`offline`,`revoked`) | |
| labels | jsonb not null default '{}' | stack, runtime, zona |
| capacity_slots | int check (`capacity_slots > 0`) | |
| sandbox_runtime | varchar(8) null check in (`runc`,`runsc`,`microvm`) | tier M wajib `runsc` (validator tier) |
| version | varchar(40) null | |
| image_digests | jsonb not null default '{}' | |
| bootstrap_token_hash | char(64) null unique | sekali pakai; di-null-kan setelah registrasi |
| bootstrap_expires_at | timestamptz null | |
| token_hash | char(64) null unique | token node saat ini (sha256) |
| token_expires_at | timestamptz null | `EXECUTOR_NODE_TOKEN_TTL_HOURS` |
| previous_token_hash | char(64) null | masa tenggang rotasi |
| previous_token_valid_until | timestamptz null | |
| client_cert_sha256 | char(64) null | mTLS [Kemungkinan Besar] |
| last_heartbeat_at | timestamptz null | |
| registered_at, drained_at, revoked_at | timestamptz null | |
| created_by | uuid fk users | |
| created_at, updated_at | timestamptz | |
Index `(status, last_heartbeat_at)`.

#### node_leases
| id uuid pk | node_id uuid fk executor_nodes on delete restrict | task_id uuid fk tasks on delete cascade | epoch bigint | status varchar(10) check in (`active`,`released`,`expired`,`revoked`) | acquired_at | renewed_at | expires_at | released_at timestamptz null | release_reason varchar(40) null | created_at |
Unique index parsial `(task_id) where status = 'active'`; unique `(task_id, epoch)`; index `(node_id, status)`, `(status, expires_at)`. Fencing: penulisan dari node mensyaratkan lease `active` dengan `epoch` yang sama dalam transaksi yang sama. Retensi 90 hari [Menebak].

#### project_repositories
| id uuid pk | project_id uuid fk projects on delete cascade | repository_id uuid fk repositories on delete cascade | alias varchar(40) (`^[a-z0-9][a-z0-9-]{0,39}$`; repo primer = `repo`) | is_primary bool not null default false | checkout_path varchar(200) (`repo` atau `repos/<alias>`) | sparse_patterns jsonb not null default '[]' (FR-253) | default_branch varchar(100) | created_at, updated_at |
Unique `(project_id, alias)`, unique `(project_id, repository_id)`; unique index parsial `(project_id) where is_primary`.

#### cost_budgets
| id uuid pk | organization_id uuid null fk organizations on delete cascade | scope_type varchar(12) check in (`organization`,`program`,`epic`,`project`) | scope_id uuid | period varchar(8) check in (`monthly`,`total`) | limit_usd numeric(12,2) check (`limit_usd > 0`) | alert_thresholds jsonb not null default '[0.5, 0.8, 1.0]' | action_on_exceed varchar(16) check in (`alert`,`block_new_tasks`,`kill`) | kill_switch bool not null default false | status varchar(10) check in (`ok`,`alerted`,`blocked`) | last_alert_threshold numeric(4,2) null | period_start date null | created_by uuid fk users | created_at, updated_at |
Unique `(scope_type, scope_id, period)`. Pemakaian **tidak** disimpan di sini: dihitung dari `usage_records` + `capability_usage` lewat task → work item → parent (ledger tunggal, aturan V2.2); `cost_status` jujur (`unavailable` tidak dihitung sebagai 0).

#### Perubahan existing (0008)
- `repositories`: UNIQUE `(project_id)` **dilepas** (multi-repo); migrasi data membuat satu `project_repositories(is_primary = true, alias = 'repo', checkout_path = 'repo')` per repository lama *(turunan, FR-254)*.
- `tasks`: + `project_repository_id uuid null fk project_repositories on delete restrict` (null = repo primer) *(turunan, FR-254)*; + `organization_id uuid null fk organizations` (denormalisasi untuk antrean/kuota; backfill dari project).
- `organization_id uuid fk organizations` — backfill organisasi default lalu `NOT NULL` pada: `projects`, `work_items`, `releases`, `terminal_sessions`, `assistant_spaces`, `assistant_threads`, `kb_collections`, `custom_assistants`, `assets`, `portfolio_profiles`, `github_installations`; backfill tanpa `NOT NULL` pada `approvals`, `tasks`; **nullable tanpa backfill** pada `audit_events` (append-only, trigger menolak UPDATE), `usage_records`, `capability_usage` (tabel besar) — `NULL` = organisasi default. Index diawali `organization_id` pada tabel yang di-query per organisasi. Backfill per batch di migrasi data terpisah.
- `users`: `password_hash` → **nullable** (user SSO/SCIM-only); + `auth_source varchar(8) not null default 'local' check in ('local','oidc','saml','scim')`; CHECK `auth_source <> 'local' or password_hash is not null` *(turunan, FR-250/251)*.
- `sessions`: + `organization_id uuid null fk organizations on delete cascade`; + `auth_method varchar(8) not null default 'password' check in ('password','oidc','saml')`; index `(user_id, revoked_at)` untuk pencabutan massal saat deprovision *(turunan)*.
- `user_roles.scope`: nilai baru `org:<uuid>` (muat di `varchar(64)`) untuk role tingkat organisasi.
- `terminal_sessions`: + `node_id uuid null fk executor_nodes on delete set null`.
- `audit_events`: tidak diubah strukturnya selain `organization_id`; rantai hash SIEM (FR-257, P14) dihitung saat ekspor dan statusnya disimpan di `policy_settings.siem_export_state` (data).

Retensi 0008: `node_leases` 90 hari [Menebak]; `scim_tokens` yang dicabut 365 hari; `org_memberships` yang dinonaktifkan dipertahankan untuk audit; `executor_nodes` berstatus `revoked` dipertahankan 365 hari.

> **Ekstensi v2.2 (22 Okt 2026, rancangan / Proposed — belum diimplementasikan).** Sumber mengikat: `docs/POLYGLOT_INTEGRATION_DESIGN.md` §7 **K19** (delta data: migrasi `0009_polyglot_usage`, `0010_integration`); desain `docs/IMPLEMENTATION_DESIGN_V2.md` §14 (§14.5 urutan landing). Konvensi bagian atas, Ekstensi v2, dan Ekstensi v2.1 tetap berlaku. Kolom bertanda *(turunan)* tidak disebut eksplisit di K19 tetapi dibutuhkan FR v2.2 (dicatat di `SYSTEM_SPEC.md` §13.9). Tidak ada kolom yang menyimpan kunci Admin API (`ANTHROPIC_ADMIN_API_KEY`), nilai secret palsu lingkungan integrasi, atau kredensial registry dependensi; tidak ada kolom embedding/vektor (ADR-046). **Aturan ledger V2.2 tetap:** token LLM → `usage_records`, unit non-token → `capability_usage`; `usage_events` dan `usage_rollups_minute` adalah **proyeksi** satu-ke-satu/agregat dari ledger, bukan ledger kedua (total halaman monitoring = total ledger, AX-19).
> **Urutan landing *(turunan, OI-38)*:** tabel usage dibutuhkan di P6 (WP-6.9), sebelum 0003..0008. Rantai Alembic ditentukan `down_revision`: `… → 0002_ide_backlog → 0009_polyglot_usage → 0003 … 0008 → 0010_integration`. Kolom 0009 yang merujuk tabel yang belum ada dibuat **tanpa FK**; FK ditambahkan oleh migrasi pemilik tabel (ditandai "FK oleh 0003/0008" di bawah). Tabel `language_packs` dan `project_stacks` inert sampai WP-15.0. Keputusan akhir Lead di WP-6.9.

### V2.11 Migrasi 0009_polyglot_usage (P6 schema-first untuk usage, WP-6.9; tabel pack dipakai P15)

#### usage_events  (satu baris per panggilan model/vendor — FR-283)
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| call_id | uuid unique | kunci idempotensi panggilan (uuid7); retry worker tidak membuat baris kedua |
| usage_record_id | uuid null unique fk usage_records on delete cascade | panggilan LLM (K19) |
| capability_usage_id | uuid null unique | panggilan vendor/unit non-token *(turunan, OI-43)*; FK `capability_usage on delete cascade` oleh 0003 |
| ts | timestamptz | waktu selesai panggilan |
| organization_id | uuid null | FK `organizations` oleh 0008; `NULL` = organisasi default |
| project_id | uuid null fk projects on delete set null | |
| task_id | uuid null fk tasks on delete set null | |
| thread_id | uuid null | FK `assistant_threads on delete set null` oleh 0003 |
| agent_run_id | uuid null fk agent_runs on delete set null | |
| agent_id | varchar(40) null | denormalisasi untuk breakdown (FR-286) |
| user_id | uuid null fk users on delete set null | pemicu/pemilik |
| capability | varchar(40) null | diisi mulai WP-10.1/WP-10.7 |
| provider | varchar(40) | `anthropic` \| `<vendor>` \| `local` |
| model | varchar(100) null | |
| request_id | varchar(120) null | request-id provider (untuk penelusuran rekonsiliasi) |
| input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens | int not null default 0 | dari `usage` provider (nilai kumulatif akhir); CHECK ≥ 0 |
| server_tool_use | jsonb not null default '{}' | `{web_search_requests, web_fetch_requests}` |
| units | jsonb not null default '{}' | `{unit: qty}` untuk vendor/capability (mis. `image`) |
| estimated_cost | numeric(12,6) null | `NULL` bila tarif tidak dikenal (bukan 0) |
| currency | char(3) default `USD` | |
| cost_status | varchar(16) check in (`estimated`,`partial`,`unavailable`) | `actual` tetap dicadangkan (ADR-025); biaya resmi hanya di `usage_reconciliation` |
| pricing_version | varchar(40) null | versi tabel tarif (`PRICING_PATH`) |
| status | varchar(10) check in (`ok`,`error`,`cancelled`) | gagal/batal setelah `message_start` → token kumulatif terakhir + `cost_status = 'partial'` |
| duration_ms | int null | |
| created_at | timestamptz | |
CHECK `ck_usage_events_ledger`: `num_nonnulls(usage_record_id, capability_usage_id) = 1`; index `(ts)`, `(organization_id, ts)`, `(project_id, ts)`, `(task_id)`, `(model, ts)`, `(capability, ts)`. Retensi 400 hari [Menebak] (sama dengan `capability_usage`).

#### usage_rollups_minute  (agregat per menit — FR-284..FR-286)
| kolom | tipe | catatan |
|---|---|---|
| bucket_start | timestamptz | `date_trunc('minute', ts)` UTC |
| dims_hash | char(64) | sha256 tuple dimensi kanonik (NULL-aman) |
| organization_id | uuid null | FK oleh 0008 |
| project_id | uuid null | tanpa FK (agregat dipertahankan setelah project dihapus sampai retensi) |
| user_id | uuid null | |
| agent_id | varchar(40) null | |
| capability | varchar(40) null | |
| provider | varchar(40) | |
| model | varchar(100) null | |
| calls | int not null default 0 | |
| input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens | bigint not null default 0 | |
| server_tool_use | jsonb not null default '{}' | jumlah per kunci |
| estimated_cost | numeric(14,6) not null default 0 | hanya dari baris ber-biaya; baris `unavailable` dihitung di kolom berikut |
| cost_unavailable_calls | int not null default 0 | panggilan tanpa estimasi (UI: "sebagian tidak tersedia") |
| updated_at | timestamptz | |
PK `(bucket_start, dims_hash)`; CHECK `bucket_start = date_trunc('minute', bucket_start)`; index `(organization_id, bucket_start)`, `(project_id, bucket_start)`. Diisi upsert inkremental di transaksi yang sama dengan ledger; dapat dibangun ulang dari `usage_events` (selisih wajib 0). Task tidak menjadi dimensi rollup (breakdown per task dihitung dari `usage_events`). Retensi 90 hari [Menebak].

#### budget_alerts  (kejadian alert 50/80/100% — FR-287)
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| scope_type | varchar(12) check in (`task`,`project`,`program`,`epic`,`organization`,`capability`) | `epic` *(turunan, FR-255)* |
| scope_key | varchar(64) | uuid atau nama capability |
| source | varchar(20) check in (`task_budget`,`project_budget`,`cost_budget`,`capability_setting`) | sumber limit asli — tidak ada penyimpanan limit kedua (OI-44) |
| cost_budget_id | uuid null | FK `cost_budgets on delete set null` oleh 0008 |
| period_key | varchar(20) | `task` \| `total` \| `YYYY-MM` |
| threshold | numeric(3,2) check in (0.50, 0.80, 1.00) | |
| used_usd | numeric(14,6) | Σ estimasi ledger saat terpicu |
| limit_usd | numeric(12,2) | |
| cost_status | varchar(16) check in (`estimated`,`partial`,`unavailable`) | `partial` bila ada panggilan tanpa estimasi |
| kill_switch_ref | varchar(200) | rute UI/endpoint kill switch (FR-132/217/255) |
| triggered_at | timestamptz | |
| acknowledged_by | uuid null fk users on delete set null | |
| acknowledged_at | timestamptz null | |
Unique `(scope_type, scope_key, period_key, threshold)` (alert tidak pernah ganda); index `(triggered_at)`, `(scope_type, scope_key)`. Setiap baris juga ditulis ke audit `budget.threshold_reached`. Retensi 400 hari [Menebak].

#### usage_reconciliation  (internal vs Usage & Cost Admin API — FR-288, FR-289)
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| bucket_width | varchar(2) check in (`1m`,`1d`) | usage resmi per menit; biaya resmi hanya harian [Pasti — PID §0 #1] |
| bucket_start | timestamptz | |
| model | varchar(100) | `*` untuk baris biaya harian tanpa rincian model *(turunan)* |
| scope_key | varchar(100) not null default `organization` | `api_key:<id>` / `workspace:<id>` bila filter tersedia (OI-51) |
| internal_input_tokens, internal_output_tokens, internal_cache_creation_tokens, internal_cache_read_tokens | bigint not null default 0 | Σ `usage_events` provider `anthropic` |
| official_input_tokens, official_output_tokens, official_cache_creation_tokens, official_cache_read_tokens | bigint null | dari usage report |
| estimated_cost | numeric(14,6) null | Σ estimasi internal |
| official_cost | numeric(14,6) null | dari cost report (`1d` saja) |
| token_variance_pct | numeric(8,3) null | |
| cost_variance_pct | numeric(8,3) null | |
| status | varchar(12) check in (`settling`,`matched`,`variance`,`unavailable`,`error`) | `settling` = bucket < ≈ 5 menit atau masih berubah |
| status_reason | varchar(60) null | mis. `code_execution_cost_only_daily`, `priority_tier_not_in_cost` |
| fetched_at | timestamptz | |
| source_request_id | varchar(120) null | request-id Admin API |
| updated_at | timestamptz | |
Unique `(bucket_width, bucket_start, model, scope_key)`; CHECK `bucket_width = '1d' or official_cost is null`; index `(status, bucket_start)`. Ditulis **hanya** oleh `worker-integrations`. Retensi 400 hari [Menebak].

#### language_packs  (registry pack berversi — FR-259, FR-266, FR-267)
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| pack_id | varchar(24) check `pack_id ~ '^[a-z][a-z0-9-]{1,23}$'` | `python`, `node`, `jvm`, `dotnet`, `go`, `php`, `ruby`, `cpp`, `react-native`, `flutter`, `sql` |
| version | varchar(20) | semver pack |
| level | varchar(2) check in (`L1`,`L2`) | |
| category | varchar(10) check in (`app`,`database`) default `app` | *(turunan)* pack `sql` = `database` |
| image_repository | varchar(200) | |
| image_digest | char(71) check `image_digest ~ '^sha256:[0-9a-f]{64}$'` | pin digest wajib |
| status | varchar(10) check in (`draft`,`validated`,`active`,`stale`,`disabled`) | |
| agent_enabled | bool not null default false | FR-266 (G8 per pack) |
| eval_report_ref | varchar(300) null | laporan eval WP-15.10 |
| manifest | jsonb | manifest tervalidasi (tanpa secret) |
| manifest_sha256 | char(64) | |
| sbom_ref | varchar(300) | |
| scan_critical | int not null default 0 | |
| built_at | timestamptz | rebuild ≤ 30 hari (NFR-30) |
| activated_at | timestamptz null | |
| created_by | uuid null fk users on delete set null | |
| created_at, updated_at | timestamptz | |
Unique `(pack_id, version)`; unique index parsial `(pack_id) where status = 'active'` (satu versi aktif per pack); CHECK `status <> 'active' or scan_critical = 0`; CHECK `not agent_enabled or status = 'active'`. Inert sampai WP-15.0; pack `python`/`node` di-seed dari manifest snapshot saat WP-15.0.

#### project_stacks  (hasil deteksi stack per path — FR-260)
| id uuid pk | project_id uuid fk projects on delete cascade | project_repository_id uuid null (FK `project_repositories on delete cascade` oleh 0008; `NULL` = repo primer) | path varchar(500) not null default `.` (relatif, tanpa `..`) | pack_id varchar(24) | framework varchar(40) null | package_manager varchar(24) null | entrypoints jsonb not null default '[]' | lockfile varchar(500) null | confidence varchar(6) check in (`high`,`medium`,`low`) | unsupported_targets jsonb not null default '[]' (FR-270) | detected_by varchar(8) check in (`detector`,`user`) | detected_commit char(40) null | created_at, updated_at |
Unique index `(project_id, coalesce(project_repository_id, '00000000-0000-0000-0000-000000000000'), path, pack_id)`; index `(project_id)`. `pack_id` tidak ber-FK (versi berganti); service memvalidasi terhadap `language_packs`. `projects.stack` (v1) tetap = stack `primary` untuk kompatibilitas.

#### Perubahan existing (0009)
- `test_runs`: + `language_pack varchar(24) null`; + `report_status varchar(10) null check in ('parsed','unparsed','missing')`; + `report_source_format varchar(24) null` (FR-264). Status test tetap ditentukan exit code + laporan nyata.
- `usage_records`: tidak berubah strukturnya; setiap baris baru punya tepat satu `usage_events` (dijamin service + test). Baris lama (sebelum 0009) tidak di-backfill ke `usage_events` — monitoring dimulai dari landing 0009 *(turunan)*.
- `policy_settings` (data): `usage.variance_alert_pct` (default dari `USAGE_VARIANCE_ALERT_PCT`), `usage.reconcile_scope` (`api_key_id`/`workspace_id` bila tersedia).

Retensi 0009: `usage_events` 400 hari, `usage_rollups_minute` 90 hari, `budget_alerts` 400 hari, `usage_reconciliation` 400 hari [Menebak]; `language_packs` dipertahankan (riwayat versi); `project_stacks` mengikuti umur project.

### V2.12 Migrasi 0010_integration (P16, WP-16.0)

#### service_catalog  (FR-271)
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| project_id | uuid fk projects on delete cascade | |
| program_id | uuid null fk work_items on delete cascade | work item bertipe `program` (dicek service); `NULL` = katalog tingkat project |
| organization_id | uuid null fk organizations on delete cascade | |
| slug | varchar(40) check `slug ~ '^[a-z][a-z0-9-]{1,39}$'` | juga nama DNS default di env |
| name | varchar(120) | |
| kind | varchar(8) check in (`internal`,`external`) | `external` = sistem di luar sandbox (mis. ABAP/SAP, layanan pihak ketiga) — hanya kontrak + mock |
| pack_id | varchar(24) null | wajib untuk `internal` |
| project_repository_id | uuid null fk project_repositories on delete restrict | wajib untuk `internal` |
| root_path | varchar(500) not null default `.` | path service di repo (monorepo) |
| owner_id | uuid null fk users on delete set null | |
| description | text | |
| status | varchar(10) check in (`active`,`deprecated`) | |
| created_by | uuid fk users | |
| created_at, updated_at | timestamptz | |
Unique index `(project_id, coalesce(program_id, '00000000-0000-0000-0000-000000000000'), slug)`; CHECK `kind <> 'internal' or (pack_id is not null and project_repository_id is not null)`.

#### service_interfaces  (FR-272)
| id uuid pk | service_id uuid fk service_catalog on delete cascade | direction varchar(8) check in (`provides`,`consumes`) | contract_type varchar(10) check in (`openapi`,`asyncapi`,`protobuf`,`graphql`) | path varchar(500) null (file kontrak di repo service) | provider_interface_id uuid null fk service_interfaces on delete restrict | version varchar(40) null | contract_sha256 char(64) null | last_validated_commit char(40) null | validation_status varchar(8) check in (`unknown`,`valid`,`invalid`) | created_at, updated_at |
Unique `(service_id, direction, contract_type, path)`; CHECK `direction <> 'provides' or path is not null`; CHECK `direction <> 'consumes' or provider_interface_id is not null or path is not null`; index `(provider_interface_id)` (mencari consumer terdampak).

#### service_dependencies
| id uuid pk | service_id uuid fk service_catalog on delete cascade | kind varchar(16) check in (`service`,`database`,`broker`,`cache`,`object_storage`) | depends_on_service_id uuid null fk service_catalog on delete restrict | support_key varchar(40) null (kunci katalog `config/support-images.yaml`) | required bool not null default true | notes text null | created_at |
CHECK `(kind = 'service') = (depends_on_service_id is not null)`; CHECK `kind = 'service' or support_key is not null`; CHECK `depends_on_service_id is null or depends_on_service_id <> service_id`.

#### integration_envs  (FR-275, FR-279, FR-280)
| kolom | tipe | catatan |
|---|---|---|
| id | uuid pk | |
| project_id | uuid fk projects on delete cascade | |
| organization_id | uuid null fk organizations on delete cascade | |
| work_item_id | uuid null fk work_items on delete set null | story/program terkait |
| task_id | uuid null fk tasks on delete set null | |
| merge_queue_entry_id | uuid null fk merge_queue_entries on delete set null | env yang dipicu merge queue (FR-282) |
| spec | jsonb | spec ternormalisasi; **tanpa** nilai secret palsu (hanya nama) |
| spec_sha256 | char(64) | K19 "spec hash" |
| status | varchar(14) check in (`requested`,`validating`,`rejected`,`provisioning`,`running`,`testing`,`passed`,`failed`,`tearing_down`,`torn_down`,`expired`,`error`) | |
| status_reason | varchar(200) null | |
| node_id | uuid null fk executor_nodes on delete set null | K19 "node" |
| network_name | varchar(64) null | `kk-env-<env8>` |
| quotas | jsonb | `{max_services, cpu, memory_bytes}` saat dibuat (K19 "kuota") |
| ttl_minutes | int check (`ttl_minutes > 0`) | K19 "ttl" |
| expires_at | timestamptz | |
| violations | jsonb not null default '[]' | hasil validasi (status `rejected`) |
| test_run_ids | jsonb not null default '[]' | |
| teardown_reason | varchar(10) null check in (`completed`,`ttl`,`user`,`failed`,`admin`) | |
| created_by | uuid fk users | |
| requested_at, ready_at, finished_at, torn_down_at | timestamptz null | |
| created_at, updated_at | timestamptz | |
CHECK `status not in ('torn_down','expired') or torn_down_at is not null`; CHECK `expires_at > requested_at`; index parsial `(expires_at) where status in ('requested','validating','provisioning','running','testing','tearing_down')` (sweep TTL); index `(project_id, created_at)`, `(merge_queue_entry_id)`. Retensi baris 180 hari [Menebak]; log per service = artefak `log` (retensi 30 hari).

#### integration_env_services
| id uuid pk | env_id uuid fk integration_envs on delete cascade | name varchar(32) | kind varchar(8) check in (`service`,`support`) | service_id uuid null fk service_catalog on delete set null | pack_id varchar(24) null | support_key varchar(40) null | image_ref varchar(200) | image_digest char(71) | project_repository_id uuid null fk project_repositories on delete set null | commit char(40) null | container_id varchar(80) null | status varchar(10) check in (`pending`,`starting`,`healthy`,`unhealthy`,`exited`,`removed`) | health jsonb null | cpu numeric(5,2) null | memory_bytes bigint null | log_artifact_id uuid null fk artifacts on delete set null | started_at, stopped_at timestamptz null |
Unique `(env_id, name)`; CHECK `(kind = 'service' and pack_id is not null) or (kind = 'support' and support_key is not null)`.

#### contract_checks  (FR-273, FR-274, FR-282)
| id uuid pk | project_id uuid fk projects on delete cascade | interface_id uuid fk service_interfaces on delete cascade | base_commit char(40) | head_commit char(40) | task_id uuid null fk tasks on delete set null | merge_queue_entry_id uuid null fk merge_queue_entries on delete set null | status varchar(8) check in (`running`,`done`) | result varchar(12) null check in (`compatible`,`breaking`,`invalid`,`error`) | changes jsonb not null default '[]' | affected_consumers jsonb not null default '[]' | exception_granted_by uuid null fk users on delete set null | exception_reason text null | exception_granted_at timestamptz null | tool varchar(40) | tool_version varchar(40) | report_artifact_id uuid null fk artifacts on delete set null | checked_at timestamptz null | created_at |
Unique `(interface_id, base_commit, head_commit)` (idempoten); CHECK `exception_granted_by is null or (result = 'breaking' and exception_reason is not null)` *(turunan, OI-45)*; index `(merge_queue_entry_id)`, `(task_id)`. Pengecualian juga ditulis ke audit `contract.exception_granted`. Retensi 365 hari [Menebak].

#### Perubahan existing (0010)
- `test_runs`: + `integration_env_id uuid null fk integration_envs on delete set null`; hasil env memakai `suite = 'integration'` (nilai CHECK v1 yang sudah ada).
- `merge_queue_entries`: CHECK `failure_code` + `CONTRACT_BREAKING`, `INTEGRATION_FAILED` *(turunan)*; + `contract_check_ids jsonb not null default '[]'`; + `integration_env_id uuid null fk integration_envs on delete set null`.
- `projects.settings.merge_queue.<repository_id>` (data, tanpa DDL): + `require_contract_check`, `require_integration_e2e` (`auto` / `off`) *(turunan)*.
- `approvals.action_type`: **tidak berubah** (pengecualian breaking change = keputusan user teraudit, bukan approval).

Retensi 0010: `integration_envs` 180 hari, `contract_checks` 365 hari [Menebak]; katalog service & interface mengikuti umur project.
