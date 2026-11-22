"""The agent registry, loaded once while a worker is built (spec WP-3.2 §16.3 #68, §16.3a item 7).

Same shape as ``app.worker.pricing``: a bootstep, so a registry that cannot be used stops ``celery worker``
before it reaches the broker. ``celery beat`` and the api never build worker steps. In eager mode (tests)
the caller holds it with ``hold_registry``. ``app.services`` must not import this module (layer contract);
a caller in ``app.worker`` reads ``worker_registry()`` and injects the registry.

A failed load is logged by type only (and the problem count when the error carries one), never by message or
problem text, then the original exception propagates.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from celery import bootsteps

from app.agents.registry import AgentRegistry, RegistryError, load_registry
from app.core.logging import get_logger

REGISTRY_PATH_CONF: Final = "kk_agent_registry_path"

_held: AgentRegistry | None = None


class RegistryNotLoaded(RuntimeError):
    """This process holds no agent registry: it is not a worker, or the worker skipped its step."""


def hold_registry(path: Path) -> AgentRegistry:
    """Load the registry (any failure is logged by type, then re-raised) and keep it for this process."""
    global _held
    logger = get_logger(__name__)
    try:
        registry = load_registry(path)
    except Exception as exc:
        fields: dict[str, Any] = {"error_type": type(exc).__name__}
        if isinstance(exc, RegistryError):
            fields["problems_count"] = len(exc.problems)
        logger.error("agent_registry_invalid", **fields)
        raise
    _held = registry
    logger.info("agent_registry_loaded", agents=len(registry.agents))
    return registry


def worker_registry() -> AgentRegistry:
    """The registry this process holds; ``RegistryNotLoaded`` when there is none."""
    if _held is None:
        raise RegistryNotLoaded("no agent registry is loaded in this process")
    return _held


class RegistryStep(bootsteps.Step):
    """Loaded while the worker is built, before any broker connection. An unusable file raises out of
    ``WorkController.__init__``, so ``celery worker`` exits instead of serving."""

    def __init__(self, parent: Any, **kwargs: Any) -> None:
        super().__init__(parent, **kwargs)
        hold_registry(Path(parent.app.conf[REGISTRY_PATH_CONF]))
