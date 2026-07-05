"""
Work Orders Router — photos stored as base64 in description (no filesystem).
"""
import re
import base64
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional

from db import get_db
from models import WorkOrder, PartsUsed, SparePart, User, WorkOrderStatus, UserRole, Asset, TaskAssignment, TaskStatus
from auth import get_current_user, forbid_viewer
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
    affected_downtime: bool = True
    remarks:           Optional[str] = None
    parts_used:        list[dict] = []   # [{spare_part_id, quantity_used}]
    completion_photos: list[PhotoEntry] = []

class AssignRequest(BaseModel):
    user_id:  Optional[str] = None
    user_ids: list[str] = []
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
    affected_downtime: bool = True

class WOUpdate(BaseModel):
    status:           Optional[str] = None
    priority:         Optional[str] = None
    title:            Optional[str] = None
    description:      Optional[str] = None
    due_date:         Optional[str] = None
    actual_hours:     Optional[float] = None
    affected_downtime: Optional[bool] = None
    assigned_to_user: Optional[str] = None


# ── Photo helpers ──────────────────────────────────────────────────────────

def _embed_photos(photos: list[PhotoEntry], tag: str, max_kb: int = 250) -> str:
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
        "affected_downtime": bool(getattr(wo, "affected_downtime", True)),
        "due_date":     wo.due_date.isoformat()     if wo.due_date     else None,
        "created_at":   wo.created_at.isoformat()   if wo.created_at   else None,
        "updated_at":   wo.updated_at.isoformat()   if wo.updated_at   else None,
        "completed_at": wo.completed_at.isoformat() if wo.completed_at else None,
        "is_deleted":   bool(getattr(wo, "is_deleted", False)),
        "deleted_at":   wo.deleted_at.isoformat()   if getattr(wo, "deleted_at", None)   else None,
        "deleted_by":   getattr(wo, "deleted_by", None),
        "restored_at":  wo.restored_at.isoformat()  if getattr(wo, "restored_at", None)  else None,
        "restored_by":  getattr(wo, "restored_by", None),
        "assigned_users": [
            {
                "id": str(a.user.id),
                "name": a.user.name,
                "email": a.user.email,
                "role": a.user.role.value if a.user.role else None,
                "assignment_status": a.status.value if a.status else None,
            }
            for a in (wo.assignments or [])
            if a.user and getattr(a.user, "is_active", True)
        ],
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
        .options(
            selectinload(WorkOrder.asset),
            selectinload(WorkOrder.assignments).selectinload(TaskAssignment.user),
        )
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
    my_jobs_only: bool = False,
    deleted_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = select(WorkOrder).options(
        selectinload(WorkOrder.asset),
        selectinload(WorkOrder.assignments).selectinload(TaskAssignment.user),
    )
    # Soft-deleted work orders are hidden everywhere by default. Only admins
    # can see the "Deleted Work Orders" trash view (deleted_only=True) —
    # everyone else always gets the normal, non-deleted list regardless of
    # what they pass, so a deleted WO never resurfaces on the Alert Board,
    # technician's board, or anyone else's History Log by mistake.
    if deleted_only and current_user.role == UserRole.admin:
        q = q.where(WorkOrder.is_deleted == True)
    else:
        q = q.where(WorkOrder.is_deleted == False)
    if status:   q = q.where(WorkOrder.status == status)
    if priority: q = q.where(WorkOrder.priority == priority)
    if asset_id: q = q.where(WorkOrder.asset_id == asset_id)

    # Explicit opt-in scoping — used by the technician Work Order Board so they
    # only see jobs assigned to them. Admin/manager are never restricted, even
    # if this flag is passed, since they're allowed full visibility everywhere.
    if my_jobs_only and current_user.role.value not in ("admin", "manager"):
        q = q.where(
            WorkOrder.assignments.any(TaskAssignment.user_id == current_user.id)
        )

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
    current_user: User = Depends(forbid_viewer),
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
        affected_downtime = body.affected_downtime,
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
    db: AsyncSession = Depends(get_db), current_user: User = Depends(forbid_viewer),
):
    from models import Priority
    wo = await _get_wo(wo_id, db)
    if wo.is_deleted:
        raise HTTPException(400, "This work order has been deleted — restore it first")
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
    if body.affected_downtime is not None:
        wo.affected_downtime = body.affected_downtime
    if body.assigned_to_user:
        wo.assigned_to_user = UUID(body.assigned_to_user)
    wo.updated_at = datetime.utcnow()
    await db.flush()
    await ws_manager.broadcast_event(ROOM, "work_order.updated", {
        "id": str(wo.id), "wo_number": wo.wo_number,
        "status": wo.status.value, "priority": wo.priority.value,
    })
    return _wo_dict(await _get_wo(wo_id, db))


