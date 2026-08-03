import re
from datetime import datetime, timedelta
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from db import get_db
from models import WorkOrder, Asset, WorkOrderStatus, WorkOrderType
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
               "Operator Mistake", "Material", "Miscellaneous"],
}
_ROOT_CAUSE_GROUP_LOOKUP = {rc: group for group, items in _ROOT_CAUSE_GROUPS.items() for rc in items}


# ── MRR (Machine Repair Request = corrective work orders) monthly reports ──
# "MRR" mirrors the History Log's own "🔧 Machine Repair Request" tab, which
# is exactly WorkOrder.type == 'corrective'.

MONTH_LABELS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

# Rolls each asset's exact `location` value up into the 6 report columns.
# Any location not listed here (Forklift, Packing Line, or an asset with no
# location) falls into "Other".
_LOCATION_GROUP_MAP = {
    "Autoline 1":        "Autoline 1",
    "Autoline 2":        "Autoline 2",
    "General Facilities": "ASS & General Facilities",
    "Supporting Line":   "Supporting Line",
    "Spray Paint Line":  "Spray Paint Line",
}
LOCATION_GROUP_ORDER = ["Autoline 1", "Autoline 2", "ASS & General Facilities",
                         "Supporting Line", "Spray Paint Line", "Other"]

def _location_group(location: Optional[str]) -> str:
    return _LOCATION_GROUP_MAP.get(location or "", "Other")


# Rolls each Autoline asset's name up into its process-station group, by
# keyword match (checked in this order so overlapping substrings resolve to
# the right station — e.g. "Sorting Robot" contains "robot" but should be
# "Sorting", not "MHS").
# NEEDS CONFIRMATION: "MHS" (Multi Handling System) is still placeholder-
# mapped to any asset with "robot" in its name. In the reference report MHS
# is one of the biggest categories, so it likely refers to a specific named
# machine/conveyor per Autoline rather than the (currently zero-case) Nesting
# Robot — update this mapping once that's confirmed.
def _autoline_station_group(asset_name: Optional[str]) -> Optional[str]:
    n = (asset_name or "").lower()
    if "buffer rack" in n:
        return "Buffer Rack"
    if "sorting" in n:
        return "Sorting"
    if "robot" in n:
        return "MHS"
    if "nesting" in n:
        return "Nesting Machine"
    if "edging" in n:
        return "Edging Machine"
    if "cnc" in n:
        return "CNC"
    return None  # not one of the tracked Autoline stations

# Backwards-compatible alias (used by the month-snapshot endpoint below).
_autoline_asset_group = _autoline_station_group

ASSET_GROUP_ORDER = ["Nesting Machine", "MHS", "Edging Machine", "CNC", "Sorting", "Buffer Rack"]

# Autoline 1/2 are broken down by process station (above). Supporting Line
# and Spray Paint Line have no shared "station" concept, so per the user's
# choice they're broken down by individual machine/asset name instead —
# their category list is fetched from the Asset table at request time
# (see mrr_autoline_detail) rather than being a fixed list here.
STATION_LINES = ["Autoline 1", "Autoline 2"]
ASSET_NAME_LINES = ["Supporting Line", "Spray Paint Line"]
LINE_ORDER = STATION_LINES + ASSET_NAME_LINES


# For the month-snapshot "other locations" mini table — splits the combined
# "ASS & General Facilities" location group back into its two named rows.
def _other_location_group(location: Optional[str], asset_name: Optional[str]) -> Optional[str]:
    if asset_name == "Automatic Storage System":
        return "Automatic Storage System (ASS)"
    if location == "General Facilities":
        return "General Facilities"
    return None


# A work order can be marked "affected" by the technician when it's logged,
# but once the asset's downtime formula (divisor, set via the ⚙️ Downtime
# Formula popup) is applied, the recalculated downtime hours may fall under
# this minimum. Below that threshold it no longer counts as a production-
# affecting stoppage, so it's reclassified as non-affected everywhere
# affected/non-affected downtime is reported — regardless of the flag
# originally stored on the work order.
AFFECTED_DOWNTIME_MIN_HOURS = 0.5


