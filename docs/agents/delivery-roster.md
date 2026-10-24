# Delivery roster: siapa membangun apa

Tabel ini adalah cara Claude Code menjalankan roster di workbook WBS (`docs/wbs/2026-10-23-kodekantor-wbs-agentic-v2.2.xlsx`, sheet *Agent Roster*, *WBS*, *RACI*, *Gates*). Bila tabel ini berbeda dengan workbook, **workbook yang berlaku**; perbedaannya dilaporkan ke Lead.

Delivery agent ≠ runtime agent (lihat `GLOSSARY.md`).

## Cara roster dijalankan di Claude Code

- **AG-00 Lead Architect = sesi utama.** Memecah WP, menulis brief, memverifikasi output, mengintegrasikan, dan membuka PR.
- **AG-01..AG-21 = peran, bukan file subagent.** Saat sebuah WP dieksekusi (`superpowers:subagent-driven-development`), brief implementer menyertakan baris peran dari tabel di bawah: path milik, model, dan larangan. Peran tidak dibuat sebagai `.claude/agents/*` karena kepemilikannya terikat fase dan sebagian besar belum aktif.
- **Reviewer lintas fase = subagent tetap** di `.claude/agents/`:
  - `kk-security-reviewer` (AG-13, sign-off 🔒).
  - `kk-backend-reviewer`, `kk-frontend-reviewer`, `kk-silent-failure-hunter` (diadaptasi dari ECC).
- **Model**: kolom *Model* di bawah dipakai saat dispatch subagent. "Opus" untuk kontrak, workflow, keamanan, dan integrasi; "Sonnet" untuk pekerjaan yang tertutup oleh path milik dan kontrak yang sudah terkunci.

## Aturan kerja (ROADMAP §2.2, IMPLEMENTATION_DESIGN_V2 §11.3)

1. Agent hanya menulis di path miliknya. Perubahan di luar path milik **dilaporkan**, tidak dilakukan, kecuali Lead mendelegasikannya secara eksplisit di brief.
2. File kontrak (`app/core/*`, `app/models/*`, kontrak kode, `ARCHITECTURE.md`, `API_CONTRACTS.md`, `DATA_MODEL.md`, `DECISIONS.md`) hanya diubah Lead. Perubahan kontrak memerlukan ADR.
3. Laporan akhir setiap agent memuat:
   - file yang berubah;
   - perintah test beserta **output aktual**;
   - deviasi kontrak;
   - gap yang diketahui;
   - langkah berikutnya;
   - baris tracker yang perlu diperbarui.
4. Test lama tidak boleh diubah agar lulus. Izin atau syarat approval tidak boleh diturunkan. Vendor nyata (Claude, GitHub, vendor gambar) tidak boleh dipanggil dari test.
5. **Git** — usulan ADR-069, menunggu PO. Dokumen asli menetapkan "hanya Lead yang commit; agent tidak menjalankan git". Di Claude Code, implementer bekerja di worktree terisolasi per task (`superpowers:using-git-worktrees`), jadi aturannya disesuaikan:
   - Implementer boleh commit **hanya di branch worktree task-nya sendiri**.
   - Hanya Lead yang me-merge ke branch WP, melakukan push, dan membuka PR.
   - Tidak ada agent yang menjalankan `push --force`, `reset --hard` pada branch bersama, atau merge ke `main`.
6. Gate (G0–G16) dan WP manusia (WP-0.9, WP-INT-07, WP-6.6, WP-9.1, WP-11.1, WP-14.0) tidak dapat diselesaikan oleh agent. Agent menyiapkan bukti, lalu manusia memutuskan.

## Roster

