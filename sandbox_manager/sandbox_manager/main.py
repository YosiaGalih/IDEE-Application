"""ASGI entry point: ``uvicorn sandbox_manager.main:app`` (ARCHITECTURE §2)."""

from sandbox_manager.app import create_app

app = create_app()
