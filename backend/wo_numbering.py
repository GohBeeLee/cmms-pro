"""
Shared work order number generation.

The old approach (in both work_orders.py and requests.py independently)
computed `SELECT COUNT(*) FROM work_orders` and used count+1 as the next
wo_number. That's unsafe two ways:
  1. Not atomic — if two requests (e.g. a "Create & Assign Task" and a
     repair request submission) land close together, both can read the
     same count before either commits, generating the SAME wo_number and
     hitting the UNIQUE constraint on the second insert.
  2. Not gap-safe — any deleted rows (or count drifting out of sync with
     the actual highest number used) can make count+1 collide with a
     number that's already in use.

This module fixes both: the number is based on the highest existing
sequence number for the current month (not a raw row count), AND callers
wrap the actual insert in a retry loop (see `insert_with_unique_wo_number`)
so a same-millisecond collision under real concurrency just quietly
retries with the next number instead of failing the request.
"""
from datetime import datetime
from typing import Awaitable, Callable, TypeVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


async def generate_wo_number(db: AsyncSession, prefix: str = "WO") -> str:
    from models import WorkOrder  # deferred import avoids circular import at module load

    full_prefix = f"{prefix}-{datetime.utcnow().strftime('%Y%m')}-"
    result = await db.execute(
        select(WorkOrder.wo_number).where(WorkOrder.wo_number.like(f"{full_prefix}%"))
    )
    max_seq = 0
    for (wn,) in result.all():
        try:
            max_seq = max(max_seq, int(wn[len(full_prefix):]))
        except ValueError:
            continue  # ignore anything that doesn't parse as NNNN
    return f"{full_prefix}{max_seq + 1:04d}"


async def insert_with_unique_wo_number(
    db: AsyncSession,
    build: Callable[[str], Awaitable[T]],
    prefix: str = "WO",
    max_attempts: int = 5,
) -> T:
    """
    Calls build(wo_number) to construct + add the row, flushes it, and
    returns whatever build() returned. On a wo_number collision (rare, but
    possible under real concurrency even with the max-based generator
    above), rolls back and retries with a freshly generated number.

    `prefix` controls the series — e.g. "WO" for repair work orders vs
    "MT" for maintenance tasks — each series is numbered independently
    (its own per-month max lookup), so the two never interfere.

    `build` must be an async function that creates the ORM object(s),
    calls db.add(...), and returns the primary object — it needs to build
    a FRESH object each attempt rather than reusing one from a prior
    attempt, since a rolled-back object can't be safely re-added.
    """
    for attempt in range(max_attempts):
        wo_number = await generate_wo_number(db, prefix)
        obj = await build(wo_number)
        try:
            await db.flush()
            return obj
        except IntegrityError:
            await db.rollback()
            if attempt == max_attempts - 1:
                raise
    raise RuntimeError("unreachable")  # pragma: no cover