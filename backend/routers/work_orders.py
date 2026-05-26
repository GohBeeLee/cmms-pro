"""
Work Orders Router — photos stored as base64 in description (no filesystem).
"""
import re
import base64
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional

from db import get_db
from models import WorkOrder, PartsUsed, SparePart, User, WorkOrderStatus, Asset
from auth import get_current_user
from websocket_manager import ws_manager

router = APIRouter(prefix="/work-orders", tags=["work_orders"])
ROOM = "work_orders"


# ── Schemas ────────────────────────────────────────────────────────────────

class PhotoEntry(BaseModel):
    filename: str
    data: str   # base64 data URL
    size: int = 0

class CompletionForm(BaseModel):
    root_cause:        str
    actions_taken:     str
    actual_hours:      float
    remarks:           Optional[str] = None
    parts_used:        list[dict] = []   # [{spare_part_id, quantity_used}]
    completion_photos: list[PhotoEntry] = []

class AssignRequest(BaseModel):
    user_id:  str
    priority: Optional[str] = None
    due_date: Optional[str] = None
    notes:    Optional[str] = None

class WOCreate(BaseModel):
    asset_id:         str
    type:             str = "corrective"
    priority:         str = "medium"
    title:            str
    description:      Optional[str] = None
    due_date:         Optional[str] = None
    estimated_hours:  Optional[float] = None

class WOUpdate(BaseModel):
    status:           Optional[str] = None
    priority:         Optional[str] = None
    title:            Optional[str] = None
    description:      Optional[str] = None
    due_date:         Optional[str] = None
    actual_hours:     Optional[float] = None
    assigned_to_user: Optional[str] = None


# ── Photo helpers ──────────────────────────────────────────────────────────

def _embed_photos(photos: list[PhotoEntry], tag: str, max_kb: int = 200) -> str:
    """Embed photos as base64 blocks with a section tag."""
    parts = []
    for p in photos[:3]:
        try:
            data_url = p.data if p.data.startswith("data:") else f"data:image/jpeg;base64,{p.data}"
            raw = data_url.split(",", 1)[1] if "," in data_url else data_url
            if len(raw) * 0.75 / 1024 > max_kb:
                parts.append(f"[PHOTO:{p.filename}|TOO_LARGE]")
                continue
            parts.append(f"[PHOTO:{p.filename}|{data_url}]")
        except Exception:
            pass
    if not parts:
        return ""
    return f"\n[{tag}]\n" + "\n".join(parts) + f"\n[/{tag}]\n"


# ── WO dict serializer ─────────────────────────────────────────────────────

def _wo_dict(wo: WorkOrder) -> dict:
    return {
        "id":           str(wo.id),
        "wo_number":    wo.wo_number,
        "title":        wo.title,
        "description":  wo.description,
        "type":         wo.type.value    if wo.type     else None,
        "priority":     wo.priority.value if wo.priority else None,
        "status":       wo.status.value  if wo.status   else None,
        "actual_hours": wo.actual_hours,
        "estimated_hours": wo.estimated_hours,
        "due_date":     wo.due_date.isoformat()     if wo.due_date     else None,
        "created_at":   wo.created_at.isoformat()   if wo.created_at   else None,
        "updated_at":   wo.updated_at.isoformat()   if wo.updated_at   else None,
        "completed_at": wo.completed_at.isoformat() if wo.completed_at else None,
        "asset": {
            "id":         str(wo.asset.id),
            "name":       wo.asset.name,
            "asset_code": wo.asset.asset_code,
            "location":   wo.asset.location,
        } if wo.asset else None,
    }


async def _get_wo(wo_id: UUID, db: AsyncSession) -> WorkOrder:
    result = await db.execute(
        select(WorkOrder).where(WorkOrder.id == wo_id)
        .options(selectinload(WorkOrder.asset))
    )
    wo = result.scalar_one_or_none()
    if not wo:
        raise HTTPException(404, "Work order not found")
    return wo


