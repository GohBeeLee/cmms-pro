"""
CMMS — FastAPI main (fixed version)
Fixes:
  1. Serves /uploads/ as static files so photos work
  2. Registers export/import routers
  3. Registers users router for technician assignment
"""
import logging, os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from db import engine, Base, get_db, settings
from models import PMSchedule, User, UserRole
from websocket_manager import ws_manager
from auth import router as auth_router, hash_password
from routers.assets import router as assets_router
from routers.work_orders import router as wo_router
from routers.inventory import router as inventory_router
from routers.pm_schedules import router as pm_router, _generate_wo_from_pm
from routers.requests import router as requests_router
from routers.export_import import router as data_router
from routers.users import router as users_router
from routers.stock import router as stock_router
from routers.analysis import router as analysis_router

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR  = os.path.join(BASE_DIR, "..", "frontend")


async def run_pm_check():
    from db import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            now = datetime.utcnow()
            result = await db.execute(
                select(PMSchedule).where(PMSchedule.is_active == True, PMSchedule.next_due <= now)
            )
            for pm in result.scalars().all():
                await _generate_wo_from_pm(pm, db, triggered_by=None)
            await db.commit()
        except Exception as e:
            logger.error("PM check error: %s", e)
            await db.rollback()


async def ensure_schema_updates():
    """Apply lightweight SQLite-safe schema updates for existing local databases."""
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    async with engine.begin() as conn:
        columns = (await conn.execute(text("PRAGMA table_info(work_orders)"))).mappings().all()
        names = {col["name"] for col in columns}
        if "affected_downtime" not in names:
            await conn.execute(text("ALTER TABLE work_orders ADD COLUMN affected_downtime BOOLEAN NOT NULL DEFAULT 1"))
        spare_columns = (await conn.execute(text("PRAGMA table_info(spare_parts)"))).mappings().all()
        spare_names = {col["name"] for col in spare_columns}
        if "barcode" not in spare_names:
            await conn.execute(text("ALTER TABLE spare_parts ADD COLUMN barcode VARCHAR(100)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_spare_parts_barcode ON spare_parts (barcode)"))
        if "used_on_asset" not in spare_names:
            await conn.execute(text("ALTER TABLE spare_parts ADD COLUMN used_on_asset VARCHAR(200)"))
        user_columns = (await conn.execute(text("PRAGMA table_info(users)"))).mappings().all()
        user_names = {col["name"] for col in user_columns}
        if "is_present" not in user_names:
            await conn.execute(text("ALTER TABLE users ADD COLUMN is_present BOOLEAN NOT NULL DEFAULT 1"))


DEFAULT_ADMIN_EMAIL    = "admin@cmms.com"
DEFAULT_ADMIN_PASSWORD = "admin1234"
DEFAULT_ADMIN_NAME     = "Administrator"

async def ensure_default_admin():
    """
    Guarantees a working admin account always exists, even on a brand new
    or freshly-deleted database. Runs on every startup:
      - If no user with DEFAULT_ADMIN_EMAIL exists, create one.
      - If it exists but isn't admin/active, restore those flags.
    Does NOT touch the password of an existing account, so changing the
    password via the app later is preserved across restarts.
    """
    from db import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(select(User).where(User.email == DEFAULT_ADMIN_EMAIL))
            admin = result.scalar_one_or_none()
            if admin is None:
                admin = User(
                    name=DEFAULT_ADMIN_NAME,
                    email=DEFAULT_ADMIN_EMAIL,
                    hashed_password=hash_password(DEFAULT_ADMIN_PASSWORD),
                    role=UserRole.admin,
                    is_active=True,
                    is_present=True,
                )
                db.add(admin)
                logger.info("Default admin account created: %s", DEFAULT_ADMIN_EMAIL)
            else:
                changed = False
                if admin.role != UserRole.admin:
                    admin.role = UserRole.admin; changed = True
                if not admin.is_active:
                    admin.is_active = True; changed = True
                if changed:
                    logger.info("Default admin account restored: %s", DEFAULT_ADMIN_EMAIL)
            await db.commit()
        except Exception as e:
            logger.error("ensure_default_admin error: %s", e)
            await db.rollback()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_schema_updates()
    await ensure_default_admin()
    logger.info("Database tables ready")
    await ws_manager.startup()
    scheduler.add_job(run_pm_check, "interval", hours=1, id="pm_check")
    scheduler.start()
    logger.info("CMMS started")
    yield
    scheduler.shutdown(wait=False)
    await ws_manager.shutdown()
    await engine.dispose()


