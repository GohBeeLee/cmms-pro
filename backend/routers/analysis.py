import re
from datetime import datetime, timedelta
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from db import get_db
from models import WorkOrder, Asset, WorkOrderStatus
from auth import get_current_user, forbid_viewer
from models import User

router = APIRouter(prefix="/analysis", tags=["analysis"], dependencies=[Depends(forbid_viewer)])


# ── Production shift schedule (Malaysia local time, UTC+8, no DST) ──────────
# Shift 1: 08:30–18:30   Shift 2: 19:30–06:30 (next day)
# Rest break (inside Shift 1): 12:30–13:30 daily, except Friday 13:00–14:00.
# Mirrors calcDowntime()/workingMsBetween() in frontend/index.html so the
# live Alert Board counters and these reports always agree. Only elapsed
# time actually inside a running shift counts as downtime — the two
# shift-change gaps (06:30-08:30, 18:30-19:30) and the daily rest break
# are excluded.
MY_TZ_OFFSET = timedelta(hours=8)


def _shift_windows_for_day(day: datetime):
    """`day` is a naive datetime at 00:00 representing a Malaysia-local
    calendar date. Returns the 3 working windows for that date as naive-UTC
    (start, end) datetime pairs."""
    is_friday = day.weekday() == 4  # Monday=0 ... Friday=4
    rest_start = day.replace(hour=13, minute=0) if is_friday else day.replace(hour=12, minute=30)
    rest_end   = day.replace(hour=14, minute=0) if is_friday else day.replace(hour=13, minute=30)
    shift1_start = day.replace(hour=8, minute=30)
    shift1_end   = day.replace(hour=18, minute=30)
    shift2_start = day.replace(hour=19, minute=30)
    shift2_end   = (day + timedelta(days=1)).replace(hour=6, minute=30)
    windows_local = [
        (shift1_start, rest_start),
        (rest_end, shift1_end),
        (shift2_start, shift2_end),
    ]
    return [(s - MY_TZ_OFFSET, e - MY_TZ_OFFSET) for s, e in windows_local]


def working_hours_between(start: Optional[datetime], end: Optional[datetime]) -> float:
    """
    Hours of `start`..`end` (naive UTC datetimes, as stored by the DB) that
    fall inside a production shift, excluding shift-change gaps and the
    daily rest break. Used as the fallback whenever a work order doesn't
    have a manually-entered actual_hours value.
    """
    if not start or not end or end <= start:
        return 0.0
    start_local = start + MY_TZ_OFFSET
    end_local = end + MY_TZ_OFFSET
    day = datetime(start_local.year, start_local.month, start_local.day) - timedelta(days=1)
    last_day = datetime(end_local.year, end_local.month, end_local.day)
    total = timedelta()
    while day <= last_day:
        for seg_start, seg_end in _shift_windows_for_day(day):
            clip_start = max(seg_start, start)
            clip_end = min(seg_end, end)
            if clip_end > clip_start:
                total += (clip_end - clip_start)
        day += timedelta(days=1)
    return round(total.total_seconds() / 3600, 2)


def _parse_field(desc: Optional[str], field: str) -> Optional[str]:
    """Mirrors the frontend's parseField(): finds a line like 'Field  : value'
    inside the free-text description and returns the value, or None."""
    if not desc:
        return None
    for line in desc.split("\n"):
        if line.strip().startswith(field):
            parts = line.split(":", 1)
            if len(parts) > 1 and parts[1].strip():
                return parts[1].strip()
    return None


# Mirrors ROOT_CAUSE_GROUPS in frontend/index.html — used to roll individual
# root causes (e.g. "Bearing", "Motor") up into their broader group
# (Mechanical / Electrical / Others) for the History Log's root-cause report.
_ROOT_CAUSE_GROUPS = {
    "Mechanical": ["Bearing", "Chain", "Belt", "Fastener", "Wear Parts",
                   "Universal Joint", "Pneumatic", "Alignment", "Cutter Adjustment"],
    "Electrical": ["Motor", "Sensor", "PLC", "Power Trip", "Controller",
                   "Control Panel", "Wiring", "Interference Signal"],
    "Others": ["CV Files", "Setting", "Hardware", "Glue Pot", "Software Bug",
               "Operator Mistake", "Material"],
}
_ROOT_CAUSE_GROUP_LOOKUP = {rc: group for group, items in _ROOT_CAUSE_GROUPS.items() for rc in items}


