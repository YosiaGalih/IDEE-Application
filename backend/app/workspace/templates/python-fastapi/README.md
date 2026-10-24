# {{project_name}}

Layanan HTTP kecil berbasis FastAPI.

## Menjalankan

```bash
pip install -e ".[dev]"
uvicorn service.main:app --reload
```

Buka `http://localhost:8000/health`.

## Menjalankan test

```bash
python -m pytest -q
```
