"""
Export & Import Router
=======================
All routes under a single /data prefix to avoid FastAPI route conflicts.

GET  /data/export/history.xlsx     — download work order history as Excel
GET  /data/import/template         — download the asset import template
POST /data/import/assets           — upload Excel to bulk create/update assets
"""

import io
import os
import base64
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse, FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db import get_db
from models import Asset, WorkOrder, WorkOrderStatus, AssetStatus, User
from auth import get_current_user, forbid_viewer
from routers.analysis import MY_TZ_OFFSET

# Single router — no prefix conflict
router = APIRouter(prefix="/data", tags=["export_import"], dependencies=[Depends(forbid_viewer)])


# ─────────────────────────────────────────────────────────────────────────────
# TEMPLATE DOWNLOAD  GET /data/import/template
# Must be declared BEFORE the POST /data/import/assets route
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/import/template")
async def download_asset_template(
    _: User = Depends(get_current_user),
):
    """Serve the asset import Excel template."""
    # Look for a pre-built template file first
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(base, "CMMS_Asset_Import_Template.xlsx"),
        os.path.join(base, "..", "CMMS_Asset_Import_Template.xlsx"),
        "/opt/cmms/CMMS_Asset_Import_Template.xlsx",
    ]
    for path in candidates:
        if os.path.exists(path):
            return FileResponse(
                path,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename="CMMS_Asset_Import_Template.xlsx",
            )

    # Generate template on the fly if file not found
    return _generate_template()


