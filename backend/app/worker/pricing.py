"""The pricing table, loaded while a worker is built (spec WP-3.1f Keputusan Lead #10; plan 3.1f1 Task 5).

A bootstep, not a signal: Celery's ``Signal.send`` catches and logs whatever a receiver raises, so a
``worker_init`` receiver could not stop a worker that has no prices. A step's ``__init__`` runs inside
``WorkController.__init__`` (``Blueprint.apply``) before any step is included or started, with nothing to
catch it, so a missing or broken file makes ``celery worker`` exit before it ever reaches the broker.

``celery beat`` and the api never build worker steps and never load the table. In eager mode (tests) the
caller holds it with ``hold_pricing``. The runtime (3.1f2) reads it with ``worker_pricing()``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

from celery import bootsteps

from app.core.logging import get_logger
from app.llm.pricing import PricingTable, load_pricing

PRICING_PATH_CONF: Final = "kk_pricing_path"

_held: PricingTable | None = None


class PricingNotLoaded(RuntimeError):
    """This process holds no pricing table: it is not a worker, or the worker skipped its step."""


def hold_pricing(path: Path) -> PricingTable:
    """Load the table (``PricingError`` propagates) and keep it for this process."""
    global _held
    table = load_pricing(path)
    _held = table
    logger = get_logger(__name__)
    # Prices not yet checked against the official page must stand out in the start log (review L1).
    log = logger.warning if table.verify_before_production else logger.info
    log("pricing_loaded", version=table.version, verify_before_production=table.verify_before_production)
    return table


def worker_pricing() -> PricingTable:
    """The table this process holds; ``PricingNotLoaded`` when there is none."""
    if _held is None:
        raise PricingNotLoaded("no pricing table is loaded in this process")
    return _held


class PricingStep(bootsteps.Step):
    """Loaded while the worker is built, before any broker connection. A broken or missing file raises
    out of ``WorkController.__init__``, so ``celery worker`` exits instead of serving."""

    def __init__(self, parent: Any, **kwargs: Any) -> None:
        super().__init__(parent, **kwargs)
        hold_pricing(Path(parent.app.conf[PRICING_PATH_CONF]))
