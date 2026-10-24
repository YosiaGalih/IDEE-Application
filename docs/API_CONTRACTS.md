# KodeKantor — API Contracts (v1)

Base path: `/api/v1`. Semua payload JSON, `snake_case`, waktu ISO-8601 UTC, ID UUID string.

> **Perluasan (rancangan, belum diimplementasikan):** §12 = kontrak v2 (Portal AI, GitHub App, Portfolio); §13 = kontrak v2.1 (SDLC, Developer Workspace v2, tier M/L) — termasuk **satu-satunya** endpoint WebSocket (terminal interaktif manusia, pengecualian terbatas ADR-014 lewat ADR-059, §13.6); §14 = kontrak v2.2 (language packs, katalog service & kontrak, lingkungan integrasi, monitoring biaya & token realtime — stream `usage.event` via SSE). Semua event lain tetap SSE.

## 1. Envelope & error (PRD §9.1)

Sukses:
```json
{ "request_id": "req_01J…", "status": "completed", "data": { }, "error": null, "meta": { "page": 1, "page_size": 50, "total": 120 } }
```
- `status`: `completed` (sinkron selesai) | `accepted` (pekerjaan asinkron dimulai, HTTP 202) | `running` | `failed`.
- `meta` hanya untuk list.

Error:
```json
{ "request_id": "req_…", "status": "failed", "data": null,
  "error": { "code": "VALIDATION_ERROR", "message": "Input did not pass validation", "details": [ {"loc": ["body","name"], "msg": "field required"} ] } }
```
| HTTP | code |
|---|---|
| 400 | `BAD_REQUEST`, `PATH_VIOLATION`, `ARCHIVE_REJECTED` |
| 401 | `UNAUTHENTICATED`, `SESSION_EXPIRED` |
| 403 | `FORBIDDEN`, `CSRF_FAILED`, `POLICY_DENIED` |
| 404 | `NOT_FOUND` (juga dipakai bila resource ada tapi di luar scope user — tidak membocorkan keberadaan) |
| 409 | `CONFLICT` (termasuk patch ditolak — `details` per hunk), `INVALID_TRANSITION`, `VERSION_CONFLICT`, `APPROVAL_PAYLOAD_MISMATCH`, `GIT_ERROR` (git juga dapat 400/500) |
| 413 | `PAYLOAD_TOO_LARGE` |
| 422 | `VALIDATION_ERROR` |
| 429 | `RATE_LIMITED`, `QUOTA_EXCEEDED` |
| 500 | `INTERNAL_ERROR` (tanpa stack trace / path absolut) |
| 503 | `DEPENDENCY_UNAVAILABLE` |

Header: response selalu `X-Request-ID`. Client boleh kirim `X-Request-ID` (divalidasi `^[A-Za-z0-9_-]{8,64}$`). Mutating endpoint menerima `Idempotency-Key` (disimpan 24 jam di Redis; replay mengembalikan respons pertama).

Pagination list: `?page=1&page_size=50` (maks 200). Filter spesifik per endpoint.

## 2. Auth & sesi (M01)

- Cookie sesi: `kk_session` (HttpOnly, Secure di production, SameSite=Lax, Path=/). Cookie CSRF: `kk_csrf` (tidak HttpOnly). Semua request mutating (POST/PUT/PATCH/DELETE) wajib header `X-CSRF-Token` = nilai `kk_csrf`, kecuali `POST /auth/login`.
- Sesi kedaluwarsa `SESSION_TTL_HOURS` (default 12) idle / 7 hari absolut. Login rate limit: 10/menit/IP dan lockout 15 menit setelah 5 gagal per akun.

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| POST | `/auth/login` | public | `{email, password, remember_me}` | `{user: User}` + set cookies |
| POST | `/auth/logout` | auth | – | `{}` |
| GET | `/auth/me` | auth | – | `{user: User, roles: [{role, scope}], permissions: [string]}` |
| POST | `/auth/password/forgot` | public | `{email}` | `{}` (selalu 200; token dicatat di audit/log dev, email = P1) |
| POST | `/auth/password/reset` | public | `{token, new_password}` | `{}` |
| POST | `/auth/password/change` | auth | `{current_password, new_password}` | `{}` |

`User = {id, email, display_name, status, created_at, last_login_at}`

### RBAC (server-side, `core/rbac.py`)
| Role | Permission |
|---|---|
| owner | semua |
| admin | semua kecuali `owner:transfer` |
| developer | `project:read`, `project:write`, `task:read`, `task:create`, `task:control`, `workspace:read`, `workspace:write`, `test:run`, `review:read`, `review:write`, `approval:decide` (project milik/ditugaskan), `git:read`, `git:branch` |
| reviewer | `project:read`, `workspace:read`, `task:read`, `review:read`, `review:write`, `approval:decide`, `git:read` |
| readonly | `project:read`, `workspace:read`, `task:read`, `review:read`, `git:read` |
Global-only: `admin:users`, `admin:policies`, `settings:providers`, `audit:read`, `usage:read_all`.
Khusus owner/admin (project): `project:delete`, `deploy:production`. Usage per project memakai `project:read`; usage lintas project memakai `usage:read_all`. Izin memutuskan approval bergantung pada `action_type` (lihat §11.1).
Role berscope `project:<id>` hanya berlaku di project itu; role `global` berlaku di semua project yang **dimiliki** user + project tempat dia diberi role. Owner project selalu punya role `owner` pada project-nya.

## 3. Projects & repository (M02–M04)

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| GET | `/dashboard` | auth | – | `{metrics:{active_projects, running_tasks, completed_tasks, failed_tasks, waiting_approval}, recent_projects:[Project], recent_tasks:[TaskSummary], agent_activity:[AgentRunSummary], usage:{period, input_tokens, output_tokens, estimated_cost, cost_status}}` |
| GET | `/projects?status=&q=` | auth | – | `[Project]` |
| POST | `/projects` | auth | `{name, description?, source:{type:"blank"} \| {type:"template", template_id} \| {type:"git_https", url, branch?}, default_branch?}` | `Project` (201) — `git_https` → `status:"accepted"`, progres via `repository.sync_state` |
| POST | `/projects/{id}/imports` | project:write | multipart `file` (.zip/.tar.gz ≤ 200 MB) | `{repository: Repository}` (202) |
| GET | `/projects/templates` | auth | – | `[{id, name, description, stack, icon}]` |
| GET | `/projects/{id}` | project:read | – | `Project` (+ `repository`, `stats`) |
| PATCH | `/projects/{id}` | project:write | `{name?, description?, settings?}` | `Project` |
| POST | `/projects/{id}/archive` | project:write | – | `Project` |
| POST | `/projects/{id}/unarchive` | project:write | – | `Project` |
| DELETE | `/projects/{id}` | project:delete | – | `{approval: Approval}` (202 — `project_delete` R3) |
| GET | `/projects/{id}/tree?path=&depth=` | workspace:read | – | `[{path, name, type:"file"\|"dir", size, children?}]` |
| POST | `/projects/{id}/index` | workspace:write | – | `{job_id}` (202) |
| GET | `/projects/{id}/search?q=&mode=text\|symbol&glob=` | workspace:read | – | `[{path, line, preview, symbol?, kind?}]` (maks 200) |
| GET | `/projects/{id}/stack` | project:read | – | `Stack` |

`Project = {id, name, slug, description, status, stack, default_branch, settings, owner_id, created_at, updated_at, repository: {provider, remote_url, branch, sync_state, last_indexed_at, error}}`
`Stack = {languages:[..], frameworks:[..], package_managers:[..], test_profiles:[profile_id], entrypoints:[path]}`

## 4. Workspace & Git (M05, M15)

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| GET | `/projects/{id}/files/{path}` | workspace:read | `?ref=main\|task:<task_id>` | `{path, content, encoding:"utf-8"\|"binary", size, sha256, language, too_large:bool}` (binary/large → content null) |
| PUT | `/projects/{id}/files/{path}` | workspace:write | `{content, expected_sha256: string\|null}` | `{path, sha256}` — 409 `VERSION_CONFLICT` bila sha berubah |
| POST | `/projects/{id}/files` | workspace:write | `{path, content, type:"file"\|"dir"}` | `{path}` |
| DELETE | `/projects/{id}/files/{path}` | workspace:write | `?expected_sha256=` | `{}` |
| POST | `/projects/{id}/patches` | workspace:write | `{diff, target:"main"}` (unified diff) | `{applied: [FileChange], rejected: [..]}` |
| GET | `/projects/{id}/git/status` | git:read | – | `{branch, head, clean, changes:[{path, status}]}` |
| GET | `/projects/{id}/git/diff?base=&path=` | git:read | – | `Diff` |
| GET | `/projects/{id}/git/branches` | git:read | – | `[{name, head, is_task_branch}]` |
| POST | `/projects/{id}/git/branch` | git:branch | `{name, from?}` | `{name, head}` |
| POST | `/projects/{id}/git/commit` | workspace:write | `{message, paths?}` | `{commit}` |
| GET | `/projects/{id}/git/log?limit=` | git:read | – | `[{commit, author, date, message}]` |

`Diff = {base, head, files:[{path, old_path, status:"added"|"modified"|"deleted"|"renamed", additions, deletions, is_binary, hunks:[{header, old_start, old_lines, new_start, new_lines, lines:[{type:"context"|"add"|"del", old_no, new_no, text}]}], old_content?, new_content?}], stats:{files, additions, deletions}}`
`FileChange = {path, change:"added"|"modified"|"deleted"|"renamed", sha256, old_path?}`

Catatan: `Diff` juga memiliki `truncated: bool`; `head` bernilai `null` bila dibandingkan dengan working tree. Login yang terkunci (lockout) sengaja mengembalikan `401 UNAUTHENTICATED` generik agar tidak membocorkan keberadaan akun.

## 5. Chat & tasks (M06, M08)

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| GET | `/projects/{id}/conversations` | project:read | – | `[Conversation]` |
| POST | `/projects/{id}/conversations` | task:create | `{title?}` | `Conversation` |
| GET | `/conversations/{cid}/messages` | project:read | – | `[Message]` |
| POST | `/conversations/{cid}/messages` | task:create | `{content, context_refs?:[ContextRef], create_task?: bool (default true), workflow_type?, budget?:{max_cost_usd?, max_tokens?}}` | `{message: Message, task: Task\|null}` (202) |
| GET | `/projects/{id}/tasks?status=&q=` | task:read | – | `[TaskSummary]` |
| POST | `/projects/{id}/tasks` | task:create | `{prompt, title?, workflow_type?, priority?, budget?, context_refs?}` | `Task` (202) |
| GET | `/tasks/{tid}` | task:read | – | `Task` |
| POST | `/tasks/{tid}/cancel` | task:control | `{reason?}` | `Task` |
| POST | `/tasks/{tid}/resume` | task:control | – | `Task` (interrupted/failed → queued) |
| POST | `/tasks/{tid}/plan/approve` | task:control | `{plan?: Plan}` (edit opsional) | `Task` (planned → queued) |
| POST | `/tasks/{tid}/clarify` | task:control | `{answers:[{question, answer}]}` | `Task` (needs_clarification → draft) |
| POST | `/tasks/{tid}/request-changes` | task:control | `{feedback, criterion_ids?:[]}` | `Task` (needs_changes/waiting_approval/completed → queued, iteration+1) |
| GET | `/tasks/{tid}/requirements` | task:read | – | `{baseline_version, requirements:[Requirement + criteria:[Criterion]]}` |
| PUT | `/tasks/{tid}/requirements` | task:control | `{requirements:[…]}` | baseline baru (versi +1, lama `superseded`) |
| GET | `/tasks/{tid}/agent-runs` | task:read | – | `[AgentRun]` |
| GET | `/tasks/{tid}/tool-calls` | task:read | – | `[ToolCall]` |
| GET | `/tasks/{tid}/artifacts` | task:read | – | `[Artifact]` |
| GET | `/artifacts/{aid}` | task:read | – | metadata; `/artifacts/{aid}/content` → raw (text/plain, image/png; `Content-Disposition`) |
| GET | `/tasks/{tid}/diff` | task:read | – | `Diff` (base_commit..branch task) |
| GET | `/tasks/{tid}/events` | task:read | SSE, header `Last-Event-ID` atau `?after_seq=` | stream `TaskEvent` |
| GET | `/tasks/{tid}/events/history?after_seq=&limit=` | task:read | – | `[TaskEvent]` (fallback polling) |

`Task = {id, project_id, title, prompt, workflow_type, status, status_reason, current_step, priority, iteration, debug_attempts, budget_limit, usage:{input_tokens, output_tokens, estimated_cost, cost_status}, plan: Plan|null, branch_name, base_commit, created_by, created_at, updated_at, started_at, completed_at, error:{code,message}|null, pending_approval: Approval|null, latest_acceptance: AcceptanceReportSummary|null, last_event_seq}`
`Plan = {summary, steps:[{id, title, specialist, goal, files:[path], depends_on:[id], status:"pending"|"running"|"done"|"skipped"|"failed"}], test_plan:{profiles:[profile_id], notes}, risks:[string]}`
`ContextRef = {type:"file", path, line_start?, line_end?} | {type:"task", id} | {type:"test_run", id} | {type:"diff", task_id} | {type:"finding", id}`

### 5.1 SSE events (`TaskEvent`)
```
id: <seq>            (tidak ada untuk ephemeral)
event: <type>
data: {"task_id": "...", "seq": 12, "type": "tool.call_finished", "ts": "...", "payload": {...}}
```
| type | payload |
|---|---|
| `task.status_changed` | `{from, to, reason}` |
| `task.step_started` / `task.step_finished` | `{step, plan_step_id?, status?}` |
| `task.plan_ready` | `{plan}` |
| `agent.run_started` / `agent.run_finished` | `{agent_run_id, agent_id, status?, usage?}` |
| `agent.delta` (ephemeral) | `{agent_run_id, text}` |
| `agent.message` | `{agent_run_id, agent_id, text}` (ringkasan/narasi final, disimpan) |
| `tool.call_started` / `tool.call_finished` | `{tool_call_id, tool_name, risk_level, status?, summary?}` |
| `approval.requested` / `approval.decided` | `{approval}` |
| `test.run_started` / `test.run_finished` | `{test_run_id, suite, profile, status?, exit_code?, counts?}` |
| `test.output` (ephemeral) | `{test_run_id, stream:"stdout"\|"stderr", chunk}` |
| `review.findings` | `{count_by_severity, finding_ids}` |
| `acceptance.report` | `{report_id, status, passed, failed, unverified, waived, blocked}` |
| `usage.updated` | `{input_tokens, output_tokens, estimated_cost, cost_status}` |
| `usage.budget_exceeded` | `{limit, used}` |
| `task.log` | `{level, message}` |

## 6. Agents (M07)

| GET | `/agents?group=&mode=&q=` | auth | – | `[AgentProfile]` |
| GET | `/agents/{agent_id}` | auth | – | `AgentProfile + {recent_runs:[AgentRunSummary], stats:{runs, success_rate, avg_duration_ms}}` |
| PATCH | `/agents/{agent_id}` | admin:policies | `{enabled?}` | `AgentProfile` (P1) |

`AgentProfile = {agent_id, slug, name, group, version, enabled, mvp_mode, priority, description, capabilities:[..], tools:[..], permission_profile:{filesystem, sandbox, git, network, deployment, approval_grant}, model_tier, limits, runtime_agent, status:"ready"|"disabled"|"planned"}`

## 7. Testing & terminal (M11, M12)

| GET | `/projects/{id}/test-profiles` | workspace:read | – | `[{profile_id, suite, label, image, timeout_s, network}]` (sesuai stack) |
| POST | `/projects/{id}/test-runs` | test:run | `{profile_id, target:"main"\|"task:<id>", args?:[..]}` | `TestRun` (202) |
| GET | `/projects/{id}/test-runs?task_id=` | task:read | – | `[TestRun]` |
| GET | `/test-runs/{rid}` | task:read | – | `TestRun + {stdout_tail, stderr_tail, cases:[{name, classname, status, duration_s, message}]}` |
| POST | `/test-runs/{rid}/cancel` | test:run | – | `TestRun` |
| GET | `/test-runs/{rid}/stream` | task:read | SSE `test.output` / `test.run_finished` | |

Terminal UI (M11) = viewer read-only untuk output run sandbox; input hanya berupa pilihan profil (tidak ada shell host). `args` divalidasi per profil (regex allowlist, mis. path test `^[\w./:-]{1,200}$`).

`TestRun = {id, project_id, task_id, suite, command_profile, argv, status, exit_code, passed, failed, skipped, errors, coverage_percent, environment_id, started_at, finished_at, duration_ms, log_artifact_id}`

## 8. Reviews, acceptance, approvals (M13, M14)

