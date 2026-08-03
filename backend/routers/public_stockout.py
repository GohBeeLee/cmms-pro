"""
Public Stock-Out Router — lets a production operator with no CMMS account
deduct Production Cutter inventory via a QR-code-linked form, the same way
routers/requests.py lets an operator submit a repair request without
logging in.

Deliberately scoped to a single category (Production Cutter) so this public
surface can only ever touch that slice of inventory — a part_id for any
other category is rejected even if someone crafts the request by hand.
"""
from datetime import datetime
from types import SimpleNamespace
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from db import get_db
from models import SparePart
from websocket_manager import ws_manager
from routers.stock import _ensure_table, _log_movement

router = APIRouter(prefix="/public-stockout", tags=["public-stockout"])

PUBLIC_CATEGORY = "Production Cutter"


class StockOutRequest(BaseModel):
    part_id:       str
    quantity:      int
    operator_name: str
    used_on:       Optional[str] = None   # which machine/line it was used on
    remarks:       Optional[str] = None

class StockOutResponse(BaseModel):
    success:       bool
    message:       str
    part_code:     str
    part_name:     str
    remaining_qty: int


@router.get("/parts")
async def get_public_stockout_parts(db: AsyncSession = Depends(get_db)):
    """Production Cutter parts for the QR form dropdown. No auth required."""
    result = await db.execute(
        select(SparePart)
        .where(SparePart.category == PUBLIC_CATEGORY)
        .order_by(SparePart.part_code)
    )
    return [
        {
            "id": str(p.id), "part_code": p.part_code, "name": p.name,
            "quantity_on_hand": p.quantity_on_hand, "unit": p.unit,
        }
        for p in result.scalars().all()
    ]


@router.post("/submit", response_model=StockOutResponse)
async def submit_public_stockout(body: StockOutRequest, db: AsyncSession = Depends(get_db)):
    """
    Operator stocks out a Production Cutter item via QR form. No login —
    the operator's typed name is recorded on the movement log instead of a
    user account, same pattern as the repair-request form's operator_name.
    """
    name = body.operator_name.strip()
    if not name:
        raise HTTPException(400, "Please enter your name")
    if body.quantity <= 0:
        raise HTTPException(400, "Quantity must be at least 1")

    try:
        part_uuid = UUID(body.part_id)
    except ValueError:
        raise HTTPException(400, "Invalid item")

    part = (await db.execute(select(SparePart).where(SparePart.id == part_uuid))).scalar_one_or_none()
    if not part:
        raise HTTPException(404, "Item not found")
    # Defensive category check — keeps this public, unauthenticated endpoint
    # scoped to Production Cutter no matter what part_id is passed in.
    if (part.category or "") != PUBLIC_CATEGORY:
        raise HTTPException(403, "This item isn't available for operator stock-out")
    if body.quantity > part.quantity_on_hand:
        raise HTTPException(400, f"Only {part.quantity_on_hand} {part.unit} available for {part.name}")

    await _ensure_table(db)
    qty_before = part.quantity_on_hand
    part.quantity_on_hand -= body.quantity
    part.updated_at = datetime.utcnow()
    await db.flush()

    reason = f"Stock out by production operator: {name}"
    if body.remarks:
        reason += f" — {body.remarks}"
    # No real User account exists for the operator — _log_movement only
    # needs an id/name pair to stamp on the log, so a lightweight stand-in
    # fills that role without requiring a user record.
    fake_user = SimpleNamespace(id=uuid4(), name=f"{name} (Operator — no login)")
    await _log_movement(
        db, part, "stock_out", body.quantity,
        qty_before, part.quantity_on_hand, fake_user,
        reason=reason, reference=body.used_on,
    )

    await ws_manager.broadcast_event("inventory", "inventory.updated", {
        "id": str(part.id), "name": part.name,
    })

    return StockOutResponse(
        success=True,
        message=f"{body.quantity} {part.unit} of {part.name} stocked out.",
        part_code=part.part_code,
        part_name=part.name,
        remaining_qty=part.quantity_on_hand,
    )
