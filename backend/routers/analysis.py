from datetime import datetime
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from db import get_db
from models import WorkOrder, Asset, WorkOrderStatus
from auth import get_current_user
from models import User

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/work-orders")
async def analyse_work_orders(
    date_from: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    date_to:   Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    asset_id:  Optional[str] = Query(None, description="Filter by asset ID"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Returns analysis data for work orders within a date range.
    Groups by machine name and root cause, with downtime and case counts.
    """

    # ── Base query joining WorkOrder + Asset ──────────────────────────────
    q = (
        select(
            WorkOrder.id,
            WorkOrder.title,
            WorkOrder.description,
            WorkOrder.type,
            WorkOrder.priority,
            WorkOrder.status,
            WorkOrder.actual_hours,
            WorkOrder.estimated_hours,
            WorkOrder.created_at,
            WorkOrder.completed_at,
            WorkOrder.due_date,
            Asset.name.label("asset_name"),
            Asset.category.label("asset_category"),
            Asset.location.label("asset_location"),
        )
        .join(Asset, WorkOrder.asset_id == Asset.id)
    )

    # ── Date filters ──────────────────────────────────────────────────────
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d")
            q = q.where(WorkOrder.created_at >= dt_from)
        except ValueError:
            pass

    if date_to:
        try:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d")
            # include full day
            dt_to = dt_to.replace(hour=23, minute=59, second=59)
            q = q.where(WorkOrder.created_at <= dt_to)
        except ValueError:
            pass

    # ── Asset filter ──────────────────────────────────────────────────────
    if asset_id:
        q = q.where(WorkOrder.asset_id == asset_id)

    q = q.order_by(WorkOrder.created_at.desc())
    result = await db.execute(q)
    rows = result.fetchall()

    # ── Build work order list ─────────────────────────────────────────────
    work_orders = []
    for r in rows:
        # Calculate actual downtime in hours
        downtime = None
        if r.actual_hours:
            downtime = r.actual_hours
        elif r.completed_at and r.created_at:
            diff = (r.completed_at - r.created_at).total_seconds() / 3600
            downtime = round(diff, 2)

        work_orders.append({
            "id":             str(r.id),
            "title":          r.title,
            "root_cause":     r.description or "Not specified",
            "type":           r.type.value,
            "priority":       r.priority.value,
            "status":         r.status.value,
            "downtime_hours": downtime,
            "created_at":     r.created_at.isoformat() if r.created_at else None,
            "completed_at":   r.completed_at.isoformat() if r.completed_at else None,
            "asset_name":     r.asset_name,
            "asset_category": r.asset_category,
            "asset_location": r.asset_location,
        })

    # ── Summary by machine ────────────────────────────────────────────────
    machine_summary = {}
    for wo in work_orders:
        name = wo["asset_name"]
        if name not in machine_summary:
            machine_summary[name] = {
                "asset_name":     name,
                "asset_category": wo["asset_category"],
                "asset_location": wo["asset_location"],
                "total_cases":    0,
                "total_downtime": 0.0,
                "completed":      0,
                "open":           0,
                "critical":       0,
            }
        machine_summary[name]["total_cases"] += 1
        if wo["downtime_hours"]:
            machine_summary[name]["total_downtime"] += wo["downtime_hours"]
        if wo["status"] == "completed":
            machine_summary[name]["completed"] += 1
        elif wo["status"] in ["open", "in_progress"]:
            machine_summary[name]["open"] += 1
        if wo["priority"] == "critical":
            machine_summary[name]["critical"] += 1

    for m in machine_summary.values():
        m["total_downtime"] = round(m["total_downtime"], 2)

    # ── Summary by root cause (description keywords) ──────────────────────
    root_cause_summary = {}
    for wo in work_orders:
        rc = wo["root_cause"] if wo["root_cause"] and wo["root_cause"] != "Not specified" else "Unknown"
        # Truncate long descriptions to use as key
        rc_key = rc[:60] + "..." if len(rc) > 60 else rc
        if rc_key not in root_cause_summary:
            root_cause_summary[rc_key] = {
                "root_cause":     rc_key,
                "total_cases":    0,
                "total_downtime": 0.0,
                "machines":       set(),
            }
        root_cause_summary[rc_key]["total_cases"] += 1
        if wo["downtime_hours"]:
            root_cause_summary[rc_key]["total_downtime"] += wo["downtime_hours"]
        root_cause_summary[rc_key]["machines"].add(wo["asset_name"])

    rc_list = []
    for rc in root_cause_summary.values():
        rc_list.append({
            "root_cause":      rc["root_cause"],
            "total_cases":     rc["total_cases"],
            "total_downtime":  round(rc["total_downtime"], 2),
            "machines_affected": len(rc["machines"]),
        })
    rc_list.sort(key=lambda x: x["total_cases"], reverse=True)

    # ── Overall summary ───────────────────────────────────────────────────
    total_downtime = sum(wo["downtime_hours"] or 0 for wo in work_orders)
    completed_count = sum(1 for wo in work_orders if wo["status"] == "completed")
    total_count = len(work_orders)

    return {
        "summary": {
            "total_cases":       total_count,
            "completed_cases":   completed_count,
            "open_cases":        sum(1 for wo in work_orders if wo["status"] in ["open", "in_progress"]),
            "total_downtime_hrs": round(total_downtime, 2),
            "completion_rate":   round(completed_count / total_count * 100, 1) if total_count > 0 else 0,
            "avg_downtime_hrs":  round(total_downtime / total_count, 2) if total_count > 0 else 0,
        },
        "by_machine":    sorted(machine_summary.values(), key=lambda x: x["total_cases"], reverse=True),
        "by_root_cause": rc_list,
        "work_orders":   work_orders,
        "date_from":     date_from,
        "date_to":       date_to,
    }


@router.get("/assets")
async def get_assets_for_filter(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Returns all assets for the filter dropdown."""
    result = await db.execute(select(Asset.id, Asset.name, Asset.asset_code).order_by(Asset.name))
    return [{"id": str(r.id), "name": r.name, "asset_code": r.asset_code} for r in result.fetchall()]