| GET | `/tasks/{tid}/reviews` | review:read | – | `{findings:[Finding], summary:{by_severity}}` |
| POST | `/reviews/findings/{fid}/feedback` | review:write | `{status:"accepted"\|"dismissed", feedback?}` | `Finding` |
| POST | `/tasks/{tid}/reviews/comments` | review:write | `{file_path, line_start, line_end?, body, request_fix: bool}` | `Finding` (reviewer_type `code_review`, dibuat oleh user) |
| GET | `/tasks/{tid}/acceptance` | task:read | – | `{baseline_version, criteria:[Criterion], latest_report: AcceptanceReport\|null, history:[AcceptanceReportSummary]}` |
| POST | `/tasks/{tid}/acceptance/revalidate` | task:control | – | `{job_id}` (202) — hanya User Proxy ulang, tanpa implementasi |
| POST | `/tasks/{tid}/acceptance/criteria/{cid}/waive` | approval:decide | `{reason}` | `Criterion` (status `waived_by_user`, audit) |
| POST | `/tasks/{tid}/acceptance/criteria/{cid}/feedback` | task:control | `{verdict:"ok"\|"not_ok", feedback}` | `Criterion` (preferensi subjektif) |
| GET | `/tasks/{tid}/acceptance/report.md` | task:read | – | ekspor Markdown |
| GET | `/approvals?status=pending&project_id=` | auth | – | `[Approval]` (hanya yang dapat diputuskan user) |
| GET | `/approvals/{aid}` | auth+scope | – | `Approval` |
| POST | `/approvals/{aid}/decision` | approval:decide | `{decision:"approve"\|"reject", payload_hash, note?}` | `Approval` — 409 `APPROVAL_PAYLOAD_MISMATCH` bila hash ≠ |

`Criterion = {id, key, requirement_id, requirement_key, criterion, verification_method, mandatory, status, evidence_refs:[EvidenceRef], rationale, waived_by, waived_at}`
`EvidenceRef = {ref:"test_run:<id>", type, label, url}`
`AcceptanceReport = {id, task_id, iteration, status, summary, confidence, criteria_results:[{criterion_id, key, status, evidence_refs, rationale, downgraded}], gaps:[{criterion_key, description, suggested_fix}], risks:[..], next_actions:[..], created_at}`
`Finding = {id, task_id, reviewer_type, severity, category, title, file_path, line_start, line_end, evidence, recommendation, status, user_feedback, created_at}`
`Approval = {id, project_id, task_id, action_type, risk_level, summary, payload, payload_hash, requested_by, status, decided_by, decided_at, decision_note, expires_at, created_at}`

## 9. Settings, usage, knowledge, deployments, admin

| GET | `/settings/providers` | settings:providers | – | `[{id, provider, base_url, model_policy, secret_status:"configured"\|"missing", secret_hint:"sk-ant-…a1b2", enabled, pricing_version}]` |
| PATCH | `/settings/providers/{id}` | settings:providers | `{model_policy?, enabled?, base_url?}` | idem (API key TIDAK pernah diterima/dikirim lewat API; dikelola di env/secret file — ADR-004) |
| POST | `/settings/providers/{id}/smoke-test` | settings:providers | – | `{job_id}` → hasil readiness gate (PRD §7.3.6) |
| GET | `/usage?project_id=&task_id=&from=&to=&group_by=day\|model\|agent` | usage:read / project:read | – | `{totals:{input_tokens, output_tokens, estimated_cost, actual_cost:null, cost_status}, series:[..], records:[UsageRecord]}` |
| GET | `/projects/{id}/budget` · PATCH | project:read / project:write | `{max_cost_usd, max_tokens, max_tool_iterations}` | default budget project |
| GET | `/projects/{id}/knowledge` | project:read | `?type=` | `[ProjectMemory]` |
| POST | `/projects/{id}/knowledge` | project:write | `{type, title, content}` | `ProjectMemory` |
| PATCH/DELETE | `/knowledge/{mid}` | project:write | | |
| POST | `/projects/{id}/docs/generate` | task:create | `{kind:"readme"\|"setup_guide"\|"architecture"}` | `Task` (G35 profile) |
| POST | `/projects/{id}/deployments/preflight` | project:write | `{environment:"staging"}` | `Deployment` (202) |
| POST | `/deployments/{did}/staging` | project:write | – | `{approval}` (deploy_staging, R3) |
| POST | `/deployments/{did}/rollback` | project:write | – | `Deployment` |
| GET | `/projects/{id}/deployments` | project:read | – | `[Deployment]` |
| GET | `/admin/users` · POST · PATCH `/admin/users/{uid}` | admin:users | `{email, display_name, password?, roles:[{role, scope}], status?}` | `User` |
| POST | `/admin/users/{uid}/reset-token` | admin:users | – | `{token, expires_at}` (ditampilkan sekali) |
| GET | `/admin/policies` · PUT `/admin/policies/{key}` | admin:policies | `{value}` | `PolicySetting` |
| GET | `/audit-events?project_id=&actor=&action=&from=&to=` | audit:read | – | `[AuditEvent]` |
| GET | `/health/live` · `/health/ready` | public | – | `{status, checks:{db, redis, sandbox_manager, migrations}}` |

## 10. Sandbox Manager internal API (tidak diekspos publik)