def _effective_affected(affected_downtime_flag: bool, downtime_hours: Optional[float]) -> bool:
    """Reclassifies a work order as non-affected if its post-formula downtime
    hours fall below AFFECTED_DOWNTIME_MIN_HOURS, even if it was originally
    marked affected."""
    return bool(affected_downtime_flag) and (downtime_hours or 0.0) >= AFFECTED_DOWNTIME_MIN_HOURS


def _wo_downtime_hours(actual_hours, created_at, completed_at, held_hours, divisor: float = 1.0) -> float:
    """Same fallback used elsewhere: prefer the manually-entered actual_hours,
    otherwise derive it from elapsed working time for completed work orders.
    Then divides by the asset's downtime_divisor (default 1.0 = no change) —
    see the Asset model for why some machines need this."""
    if actual_hours:
        hours = actual_hours
    elif completed_at and created_at:
        hours = max(working_hours_between(created_at, completed_at) - (held_hours or 0), 0.0)
    else:
        return 0.0
    return hours / (divisor or 1.0)


@router.get("/work-orders")
async def analyse_work_orders(
    date_from: Optional[str] = Query(None, description="Start date YYYY-MM-DD"),
    date_to:   Optional[str] = Query(None, description="End date YYYY-MM-DD"),
    asset_id:  Optional[str] = Query(None, description="Filter by asset ID"),
    location:  Optional[str] = Query(None, description="Filter by asset location. Comma-separate to select multiple locations (e.g. 'Autoline 1,Autoline 2')."),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Returns analysis data for work orders within a date range.
    Groups by machine name and root cause, with downtime and case counts.
    """
    # A comma-separated `location` selects multiple locations at once (used by
    # the Root Cause Analysis tab's multi-select). A single value behaves as
    # before.
    location_list = [l.strip() for l in location.split(",") if l.strip()] if location else []

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
            WorkOrder.held_hours,
            WorkOrder.due_date,
            Asset.name.label("asset_name"),
            Asset.category.label("asset_category"),
            Asset.location.label("asset_location"),
            Asset.downtime_divisor.label("asset_downtime_divisor"),
        )
        .join(Asset, WorkOrder.asset_id == Asset.id)
        .where(WorkOrder.is_deleted == False)
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
    if len(location_list) == 1:
        q = q.where(Asset.location == location_list[0])
    elif len(location_list) > 1:
        q = q.where(Asset.location.in_(location_list))

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
            # held_hours excludes time the work order spent on_hold, so a
            # pause doesn't count against its downtime.
            downtime = max(working_hours_between(r.created_at, r.completed_at) - (r.held_hours or 0), 0.0)
        if downtime is not None:
            downtime = downtime / (r.asset_downtime_divisor or 1.0)

        # Reclassify to non-affected if the formula-adjusted downtime is
        # too small to count as production-affecting (see
        # AFFECTED_DOWNTIME_MIN_HOURS above).
        effective_affected = _effective_affected(r.affected_downtime, downtime)

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
            "downtime_type":   "affected" if effective_affected else "non_affected",
            "affected_downtime": effective_affected,
            "affected_downtime_original": bool(r.affected_downtime),
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

    # ── Per-location case counts, keyed by root cause ───────────────────────
    # Only meaningful when multiple locations are selected (the Root Cause
    # Analysis tab's multi-select) — lets the grouped table below show one
    # column of cases per selected location plus a combined Total column.
    by_location_rc = {}
    for wo in work_orders:
        loc = wo["asset_location"] or "Unspecified"
        rc = wo["root_cause"]
        bucket = by_location_rc.setdefault(loc, {})
        bucket[rc] = bucket.get(rc, 0) + 1
    # Column order: the locations the user actually selected, in that order.
    # (With 0 or 1 selected there's nothing to break out and the frontend
    # falls back to just the Total column.)
    location_columns = location_list

    # Grouped root-cause table — includes every canonical root cause, even
    # ones with zero cases, under its category, with a category subtotal.
    # Each item carries per-location case counts (when multiple locations are
    # selected) alongside its combined total, so the frontend can render one
    # integrated "Category | <location> cases... | Total" table instead of
    # separate per-location tables.
    rc_by_name = {rc["root_cause"]: rc for rc in rc_list}
    by_root_cause_grouped = []
    for group, causes in _ROOT_CAUSE_GROUPS.items():
        group_cases = 0
        group_hours = 0.0
        group_by_location = {loc: 0 for loc in location_columns}
        items = []
        for cause in causes:
            existing = rc_by_name.get(cause)
            cases = existing["total_cases"] if existing else 0
            hours = existing["total_downtime"] if existing else 0.0
            group_cases += cases
            group_hours += hours
            by_location = {}
            for loc in location_columns:
                n = by_location_rc.get(loc, {}).get(cause, 0)
                by_location[loc] = n
                group_by_location[loc] += n
            items.append({"root_cause": cause, "cases": cases, "hours": round(hours, 2), "by_location": by_location})
        by_root_cause_grouped.append({
            "group": group,
            "total_cases": group_cases,
            "total_hours": round(group_hours, 2),
            "by_location": group_by_location,
            "items": items,
        })

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
        "by_root_cause_grouped": by_root_cause_grouped,
        "location_columns": location_columns,
        "downtime_graph": downtime_graph,
        "work_orders":   work_orders,
        "date_from":     date_from,
        "date_to":       date_to,
        "location":      location,
        "locations":     location_list,
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
            WorkOrder.held_hours,
            Asset.id.label("asset_id"),
            Asset.name.label("asset_name"),
            Asset.downtime_divisor.label("asset_downtime_divisor"),
        )
        .join(Asset, WorkOrder.asset_id == Asset.id)
        .where(WorkOrder.is_deleted == False)
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
            downtime = max(working_hours_between(r.created_at, r.completed_at) - (r.held_hours or 0), 0.0)
        if not downtime:
            continue
        downtime = downtime / (r.asset_downtime_divisor or 1.0)

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
        select(WorkOrder.actual_hours, WorkOrder.affected_downtime, WorkOrder.created_at, WorkOrder.completed_at, WorkOrder.held_hours, Asset.downtime_divisor)
        .join(Asset, WorkOrder.asset_id == Asset.id)
        .where(
            WorkOrder.created_at >= month_start,
            WorkOrder.created_at < month_end,
            WorkOrder.affected_downtime == True,
            WorkOrder.is_deleted == False,
        )
    )
    if location:
        q = q.where(Asset.location == location)

    rows = (await db.execute(q)).fetchall()

    affected_downtime_hours = 0.0
    for r in rows:
        h = r.actual_hours
        if h is None and r.completed_at and r.created_at:
            h = max(working_hours_between(r.created_at, r.completed_at) - (r.held_hours or 0), 0.0)
        hrs = (h or 0) / (r.downtime_divisor or 1.0)
        # Below the minimum, the formula-adjusted downtime no longer counts
        # as production-affecting, so it's excluded here too (see
        # AFFECTED_DOWNTIME_MIN_HOURS).
        if hrs >= AFFECTED_DOWNTIME_MIN_HOURS:
            affected_downtime_hours += hrs

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
    result = await db.execute(select(Asset.id, Asset.name, Asset.asset_code, Asset.downtime_divisor).order_by(Asset.name))
    return [{"id": str(r.id), "name": r.name, "asset_code": r.asset_code, "downtime_divisor": r.downtime_divisor} for r in result.fetchall()]


@router.get("/locations")
async def get_locations_for_filter(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Returns distinct asset locations for the analysis filter dropdown."""
    result = await db.execute(select(Asset.location).where(Asset.location.is_not(None)).distinct().order_by(Asset.location))
    return [{"location": r.location} for r in result.fetchall() if r.location]


@router.get("/mrr-monthly-summary")
async def mrr_monthly_summary(
    year: Optional[int] = Query(None, description="Year, defaults to current"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Table 1 — Summary of Total MRR (Machine Repair Request = corrective work
    orders) by month, broken down by production line / location, with
    cases (count) and hours (downtime) for each, plus a combined total per
    month across all locations.
    """
    yr = year or datetime.utcnow().year
    year_start = datetime(yr, 1, 1)
    year_end = datetime(yr + 1, 1, 1)

    q = (
        select(
            WorkOrder.actual_hours, WorkOrder.created_at, WorkOrder.completed_at,
            WorkOrder.held_hours, Asset.location, Asset.downtime_divisor,
        )
        .join(Asset, WorkOrder.asset_id == Asset.id)
        .where(
            WorkOrder.type == WorkOrderType.corrective,
            WorkOrder.is_deleted == False,
            WorkOrder.created_at >= year_start,
            WorkOrder.created_at < year_end,
        )
    )
    rows = (await db.execute(q)).fetchall()

    # months[m][group] = {cases, hours}
    months = {
        m: {g: {"cases": 0, "hours": 0.0} for g in LOCATION_GROUP_ORDER}
        for m in range(1, 13)
    }
    for r in rows:
        m = r.created_at.month
        g = _location_group(r.location)
        hrs = _wo_downtime_hours(r.actual_hours, r.created_at, r.completed_at, r.held_hours, r.downtime_divisor)
        months[m][g]["cases"] += 1
        months[m][g]["hours"] += hrs

    out_months = []
    for m in range(1, 13):
        groups = months[m]
        for g in groups.values():
            g["hours"] = round(g["hours"], 2)
        total_cases = sum(g["cases"] for g in groups.values())
        total_hours = round(sum(g["hours"] for g in groups.values()), 2)
        out_months.append({
            "month": m,
            "month_label": MONTH_LABELS[m - 1],
            "groups": groups,
            "total": {"cases": total_cases, "hours": total_hours},
        })

    return {
        "year": yr,
        "location_groups": LOCATION_GROUP_ORDER,
        "months": out_months,
    }


@router.get("/mrr-autoline-detail")
async def mrr_autoline_detail(
    year: Optional[int] = Query(None, description="Year, defaults to current"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Table 2 — MRR Detail by Month: Autoline 1, Autoline 2, Supporting Line
    and Spray Paint Line corrective work orders by month. Autoline 1/2 are
    broken down by process station; Supporting Line and Spray Paint Line
    are broken down by individual machine, since they don't share Autoline's
    station concept. Each cell carries both the "overall" figure (every
    corrective case) and the "affected" figure (only cases where
    affected_downtime=True), so the frontend can switch views without a
    refetch.
    """
    yr = year or datetime.utcnow().year
    year_start = datetime(yr, 1, 1)
    year_end = datetime(yr + 1, 1, 1)

    # Supporting Line / Spray Paint Line categories = every asset actually
    # sited there (so a machine with zero cases this year still gets a row),
    # sorted by name for a stable, predictable order.
    asset_rows = (await db.execute(
        select(Asset.name, Asset.location).where(Asset.location.in_(ASSET_NAME_LINES))
    )).fetchall()
    asset_name_categories = {loc: [] for loc in ASSET_NAME_LINES}
    for r in asset_rows:
        asset_name_categories[r.location].append(r.name)
    for loc in ASSET_NAME_LINES:
        asset_name_categories[loc].sort()

    line_categories = {loc: ASSET_GROUP_ORDER for loc in STATION_LINES}
    line_categories.update(asset_name_categories)

    q = (
        select(
            WorkOrder.actual_hours, WorkOrder.created_at, WorkOrder.completed_at,
            WorkOrder.held_hours, WorkOrder.affected_downtime,
            Asset.location, Asset.name.label("asset_name"), Asset.downtime_divisor,
        )
        .join(Asset, WorkOrder.asset_id == Asset.id)
        .where(
            WorkOrder.type == WorkOrderType.corrective,
            WorkOrder.is_deleted == False,
            WorkOrder.created_at >= year_start,
            WorkOrder.created_at < year_end,
            Asset.location.in_(LINE_ORDER),
        )
    )
    rows = (await db.execute(q)).fetchall()

    def _empty_cell():
        return {"cases_all": 0, "hours_all": 0.0, "cases_affected": 0, "hours_affected": 0.0}

    # months[m][line][category] = cell
    months = {
        m: {loc: {c: _empty_cell() for c in line_categories[loc]} for loc in LINE_ORDER}
        for m in range(1, 13)
    }
    unclassified = 0
    for r in rows:
        if r.location in STATION_LINES:
            cat = _autoline_station_group(r.asset_name)
        else:
            cat = r.asset_name  # Supporting Line / Spray Paint Line: by machine name
        if cat is None or cat not in line_categories.get(r.location, []):
            unclassified += 1
            continue
        m = r.created_at.month
        hrs = _wo_downtime_hours(r.actual_hours, r.created_at, r.completed_at, r.held_hours, r.downtime_divisor)
        cell = months[m][r.location][cat]
        cell["cases_all"] += 1
        cell["hours_all"] += hrs
        if _effective_affected(r.affected_downtime, hrs):
            cell["cases_affected"] += 1
            cell["hours_affected"] += hrs

    out_months = []
    for m in range(1, 13):
        row = {"month": m, "month_label": MONTH_LABELS[m - 1]}
        for loc in LINE_ORDER:
            cats = months[m][loc]
            for c in cats.values():
                c["hours_all"] = round(c["hours_all"], 2)
                c["hours_affected"] = round(c["hours_affected"], 2)
            total = {
                "cases_all": sum(c["cases_all"] for c in cats.values()),
                "hours_all": round(sum(c["hours_all"] for c in cats.values()), 2),
                "cases_affected": sum(c["cases_affected"] for c in cats.values()),
                "hours_affected": round(sum(c["hours_affected"] for c in cats.values()), 2),
            }
            row[loc] = {"categories": cats, "total": total}
        out_months.append(row)

    return {
        "year": yr,
        "locations": LINE_ORDER,
        "line_categories": line_categories,
        "months": out_months,
        # Cases whose asset didn't match a tracked station (Autoline 1/2) or
        # wasn't found in the current asset list for its line (Supporting /
        # Spray Paint Line) — surfaced so they aren't silently dropped.
        "unclassified_cases": unclassified,
    }


@router.get("/mrr-month-snapshot")
async def mrr_month_snapshot(
    year:  int = Query(..., description="Year"),
    month: int = Query(..., description="Month 1-12"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Single-month MRR snapshot matching the reference report layout:
    Autoline 1 vs Autoline 2 by process station (Cases & Hours each), plus a
    small mini-table for Automatic Storage System (ASS) and General
    Facilities for the same month.
    """
    month_start = datetime(year, month, 1)
    month_end = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    AUTOLINES = ["Autoline 1", "Autoline 2"]

    q = (
        select(
            WorkOrder.actual_hours, WorkOrder.created_at, WorkOrder.completed_at,
            WorkOrder.held_hours, Asset.location, Asset.name.label("asset_name"), Asset.downtime_divisor,
        )
        .join(Asset, WorkOrder.asset_id == Asset.id)
        .where(
            WorkOrder.type == WorkOrderType.corrective,
            WorkOrder.is_deleted == False,
            WorkOrder.created_at >= month_start,
            WorkOrder.created_at < month_end,
        )
    )
    rows = (await db.execute(q)).fetchall()

    autoline = {loc: {g: {"cases": 0, "hours": 0.0} for g in ASSET_GROUP_ORDER} for loc in AUTOLINES}
    other = {"Automatic Storage System (ASS)": {"cases": 0, "hours": 0.0},
              "General Facilities": {"cases": 0, "hours": 0.0}}
    unclassified = 0

    for r in rows:
        hrs = _wo_downtime_hours(r.actual_hours, r.created_at, r.completed_at, r.held_hours, r.downtime_divisor)
        if r.location in AUTOLINES:
            grp = _autoline_asset_group(r.asset_name)
            if grp is None:
                unclassified += 1
                continue
            autoline[r.location][grp]["cases"] += 1
            autoline[r.location][grp]["hours"] += hrs
        else:
            og = _other_location_group(r.location, r.asset_name)
            if og:
                other[og]["cases"] += 1
                other[og]["hours"] += hrs

    autoline_out = {}
    for loc in AUTOLINES:
        groups = autoline[loc]
        for g in groups.values():
            g["hours"] = round(g["hours"], 2)
        total_cases = sum(g["cases"] for g in groups.values())
        total_hours = round(sum(g["hours"] for g in groups.values()), 2)
        autoline_out[loc] = {"groups": groups, "total": {"cases": total_cases, "hours": total_hours}}

    for og in other.values():
        og["hours"] = round(og["hours"], 2)

    return {
        "year": year,
        "month": month,
        "month_label": MONTH_LABELS[month - 1],
        "asset_groups": ASSET_GROUP_ORDER,
        "locations": AUTOLINES,
        "autoline": autoline_out,
        "other": other,
        "unclassified_cases": unclassified,
    }