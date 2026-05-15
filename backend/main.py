"""
CMMS — FastAPI main entry point (Render.com free hosting version)
"""
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from db import engine, Base, get_db, settings
from models import PMSchedule
from websocket_manager import ws_manager
from auth import router as auth_router
from routers.assets import router as assets_router
from routers.work_orders import router as wo_router
from routers.inventory import router as inventory_router
from routers.pm_schedules import router as pm_router, _generate_wo_from_pm

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_INDEX = os.path.join(BASE_DIR, "..", "frontend", "index.html")


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database ready")

    # Auto-seed if database is empty
    from db import AsyncSessionLocal
    from models import User
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(func.count()).select_from(User))
        if result.scalar() == 0:
            logger.info("Database empty — auto-seeding...")
            try:
                import seed
                await seed.seed()
                logger.info("Auto-seed complete")
            except Exception as e:
                logger.error("Auto-seed failed: %s", e)

    await ws_manager.startup()
    scheduler.add_job(run_pm_check, "interval", hours=1, id="pm_check")
    scheduler.start()
    logger.info("CMMS started")
    yield
    scheduler.shutdown(wait=False)
    await ws_manager.shutdown()
    await engine.dispose()


app = FastAPI(title="CMMS API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(assets_router)
app.include_router(wo_router)
app.include_router(inventory_router)
app.include_router(pm_router)


@app.websocket("/ws/{room}")
async def websocket_endpoint(websocket: WebSocket, room: str, token: str = Query(...)):
    from jose import jwt as jose_jwt, JWTError
    try:
        jose_jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        await websocket.close(code=4001)
        return
    await ws_manager.connect(websocket, room)
    try:
        await websocket.send_json({"room": room, "type": "connection.established", "payload": {}})
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket, room)


@app.get("/dashboard/kpi", tags=["dashboard"])
async def get_kpi(db: AsyncSession = Depends(get_db)):
    from models import Asset, WorkOrder, SparePart, User as UserModel
    from models import AssetStatus, WorkOrderStatus
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ahead = now + timedelta(days=7)
    return {
        "total_assets":                (await db.execute(select(func.count()).select_from(Asset))).scalar(),
        "assets_under_maintenance":    (await db.execute(select(func.count()).select_from(Asset).where(Asset.status == AssetStatus.under_maintenance))).scalar(),
        "open_work_orders":            (await db.execute(select(func.count()).select_from(WorkOrder).where(WorkOrder.status.in_([WorkOrderStatus.open, WorkOrderStatus.in_progress])))).scalar(),
        "overdue_work_orders":         (await db.execute(select(func.count()).select_from(WorkOrder).where(WorkOrder.status.notin_([WorkOrderStatus.completed, WorkOrderStatus.cancelled]), WorkOrder.due_date < now))).scalar(),
        "work_orders_completed_today": (await db.execute(select(func.count()).select_from(WorkOrder).where(WorkOrder.completed_at >= today_start))).scalar(),
        "pm_schedules_due_soon":       (await db.execute(select(func.count()).select_from(PMSchedule).where(PMSchedule.is_active == True, PMSchedule.next_due <= week_ahead))).scalar(),
        "low_stock_parts":             (await db.execute(select(func.count()).select_from(SparePart).where(SparePart.quantity_on_hand <= SparePart.reorder_level))).scalar(),
        "total_technicians":           (await db.execute(select(func.count()).select_from(UserModel).where(UserModel.is_active == True))).scalar(),
        "total_pm":                    (await db.execute(select(func.count()).select_from(PMSchedule).where(PMSchedule.is_active == True))).scalar(),
    }


@app.get("/", include_in_schema=False)
async def serve_frontend():
    if os.path.exists(FRONTEND_INDEX):
        return FileResponse(FRONTEND_INDEX)
    return HTMLResponse("<h2>Frontend not found.</h2>")


@app.get("/health", tags=["system"])
async def health(db: AsyncSession = Depends(get_db)):
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}