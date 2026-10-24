"""Application entry point: ``uvicorn service.main:app``."""

from fastapi import FastAPI

app = FastAPI(title="{{project_slug}}")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/items/{item_id}")
def read_item(item_id: int) -> dict[str, int]:
    return {"item_id": item_id}