app = FastAPI(title="CMMS API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(assets_router)
app.include_router(wo_router)
app.include_router(inventory_router)
app.include_router(pm_router)
app.include_router(requests_router)    # public — no auth needed
app.include_router(data_router)        # export + import (Excel)
app.include_router(users_router)       # technician list for assignment
app.include_router(stock_router)       # stock in/out + history
app.include_router(analysis_router)    # work order downtime analysis


# ── WebSocket ─────────────────────────────────────────────────────────────
@app.websocket("/ws/{room}")
async def websocket_endpoint(websocket: WebSocket, room: str, token: str = Query(...)):
    from jose import jwt as jose_jwt, JWTError
    try:
        jose_jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        await websocket.close(code=4001); return
    await ws_manager.connect(websocket, room)
    try:
        await websocket.send_json({"room": room, "type": "connection.established", "payload": {}})
        while True:
            data = await websocket.receive_text()
            if data == "ping": await websocket.send_text("pong")
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket, room)


# ── Dashboard KPI ─────────────────────────────────────────────────────────
@app.get("/dashboard/kpi", tags=["dashboard"])
async def get_kpi(db: AsyncSession = Depends(get_db)):
    from models import Asset, WorkOrder, SparePart, User as UserModel, AssetStatus, WorkOrderStatus
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ahead  = now + timedelta(days=7)
    return {
        "total_assets":                (await db.execute(select(func.count()).select_from(Asset))).scalar(),
        "assets_under_maintenance":    (await db.execute(select(func.count()).select_from(Asset).where(Asset.status == AssetStatus.under_maintenance))).scalar(),
        "open_work_orders":            (await db.execute(select(func.count()).select_from(WorkOrder).where(WorkOrder.status.in_([WorkOrderStatus.open, WorkOrderStatus.in_progress])))).scalar(),
        "overdue_work_orders":         (await db.execute(select(func.count()).select_from(WorkOrder).where(WorkOrder.status.notin_([WorkOrderStatus.completed, WorkOrderStatus.cancelled]), WorkOrder.due_date < now))).scalar(),
        "work_orders_completed_today": (await db.execute(select(func.count()).select_from(WorkOrder).where(WorkOrder.completed_at >= today_start))).scalar(),
        "pm_schedules_due_soon":       (await db.execute(select(func.count()).select_from(PMSchedule).where(PMSchedule.is_active == True, PMSchedule.next_due <= week_ahead))).scalar(),
        "low_stock_parts":             (await db.execute(select(func.count()).select_from(SparePart).where(SparePart.quantity_on_hand <= SparePart.reorder_level))).scalar(),
        "total_technicians":           (await db.execute(select(func.count()).select_from(UserModel).where(UserModel.is_active == True))).scalar(),
    }


# ── Serve frontend ────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def serve_frontend():
    path = os.path.join(FRONTEND_DIR, "index.html")
    return FileResponse(path) if os.path.exists(path) else HTMLResponse("<h2>Frontend not found.</h2>")

@app.get("/request", include_in_schema=False)
@app.get("/request/{asset_id}", include_in_schema=False)
async def serve_request_form(asset_id: str = None):
    path = os.path.join(FRONTEND_DIR, "request.html")
    return FileResponse(path) if os.path.exists(path) else HTMLResponse("<h2>Request form not found.</h2>")

@app.get("/qr", include_in_schema=False)
async def serve_qr():
    url = str(app.url_path_for("serve_request_form")).rstrip("/")
    html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"/>
<title>CMMS QR Code</title>
<style>body{{font-family:sans-serif;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;background:#f8fafc;}}
.box{{background:#fff;border-radius:16px;padding:40px;text-align:center;box-shadow:0 4px 20px rgba(0,0,0,.08);max-width:360px;width:100%;}}
h1{{color:#1e293b;font-size:20px;margin-bottom:4px;}} p{{color:#64748b;font-size:13px;margin-bottom:20px;}}
img{{border-radius:12px;border:1px solid #e2e8f0;margin-bottom:16px;}}
.url{{font-size:11px;color:#94a3b8;word-break:break-all;margin-bottom:20px;}}
button{{background:#2563eb;color:#fff;border:none;padding:12px 28px;border-radius:10px;font-size:15px;font-weight:600;cursor:pointer;}}
@media print{{button{{display:none;}}body{{background:#fff;}}}}</style></head>
<body><div class="box">
<h1>&#128295; Machine Repair Request</h1>
<p>Scan this QR code to report a machine problem</p>
<img src="https://api.qrserver.com/v1/create-qr-code/?size=280x280&data={url}" width="280" height="280"/>
<div class="url">{url}</div>
<button onclick="window.print()">&#128438; Print QR Code</button>
</div></body></html>"""
    return HTMLResponse(html)


# ── Health ────────────────────────────────────────────────────────────────
@app.get("/health", tags=["system"])
async def health(db: AsyncSession = Depends(get_db)):
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}