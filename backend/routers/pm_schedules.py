from uuid import UUID
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db import get_db
from models import PMSchedule, WorkOrder, WorkOrderType, Priority, User
from schemas import PMScheduleCreate, PMScheduleUpdate, PMScheduleOut, WorkOrderOut
from auth import get_current_user
from websocket_manager import ws_manager

router = APIRouter(prefix="/pm-schedules", tags=["pm_schedules"])
ROOM = "work_orders"


FREQUENCY_DAYS = {
    "daily": 1,
    "weekly": 7,
    "monthly": 30,
    "quarterly": 90,
    "biannual": 182,
    "annual": 365,
}


@router.get("/", response_model=list[PMScheduleOut])
async def list_pm_schedules(
    skip: int = 0,
    limit: int = 100,
    asset_id: UUID | None = None,
    active_only: bool = True,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(PMSchedule).options(selectinload(PMSchedule.asset))
    if active_only:
        q = q.where(PMSchedule.is_active == True)
    if asset_id:
        q = q.where(PMSchedule.asset_id == asset_id)
    q = q.offset(skip).limit(limit).order_by(PMSchedule.next_due)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/due-soon", response_model=list[PMScheduleOut])
async def get_due_soon(
    days_ahead: int = 7,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Return active PM schedules due within the next N days."""
    cutoff = datetime.utcnow() + timedelta(days=days_ahead)
    result = await db.execute(
        select(PMSchedule)
        .options(selectinload(PMSchedule.asset))
        .where(PMSchedule.is_active == True, PMSchedule.next_due <= cutoff)
        .order_by(PMSchedule.next_due)
    )
    return result.scalars().all()


@router.post("/", response_model=PMScheduleOut, status_code=201)
async def create_pm_schedule(
    body: PMScheduleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Use frequency default if interval_days not matching
    if body.frequency != "custom":
        body = body.model_copy(update={"interval_days": FREQUENCY_DAYS.get(body.frequency, body.interval_days)})

    pm = PMSchedule(**body.model_dump())
    db.add(pm)
    await db.flush()
    await db.refresh(pm)

    await ws_manager.broadcast_event("pm_schedules", "pm.created", {
        "id": str(pm.id),
        "title": pm.title,
        "next_due": pm.next_due.isoformat(),
    })
    return pm


@router.patch("/{pm_id}", response_model=PMScheduleOut)
async def update_pm_schedule(
    pm_id: UUID,
    body: PMScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(PMSchedule).where(PMSchedule.id == pm_id))
    pm = result.scalar_one_or_none()
    if not pm:
        raise HTTPException(404, "PM Schedule not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(pm, field, value)
    await db.flush()
    return pm


@router.delete("/{pm_id}", status_code=204)
async def delete_pm_schedule(
    pm_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(PMSchedule).where(PMSchedule.id == pm_id))
    pm = result.scalar_one_or_none()
    if not pm:
        raise HTTPException(404, "PM Schedule not found")
    await db.delete(pm)


@router.post("/{pm_id}/trigger", response_model=WorkOrderOut, status_code=201)
async def trigger_pm_now(
    pm_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually trigger a PM schedule — generates a work order immediately."""
    result = await db.execute(
        select(PMSchedule)
        .options(selectinload(PMSchedule.asset))
        .where(PMSchedule.id == pm_id)
    )
    pm = result.scalar_one_or_none()
    if not pm:
        raise HTTPException(404, "PM Schedule not found")

    return await _generate_wo_from_pm(pm, db, current_user.id)


async def _generate_wo_from_pm(
    pm: PMSchedule,
    db: AsyncSession,
    triggered_by: UUID | None = None,
) -> WorkOrder:
    """Core logic: create a WO from a PM schedule and advance next_due."""
    from sqlalchemy import func as sqlfunc
    count_result = await db.execute(
        select(sqlfunc.count()).select_from(WorkOrder)
    )
    count = count_result.scalar() or 0

    wo = WorkOrder(
        wo_number=f"WO-{datetime.utcnow().strftime('%Y%m')}-{count + 1:04d}",
        asset_id=pm.asset_id,
        type=WorkOrderType.preventive,
        priority=Priority.medium,
        title=f"[PM] {pm.title}",
        description=pm.description,
        estimated_hours=pm.estimated_hours,
        due_date=pm.next_due,
        created_by=triggered_by,
    )
    db.add(wo)

    # Advance schedule
    pm.last_triggered = datetime.utcnow()
    pm.next_due = pm.next_due + timedelta(days=pm.interval_days)

    await db.flush()
    await db.refresh(wo)

    await ws_manager.broadcast_event("work_orders", "work_order.created", {
        "id": str(wo.id),
        "wo_number": wo.wo_number,
        "title": wo.title,
        "source": "pm_schedule",
        "pm_id": str(pm.id),
    })
    return wo