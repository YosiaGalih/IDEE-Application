# agents/ — Agent registry KodeKantor

Dua file registry (sampai WP-9.0):

| File | Schema | Entri | Dipakai | Validator |
|---|---|---|---|---|
| `registry.yaml` | 1 | 42 | runtime snapshot (`AGENT_REGISTRY_PATH`, `seed-agents`) | `scripts/validate_registry_v1.py` |
| `registry.v2.yaml` | 2 | 53 | rancangan v2 (ADR-048), **sumber kebenaran katalog** | `scripts/validate_registry.py` (file schema 1 otomatis didelegasikan ke validator v1) |

`registry.v2.yaml` adalah **sumber kebenaran** katalog agent. Sejak **schema v2** (ADR-048, rancangan 21 Okt 2026) berisi **53 entri**: 41 specialist PRD §6 (A01–H41) + Orchestrator (`ORCH`, PRD §6.1) + 11 agent perluasan K2 (`docs/PLATFORM_EXPANSION.md` §9b): I42–I47 (Portal AI), J48 (GitHub App), K49–K51 (Portfolio), S52 (Content Safety). Semua agent v2 masih `mvp_mode: planned`, `enabled: false` — **belum diimplementasikan**. Penjelasan lengkap, tabel induk 53 entri, kartu spesifikasi, dan rencana aktivasi P9–P12: `docs/AGENT_CATALOG.md` (§12 = v2).