| ID | Peran | Model | Path milik (ringkas) | Batasan |
|---|---|---|---|---|
| AG-00 | Lead Architect / Orchestrator | Opus | `docs/{ARCHITECTURE,DATA_MODEL,API_CONTRACTS,DECISIONS,TOOL_CATALOG,CONFIGURATION,IMPLEMENTATION_TRACKER}.md`, `CLAUDE.md`, kontrak kode (`app/agents/contracts.py`, `app/llm/base.py`, `app/tools/base.py`, `app/sandbox/client.py`) | Satu-satunya integrator (merge/push/PR) |
| AG-01 | Backend Core | Opus/Sonnet | `backend/pyproject.toml`, `backend/alembic/`, `app/{main,cli}.py`, `app/core/`, `app/models/`, `app/api/{deps,router}.py`, routes auth/health, services auth/users, `workflow/states.py`, `worker/celery_app.py`, `tests/conftest.py` | — |
| AG-02 | Workspace & Git | Sonnet | `app/workspace/{paths,fs,patches,archive,stack,templates_registry,templates/}`, `app/gitops/` | Repo git uji hanya di tmp |
| AG-03 | Sandbox & Runtime | Sonnet | `sandbox_manager/`, `sandbox-images/`, `app/sandbox/{http_client,profiles,junit,factory}.py` | Docker lokal hanya untuk test |
| AG-04 | Frontend Foundation | Sonnet | `frontend/` kecuali feature milik AG-10/11/20/21 | — |
| AG-05 | Docs & Catalog | Sonnet | `docs/{PRD*,FR_CATALOG,AGENT_CATALOG,SECURITY_MODEL,USER_PROXY_ACCEPTANCE,TEST_STRATEGY,IMPLEMENTATION_ROADMAP}.md`, `agents/`, `scripts/validate_registry.py` | — |
| AG-06 | Infra & DevOps | Sonnet | `infra/`, `.env.example`, `Makefile`, `.github/`, Dockerfile, `scripts/*.sh`, `docs/{RUNBOOK,DEPLOYMENT}.md`, `README.md` | Build/run container lokal saja |
| AG-07 | Agentic Core | Opus | `app/llm/`, `app/tools/`, `app/agents/{registry,runtime,factory,prompting,prompts/}`, `workflow/budget.py`, services+routes approvals/usage/agents/settings; v2: `capabilities/`, `usage/` | Tanpa API key nyata |
| AG-08 | Workflow & Acceptance | Opus | `app/workflow/{engine,steps,lease,recovery,dispatch,context,checkpoint,gate,approval_handlers}.py`, `worker/jobs.py`, services+routes tasks/conversations/events/testing/terminal/reviews/acceptance/artifacts/deployments; v2.1: `sdlc/` | — |
| AG-09 | Platform Services | Sonnet | `app/workspace/{search,symbols}.py`, services+routes projects/workspace/git/repository/indexing/knowledge/dashboard/admin/audit, `worker/platform_jobs.py` | — |
| AG-10 | Frontend Workspace | Sonnet | `frontend/src/features/{workspace,tasks,review,testing,acceptance,preview}` + hooks | — |
| AG-11 | Frontend Admin | Sonnet | `frontend/src/features/{settings,admin,knowledge,deployments}` + hooks | — |
| AG-12 | Integration & E2E QA | Opus | `tests/e2e/`, job CI `e2e`/`clean-install`, `backend/evals/` | Lingkungan Docker |
| AG-13 | Security Reviewer | Opus | `backend/tests/security/`; selain itu read-only | Sign-off 🔒 tertulis |
| AG-14 | Release & Staging | Sonnet | `infra/` (bersama AG-06), `docs/RUNBOOK.md` | VPS staging lewat OPS; tidak ke production |
| AG-15 | Specialist Rollout | Sonnet/Opus | `agents/registry*.yaml` (enable), prompt runtime agent, eval set | Aktivasi per gate G8 |
| AG-16 | Design Reviewer | Opus | — | Read-only |
| AG-17 | Demo & Prototype | Opus | di luar repo | Tidak boleh mengarang data |
| AG-18 | Integrations (GitHub, Admin API) | Opus | `backend/app/integrations/`, routes integrations, tabel `github_*` | Private key hanya di `worker-integrations` |
| AG-19 | AI Portal Backend | Opus | `backend/app/{assistant,docengine,kb,moderation}/`, `app/llm/image_*` | — |
| AG-20 | AI Portal Frontend | Sonnet | `frontend/src/features/assistant/**` | — |
| AG-21 | Portfolio Full-stack | Sonnet | `backend/app/portfolio/`, `frontend/src/features/portfolio/**` | AI hanya draft |
| PO | Product Owner (manusia) | — | — | Memutuskan ADR, gate, aktivasi agent |
| OPS | Operator (manusia) | — | server & secret store | Secret, model ID, budget, drill |

Path `app/...` berarti `backend/app/...`.

Konflik dokumen yang memengaruhi tabel ini, misalnya path prompt `agents/prompts/` vs `app/agents/prompts/` dan path `contracts.py`, tercatat di `docs/superpowers/specs/2026-10-24-development-system-design.md` §8.
