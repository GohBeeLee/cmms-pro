from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db import get_db
from models import Asset, User
from schemas import AssetCreate, AssetUpdate, AssetOut
from auth import get_current_user
from websocket_manager import ws_manager

router = APIRouter(prefix="/assets", tags=["assets"])
ROOM = "assets"


@router.get("/", response_model=list[AssetOut])
async def list_assets(
    skip: int = 0,
    limit: int = 100,
    category: str | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(Asset)
    if category:
        q = q.where(Asset.category == category)
    if status:
        q = q.where(Asset.status == status)
    q = q.offset(skip).limit(limit).order_by(Asset.created_at.desc())
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{asset_id}", response_model=AssetOut)
async def get_asset(
    asset_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(Asset).where(Asset.id == asset_id))
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Asset not found")
    return asset


@router.post("/", response_model=AssetOut, status_code=201)
async def create_asset(
    body: AssetCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Check unique asset_code
    existing = await db.execute(select(Asset).where(Asset.asset_code == body.asset_code))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Asset code already exists")

    asset = Asset(**body.model_dump())
    db.add(asset)
    await db.flush()
    await db.refresh(asset)

    await ws_manager.broadcast_event(ROOM, "asset.created", {
        "id": str(asset.id),
        "asset_code": asset.asset_code,
        "name": asset.name,
        "status": asset.status.value,
    })
    return asset


@router.patch("/{asset_id}", response_model=AssetOut)
async def update_asset(
    asset_id: UUID,
    body: AssetUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(Asset).where(Asset.id == asset_id))
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Asset not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(asset, field, value)

    await db.flush()
    await db.refresh(asset)

    await ws_manager.broadcast_event(ROOM, "asset.updated", {
        "id": str(asset.id),
        "name": asset.name,
        "status": asset.status.value,
    })
    return asset


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(
    asset_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(Asset).where(Asset.id == asset_id))
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Asset not found")

    await db.delete(asset)
    await ws_manager.broadcast_event(ROOM, "asset.deleted", {"id": str(asset_id)})