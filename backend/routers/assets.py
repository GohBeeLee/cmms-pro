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
from models import Asset, WorkOrder, AssetStatus
from schemas import AssetCreate, AssetUpdate, AssetOut
from auth import get_current_user, forbid_viewer
from models import User
from websocket_manager import ws_manager

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