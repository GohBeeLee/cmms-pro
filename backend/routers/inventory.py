from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import SparePart, User
from schemas import SparePartCreate, SparePartUpdate, SparePartOut
from auth import get_current_user
from websocket_manager import ws_manager

router = APIRouter(prefix="/inventory", tags=["inventory"])
ROOM = "inventory"


@router.get("/", response_model=list[SparePartOut])
async def list_parts(
    skip: int = 0,
    limit: int = 100,
    low_stock: bool = False,
    category: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(SparePart)
    if low_stock:
        q = q.where(SparePart.quantity_on_hand <= SparePart.reorder_level)
    if category:
        q = q.where(SparePart.category == category)
    q = q.offset(skip).limit(limit).order_by(SparePart.name)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{part_id}", response_model=SparePartOut)
async def get_part(
    part_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(SparePart).where(SparePart.id == part_id))
    part = result.scalar_one_or_none()
    if not part:
        raise HTTPException(404, "Spare part not found")
    return part


@router.post("/", response_model=SparePartOut, status_code=201)
async def create_part(
    body: SparePartCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    existing = await db.execute(select(SparePart).where(SparePart.part_code == body.part_code))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Part code already exists")

    part = SparePart(**body.model_dump())
    db.add(part)
    await db.flush()
    await db.refresh(part)

    await ws_manager.broadcast_event(ROOM, "inventory.created", {
        "id": str(part.id),
        "part_code": part.part_code,
        "name": part.name,
        "quantity_on_hand": part.quantity_on_hand,
    })
    return part


@router.patch("/{part_id}", response_model=SparePartOut)
async def update_part(
    part_id: UUID,
    body: SparePartUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(SparePart).where(SparePart.id == part_id))
    part = result.scalar_one_or_none()
    if not part:
        raise HTTPException(404, "Spare part not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(part, field, value)

    await db.flush()
    await db.refresh(part)

    await ws_manager.broadcast_event(ROOM, "inventory.updated", {
        "id": str(part.id),
        "quantity_on_hand": part.quantity_on_hand,
        "low_stock": part.quantity_on_hand <= part.reorder_level,
    })
    return part


@router.delete("/{part_id}", status_code=204)
async def delete_part(
    part_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(SparePart).where(SparePart.id == part_id))
    part = result.scalar_one_or_none()
    if not part:
        raise HTTPException(404, "Spare part not found")

    await db.delete(part)
    await ws_manager.broadcast_event(ROOM, "inventory.deleted", {"id": str(part_id)})


@router.post("/{part_id}/restock", response_model=SparePartOut)
async def restock_part(
    part_id: UUID,
    quantity: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Add stock to an existing spare part."""
    if quantity <= 0:
        raise HTTPException(400, "Quantity must be positive")

    result = await db.execute(select(SparePart).where(SparePart.id == part_id))
    part = result.scalar_one_or_none()
    if not part:
        raise HTTPException(404, "Spare part not found")

    part.quantity_on_hand += quantity
    await db.flush()

    await ws_manager.broadcast_event(ROOM, "inventory.restocked", {
        "id": str(part.id),
        "part_code": part.part_code,
        "quantity_on_hand": part.quantity_on_hand,
        "low_stock": part.quantity_on_hand <= part.reorder_level,
    })
    return part