def _generate_template():
    """Build and return the Excel template as a streaming response."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise HTTPException(500, "openpyxl not installed. Run: pip install openpyxl")

    wb  = Workbook()
    ws  = wb.active
    ws.title = "Asset Import"

    thin   = Side(style="thin",   color="CCCCCC")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # Row 1: Title
    ws.merge_cells("A1:J1")
    ws["A1"] = "CMMS Pro — Asset Import Template"
    ws["A1"].font      = Font(name="Arial", bold=True, size=13, color="1E3A5F")
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws["A1"].fill      = PatternFill("solid", start_color="EBF3FB")
    ws.row_dimensions[1].height = 30

    # Row 2: Instructions
    ws.merge_cells("A2:J2")
    ws["A2"] = (
        "Required columns: asset_code, name, category, location.  "
        "Do NOT modify column headers.  "
        "Save as .xlsx before uploading."
    )
    ws["A2"].font      = Font(name="Arial", italic=True, size=10, color="5A6A7A")
    ws["A2"].alignment = Alignment(horizontal="center")
    ws["A2"].fill      = PatternFill("solid", start_color="FFF9C4")
    ws.row_dimensions[2].height = 18

    # Row 3: spacer
    ws.row_dimensions[3].height = 6

    # Row 4: Headers
    headers = [
        "asset_code *", "name *", "category *", "location *",
        "manufacturer", "model", "serial_number",
        "status", "purchase_date", "notes",
    ]
    hints = [
        "Unique code e.g. PMP-001",
        "Full machine name",
        "Multi Handling System (MHS) / Machine / Robot / Forklift (Gas) / Forklift (Battery) / Forklift (Diesel) / Utilities / Other",
        "Physical location e.g. Building A - Ground Floor",
        "Brand name (optional)",
        "Model number (optional)",
        "Serial or tag number (optional)",
        "operational (default) / under_maintenance / out_of_service / decommissioned",
        "YYYY-MM-DD  (optional)",
        "Any additional notes (optional)",
    ]
    hdr_font = Font(name="Arial", bold=True, color="FFFFFF", size=10)
    hdr_fill = PatternFill("solid", start_color="1E3A5F")
    hint_fill = PatternFill("solid", start_color="EBF3FB")

    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=4, column=c, value=h)
        cell.font = hdr_font; cell.fill = hdr_fill; cell.border = border
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        hcell = ws.cell(row=5, column=c, value=hints[c-1])
        hcell.font = Font(name="Arial", italic=True, color="5A6A7A", size=9)
        hcell.fill = hint_fill; hcell.border = border
        hcell.alignment = Alignment(wrap_text=True, vertical="top")

    ws.row_dimensions[4].height = 26
    ws.row_dimensions[5].height = 34

    # Sample data rows 6-10
    samples = [
        ["PMP-001","Centrifugal Pump A","Pump","Building A - Ground Floor","Grundfos","CM10-4","GF-2021-00341","operational","2021-03-15","Primary water supply pump"],
        ["MTR-001","Conveyor Drive Motor #1","Motor","Production Line 1","ABB","M2BAX 90","ABB-MTR-9921","operational","2020-08-10",""],
        ["CMP-001","Air Compressor Unit 1","Compressor","Utility Room B","Atlas Copco","GA15","AC-GA15-4423","operational","2019-06-01",""],
        ["HVAC-001","AHU - Production Hall","HVAC","Rooftop Level 2","Carrier","AHU-30XA","CAR-30XA-881","operational","",""],
        ["GEN-001","Standby Generator","Generator","Genset Room - Block C","Caterpillar","C15","CAT-C15-0042","operational","2018-01-20","500 kVA"],
    ]
    fills = [
        PatternFill("solid", start_color="FFFFFF"),
        PatternFill("solid", start_color="F7FBFF"),
    ]
    for r, row in enumerate(samples, 6):
        for c, v in enumerate(row, 1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.font = Font(name="Arial", size=10)
            cell.fill = fills[r % 2]
            cell.border = border
        ws.row_dimensions[r].height = 18

    # 15 blank rows for user data
    for r in range(11, 26):
        for c in range(1, 11):
            cell = ws.cell(row=r, column=c, value="")
            cell.fill = fills[r % 2]
            cell.border = border
        ws.row_dimensions[r].height = 18

    # Column widths
    for i, w in enumerate([16,28,20,28,18,14,18,22,14,32], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.freeze_panes = "A6"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="CMMS_Asset_Import_Template.xlsx"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
# ASSET IMPORT  POST /data/import/assets
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/import/assets")
async def import_assets(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    Upload a filled-in Excel template to bulk create/update assets.
    - New asset_code  → INSERT
    - Existing code   → UPDATE
    Returns a summary: inserted, updated, skipped.
    """
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "Only .xlsx files are accepted. Please save your file as Excel Workbook (.xlsx).")

    try:
        from openpyxl import load_workbook
    except ImportError:
        raise HTTPException(500, "openpyxl not installed on server. Run: pip install openpyxl")

    content = await file.read()
    try:
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        raise HTTPException(400, "Cannot open file — make sure it is a valid .xlsx Excel file.")

    # Find the asset sheet (first sheet containing "asset" or "import" in name, else sheet 1)
    sheet_name = wb.sheetnames[0]
    for name in wb.sheetnames:
        if "asset" in name.lower() or "import" in name.lower():
            sheet_name = name
            break
    ws = wb[sheet_name]

    # Find header row — look for "asset_code" in any of the first 10 rows
    header_row_idx = None
    for row in ws.iter_rows(min_row=1, max_row=10):
        for cell in row:
            val = str(cell.value or "").lower().replace("*","").strip()
            if val == "asset_code":
                header_row_idx = cell.row
                break
        if header_row_idx:
            break

    if not header_row_idx:
        raise HTTPException(
            400,
            "Cannot find 'asset_code' header. "
            "Make sure you are using the official CMMS template and have not renamed the columns."
        )

    # Map column names to 0-based indices
    col_map = {}
    for cell in ws[header_row_idx]:
        if cell.value:
            key = str(cell.value).lower().replace("*","").strip()
            col_map[key] = cell.column - 1

    missing = [r for r in ["asset_code","name","category","location"] if r not in col_map]
    if missing:
        raise HTTPException(400, f"Missing required columns: {', '.join(missing)}")

    # Load existing assets for upsert
    existing = {a.asset_code: a for a in (await db.execute(select(Asset))).scalars().all()}

    valid_status   = {s.value for s in AssetStatus}
    valid_category = {"Multi Handling System (MHS)","Machine","Robot","Forklift (Gas)","Forklift (Battery)","Forklift (Diesel)","Utilities","Other"}

    inserted, updated, skipped = 0, 0, []

    for row in ws.iter_rows(min_row=header_row_idx + 1, values_only=True):
        # Skip blank rows
        if all(v is None or str(v).strip() == "" for v in row):
            continue

        def gcol(key):
            idx = col_map.get(key)
            if idx is None or idx >= len(row):
                return ""
            return str(row[idx] or "").strip()

        asset_code    = gcol("asset_code")
        name          = gcol("name")
        category      = gcol("category")
        location      = gcol("location")
        manufacturer  = gcol("manufacturer") or None
        model         = gcol("model")         or None
        serial_number = gcol("serial_number") or None
        notes         = gcol("notes")         or None
        status_val    = gcol("status").lower() or "operational"
        pd_raw        = gcol("purchase_date")

        # Validate required fields
        if not asset_code:
            skipped.append({"reason": "Missing asset_code (blank row skipped)"}); continue
        if not name:
            skipped.append({"asset_code": asset_code, "reason": "Missing name"}); continue
        if not location:
            skipped.append({"asset_code": asset_code, "reason": "Missing location"}); continue

        # Normalise category
        if category not in valid_category:
            category = "Other"

        # Normalise status
        if status_val not in valid_status:
            status_val = "operational"

        # Parse purchase_date
        purchase_date = None
        if pd_raw:
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
                try:
                    purchase_date = datetime.strptime(pd_raw, fmt)
                    break
                except ValueError:
                    pass

        if asset_code in existing:
            # UPDATE existing asset
            a = existing[asset_code]
            a.name          = name
            a.category      = category
            a.location      = location
            a.manufacturer  = manufacturer
            a.model         = model
            a.serial_number = serial_number
            a.status        = AssetStatus(status_val)
            a.notes         = notes
            if purchase_date:
                a.purchase_date = purchase_date
            updated += 1
        else:
            # INSERT new asset
            a = Asset(
                asset_code    = asset_code,
                name          = name,
                category      = category,
                location      = location,
                manufacturer  = manufacturer,
                model         = model,
                serial_number = serial_number,
                status        = AssetStatus(status_val),
                purchase_date = purchase_date,
                notes         = notes,
            )
            db.add(a)
            existing[asset_code] = a
            inserted += 1

    await db.flush()

    return {
        "success":         True,
        "inserted":        inserted,
        "updated":         updated,
        "skipped":         len(skipped),
        "skipped_details": skipped,
        "message":         f"Import complete: {inserted} inserted, {updated} updated, {len(skipped)} skipped.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# HISTORY EXPORT  GET /data/export/history.xlsx
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/export/history.xlsx")
async def export_history(
    status:    Optional[str] = None,
    date_from: Optional[str] = None,
    date_to:   Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Export work order history to a formatted Excel file."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise HTTPException(500, "openpyxl not installed")

    # Query
    q = select(WorkOrder).options(selectinload(WorkOrder.asset)).order_by(WorkOrder.created_at.desc())
    if status:
        q = q.where(WorkOrder.status == status)
    if date_from:
        try:
            q = q.where(WorkOrder.created_at >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        try:
            dt = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            q = q.where(WorkOrder.created_at <= dt)
        except ValueError:
            pass

    wos = (await db.execute(q)).scalars().all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Work Order History"

    thin   = Side(style="thin",   color="CCCCCC")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    # Title
    ws.merge_cells("A1:P1")
    ws["A1"] = "CMMS Pro — Work Order History Report"
    ws["A1"].font      = Font(name="Arial", bold=True, size=13, color="1E3A5F")
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws["A1"].fill      = PatternFill("solid", start_color="EBF3FB")
    ws.row_dimensions[1].height = 30

    ws.merge_cells("A2:P2")
    ws["A2"] = f"Generated: {datetime.utcnow().strftime('%d %b %Y %H:%M')} UTC   |   Total: {len(wos)} records"
    ws["A2"].font      = Font(name="Arial", italic=True, size=10, color="5A6A7A")
    ws["A2"].alignment = Alignment(horizontal="center")
    ws.row_dimensions[2].height = 16

    ws.row_dimensions[3].height = 6  # spacer

    headers = [
        "WO Number","Machine","Asset Code","Problem Category","Problem Description",
        "Type","Priority","Status","Operator",
        "Root Cause Analysis","Action Taken","Completed By",
        "Request Time (MYT)","Completed Time (MYT)",
        "Downtime / Actual Hours","Downtime Type",
    ]
    hdr_font = Font(name="Arial", bold=True, color="FFFFFF", size=10)
    hdr_fill = PatternFill("solid", start_color="1E3A5F")
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=4, column=c, value=h)
        cell.font = hdr_font; cell.fill = hdr_fill; cell.border = border
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[4].height = 26

    status_colors = {
        "completed":   "DCFCE7",
        "open":        "DBEAFE",
        "in_progress": "EDE9FE",
        "on_hold":     "FFEDD5",
        "cancelled":   "F1F5F9",
    }

    def parse_field(desc, key):
        for line in (desc or "").splitlines():
            if line.strip().startswith(key):
                parts = line.split(":", 1)
                return parts[1].strip() if len(parts) == 2 else ""
        return ""

    def downtime(wo):
        if wo.actual_hours:
            return round(wo.actual_hours, 2)
        if wo.completed_at and wo.created_at:
            return round((wo.completed_at - wo.created_at).total_seconds() / 3600, 2)
        if wo.created_at:
            return round((datetime.utcnow() - wo.created_at).total_seconds() / 3600, 2)
        return ""

    data_font = Font(name="Arial", size=10)

    for r, wo in enumerate(wos, 5):
        fill = PatternFill("solid", start_color=status_colors.get(wo.status.value if wo.status else "", "FFFFFF"))
        row_data = [
            wo.wo_number,
            wo.asset.name        if wo.asset else "—",
            wo.asset.asset_code  if wo.asset else "—",
            parse_field(wo.description, "Category") or "—",
            parse_field(wo.description, "Problem")  or "—",
            wo.type.value        if wo.type     else "—",
            wo.priority.value    if wo.priority else "—",
            wo.status.value      if wo.status   else "—",
            parse_field(wo.description, "Submitted by")  or "—",
            parse_field(wo.description, "Root cause")    or "—",
            parse_field(wo.description, "Actions taken") or "—",
            parse_field(wo.description, "Completed by")  or "—",
            (wo.created_at + MY_TZ_OFFSET).strftime("%d %b %Y %H:%M")   if wo.created_at   else "—",
            (wo.completed_at + MY_TZ_OFFSET).strftime("%d %b %Y %H:%M") if wo.completed_at else "—",
            downtime(wo),
            "Affected" if getattr(wo, "affected_downtime", True) else "Non affected",
        ]
        for c, v in enumerate(row_data, 1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.font = data_font; cell.fill = fill; cell.border = border
            cell.alignment = Alignment(vertical="center", wrap_text=(c in (5, 10, 11)))
        ws.row_dimensions[r].height = 18

    # Column widths
    for i, w in enumerate([16,22,14,18,34,12,12,14,18,22,30,18,18,18,18,14], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.freeze_panes = "A5"

    # Summary sheet
    ws2 = wb.create_sheet("Summary")
    ws2.column_dimensions["A"].width = 26
    ws2.column_dimensions["B"].width = 14

    ws2.merge_cells("A1:B1")
    ws2["A1"] = "Summary"
    ws2["A1"].font      = Font(name="Arial", bold=True, size=13, color="1E3A5F")
    ws2["A1"].alignment = Alignment(horizontal="center")
    ws2["A1"].fill      = PatternFill("solid", start_color="EBF3FB")
    ws2.row_dimensions[1].height = 28

    from collections import Counter
    sc = Counter(wo.status.value   for wo in wos)
    pc = Counter(wo.priority.value for wo in wos)

    rows = [
        ("",""),("Total Work Orders", len(wos)),("",""),
        ("BY STATUS",""),
    ] + [(k.replace("_"," ").title(), v) for k,v in sc.items()] + [
        ("",""),("BY PRIORITY",""),
    ] + [(k.title(), v) for k,v in pc.items()]

    for r, (k, v) in enumerate(rows, 2):
        if k in ("BY STATUS","BY PRIORITY"):
            ws2.cell(row=r,column=1,value=k).font = Font(name="Arial",bold=True,size=11,color="1E3A5F")
            ws2.cell(row=r,column=1).fill = PatternFill("solid",start_color="EBF3FB")
            ws2.merge_cells(f"A{r}:B{r}")
        else:
            ws2.cell(row=r,column=1,value=k).font = Font(name="Arial",size=10,bold=(k=="Total Work Orders"))
            ws2.cell(row=r,column=2,value=v).font = Font(name="Arial",size=10)
        ws2.row_dimensions[r].height = 18

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    fname = f"WO_History_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )