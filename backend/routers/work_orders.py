from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db import get_db
from models import WorkOrder, TaskAssignment, PartsUsed, SparePart, User, WorkOrderStatus
from schemas import (
    WorkOrderCreate, WorkOrderUpdate, WorkOrderOut,
    TaskAssignmentCreate, TaskAssignmentUpdate, TaskAssignmentOut,
    PartsUsedCreate, PartsUsedOut,
)
from auth import get_current_user
from websocket_manager import ws_manager

router = APIRouter(prefix="/work-orders", tags=["work_orders"])
ROOM = "work_orders"


def _generate_wo_number(sequence: int) -> str:
    return f"WO-{datetime.utcnow().strftime('%Y%m')}-{sequence:04d}"


async def _get_wo_or_404(wo_id: UUID, db: AsyncSession) -> WorkOrder:
    result = await db.execute(
        select(WorkOrder)
        .where(WorkOrder.id == wo_id)
        .options(selectinload(WorkOrder.asset))
    )
    wo = result.scalar_one_or_none()
    if not wo:
        raise HTTPException(404, "Work order not found")
    return wo


# ── Work Orders ────────────────────────────────────────────────────────────

@router.get("/", response_model=list[WorkOrderOut])
async def list_work_orders(
    skip: int = 0,
    limit: int = 100,
    status: str | None = None,
    priority: str | None = None,
    asset_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(WorkOrder).options(selectinload(WorkOrder.asset))
    if status:
        q = q.where(WorkOrder.status == status)
    if priority:
        q = q.where(WorkOrder.priority == priority)
    if asset_id:
        q = q.where(WorkOrder.asset_id == asset_id)
    q = q.offset(skip).limit(limit).order_by(WorkOrder.created_at.desc())
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{wo_id}", response_model=WorkOrderOut)
async def get_work_order(
    wo_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return await _get_wo_or_404(wo_id, db)


@router.post("/", response_model=WorkOrderOut, status_code=201)
async def create_work_order(
    body: WorkOrderCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Generate sequential WO number
    count_result = await db.execute(select(func.count()).select_from(WorkOrder))
    count = count_result.scalar() or 0

    wo = WorkOrder(
        **body.model_dump(),
        wo_number=_generate_wo_number(count + 1),
        created_by=current_user.id,
    )
    db.add(wo)
    await db.flush()
    await db.refresh(wo)

    await ws_manager.broadcast_event(ROOM, "work_order.created", {
        "id": str(wo.id),
        "wo_number": wo.wo_number,
        "title": wo.title,
        "priority": wo.priority.value,
        "status": wo.status.value,
    })
    return await _get_wo_or_404(wo.id, db)


@router.patch("/{wo_id}", response_model=WorkOrderOut)
async def update_work_order(
    wo_id: UUID,
    body: WorkOrderUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    wo = await _get_wo_or_404(wo_id, db)

    updates = body.model_dump(exclude_unset=True)

    # Auto-set completed_at when status → completed
    if updates.get("status") == WorkOrderStatus.completed and not wo.completed_at:
        updates["completed_at"] = datetime.utcnow()

    for field, value in updates.items():
        setattr(wo, field, value)

    wo.updated_at = datetime.utcnow()
    await db.flush()

    await ws_manager.broadcast_event(ROOM, "work_order.updated", {
        "id": str(wo.id),
        "wo_number": wo.wo_number,
        "status": wo.status.value,
        "priority": wo.priority.value,
    })
    return await _get_wo_or_404(wo_id, db)


@router.delete("/{wo_id}", status_code=204)
async def delete_work_order(
    wo_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    wo = await _get_wo_or_404(wo_id, db)
    await db.delete(wo)
    await ws_manager.broadcast_event(ROOM, "work_order.deleted", {"id": str(wo_id)})


# ── Task Assignments ───────────────────────────────────────────────────────

@router.get("/{wo_id}/assignments", response_model=list[TaskAssignmentOut])
async def list_assignments(
    wo_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(
        select(TaskAssignment)
        .where(TaskAssignment.work_order_id == wo_id)
        .options(selectinload(TaskAssignment.user))
    )
    return result.scalars().all()


@router.post("/{wo_id}/assignments", response_model=TaskAssignmentOut, status_code=201)
async def assign_task(
    wo_id: UUID,
    body: TaskAssignmentCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await _get_wo_or_404(wo_id, db)
    assignment = TaskAssignment(work_order_id=wo_id, **body.model_dump())
    db.add(assignment)
    await db.flush()
    await db.refresh(assignment)

    await ws_manager.broadcast_event("tasks", "task.assigned", {
        "work_order_id": str(wo_id),
        "user_id": str(body.user_id),
        "assignment_id": str(assignment.id),
    })
    return assignment


@router.patch("/{wo_id}/assignments/{assignment_id}", response_model=TaskAssignmentOut)
async def update_assignment(
    wo_id: UUID,
    assignment_id: UUID,
    body: TaskAssignmentUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(
        select(TaskAssignment)
        .where(TaskAssignment.id == assignment_id, TaskAssignment.work_order_id == wo_id)
        .options(selectinload(TaskAssignment.user))
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(404, "Assignment not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(assignment, field, value)

    await db.flush()
    await ws_manager.broadcast_event("tasks", "task.updated", {
        "assignment_id": str(assignment_id),
        "status": assignment.status.value,
    })
    return assignment


# ── Parts Used ─────────────────────────────────────────────────────────────

@router.get("/{wo_id}/parts", response_model=list[PartsUsedOut])
async def list_parts_used(
    wo_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(
        select(PartsUsed)
        .where(PartsUsed.work_order_id == wo_id)
        .options(selectinload(PartsUsed.spare_part))
    )
    return result.scalars().all()


@router.post("/{wo_id}/parts", response_model=PartsUsedOut, status_code=201)
async def consume_part(
    wo_id: UUID,
    body: PartsUsedCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await _get_wo_or_404(wo_id, db)

    # Check stock
    part_result = await db.execute(
        select(SparePart).where(SparePart.id == body.spare_part_id)
    )
    part = part_result.scalar_one_or_none()
    if not part:
        raise HTTPException(404, "Spare part not found")
    if part.quantity_on_hand < body.quantity_used:
        raise HTTPException(400, f"Insufficient stock. Available: {part.quantity_on_hand}")

    # Deduct stock
    part.quantity_on_hand -= body.quantity_used

    record = PartsUsed(work_order_id=wo_id, **body.model_dump())
    db.add(record)
    await db.flush()
    await db.refresh(record)

    # Broadcast inventory update
    await ws_manager.broadcast_event("inventory", "inventory.updated", {
        "part_id": str(part.id),
        "part_code": part.part_code,
        "quantity_on_hand": part.quantity_on_hand,
        "low_stock": part.quantity_on_hand <= part.reorder_level,
    })
    return record
