"""
Assets Router — with safe delete handling
Fix: DELETE returns 409 with clear message if work orders are linked.
"""
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db import get_db
from models import Asset, WorkOrder, AssetStatus, AssetBOMItem, SparePart, PartsUsed
from schemas import AssetCreate, AssetUpdate, AssetOut
from auth import get_current_user, forbid_viewer
from models import User
from websocket_manager import ws_manager
from pydantic import BaseModel

router = APIRouter(prefix="/assets", tags=["assets"], dependencies=[Depends(forbid_viewer)])
ROOM = "assets"


async def _get_asset(asset_id: UUID, db: AsyncSession) -> Asset:
    result = await db.execute(select(Asset).where(Asset.id == asset_id))
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Asset not found")
    return asset


@router.get("/", response_model=list[AssetOut])
async def list_assets(
    skip: int = 0,
    limit: int = 200,
    status: str | None = None,
    category: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(Asset)
    if status:   q = q.where(Asset.status == status)
    if category: q = q.where(Asset.category == category)
    q = q.offset(skip).limit(limit).order_by(Asset.name)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{asset_id}", response_model=AssetOut)
async def get_asset(asset_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    return await _get_asset(asset_id, db)


@router.post("/", response_model=AssetOut, status_code=201)
async def create_asset(
    body: AssetCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    # Check duplicate asset_code
    existing = await db.execute(select(Asset).where(Asset.asset_code == body.asset_code))
    if existing.scalar_one_or_none():
        raise HTTPException(400, f"Asset code '{body.asset_code}' already exists")
    asset = Asset(**body.model_dump())
    db.add(asset)
    await db.flush()
    await db.refresh(asset)
    await ws_manager.broadcast_event(ROOM, "asset.created", {
        "id": str(asset.id), "name": asset.name, "asset_code": asset.asset_code,
    })
    return asset


@router.patch("/{asset_id}", response_model=AssetOut)
async def update_asset(
    asset_id: UUID,
    body: AssetUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    asset = await _get_asset(asset_id, db)
    updates = body.model_dump(exclude_unset=True)
    for k, v in updates.items():
        setattr(asset, k, v)
    asset.updated_at = datetime.utcnow()
    await db.flush()
    await ws_manager.broadcast_event(ROOM, "asset.updated", {
        "id": str(asset.id), "name": asset.name, "status": asset.status.value,
    })
    return asset


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(
    asset_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Hard delete an asset.
    Returns 409 if work orders are still linked — caller should use PATCH to
    set status=decommissioned instead, which preserves the history.
    """
    asset = await _get_asset(asset_id, db)

    # Check for linked work orders
    wo_count = (await db.execute(
        select(func.count()).select_from(WorkOrder).where(WorkOrder.asset_id == asset_id)
    )).scalar() or 0

    if wo_count > 0:
        raise HTTPException(
            409,
            f"Cannot delete: this asset has {wo_count} linked work order(s). "
            f"Set status to 'decommissioned' instead to preserve history."
        )

    await db.delete(asset)
    await ws_manager.broadcast_event(ROOM, "asset.deleted", {"id": str(asset_id)})


# ── Bill of Materials (BOM) ───────────────────────────────────────────────
# Which spare parts a machine requires, and how many. Distinct from the
# "used_on_asset" free-text field on SparePart and from PartsUsed (a log of
# what's actually been consumed on past repairs) — a BOM entry is an
# explicit, admin-maintained requirement that exists whether or not the
# part has ever failed yet, so it can drive stock planning ahead of time.

class BOMItemCreate(BaseModel):
    spare_part_id: str
    qty_required: int = 1
    notes: str | None = None


class BOMItemUpdate(BaseModel):
    qty_required: int | None = None
    notes: str | None = None


@router.get("/{asset_id}/bom")
async def get_asset_bom(asset_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    await _get_asset(asset_id, db)  # 404 if the asset itself doesn't exist
    rows = (await db.execute(
        select(AssetBOMItem, SparePart)
        .join(SparePart, SparePart.id == AssetBOMItem.spare_part_id)
        .where(AssetBOMItem.asset_id == asset_id)
        .order_by(SparePart.name)
    )).all()
    return [
        {
            "id":                  str(item.id),
            "spare_part_id":       str(part.id),
            "part_code":           part.part_code,
            "part_name":           part.name,
            "unit":                part.unit,
            "quantity_on_hand":    part.quantity_on_hand,
            "reorder_level":       part.reorder_level,
            "qty_required":        item.qty_required,
            "notes":               item.notes,
            "seeded_from_history": item.seeded_from_history,
            # Not enough of this part in stock to cover what this one
            # machine alone would need for a repair.
            "low_stock":           part.quantity_on_hand < item.qty_required,
        }
        for item, part in rows
    ]


@router.post("/{asset_id}/bom", status_code=201)
async def add_bom_item(
    asset_id: UUID,
    body: BOMItemCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    await _get_asset(asset_id, db)
    part = await db.get(SparePart, UUID(body.spare_part_id))
    if not part:
        raise HTTPException(404, "Spare part not found")
    dup = (await db.execute(
        select(AssetBOMItem).where(AssetBOMItem.asset_id == asset_id, AssetBOMItem.spare_part_id == part.id)
    )).scalar_one_or_none()
    if dup:
        raise HTTPException(400, f"'{part.name}' is already in this machine's BOM — edit the existing entry instead.")
    item = AssetBOMItem(
        asset_id=asset_id, spare_part_id=part.id,
        qty_required=max(1, body.qty_required), notes=body.notes,
    )
    db.add(item)
    await db.flush()
    await ws_manager.broadcast_event(ROOM, "asset.bom_updated", {"asset_id": str(asset_id)})
    return {"id": str(item.id)}


@router.patch("/bom/{item_id}")
async def update_bom_item(
    item_id: UUID,
    body: BOMItemUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    item = await db.get(AssetBOMItem, item_id)
    if not item:
        raise HTTPException(404, "BOM item not found")
    if body.qty_required is not None:
        item.qty_required = max(1, body.qty_required)
    if body.notes is not None:
        item.notes = body.notes
    item.updated_at = datetime.utcnow()
    await db.flush()
    await ws_manager.broadcast_event(ROOM, "asset.bom_updated", {"asset_id": str(item.asset_id)})
    return {"ok": True}


@router.delete("/bom/{item_id}", status_code=204)
async def delete_bom_item(item_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    item = await db.get(AssetBOMItem, item_id)
    if not item:
        raise HTTPException(404, "BOM item not found")
    asset_id = item.asset_id
    await db.delete(item)
    await ws_manager.broadcast_event(ROOM, "asset.bom_updated", {"asset_id": str(asset_id)})


@router.post("/{asset_id}/bom/seed-from-history")
async def seed_bom_from_history(asset_id: UUID, db: AsyncSession = Depends(get_db), _: User = Depends(get_current_user)):
    """
    Pre-fills this machine's BOM from its repair history: every spare part
    ever logged against one of its work orders (PartsUsed), skipping parts
    already in the BOM. The suggested qty_required is the average quantity
    used per repair that involved that part (rounded, minimum 1) — a
    starting point the admin can adjust, not a guarantee.
    """
    await _get_asset(asset_id, db)

    rows = (await db.execute(
        select(
            PartsUsed.spare_part_id,
            func.sum(PartsUsed.quantity_used).label("total_qty"),
            func.count(func.distinct(PartsUsed.work_order_id)).label("wo_count"),
        )
        .join(WorkOrder, WorkOrder.id == PartsUsed.work_order_id)
        .where(WorkOrder.asset_id == asset_id)
        .group_by(PartsUsed.spare_part_id)
    )).all()

    if not rows:
        return {"added": 0, "message": "No parts usage history found for this machine yet."}

    existing_ids = set((await db.execute(
        select(AssetBOMItem.spare_part_id).where(AssetBOMItem.asset_id == asset_id)
    )).scalars().all())

    added = 0
    for r in rows:
        if r.spare_part_id in existing_ids:
            continue
        suggested_qty = max(1, round(r.total_qty / r.wo_count))
        db.add(AssetBOMItem(
            asset_id=asset_id,
            spare_part_id=r.spare_part_id,
            qty_required=suggested_qty,
            notes=f"Seeded from history: used in {r.wo_count} repair(s), {r.total_qty} pcs total",
            seeded_from_history=True,
        ))
        added += 1

    await db.flush()
    if added:
        await ws_manager.broadcast_event(ROOM, "asset.bom_updated", {"asset_id": str(asset_id)})
    return {"added": added}