"""Durable-state model for compensating ingestion writes across external stores."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field

ResourceIds = dict[str, object]
SagaJournal = Callable[[str, str, ResourceIds, str | None, str | None], Awaitable[None]]
SagaCompensator = Callable[[ResourceIds], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class CompensationOutcome:
    """Result of attempting to undo every completed external ingest step."""

    reconcile_required: bool
    errors: tuple[str, ...]


@dataclass(slots=True)
class IngestionSaga:
    """Track and compensate external writes for a single ingestion job.

    Steps are recorded only after their external side effect completes. On a
    later failure they are undone in reverse execution order, while the journal
    receives each state transition for durable operational recovery.
    """

    job_id: str
    tenant_id: str
    journal: SagaJournal
    compensators: Mapping[str, SagaCompensator]
    _completed: list[tuple[str, ResourceIds]] = field(default_factory=list, init=False)

    def _track(self, step: str, resource_ids: ResourceIds) -> ResourceIds:
        copied_resources = deepcopy(resource_ids)
        for index, (completed_step, _) in enumerate(self._completed):
            if completed_step == step:
                self._completed[index] = (step, copied_resources)
                break
        else:
            self._completed.append((step, copied_resources))
        return copied_resources

    async def record_started(self, step: str, resource_ids: ResourceIds) -> None:
        """Record a possibly-partial external step before invoking its store."""
        copied_resources = self._track(step, resource_ids)
        await self.journal(step, "started", copied_resources, None, None)

    async def record_completed(self, step: str, resource_ids: ResourceIds) -> None:
        """Mark an external step completed after its store call returns successfully."""
        copied_resources = self._track(step, resource_ids)
        await self.journal(step, "completed", copied_resources, None, None)

    async def compensate(self) -> CompensationOutcome:
        """Undo completed steps in strict reverse order and journal every outcome."""
        errors: list[str] = []
        for step, resource_ids in reversed(self._completed):
            compensator = self.compensators.get(step)
            if compensator is None:
                message = f"{step}: no compensator registered"
                errors.append(message)
                await self.journal(
                    step,
                    "reconcile_required",
                    resource_ids,
                    "failed",
                    message,
                )
                continue
            try:
                await compensator(resource_ids)
            except Exception as exc:
                message = f"{step}: {exc}"
                errors.append(message)
                await self.journal(
                    step,
                    "reconcile_required",
                    resource_ids,
                    "failed",
                    message,
                )
            else:
                await self.journal(step, "compensated", resource_ids, "completed", None)
        return CompensationOutcome(reconcile_required=bool(errors), errors=tuple(errors))
