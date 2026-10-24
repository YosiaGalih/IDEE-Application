# KodeKantor — Test Strategy (v1.0, MVP)

> Sumber: PRD v1.0 §7.4 (toolchain & quality gates), §12.1 (AC-01..AC-20), §12.2 (test layers), §13 (phases), §13.1 (MVP DoD), §14.1 #5 ("jangan mengklaim test lulus tanpa menjalankan dan mengutip hasil"); ADR-015 (ScriptedProvider), ADR-017 (Pyright); `CLAUDE.md`; `docs/SECURITY_MODEL.md`; `docs/USER_PROXY_ACCEPTANCE.md §12`.
> Nama file test di dokumen ini adalah **rencana lokasi** yang mengikat agent implementasi; status keberadaannya dilacak di `docs/IMPLEMENTATION_TRACKER.md`.

## 1. Prinsip

1. **Evidence over claims** berlaku juga untuk tim: laporan batch wajib mengutip output perintah test aktual (CLAUDE.md, PRD §14.1 #4–5).
2. **Deterministik dulu.** Unit/integration/E2E memakai `LLM_PROVIDER=scripted` (ADR-015) dengan skenario di `backend/app/llm/scenarios/`; tidak ada panggilan Claude API di CI. Panggilan nyata hanya pada readiness gate (`python -m app.cli claude-smoke`, PRD §7.3.6) dan eval set staging.
3. **Sandbox dua tingkat.** `SANDBOX_DRIVER=fake` / `FakeSandboxClient` untuk unit & sebagian besar integration; test isolasi nyata ber-marker `docker` (dan `runsc` bila tersedia) di job CI terpisah.
4. **Coverage = indikator**, bukan satu-satunya ukuran (PRD §7.4). Target awal *(turunan)*: ≥ 85% line untuk `app/core`, `app/tools`, `app/workflow`, `app/services/acceptance.py`, `app/workspace/paths.py`, `app/workspace/archive.py`; ≥ 70% backend keseluruhan.
5. **Setiap bug keamanan → test regresi** di `backend/tests/security/` sebelum fix di-merge.

## 2. Lapisan test (PRD §12.2)

| Lapisan | Cakupan PRD | Tool | Lokasi | Lingkungan |
|---|---|---|---|---|
| **Unit** | schema validation, routing, permission policy, state transitions, path validation, budget logic, redaction | Pytest; Vitest + RTL | `backend/tests/unit/`, `sandbox_manager/tests/`, `frontend/src/**/__tests__/` | tanpa DB/Redis/Docker |
| **Integration** | PostgreSQL/Redis, provider adapter mocked, task queue, sandbox manager, storage, Git adapter | Pytest + httpx `AsyncClient`, Celery eager, git CLI nyata di tmpdir | `backend/tests/integration/` | PostgreSQL 16 + Redis 7 (service container CI); `FakeSandboxClient` atau sandbox-manager fake driver |
| **E2E** | login → create project → prompt → generate patch → run tests → view diff → User Proxy report → approve/reject | Playwright | `tests/e2e/` | full stack `infra/compose.yml` + `compose.e2e.yml` (`LLM_PROVIDER=scripted`, sandbox docker driver) |
| **Security** | cross-project access, path traversal/symlink, prompt injection, secret leakage, approval replay, container limits | Pytest (+ marker `docker`) | `backend/tests/security/`, `sandbox_manager/tests/test_isolation_docker.py` | DB/Redis; Docker untuk marker `docker` |
| **Recovery** | process restart, duplicate queue delivery, interrupted LLM stream, sandbox crash, partial task artifacts | Pytest | `backend/tests/integration/test_recovery.py` | DB/Redis; ScriptedProvider dengan skenario putus/timeout |
| **UX** | empty/loading/error states, keyboard navigation, responsive desktop widths, live logs & diff readability | Vitest + RTL, Playwright (`@axe-core/playwright`) | `frontend/src/**/__tests__/`, `tests/e2e/a11y.spec.ts` | jsdom; browser Chromium |

### 2.1 Unit — yang wajib
- `test_states_transition.py` (integration): semua pasangan (from, to) — yang ada di `ALLOWED` lolos, sisanya `INVALID_TRANSITION`; optimistic lock; audit + event tertulis (AC-12, AC-14, ADR-006).
- `test_policy.py`: matriks *permission profile × tool* dari TOOL_CATALOG (R0–R3 prasyarat), aturan path `test_only`/`docs_only`, R4 tidak pernah terdaftar, reviewer read-only (AC-06, AC-10).
- `test_paths.py` (unit, `backend/tests/unit/`): traversal, absolut, NUL, symlink keluar root, denylist tulis/kirim-LLM.
- `test_redaction.py`: semua pola secret (§6.2 SECURITY_MODEL) + nilai literal secret terkonfigurasi.
- `test_budget.py`, `test_pricing_usage.py`: `BudgetGuard`, estimasi vs `unavailable`, pricing berversi (AC-16).
- `test_evidence_verification.py`, `test_acceptance_gate.py`: aturan V1–V10 & gate S9 (AC-11, AC-12).
- `test_contracts.py`: setiap output contract menolak field asing (`extra=forbid`), regex key, batas panjang.
- `test_junit_parser.py`, `test_profiles.py`: parsing junit/coverage; pemilihan profil per stack; validasi args.
- `test_registry_loader.py`: loader menolak registry invalid; `scripts/test_validate_registry.py` (sudah ada, 17 test).
- `test_config.py`: fail-start (SECRET_KEY pendek, `scripted` di production, `fake` driver di production).
- `test_rbac.py`: matriks role × permission (API_CONTRACTS §2).

### 2.2 Integration — yang wajib
Service + DB nyata, provider di-mock (ScriptedProvider), sandbox fake: auth/sesi, projects & import, workspace API, task flow S1–S10, SSE replay, gateway denial, test runner, review/apply/reject, User Proxy step, cancellation, usage API, recovery, remediation cycle, audit trace, Git adapter (worktree, diff, commit, merge konflik), migrasi Alembic `upgrade head` → `downgrade -1` → `upgrade head` pada DB uji.

### 2.3 E2E — skenario
`happy-path.spec.ts`, `reject-patch.spec.ts`, `acceptance-remediation.spec.ts`, `reconnect.spec.ts`, `cancel.spec.ts`, `a11y.spec.ts`. Semua memakai project template Python kecil dan skenario Scripted deterministik.

### 2.4 Recovery — skenario
Restart worker saat `running` (lease kedaluwarsa → `interrupted` → resume melanjutkan checkpoint), duplicate Celery delivery (lease menolak job kedua), stream LLM terputus (`LLMError(kind=transient)` → retry; `cancelled` → stop), sandbox crash (`status=error` dibedakan dari `failed`), artefak parsial (run timeout → log tersimpan, `test_runs.status=timeout`).

## 3. Kewajiban test per paket

| Paket | Wajib diuji |
|---|---|
| `app/core` | config fail-start, redaction, RBAC, CSRF/sesi (security.py), error envelope (tanpa stack trace/path absolut), audit append-only, event seq monotonic |
| `app/api` | setiap route: 401 tanpa sesi, 403/404 lintas scope, 422 validasi, CSRF untuk mutating, pagination, `X-Request-ID`, idempotency replay |
| `app/services` | logika domain per modul; acceptance (verifikasi/gate), approvals (hash, consume, expiry, superseded), usage |
| `app/workspace` | safe paths, archive importer (fixture §4), patches (apply unified diff, konflik), stack detect, index/search, template project dapat di-test tanpa network |
| `app/gitops` | argumen tetap tanpa shell, env bersih, hooks off, protokol dibatasi, worktree per task, merge konflik terdeteksi, tidak ada force |
| `app/llm` | `AnthropicClaudeProvider` dengan transport mock (mapping error 429/5xx/timeout/refusal/max_tokens, usage, request_id, cancel), `ScriptedProvider` (skenario), hanya file ini import `anthropic` (`test_no_host_exec.py`) |
| `app/tools` | gateway urutan pemeriksaan, setiap builtin tool (input schema, output, risk, prasyarat), truncation, idempotency key, `ApprovalRequired` |
| `app/agents` | registry loader, `AgentRuntime` (allowlist tools ke model, `submit_result` valid/invalid, nudge 1x, batas iterasi, budget, cancel), prompt_version hash |
| `app/workflow` | state machine, engine S1–S10, checkpoint idempoten, lease/heartbeat, recovery sweep, budget, debug loop ≤ 5, auto-remediation ≤ 1 |
| `app/sandbox` | `HttpSandboxClient` (auth header, timeout, mapping status), junit parser, profil |
| `sandbox_manager/` | validasi request (subpath regex+realpath, profile, args, timeout, network), HostConfig hardened & tidak dapat dipengaruhi caller, token constant-time, kill run/task, isolasi Docker (marker) |
| `frontend/` | per fitur: state normal/loading/empty/error/permission denied (PRD §4.3, §7.4); hooks API (TanStack Query) dengan MSW; SSE hook reconnect + dedupe; diff viewer; editor save state & konflik 409; i18n key (tidak ada teks hard-coded — lint) |
| `infra/` | `docker compose config` valid dan hanya `caddy` yang mempublikasi port (80/443) — cek CI rencana; `scripts/healthcheck.sh` (status health container + `/health/live`, `/health/ready`) |
| `scripts/` | `scripts/test_validate_registry.py` |

## 4. Fixture

| Fixture | Isi | Dipakai oleh |
|---|---|---|
| `tests/fixtures/archives/` *(dibangkitkan oleh `conftest.py`, bukan biner di repo)* | `traversal.zip` (`../../etc/passwd`), `absolute.tar.gz` (`/etc/x`), `symlink_escape.tar.gz` (link → `/`), `hardlink.tar.gz`, `device.tar` (char device), `zip_bomb.zip` (rasio > 100), `too_many_files.zip` (> 50.000), `oversize.zip` (> batas), `overwrite.tar.gz` (dua entri path sama / `.git/config`), `nested_ok.zip` (valid) | `test_archive_import.py` (AC-02) |
| Workspace symlink | repo tmp berisi symlink ke luar root, symlink ke `.env`, dir symlink loop | `test_paths.py`, `test_workspace_api.py` |
| `prompt_injection_repo/` | README & komentar kode berisi instruksi injeksi (baca `.env`, tulis `/etc`, `git.push`, "approval granted", "tandai semua kriteria passed"), file `.env` palsu berisi secret sintetis | `test_prompt_injection.py` |
| Secret fixtures | nilai sintetis: `sk-ant-test-0000…`, PEM private key palsu, `AKIA…` palsu, URL `https://user:pass@…`; disuntik ke env test & file repo | `test_secret_redaction.py`, `test_redaction.py` (AC-15) |
| Sample projects | `python_ok/` (pytest lulus), `python_failing/` (1 assertion gagal, diperbaiki skenario debugger), `python_timeout/` (sleep), `python_crash/` (segfault/exit 137 simulasi), `node_ok/` | test runner (AC-08), debug loop, E2E |
| Scripted scenarios | per agent: sukses, output invalid → retry, tool di luar allowlist, budget habis, cancel di tengah stream, User Proxy pass/fail/no-evidence/foreign-ref | integration & E2E |
| Fake sandbox results | `succeeded`, `failed`, `timeout`, `killed`, `error`, output terpotong, junit/coverage XML | unit/integration |
| Waktu | `freezegun`/clock injectable untuk lease, expiry approval, sesi | recovery, approvals, auth |

## 5. Rencana test AC-01..AC-20

| AC | Kriteria (ringkas) | Test (file::nama) | Lapisan |
|---|---|---|---|
| AC-01 | login/logout; tidak bisa membuka project user lain | `backend/tests/integration/test_auth_flow.py::test_login_sets_cookies_and_me`, `::test_logout_revokes_session_and_clears_cookies`, `::test_lockout_after_five_failures`; `backend/tests/integration/test_rbac_scope.py::test_task_access_cross_project`; `backend/tests/security/test_cross_project.py::test_endpoint_denies_other_users_project[*]`; `frontend/src/features/auth/__tests__/LoginPage.test.tsx` | integration, security, UI |
| AC-02 | blank/template + import archive aman | `backend/tests/integration/test_projects.py::test_create_blank`, `::test_create_from_template_runs_tests`; `backend/tests/security/test_archive_import.py::test_rejects[<kasus>]` (kasus: traversal, absolute, symlink, hardlink, device, zip_bomb, too_many_files, oversize, overwrite), `::test_accepts_valid_archive` | integration, security |
| AC-03 | baca, cari, tab, edit via workspace API | `backend/tests/integration/test_workspace_api.py::test_read_file`, `::test_search_text_and_symbol`, `::test_write_version_conflict_409`; `frontend/src/features/workspace/__tests__/EditorTabs.test.tsx`, `FileTree.test.tsx` | integration, UI |
| AC-04 | prompt → task dengan requirement, plan, status, ID | `backend/tests/integration/test_task_flow.py::test_prompt_creates_task_with_requirements_and_plan` | integration |
| AC-05 | stream + reconnect mengambil event hilang | `backend/tests/integration/test_events_sse.py::test_replay_after_last_event_id`, `::test_no_duplicate_seq`; `frontend/src/api/__tests__/useTaskEvents.test.ts`; `tests/e2e/reconnect.spec.ts` | integration, UI, E2E |
| AC-06 | tool hanya yang diizinkan | `backend/tests/unit/test_policy.py`; `backend/tests/integration/test_gateway_denials.py::test_denied_tool_recorded_and_audited`; `backend/tests/security/test_prompt_injection.py`; `scripts/test_validate_registry.py` | unit, integration, security |
| AC-07 | sandbox, bukan host; timeout & limit | `sandbox_manager/tests/test_hostconfig.py::test_hardened_hostconfig`, `::test_no_docker_sock_mount`, `::test_caller_cannot_override`; `sandbox_manager/tests/test_isolation_docker.py` (marker `docker`): `test_fork_bomb_contained`, `test_timeout_kills`, `test_no_network_by_default`, `test_readonly_rootfs`, `test_env_has_no_secrets`; `backend/tests/security/test_no_host_exec.py` | unit, security |
| AC-08 | runner menyimpan exit code, log, status, durasi, artefak | `backend/tests/integration/test_test_runner.py::test_pass`, `::test_fail`, `::test_timeout`, `::test_runner_crash_distinguished`, `::test_coverage_only_from_file`; `backend/tests/unit/test_junit_parser.py` | integration, unit |
| AC-09 | diff + tolak patch | `backend/tests/integration/test_review_apply.py::test_reject_keeps_main_unchanged`, `::test_apply_conflict_needs_changes`; `frontend/src/features/review/__tests__/DiffView.test.tsx`; `tests/e2e/reject-patch.spec.ts` | integration, UI, E2E |
| AC-10 | findings dengan evidence; reviewer read-only | `backend/tests/unit/test_contracts.py::test_review_output_schema`; `backend/tests/integration/test_review_step.py::test_findings_persisted_with_evidence`; `backend/tests/unit/test_policy.py::test_reviewer_profile_read_only` | unit, integration |
| AC-11 | status per kriteria + link evidence | `backend/tests/unit/test_evidence_verification.py` (UPA-01..UPA-11); `backend/tests/integration/test_user_proxy_step.py::test_report_links_evidence` (UPA-12) | unit, integration |
| AC-12 | wajib gagal → tidak accepted tanpa waiver/decision | `backend/tests/unit/test_acceptance_gate.py` (UPA-20..UPA-26); `backend/tests/integration/test_states_transition.py` (+ rencana `::test_no_path_to_completed_without_apply_approval`) | unit |
| AC-13 | R3 butuh approval valid; payload berubah → batal | `backend/tests/security/test_approvals.py::test_payload_hash_mismatch_409`, `::test_replay_consumed_rejected`, `::test_expired_rejected`, `::test_superseded_on_payload_change`, `::test_agent_cannot_decide` | security |
| AC-14 | cancel menghentikan worker/sandbox | `backend/tests/integration/test_cancellation.py::test_cancel_running_kills_sandbox`, `::test_cancel_queued_immediate`, `::test_cancel_during_llm_stream`, `::test_unkillable_marked`; `tests/e2e/cancel.spec.ts` | integration, E2E |
| AC-15 | secret tidak muncul di frontend/log/artefak | `backend/tests/security/test_secret_redaction.py::test_no_secret_in_logs_events_artifacts_api_prompts_sandbox_env`; `backend/tests/integration/test_settings_api.py::test_provider_key_masked` | security, integration |
| AC-16 | token/cost aktual atau "unavailable" | `backend/tests/unit/test_pricing_usage.py::test_estimated_vs_unavailable`; `backend/tests/integration/test_usage_api.py::test_task_usage_report` | unit, integration |
| AC-17 | restart tidak menghilangkan task running | `backend/tests/integration/test_recovery.py::test_expired_lease_marks_interrupted`, `::test_resume_skips_completed_steps`, `::test_duplicate_delivery_single_execution` | recovery |
| AC-18 | request fix → revalidation terbatas | `backend/tests/integration/test_remediation_cycle.py` (UPA-30..UPA-35); `tests/e2e/acceptance-remediation.spec.ts` (UPA-E2E) | integration, E2E |
| AC-19 | perubahan punya referensi task & audit | `backend/tests/integration/test_audit_trace.py::test_task_trace_complete`; `backend/tests/integration/test_core_audit.py::test_audit_events_cannot_be_updated_or_deleted` | integration, security |
| AC-20 | deployment terdokumentasi + health checks | CI job `clean-install`: `make up` dari checkout bersih → `scripts/healthcheck.sh --wait 180` (semua container healthy; `/health/ready` = ok untuk db, redis, sandbox_manager, migrations) + cek `docker compose config` hanya `caddy` mempublikasi port; `tests/e2e/happy-path.spec.ts` di atas stack yang sama | E2E/smoke |

## 6. CI gates (PRD §7.4)

| Area PRD | Tool | Job CI | Gate |
|---|---|---|---|
| Backend lint/style | Ruff (`ruff check`, `ruff format --check`) | `backend-lint` | wajib lulus |
| Backend type checks | Pyright (ADR-017) | `backend-types` | `app/core`, `app/workflow`, `app/tools`, `app/llm` mode standard; pengecualian di `pyproject.toml` |
| Dependency rule | `import-linter` (ARCHITECTURE §3.2) | `backend-lint` | kontrak layer lulus |
| Backend tests | Pytest + coverage (unit, integration, security, recovery) | `backend-test` (service Postgres 16 + Redis 7) | lulus; coverage dilaporkan (indikator) |
| Sandbox manager | Pytest; marker `docker` | `sandbox-test`, `sandbox-isolation` (runner dengan Docker) | lulus |
| Frontend tests | ESLint, `tsc --noEmit`, Vitest + RTL, `vite build` | `frontend` | komponen kritis punya test normal/error/loading/empty |
| Browser E2E | Playwright | `e2e` (compose stack, scripted) | happy path login → project → task → patch → test → diff → User Proxy |
| Database migrations | Alembic + DB uji | `backend-test` (`upgrade head`, `downgrade -1`, `upgrade head`) | lulus + strategi recovery terdokumentasi (RUNBOOK) |
| Security scans | gitleaks, pip-audit, npm audit, Semgrep, Trivy | `security-scan` | critical/high memblokir atau exception tertulis |
| Supply chain | lockfile check (`uv lock --check`, `npm ci`), image digest pin, SBOM | `release` | build repeatable; SBOM terlampir |
| Registry | `python3 scripts/validate_registry.py` + pytest | `backend-lint` | lulus |
| Clean install | `make up` + smoke | `clean-install` (nightly & pre-release) | AC-20 |

Perintah lokal: lihat `CLAUDE.md` (backend `uv run …`, frontend `npm …`, e2e `npx playwright test`). Marker pytest: `docker` (butuh Docker), `slow`, `e2e_backend`. `pytest -q` default **tidak** menjalankan marker `docker`.

## 7. Definition of done per phase

| Phase | DoD test (selain exit criteria PRD §13) |
|---|---|
| 0 — Discovery & contracts | Validator registry + test lulus; dokumen kontrak (ARCHITECTURE, DATA_MODEL, API_CONTRACTS, TOOL_CATALOG, SECURITY_MODEL, TEST_STRATEGY) disetujui; tidak ada blocker P0 terbuka |
| 1 — Foundation | CI hijau untuk lint/type/unit; `alembic upgrade head` di DB uji; `test_config.py`, `test_auth_flow.py` dasar, `test_core_audit.py`; clean-install smoke (`/health/ready`) lulus |
| 2 — Projects & IDE | AC-01, AC-02, AC-03 hijau (termasuk `test_cross_project.py`, `test_archive_import.py`, `test_paths.py`); UI state tests untuk Projects/IDE |
| 3 — First agent loop | AC-04, AC-05, AC-06, AC-15 (jalur prompt/log), AC-16, AC-19 hijau dengan ScriptedProvider; readiness gate `claude-smoke` lulus di staging dengan key nyata (tanpa secret di log) |
| 4 — Sandbox & tests | AC-07, AC-08, AC-14, AC-17 hijau; job `sandbox-isolation` (marker `docker`) lulus; **security gate** (SECURITY_MODEL §11) lulus sebelum egress/internet dibuka |
| 5 — Review & User Proxy | AC-09, AC-10, AC-11, AC-12, AC-13, AC-18 hijau; E2E `acceptance-remediation.spec.ts` membuktikan failed requirement → fix → revalidation |
| 6 — Product completeness | Semua AC P0 hijau di CI; P1 yang disepakati punya test; `a11y.spec.ts` tanpa pelanggaran serious/critical pada layar inti; review biaya & usability pilot |
| 7 — Staging delivery | AC-20 di staging (clean install + smoke + E2E); rollback dilatih & dicatat; restore drill backup lulus |
| 8 — Advanced specialists | Per agent baru: contract tests + scripted scenarios + eval set (AGENT_CATALOG §10) dengan metrik dibandingkan baseline |

**MVP DoD (PRD §13.1)** dari sisi test: happy path E2E lulus; semua AC P0 lulus di CI **dan** staging; tidak ada finding security critical; test isolasi & approval lulus; setiap task di E2E punya status, events, artefak, test evidence, dan usage record (atau `unavailable` eksplisit).

## 8. Pengukuran indikator pilot (PRD §1.3)

| Metrik | Cara ukur | Sumber data |
|---|---|---|
| Task success rate | task `completed` dengan semua kriteria wajib `passed` ÷ task tervalidasi, pada benchmark task yang disepakati | `tasks`, `acceptance_reports` |
| Test pass evidence | task yang mengklaim tested dan punya `test_runs` ÷ task implementasi (target ≥ 95%) | `test_runs`, `agent_runs` |
| Requirement traceability | kriteria wajib dengan status ≠ `pending` ÷ total kriteria wajib (target 100%) | `acceptance_criteria` |
| Human approval coverage | aksi R3 tereksekusi yang punya approval `consumed` ÷ aksi R3 (target 100%) | `tool_calls`, `approvals` |
| Reproducibility | task dengan rantai prompt→…→acceptance lengkap (test_audit_trace query) | `audit_events`, relasi tabel |
| Cost visibility | request model dengan `usage_records` ÷ request model (100%); `cost_status` tampil | `usage_records` |
