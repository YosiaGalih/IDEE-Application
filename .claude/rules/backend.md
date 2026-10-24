---
paths:
  - "backend/**/*.py"
  - "sandbox_manager/**/*.py"
  - "scripts/**/*.py"
---
# Backend Python (FastAPI · SQLAlchemy 2 async · Celery)

Kontrak: `docs/ARCHITECTURE.md` §3–§11, `docs/IMPLEMENTATION_DESIGN_V2.md` §4–§5, `docs/DATA_MODEL.md`, `docs/API_CONTRACTS.md`. Aturan di sini adalah invarian yang paling sering dilanggar; detail ada di kontrak.

## Toolchain (ADR-001, ADR-017, ADR-049)

- Python 3.12 (CI juga 3.13), `uv` dengan `uv.lock`. Ruff untuk lint dan format, Pyright untuk tipe, import-linter untuk layer.
- Library baru: rilis berumur ≥ 2 minggu dan audit dependensi bersih. Versi dipin di `uv.lock`.

## Struktur dan layer

- Layer mengikuti `ARCHITECTURE.md` §3.2 + `IMPLEMENTATION_DESIGN_V2.md` §4.3, diuji `lint-imports`. `tools` tidak mengimpor `workflow`/`agents`/domain; workflow ↔ services lewat Ports (ADR-022), tanpa `importlib` lintas layer.
- SDK vendor hanya di adapternya: `anthropic` hanya di `app/llm/anthropic_provider.py`; `PyJWT` hanya di `app/integrations/github/app_auth.py`.
- `subprocess`/`os.system` hanya di `app/gitops/`. Tidak ada `requests`/`urllib`; pakai `httpx`.
- `create_app()` membangun aplikasi; route tipis (validasi + panggil service), logika di `services/`, sesi DB dan auth lewat dependency.
- Endpoint I/O memakai `async def` dengan klien async. Tidak ada panggilan blocking di route async.

## API

- Base `/api/v1`, envelope `{request_id, status, data, error, meta}`, error `{code, message, details}`; 500 tanpa stack trace/path absolut; resource di luar scope → 404.
- Mutasi: CSRF double-submit + `Idempotency-Key`. Operasi panjang → 202 + SSE (`Last-Event-ID`). WebSocket hanya untuk terminal (ADR-059).
- Skema request/update/response terpisah; `response_model` di setiap endpoint data; response tidak pernah memuat hash password, token, atau state auth internal. Model yang dihadapkan ke LLM memakai `extra="forbid"`.

## Data

- PK UUID dibuat aplikasi, `created_at`/`updated_at`, enum `VARCHAR + CHECK` dengan nama `ck_<table>_<rule>` (ADR-018), `ON DELETE` eksplisit, isi file tidak di DB (hanya `storage_ref`).
- Tidak ada kolom yang menyimpan token, private key, atau API key.
- Migrasi Alembic: kolom baru nullable/default konstan; nilai enum baru lewat `NOT VALID` lalu `VALIDATE`; setiap migrasi lulus `upgrade head → downgrade -1 → upgrade head`.
- `tasks.status` hanya ditulis `workflow.states.transition`; `audit_events` append-only.

## Konfigurasi, log, secret

- Semua variabel di `docs/CONFIGURATION.md` ada di `Settings` (pydantic-settings); nilai wajib yang hilang/invalid → gagal start dengan pesan aman. `scripted` provider dan driver `fake` ditolak di production.
- Log `structlog` JSON lewat `core/redaction.py`. Secret tidak pernah masuk log, event, prompt, argv, DB, atau env sandbox.

## Test

- Pytest + pytest-asyncio + respx. Lokasi dan nama file test di `docs/TEST_STRATEGY.md` adalah kontrak lokasi.
- Tidak ada panggilan Claude/GitHub/vendor nyata: `LLM_PROVIDER=scripted` (ADR-015), `FakeSandboxClient`, respx. Test Docker nyata memakai marker `docker`.
- Setiap bug keamanan mendapat test regresi di `backend/tests/security/` sebelum fix di-merge.
- Coverage target awal ≥ 85% untuk `app/core`, `app/tools`, `app/workflow`, `services/acceptance.py`, `workspace/paths.py`, `workspace/archive.py`; ≥ 70% keseluruhan (indikator, bukan gate tunggal).