@router.delete("/{wo_id}")
async def delete_work_order(
    wo_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(forbid_viewer),
):
    """
    Soft-deletes a work order — admin only. The record isn't removed from
    the database, just hidden from every normal view (Alert Board,
    technician's board, History Log, reports) and marked with who deleted
    it and when, so it can be reviewed and restored later from History
    Log's "Deleted Work Orders" view.
    """
    if current_user.role != UserRole.admin:
        raise HTTPException(403, "Only admins can delete work orders")
    wo = await _get_wo(wo_id, db)
    if wo.is_deleted:
        raise HTTPException(400, "Work order is already deleted")
    wo.is_deleted = True
    wo.deleted_at = datetime.utcnow()
    wo.deleted_by = current_user.name
    await db.flush()
    await ws_manager.broadcast_event(ROOM, "work_order.deleted", {
        "id": str(wo_id), "wo_number": wo.wo_number, "deleted_by": current_user.name,
    })
    return _wo_dict(await _get_wo(wo_id, db))


@router.post("/{wo_id}/restore")
async def restore_work_order(
    wo_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(forbid_viewer),
):
    """Reverses a soft-delete — admin only. Brings the work order back into
    every normal view exactly as it was before deletion."""
    if current_user.role != UserRole.admin:
        raise HTTPException(403, "Only admins can restore work orders")
    wo = await _get_wo(wo_id, db)
    if not wo.is_deleted:
        raise HTTPException(400, "Work order is not deleted")
    wo.is_deleted = False
    wo.restored_at = datetime.utcnow()
    wo.restored_by = current_user.name
    await db.flush()
    await ws_manager.broadcast_event(ROOM, "work_order.restored", {
        "id": str(wo_id), "wo_number": wo.wo_number, "restored_by": current_user.name,
    })
    return _wo_dict(await _get_wo(wo_id, db))


# ── Assign to technician ───────────────────────────────────────────────────

@router.post("/{wo_id}/assign")
async def assign_to_technician(
    wo_id: UUID, body: AssignRequest,
    db: AsyncSession = Depends(get_db), current_user: User = Depends(forbid_viewer),
):
    from models import Priority

    # Collect all requested user IDs (supports legacy single user_id + new user_ids list)
    raw_ids: list[str] = list(body.user_ids or [])
    if body.user_id and body.user_id not in raw_ids:
        raw_ids.append(body.user_id)
    if not raw_ids:
        raise HTTPException(400, "At least one technician must be selected")

    wo = await _get_wo(wo_id, db)
    if wo.is_deleted:
        raise HTTPException(400, "This work order has been deleted — restore it first")

    techs: list[User] = []
    for uid in raw_ids:
        tech = (await db.execute(select(User).where(User.id == UUID(uid)))).scalar_one_or_none()
        if not tech:
            raise HTTPException(404, f"User {uid} not found")
        techs.append(tech)

    # Replace existing assignments with the new set
    await db.execute(delete(TaskAssignment).where(TaskAssignment.work_order_id == wo_id))
    for tech in techs:
        db.add(TaskAssignment(
            work_order_id=wo_id,
            user_id=tech.id,
            status=TaskStatus.pending,
            notes=body.notes,
        ))

    wo.status = WorkOrderStatus.in_progress
    wo.updated_at = datetime.utcnow()
    if body.priority:
        wo.priority = Priority(body.priority)
    if body.due_date:
        wo.due_date = datetime.fromisoformat(body.due_date.replace("Z",""))

    tech_names = ", ".join(f"{t.name} ({t.email})" for t in techs)
    note = (
        f"\n[ASSIGNED — {datetime.utcnow().strftime('%d %b %Y %H:%M')}]\n"
        f"Assigned to   : {tech_names}\n"
    )
    if body.notes:
        note += f"Notes         : {body.notes}\n"
    wo.description = (wo.description or "") + note

    await db.flush()
    await ws_manager.broadcast_event(ROOM, "work_order.assigned", {
        "id": str(wo.id), "wo_number": wo.wo_number,
        "assigned_to": tech_names,
        "assigned_user_ids": [str(t.id) for t in techs],
    })
    return _wo_dict(await _get_wo(wo_id, db))


# ── Completion form ────────────────────────────────────────────────────────

@router.post("/{wo_id}/complete")
async def complete_work_order(
    wo_id: UUID, body: CompletionForm,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(forbid_viewer),
):
    wo = await _get_wo(wo_id, db)
    if wo.is_deleted:
        raise HTTPException(400, "This work order has been deleted — restore it first")
    if wo.status == WorkOrderStatus.completed:
        raise HTTPException(400, "Work order already completed")

    wo.status       = WorkOrderStatus.completed
    wo.completed_at = datetime.utcnow()
    wo.actual_hours = body.actual_hours
    wo.affected_downtime = body.affected_downtime
    wo.updated_at   = datetime.utcnow()

    comp = (
        f"\n[COMPLETION — {datetime.utcnow().strftime('%d %b %Y %H:%M')}]\n"
        f"Completed by  : {current_user.name}\n"
        f"Root cause    : {body.root_cause}\n"
        f"Actions taken : {body.actions_taken}\n"
        f"Actual hours  : {body.actual_hours}h\n"
        f"Downtime type : {'Affected' if body.affected_downtime else 'Non affected'}\n"
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
        "barcode":          getattr(p, "barcode", None),
    } for p in result.scalars().all()]