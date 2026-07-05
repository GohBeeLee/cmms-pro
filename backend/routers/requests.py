"""
Repair Request Router — stores photos as base64 in DB description.
No filesystem dependency — works on Render free tier and localhost.
"""
import base64
import re
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from db import get_db
from models import WorkOrder, WorkOrderType, WorkOrderStatus, Priority, Asset
from websocket_manager import ws_manager

router = APIRouter(prefix="/requests", tags=["requests"])


# ── Schemas ────────────────────────────────────────────────────────────────

class PhotoData(BaseModel):
    filename: str
    data: str       # full base64 data URL: "data:image/jpeg;base64,..."
    size: int       # bytes

class RepairRequest(BaseModel):
    asset_id:            str
    operator_name:       str
    problem_category:    str
    problem_description: str
    urgency:             str = "medium"
    remarks:             Optional[str] = None
    photos:              list[PhotoData] = []

class RepairRequestOut(BaseModel):
    success:    bool
    message:    str
    wo_number:  str
    asset_name: str


# ── Helpers ────────────────────────────────────────────────────────────────

def _encode_photos(photos: list[PhotoData], max_size_kb: int = 250) -> str:
    """
    Encode up to 3 photos as compact base64 blocks inside the description.
    Each photo is stored as:
      [PHOTO:filename|data:image/jpeg;base64,/9j/4AAQ...]
    Frontend extracts these blocks to display as <img src="data:...">
    Max 200KB per photo to keep description size reasonable.
    """
    parts = []
    for p in photos[:3]:
        try:
            # Ensure data URL format
            if p.data.startswith("data:"):
                data_url = p.data
            else:
                data_url = f"data:image/jpeg;base64,{p.data}"

            # Check size limit — skip oversized photos
            raw = data_url.split(",", 1)[1] if "," in data_url else data_url
            size_kb = len(raw) * 0.75 / 1024   # base64 to bytes estimate
            if size_kb > max_size_kb:
                # Shrink: just store a note
                parts.append(f"[PHOTO:{p.filename}|TOO_LARGE]")
                continue

            parts.append(f"[PHOTO:{p.filename}|{data_url}]")
        except Exception:
            pass
    return "\n".join(parts)


def extract_photos_from_description(description: str) -> list[dict]:
    """
    Extract embedded photo data URLs from description text.
    Returns list of {filename, src} dicts.
    """
    photos = []
    for match in re.finditer(r'\[PHOTO:([^\|]+)\|([^\]]+)\]', description or ""):
        filename = match.group(1)
        data_url = match.group(2)
        if data_url != "TOO_LARGE":
            photos.append({"filename": filename, "src": data_url})
    return photos


# ── Public endpoints ───────────────────────────────────────────────────────

@router.get("/assets")
async def get_assets_public(db: AsyncSession = Depends(get_db)):
    """All assets for the QR form dropdown. No auth required."""
    result = await db.execute(
        select(Asset.id, Asset.name, Asset.asset_code, Asset.location, Asset.category)
        .order_by(Asset.name)
    )
    return [
        {"id": str(r.id), "name": r.name, "asset_code": r.asset_code,
         "location": r.location, "category": r.category}
        for r in result.fetchall()
    ]


@router.post("/submit", response_model=RepairRequestOut)
async def submit_repair_request(
    body: RepairRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Operator submits a repair request via QR form.
    Photos stored as base64 in description — no filesystem needed.
    """
    result = await db.execute(select(Asset).where(Asset.id == UUID(body.asset_id)))
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Machine not found")

    priority_map = {
        "low": Priority.low, "medium": Priority.medium,
        "high": Priority.high, "critical": Priority.critical,
    }
    priority = priority_map.get(body.urgency.lower(), Priority.medium)

    count = (await db.execute(select(func.count()).select_from(WorkOrder))).scalar() or 0
    wo_number = f"WO-{datetime.utcnow().strftime('%Y%m')}-{count + 1:04d}"

    # Build description
    description = (
        f"[OPERATOR REQUEST]\n"
        f"Submitted by  : {body.operator_name}\n"
        f"Category      : {body.problem_category}\n"
        f"Problem       : {body.problem_description}\n"
    )
    if body.remarks:
        description += f"Remarks       : {body.remarks}\n"

    # Embed photos as base64 in description
    if body.photos:
        photo_block = _encode_photos(body.photos)
        if photo_block:
            description += f"\n[OPERATOR_PHOTOS]\n{photo_block}\n[/OPERATOR_PHOTOS]\n"

    wo = WorkOrder(
        wo_number   = wo_number,
        asset_id    = UUID(body.asset_id),
        type        = WorkOrderType.corrective,
        priority    = priority,
        status      = WorkOrderStatus.open,
        title       = f"[Request] {asset.name} — {body.problem_category}",
        description = description,
    )
    db.add(wo)
    await db.flush()
    await db.refresh(wo)

    await ws_manager.broadcast_event("requests", "request.new", {
        "id": str(wo.id), "wo_number": wo_number,
        "asset_name": asset.name, "operator": body.operator_name,
        "category": body.problem_category, "urgency": body.urgency,
        "created_at": datetime.utcnow().isoformat(),
    })
    await ws_manager.broadcast_event("work_orders", "work_order.created", {
        "id": str(wo.id), "wo_number": wo_number,
        "title": wo.title, "priority": priority.value, "status": WorkOrderStatus.open.value,
    })

    return RepairRequestOut(
        success=True,
        message="Your repair request has been submitted. Our maintenance team will attend shortly.",
        wo_number=wo_number,
        asset_name=asset.name,
    )