Auth: `Authorization: Bearer ${SANDBOX_MANAGER_TOKEN}` (≥ 32 byte, dibandingkan constant-time). Network: sandbox-manager tergabung di `app` (agar dapat dijangkau api/worker) dan `exec` (ke docker-socket-proxy). **Hanya** sandbox-manager dan docker-socket-proxy yang berada di `exec`; api/worker/scheduler tidak pernah di `exec` karena proxy tanpa autentikasi yang mengizinkan pembuatan container setara root host (ADR-009).

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/healthz` | – | `{status:"ok", driver, docker:"ok"\|"unavailable", runtime}` |
| GET | `/v1/profiles` | – | `[{profile_id, image, suite, timeout_s, network, args_pattern}]` |
| POST | `/v1/runs` | `{project_id, task_id, workspace_subpath, profile_id, args:[..], timeout_s?, network?:"none"\|"egress", wait?: bool}` | `{run_id, status}` (`wait=true` → hasil final) |
| GET | `/v1/runs/{run_id}` | – | `{run_id, status:"queued"\|"running"\|"succeeded"\|"failed"\|"timeout"\|"killed"\|"error", exit_code, started_at, finished_at, duration_ms, image, image_digest, stdout_tail, stderr_tail, truncated, artifacts:{junit?, coverage?}}` |
| GET | `/v1/runs/{run_id}/logs?offset=` | – | `{offset, next_offset, chunks:[{stream, data}], done}` |
| POST | `/v1/runs/{run_id}/kill` | – | `{status}` |
| POST | `/v1/tasks/{task_id}/kill` | – | `{killed: n}` |

Validasi manager: `workspace_subpath` harus cocok `^[0-9a-f-]{36}/tasks/[0-9a-f-]{36}$` atau `^[0-9a-f-]{36}/repo$` dan berada di bawah `WORKSPACE_ROOT` setelah `realpath`; `profile_id` harus ada; `args` cocok `args_pattern`; `timeout_s ≤ profile.max_timeout_s`; `network=egress` hanya bila profil mengizinkan dan `SANDBOX_EGRESS=allowlist`.

## 11. Penyelarasan kontrak v1.1 (ADR-029) — dikunci di rancangan, implementasi belum

Daftar ini menutup ketidakcocokan yang ditemukan agent frontend dan backend pada Wave 1–2. Bila bertentangan dengan bagian sebelumnya, bagian ini yang berlaku.

| # | Area | Keputusan kontrak |
|---|---|---|
| 1 | `Task` | Tambah `created_by: {id, display_name}`, `cancel_requested: bool`, `clarification_questions: [{question, why, blocking}]` (dari checkpoint; kosong bila tidak ada), `iteration_max` (= `TASK_MAX_ITERATIONS`). |
| 2 | `TaskSummary` (list) | `{id, project_id, title, status, status_reason, current_step, workflow_type, priority, iteration, usage:{input_tokens, output_tokens, estimated_cost, cost_status}, has_pending_approval, created_at, updated_at}`. |
| 3 | `cost_status` | `estimated` \| `partial` \| `unavailable` (ADR-025). UI: `partial` = "estimasi sebagian". |
| 4 | `Finding` | Tambah `author: {type:"agent"\|"user"\|"scanner", id, label}` agar komentar user terpisah dari output AI. |
| 5 | `criteria_results[]` | Tambah `downgraded: bool`, `downgrade_reason: string\|null`, `subjective: bool`. |
| 6 | Payload approval | `apply_changes`: `{task_id, branch, base_commit, head_commit, target_branch, diff_sha256, files_changed, unverified_criteria:[key]}`; `budget_increase`: `{task_id, current_limit:{max_cost_usd,max_tokens}, used:{cost_usd,tokens,cost_status}, proposed_limit:{…}}`; `tool_r3`: `{task_id, agent_run_id, tool, input}`; `project_delete`: `{project_id, name, previous_status}`; `deploy_staging`: `{deployment_id, project_id, commit}`. |
| 7 | `/test-runs/{id}/stream` | Frame SSE sama dengan TaskEvent (`event: test.output` / `test.run_finished`, `data: {test_run_id, seq?, type, ts, payload}`), channel `kk:test:{id}`. |
| 8 | Smoke test | `POST /settings/providers/{id}/smoke-test` sinkron (≤ 60 dtk) → `{provider, mode:"anthropic"\|"scripted", ready, checks:[{key, label, status:"passed"\|"failed"\|"skipped", detail}], ran_at}`; key: `api_key_valid, model_accessible, messages_ok, streaming_ok, usage_reported, tool_use_roundtrip, timeout_cancel_handled, secret_not_in_logs`. Tidak ada endpoint polling. `ProviderConfig` menambah `llm_provider_mode`. |
| 9 | Usage | `series: [{key, input_tokens, output_tokens, estimated_cost, cost_status}]`; list `records` dipaginasi (`page`, `page_size`, `meta.total`). |
| 10 | Audit | Query `result=success\|denied\|error` didukung server; `action` = prefix match. |
| 11 | Admin users | `GET /admin/users` menyertakan `roles:[{role, scope}]`; `PATCH` menerima `{display_name?, status?, roles?}`; `GET /admin/users/{uid}`, `GET /admin/policies/{key}` resmi. |
| 12 | Knowledge | `GET /knowledge/{mid}` dan `POST /knowledge/{mid}/refresh` resmi. |
| 13 | Deployment | `preflight: {checks:[{key, status:"passed"\|"failed"\|"skipped", detail}], ran_at}`. |
| 14 | Projects | `POST /projects` sumber `git_https` → 202 dengan `meta.job_id`; `DELETE` → `{approval, project}`; import → `{repository, job_id, upload}`; `Project` menambah `permissions:[perm]` dan `stats:{files, last_commit, open_tasks}`. File read menambah `restricted: bool` (file denylist dikembalikan tanpa isi). |
| 15 | Approvals | `GET /approvals?status=` menerima `pending\|approved\|rejected\|expired\|consumed\|all`. |
| 16 | Artifacts | `/artifacts/{id}/content` selalu `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox`, dan tidak pernah `text/html` inline (HTML → `text/plain` atau `attachment`). |
| 17 | Login lockout | Tetap `401` generik (anti enumerasi). UI tidak boleh menebak status lockout. |
| 18 | Settings landing | `/settings` mengarah ke sub-halaman pertama yang diizinkan permission user. |

### 11.1 Matriks approval per `action_type` (ADR-034)

| action_type | Dibuat oleh (server-side) | Payload | Handler | Kedaluwarsa | Izin memutuskan | Self-approval |
|---|---|---|---|---|---|---|
| `apply_changes` | orchestrator S9 | lihat §11 #6 | workflow: merge → `completed`; konflik → `needs_changes`; reject → `rejected` | 7 hari | `approval:decide` | boleh (pilot single-owner) |
| `budget_increase` | AgentRuntime/BudgetGuard via orchestrator | §11 #6 | workflow: limit baru → `queued`; reject → `cancelled` | 24 jam | `approval:decide` | boleh |
| `tool_r3` | ToolGateway (tool R3 tanpa tipe khusus) | `{task_id, agent_run_id, tool, input}` | workflow: resume → `queued`; reject → `failed` | 24 jam | `approval:decide` | boleh |
| `git_push` (P2) | ToolGateway (`git.push`) | `{task_id, remote, branch, head_commit}` | workflow: resume | 24 jam | `approval:decide` + `git:branch` | tidak (policy `four_eyes`) |
| `deploy_staging` (P2) | deployments service (aksi user) | `{deployment_id, project_id, commit}` | deployments: build + smoke di sandbox | 24 jam | `approval:decide` + `project:write` | boleh |
| `deploy_production` (P2, nonaktif) | deployments service | `{deployment_id, commit, environment}` | – (ditolak policy di MVP) | 4 jam | `deploy:production` | **tidak** |
| `project_delete` | projects service (aksi user) | `{project_id, name, previous_status}` | platform: `deleted` + purge 7 hari; reject → status sebelumnya | 7 hari | `project:delete` | boleh untuk owner tunggal; `four_eyes` bila diaktifkan |

Catatan: `waive_criterion` **bukan** approval — waiver adalah aksi langsung user ber-izin `approval:decide` yang wajib alasan dan ter-audit (nilai CHECK dicadangkan, tidak dipakai MVP). Tool R3 yang punya tipe khusus memakai tipe itu (mis. `git.push` → `git_push`); `tool_r3` hanya fallback. Policy `four_eyes` (policy_settings, P1) mewajibkan pemutus ≠ peminta untuk tipe yang ditandai.

### 11.2 Shape yang sebelumnya belum didefinisikan

| Shape | Field |
|---|---|
| `AgentRun` | `{id, task_id, agent_id, agent_name, agent_version, step, model_provider, model_name, prompt_version, status: running\|succeeded\|failed\|cancelled\|budget_exceeded\|approval_required, iterations, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, estimated_cost, cost_status, error, started_at, finished_at, duration_ms}` |
| `AgentRunSummary` | `{id, task_id, project_id, agent_id, agent_name, status, started_at, duration_ms}` |
| `ToolCall` | `{id, task_id, agent_run_id, tool_name, risk_level, status, output_summary, output_artifact_id, approval_id, duration_ms, created_at}` (input disanitasi, hanya untuk user ber-izin `task:read`) |
| `Artifact` | `{id, task_id, type, mime, size_bytes, checksum, metadata, created_at, content_url}` |
| `Message` | `{id, conversation_id, role, agent_id, content, context_refs, task_id, created_at}` |
| `Requirement` | `{id, key, title, description, priority, status, baseline_version, criteria:[Criterion]}` |
| `UsageRecord` | `{id, task_id, agent_run_id, provider, model, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, estimated_cost, actual_cost:null, cost_status, pricing_version, created_at}` |
| `Deployment` | `{id, project_id, task_id, environment, status, preflight, approval_id, url, rollback_of, created_by, created_at, updated_at}` |
| `AuditEvent` | `{id, ts, actor_type, actor_id, action, resource_type, resource_id, project_id, result, metadata, correlation_id}` |
| `AcceptanceReportSummary` | `{id, iteration, status, passed, failed, unverified, waived, blocked, confidence, created_at}` |
| `TestRun` (tambahan) | `failure_kind: none\|assertion\|lint\|build\|dependency\|runner_crash\|timeout\|infra` (ADR-035) |

### 11.3 Endpoint tambahan dari review desain

| Method | Path | Perm | Keterangan |
|---|---|---|---|
| POST | `/tasks/{tid}/pause` | task:control | ADR-031: berhenti di batas langkah berikutnya → `interrupted` (`status_reason=paused_by_user`); lanjut lewat `/resume` |
| GET | `/settings/providers/{id}/readiness` | settings:providers | ADR-032: hasil readiness gate terakhir (`readiness_status`, `checked_at`, `checks`) |
| — | `budget` task/project | – | `budget_limit` menambah `max_duration_minutes` (default `TASK_MAX_DURATION_MINUTES`) |
| — | `SandboxRunRequest` / `POST /v1/runs` | – | menambah `read_only: bool`; `target:"main"` selalu `read_only=true` (ADR-033) |

## 12. Kontrak API v2 — Portal AI, Integrasi GitHub, Portfolio (Rancangan)

> **Status: rancangan, belum diimplementasikan.** Berlaku setelah gate G3 (ADR-036). Sumber: `docs/PLATFORM_EXPANSION.md` §4.7, §5.6, §6.4, §9b (K1–K4); desain rinci `docs/IMPLEMENTATION_DESIGN_V2.md` (§5 interface, §6 alur, §9 keamanan). Semua aturan §1 (envelope, error, `X-Request-ID`, `Idempotency-Key`, pagination) dan §2 (sesi, CSRF, RBAC, 404 untuk resource di luar scope) tetap berlaku, kecuali endpoint yang dinyatakan *public*. Resource Portal AI dan Portfolio dimiliki **user** (`owner_id`), bukan project: user lain mendapat 404.

### 12.0 Kode error tambahan

| HTTP | code | Kapan | `details` |
|---|---|---|---|
| 400 | `UNSUPPORTED_FILE_TYPE` | upload/aset dengan magic bytes di luar allowlist (termasuk SVG/HTML) | `{detected_mime}` |
| 401 | `WEBHOOK_SIGNATURE_INVALID` | `X-Hub-Signature-256` hilang/salah (tanpa menulis DB) | – |
| 403 | `CAPABILITY_DISABLED` | capability nonaktif (flag/kill switch/`capability_settings`) | `{capability, reason}` |
| 403 | `SENSITIVITY_BLOCKED` | sensitivitas data melebihi `max_sensitivity` capability/engine (mis. Skills untuk `confidential`) | `{capability, sensitivity, allowed_max}` |
| 403 | `ASSET_LINK_INVALID` | URL bertanda tangan kedaluwarsa/diubah/milik user lain | – |
| 409 | `GITHUB_NOT_LINKED` | user belum punya instalasi/grant atas repo | `{install_url}` |
| 409 | `TASK_NOT_READY` | push-PR diminta saat status task ≠ `completed` (apply sudah disetujui, PE §5.3) atau worktree kotor | `{status}` |
| 409 | `HEAD_MOVED` | head branch task berubah dari `head_commit` yang disetujui | `{expected, actual}` |
| 409 | `SECRET_SCAN_BLOCKED` | temuan secret pada diff yang akan di-push | `{findings:[{path, line, rule}]}` |
| 409 | `PRIVACY_CHECK_FAILED` | cek privasi portfolio punya temuan `block` | `{findings:[PrivacyFinding]}` |
| 409 | `AI_DRAFT_NOT_APPROVED` | item hasil AI dijadikan `public` sebelum disetujui (AX-09) | `{item_id}` |
| 422 | `MODERATION_BLOCKED` | prompt/konten ditolak moderasi (S52) | `{stage, categories, reasons}` (teks aman) |
| 429 | `QUOTA_EXCEEDED` (sudah ada) | kuota capability habis | `{capability, unit, remaining, resets_at}` |
| 503 | `DEPENDENCY_UNAVAILABLE` (sudah ada) | provider/vendor/GitHub tidak tersedia (circuit breaker terbuka) | `{provider, retry_after_s}` |

Penolakan `POLICY_DENIED` (403) tetap dipakai untuk pelanggaran path policy (mis. `.github/workflows/**`) dan aksi R4.

### 12.1 RBAC v2

Izin baru (PLATFORM_EXPANSION §7) — kemampuan tetap dibatasi `capability_settings` dan kuota (§12.2 `Capability`).

| Permission | owner | admin | developer | reviewer | readonly | Scope |
|---|---|---|---|---|---|---|
| `assistant:use` (chat, riset, KB, analisis) | ✓ | ✓ | ✓ | ✓ | – | global (resource milik sendiri) |
| `assistant:docs` (buat dokumen) | ✓ | ✓ | ✓ | – | – | global |
| `assistant:image` (generate/edit gambar) | ✓ | ✓ | ✓ | – | – | global |
| `integrations:github:manage` (instal/tautkan/sinkron instalasi) | ✓ | ✓ | – | – | – | global |
| `github:push` (minta push branch + PR) | ✓ | ✓ | ✓ | – | – | project |
| `portfolio:edit` | ✓ | ✓ | ✓ | ✓ | – | portfolio milik sendiri |
| `portfolio:publish` | ✓ | ✓ | ✓ | ✓ | – | portfolio milik sendiri |

Pengaturan capability (`/admin/capabilities`) memakai `admin:policies`; usage lintas user memakai `usage:read_all` (sudah ada). Memutuskan approval v2: lihat §12.6.

### 12.2 Portal AI (`/assistant/*`, `/capabilities`, `/assets/*`)

**Ruang & thread**

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| GET | `/assistant/spaces?q=&archived=` | assistant:use | – | `[Space]` |
| POST | `/assistant/spaces` | assistant:use | `{name, description?, sensitivity?: "public"\|"internal"\|"confidential" (default internal), default_model_tier?}` | `Space` (201) |
| PATCH | `/assistant/spaces/{sid}` | assistant:use (pemilik) | `{name?, description?, sensitivity?, default_model_tier?, archived?}` | `Space` — menurunkan sensitivitas ditulis ke audit |
| DELETE | `/assistant/spaces/{sid}?cascade=` | assistant:use (pemilik) | – | `{}` — 409 `CONFLICT` bila masih ada thread dan `cascade` ≠ true |
| GET | `/assistant/threads?space_id=&kind=&q=&archived=` | assistant:use | – | `[ThreadSummary]` |
| POST | `/assistant/threads` | assistant:use | `{space_id?, title?, model_tier?: "fast"\|"default"\|"complex", assistant_id?, sensitivity?}` | `Thread` (201) — sensitivitas ≥ sensitivitas space |
| GET | `/assistant/threads/{tid}` | assistant:use (pemilik) | – | `Thread` |
| PATCH | `/assistant/threads/{tid}` | assistant:use (pemilik) | `{title?, model_tier?, space_id?, archived?, sensitivity?}` | `Thread` |
| DELETE | `/assistant/threads/{tid}` | assistant:use (pemilik) | – | `{}` — pesan, sitasi, event, dan aset tidak di-*pin* ikut dihapus |
| GET | `/assistant/threads/{tid}/messages?before=&limit=` | assistant:use (pemilik) | – | `[AssistantMessage]` (urut waktu, maks 100) |
| POST | `/assistant/threads/{tid}/messages` | assistant:use | `{content (≤ 32 000 char), mode: "chat"\|"research_quick"\|"research_deep"\|"kb"\|"analysis", attachments?: [Attachment], model_tier?, web_with_kb?: bool (default false), research?: {blocked_domains?: [string]}}` | `{user_message, assistant_message, events_url}` (202) — capability dicek sebelum enqueue |
| GET | `/assistant/threads/{tid}/events` | assistant:use (pemilik) | SSE, `Last-Event-ID` atau `?after_seq=` | stream `AssistantEvent` (§12.2.1) |
| GET | `/assistant/threads/{tid}/events/history?after_seq=&limit=` | assistant:use (pemilik) | – | `[AssistantEvent]` (fallback polling) |
| POST | `/assistant/messages/{mid}/cancel` | assistant:use (pemilik) | – | `AssistantMessage` (`cancelled`) |
| POST | `/assistant/messages/{mid}/regenerate` | assistant:use (pemilik) | `{model_tier?}` | `{assistant_message}` (202) — versi lama `superseded_by` |
| PATCH | `/assistant/messages/{mid}` | assistant:use (pemilik) | `{content}` (hanya pesan `user`) | `{user_message, assistant_message}` (202) — pesan sesudahnya ditandai `superseded` |
| GET | `/assistant/messages/{mid}/citations` | assistant:use (pemilik) | – | `[Citation]` |
| GET | `/assistant/search?q=&space_id=` | assistant:use | – | `[{thread_id, message_id, role, snippet, created_at}]` (FTS pesan milik user, maks 50) |

`Attachment = {type:"file", id} | {type:"kb_collection", id} | {type:"asset", id} | {type:"project", id}` (project dilampirkan read-only; butuh `project:read`).

**File, dokumen, gambar, analisis**

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| POST | `/assistant/files` | assistant:use | multipart `file` (≤ `ASSISTANT_UPLOAD_MAX_MB`; pdf, docx, xlsx, pptx, csv, txt, md, png, jpeg, webp), `sensitivity?`, `space_id?`, `thread_id?` | `AssistantFile` (201) — 413 `PAYLOAD_TOO_LARGE`, 400 `UNSUPPORTED_FILE_TYPE` |
| GET | `/assistant/files?space_id=&thread_id=` | assistant:use | – | `[AssistantFile]` |
| GET | `/assistant/files/{fid}` | assistant:use (pemilik) | – | `AssistantFile` |
| DELETE | `/assistant/files/{fid}?force=` | assistant:use (pemilik) | – | `{}` — 409 bila dipakai dokumen KB dan `force` ≠ true |
| GET | `/assistant/templates?kind=` | assistant:docs | – | `[{id, name, kind, font_family, preview_asset_id}]` |
| POST | `/assistant/documents` | assistant:docs | `{kind: "pptx"\|"docx"\|"xlsx"\|"pdf", prompt? \| outline?, template_id?, engine?: "auto"\|"claude_skills"\|"local_sandbox", thread_id?, source_file_ids?, locale?: "id"\|"en"}` | `{job_id, thread_id, assistant_message, route: Route}` (202) — 403 `SENSITIVITY_BLOCKED` bila `claude_skills` diminta untuk data `confidential`; tanpa `thread_id` server membuat thread |
| POST | `/assistant/images/estimate` | assistant:image | `{size, n?, edit?: bool}` | `{estimated_cost, cost_status, pricing_version, remaining_today}` |
| POST | `/assistant/images` | assistant:image | `{prompt (≤ 4000), size, n? (≤ 4), style?, seed?, reference_asset_ids?, thread_id?}` | `{job_id, thread_id, assistant_message, estimated_cost, cost_status}` (202) — 403/429 tanpa panggilan vendor |
| POST | `/assistant/images/{asset_id}/edit` | assistant:image | `{instruction, mask_asset_id?, size?, thread_id?}` | idem (202) |
| GET | `/assistant/images?thread_id=&page=` | assistant:image | – | `[ImageAsset]` (galeri milik user) |
| POST | `/assistant/analysis` | assistant:use | `{file_ids, question, thread_id?, engine?: "auto"\|"local_sandbox"\|"code_execution_remote"}` | `{job_id, thread_id, assistant_message, route: Route}` (202) |

**Knowledge base (ADR-046)**

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| GET | `/assistant/kb/collections?space_id=` | assistant:use | – | `[KBCollection]` |
| POST | `/assistant/kb/collections` | assistant:use | `{name, description?, space_id?, sensitivity?}` | `KBCollection` (201) |
| PATCH | `/assistant/kb/collections/{cid}` | assistant:use (pemilik) | `{name?, description?, sensitivity?, status?: "active"\|"archived"}` | `KBCollection` |
| DELETE | `/assistant/kb/collections/{cid}` | assistant:use (pemilik) | – | `{}` (dokumen & chunk ikut terhapus; file tetap) |
| GET | `/assistant/kb/collections/{cid}/documents` | assistant:use (pemilik) | – | `[KBDocument]` (UI melakukan polling selama ada status non-final) |
| POST | `/assistant/kb/collections/{cid}/documents` | assistant:use (pemilik) | `{file_id}` | `{document: KBDocument, job_id}` (202) |
| POST | `/assistant/kb/documents/{did}/reindex` | assistant:use (pemilik) | – | `{job_id}` (202) |
| DELETE | `/assistant/kb/documents/{did}` | assistant:use (pemilik) | – | `{}` |
| POST | `/assistant/kb/search` | assistant:use | `{collection_ids, q (≤ 500), top_k? (≤ 20)}` | `[KBHit]` (pratinjau retrieval; R0, tanpa LLM) |

**Asisten kustom, usage, capability**

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| GET | `/assistant/assistants` | assistant:use | – | `[CustomAssistant]` (milik sendiri + yang dibagikan ke role user) |
| POST | `/assistant/assistants` | assistant:use | `{name, description?, system_prompt (≤ 8000), capabilities:[CapabilityName], kb_collection_ids?, model_tier?, shared_with_roles?}` | `CustomAssistant` (201) — capability di luar yang aktif untuk pembuat → 422 |
| GET/PATCH/DELETE | `/assistant/assistants/{aid}` | assistant:use (pemilik; GET juga penerima share) | sebagian field POST | `CustomAssistant` / `{}` |
| GET | `/assistant/usage?from=&to=&group_by=day\|capability` | assistant:use (sendiri) / usage:read_all (`user_id=`) | – | `{totals:{input_tokens, output_tokens, web_search_requests, images, documents, estimated_cost, cost_status}, series:[{key, …}], by_capability:[{capability, units:{…}, estimated_cost, cost_status}]}` |
| GET | `/capabilities` | auth | – | `[Capability]` — hanya modul yang aktif untuk user (AX-11) |
| GET | `/capabilities/data-flow` | auth | – | `[{capability, data, recipient, zdr_eligible: bool\|null, retention_note}]` |
| GET | `/admin/capabilities` | admin:policies | – | `[{capability, spec, settings:[CapabilitySetting], usage_month:{…}}]` |
| PUT | `/admin/capabilities/{name}/settings` | admin:policies | `{scope: "global"\|"role:<r>"\|"user:<uuid>", enabled?, kill_switch?, limits?: {monthly_usd?, monthly_units?, daily_units?}, config?}` | `CapabilitySetting` (audit `capability.settings_changed`) |

**Aset**

| Method | Path | Perm | Request | Response |
|---|---|---|---|---|
| GET | `/assets/{id}` | pemilik aset | – | `Asset` |
| POST | `/assets/{id}/signed-url` | pemilik aset | `{disposition?: "attachment"\|"inline"}` (`inline` hanya png/jpeg/webp/pdf) | `{url, expires_at}` (TTL `ASSET_SIGNED_URL_TTL_SECONDS`) |
| GET | `/assets/{id}/download?exp=&disp=&sig=` | **sesi user yang sama** + tanda tangan valid | – | byte file; `Content-Disposition` sesuai `disp` (default `attachment`), `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox`, `Cache-Control: private, no-store`; 403 `ASSET_LINK_INVALID`. Tidak pernah `text/html` |
| GET | `/assets/{id}/preview` | pemilik aset | – | thumbnail raster inline (png/webp) atau 404 bila tidak ada |
| POST | `/assets/{id}/pin` | pemilik aset | `{pinned: bool}` | `Asset` (pin mengecualikan dari retensi) |
| DELETE | `/assets/{id}` | pemilik aset | – | `{}` — 409 bila dipakai publikasi portfolio aktif |

**Shapes**

| Shape | Field |
|---|---|
| `Space` | `{id, name, description, sensitivity, default_model_tier, archived_at, thread_count, created_at, updated_at}` |
| `ThreadSummary` | `{id, space_id, kind:"chat"\|"portfolio"\|"system", title, model_tier, sensitivity, last_message_at, archived_at}` |
| `Thread` | `ThreadSummary + {assistant_id, last_event_seq, usage:{input_tokens, output_tokens, web_search_requests, estimated_cost, cost_status}}` |
| `AssistantMessage` | `{id, thread_id, role:"user"\|"assistant", mode, status:"queued"\|"streaming"\|"completed"\|"failed"\|"cancelled"\|"blocked", content (Markdown teks; untuk assistant = teks final), attachments:[Attachment], citations_count, grounding:{status:"cited"\|"partially_cited"\|"uncited"\|"not_applicable", web_used, searched_at_min, searched_at_max, uncited_spans:[{start, end, reason}]}, assets:[AssetSummary], model, agent_id, usage:{input_tokens, output_tokens, web_search_requests, estimated_cost, cost_status}, error:{code, message}\|null, parent_id, superseded_by, created_at, completed_at}` |
| `Citation` | `{id, ordinal, kind:"web"\|"kb"\|"document", location, cited_text, url\|null, title\|null, query\|null, searched_at\|null, chunk_id\|null, asset_id\|null, page_start, page_end, answer_start, answer_end, verified: bool\|null}` — `kind:"web"` selalu punya `url` + `searched_at` (AX-04) |
| `AssetSummary` | `{id, kind, type:"image"\|"pptx"\|"docx"\|"xlsx"\|"pdf"\|"chart"\|"csv", filename, mime, size_bytes, thumbnail_url\|null, engine\|null, created_at}` |
| `Asset` | `AssetSummary + {sha256, sensitivity, pinned, retention_until, provenance:{provider, model, engine, prompt_hash, request_id, c2pa_present}}` |
| `ImageAsset` | `Asset + {prompt_excerpt (≤ 200), size, moderation_status}` |
| `AssistantFile` | `{id, asset_id, filename, mime, size_bytes, sensitivity, page_count, extraction_status:"pending"\|"extracting"\|"ready"\|"failed"\|"unsupported", extraction_error, space_id, thread_id, created_at}` |
| `KBCollection` | `{id, name, description, space_id, sensitivity, status, document_count, chunk_count, created_at, updated_at}` |
| `KBDocument` | `{id, collection_id, file_id, title, status:"pending"\|"extracting"\|"contextualizing"\|"indexed"\|"failed", page_count, chunk_count, error, ingest_cost:{estimated_cost, cost_status}, indexed_at}` |
| `KBHit` | `{chunk_id, document_id, title, page_start, page_end, text_excerpt (≤ 600), score}` |
| `CustomAssistant` | `{id, owner_id, name, description, system_prompt, capabilities, kb_collection_ids, model_tier, shared_with_roles, enabled, version, created_at, updated_at}` |
| `Capability` | `{name, portals, enabled, disabled_reason\|null, permission, risk_level, max_sensitivity, engines, limits:{monthly_usd, monthly_units, daily_units}, used:{usd, units}, remaining:{usd\|null, units}, resets_at, estimated_unit_costs:{unit: usd\|null}, degraded: bool}` |
| `CapabilitySetting` | `{capability, scope, enabled\|null, kill_switch, limits, config, updated_by, updated_at}` |
| `Route` | `{capability, engine, provider, model\|null, reason, degraded}` |

#### 12.2.1 SSE event Portal AI (`AssistantEvent`)

Frame sama dengan `TaskEvent` (§5.1): `id: <seq>` (tidak ada untuk ephemeral), `event: <type>`, `data: {"thread_id", "seq", "type", "ts", "payload"}`. Channel Redis `kk:thread:{id}`; persisten di `assistant_events` (retensi 30 hari); heartbeat `: ping` tiap 15 dtk; replay dari DB lalu live dengan dedupe `seq`.

| type | Persisten | payload |
|---|---|---|
| `message.started` | ya | `{message_id, mode, model_tier, route: Route}` |
| `assistant.delta` | **ephemeral** | `{message_id, block_index, text}` |
| `tool.activity` | ya | `{message_id, tool (kanonik, mis. "web.search"), executor:"provider"\|"backend"\|"sandbox"\|"worker", phase:"started"\|"finished", summary (mis. query/url terpangkas, "5 hasil"), status?: "ok"\|"error"\|"denied"}` |
| `citation.added` | ya | `{message_id, citation: Citation}` |
| `asset.created` | ya | `{message_id, asset: AssetSummary}` |
| `usage.updated` | ya | `{message_id, input_tokens, output_tokens, web_search_requests, images, estimated_cost, cost_status, remaining:{capability: {unit: n}}}` |
| `message.completed` | ya | `{message_id, status:"completed"\|"failed"\|"cancelled"\|"blocked", grounding, citations_count, assets_count, error?: {code, message}}` |
| `error` | ya | `{message_id?, code (§12.0 / §1), message, retryable: bool}` |

Urutan dijamin per thread (seq monotonic). `message.completed` selalu menjadi event terakhir untuk satu pesan, termasuk saat gagal/dibatalkan.

### 12.3 Integrasi GitHub (`/integrations/github/*`, project & task)

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| GET | `/integrations/github/install-url` | integrations:github:manage | – | `{url, expires_at}` — `state` nonce sekali pakai terikat sesi (10 menit) |
| GET | `/integrations/github/callback` | auth (cookie sesi; navigasi dari GitHub) | query `installation_id`, `setup_action`, `state`, `code` | **302** ke `/settings/integrations/github?link=<id>` (bukan envelope). `state` salah/kedaluwarsa → 302 dengan `?error=state_invalid`. Verifikasi kepemilikan instalasi dilakukan asinkron (worker) |
| GET | `/integrations/github/link-requests/{lid}` | auth (pembuat) | – | `{id, status:"pending"\|"verified"\|"rejected"\|"expired", installation: Installation\|null, reason\|null}` |
| POST | `/integrations/github/webhook` | **public** (HMAC; tanpa sesi/CSRF) | body mentah ≤ `GITHUB_WEBHOOK_MAX_BYTES`; header `X-GitHub-Event`, `X-GitHub-Delivery`, `X-Hub-Signature-256` | 202 `{status:"accepted"}` · 200 `{status:"duplicate"\|"ignored"}` · 401 `WEBHOOK_SIGNATURE_INVALID` · 413 |
| GET | `/integrations/github/installations` | integrations:github:manage, atau user ber-grant | – | `[Installation]` |
| POST | `/integrations/github/installations/{iid}/sync` | integrations:github:manage | – | `{job_id}` (202) |
| DELETE | `/integrations/github/installations/{iid}/grant` | integrations:github:manage | – | `{uninstall_url}` — mencabut akses lokal; uninstall App dilakukan user di GitHub |
| GET | `/integrations/github/repos?installation_id=&q=&page=` | auth + grant instalasi | – | `[GithubRepo]` (dari cache DB, tanpa panggilan GitHub sinkron) |
| POST | `/projects` | auth + grant atas repo | `{name, description?, source:{type:"github_app", repo_id (uuid github_repositories.id), branch?}, default_branch?}` | `Project` (202) + `meta.job_id`; `repository.provider = "github_app"`, `remote_url` tanpa kredensial; 409 `CONFLICT` bila repo sudah diimpor user ini (`details.project_id`); 413 bila ukuran repo > kuota |
| GET | `/projects/{id}/github` | project:read | – | `{repository: GithubRepo, last_sync_at, open_pull_requests:[PullRequest]}` |
| POST | `/projects/{id}/git/pull` | workspace:write + git:read | `{}` | `{job_id}` (202) — hasil sebagai event `repository.synced {head}` atau `repository.sync_conflict {branch}` (tidak pernah menimpa perubahan lokal) |
| POST | `/tasks/{tid}/github/pr-draft` | task:control | `{base?}` | `{job_id}` (202) — E29; hasil event task `github.pr_draft_ready` |
| POST | `/tasks/{tid}/github/push-pr` | github:push | `{title (≤ 256), body (≤ 8000), draft?: bool (default true), base?}` | `{approval: Approval, preview:{repo_full_name, head_branch, head_commit, base_branch, files_changed, additions, deletions, workflow_paths_touched:false, secret_scan:{findings:0, scanned_commit}}}` (202) — 409 `TASK_NOT_READY`/`SECRET_SCAN_BLOCKED`/`GITHUB_NOT_LINKED`, 403 `POLICY_DENIED` (path `.github/workflows/**`, `.github/actions/**`) |
| GET | `/tasks/{tid}/github/pr` | task:read | – | `PullRequest` — 404 bila belum ada |

**Event task tambahan** (§5.1 `TaskEvent`): `github.pr_draft_ready {title, body}`, `github.push_started {approval_id}`, `github.pr_opened {number, url, head_commit}`, `github.pr_updated {state, checks_status}`, `github.push_failed {code, message}`; event project-level untuk impor/pull memakai `repository.sync_state` (sudah ada) + `repository.synced`/`repository.sync_conflict`.

| Shape | Field |
|---|---|
| `Installation` | `{id, installation_id, account_login, account_type:"User"\|"Organization", repository_selection:"all"\|"selected", permissions:{…}, suspended_at\|null, repos_count, granted_users:[{id, display_name}], created_at}` |
| `GithubRepo` | `{id, installation_id, repo_id, full_name, private, default_branch, archived, size_kb, language, topics, html_url, project_id\|null, synced_at}` |
| `PullRequest` | `{id, task_id, project_id, number, html_url, state:"open"\|"closed"\|"merged", draft, title, head_branch, head_commit, base_branch, checks_status:"unknown"\|"pending"\|"success"\|"failure"\|"neutral", approval_id, created_at, updated_at}` |
| `Project` (tambahan) | `repository.provider` dapat `github_app`; `repository.github: {repo_full_name, private, installation_id}\|null` |

### 12.4 Portfolio (`/portfolio/*`)

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| GET | `/portfolio/profile` | portfolio:edit | – | `PortfolioProfile` (dibuat kosong saat pertama diakses) |
| PUT | `/portfolio/profile` | portfolio:edit | `{slug, display_name, headline?, summary?, location?, contact?: {email?:{value, public}, phone?:{value, public}, links?:[{label, url, public}]}, locale?, theme?, seo?}` | `PortfolioProfile` |
| POST | `/portfolio/photo` | portfolio:edit | multipart `file` (png/jpeg/webp) | `{asset: AssetSummary}` (EXIF dibuang saat build situs) |
| GET | `/portfolio/items?type=&visibility=` | portfolio:edit | – | `[PortfolioItem]` |
| POST | `/portfolio/items` | portfolio:edit | `{type, title, organization?, start_date?, end_date?, data, visibility?: "draft"\|"public"}` | `PortfolioItem` (201, `source:"manual"`) |
| PATCH | `/portfolio/items/{iid}` | portfolio:edit | sebagian field POST | `PortfolioItem` — 409 `AI_DRAFT_NOT_APPROVED` bila `public` untuk item AI yang belum disetujui |
| DELETE | `/portfolio/items/{iid}` | portfolio:edit | – | `{}` |
| POST | `/portfolio/items/{iid}/approve-ai` | portfolio:edit | `{}` | `PortfolioItem` (`ai_approved_at`, audit) |
| POST | `/portfolio/items/reorder` | portfolio:edit | `{ids:[uuid]}` | `[PortfolioItem]` |
| POST | `/portfolio/import/github` | portfolio:edit + grant repo | `{repo_ids:[uuid], fields:["description"\|"readme_summary"\|"languages"\|"topics"\|"stars"\|"url"]}` | `{job_id, thread_id}` (202) — repo privat: hanya `fields` terpilih (default tanpa `readme_summary`); hasil = item `ai_draft` |
| POST | `/portfolio/import/project` | portfolio:edit + project:read | `{project_id}` | `{job_id, thread_id}` (202) |
| POST | `/portfolio/cv` | portfolio:edit | `{template:"ats_sans"\|…, format:"docx"\|"pdf", item_ids:[uuid], locale?: "id"\|"en"}` | `{job_id, thread_id}` (202) → `asset.created` — dirender engine lokal tanpa LLM; hanya item `public` atau yang dipilih & disetujui |
| POST | `/portfolio/job-fit` | portfolio:edit + assistant:use | `{job_url? \| job_text?}` | `{job_id, thread_id}` (202) — K50: saran & draft cover letter berlabel AI, kutipan lowongan bersitasi |
| GET | `/portfolio/versions` | portfolio:edit | – | `[PortfolioVersion]` |
| POST | `/portfolio/preview` | portfolio:edit | `{version_id?}` (default: snapshot baru) | `{job_id, version_id}` (202) |
| GET | `/portfolio/previews/{bundle_id}` | portfolio:edit (pemilik) | – | `{bundle_id, version_id, bundle_sha256, preview_url, privacy_report: PrivacyReport}` |
| GET | `/portfolio/preview/{bundle_id}/{path}` | portfolio:edit (pemilik) | – | file situs pratinjau (HTML/CSS/gambar) dengan `Content-Security-Policy: sandbox; default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'self'` — bukan envelope; dirender dalam `<iframe sandbox>` |
| POST | `/portfolio/publish` | portfolio:publish | `{bundle_id, target:"static"\|"github_pages", github_repository_id? (wajib untuk github_pages)}` | `{approval: Approval, publication: PortfolioPublication}` (202) — 409 `PRIVACY_CHECK_FAILED` (tanpa approval dibuat) |
| GET | `/portfolio/publications` | portfolio:edit | – | `[PortfolioPublication]` |
| POST | `/portfolio/publications/{pid}/unpublish` | portfolio:publish | `{}` | `PortfolioPublication` (`unpublished`; R1 — mengurangi eksposur, tanpa approval, diaudit) |

Situs publik dilayani Caddy di `PORTFOLIO_DOMAIN` (bukan API).

| Shape | Field |
|---|---|
| `PortfolioProfile` | `{id, slug, display_name, headline, summary, location, contact, photo_asset_id, locale, theme, seo, ai_fields:{field: {ai_generated, approved}}, updated_at}` |
| `PortfolioItem` | `{id, type:"experience"\|"education"\|"project"\|"skill"\|"certification"\|"publication"\|"link", title, organization, start_date, end_date, data, visibility:"draft"\|"public", source:"manual"\|"github"\|"kodekantor"\|"ai_draft", ai_generated, ai_approved_at\|null, source_ref, sort_order, created_at, updated_at}` |
| `PortfolioVersion` | `{id, version, snapshot_sha256, created_by, created_at, published: bool}` |
| `PrivacyReport` | `{ok, findings:[{path, kind, severity:"block"\|"warn", detail}], public_contact_fields, private_repo_items:[{full_name, fields}], sha256}` |
| `PortfolioPublication` | `{id, version_id, target, url, status:"pending_approval"\|"publishing"\|"published"\|"failed"\|"rejected"\|"superseded"\|"unpublished", bundle_sha256, approval_id, privacy_report_sha256, published_at, error, created_at}` |

### 12.5 Tambahan pada `Approval`

`Approval` (§8) menambah `scope_type: "project"|"portfolio"|"integration"` dan `owner_id`; `project_id` dapat `null` untuk `scope_type ≠ "project"`. `GET /approvals` menyertakan approval portfolio milik user. `POST /approvals/{id}/decision` tidak berubah (wajib `payload_hash`, CSRF).

### 12.6 Matriks approval v2 (melanjutkan §11.1, ADR-034)

| action_type | Dibuat oleh (server-side) | Payload | Handler | Kedaluwarsa | Izin memutuskan | Self-approval |
|---|---|---|---|---|---|---|
| `github_push_pr` | integrations service (aksi user `POST /tasks/{id}/github/push-pr`); juga ToolGateway bila `github.push_branch`/`github.open_pr` dipanggil tanpa grant | `{task_id, project_id, github_repository_id, repo_full_name, installation_id, base_branch, head_branch, base_commit, head_commit, diff_sha256, files_changed, workflow_paths_touched:false, secret_scan:{findings, scanned_commit}, pr:{title, body_sha256, draft}}` | integrations: enqueue job → `consume()` → `ApprovalGrant` → verifikasi head + path policy + secret scan ulang → push branch tanpa force → buka PR → `github_pull_requests`; head berubah → gagal `HEAD_MOVED` (approval `superseded`). Reject → tidak ada aksi; **status task tidak berubah** | 24 jam | `approval:decide` + `github:push` (project) | boleh (pilot single-owner); dilarang bila policy `four_eyes` aktif |
| `portfolio_publish` | portfolio service (aksi user `POST /portfolio/publish`) setelah `PrivacyReport.ok` | `{profile_id, version_id, snapshot_sha256, bundle_id, bundle_sha256, target, url, github_repository_id\|null, privacy_report_sha256, public_contact_fields, private_repo_items, ai_unapproved_count:0}` | integrations: `consume()` → `ApprovalGrant` → K51 `portfolio.publish` (verifikasi sha bundle → `static`: rilis + swap symlink atomik; `github_pages`: push `gh-pages` tanpa force) → publication `published`. Reject → publication `rejected` | 24 jam | `portfolio:publish` + pemilik profil (`owner_id`) | boleh — hanya pemilik yang dapat memutuskan publikasi portofolionya |

Catatan: `git_push` (P2, ADR-003) tetap dicadangkan untuk remote generik non-GitHub dan tidak dipakai jalur GitHub App. Kedua action type v2 dieksekusi lewat `ApprovalGrant` (desain v2 §5.15.3): approval dikonsumsi **sekali** oleh job server, lalu langkah-langkah yang field-nya terikat payload dijalankan dalam ≤ 15 menit; LLM tidak dapat membuat atau memperpanjang grant.

## 13. Kontrak v2.1 — SDLC, Developer Workspace v2, tier M/L (Rancangan)

> **Status: rancangan / Proposed, belum diimplementasikan.** Sumber mengikat: `docs/SDLC_SCALE_DESIGN.md` §6 **KONTRAK v2.1** (K11 API, K10 tool, K12 ancaman); desain rinci `docs/IMPLEMENTATION_DESIGN_V2.md` §13. Ditambahkan oleh agen dokumentasi (CLAUDE.md: perubahan `API_CONTRACTS.md` dicatat eksplisit di laporan agent; menunggu review Lead Architect). Semua aturan §1 (envelope, error, `X-Request-ID`, `Idempotency-Key`, pagination) dan §2 (sesi, CSRF, RBAC, 404 untuk resource di luar scope — **termasuk di luar organisasi**) tetap berlaku, kecuali: WebSocket terminal (§13.6), API internal node (§13.7), dan SCIM (§13.8). Endpoint bertanda *(turunan)* melengkapi daftar K11 agar FR dapat diuji. Angka batas bertanda [Menebak].

### 13.0 Kode error tambahan

| HTTP | code | Kapan | `details` |
|---|---|---|---|
| 401 | `NODE_AUTH_FAILED` | token node salah/kedaluwarsa/dicabut (API internal §13.7) | – |
| 401 | `SSO_ASSERTION_INVALID` | assertion/id_token gagal verifikasi (tanda tangan, audience, nonce, `InResponseTo`) — halaman login menampilkan pesan generik | – |
| 403 | `TERMINAL_DISABLED` | terminal nonaktif (`TERMINAL_ENABLED=false`, belum diaktifkan admin untuk project, atau tier tidak mengizinkan) | `{scope: "global"\|"project"\|"tier"}` |
| 403 | `SSO_REQUIRED` | login lokal dimatikan untuk organisasi akun (kecuali owner *break-glass*) | `{sso_start_url}` |
| 409 | `TAKEOVER_ACTIVE` | task sedang di-take-over: resume/klaim worker/tulisan agent, atau take-over kedua oleh user lain | `{task_id, by_user: {id, display_name}, since}` |
| 409 | `EVIDENCE_STALE` | apply/gate diminta dengan bukti test/User Proxy dari sebelum take-over terakhir | `{task_id, stale_before, missing: ["test"\|"user_proxy"]}` |
| 409 | `MERGE_QUEUE_FAILED` | entri merge queue gagal; dipakai sebagai `error.code` entri/event dan bila aksi sinkron bergantung pada entri gagal | `{entry_id, stage: "preparing"\|"testing"\|"waiting_ci"\|"merging", failure_code, evidence_artifact_id}` |
| 409 | `PLAN_STALE` | `plan_sha256` tidak cocok atau plan bukan berstatus `proposed` | `{plan_id, current_sha256, status}` |
| 409 | `WORK_ITEM_HAS_ACTIVE_TASKS` | menutup/menghapus work item yang masih punya task aktif | `{task_ids}` |
| 409 | `ORG_SCOPE` | permintaan menggabungkan resource dari organisasi berbeda yang **sama-sama terlihat** user, atau header `X-KK-Org` ≠ organisasi aktif sesi (resource yang tidak terlihat tetap 404) | `{expected_org_id, actual_org_id}` |
| 409 | `LEASE_LOST` | epoch lease node basi (API internal §13.7) | `{lease_id, current_epoch}` |
| 422 | `PLAN_INVALID` | `ProgramPlan` gagal validasi deterministik | `{errors: [{path, code: "DAG_CYCLE"\|"DUPLICATE_KEY"\|"UNKNOWN_REF"\|"UNKNOWN_REPO"\|"LIMIT", detail}]}` |
| 503 | `NODE_UNAVAILABLE` | permintaan sinkron butuh node executor tertentu/aktif yang tidak tersedia (mis. terminal/run pada workspace di node offline). Task asinkron tidak memakai kode ini — task tetap antre dengan ETA | `{node_id\|null, retry_after_s}` |

Kode lama yang dipakai ulang: `VERSION_CONFLICT` (409) kini menyertakan `details: {path, base_sha256, server_sha256, merge3: Merge3View}`; `POLICY_DENIED` (403) untuk menulis default branch/target `repo` saat merge queue aktif; `RATE_LIMITED` (429) untuk batas sesi terminal (`details.scope = "terminal_sessions"`); `QUOTA_EXCEEDED` (429) untuk anggaran (`details.scope = "budget"`); `INVALID_TRANSITION` (409) untuk take-over dari status yang tidak diizinkan.

### 13.1 Work item & SDLC (M25)

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| GET | `/projects/{id}/work-items?type=&parent_id=&status=&q=` | project:read | – | `[WorkItemSummary]` |
| POST | `/projects/{id}/work-items` | workitem:write | `{type: "program"\|"epic"\|"story", title, parent_id?, description?, owner_id?, priority?, mandatory?, repo_aliases?}` | `WorkItem` (201) — 422 bila parent tidak sah |
| GET | `/work-items/{wid}` | project:read | – | `WorkItem` (+ `children:[WorkItemSummary]`, `tasks:[TaskSummary]`) |
| PATCH | `/work-items/{wid}` | workitem:write | `{title?, description?, owner_id?, priority?, mandatory?, parent_id?, repo_aliases?, version}` | `WorkItem` — `status` tidak dapat di-PATCH (422) |
| POST | `/work-items/{wid}/close` | workitem:write | `{reason}` | `WorkItem` (`closed`, audit) — 409 `WORK_ITEM_HAS_ACTIVE_TASKS` |
| DELETE | `/work-items/{wid}` | workitem:write | – | `{}` — hanya `draft` tanpa child/task; lainnya 409 (pakai close) |
| GET | `/work-items/{wid}/requirements` | project:read | – | `{baseline_version, requirements:[Requirement + criteria:[Criterion]]}` (story) |
| PUT | `/work-items/{wid}/requirements` | workitem:write | `{requirements:[…]}` | baseline baru (versi +1, lama `superseded`) |
| POST | `/work-items/{wid}/requirements/draft` *(turunan)* | workitem:write | `{instructions?}` | `{job_id}` (202) — A02 menyusun draf baseline story |
| POST | `/work-items/{id}/plan` | workitem:plan | `{instructions?, depth?: "story"\|"task"}` | `{job_id}` (202) — A03; hasil event `work_item.plan_ready` |
| GET | `/work-items/{wid}/plans` *(turunan)* | project:read | – | `[PlanProposalSummary]` |
| GET | `/work-items/{wid}/plans/{pid}` *(turunan)* | project:read | – | `PlanProposal` |
| POST | `/work-items/{id}/plan/accept` | workitem:plan | `{plan_id, plan_sha256, selection?: [key]}` | `{created_work_items:[WorkItemSummary], created_tasks:[TaskSummary]}` (201) — aksi user biasa, **bukan** approval R3; 409 `PLAN_STALE`, 422 `PLAN_INVALID` |
| POST | `/work-items/{wid}/plans/{pid}/reject` *(turunan)* | workitem:plan | `{reason?}` | `PlanProposalSummary` |
| GET | `/work-items/{wid}/board` | project:read | – | `SdlcBoard` (8 fase + bukti) |
| POST | `/work-items/{wid}/board/phases/{phase}/not-applicable` *(turunan)* | workitem:write | `{reason}` | `SdlcBoard` — hanya `design`/`run`/`operate`; audit |
| POST | `/work-items/{wid}/links` · DELETE `/work-items/{wid}/links/{lid}` | workitem:write | `{link_type, target_type, target_ref}` | `WorkItemLink` / `{}` |
| POST | `/work-items/{wid}/scheduling` *(turunan)* | workitem:plan | `{enabled: bool}` | `WorkItem` — aktif/jeda penjadwal DAG subtree |
| GET | `/work-items/{wid}/traceability?format=json\|csv\|pdf` | project:read | – | `json` → `TraceabilityExport`; `csv` → file attachment; `pdf` → `{job_id}` (202) → `asset.created` |
| GET | `/projects/{id}/program-dashboard?program_id=` | project:read | – | `ProgramDashboard` |
| GET · PUT | `/work-items/{wid}/budget` | project:read · workitem:plan | `{limit_usd, period: "monthly"\|"total", alert_thresholds?, action_on_exceed: "alert"\|"block_new_tasks"\|"kill"}` | `CostBudget` (tier M+, 0008) |
| POST | `/work-items/from-incident` (P2) | workitem:write | `{project_id, title, source: {type: "log"\|"alert"\|"run"\|"ci", ref}}` | `WorkItem` (story + tautan bukti); `ci` = CI pasca-merge gagal, juga dibuat otomatis oleh merge queue (K13 OI-23) |

| Shape | Field |
|---|---|
| `WorkItemSummary` | `{id, project_id, organization_id\|null, type, key, title, status: "draft"\|"ready"\|"in_progress"\|"blocked"\|"in_review"\|"accepted"\|"closed", status_reason, priority, mandatory, parent_id, owner: {id, display_name}\|null, counts: {children, tasks, tasks_completed, tasks_blocked}, updated_at}` |
| `WorkItem` | `WorkItemSummary + {description, repo_aliases, version, closed_reason\|null, created_by, created_at}` |
| `PlanProposalSummary` | `{id, work_item_id, status: "proposed"\|"accepted"\|"rejected"\|"superseded", plan_sha256, valid, totals: {epics, stories, tasks, est_cost_usd\|null, cost_status}, created_at}` |
| `PlanProposal` | `PlanProposalSummary + {plan: ProgramPlan (IMPLEMENTATION_DESIGN_V2 §13.3.1), diff: {added:[…], changed:[…], untouched:[…]}, validation: {valid, errors}, agent_run_id}` |
| `SdlcBoard` | `{work_item_id, status, phases: [{phase, state: "done"\|"pending"\|"not_applicable", evidence: [EvidenceRef], reason\|null}]}` |
| `TraceabilityExport` | `{scope: {work_item_id\|null, release_id\|null}, rows: [{requirement_key, criterion_key, story_key, task_id, commit, test_run_ids, acceptance_report_id, merge_queue_entry_id, release_version\|null}], generated_at, source: "records"}` |
| `ProgramDashboard` | `{program: WorkItemSummary, epics: [{id, title, status, accepted, total}], blocked_tasks: [TaskSummary], failed_tasks: [TaskSummary], lead_time_hours: {p50, p90}, queue: {tasks_waiting, merge_queue_waiting, eta_minutes\|null}, cost: {estimated_cost, cost_status, budget: {limit_usd, used_usd}\|null}}` |
| `WorkItemLink` | `{id, work_item_id, link_type, target_type, target_ref, sdlc_phase\|null, created_by, created_at}` |
| `CostBudget` | `{id, scope_type: "organization"\|"program"\|"epic"\|"project", scope_id, period, limit_usd, used_usd, cost_status, alert_thresholds, action_on_exceed, status: "ok"\|"alerted"\|"blocked", updated_at}` |

### 13.2 Merge queue, CI, release, environment (M25)

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| GET | `/merge-queue/{repo_id}?status=` | project:read (project pemilik repo) | – | `{repository_id, paused, policy: MergeQueuePolicy, entries: [MergeQueueEntry]}` |
| GET | `/merge-queue/{repo_id}/entries/{eid}` | project:read | – | `MergeQueueEntry + {evidence: {test_runs: [TestRun], ci_checks: [CiCheckResult], conflict_files: [path]}}` |
| POST | `/merge-queue/{repo_id}/entries` *(turunan)* | workspace:write + git:branch | `{source: {type: "user_branch", branch}}` | `MergeQueueEntry` (202). Entri **task** dibuat otomatis oleh handler `apply_changes` setelah `consume()` (tidak lewat endpoint ini). 403 `POLICY_DENIED` bila mode branch user tidak aktif |
| POST | `/merge-queue/{repo_id}/entries/{eid}/cancel` | merge_queue:manage (atau pembuat entri `user_branch`) | `{reason}` | `MergeQueueEntry` (`cancelled`) |
| POST | `/merge-queue/{repo_id}/entries/{eid}/retry` | merge_queue:manage | – | `MergeQueueEntry` — hanya `INFRA_ERROR`/`CI_TIMEOUT` |
| POST | `/merge-queue/{repo_id}/pause` · `/resume` | merge_queue:manage | `{reason?}` | `{paused}` (audit) |
| PUT | `/merge-queue/{repo_id}/policy` | merge_queue:manage | `{enabled, required_profiles: [profile_id]\|"auto", require_ci, ci_scope: "task_head", ci_timeout_minutes, watch_post_merge_ci, merge_method: "rebase"\|"merge", codeowners_enforced}` | `MergeQueuePolicy` (audit). Aturan CI (K13 OI-23): `require_ci` hanya berlaku bila `approved_head` sudah di-push (UC-18) **dan** rebase fast-forward (commit tidak berubah); selain itu `waiting_ci` dilewati dan entri memakai profil lokal wajib saja (`ci_skip_reason`); `ci_timeout_minutes` selalu berlaku. `require_ci`/`watch_post_merge_ci` hanya efektif setelah WP-13.12 (G9) |
| GET | `/tasks/{tid}/ci-checks` | task:read | – | `[CiCheckResult]` |
| GET | `/projects/{id}/releases` | project:read | – | `[ReleaseSummary]` |
| POST | `/projects/{id}/releases` | release:manage | `{version, name?, target_environment_id?}` | `Release` (201) |
| GET · PATCH | `/releases/{rid}` | project:read · release:manage | `{name?, version?}` (hanya `draft`) | `Release` |
| POST | `/releases/{rid}/items` · DELETE `/releases/{rid}/items/{iid}` | release:manage | `{work_item_id}` (story `accepted`) | `Release` — 409 bila story belum `accepted` |
| POST | `/releases/{rid}/freeze` | release:manage | – | `Release` (`frozen`; changelog deterministik + versi commit per repo terikat) |
| POST | `/releases/{rid}/notes` | release:manage | – | `Task` (G35, release notes sebagai patch yang direview) |
| POST | `/releases/{rid}/promote` | release:manage (+ `deploy:production` untuk production) | `{environment_id}` | `{deployment: Deployment, approval: Approval\|null}` (202) — staging → `deploy_staging`, production → `deploy_production` (R3, FR-141) |
| GET | `/projects/{id}/environments` | project:read | – | `[Environment]` |
| POST | `/projects/{id}/environments` | release:manage | `{name, kind: "dev"\|"staging"\|"production", order, url?, requires_approval?}` | `Environment` (201) |
| PATCH · DELETE | `/environments/{eid}` | release:manage | sebagian field POST | `Environment` / `{}` |

| Shape | Field |
|---|---|
| `MergeQueueEntry` | `{id, repository_id, project_id, source_type: "task"\|"user_branch", task_id\|null, work_item_id\|null, source_branch, target_branch, position, status: "queued"\|"preparing"\|"testing"\|"waiting_ci"\|"merging"\|"merged"\|"failed"\|"needs_reapproval"\|"cancelled", approval_id\|null, approved_head, approved_patch_id, candidate_patch_id\|null, base_head\|null, result_commit\|null, failure_code\|null, error: {code: "MERGE_QUEUE_FAILED", message}\|null, ci_required, ci_skip_reason: "not_pushed"\|"not_fast_forward"\|"ci_not_required"\|null, post_merge_ci: {status, conclusion\|null, repair_work_item_id\|null}\|null, enqueued_by, enqueued_at, started_at, finished_at}` (field CI: K13 OI-23) |
| `MergeQueuePolicy` | `{repository_id, enabled, required_profiles, require_ci, ci_scope, ci_timeout_minutes, watch_post_merge_ci, merge_method, codeowners_enforced, paused, updated_by, updated_at}` |
| `CiCheckResult` | `{id, repository_id, commit_sha, check_name, app_slug, status: "queued"\|"in_progress"\|"completed", conclusion\|null, details_url, started_at, completed_at, source: "webhook"\|"poll", fetched_at}` |
| `ReleaseSummary` | `{id, project_id, version, name, status: "draft"\|"frozen"\|"released"\|"cancelled", items_count, released_at}` |
| `Release` | `ReleaseSummary + {items: [{id, item_type: "story"\|"repo_version", work_item_id\|null, repository_id\|null, commit\|null}], changelog: [{story_key, task_id, commit, title}], notes_artifact_id\|null, target_environment_id\|null, created_by, created_at}` |
| `Environment` | `{id, project_id, name, kind, order, url\|null, requires_approval, current_release_id\|null, updated_at}` |

`apply_changes` (§11 #6) menambah field payload: `{merge_queue: bool, approved_patch_id, evidence: {test_run_ids, acceptance_report_id, takeover_epoch}}`. Diff berubah karena rebase → approval baru (lama `superseded`). **Tidak ada `action_type` baru** di v2.1.

**Semantik approval `apply_changes` tidak berubah (SSD §3.2 #4, ADR-034, CLAUDE.md #5):** tetap hanya diputuskan user lewat `POST /approvals/{id}/decision`, terikat `payload_hash`, sekali pakai (`consume()` atomik), dengan kedaluwarsa dan status `superseded` seperti §11.1. **Merge queue hanya mengeksekusi entri yang sudah disetujui:**

1. Entri `source_type = task` wajib membawa `approval_id` yang sudah `approved` dan di-consume (CHECK DB `DATA_MODEL.md` V2.9 + prasyarat `merge_queue.enqueue`); entri dibuat oleh handler approval, bukan oleh agent atau endpoint publik.
2. Head target yang bergerak selama antrean **tidak** membatalkan approval selama diff task identik (`candidate_patch_id == approved_patch_id`, `git patch-id --stable`); bila diff berubah karena rebase/konflik → entri `needs_reapproval`, merge queue hanya **meminta** approval `apply_changes` baru (status `pending`) yang tetap diputuskan user — merge queue tidak pernah menyetujui untuk dirinya sendiri.
3. Entri yang gagal (konflik, profil wajib, CI bila berlaku) keluar antrean dengan bukti; task kembali `needs_changes`; branch target tidak berubah. CI pasca-merge yang gagal di default branch **tidak** membatalkan merge: sistem membuat story perbaikan otomatis (jalur FR-234, `POST /work-items/from-incident` dengan `source.type = "ci"` *(turunan)*) dan mengirim event `merge_queue.post_merge_ci_failed` (K13 OI-23).
4. Entri `source_type = user_branch` *(turunan; OI-20/OQ-02)* bukan jalur agent: dibuat langsung oleh manusia pemilik perubahan (`workspace:write` + `git:branch`) sebagai keputusan user teraudit (`merge_queue.enqueued` dengan `approved_patch_id` saat enqueue), tidak dapat dibuat agent/tool, dan tetap melewati profil wajib (CI tidak berlaku kecuali head ter-push & fast-forward, K13 OI-23). Apakah entri ini butuh persetujuan orang kedua pada mode tim (`four_eyes`) diputuskan PO bersama OQ-02.

### 13.3 Developer Workspace v2 (M26)

`target` = `"repo"` (checkout `<project>/repo`; **read-only** bila merge queue repo aktif) · `"user"` (worktree `kk/user-<user8>`) · `"task:<id>"` (worktree task, hanya pemegang take-over aktif).

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| POST | `/projects/{id}/files` | workspace:write | `{path, type: "file"\|"dir", content?, target, editor_session}` | `FileVersion` (201) — audit `workspace.file_created`; 409 bila path sudah ada |
| PATCH | `/projects/{id}/files` | workspace:write | `{op: "rename", from_path, to_path, expected_sha256?, target, editor_session}` | `FileVersion` — audit `workspace.file_renamed`; 409 `VERSION_CONFLICT` |
| DELETE | `/projects/{id}/files?path=&expected_sha256=&recursive=&target=&editor_session=` | workspace:write | – | `{}` — audit `workspace.file_deleted`; file wajib `expected_sha256` (folder: `recursive=true`). `DELETE /projects/{id}/files/{path}` (v1) tetap sebagai alias |
| PUT | `/projects/{id}/files/{path}` (v1, diperluas) | workspace:write | `{content, expected_sha256, target?, editor_session?}` | `FileVersion` — 409 `VERSION_CONFLICT` + `details.merge3` |
| GET | `/projects/{id}/files/{path}?ref=&target=` (v1, diperluas) | workspace:read | – | v1 + `target` |
| POST | `/projects/{id}/files/merge3` *(turunan)* | workspace:write | `{path, target, base_sha256, mine}` | `Merge3View` |
| GET | `/projects/{id}/workspace-ops?editor_session=` *(turunan)* | workspace:read | – | `[WorkspaceOp]` (jurnal undo sesi) |
| POST | `/projects/{id}/workspace-ops/undo` *(turunan)* | workspace:write | `{editor_session, op_id}` | `FileVersion\|null` — 409 bila file berubah sejak operasi |
| POST | `/projects/{id}/format` *(turunan)* | workspace:write | `{path, target, content_sha256}` | `{job_id}` (202) → event `workspace.format_patch_ready`; patch diterapkan hanya bila user menyimpannya |
| POST | `/tasks/{id}/takeover` | task:control + workspace:write | – | `{takeover: TakeoverState, task: Task}` — 202 bila `running` (menunggu checkpoint/batas langkah), 200 bila langsung dari `waiting_approval`/`needs_changes`/`failed`; status lain termasuk `validating` dan `interrupted` → 409 `INVALID_TRANSITION` (K13 OI-22, ADR-058); 409 `TAKEOVER_ACTIVE` |
| GET | `/tasks/{id}/takeover` *(turunan)* | task:read | – | `TakeoverState\|null` |
| POST | `/tasks/{id}/takeover/release` *(turunan)* | task:control (pemegang take-over) | `{commit_message?, resume_mode: "revalidate"\|"stay_interrupted"}` | `{takeover, task, commit\|null, evidence_stale: bool}` — `revalidate` ("Selesai take-over") → task `queued` dengan rencana validasi saja: test → review → User Proxy, **tanpa** langkah implementasi agent (K13 OI-22); `stay_interrupted` → perubahan dicommit, take-over **tetap dipegang** (task tetap `interrupted/user_takeover`) sampai pemegang memilih `revalidate` atau membatalkan task |
| POST | `/projects/{id}/runs` | test:run | `{profile_id (run.*), target, args?}` | `TestRun` (202; `suite: "run"`, `initiated_by: "user"`, `preview_url?`) — 503 `NODE_UNAVAILABLE` (tier M) |
| GET | `/projects/{id}/runs?target=` · POST `/runs/{rid}/stop` · GET `/runs/{rid}/stream` | task:read · test:run · task:read | – | `[TestRun]` · `TestRun` · SSE `test.output`/`test.run_finished` (frame §11 #7) |
| POST | `/projects/{id}/test-runs` (v1, diperluas) | test:run | `{profile_id?, target, selector?: {scope: "all"\|"file"\|"test", path?, test_id?}, args?}` | `TestRun` (202) — server mengisi `initiated_by: "user"`; profil `*.test_select` dipilih dari selector |
| POST | `/projects/{id}/git/stage` · `/git/unstage` *(turunan)* | workspace:write | `{paths, target}` | `GitStatus` |
| POST | `/projects/{id}/git/discard` *(turunan)* | workspace:write | `{paths, expected_sha256s, target}` | `GitStatus` (audit) |
| POST | `/projects/{id}/git/checkout` *(turunan)* | workspace:write + git:branch | `{branch, target: "user"}` | `GitStatus` — hanya branch `kk/user-*` milik user |
| POST | `/projects/{id}/git/commit` (v1, diperluas) | workspace:write | `{message, paths?, target}` | `{commit}` — 403 `POLICY_DENIED` untuk default branch saat merge queue aktif; push hanya lewat `POST /tasks/{id}/github/push-pr` (R3) |

| Shape | Field |
|---|---|
| `FileVersion` | `{path, sha256, size, target, op_id\|null}` |
| `Merge3View` | `{path, base\|null, server, mine, auto_merged\|null, conflicts: [[start, end]]}` |
| `WorkspaceOp` | `{op_id, kind: "write"\|"create"\|"rename"\|"delete", path, from_path\|null, before_sha256\|null, after_sha256\|null, at, undone}` |
| `TakeoverState` | `{task_id, active, pending, by_user: {id, display_name}, epoch, started_at, base_head, released_at\|null, user_commits: [commit]}` |
| `TestRun` (tambahan) | `{initiated_by: "agent"\|"user"\|"system", initiated_by_user_id\|null, selector\|null, target, preview_url\|null}`; `suite` dapat bernilai `run` |
| `GitStatus` | sama dengan `GET /projects/{id}/git/status` (§4) + `target` |

### 13.4 Terminal — REST (M26, ADR-059)

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| POST | `/projects/{id}/terminal-sessions` | terminal:use | `{target, cols (20–400), rows (5–200)}` | `{session: TerminalSession, ws_url, ticket_expires_at}` (201) — 403 `TERMINAL_DISABLED`; 429 `RATE_LIMITED`; 503 `NODE_UNAVAILABLE` |
| GET | `/projects/{id}/terminal-sessions?status=` | terminal:use (milik sendiri) / owner-admin project (semua) | – | `[TerminalSession]` |
| POST | `/terminal-sessions/{sid}/ticket` *(turunan)* | terminal:use (pemilik) | – | `{ws_url, ticket_expires_at}` — reconnect selama sesi `active` |
| DELETE | `/terminal-sessions/{sid}` | terminal:use (pemilik) | – | `TerminalSession` (`closed`, alasan `user`) |
| GET | `/terminal-sessions/{sid}/transcript` *(turunan)* | pemilik sesi / owner-admin project | – | `text/plain` attachment teredaksi (`nosniff`) |
| POST | `/admin/terminal-sessions/{sid}/kill` *(turunan)* | admin:policies | `{reason}` | `TerminalSession` (`killed`) |
| PATCH | `/projects/{id}` `{settings: {terminal: {enabled}}}` (v1, diperluas) | owner/admin project | – | `Project` — audit `terminal.enabled_changed`; tidak berefek bila `TERMINAL_ENABLED=false` |

`TerminalSession = {id, project_id, user: {id, display_name}, target, status: "pending"|"active"|"closed"|"expired"|"killed"|"failed", close_reason: "user"|"idle_timeout"|"max_duration"|"admin_kill"|"policy"|"node_lost"|"error"|null, started_at, last_activity_at, ended_at, max_duration_s, idle_timeout_s, input_bytes, output_bytes, transcript_artifact_id|null}`

### 13.5 SSE event v2.1

**Event task tambahan** (`TaskEvent`, §5.1, persisten di `task_events`): `task.takeover_started {by_user, epoch}`, `task.takeover_released {epoch, commits, evidence_stale}`, `task.evidence_stale {stale_before}`, serta cermin event merge queue untuk task terkait (`merge_queue.entry_enqueued`, `merge_queue.entry_stage`, `merge_queue.entry_merged`, `merge_queue.entry_failed`, `merge_queue.entry_needs_reapproval`, `merge_queue.post_merge_ci_failed`).

**Stream project** `GET /projects/{id}/events?types=` (SSE, `project:read`): channel Redis `kk:project:{id}`; **ephemeral** — K11 tidak menambah tabel event project, sehingga tidak ada `Last-Event-ID`/replay; klien me-refetch resource terkait saat reconnect; heartbeat `: ping` tiap 15 dtk. Frame: `event: <type>`, `data: {"project_id", "type", "ts", "payload"}`.

| type | payload |
|---|---|
| `work_item.created` / `work_item.updated` | `{work_item: WorkItemSummary}` |
| `work_item.status_changed` | `{work_item_id, from, to, reason}` (hasil roll-up deterministik) |
| `work_item.plan_ready` | `{work_item_id, plan_id, plan_sha256, valid}` |
| `work_item.plan_accepted` | `{work_item_id, plan_id, created_task_ids}` |
| `work_item.task_scheduled` | `{work_item_id, task_id}` |
| `budget.alert` / `budget.blocked` | `{scope_type, scope_id, threshold, used_usd, limit_usd, cost_status}` |
| `merge_queue.entry_enqueued` | `{entry_id, repository_id, task_id\|null, position}` |
| `merge_queue.entry_stage` | `{entry_id, stage: "preparing"\|"testing"\|"waiting_ci"\|"merging", status}` |
| `merge_queue.entry_merged` | `{entry_id, result_commit}` |
| `merge_queue.entry_failed` | `{entry_id, error: {code: "MERGE_QUEUE_FAILED", failure_code, stage}, evidence_artifact_id}` |
| `merge_queue.entry_needs_reapproval` | `{entry_id, approval_id}` |
| `merge_queue.paused` / `merge_queue.resumed` | `{repository_id, by}` |
| `merge_queue.ci_skipped` | `{entry_id, reason: "not_pushed"\|"not_fast_forward"}` — CI tidak berlaku, entri memakai profil lokal wajib (K13 OI-23) |
| `merge_queue.post_merge_ci_failed` | `{repository_id, entry_id, result_commit, failed_checks: [check_name], repair_work_item_id}` — CI pasca-merge gagal di default branch; story perbaikan dibuat otomatis (jalur FR-234, K13 OI-23) |
| `workspace.format_patch_ready` | `{path, target, job_id, patch}` |

### 13.6 Protokol WebSocket terminal (satu-satunya WebSocket — pengecualian terbatas ADR-014 via ADR-059)

- **URL:** `wss://{APP_DOMAIN}/api/v1/terminal-sessions/{sid}/ws?ticket=<t>` dari §13.4. Tiket: 32 byte acak (base64url), **sekali pakai** (GETDEL Redis, disimpan sebagai sha256), TTL 30 dtk, terikat `(session_id, user_id, sha256(token sesi))`. Log akses Caddy/api wajib meredaksi parameter `ticket`.
- **Handshake wajib:** cookie `kk_session` valid; header `Origin` == origin `APP_URL` (anti *cross-site WebSocket hijacking*); tiket valid; subprotocol `kk.terminal.v1` (`Sec-WebSocket-Protocol`). Gagal sebelum upgrade → HTTP 403/404 tanpa upgrade; gagal sesudah → close code di bawah. Header CSRF tidak dipakai (tidak tersedia pada handshake WS) — digantikan tiket dari `POST` ber-CSRF.
- **Frame:** JSON text, satu pesan per frame, UTF-8. Output **tidak** di-replay saat reconnect (transcript teredaksi tersedia lewat REST setelah sesi ditutup).

| Arah | `type` | Field | Batas |
|---|---|---|---|
| klien → server | `input` | `{data}` | ≤ 64 KB per frame; laju ≤ 4 KB/dtk, burst 16 KB [Menebak] |
| klien → server | `resize` | `{cols, rows}` | cols 20–400, rows 5–200; ≤ 10 per detik |
| klien → server | `ping` | `{ts}` | – |
| klien → server | `close` | `{}` | – |
| server → klien | `ready` | `{session_id, cols, rows, idle_timeout_s, max_duration_s, expires_at}` | pesan pertama |
| server → klien | `output` | `{seq, data}` | ≤ 64 KB per frame; stdout+stderr PTY |
| server → klien | `notice` | `{kind: "idle_warning"\|"duration_warning", seconds_left}` | 60 dtk sebelum batas |
| server → klien | `exit` | `{code}` | proses shell selesai, lalu close 1000 |
| server → klien | `error` | `{code, message}` | sebelum close non-normal |
| server → klien | `pong` | `{ts}` | – |

Batas sesi: durasi `TERMINAL_SESSION_MAX_MINUTES` (default 30), idle `TERMINAL_IDLE_MINUTES` (default 10, dihitung dari input user terakhir), 1 sesi aktif per user per project dan 10 sesi baru per jam per user [Menebak], transcript ≤ 5 MB [Menebak]. Network container terminal selalu `none`.

| Close code | Arti |
|---|---|
| 1000 | normal (user menutup / shell selesai) |
| 1001 | server berhenti / node di-drain |
| 1008 | `Origin` atau subprotocol tidak valid |
| 1011 | error internal |
| 4401 | tiket tidak valid, kedaluwarsa, atau sudah dipakai |
| 4403 | terminal dinonaktifkan atau izin dicabut (termasuk deprovision SCIM) |
| 4404 | sesi tidak ditemukan / di luar scope |
| 4408 | idle timeout |
| 4409 | durasi maksimum tercapai |
| 4413 | frame terlalu besar |
| 4429 | laju input terlampaui |
| 4499 | dihentikan admin |
| 4503 | sandbox/node tidak tersedia |

### 13.7 Admin node & API internal node (M27)

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| GET | `/admin/nodes` | admin:nodes | – | `[ExecutorNode]` |
| POST | `/admin/nodes` | admin:nodes | `{name, labels?, capacity_slots, organization_id?}` | `{node: ExecutorNode, bootstrap_token, bootstrap_expires_at}` (201) — token tampil **sekali** |
| GET | `/admin/nodes/{nid}` | admin:nodes | – | `ExecutorNode + {leases: [NodeLease]}` |
| POST | `/admin/nodes/{nid}/drain` · `/undrain` · `/revoke` · `/rotate-token` | admin:nodes | `{reason?}` | `ExecutorNode` (audit; revoke → lease dicabut, task failover) |
| DELETE | `/admin/nodes/{nid}` | admin:nodes | – | `{}` — hanya `offline`/`revoked` tanpa lease aktif |
| GET | `/capacity` | auth (detail node hanya admin:nodes) | – | `Capacity` (FR-258) |

**API internal node** — listener internal (tidak lewat situs publik Caddy), `Authorization: Bearer <token node>` + mTLS [Kemungkinan Besar], envelope §1:

| Method | Path | Request | Response `data` |
|---|---|---|---|
| POST | `/internal/nodes/register` | `{bootstrap_token, info: {hostname, version, sandbox_runtime, labels}}` | `{node_id, node_token, expires_at}` |
| POST | `/internal/nodes/{nid}/heartbeat` | `{slots_free, leases: [{lease_id, epoch}], runtime, image_digests}` | `{ack: true, revoke_leases: [lease_id], drain: bool}` |
| POST | `/internal/nodes/{nid}/token/rotate` | – | `{node_token, expires_at, previous_valid_until}` |
| POST | `/internal/leases/{lid}/renew` | `{epoch}` | `{expires_at}` — 409 `LEASE_LOST` |
| POST | `/internal/leases/{lid}/release` | `{epoch, reason}` | `{}` |
| POST | `/internal/workspaces/{task_id}/checkpoints` | `{epoch, seq, sha256, size_bytes}` | `{upload_url, expires_at}` — URL terikat (task, epoch); 409 `LEASE_LOST` |
| GET | `/internal/workspaces/{task_id}/checkpoints/latest?epoch=` | – | `{refs: [{seq, sha256, download_url}], commit}` |

| Shape | Field |
|---|---|
| `ExecutorNode` | `{id, name, organization_id\|null, status: "pending"\|"active"\|"draining"\|"offline"\|"revoked", labels, capacity_slots, slots_used, sandbox_runtime, version, last_heartbeat_at, token_expires_at, created_at}` (tanpa hash token) |
| `NodeLease` | `{id, node_id, task_id, epoch, status: "active"\|"released"\|"expired"\|"revoked", acquired_at, renewed_at, expires_at, release_reason\|null}` |
| `Capacity` | `{tier: "S"\|"M"\|"L", queue: {waiting, eta_minutes\|null}, nodes: [{id, name, status, slots_total, slots_used}] (admin), rejected_by_quota: [{task_id, scope, at}]}` |

### 13.8 SSO, SCIM, organisasi (M27)

**SSO (publik):**

| Method | Path | Perm | Request | Response |
|---|---|---|---|---|
| GET | `/auth/sso/connections?email_domain=` | public | – | `[{slug, display_name, protocol}]` (hanya koneksi aktif) |
| GET | `/auth/sso/{slug}/start?return_to=` | public | – | 302 ke IdP; set cookie pra-login `kk_sso` (HttpOnly, SameSite=Lax, 10 menit) |
| GET | `/auth/sso/oidc/callback?state=&code=` | public (cookie `kk_sso`) | – | 302 `/login/sso?request=<rid>` — pertukaran code di `worker-integrations` (api tanpa network outbound) |
| GET | `/auth/sso/requests/{rid}` | cookie `kk_sso` | – | `{status: "pending"\|"completed"\|"denied", reason_code\|null}`; `completed` → cookie sesi diset |
| POST | `/auth/sso/saml/acs` | public (form `SAMLResponse`, `RelayState`) | – | 302; dikecualikan dari header CSRF karena diverifikasi tanda tangan + `InResponseTo` + anti replay |
| GET | `/auth/sso/saml/metadata/{slug}` | public | – | XML metadata SP |
| POST | `/auth/login` (v1) | public | – | 403 `SSO_REQUIRED` bila login lokal dimatikan untuk organisasi akun (kecuali owner *break-glass*) |

**Admin SSO & SCIM token (`admin:sso`):** `GET/POST /admin/sso/connections` `{protocol: "oidc"|"saml", slug, display_name, organization_id?, issuer? (default OIDC_ISSUER), metadata_url? (default SAML_IDP_METADATA_URL), group_claim, group_role_map: {group: role}, jit_provisioning: false, enforce_sso: false, allowed_email_domains?}` → `SsoConnection` (secret IdP **tidak pernah** diterima lewat API — `OIDC_CLIENT_SECRET` dari env/secret file) · `GET/PATCH/DELETE /admin/sso/connections/{cid}` · `POST /admin/sso/connections/{cid}/test` → `{job_id}` · `POST /admin/sso/connections/{cid}/refresh-metadata` → `{job_id}` · `GET/POST /admin/sso/scim-tokens` `{organization_id, name, expires_at?}` → `{token (sekali tampil), token_prefix, id, expires_at}` · `DELETE /admin/sso/scim-tokens/{tid}`.

**Organisasi (`/orgs`):**

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| GET | `/orgs` | auth | – | `[{organization: Organization, org_role, source}]` |
| POST | `/orgs` | admin:orgs | `{slug, name}` | `Organization` (201) |
| GET · PATCH | `/orgs/{oid}` | anggota · org_admin | `{name?, settings?: {local_login_enabled?, four_eyes?, retention?}}` | `Organization` |
| GET · POST | `/orgs/{oid}/members` | anggota · org_admin | `{user_id\|email, org_role: "org_owner"\|"org_admin"\|"member"}` | `[OrgMember]` · `OrgMember` |
| PATCH · DELETE | `/orgs/{oid}/members/{uid}` | org_admin | `{org_role?}` | `OrgMember` / `{}` — anggota bersumber SCIM tidak dapat diubah manual (409) |
| POST | `/orgs/{oid}/switch` | anggota | – | `{session: {organization_id}}` |
| GET · PUT | `/orgs/{oid}/budgets` | org_admin | `{limit_usd, period, alert_thresholds?, action_on_exceed}` | `CostBudget` |

Header opsional `X-KK-Org: <uuid>`: bila ada dan ≠ organisasi aktif sesi → 409 `ORG_SCOPE`. Tier S/M memakai satu organisasi default.

**SCIM v2 (tier L)** — standar SCIM 2.0 (RFC 7643/7644) [Pasti untuk standar; cakupan implementasi = rancangan]:

| Method | Path | Catatan |
|---|---|---|
| GET | `/scim/v2/ServiceProviderConfig`, `/scim/v2/ResourceTypes`, `/scim/v2/Schemas` | discovery |
| GET | `/scim/v2/Users?filter=&startIndex=&count=` | filter hanya `eq` pada `userName`/`externalId` |
| POST | `/scim/v2/Users` | buat user + keanggotaan organisasi (tanpa password; `auth_source = scim`) |
| GET · PUT · PATCH | `/scim/v2/Users/{id}` | `active: false` → deprovision: sesi & token dicabut, WS/SSE ditutup; efektif ≤ 5 menit [Menebak] |
| DELETE | `/scim/v2/Users/{id}` | deprovision + keanggotaan dihapus; baris user dipertahankan untuk audit |
| GET · POST | `/scim/v2/Groups` | grup → role hanya lewat `group_role_map` eksplisit (tidak pernah owner platform) |
| GET · PATCH · DELETE | `/scim/v2/Groups/{id}` | perubahan anggota grup menyinkronkan role |

Auth SCIM: `Authorization: Bearer <token>` (sha256 + `compare_digest`; `scim_tokens` per organisasi atau `SCIM_TOKEN` untuk satu organisasi); tanpa sesi/CSRF; `Content-Type: application/scim+json`; error memakai skema SCIM (`urn:ietf:params:scim:api:messages:2.0:Error`, `status`, `detail`) — pengecualian envelope §1 seperti webhook; rate limit per token; audit `scim.*`.

| Shape | Field |
|---|---|
| `SsoConnection` | `{id, organization_id\|null, protocol, slug, display_name, issuer\|null, metadata_url\|null, group_claim, group_role_map, jit_provisioning, enforce_sso, allowed_email_domains, status, metadata_fetched_at\|null, updated_at}` (tanpa secret) |
| `Organization` | `{id, slug, name, status, settings, created_at}` |
| `OrgMember` | `{user: {id, email, display_name}, org_role, source: "local"\|"scim"\|"sso_jit", status, created_at}` |

### 13.9 RBAC v2.1 (usulan; pola §12.1)

| Permission | owner | admin | developer | reviewer | readonly | Scope |
|---|---|---|---|---|---|---|
| `workitem:write` (buat/ubah/tutup work item, baseline story) | ✓ | ✓ | ✓ | – | – | project |
| `workitem:plan` (minta/terima/tolak `ProgramPlan`, penjadwal, budget program/epic) | ✓ | ✓ | ✓ | – | – | project |
| `merge_queue:manage` (pause/resume/cancel/retry/policy) | ✓ | ✓ | – | – | – | project |
| `release:manage` (release, environment, promosi non-production) | ✓ | ✓ | ✓ | – | – | project |
| `terminal:use` (efektif hanya bila terminal diaktifkan global + project) | ✓ | ✓ | ✓ | – | – | project |
| `admin:nodes` | ✓ | ✓ | – | – | – | global |
| `admin:sso` (koneksi SSO, token SCIM) | ✓ | ✓ | – | – | – | global / organisasi |
| `admin:orgs` (buat organisasi, kelola lintas organisasi) | ✓ | – | – | – | – | global |

Izin lama yang dipakai: take-over = `task:control` + `workspace:write`; operasi file & git manual = `workspace:write` (+ `git:branch` untuk checkout/entri `user_branch`); run & test user = `test:run`; promosi production = `deploy:production`; mengaktifkan terminal per project = role owner/admin project. Role organisasi (tier L): `org_owner` = semua izin di dalam organisasinya; `org_admin` = `admin:sso`, anggota, dan budget organisasi; `member` = role project seperti biasa. Semua resource organisasi lain → 404. Pemetaan ini usulan yang menunggu persetujuan PO.

## 14. Kontrak v2.2 — Language packs, katalog service & kontrak, lingkungan integrasi, monitoring biaya & token realtime (Rancangan)

> **Status: rancangan / Proposed, belum diimplementasikan.** Sumber mengikat: `docs/POLYGLOT_INTEGRATION_DESIGN.md` §7 **KONTRAK v2.2** (K19 data/API/konfigurasi, K14 FR, K20 ancaman) dan §1–§3; desain rinci `docs/IMPLEMENTATION_DESIGN_V2.md` §14. Ditambahkan oleh agen dokumentasi (CLAUDE.md: perubahan `API_CONTRACTS.md` dicatat eksplisit di laporan agent; menunggu review Lead Architect). Semua aturan §1 (envelope, error, `X-Request-ID`, `Idempotency-Key`, pagination) dan §2 (sesi, CSRF, RBAC, 404 untuk resource di luar scope project/organisasi) tetap berlaku, kecuali stream SSE (`/usage/live`, `/integration-envs/{eid}/events`) dan unduhan ekspor yang bukan envelope. Endpoint K19 ditandai **K19**; endpoint bertanda *(turunan)* melengkapi daftar K19 agar FR dapat diuji. Tidak ada WebSocket baru (ADR-014 tetap; satu-satunya WS = terminal, §13.6). Angka batas bertanda [Menebak].

### 14.0 Kode error tambahan

| HTTP | code | Kapan | `details` |
|---|---|---|---|
| 409 | `PACK_NOT_ENABLED` | pack tidak terdaftar, nonaktif, `stale`, tidak ada di `LANGUAGE_PACKS_ENABLED`, atau (jalur agent) belum lulus eval G8 per pack (FR-259, FR-266) | `{pack_id, version\|null, reason: "not_registered"\|"disabled"\|"stale"\|"not_in_enabled_list"\|"agent_not_enabled"}` |
| 422 | `ENV_SPEC_REJECTED` | spec lingkungan integrasi melanggar policy: field terlarang (privileged, volumes/host mount, network_mode/host network, ports, devices, cap_add, image bebas, command), image/pack/service pendukung tidak terdaftar, env key di luar allowlist, pola secret (T28), siklus dependensi, tier S | `{violations: [{path, code, detail}], spec_sha256}` — `code` = `ViolationCode` (`IMPLEMENTATION_DESIGN_V2.md` §14.3.5) |
| 429 | `ENV_QUOTA_EXCEEDED` | kuota env terlampaui: jumlah service > `INTEGRATION_ENV_MAX_SERVICES`, CPU/RAM total, TTL > `INTEGRATION_ENV_TTL_MINUTES`, env aktif per project/node | `{scope: "env"\|"project"\|"node", limit, requested, allowed}` |
| 409 | `CONTRACT_BREAKING` | perubahan kontrak breaking sementara consumer terdampak belum diperbarui dan tidak ada pengecualian (FR-273); juga `failure_code` entri merge queue | `{contract_check_id, interface_id, changes: [ContractChange], affected_consumers: [service_id]}` |
| 409 | `RECONCILIATION_UNAVAILABLE` | rekonsiliasi tidak dapat dilakukan: nonaktif, tanpa kredensial, akun individual (Admin API tidak tersedia), kredensial ditolak, atau upstream gagal (FR-288, FR-290) | `{reason: "disabled"\|"no_credentials"\|"individual_account"\|"forbidden"\|"upstream_error", since\|null}` |
| 409 | `LOCKFILE_REQUIRED` *(turunan)* | `deps_install` diminta tanpa lockfile ekosistem (FR-263) | `{pack_id, path, expected: [file]}` |
| 409 | `DEPENDENCY_REJECTED` *(turunan)* | entri lockfile menunjuk registry selain proxy, paket di daftar blokir, atau namespace terpin dari upstream lain (T24) | `{findings: [{ecosystem, package, version, rule}]}` |
| 422 | `CONTRACT_INVALID` *(turunan)* | dokumen kontrak gagal validasi skema | `{interface_id, commit, errors: [{location, message}]}` |
| 409 | `ENV_NOT_ACTIVE` *(turunan)* | aksi pada env yang sudah `torn_down`/`expired`/`rejected` | `{status}` |

Kode lama yang dipakai ulang: `POLICY_DENIED` (403) bila `USAGE_LIVE_ENABLED=false` (`details.feature = "usage_live"`) atau `INTEGRATION_ENV_ENABLED=false` (`details.feature = "integration_env"`); `QUOTA_EXCEEDED` (429) untuk budget (`details.scope = "budget"`); `MERGE_QUEUE_FAILED` (409) dengan `failure_code` baru `CONTRACT_BREAKING` / `INTEGRATION_FAILED`; `NODE_UNAVAILABLE` (503) bila node env tidak tersedia; `DEPENDENCY_UNAVAILABLE` (503) untuk dep-proxy tidak tersedia.

### 14.1 Language packs & stack project (M28)

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| GET | `/language-packs` **K19** | auth | `?include_inactive=` (hanya `admin:policies`) | `[LanguagePack]` |
| GET | `/projects/{id}/stack` **K19** | project:read | – | `ProjectStack` |
| POST | `/projects/{id}/stack/detect` *(turunan)* | project:write | `{project_repository_id?}` | `{job_id}` (202) → event project `stack.detected` |
| PUT | `/projects/{id}/stack/overrides` *(turunan)* | project:write | `{stacks: [{path, pack_id, framework?}]}` | `ProjectStack` (`detected_by: "user"`; audit) |
| POST | `/projects` (v1, diperluas) | auth | `{name, source: {type: "template", template_id: "<pack>/<framework>"}, …}` | `Project` (202) — 409 `PACK_NOT_ENABLED` bila pack template tidak aktif (FR-261) |
| POST | `/projects/{id}/runs` · `/projects/{id}/test-runs` (v2.1, diperluas) | test:run | `profile_id` boleh profil pack (`<pack>.run`, `<pack>.test_select`, …) | `TestRun` + `language_pack`, `report_status` — 409 `PACK_NOT_ENABLED` |
| POST | `/projects/{id}/deps/install` *(turunan)* | test:run + workspace:write | `{path, pack_id}` | `TestRun` (202, profil `<pack>.deps_install` lewat proxy) — 409 `LOCKFILE_REQUIRED`/`DEPENDENCY_REJECTED`; 403 `POLICY_DENIED` bila `SANDBOX_EGRESS≠allowlist` atau proxy ekosistem tidak dikonfigurasi |
| POST | `/admin/language-packs` *(turunan)* | admin:policies | `LanguagePackManifest` (JSON, skema `IMPLEMENTATION_DESIGN_V2.md` §14.3.1) | `LanguagePack` (`validated`) — 422 `VALIDATION_ERROR` + `details.issues` |
| POST | `/admin/language-packs/{pack_id}/versions/{version}/activate` *(turunan)* | admin:policies | – | `LanguagePack` (`active`; versi lama `disabled`; audit) |
| POST | `/admin/language-packs/{pack_id}/agent-enable` *(turunan)* | admin:policies | `{eval_report_id, decision_ref}` | `LanguagePack` (`agent_enabled: true`) — 409 bila eval di bawah ambang (FR-266) |

| Shape | Field |
|---|---|
| `LanguagePack` | `{pack_id, version, level: "L1"\|"L2", category: "app"\|"database", languages, status: "draft"\|"validated"\|"active"\|"stale"\|"disabled", agent_enabled, image: {repository, tag, digest, built_at, scan_critical}, profiles: [{profile_id, kind, suite, network}], registries: [ecosystem], templates: [{id, framework, description}], unsupported_targets, resource_class, updated_at}` |
| `ProjectStack` | `{project_id, detected_commit\|null, monorepo_tool\|null, stacks: [{path, project_repository_id\|null, pack_id, pack_status, framework\|null, package_manager\|null, entrypoints, lockfile\|null, confidence, unsupported_targets, detected_by: "detector"\|"user", external_ci_only: bool}], primary: {path, pack_id}\|null, truncated}` |
| `TestRun` (tambahan) | `{language_pack\|null, report_status: "parsed"\|"unparsed"\|"missing"\|null, report_source_format\|null, integration_env_id\|null}` |

### 14.2 Usage, budget, dan rekonsiliasi (M30)

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| GET | `/usage/live` **K19** | usage:read | `?project_id=&capability=` (filter opsional) | **SSE** (bukan envelope): `usage.event`, `budget.alert`, `usage.reconciliation_updated` (§14.3); 403 `POLICY_DENIED` bila `USAGE_LIVE_ENABLED=false` |
| GET | `/usage/summary` **K19** | usage:read (+`usage:read_all` lintas project) | `?group_by=model\|agent\|project\|task\|capability\|user\|provider&period=today\|month\|custom&from=&to=&project_id=` | `UsageSummary` |
| GET | `/usage/timeseries` **K19** | usage:read | `?bucket=1m\|1h\|1d&from=&to=&group_by=&project_id=` — rentang maks: `1m` 24 jam, `1h` 31 hari, `1d` 400 hari [Menebak] | `{bucket, points: [SeriesPoint]}` |
| GET | `/usage/reconciliation` **K19** | usage:admin | `?bucket=1m\|1d&from=&to=&model=&status=` | `ReconciliationView` — 409 `RECONCILIATION_UNAVAILABLE` |
| GET | `/budgets` **K19** | usage:read | `?scope_type=&scope_key=` | `[BudgetView]` (gabungan sumber; tanpa penyimpanan kedua — OI-44) |
| POST | `/budgets` **K19** | budget:manage + izin sumber (project: `project:write`; program/epic: `workitem:plan`; organisasi: `org_admin`; capability: `admin:policies`) | `{scope_type: "project"\|"program"\|"epic"\|"organization"\|"capability", scope_key, limit_usd, period: "monthly"\|"total", action_on_exceed?: "alert"\|"block_new_tasks"\|"kill"}` | `BudgetView` (ditulis ke sumber asli; audit) — `scope_type = "task"` → 422 (budget task hanya lewat pembuatan task / approval `budget_increase`); sumber belum ada di fase berjalan → 409 `CONFLICT` dengan alasan |
| GET | `/budgets/alerts` **K19** | usage:read | `?scope_type=&since=&acknowledged=` | `[BudgetAlert]` |
| POST | `/budgets/alerts/{aid}/ack` *(turunan)* | budget:manage | – | `BudgetAlert` (audit) |
| GET | `/usage/export` **K19** | usage:read (+`usage:read_all` lintas project) | `?format=csv\|json&granularity=call\|minute\|day&from=&to=&group_by=&project_id=` | file attachment streaming (`nosniff`), maks 1 juta baris [Menebak]; setiap baris membawa `figure_status` per angka |
| POST | `/usage/rollups/rebuild` *(turunan)* | usage:admin | `{from, to}` | `{job_id}` (202) — hasil selisih rebuild (harus 0) |

Kill switch yang ditautkan alert memakai endpoint yang sudah ada: capability (`/admin/capabilities`, FR-217), program/epic/organisasi (`/work-items/{wid}/budget`, `/orgs/{oid}/budgets`, FR-255), task/project (budget v1, FR-132).

| Shape | Field |
|---|---|
| `FigureStatus` | `"actual"\|"estimated"\|"official"\|"unavailable"` → label UI `aktual` / `estimasi` / `resmi` / `tidak tersedia` (FR-290). Token dari provider = `actual`; biaya realtime = `estimated` (atau `unavailable` bila tarif tidak dikenal); biaya/token Admin API = `official`. `cost_status` lama (`estimated`/`partial`/`unavailable`) tetap pada record; `actual` tetap dicadangkan (ADR-025) |
| `UsageSummary` | `{period: {from, to}, totals: {calls, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, cache_hit_ratio, estimated_cost, cost_status, figures: {tokens: FigureStatus, cost: FigureStatus}}, burn_rate: {tokens_per_minute, usd_per_hour\|null, window_minutes}, projection: {period_end, estimated_cost\|null, basis: "7d_average"}, budgets: [BudgetView], groups: [{key, label, calls, input_tokens, output_tokens, cache_read_tokens, cache_hit_ratio, estimated_cost, cost_status}]}` |
| `SeriesPoint` | `{bucket_start, key\|null, calls, input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, estimated_cost\|null, cost_status}` |
| `BudgetView` | `{scope_type, scope_key, source: "task_budget"\|"project_budget"\|"cost_budget"\|"capability_setting", limit_usd, period, used_usd, cost_status, thresholds: [0.5, 0.8, 1.0], reached: [threshold], action_on_exceed\|null, kill_switch: {active, ref}}` |
| `BudgetAlert` | `{id, scope_type, scope_key, source, period_key, threshold, used_usd, limit_usd, cost_status, kill_switch_ref, triggered_at, acknowledged_by\|null, acknowledged_at\|null}` |
| `ReconciliationView` | `{available: true, scope: "api_key"\|"workspace"\|"organization", interval_seconds, last_run_at, variance_alert_pct, rows: [{bucket_width: "1m"\|"1d", bucket_start, model, internal: {input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens, estimated_cost\|null}, official: {input_tokens\|null, output_tokens\|null, cache_creation_tokens\|null, cache_read_tokens\|null, cost\|null}, token_variance_pct\|null, cost_variance_pct\|null, status: "settling"\|"matched"\|"variance"\|"unavailable"\|"error", status_reason\|null}], notes: [string]}` — `official.cost` hanya untuk `1d`; kunci admin **tidak pernah** muncul (status kredensial hanya `configured`/`missing` di `/settings/providers`) |

### 14.3 SSE `usage.event` (stream `GET /usage/live`)

Frame SSE: `event: <type>` dan `data: <json>`; **ephemeral** — tanpa `id:`/`Last-Event-ID`/replay (klien me-refetch `/usage/summary` dan `/usage/timeseries` saat reconnect); heartbeat `: ping` tiap 15 dtk; kanal Redis `kk:usage:org:{org_id|default}`; filter per koneksi: user tanpa `usage:read_all` hanya menerima event project yang dapat ia baca (`project:read`) dan panggilan Portal AI miliknya sendiri; field dimensi di luar scope tidak pernah dikirim. Urutan per `call_id`: nol atau lebih `progress` lalu tepat satu `final` (panggilan yang gagal/dibatalkan tetap mendapat `final` dengan `status ≠ "ok"`).

```json
{
  "event_id": "uev_01J…",
  "call_id": "0192f…-uuid",
  "phase": "final",
  "ts": "2026-10-22T08:15:30.412Z",
  "usage_event_id": "uuid",
  "organization_id": "uuid",
  "project_id": "uuid",
  "task_id": "uuid",
  "thread_id": null,
  "agent_run_id": "uuid",
  "agent_id": "B14",
  "user_id": "uuid",
  "capability": "chat",
  "provider": "anthropic",
  "model": "<model id>",
  "tokens": {"input": 1834, "output": 412, "cache_creation": 0, "cache_read": 12000},
  "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
  "units": {},
  "estimated_cost": "0.012345",
  "currency": "USD",
  "cost_status": "estimated",
  "pricing_version": "2026-10-01",
  "figures": {"tokens": "actual", "cost": "estimated"},
  "status": "ok",
  "duration_ms": 8123
}
```

| Field | Tipe | Catatan |
|---|---|---|
| `event_id` | string | unik per frame; dedupe klien |
| `call_id` | uuid | satu per panggilan model/vendor (idempotensi, `usage_events.call_id`) |
| `phase` | `"progress"\|"final"` | `progress` = token kumulatif sejauh ini (throttle ≤ 1/dtk/panggilan [Menebak]); `final` = setelah ledger ter-commit |
| `usage_event_id` | uuid\|null | hanya pada `final` |
| `organization_id`, `project_id`, `task_id`, `thread_id`, `agent_run_id`, `agent_id`, `user_id`, `capability` | uuid/string\|null | dimensi; `null` bila tidak berlaku atau di luar scope penerima |
| `provider`, `model` | string, string\|null | vendor non-LLM: `model` dapat `null` |
| `tokens` | `{input, output, cache_creation, cache_read}` (int) | dari `usage` provider (`message_start` + nilai kumulatif `message_delta`, tidak dijumlahkan) — `figures.tokens = "actual"`; vendor tanpa token → 0 dan `figures.tokens = "unavailable"` |
| `server_tool_use` | object int | mis. `web_search_requests` (dari usage provider) |
| `units` | object string-desimal | unit non-token vendor/capability, mis. `{"image": "2"}` (ledger `capability_usage`) |
| `estimated_cost` | string desimal\|null | estimasi dari tabel tarif (`pricing_version`); `null` bila tarif tidak dikenal (bukan 0) |
| `cost_status` | `"estimated"\|"partial"\|"unavailable"` | sama dengan record (ADR-025) |
| `figures` | `{tokens: FigureStatus, cost: FigureStatus}` | label jujur FR-290; stream tidak pernah `official` (resmi hanya lewat rekonsiliasi) |
| `status` | `"ok"\|"error"\|"cancelled"` | gagal/batal setelah `message_start` → token kumulatif terakhir, `cost_status = "partial"` |
| `duration_ms` | int\|null | hanya `final` |

Event lain di stream yang sama:

| type | payload |
|---|---|
| `budget.alert` | `BudgetAlert` |
| `usage.reconciliation_updated` | `{bucket_width, from, to, rows_changed, variance_rows, status_counts: {settling, matched, variance, unavailable, error}}` (hanya penerima ber-izin `usage:admin`) |

### 14.4 Katalog service & kontrak (M29)

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| GET | `/programs/{id}/services` **K19** | project:read | `?view=list\|graph` | `[Service]` atau `ServiceGraph` (FR-281) |
| POST | `/programs/{id}/services` **K19** | catalog:write | `{slug, name, kind: "internal"\|"external", pack_id?, project_repository_id?, root_path?, owner_id?, description?, interfaces?: [InterfaceIn], dependencies?: [{kind, depends_on_service_id?, support_key?, required?}]}` | `Service` (201) — 409 `PACK_NOT_ENABLED`; 422 bila repo bukan milik project, path kontrak tidak ada, atau support di luar katalog |
| PATCH · DELETE | `/services/{sid}` *(turunan)* | catalog:write | sebagian field POST | `Service` / `{}` — DELETE ditolak (409) bila service masih dirujuk consumer |
| GET | `/services/{sid}/contracts` **K19** | project:read | – | `[ServiceInterface]` (+ `last_check: ContractCheck\|null`) |
| POST | `/services/{sid}/contracts` *(turunan)* | catalog:write | `InterfaceIn = {direction: "provides"\|"consumes", contract_type: "openapi"\|"asyncapi"\|"protobuf"\|"graphql", path?, provider_interface_id?}` | `ServiceInterface` (201) |
| POST | `/contracts/check` **K19** | test:run | `{interface_id, head_commit, base_commit? (default: head default branch), task_id?}` | `{job_id, contract_check_id}` (202) — validasi & diff di sandbox; hasil `GET /contract-checks/{cid}` + event project `contract.check_finished` |
| GET | `/contract-checks/{cid}` *(turunan)* | project:read | – | `ContractCheck` |
| POST | `/contract-checks/{cid}/exception` *(turunan)* | merge_queue:manage | `{reason}` (wajib) | `ContractCheck` (`exception_granted_*`; audit `contract.exception_granted`) — hanya `result = "breaking"`; terikat `head_commit`; **bukan** approval R3 (OI-45) |
| POST | `/services/{sid}/contracts/{iid}/mock` *(turunan)* | test:run | `{commit?}` | `{job_id}` (202) → artefak mock bundle; dipakai test consumer dan mode tier S (FR-274, FR-276) |

| Shape | Field |
|---|---|
| `Service` | `{id, project_id, program_id\|null, slug, name, kind, pack_id\|null, pack_status\|null, project_repository_id\|null, root_path, owner: {id, display_name}\|null, status, interfaces: [ServiceInterface], dependencies: [{id, kind, depends_on_service_id\|null, support_key\|null, required}], updated_at}` |
| `ServiceInterface` | `{id, service_id, direction, contract_type, path\|null, provider_interface_id\|null, version\|null, contract_sha256\|null, validation_status: "unknown"\|"valid"\|"invalid", last_validated_commit\|null}` |
| `ServiceGraph` | `{nodes: [{service_id, slug, kind, pack_id, status, last_env: {env_id, status}\|null}], edges: [{from_service_id, to_service_id\|null, support_key\|null, interface_id\|null, contract_type\|null, last_check_result\|null}]}` |
| `ContractCheck` | `{id, interface_id, contract_type, base_commit, head_commit, task_id\|null, merge_queue_entry_id\|null, status: "running"\|"done", result: "compatible"\|"breaking"\|"invalid"\|"error"\|null, changes: [ContractChange], affected_consumers: [{service_id, slug, updated_at_commit\|null, contract_test_passed: bool}], exception: {granted_by, reason, granted_at}\|null, tool, tool_version, report_artifact_id\|null, checked_at}` |
| `ContractChange` | `{kind: "breaking"\|"non_breaking"\|"info", rule, location, detail}` |

### 14.5 Lingkungan integrasi (M29)

| Method | Path | Perm | Request | Response `data` |
|---|---|---|---|---|
| POST | `/projects/{id}/integration-envs` **K19** | env:run | `IntegrationEnvSpec` (`IMPLEMENTATION_DESIGN_V2.md` §14.3.5) + `Idempotency-Key` | `IntegrationEnv` (202) — 422 `ENV_SPEC_REJECTED`; 429 `ENV_QUOTA_EXCEEDED`; 403 `POLICY_DENIED` (`INTEGRATION_ENV_ENABLED=false`); tier S → 422 `ENV_SPEC_REJECTED` dengan `code = "TIER_UNSUPPORTED"` (gunakan mode kontrak + mock, §14.4); 503 `NODE_UNAVAILABLE` |
| GET | `/projects/{id}/integration-envs?status=` *(turunan)* | project:read | – | `[IntegrationEnvSummary]` |
| GET | `/integration-envs/{eid}` **K19** | project:read | – | `IntegrationEnv` |
| GET | `/integration-envs/{eid}/events` **K19** (SSE status/log) | project:read | – | **SSE** ephemeral, kanal `kk:env:{id}`: `integration_env.status_changed {status, reason}`, `integration_env.service_health {name, status, health}`, `integration_env.log {name, seq, data}` (teredaksi, ≤ 64 KB per frame), `integration_env.test_finished {test_run_id, status}`; reconnect → refetch `GET /integration-envs/{eid}` |
| GET | `/integration-envs/{eid}/services/{name}/logs?offset=` *(turunan)* | project:read | – | `{offset, next_offset, chunks: [{stream, data}], done}` (teredaksi) |
| DELETE | `/integration-envs/{eid}` **K19** | env:run (pembuat) / owner-admin project | – | `IntegrationEnv` (`tearing_down`, 202) — idempoten; 409 `ENV_NOT_ACTIVE` bila sudah selesai |

| Shape | Field |
|---|---|
| `IntegrationEnvSummary` | `{id, project_id, work_item_id\|null, status: "requested"\|"validating"\|"rejected"\|"provisioning"\|"running"\|"testing"\|"passed"\|"failed"\|"tearing_down"\|"torn_down"\|"expired"\|"error", services_count, created_by, requested_at, expires_at, torn_down_at\|null}` |
| `IntegrationEnv` | `IntegrationEnvSummary + {spec_sha256, spec (ternormalisasi, tanpa nilai secret palsu), quotas: {max_services, cpu, memory_bytes, ttl_minutes}, node_id\|null (admin), services: [{name, kind, service_id\|null, pack_id\|null, support_key\|null, image_digest, commit\|null, status, health\|null, log_artifact_id\|null}], test_runs: [TestRun], violations: [{path, code, detail}], teardown_reason\|null}` |

Tidak ada endpoint yang menerima image bebas, port publish, volume host, atau secret nyata. Secret palsu yang dibangkitkan tidak pernah dikembalikan API.

### 14.6 Tambahan pada merge queue & `TestRun`

- `MergeQueueEntry` (§13.2) menambah `contract_check_ids: [uuid]`, `integration_env_id: uuid | null`, dan `failure_code` baru `CONTRACT_BREAKING` (stage `testing`, details §14.0) serta `INTEGRATION_FAILED` *(turunan)* (E2E lintas service gagal atau bukti tidak ada untuk story > 1 service, FR-282).
- `MergeQueuePolicy` menambah `require_contract_check: bool` (default `true` bila repo punya interface terdaftar) dan `require_integration_e2e` (`"auto"` / `"off"`) (`auto` = wajib untuk story yang menyentuh > 1 service; tier S = bukti kontrak + mock, OI-39) *(turunan)*.
- Event project (§13.5) menambah `stack.detected {project_id, stacks_count}`, `contract.check_finished {contract_check_id, interface_id, result}`, `integration_env.created {env_id}` *(turunan)*.

### 14.7 Tambahan pada `/settings/providers`

`ProviderConfig` menambah `admin_api: {reconcile_enabled, secret_status: "configured"|"missing", last_status: "ok"|"disabled"|"no_credentials"|"individual_account"|"forbidden"|"upstream_error", last_run_at|null}` — tanpa `secret_hint` untuk kunci admin; kunci tidak pernah diterima lewat API (ADR-004, T26).

### 14.8 Sandbox Manager internal API v2.2 (tidak diekspos publik)

Auth & network sama dengan §10. Hanya aktif di node executor tier M/L dengan `INTEGRATION_ENV_ENABLED=true`.

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/v1/envs` | `{env_id, project_id, expires_at, plan: ResolvedEnvPlan}` (image@digest per service, profil, urutan start, CPU/RAM, env non-secret, nama secret palsu) | `{env_id, network, services: [{name, container_id, status}]}` — 422 bila validasi ulang otoritatif gagal (`violations`) |
| GET | `/v1/envs/{env_id}` | – | `{env_id, status, services: [{name, status, health}], expires_at}` |
| GET | `/v1/envs/{env_id}/services/{name}/logs?offset=` | – | `{offset, next_offset, chunks, done}` (teredaksi) |
| POST | `/v1/envs/{env_id}/runs` | `{profile_id, args: [..], timeout_s?}` | `{run_id, status}` → hasil via `GET /v1/runs/{run_id}` (§10) |
| DELETE | `/v1/envs/{env_id}` | `{reason: "completed"\|"ttl"\|"user"\|"failed"\|"admin"}` | `{removed_containers, network_removed: bool}` — idempoten |
| GET | `/v1/support-images` | – | `[{support_key, image, digest, env_allowlist, healthcheck}]` |

`GET /v1/profiles` (§10) kini memuat profil semua pack aktif (dibangkitkan dari manifest). Validasi tambahan: `workspace_subpath` env `^[0-9a-f-]{36}/envs/[0-9a-f-]{36}/[a-z][a-z0-9-]{1,30}$` *(turunan)*; network env selalu `internal`; tidak ada field `HostConfig`, port, volume, atau `network_mode` dari caller.

### 14.9 RBAC v2.2 (usulan; pola §12.1)

| Permission | owner | admin | developer | reviewer | readonly | Scope |
|---|---|---|---|---|---|---|
| `usage:read` (halaman monitoring, ringkasan, timeseries, alert, ekspor — dalam scope project yang dapat dibaca + resource milik sendiri) | ✓ | ✓ | ✓ | ✓ | ✓ | project / milik sendiri |
| `usage:admin` (panel rekonsiliasi, rebuild rollup, status kredensial — tanpa pernah melihat kunci) | ✓ | ✓ | – | – | – | global |
| `budget:manage` (ubah budget lewat fasad `/budgets`, ack alert; tetap butuh izin sumber) | ✓ | ✓ | ✓ | – | – | project / global |
| `env:run` (buat/hapus lingkungan integrasi; mock memakai `test:run`) | ✓ | ✓ | ✓ | – | – | project |
| `catalog:write` (katalog service, interface kontrak, dependensi) | ✓ | ✓ | ✓ | – | – | project |

Izin lama yang dipakai: lintas project = `usage:read_all` (global, owner/admin); kelola pack & aktivasi agent per pack = `admin:policies`; `POST /contracts/check` dan mock = `test:run`; pengecualian breaking change = `merge_queue:manage`; instal dependensi = `test:run` + `workspace:write`. Tidak ada `action_type` approval baru di v2.2. Pemetaan ini usulan yang menunggu persetujuan PO.
