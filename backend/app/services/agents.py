"""Agents: registry sync (FR-041) and the catalog read side (FR-044 to FR-046; spec WP-3.1b goals 6, 7)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import AgentRegistry, AgentSpec
from app.core.audit import AuditWriter
from app.models.agents import AgentProfile

# One sync at a time across every process (API replicas starting together, seed-agents): held to commit.
_SYNC_LOCK = text("SELECT pg_advisory_xact_lock(hashtext('kk.agent_registry'))")


@dataclass(frozen=True, slots=True)
class SyncResult:
    inserted: int
    updated: int
    retired: int
    unchanged: int

    @property
    def changed(self) -> bool:
        return bool(self.inserted or self.updated or self.retired)


def _values(spec: AgentSpec) -> dict[str, Any]:
    data = spec.model_dump(mode="json")
    return {
        "agent_id": spec.agent_id,
        "slug": spec.slug,
        "name": spec.name,
        "group": spec.group,
        "version": spec.version,
        "enabled": spec.enabled,
        "mvp_mode": spec.mvp_mode,
        "priority": spec.priority,
        "description": spec.description,
        "purpose": spec.purpose,
        "triggers": data["triggers"],
        "capabilities": data["capabilities"],
        "tools": data["tools"],
        "permission_profile": data["permissions"],
        "model_tier": spec.model_tier,
        "limits": data["limits"],
        "runtime_agent": spec.runtime_agent,
        "input_contract": spec.input_contract,
        "output_contract": spec.output_contract,
        "inputs_tools_note": spec.inputs_tools_note,
        "output_note": spec.output_note,
        "access_note": spec.access_note,
        "target_mode": spec.target_mode,
        "phase": spec.phase,
        "contract_status": spec.contract_status,
        "notes": data["notes"],
        "registry_hash": spec.canonical_hash(),
        "in_registry": True,
    }


async def sync_registry(session: AsyncSession, registry: AgentRegistry, audit: AuditWriter) -> SyncResult:
    """Make ``agent_profiles`` match the registry; the caller commits. Rows are never deleted: an id that
    left the registry is kept with ``in_registry`` and ``enabled`` false (ids are never reused; runs point at
    them)."""
    await session.execute(_SYNC_LOCK)
    rows = {row.agent_id: row for row in (await session.scalars(select(AgentProfile))).all()}
    inserted = updated = unchanged = retired = 0
    for spec in registry.agents:
        values = _values(spec)
        row = rows.pop(spec.agent_id, None)
        if row is None:
            session.add(AgentProfile(**values))
            inserted += 1
        elif row.registry_hash != values["registry_hash"] or not row.in_registry:
            for field, value in values.items():
                setattr(row, field, value)
            updated += 1
        else:
            unchanged += 1
    for row in rows.values():
        if row.in_registry or row.enabled:
            row.in_registry = False
            row.enabled = False
            retired += 1
    result = SyncResult(inserted=inserted, updated=updated, retired=retired, unchanged=unchanged)
    await session.flush()
    if result.changed:
        await audit.write(
            session,
            actor_type="system",
            actor_id="agent-registry",
            action="agent.registry_synced",
            result="success",
            resource_type="agent_registry",
            metadata={"inserted": inserted, "updated": updated, "retired": retired, "unchanged": unchanged},
        )
    return result