@router.get("/work-orders")
async def analyse_work_orders(
    date_from: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    date_to:   Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    asset_id:  Optional[str] = Query(None, description="Filter by asset ID"),
    location:  Optional[str] = Query(None, description="Filter by asset location"),
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
            WorkOrder.affected_downtime,
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
    if location:
        q = q.where(Asset.location == location)

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
            downtime = working_hours_between(r.created_at, r.completed_at)

        # The technician selects a Root Cause (e.g. "Bearing", "Motor") from
        # a fixed list when completing the work order — it's embedded in the
        # description as a "Root cause  : X" line. Open/in-progress work
        # orders haven't reached that step yet, so they won't have one.
        root_cause = _parse_field(r.description, "Root cause")
        if not root_cause:
            root_cause = "Pending diagnosis" if r.status.value != "completed" else "Not recorded"

        work_orders.append({
            "id":             str(r.id),
            "title":          r.title,
            "root_cause":     root_cause,
            "root_cause_group": _ROOT_CAUSE_GROUP_LOOKUP.get(root_cause, "Other"),
            "type":           r.type.value,
            "priority":       r.priority.value,
            "status":         r.status.value,
            "downtime_type":   "affected" if r.affected_downtime else "non_affected",
            "affected_downtime": bool(r.affected_downtime),
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
                "affected_downtime": 0.0,
                "non_affected_downtime": 0.0,
                "completed":      0,
                "open":           0,
                "critical":       0,
            }
        machine_summary[name]["total_cases"] += 1
        if wo["downtime_hours"]:
            machine_summary[name]["total_downtime"] += wo["downtime_hours"]
            if wo["affected_downtime"]:
                machine_summary[name]["affected_downtime"] += wo["downtime_hours"]
            else:
                machine_summary[name]["non_affected_downtime"] += wo["downtime_hours"]
        if wo["status"] == "completed":
            machine_summary[name]["completed"] += 1
        elif wo["status"] in ["open", "in_progress"]:
            machine_summary[name]["open"] += 1
        if wo["priority"] == "critical":
            machine_summary[name]["critical"] += 1

    for m in machine_summary.values():
        m["total_downtime"] = round(m["total_downtime"], 2)
        m["affected_downtime"] = round(m["affected_downtime"], 2)
        m["non_affected_downtime"] = round(m["non_affected_downtime"], 2)

    # ── Summary by root cause ───────────────────────────────────────────────
    # Groups completed (and in-progress) work orders by the technician's
    # selected Root Cause, reporting case count + downtime hours per cause —
    # sorted by total downtime hours so the biggest-impact causes surface
    # first. This is the whole point of the report: find what's actually
    # costing the most machine downtime, not just what happens most often.
    root_cause_summary = {}
    for wo in work_orders:
        rc = wo["root_cause"]
        if rc not in root_cause_summary:
            root_cause_summary[rc] = {
                "root_cause":     rc,
                "group":          wo["root_cause_group"],
                "total_cases":    0,
                "total_downtime": 0.0,
                "affected_downtime": 0.0,
                "non_affected_downtime": 0.0,
                "machines":       set(),
            }
        root_cause_summary[rc]["total_cases"] += 1
        if wo["downtime_hours"]:
            root_cause_summary[rc]["total_downtime"] += wo["downtime_hours"]
            if wo["affected_downtime"]:
                root_cause_summary[rc]["affected_downtime"] += wo["downtime_hours"]
            else:
                root_cause_summary[rc]["non_affected_downtime"] += wo["downtime_hours"]
        root_cause_summary[rc]["machines"].add(wo["asset_name"])

    rc_list = []
    for rc in root_cause_summary.values():
        cases = rc["total_cases"]
        rc_list.append({
            "root_cause":      rc["root_cause"],
            "group":           rc["group"],
            "total_cases":     cases,
            "total_downtime":  round(rc["total_downtime"], 2),
            "affected_downtime": round(rc["affected_downtime"], 2),
            "non_affected_downtime": round(rc["non_affected_downtime"], 2),
            "avg_downtime":    round(rc["total_downtime"] / cases, 2) if cases else 0.0,
            "machines_affected": len(rc["machines"]),
        })
    rc_list.sort(key=lambda x: x["total_downtime"], reverse=True)

    # ── Overall summary ───────────────────────────────────────────────────
    total_downtime = sum(wo["downtime_hours"] or 0 for wo in work_orders)
    affected_downtime = sum((wo["downtime_hours"] or 0) for wo in work_orders if wo["affected_downtime"])
    non_affected_downtime = sum((wo["downtime_hours"] or 0) for wo in work_orders if not wo["affected_downtime"])
    completed_count = sum(1 for wo in work_orders if wo["status"] == "completed")
    total_count = len(work_orders)
    affected_count = sum(1 for wo in work_orders if wo["affected_downtime"])
    non_affected_count = total_count - affected_count

    daily_summary = {}
    for wo in work_orders:
        day = (wo["created_at"] or "")[:10] or "Unknown"
        if day not in daily_summary:
            daily_summary[day] = {
                "date": day,
                "affected_downtime": 0.0,
                "non_affected_downtime": 0.0,
                "total_downtime": 0.0,
                "cases": 0,
            }
        hours = wo["downtime_hours"] or 0
        daily_summary[day]["cases"] += 1
        daily_summary[day]["total_downtime"] += hours
        if wo["affected_downtime"]:
            daily_summary[day]["affected_downtime"] += hours
        else:
            daily_summary[day]["non_affected_downtime"] += hours

    downtime_graph = []
    for row in daily_summary.values():
        downtime_graph.append({
            **row,
            "affected_downtime": round(row["affected_downtime"], 2),
            "non_affected_downtime": round(row["non_affected_downtime"], 2),
            "total_downtime": round(row["total_downtime"], 2),
        })
    downtime_graph.sort(key=lambda x: x["date"])

    return {
        "summary": {
            "total_cases":       total_count,
            "affected_cases":    affected_count,
            "non_affected_cases": non_affected_count,
            "completed_cases":   completed_count,
            "open_cases":        sum(1 for wo in work_orders if wo["status"] in ["open", "in_progress"]),
            "total_downtime_hrs": round(total_downtime, 2),
            "affected_downtime_hrs": round(affected_downtime, 2),
            "non_affected_downtime_hrs": round(non_affected_downtime, 2),
            "completion_rate":   round(completed_count / total_count * 100, 1) if total_count > 0 else 0,
            "avg_downtime_hrs":  round(total_downtime / total_count, 2) if total_count > 0 else 0,
        },
        "by_machine":    sorted(machine_summary.values(), key=lambda x: x["total_cases"], reverse=True),
        "by_root_cause": rc_list,
        "downtime_graph": downtime_graph,
        "work_orders":   work_orders,
        "date_from":     date_from,
        "date_to":       date_to,
        "location":      location,
    }


@router.get("/by-machine-timeline")
async def analyse_by_machine_timeline(
    date_from: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    date_to:   Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    asset_ids: Optional[str] = Query(None, description="Comma-separated asset IDs to compare"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Returns downtime grouped by date (X-axis) with one series per selected
    machine, so multiple machines' downtime can be compared on the same chart
    over the chosen time range.
    """
    q = (
        select(
            WorkOrder.actual_hours,
            WorkOrder.affected_downtime,
            WorkOrder.created_at,
            WorkOrder.completed_at,
            Asset.id.label("asset_id"),
            Asset.name.label("asset_name"),
        )
        .join(Asset, WorkOrder.asset_id == Asset.id)
    )

    if date_from:
        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d")
            q = q.where(WorkOrder.created_at >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            q = q.where(WorkOrder.created_at <= dt_to)
        except ValueError:
            pass

    asset_id_list = None
    if asset_ids:
        try:
            asset_id_list = [UUID(a.strip()) for a in asset_ids.split(",") if a.strip()]
        except ValueError:
            asset_id_list = []  # invalid id supplied — return empty result rather than erroring
    if asset_id_list is not None:
        if len(asset_id_list) == 0:
            # No valid IDs — short-circuit to an empty result instead of matching everything
            return {"machines": [], "chart_data": [], "date_from": date_from, "date_to": date_to}
        q = q.where(WorkOrder.asset_id.in_(asset_id_list))

    result = await db.execute(q)
    rows = result.fetchall()

    # Build { date: { machine_name: downtime_hours } }
    pivot: dict[str, dict[str, float]] = {}
    machine_names: set[str] = set()

    for r in rows:
        downtime = r.actual_hours
        if downtime is None and r.completed_at and r.created_at:
            downtime = working_hours_between(r.created_at, r.completed_at)
        if not downtime:
            continue

        day = r.created_at.strftime("%Y-%m-%d") if r.created_at else "Unknown"
        name = r.asset_name
        machine_names.add(name)

        pivot.setdefault(day, {})
        pivot[day][name] = round(pivot[day].get(name, 0.0) + downtime, 2)

    machine_list = sorted(machine_names)
    chart_data = []
    for day in sorted(pivot.keys()):
        row = {"date": day}
        for m in machine_list:
            row[m] = pivot[day].get(m, 0)
        chart_data.append(row)

    return {
        "machines": machine_list,
        "chart_data": chart_data,
        "date_from": date_from,
        "date_to": date_to,
    }


@router.get("/uptime")
async def get_uptime(
    year:  Optional[int] = Query(None, description="Year, defaults to current"),
    month: Optional[int] = Query(None, description="Month 1-12, defaults to current"),
    hours_per_day: float = Query(20.0, description="Operating hours per day used in the uptime formula (2 shifts minus daily rest break = 20h/day per the production schedule)"),
    location: Optional[str] = Query(None, description="Filter by asset/production line location"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Computes real-time production line uptime % for the given month using:
        ((days_in_month * hours_per_day - affected_downtime_hours) /
         (days_in_month * hours_per_day)) * 100
    Only AFFECTED downtime counts against uptime — non-affected downtime
    (maintenance with no production loss) does not reduce uptime.
    Can be scoped to a specific production line / location.
    """
    import calendar

    now = datetime.utcnow()
    yr = year or now.year
    mo = month or now.month

    days_in_month = calendar.monthrange(yr, mo)[1]
    total_available_hours = days_in_month * hours_per_day

    month_start = datetime(yr, mo, 1)
    if mo == 12:
        month_end = datetime(yr + 1, 1, 1)
    else:
        month_end = datetime(yr, mo + 1, 1)

    q = (
        select(WorkOrder.actual_hours, WorkOrder.affected_downtime, WorkOrder.created_at, WorkOrder.completed_at)
        .join(Asset, WorkOrder.asset_id == Asset.id)
        .where(
            WorkOrder.created_at >= month_start,
            WorkOrder.created_at < month_end,
            WorkOrder.affected_downtime == True,
        )
    )
    if location:
        q = q.where(Asset.location == location)

    rows = (await db.execute(q)).fetchall()

    affected_downtime_hours = 0.0
    for r in rows:
        h = r.actual_hours
        if h is None and r.completed_at and r.created_at:
            h = working_hours_between(r.created_at, r.completed_at)
        affected_downtime_hours += h or 0

    affected_downtime_hours = round(affected_downtime_hours, 2)
    uptime_pct = (
        round(((total_available_hours - affected_downtime_hours) / total_available_hours) * 100, 2)
        if total_available_hours > 0 else 0
    )
    uptime_pct = max(0, min(100, uptime_pct))

    return {
        "year": yr,
        "month": mo,
        "days_in_month": days_in_month,
        "hours_per_day": hours_per_day,
        "location": location,
        "total_available_hours": round(total_available_hours, 2),
        "affected_downtime_hours": affected_downtime_hours,
        "uptime_pct": uptime_pct,
        "is_current_month": (yr == now.year and mo == now.month),
    }


@router.get("/assets")
async def get_assets_for_filter(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Returns all assets for the filter dropdown."""
    result = await db.execute(select(Asset.id, Asset.name, Asset.asset_code).order_by(Asset.name))
    return [{"id": str(r.id), "name": r.name, "asset_code": r.asset_code} for r in result.fetchall()]


@router.get("/locations")
async def get_locations_for_filter(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Returns distinct asset locations for the analysis filter dropdown."""
    result = await db.execute(select(Asset.location).where(Asset.location.is_not(None)).distinct().order_by(Asset.location))
    return [{"location": r.location} for r in result.fetchall() if r.location]