def _wo_number(seq: int) -> str:
    return f"WO-{datetime.utcnow().strftime('%Y%m')}-{seq:04d}"


# ── CRUD ───────────────────────────────────────────────────────────────────

@router.get("/")
async def list_work_orders(
    skip: int = 0, limit: int = 100,
    status: str | None = None,
    priority: str | None = None,
    asset_id: UUID | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(WorkOrder).options(selectinload(WorkOrder.asset))
    if status:   q = q.where(WorkOrder.status == status)
    if priority: q = q.where(WorkOrder.priority == priority)
    if asset_id: q = q.where(WorkOrder.asset_id == asset_id)
    q = q.offset(skip).limit(limit).order_by(WorkOrder.created_at.desc())
    result = await db.execute(q)
    return [_wo_dict(w) for w in result.scalars().all()]


@router.get("/{wo_id}")
async def get_work_order(wo_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    return _wo_dict(await _get_wo(wo_id, db))


@router.post("/", status_code=201)
async def create_work_order(
    body: WOCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from models import WorkOrderType, Priority
    count = (await db.execute(select(func.count()).select_from(WorkOrder))).scalar() or 0
    wo = WorkOrder(
        wo_number       = _wo_number(count + 1),
        asset_id        = UUID(body.asset_id),
        type            = WorkOrderType(body.type),
        priority        = Priority(body.priority),
        status          = WorkOrderStatus.open,
        title           = body.title,
        description     = body.description,
        estimated_hours = body.estimated_hours,
        created_by      = current_user.id,
    )
    if body.due_date:
        wo.due_date = datetime.fromisoformat(body.due_date.replace("Z",""))
    db.add(wo)
    await db.flush()
    await db.refresh(wo)
    await ws_manager.broadcast_event(ROOM, "work_order.created", {
        "id": str(wo.id), "wo_number": wo.wo_number, "title": wo.title,
        "priority": wo.priority.value, "status": wo.status.value,
    })
    return _wo_dict(await _get_wo(wo.id, db))


@router.patch("/{wo_id}")
async def update_work_order(
    wo_id: UUID, body: WOUpdate,
    db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user),
):
    from models import Priority
    wo = await _get_wo(wo_id, db)
    if body.status:
        wo.status = WorkOrderStatus(body.status)
        if body.status == "completed" and not wo.completed_at:
            wo.completed_at = datetime.utcnow()
    if body.priority:
        wo.priority = Priority(body.priority)
    if body.title:
        wo.title = body.title
    if body.description is not None:
        wo.description = body.description
    if body.due_date:
        wo.due_date = datetime.fromisoformat(body.due_date.replace("Z",""))
    if body.actual_hours is not None:
        wo.actual_hours = body.actual_hours
    if body.assigned_to_user:
        wo.assigned_to_user = UUID(body.assigned_to_user)
    wo.updated_at = datetime.utcnow()
    await db.flush()
    await ws_manager.broadcast_event(ROOM, "work_order.updated", {
        "id": str(wo.id), "wo_number": wo.wo_number,
        "status": wo.status.value, "priority": wo.priority.value,
    })
    return _wo_dict(await _get_wo(wo_id, db))


@router.delete("/{wo_id}", status_code=204)
async def delete_work_order(
    wo_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    wo = await _get_wo(wo_id, db)
    await db.delete(wo)
    await ws_manager.broadcast_event(ROOM, "work_order.deleted", {"id": str(wo_id)})


# ── Assign to technician ───────────────────────────────────────────────────

@router.post("/{wo_id}/assign")
async def assign_to_technician(
    wo_id: UUID, body: AssignRequest,
    db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user),
):
    from models import Priority
    wo = await _get_wo(wo_id, db)
    tech = (await db.execute(select(User).where(User.id == UUID(body.user_id)))).scalar_one_or_none()
    if not tech:
        raise HTTPException(404, "User not found")

    wo.assigned_to_user = UUID(body.user_id)
    wo.status = WorkOrderStatus.in_progress
    wo.updated_at = datetime.utcnow()
    if body.priority:
        wo.priority = Priority(body.priority)
    if body.due_date:
        wo.due_date = datetime.fromisoformat(body.due_date.replace("Z",""))

    note = (
        f"\n[ASSIGNED — {datetime.utcnow().strftime('%d %b %Y %H:%M')}]\n"
        f"Assigned to   : {tech.name} ({tech.email})\n"
    )
    if body.notes:
        note += f"Notes         : {body.notes}\n"
    wo.description = (wo.description or "") + note

    await db.flush()
    await ws_manager.broadcast_event(ROOM, "work_order.assigned", {
        "id": str(wo.id), "wo_number": wo.wo_number,
        "assigned_to": tech.name, "assigned_to_id": str(tech.id),
    })
    return _wo_dict(await _get_wo(wo_id, db))


# ── Completion form ────────────────────────────────────────────────────────

@router.post("/{wo_id}/complete")
async def complete_work_order(
    wo_id: UUID, body: CompletionForm,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    wo = await _get_wo(wo_id, db)
    if wo.status == WorkOrderStatus.completed:
        raise HTTPException(400, "Work order already completed")

    wo.status       = WorkOrderStatus.completed
    wo.completed_at = datetime.utcnow()
    wo.actual_hours = body.actual_hours
    wo.updated_at   = datetime.utcnow()

    comp = (
        f"\n[COMPLETION — {datetime.utcnow().strftime('%d %b %Y %H:%M')}]\n"
        f"Completed by  : {current_user.name}\n"
        f"Root cause    : {body.root_cause}\n"
        f"Actions taken : {body.actions_taken}\n"
        f"Actual hours  : {body.actual_hours}h\n"
    )
    if body.remarks:
        comp += f"Remarks       : {body.remarks}\n"

    # Embed completion photos as base64 in description
    if body.completion_photos:
        comp += _embed_photos(body.completion_photos, "COMPLETION_PHOTOS")

    wo.description = (wo.description or "") + comp

    # Deduct parts from inventory
    for entry in body.parts_used:
        part = (await db.execute(
            select(SparePart).where(SparePart.id == UUID(entry["spare_part_id"]))
        )).scalar_one_or_none()
        if not part:
            raise HTTPException(404, f"Part not found: {entry['spare_part_id']}")
        qty = int(entry.get("quantity_used", 1))
        if part.quantity_on_hand < qty:
            raise HTTPException(400, f"Insufficient stock for {part.name}. Available: {part.quantity_on_hand}")
        part.quantity_on_hand -= qty
        db.add(PartsUsed(
            work_order_id = wo_id,
            spare_part_id = UUID(entry["spare_part_id"]),
            quantity_used = qty,
        ))
        if part.quantity_on_hand <= part.reorder_level:
            await ws_manager.broadcast_event("inventory", "inventory.low_stock", {
                "part_id": str(part.id), "part_name": part.name,
                "quantity_on_hand": part.quantity_on_hand,
            })

    await db.flush()
    await ws_manager.broadcast_event(ROOM, "work_order.completed", {
        "id": str(wo_id), "wo_number": wo.wo_number,
        "completed_by": current_user.name, "actual_hours": body.actual_hours,
    })
    return _wo_dict(await _get_wo(wo_id, db))


# ── Parts availability ─────────────────────────────────────────────────────

@router.get("/{wo_id}/parts-availability")
async def parts_availability(
    wo_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(SparePart).order_by(SparePart.name))
    return [{
        "id":               str(p.id),
        "part_code":        p.part_code,
        "name":             p.name,
        "category":         p.category,
        "unit":             p.unit,
        "quantity_on_hand": p.quantity_on_hand,
        "reorder_level":    p.reorder_level,
        "is_available":     p.quantity_on_hand > 0,
        "is_low_stock":     p.quantity_on_hand <= p.reorder_level,
        "unit_cost":        p.unit_cost,
        "location":         p.location,
    } for p in result.scalars().all()]