Backend memuat registry lewat `AGENT_REGISTRY_PATH` dan `python -m app.cli seed-agents` menyinkronkannya ke tabel `agent_profiles` (DATA_MODEL). **Catatan:** loader backend (`backend/app/agents/registry.py`) masih hanya menerima `schema_version: 1`, sehingga `registry.yaml` tetap v1 dan v2 disimpan terpisah di `registry.v2.yaml`. WP-9.0 memperbarui loader lalu mengganti `registry.yaml` dengan isi v2 (`docs/AGENT_CATALOG.md` §12.9 #1).

## Validasi

```bash
python3 scripts/validate_registry.py                 # default agents/registry.v2.yaml; exit 0 = valid, 1 = error validasi, 2 = file/usage error
python3 scripts/validate_registry.py agents/registry.yaml   # schema 1 → validate_registry_v1.py
backend/.venv/bin/python -m pytest -q -p no:cacheprovider scripts/test_validate_registry.py scripts/test_validate_registry_v1.py
```

Validator hanya bergantung pada PyYAML + stdlib, membaca `backend/app/agents/contracts.py` via `ast` (tanpa import), dan menanam salinan mekanis PRD §6 (`PRD_CATALOG`), K2 (`K2_CATALOG`), serta TOOL_CATALOG v2 (risk, executor, prasyarat, status). Jalankan setiap kali registry, `docs/TOOL_CATALOG.md`, `contracts.py`, atau §9b PLATFORM_EXPANSION berubah.

## Skema entri (`schema_version: 2`)

### Field wajib (sama dengan v1)

| Field | Isi | Sumber |
|---|---|---|
| `agent_id` | `A01`…`H41`, `ORCH`, atau ID K2 `I42`…`I47`, `J48`, `K49`…`K51`, `S52` (unik; pola `^(?:[A-H]\d{2}\|[IJKS]\d{2}\|ORCH)$`) | PRD §6, K2 |
| `slug` | snake_case unik (mis. `user_proxy`, `web_research`); entri K2 verbatim dari K2 | turunan / K2 |
| `name`, `description`, `capabilities` | entri PRD: **verbatim** PRD (validator menolak perbedaan); entri K2: `name` verbatim K2, `description` & `capabilities` turunan (gaya `namespace.function`, mis. `research.synthesize_with_citations`) | PRD §6 / K2 |
| `group` | v1: `planning_intent` · `software_development` · `testing_debugging` · `quality_security` · `git_collaboration` · `devops_operations` · `documentation_knowledge` · `specialized_engineering` · `orchestration`; **v2:** `ai_assistant` · `integrations` · `portfolio` | PRD §6, K2 |
| `version` | int ≥ 1; naik bila prompt/tools/permission/kontrak berubah (E29 = 2 di v2) | — |
| `priority` | `P0` · `P1` · `P2` (entri v2: prioritas *di dalam perluasan*) | PRD / ADR-048 |
| `mvp_mode` | `llm` · `profile` · `tool` · `planned` | ADR-012 |
| `runtime_agent` | wajib untuk `profile`, selain itu `null` | ADR-012 |
| `enabled` | `true` ⇔ `mvp_mode ≠ planned` | — |
| `model_tier` | `fast` · `default` · `complex` → `CLAUDE_MODEL_*` | PRD §7.3.1 |
| `purpose`, `triggers` | turunan (A07 verbatim PRD §6.2); entri v2 berbahasa Indonesia | — |
| `tools` | allowlist; hanya nama di `docs/TOOL_CATALOG.md` (MVP + v2/K3); aturan `submit_result` lihat di bawah | TOOL_CATALOG, ADR-013/047 |
| `permissions` | 6 field v1 atau 8 field v2 (lihat bawah); **`approval_grant: deny` selalu** | TOOL_CATALOG, PRD §10.4 |
| `input_contract` / `output_contract` | aktif: nama kelas di `contracts.py` (profile = milik runtime; tool = `null`); planned ber-`target_mode`: nama **konseptual** (+ `contract_status: planned`); planned tanpa target: `null` | ADR-013 |
| `limits` | `{max_iterations (1..40), timeout_seconds}` (+ `tool_quota` untuk entri v2); llm/profile (aktif atau target) timeout 300–900 s; target `tool` ⇒ `max_iterations: 1` | PRD §6.2, §7.3.1 |
| `inputs_tools_note`, `output_note`, `access_note` | entri PRD: verbatim PRD (ORCH: turunan); entri K2: turunan bertanda `(K2)` | PRD §6 |

### Field opsional baru (v2)

| Field | Nilai | Aturan validator |
|---|---|---|
| `target_mode` | `llm` · `profile` · `tool` | hanya untuk `mvp_mode: planned`; mode yang dituju saat diaktifkan |
| `phase` | `P8` · `P9` · `P10` · `P11` · `P12` | wajib bila ada `target_mode`; dilarang tanpa `target_mode` |
| `contract_status` | `planned` | wajib untuk planned ber-`target_mode` (bersama kontrak konseptual); dilarang untuk agent aktif |
| `notes` | list string tidak kosong | catatan desain/keselamatan (ADR, larangan mengarang, pemegang kunci, dll.) |
| `limits.tool_quota` | `{tool: int 1..100}` | hanya entri v2; kunci ⊆ `tools`; batas panggilan per run (mis. `web.search: 20`) |

### Permission profile v2

`{filesystem, sandbox, git, network, deployment, portfolio, data_sensitivity_max, approval_grant}`

| Field | Nilai | Catatan |
|---|---|---|
| `network` | `deny` · `allowlist` · **`provider_web`** | `provider_web` wajib untuk server tool Claude (`web.search`, `web.fetch`, `code.execute_remote`, `docs.render_skills`) dan hanya boleh bila agent memegang salah satunya |
| `portfolio` | `none` · `draft` | `draft` wajib untuk `portfolio.draft_item` (dan hanya bila tool itu ada) |
| `data_sensitivity_max` | `public` · `internal` · `confidential` | `confidential` ⇒ tanpa `docs.render_skills`, `code.execute_remote`, `image.generate`, `image.edit` |

Entri v2 (ber-`target_mode`) wajib menulis 8 field. Entri lama boleh tetap 6 field; validator memakai default `portfolio: none`, `data_sensitivity_max: confidential` (menyatakan ulang semantik v1 dan fail-closed bila tool provider ditambahkan).

### Aturan `submit_result` & tool berisiko (ringkas)

- Wajib untuk agent llm/profile **aktif atau target**; dilarang untuk tool-mode, planned tanpa target, dan target `tool`.
- **ADR-047:** `output_contract: CitedAnswer` (I43, I47) ⇒ **tanpa** `submit_result`; jawaban berupa teks bersitasi dan wajib ada tool sumber sitasi.
- R2 ⇒ `sandbox: allow`. R3 baru: `github.push_branch`/`github.open_pr` hanya J48, `portfolio.publish` hanya K51, tidak pernah pada agent llm/profile; R3 lama (`git.push`, `deploy.staging`) tetap seperti v1.
- Tool berstatus `disabled` (P1/P2) atau `planned` (K3) tidak boleh pada agent `enabled`.

## Pemetaan mode

| mvp_mode | Agent |
|---|---|
| `llm` (6) | A02 (+A01), A03 (+C16), B14, C20, D22, A07 |
| `profile` (7) | A01 → A02, C16 → A03 (*absorbed*); B09, B10, B13, G35 → B14; C21 → C20 |
| `tool` (7) | A04, C17, D23, E27, G37, H41, ORCH |
| `planned` (33) | 21 planned v1 tanpa target (A05, A06, B08, B11, B12, B15, C18, C19, D24, D25, D26, E28, F30, F31, F32, F33, F34, G36, H38, H39, H40) + 12 ber-`target_mode` (di bawah) |

| target_mode | Fase | Agent |
|---|---|---|
| `llm` (9) | P9 | E29 (promosi) |
| | P10 | I42, I43, I44 |
| | P11 | I45, I46, I47 |
| | P12 | K49, K50 |
| `tool` (3) | P9 | J48 |
| | P10 | S52 |
| | P12 | K51 |

## Aturan perubahan

- Jangan mengubah teks verbatim PRD/K2 kecuali sumbernya direvisi (perubahan K2 → ADR baru).
- Memperluas permission (mis. `read_only` → `scoped_write`, `sandbox: deny` → `allow`, `network` → `provider_web`, `data_sensitivity_max` naik) butuh review Security Reviewer + catatan di `docs/DECISIONS.md`.
- Mengaktifkan agent `planned` mengikuti kriteria wave (`docs/AGENT_CATALOG.md §10`) dan, untuk agent v2, rencana fase + gate G9–G12 (`§12.7`): `mvp_mode ← target_mode`, `enabled: true`, hapus `target_mode`/`phase`/`contract_status`, `version` + 1, kontrak llm tertulis di `contracts.py`, tool K3 sudah diimplementasikan.
- Registry bukan satu-satunya pagar: `ToolGateway` menegakkan ulang allowlist dan permission saat runtime (AC-06).
