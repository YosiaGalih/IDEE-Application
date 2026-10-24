# KodeKantor — Tool Catalog (kontrak nama tool & profil sandbox)

Nama tool di sini adalah kontrak antara `agents/registry.yaml` (allowlist per agent), `app/tools/builtin/*` (implementasi), dan `ScriptedProvider` scenarios. Semua tool dieksekusi **hanya** melalui `ToolGateway.execute()`.

> **v2 (RANCANGAN):** tool perluasan K3 (Portal AI, GitHub App, Portfolio) dan permission profile 8 field (`network: provider_web`, `portfolio`, `data_sensitivity_max`) ada di bagian akhir dokumen ini: [v2 — Tool perluasan (K3) & permission profile v2](#v2--tool-perluasan-k3--permission-profile-v2). Bagian MVP di bawah tidak berubah.
>
> **v2.1 (RANCANGAN):** tool K10 (SDLC, Developer Workspace v2, tier M/L — `workspace.*`, `sdlc.propose_plan`, `merge_queue.enqueue`, `tests.run_selected`, `index.symbols_incremental`, `ci.get_checks`) dan profil sandbox v2.1 ada di bagian [v2.1](#v21--tool-sdlc-developer-workspace-v2-tier-ml-k10), berstatus `planned`. Terminal interaktif v2.1 adalah fitur manusia, **bukan** tool agent.
>
> **v2.2 (RANCANGAN):** perluasan `repo.detect_stack` (deteksi polyglot), profil per language pack (`<pack>.build/test/test_select/lint/fmt/run/package`, `sql.migrate_*`), `deps.install` lewat proxy per ekosistem, `deps.audit`, `env.up`/`env.status`/`env.down`, `catalog.list_services`, `contract.check`, `contract.mock`, `usage.query` ada di bagian [v2.2](#v22--tool-language-pack-dependensi-lingkungan-integrasi-kontrak-usage), berstatus `planned`. KONTRAK v2.2 tidak memuat daftar tool (tidak ada padanan K3/K10) — daftar v2.2 *(turunan)*; `deps.audit` disahkan masuk kontrak v2.2 oleh K21 OI-47, sisanya menunggu pengesahan Lead.

## Permission profile (field `permissions` di registry)

| Field | Nilai | Arti |
|---|---|---|
| `filesystem` | `none` · `read_only` · `test_only` · `docs_only` · `scoped_write` | `test_only`: tulis hanya path test (`tests/**`, `**/test_*.py`, `**/*_test.py`, `**/*.test.ts(x)`, `**/*.spec.ts(x)`, `**/__tests__/**`); `docs_only`: `**/*.md`, `docs/**`; `scoped_write`: semua path di worktree task kecuali denylist |
| `sandbox` | `deny` · `allow` | boleh memanggil tool R2 |
| `git` | `none` · `read` · `branch_commit` | `branch_commit` = commit di branch task |
| `network` | `deny` · `allowlist` | egress sandbox (P1) |
| `deployment` | `deny` · `staging_with_approval` | P2 |
| `approval_grant` | `deny` | **selalu deny** — validator registry menolak nilai lain |

## Builtin tools (MVP)

| Tool | Risk | Syarat permission | Input (ringkas) | Output | Modul |
|---|---|---|---|---|---|
| `repo.get_tree` | R0 | filesystem ≠ none | `{path=".", depth=3}` | daftar path (maks 500) | M04 |
| `repo.read_file` | R0 | filesystem ≠ none | `{path, start_line?, end_line?}` | teks bernomor baris (maks 64 KB, denylist ditolak) | M04 |
| `repo.search_text` | R0 | filesystem ≠ none | `{query, glob?, max_results=50, regex=false}` | `[{path,line,preview}]` | M04 |
| `repo.search_symbol` | R0 | filesystem ≠ none | `{name, kind?}` | `[{path,line,symbol,kind}]` | M04 |
| `repo.detect_stack` | R0 | – | `{}` | `Stack` | H41 |
| `repo.find_related_tests` | R0 | filesystem ≠ none | `{path}` | `[path]` | A04 |
| `git.status` | R0 | git ≥ read | `{}` | perubahan worktree | E27 |
| `git.diff` | R0 | git ≥ read | `{paths?}` | unified diff vs `base_commit` (maks 64 KB) | E27 |
| `git.log` | R0 | git ≥ read | `{limit=10}` | commit list | E27 |
| `tests.list_profiles` | R0 | – | `{}` | profil sesuai stack | C17 |
| `tests.get_run` | R0 | – | `{test_run_id}` | ringkasan + failing cases + tail log | C17 |
| `review.get_task_diff` | R0 | – | `{}` | diff + stats task | D22 |
| `acceptance.list_criteria` | R0 | – | `{}` | kriteria baseline aktif | A07 |
| `acceptance.list_evidence` | R0 | – | `{}` | index bukti nyata: test_runs, artifacts, findings, diff | A07 |
| `memory.retrieve_context` | R0 | – | `{query}` | ADR/decision relevan (fresh/stale) | G37 |
| `security.scan_secrets` | R0 | – | `{paths?}` | temuan pola secret di worktree (deterministik) | D23 |
| `code.write_file` | R1 | filesystem ∈ {test_only, docs_only, scoped_write} (+ path rule) | `{path, content}` | `FileChange` | B14 |
| `code.apply_patch` | R1 | idem | `{diff}` (unified) | `[FileChange]` | B14 |
| `code.delete_file` | R1 | scoped_write | `{path}` | `FileChange` | B14 |
| `git.commit_task` | R1 | git = branch_commit | `{message}` | `{commit}` (branch task saja) | E27 |
| `memory.store_decision` | R1 | – | `{title, content}` | `ProjectMemory` (redaksi) | G37 |
| `tests.run` | R2 | sandbox = allow | `{profile_id, args?}` | `TestRun` (exit code, counts, tail) — **bukti** | C17 |
| `env.inspect_runtime` | R2 | sandbox = allow | `{}` | versi runtime sandbox | C21 |
| `deps.install` | R2 | sandbox = allow & network = allowlist & `SANDBOX_EGRESS=allowlist` | `{manager:"pip"\|"npm"}` | TestRun-like | P1 |
| `git.push` | R3 | git = branch_commit | `{remote, branch}` | approval required | P2 (disabled) |
| `deploy.staging` | R3 | deployment = staging_with_approval | `{deployment_id}` | approval required | P2 |
| `submit_result` | – | semua agent LLM | output contract agent | terminal | ADR-013 |

**Selalu ditolak (R4, tidak pernah didaftarkan):** shell host, akses Docker, baca `.env`/kunci, ubah audit/policy, membuat approval, akses DB langsung, network bebas.

## Profil sandbox (`sandbox-images/sandbox-profiles.yaml`)

| profile_id | suite | image | argv | network | timeout default/max |
|---|---|---|---|---|---|
| `python.ruff` | lint | sandbox-python | `ruff check --output-format concise .` | none | 60/120 |
| `python.pytest` | unit | sandbox-python | `python -m pytest -q -p no:cacheprovider --junitxml=/workspace/.kk/junit.xml {args}` | none | 300/900 |
| `python.pytest_cov` | unit | sandbox-python | `python -m pytest -q -p no:cacheprovider --cov=. --cov-report=xml:/workspace/.kk/coverage.xml --junitxml=/workspace/.kk/junit.xml {args}` | none | 300/900 |
| `python.compile` | build | sandbox-python | `python -m compileall -q -x '(\.venv|\.kk)' .` | none | 120/300 |
| `python.env_info` | custom | sandbox-python | `python -c "…print versions…"` | none | 30/30 |
| `python.deps_install` | custom | sandbox-python | `pip install --target /workspace/.kk/site -r requirements.txt` | egress | 300/600 (P1) |
| `node.npm_test` | unit | sandbox-node | `npm test --silent` | none | 300/900 |
| `node.npm_build` | build | sandbox-node | `npm run build` | none | 300/900 |
| `node.npm_lint` | lint | sandbox-node | `npm run lint` | none | 120/300 |
| `node.env_info` | custom | sandbox-node | `node -e "…"` | none | 30/30 |
| `node.npm_ci` | custom | sandbox-node | `npm ci --ignore-scripts --no-audit --no-fund` | egress | 600/900 (P1) |

`{args}` hanya boleh berisi token yang cocok `args_pattern` profil (default `^[A-Za-z0-9_./:\-\[\]=]{1,200}$`, maks 10 token). Direktori `/workspace/.kk/` adalah lokasi artefak run dan di-*exclude* dari Git (`.git/info/exclude`).

Pemilihan profil otomatis (`backend/app/sandbox/profiles.py::profiles_for_stack`): Python → `python.ruff`, `python.pytest` (+`python.compile` bila tidak ada test); Node → `node.npm_lint` (bila script `lint`), `node.npm_test` (bila script `test`), `node.npm_build` (bila script `build`).

---

## v2 — Tool perluasan (K3) & permission profile v2

> Status: **RANCANGAN — belum diimplementasikan** (ADR-048). Sumber mengikat: `docs/PLATFORM_EXPANSION.md` §9b K3; ADR-038/039/040/041/042/045/046/047. Semua tool di bagian ini berstatus `planned` di `scripts/validate_registry.py` (`TOOL_CATALOG[...].status`) sehingga **tidak boleh** di-allowlist pada agent `enabled` sampai diimplementasikan di `app/tools/builtin/*` dengan unit test policy. Allowlist per agent: `agents/registry.yaml`; spesifikasi agent: `docs/AGENT_CATALOG.md` §12.

### Permission profile v2 (field `permissions` di registry)

| Field | Nilai | Arti | Catatan v2 |
|---|---|---|---|
| `filesystem` | `none` · `read_only` · `test_only` · `docs_only` · `scoped_write` | sama dengan v1 | agent Portal AI umumnya `none` (tidak menyentuh workspace repo) |
| `sandbox` | `deny` · `allow` | boleh memanggil tool R2 | validator: **setiap** tool R2 ⇒ `allow` (termasuk `docs.render_local`, `analysis.run`, `portfolio.build_site`, `code.execute_remote`) |
| `git` | `none` · `read` · `branch_commit` | sama dengan v1 | tool `github.*` R0/R1 ⇒ `read`; `github.push_branch`/`github.open_pr` ⇒ `branch_commit` |
| `network` | `deny` · `allowlist` · **`provider_web`** | `allowlist` = egress sandbox via proxy (P1); `provider_web` = boleh memakai **server tool Claude** (dieksekusi di infrastruktur Anthropic) | `provider_web` wajib untuk `web.search`, `web.fetch`, `code.execute_remote`, `docs.render_skills`, dan hanya boleh diberikan bila agent memegang salah satunya. Panggilan GitHub/vendor gambar/moderasi dilakukan worker — **bukan** egress sandbox, tidak diatur field ini |
| `deployment` | `deny` · `staging_with_approval` | sama dengan v1 (hanya F32) | publikasi portfolio bukan `deployment`; diatur R3 `portfolio.publish` |
| `portfolio` | `none` · `draft` | `draft` = boleh membuat/ubah item portfolio berstatus draft berlabel AI | wajib untuk `portfolio.draft_item`; tidak ada nilai "publish" — publikasi hanya lewat `portfolio.publish` (R3, K51) |
| `data_sensitivity_max` | `public` · `internal` · `confidential` | label data tertinggi (Ruang/project, ADR-045) yang boleh diproses agent; ToolGateway menolak run di Ruang berlabel lebih tinggi | `confidential` ⇒ dilarang memegang tool provider tidak-ZDR/pihak ketiga: `docs.render_skills`, `code.execute_remote`, `image.generate`, `image.edit` |
| `approval_grant` | `deny` | **selalu deny** | tidak berubah |

Entri registry lama (6 field) dibaca dengan default `portfolio: none`, `data_sensitivity_max: confidential`; entri v2 wajib menulis 8 field.

### Builtin tools v2 (K3)

`executor` dicatat di `tool_calls.executor`: `backend` (proses api/worker, tanpa eksekusi kode user), `sandbox` (sandbox-manager, profil tetap, network none), `worker` (antrean `integrations`, GitHub App), `provider` (server tool Claude atau `ImageProvider`/vendor moderasi; hasil tetap lewat allowlist, kuota, audit ToolGateway). Kolom *metering* = unit yang ditulis ke `capability_usage` (capability, unit, quantity, estimated_cost, `cost_status`).

| Tool | Risk | Executor | Syarat permission (validator) | Input (ringkas) | Output | Audit & metering | Agent |
|---|---|---|---|---|---|---|---|
| `capabilities.list` | R0 | backend | – | `{}` | `[{capability, enabled, risk, quota_remaining{unit, qty}, data_flow}]` untuk user saat ini | tanpa audit (read); tanpa metering | I42 |
| `web.search` | R0 | provider (Claude `web_search`, versi dipin di config) | `network: provider_web` | kueri dibuat model; `max_uses`, allow/block domain, lokasi dari **config server per mode**, bukan dari model | `web_search_tool_result` (url, title, page_age) + sitasi `web_search_result_location` di blok teks | `tool_calls` (query ter-redaksi, jumlah hasil); metering `web_search` × jumlah + token (tarif $10/1.000 pencarian per PLATFORM_EXPANSION §4.3) | I43 |
| `web.fetch` | R0 | provider (Claude `web_fetch`) | `network: provider_web` | `{url}` (aturan provider: URL yang sudah muncul di percakapan); `max_content_tokens` dari config | blok dokumen (teks/PDF) + sitasi | url + status di audit; metering token konten (verifikasi tarif saat deployment) | I43, K50 |
| `code.execute_remote` | R2 | provider (Claude code execution, container tanpa internet) | `sandbox: allow` + `network: provider_web` + data ≠ confidential | kode dibuat model + file input (Files API) | stdout/stderr, file keluaran (`file_id`) → `assets.store` | metering detik/jam container + token; `cost_status` jujur bila tarif belum diverifikasi | (alternatif I46, tidak di-allowlist) |
| `docs.render_skills` | R1 | provider (Agent Skills `pptx`/`docx`/`xlsx`/`pdf` via code execution) | `network: provider_web` + data ≠ confidential | skill id + instruksi/outline/konten + template | `file_id` → diunduh via Files API → `assets.store` (engine `claude_skills`) | audit skill_id, file_id, engine; metering token + waktu container | I44 |
| `docs.render_local` | R2 | sandbox `kodekantor/sandbox-docs` (python-pptx, python-docx, openpyxl, LibreOffice headless) | `sandbox: allow` | `{kind, document_spec (outline/konten JSON), template_id, theme}` | artefak file + thumbnail + `validation{opened_ok, checker}` (engine `local_sandbox`) | audit engine + checksum; metering detik eksekusi sandbox | I44, K50 |
| `assets.store` | R1 | backend | – | `{source: file_id\|sandbox_artifact, kind, provenance{provider, model, prompt_hash, engine}}` | `{asset_id, checksum, size}`; unduhan via `GET /assets/{id}/download` (URL bertanda tangan, `nosniff`) | audit `asset.created`; metering byte penyimpanan | I44, I46 |
| `image.generate` | R1 | provider (`ImageProvider`, vendor ADR-040a) | verdict moderasi lulus (allowlist memuat `moderation.check_image_prompt`) + kuota; data ≠ confidential (T15) | `{prompt, size, n, style, seed?, reference_asset_ids[], moderation_verdict_id}` | `{asset_ids[], revised_prompt?, provider, model, safety{flagged, reasons}}`; hasil `flagged` tidak ditampilkan | audit provenance (provider, model, hash prompt, user, waktu, C2PA); metering jumlah gambar + estimasi biaya per gambar (`pricing.yaml`) | I45 |
| `image.edit` | R1 | provider (`ImageProvider`) | sama dengan `image.generate` | `{asset_id, instruction, mask_asset_id?, moderation_verdict_id}` | sama dengan `image.generate` | sama | I45 |
| `moderation.check_text` | R0 | backend (+ vendor) | – | `{content, context, sensitivity, policy_version}` | `ModerationVerdict {verdict_id, content_hash, decision, rule_hits[], vendor_checked}` | audit hash konten + kategori (bukan isi); metering panggilan vendor; vendor hanya untuk ≤ internal | S52 |
| `moderation.check_image_prompt` | R0 | backend (+ vendor) | – | `{prompt, reference_asset_ids[], policy_version}` | `ModerationVerdict` (aturan lebih ketat: tokoh nyata, merek, karakter berhak cipta, seksual/minor, kekerasan); fail-closed bila vendor tidak tersedia | sama | S52, I45 |
| `kb.search` | R0 | backend (PostgreSQL FTS `simple` + `pg_trgm`) | scope koleksi milik user (runtime) | `{query, collection_ids[], limit ≤ 20}` | blok `search_result` (source = file/chunk ref, title, content, `citations.enabled`) | audit hash kueri; tanpa metering (token dihitung di LLM) | I42, I47 |
| `kb.get_chunk` | R0 | backend | scope koleksi milik user | `{chunk_id, neighbors?: 0..2}` | blok `search_result` | sama | I47 |
| `analysis.run` | R2 | sandbox `kodekantor/sandbox-docs` (pandas/matplotlib, network none, dataset read-only) | `sandbox: allow` | `{code, dataset_asset_ids[]}` | `{analysis_run_id, exit_code, stdout_tail, tables[], chart_artifacts[]}` — **bukti** | audit kode (hash) + exit code; metering detik eksekusi | I46 |
| `github.list_repos` | R0 | backend (GitHub App, token `metadata:read`) | `git ≥ read` | `{installation_id?, q?}` | `[{repo_id, full_name, private, default_branch}]` | audit ringan; token tidak dicatat (redaksi `ghs_`/`ghu_`) | J48 |
| `github.get_pr_status` | R0 | backend (token read) | `git ≥ read` | `{repo_id, pr_number}` | state, mergeable, ringkasan checks, review decision, komentar review (ter-redaksi; data tidak tepercaya) | audit ringan | J48, E29 |
| `github.get_repo_summary` | R0 | backend (token `contents:read`) | `git ≥ read` | `{repo_id, fields[]}` | README (potongan), bahasa, topik, bintang, tautan; repo privat → hanya field metadata yang dipilih user | audit ringan | K49 |
| `github.import_repo` | R1 | worker `integrations` | `git ≥ read` | `{installation_id, repo_id, branch?}` | `{project_id, commit, stack}`; clone tanpa token di URL/`.git/config` | audit `github.import`; token 1 repo `contents:read` ≤ 1 jam, tidak disimpan | J48 |
| `github.pull` | R1 | worker `integrations` | `git ≥ read` | `{project_id}` | fast-forward main lokal, atau branch `kk/sync-*` bila konflik (tidak menimpa) | audit `github.pull` | J48 |
| `github.push_branch` | **R3** | worker `integrations` | `git: branch_commit` + approval; **hanya J48**, tidak pernah agent llm | `{task_id, approval_id}` (payload approval: repo + branch `kk/task-*` + `head_commit`) | `{remote_branch, head_commit}` | approval terikat payload hash; gitleaks pre-push; blokir `.github/workflows/**`; tanpa force | J48 |
| `github.open_pr` | **R3** | worker `integrations` | `git: branch_commit` + approval; **hanya J48** | `{task_id, approval_id}` (judul/deskripsi = `PRDraft` E29 yang disetujui; hash di payload) | `{pr_number, pr_url}` | approval terikat; merge tetap manusia di GitHub | J48 |
| `portfolio.get_profile` | R0 | backend | – | `{fields?}` | profil + item (dengan `visibility`) | tanpa audit (read) | K49, K50 |
| `portfolio.draft_item` | R1 | backend | `portfolio: draft` | `{type, content, evidence_refs[], source}` | `{item_id, visibility: draft, source: ai_draft, ai_label: true}` — tidak pernah publik | audit `portfolio.item.drafted` (agent_id, evidence_refs) | K49, K50 |
| `portfolio.privacy_check` | R0 | backend | – | `{version_id}` | temuan: kontak tidak bertanda publik, isi repo privat, pola secret | audit hasil cek | K51 |
| `portfolio.build_site` | R2 | sandbox (Jinja2 → statis, network none) | `sandbox: allow` | `{version_id, template_id}` | artefak situs + `snapshot_hash` (HTML + daftar aset) | metering detik eksekusi | K51 |
| `portfolio.publish` | **R3** | backend (target `static`, atau GitHub Pages via adapter GitHub App) | approval; **hanya K51** | `{version_id, target, approval_id}` | `{publication_id, url}` | approval terikat `snapshot_hash`; audit `portfolio.published` | K51 |
| `submit_result` | – | terminal | agent llm/profile (aktif atau target) | output contract agent | terminal | **pengecualian ADR-047:** agent dengan `output_contract: CitedAnswer` (I43, I47) tidak memakainya — jawaban = teks bersitasi | – |

**Aturan validator yang berkaitan dengan tool v2** (detail: `docs/AGENT_CATALOG.md` §12.2): server tool ⇒ `network: provider_web`; R2 ⇒ `sandbox: allow`; R3 baru hanya J48 (`github.push_branch`, `github.open_pr`) dan K51 (`portfolio.publish`), tidak pernah pada agent llm/profile; `portfolio.draft_item` ⇒ `portfolio: draft`; `confidential` ⇒ tanpa `docs.render_skills`/`code.execute_remote`/`image.*`; `image.*` ⇒ `moderation.check_image_prompt` di allowlist yang sama; tool berstatus `planned` tidak boleh di agent `enabled`.

**Tambahan daftar selalu ditolak (R4, tidak pernah didaftarkan) untuk v2:** force push, hapus branch remote, merge PR, mengubah `.github/workflows/**`, publikasi portfolio tanpa approval, mengirim data `confidential` ke Skills/Files API/vendor gambar, menyimpan token GitHub atau private key di DB/log/sandbox/prompt.

---

## v2.1 — Tool SDLC, Developer Workspace v2, tier M/L (K10)

> Status: **RANCANGAN / Proposed (22 Okt 2026) — belum diimplementasikan.** Sumber mengikat: `docs/SDLC_SCALE_DESIGN.md` §6 **K10** (tool baru), K7 (ADR-057..062, *Proposed*), K12 (T19–T23). Desain handler: `docs/IMPLEMENTATION_DESIGN_V2.md` §13.3.12; agent pemegang: `docs/AGENT_CATALOG.md` §13.5; endpoint user: `docs/API_CONTRACTS.md` §13. Semua tool di bagian ini berstatus **`planned`**. Daftar tool validator (`scripts/validate_registry.py::TOOL_CATALOG`) **belum** memuatnya dan registry tidak disunting di v2.1 — tool ditambahkan ke validator dengan status `planned` pada registry versi berikutnya (WP-13.0/WP-13.4) dan baru boleh di-allowlist pada agent `enabled` setelah diimplementasikan di `app/tools/builtin/*` dengan unit test policy (aturan V6).

**Tidak ada field permission baru** di v2.1 (tetap 8 field v2). Aturan baru bersifat per tool: `user_only` (tidak pernah di allowlist agent), `system_only` (dipanggil service/handler, bukan agent llm/profile), dan pembatasan pemegang (`only:`).

### Builtin tools v2.1 (K10)

Kolom *Risk*, *Executor*, dan *Catatan K10* mengikuti K10; kolom lain melengkapi *(turunan dari desain §13)*.

| Tool | Risk | Executor | Syarat permission (validator, usulan) | Prasyarat runtime | Input (ringkas) | Output | Audit & bukti | Pemanggil | Status |
|---|---|---|---|---|---|---|---|---|---|
| `workspace.create_file` | R1 | backend | `user_only` — ditolak di allowlist agent mana pun | RBAC `workspace:write`; target `repo` read-only bila merge queue repo aktif; `task:<id>` hanya pemegang take-over aktif; path lewat `TaskWorkspace.resolve(for_write=True)` | `{path, type: file\|dir, content?, target, editor_session}` | `FileVersion {path, sha256, size, target, op_id}`; 409 bila path ada | audit `workspace.file_created`; jurnal undo sesi (FR-235) | **user** via `POST /projects/{id}/files` | planned |
| `workspace.rename` | R1 | backend | `user_only` | sama; `expected_sha256` untuk file | `{from_path, to_path, expected_sha256?, target, editor_session}` | `FileVersion`; 409 `VERSION_CONFLICT` + `Merge3View` | audit `workspace.file_renamed` | **user** via `PATCH /projects/{id}/files` | planned |
| `workspace.delete` | R1 | backend | `user_only` | sama; file wajib `expected_sha256`, folder wajib `recursive=true` | `{path, expected_sha256?, recursive?, target, editor_session}` | `{}`; 409 `VERSION_CONFLICT` | audit `workspace.file_deleted` | **user** via `DELETE /projects/{id}/files` | planned |
| `sdlc.propose_plan` | R0 | backend | `only: A03`; `filesystem ≠ none` | work item target ada di project task/run; tanpa menulis apa pun (*dry-run*) | `{plan: ProgramPlan}` | `{valid, errors: [{path, code: DAG_CYCLE\|DUPLICATE_KEY\|UNKNOWN_REF\|UNKNOWN_REPO\|LIMIT, detail}], diff: {added, changed, untouched}, plan_sha256}` | `tool_calls` (hash plan, jumlah item); tidak membuat task (FR-223) | A03 | planned |
| `merge_queue.enqueue` | R1 | backend | `system_only` — tidak pernah pada agent llm/profile | approval `apply_changes` untuk task berstatus `approved`, payload hash cocok, sudah di-`consume()` atomik; merge queue aktif untuk repo; satu entri aktif per `(repository_id, source_branch)` | `{task_id, approval_id}` | `MergeQueueEntry {id, position, status: queued, approved_patch_id}` | audit `merge_queue.enqueued`; ditolak bila approval tidak valid/kedaluwarsa/`superseded` (FR-225) | handler approval `apply_changes` (aksi sistem; bila dicatat sebagai tool call: ORCH, `tool`) | planned |
| `tests.run_selected` | R2 | sandbox | `sandbox: allow` (V8); `filesystem ≠ none` | profil `*.test_select` stack project; argv exec-form, `test_id`/`path` tervalidasi pola (tanpa shell); network none | `{selector: {scope: all\|file\|test, path?, test_id?}, profile_id?, args?}` | `TestRun` (exit code, counts, failing cases, tail log) — **bukti**; `initiated_by` = `agent` (lewat gateway) atau `user` (lewat service) | `test_runs` + `tool_calls` (FR-240) | C17; user via `POST /projects/{id}/test-runs` (service, bukan gateway) | planned |
| `index.symbols_incremental` | R2 | sandbox | `sandbox: allow` (V8) | profil `index.ctags_incremental` (ctags/tree-sitter), network none, workspace read-only; sparse checkout bila `SPARSE_CHECKOUT_ENABLED` | `{repo_alias, since_commit?, paths?}` | `{index_ref, base_commit, files_indexed, symbols, duration_ms}` — hanya path yang berubah sejak `repositories.index_commit`; hasil ke `repo_index_entries` (tabel v1) dan dibaca `repo.search_symbol` | metering detik sandbox; tanpa embedding (ADR-046, ADR-062) | job indexer sistem (rekomendasi, `AGENT_CATALOG.md` §13.5); A04 hanya bila `sandbox: allow` disetujui | planned |
| `ci.get_checks` | R0 | worker integrations | `git ≥ read` | GitHub App permission `checks: read` (G9); token installation tidak pernah ke agent/log | `{repository_id, commit_sha}` | `[CiCheckResult {check_name, app_slug, status, conclusion, details_url, completed_at}]` | `ci_check_results` (sumber `webhook`/`poll`); isi check = **data tidak tepercaya** (FR-230). Aturan K13 (OI-23): bagi merge queue, CI sah hanya untuk `head_commit` yang sudah di-push dan rebase fast-forward; juga dipakai untuk CI pasca-merge atas `result_commit` | J48; F30 (setelah aktivasi P13); merge queue (service, **WP-13.12**) | planned |

**Bukan tool (dicatat agar tidak disalahpahami):**

- **Terminal interaktif** (FR-241, ADR-059) adalah sesi PTY untuk **manusia** lewat `POST /projects/{id}/terminal-sessions` + WebSocket; tidak ada tool `terminal.*`. Bagi agent, shell/perintah bebas tetap **R4** (daftar "Selalu ditolak" di atas tetap berlaku); agent tetap hanya memilih profil allowlist (FR-078).
- **Take-over** (`POST /tasks/{id}/takeover`), **run user** (`POST /projects/{id}/runs`), dan **Git manual user** (stage/commit/branch/discard) adalah aksi user lewat service yang diaudit, bukan tool agent.
- **Memajukan branch target** dilakukan `GitAdapter.advance_target_ref` yang hanya menerima `MergeQueueLease` dari `MergeQueue.process_next` (T23) — tidak diekspos sebagai tool.

### Profil sandbox v2.1 (rancangan, `sandbox-images/sandbox-profiles.yaml`)

Pola nama dari `IMPLEMENTATION_DESIGN_V2.md` §13.2 (`run.*`, `*.test_select`, `fmt.*`, `lint.*_file`, `index.ctags_incremental`, `terminal.shell`); contoh `profile_id` konkret bertanda *(turunan)*. Semua: user 10001, `cap_drop ALL`, rootfs read-only + tmpfs, limit CPU/RAM/PID profil sandbox, **network none**.

| Pola / profile_id | Suite | Dipakai oleh | Catatan |
|---|---|---|---|
| `run.*` — mis. `python.run_module`, `node.dev_server` | `run` (CHECK `test_runs.suite` + `run`, OI-24) | user (FR-239) | dev server dilayani lewat preview terisolasi ADR-008 (`preview-<run8>.<APP_DOMAIN>`); output streaming + tersimpan |
| `*.test_select` — mis. `python.pytest_select`, `node.test_select` *(turunan)* | `unit` | `tests.run_selected` (C17, user) | memetakan selector all/file/test ke argv tetap; token tervalidasi `args_pattern` |
| `fmt.*` — mis. `python.ruff_format` *(turunan)* | `lint` | format on save (FR-238) | hasil ditampilkan sebagai patch yang dapat ditolak user; tidak pernah menulis langsung |
| `lint.*_file` — mis. `python.ruff_file`, `node.eslint_file` *(turunan)* | `lint` | lint on save (FR-238) | satu file; hasil = diagnostik |
| `index.ctags_incremental` | `custom` | `index.symbols_incremental` | inkremental dari `since_commit`; tanpa network |
| `terminal.shell` | – (bukan run) | **manusia saja** via sandbox-manager `POST /v1/terminals` | argv `["/bin/bash","--noprofile","--norc","-i"]`, `Tty=true`; durasi `TERMINAL_SESSION_MAX_MINUTES`, idle `TERMINAL_IDLE_MINUTES`; tidak dapat dipanggil `ToolGateway` |

### Aturan validator & risk level tambahan (usulan untuk registry berikutnya)

- `workspace.*` = `user_only`: muncul di allowlist agent mana pun → validator menolak.
- `merge_queue.enqueue` = `system_only`: tidak boleh pada agent `llm`/`profile` (aktif maupun target), termasuk E28.
- `sdlc.propose_plan` hanya pada A03; `ci.get_checks` ⇒ `git ≥ read`; tool R2 baru (`tests.run_selected`, `index.symbols_incremental`) ⇒ `sandbox: allow` (V8 berlaku apa adanya).
- **Tambahan daftar selalu ditolak (R4) untuk v2.1:** terminal/perintah bebas oleh agent; memajukan branch target di luar merge queue; menetapkan status work item atau fase SDLC; membuat/mengubah koneksi SSO, token SCIM, atau token node; menulis ke worktree yang sedang di-take-over user; membuat task dari `ProgramPlan` tanpa persetujuan user.

---

## v2.2 — Tool language pack, dependensi, lingkungan integrasi, kontrak, usage

> Status: **RANCANGAN / Proposed (22 Okt 2026) — belum diimplementasikan.** Sumber mengikat: `docs/POLYGLOT_INTEGRATION_DESIGN.md` §7 **KONTRAK v2.2** (K14 FR, K17 ADR-063..068, K19 data/API/konfigurasi, K20 T24–T28) dan §1 (komponen pack: deteksi stack diperluas di H41 `repo.detect_stack`; profil tetap `*.deps_install`, `*.build`, `*.test`, `*.test_select`, `*.lint`, `*.fmt`, `*.run`, `*.package` dengan argumen ter-allowlist). **KONTRAK v2.2 tidak memuat daftar tool** (tidak ada padanan K3/K10): seluruh nama tool di bagian ini *(turunan)* dari FR/K19/PID dan menunggu pengesahan Lead (WP-15.0 / WP-16.0). Semua berstatus **`planned`**; belum ada di `scripts/validate_registry.py::TOOL_CATALOG`; tidak boleh di-allowlist pada agent `enabled` sebelum diimplementasikan dengan unit test policy (V6). Desain handler: `docs/IMPLEMENTATION_DESIGN_V2.md` §14.3.11; agent pemegang: `docs/AGENT_CATALOG.md` §14; endpoint user: `docs/API_CONTRACTS.md` §14.

**Tidak ada field permission baru** (tetap 8 field v2). Aturan V8 berlaku apa adanya: setiap tool yang dieksekusi di sandbox = R2 ⇒ `sandbox: allow`. Profil pack bukan tool baru: dieksekusi lewat `tests.run` / `tests.run_selected` (dan `deps.install`) dengan `profile_id` dari pack aktif.

### Builtin tools v2.2 *(turunan)*

| Tool | Risk | Executor | Syarat permission (validator, usulan) | Prasyarat runtime | Input (ringkas) | Output | Audit & bukti | Pemanggil | Status |
|---|---|---|---|---|---|---|---|---|---|
| `repo.detect_stack` (diperluas; peran "`pack.detect`") | R0 | backend | – (seperti v1) | hanya membaca nama file & file penanda kecil lewat resolver workspace (denylist berlaku); tidak menjalankan build tool | `{path?: ".", max_files?}` | `StackReport {stacks: [{path, pack_id, framework, package_manager, entrypoints, lockfile, confidence, unsupported_targets}], primary, monorepo_tool, truncated}` — `primary` = bentuk `Stack` v1 (kompatibel-mundur) | tanpa audit (read); hasil disimpan `project_stacks` oleh service | H41, A04; job deteksi sistem | v1 `mvp`; perluasan `planned` (WP-15.0) |
| `tests.run` / `tests.run_selected` dengan profil pack | R2 | sandbox | `sandbox: allow` (V8); `filesystem ≠ none` | pack `active` **dan** `agent_enabled` (FR-266) untuk stack file yang diubah; `profile_id` milik pack itu; argumen cocok `args_pattern` profil; network none | `{profile_id: "<pack>.<kind>[_varian]", args?, selector?}` | `TestRun` + `language_pack`, `report_status`; laporan dinormalisasi ke JUnit XML — **bukti** (FR-264) | `test_runs` + `tool_calls`; 409 `PACK_NOT_ENABLED` | C17; B14 (`<pack>.lint`/`.fmt` saja); C20 (reproduksi); user lewat service | `planned` (WP-15.0..15.9) |
| `deps.install` (diperluas) | R2 | sandbox (network `kk-egress` → `dep-proxy`) | `sandbox: allow` + `network: allowlist` (seperti v1) | `SANDBOX_EGRESS=allowlist`; ekosistem pack ada di `DEP_PROXY_URLS`; preflight lockfile/namespace/blocklist lulus; hanya profil `<pack>.deps_install` | `{pack_id, path}` (menggantikan `{manager: "pip"\|"npm"}` v1) | `TestRun`-like (exit code, paket terpasang, tail log) | audit `deps.installed` (+ sha256 lockfile); 409 `LOCKFILE_REQUIRED` / `DEPENDENCY_REJECTED` (T24) | C17, B14; user lewat `POST /projects/{id}/deps/install` | v1 P1; perluasan `planned` (WP-15.0) |
| `deps.audit` | R2 | sandbox (network none) | `sandbox: allow` | snapshot advisory read-only (disinkronkan job OPS harian) di-mount ke sandbox; lockfile ada | `{pack_id, path}` | `[{ecosystem, package, version, advisory_id, severity, fixed_in}]` | `findings(reviewer_type=security)`; `critical` memblokir gate S9 (pola `security.scan_secrets`) | D24 (P15, WP-15.10) | `planned` — **usulan** (OI-47) |
| `catalog.list_services` | R0 | backend | – | scope program/project task | `{program_id?}` | `[Service {slug, kind, pack_id, interfaces, dependencies}]` (tanpa data env) | tanpa audit (read) | A05, B11, C18 | `planned` (WP-16.2) |
| `contract.check` | R2 | sandbox (`kodekantor/sandbox-contracts`, network none) | `sandbox: allow` | interface terdaftar; dokumen kontrak = data tidak tepercaya, di-parse/di-diff hanya di sandbox | `{interface_id, base_commit?, head_commit}` | `ContractCheck {result: compatible\|breaking\|invalid\|error, changes, affected_consumers}` | `contract_checks` + `tool_calls`; hasil breaking dipakai merge queue (FR-273) | B11 (P16), C18; merge queue lewat service | `planned` (WP-16.2) |
| `contract.mock` | R2 | sandbox (`sandbox-contracts`) | `sandbox: allow` | idem | `{interface_id, commit?}` | artefak mock/stub (dipakai test consumer & mode tier S) | artefak + `tool_calls` (FR-274, FR-276) | C18, B11 | `planned` (WP-16.2) |
| `env.up` | R2 | worker → sandbox-manager node (`/v1/envs`) | `sandbox: allow` | `INTEGRATION_ENV_ENABLED=true`, tier M/L, pemilik task punya `env:run`; spec lulus `EnvSpecPolicy` (validasi ganda); kuota & TTL | `{spec: IntegrationEnvSpec}` (tanpa field image bebas/privileged/mount/network/port/secret nyata) | `{env_id, status, services}` | `integration_envs` + audit `integration_env.created`; 422 `ENV_SPEC_REJECTED`, 429 `ENV_QUOTA_EXCEEDED` | C18, C19 (setelah G16) | `planned` (WP-16.1) |
| `env.status` | R0 | backend | – | env milik task/project yang sama | `{env_id}` | status, kesehatan per service, `test_runs` env, tail log teredaksi (≤ 8 KB/service) | tanpa audit (read) | C18, C19, C20 | `planned` (WP-16.1) |
| `env.down` | R1 | backend → sandbox-manager | – | env dibuat dalam task yang sama; idempoten | `{env_id}` | `{status: tearing_down}` | audit `integration_env.torn_down` | C18, C19 | `planned` (WP-16.1) |
| `usage.query` | R0 | backend | – | scope = task/project run saat ini (tanpa data organisasi atau user lain) | `{group_by?: model\|agent, period?: task\|today}` | `UsageSummary` terbatas (token aktual, biaya estimasi berlabel) | tanpa audit (read) | ORCH (kesadaran budget), A03 (estimasi biaya rencana) | `planned` (WP-6.9) |

**Bukan tool (dicatat agar tidak disalahpahami):** monitoring biaya & token (`usage.event`, rollup, alert, rekonsiliasi) adalah fungsi **core** yang dijalankan service/worker, bukan aksi agent; kunci Admin API tidak pernah dapat diakses tool mana pun. Registrasi/aktivasi pack, katalog service (tulis), pengecualian breaking change (`merge_queue:manage`), dan budget adalah aksi **user** lewat API (`API_CONTRACTS.md` §14), bukan tool agent.

### Profil sandbox v2.2 (rancangan, dibangkitkan dari `config/language-packs/<pack>.yaml`)

Pola nama `<pack>.<kind>[_varian]`; `sandbox-images/sandbox-profiles.yaml` dibangkitkan dari manifest (`scripts/render_pack_profiles.py --check` di CI) sehingga sandbox-manager tetap membaca **satu daftar tetap** (ADR-009, diperluas ADR-065). Semua: user 10001, `cap_drop ALL`, rootfs read-only + tmpfs, limit CPU/RAM/PID profil (override ≤ batas tier; pack `heavy` hanya di node berlabel heavy — T27), **network none** kecuali `*.deps_install` (`kk-egress` → dep-proxy). Contoh `profile_id` konkret *(turunan)*, versi alat dipin di WP.

| Pack | Tingkat | `deps_install` (registry → proxy) | build | test / test_select (format laporan → JUnit XML) | lint / fmt | run / package | Catatan |
|---|---|---|---|---|---|---|---|
| `python` | L1 | `python.deps_install` (PyPI; `requirements*.txt`/`uv.lock`/`poetry.lock`) | `python.compile` | `python.pytest`, `python.pytest_select` (JUnit) | `python.ruff` / `python.ruff_format` | `python.run_module` / `python.package_wheel` | profil v1 tetap identik |
| `node` | L1 | `node.npm_ci` (npm; `package-lock.json`/`pnpm-lock.yaml`) | `node.npm_build` | `node.npm_test`, `node.test_select` (JUnit reporter) | `node.npm_lint` / `node.prettier` | `node.dev_server` / `node.npm_pack` | profil v1 tetap identik |
| `jvm` | L1 | `jvm.maven_go_offline`, `jvm.gradle_deps` (Maven Central, Gradle Plugin Portal; lockfile Gradle / dependency lock) | `jvm.maven_build`, `jvm.gradle_build` | `jvm.maven_test`, `jvm.gradle_test`, `jvm.test_select` (Surefire/Gradle JUnit XML) | `jvm.checkstyle` / `jvm.spotless` | `jvm.spring_run` / `jvm.package_jar` | `heavy` |
| `dotnet` | L1 | `dotnet.restore` (NuGet; `packages.lock.json`) | `dotnet.build` | `dotnet.test`, `dotnet.test_select` (TRX → JUnit) | `dotnet.format_verify` / `dotnet.format` | `dotnet.run` / `dotnet.publish` | Linux saja; .NET Framework → CI eksternal |
| `go` | L1 | `go.mod_download` (Go module proxy; `go.sum`) | `go.build` | `go.test`, `go.test_select` (`go test -json` → JUnit) | `go.golangci_lint` / `go.gofmt` | `go.run` / `go.build_binary` | `GOFLAGS=-mod=readonly` |
| `php` | L1 | `php.composer_install` (Packagist; `composer.lock`) | `php.lint_syntax` | `php.phpunit`, `php.test_select` (JUnit) | `php.cs_fixer_check` / `php.cs_fixer` | `php.artisan_serve` / `php.package_archive` | Laravel |
| `ruby` | L2 | `ruby.bundle_install` (RubyGems; `Gemfile.lock`) | `ruby.bundle_check` | `ruby.rspec` (JUnit formatter) | `ruby.rubocop` | – | tanpa run/eval penuh (L2) |
| `cpp` | L2 | `cpp.conan_install` / `cpp.vcpkg_install` (Conan Center / vcpkg; lockfile) | `cpp.cmake_build` | `cpp.ctest` (CTest XML → JUnit) | `cpp.clang_tidy` | – | `heavy` |
| `react-native` | L2 | `react-native.npm_ci` (npm, Maven) | `react-native.build_android` | `react-native.jest` (JUnit) | `react-native.eslint` | – | Android saja; iOS → CI eksternal (FR-270); `heavy` |
| `flutter` | L2 | `flutter.pub_get` (pub.dev, Maven; `pubspec.lock`) | `flutter.build_web`, `flutter.build_apk` | `flutter.test` (`--machine` json → JUnit) | `flutter.dart_analyze` | – | web + Android; iOS/macOS & emulator → CI eksternal; `heavy` |
| `sql` | L1 (migrasi & test) | – (image DB) | `sql.migrate_flyway`, `sql.migrate_liquibase` | `sql.test_postgres`, `sql.test_mysql` (DB lokal di container, network none — P15); SQL Server dev lewat lingkungan integrasi (P16) | `sql.lint_sqlfluff` | – | kategori `database` |
| *(kontrak)* | – | – | – | `contracts.provider_test` (di env/sandbox) | `contracts.validate_*` | `contracts.diff_*`, `contracts.mock_*` | image `sandbox-contracts` |

### Aturan validator & risk level tambahan (usulan untuk registry berikutnya)

- Profil pack hanya boleh dipanggil untuk pack `active`; pada jalur agent juga `agent_enabled` (FR-266) — dicek runtime (`PACK_NOT_ENABLED`), bukan di registry.
- `network: egress` pada profil hanya untuk kind `deps_install` (validator manifest); `deps.install` ⇒ `sandbox: allow` + `network: allowlist` (aturan v1 tetap).
- `deps.audit`, `contract.check`, `contract.mock`, `env.up` = R2 ⇒ `sandbox: allow` (V8). D24 memegang `deps.audit` ⇒ `sandbox: deny → allow` = perluasan akses (§11 #4 AGENT_CATALOG; review AG-13) — **tanpa** `network: allowlist` karena advisory dibaca dari snapshot lokal *(turunan)*.
- `env.up`/`env.down` hanya pada agent `profile`/`tool` dengan `sandbox: allow` (C18, C19); tidak pernah pada agent `llm` tanpa sandbox; spec dari agent tetap divalidasi penuh (data tidak tepercaya).
- **Tambahan daftar selalu ditolak (R4) untuk v2.2:** container/env dengan field Docker bebas (image, privileged, mount host, network mode, port publish, device, capability); egress dari profil selain `*.deps_install`; menambah upstream registry atau mematikan namespace pinning; membaca/memakai kunci Admin API; memasukkan secret nyata ke spec/seed env; memberi pengecualian breaking change atau mengubah budget/kill switch.
