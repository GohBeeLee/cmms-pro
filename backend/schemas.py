from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from typing import Optional
from uuid import UUID
from models import (
    AssetStatus, WorkOrderType, WorkOrderStatus,
    Priority, TaskStatus, UserRole, PMFrequency
)


# ── Auth ───────────────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ── User ───────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: UserRole = UserRole.technician

class UserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    is_present: Optional[bool] = None

class UserOut(BaseModel):
    id: UUID
    name: str
    email: str
    role: UserRole
    is_active: bool
    is_present: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Asset ──────────────────────────────────────────────────────────────────

class AssetCreate(BaseModel):
    asset_code: str
    name: str
    category: str
    location: str
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    status: AssetStatus = AssetStatus.operational
    purchase_date: Optional[datetime] = None
    notes: Optional[str] = None

class AssetUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    location: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    status: Optional[AssetStatus] = None
    notes: Optional[str] = None

class AssetOut(BaseModel):
    id: UUID
    asset_code: str
    name: str
    category: str
    location: str
    manufacturer: Optional[str]
    model: Optional[str]
    serial_number: Optional[str]
    status: AssetStatus
    purchase_date: Optional[datetime]
    last_maintained: Optional[datetime]
    notes: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Work Order ─────────────────────────────────────────────────────────────

class WorkOrderCreate(BaseModel):
    asset_id: UUID
    type: WorkOrderType
    priority: Priority = Priority.medium
    title: str
    description: Optional[str] = None
    due_date: Optional[datetime] = None
    estimated_hours: Optional[float] = None
    affected_downtime: bool = True

class WorkOrderUpdate(BaseModel):
    priority: Optional[Priority] = None
    status: Optional[WorkOrderStatus] = None
    title: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[datetime] = None
    estimated_hours: Optional[float] = None
    actual_hours: Optional[float] = None
    affected_downtime: Optional[bool] = None
    completed_at: Optional[datetime] = None

class WorkOrderOut(BaseModel):
    id: UUID
    wo_number: str
    asset_id: UUID
    asset: Optional[AssetOut] = None
    type: WorkOrderType
    priority: Priority
    status: WorkOrderStatus
    title: str
    description: Optional[str]
    due_date: Optional[datetime]
    estimated_hours: Optional[float]
    actual_hours: Optional[float]
    affected_downtime: bool
    hold_started_at: Optional[datetime] = None
    held_hours: float = 0.0
    completed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── PM Schedule ────────────────────────────────────────────────────────────

class PMScheduleCreate(BaseModel):
    asset_id: UUID
    title: str
    description: Optional[str] = None
    frequency: PMFrequency
    interval_days: int = Field(gt=0)
    estimated_hours: Optional[float] = None
    assigned_to: Optional[UUID] = None
    next_due: datetime

class PMScheduleUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    frequency: Optional[PMFrequency] = None
    interval_days: Optional[int] = None
    estimated_hours: Optional[float] = None
    assigned_to: Optional[UUID] = None
    next_due: Optional[datetime] = None
    is_active: Optional[bool] = None

class PMScheduleOut(BaseModel):
    id: UUID
    asset_id: UUID
    asset: Optional[AssetOut] = None
    title: str
    description: Optional[str]
    frequency: PMFrequency
    interval_days: int
    estimated_hours: Optional[float]
    assigned_to: Optional[UUID]
    last_triggered: Optional[datetime]
    next_due: datetime
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Task Assignment ────────────────────────────────────────────────────────

class TaskAssignmentCreate(BaseModel):
    work_order_id: UUID
    user_id: UUID
    notes: Optional[str] = None

class TaskAssignmentUpdate(BaseModel):
    status: Optional[TaskStatus] = None
    notes: Optional[str] = None

class TaskAssignmentOut(BaseModel):
    id: UUID
    work_order_id: UUID
    user_id: UUID
    user: Optional[UserOut] = None
    status: TaskStatus
    notes: Optional[str]
    assigned_at: datetime
    completed_at: Optional[datetime]

    model_config = {"from_attributes": True}


# ── Spare Part ─────────────────────────────────────────────────────────────

class SparePartCreate(BaseModel):
    part_code: str
    name: str
    description: Optional[str] = None
    category: Optional[str] = None
    unit: str = "pcs"
    quantity_on_hand: int = 0
    reorder_level: int = 5
    unit_cost: Optional[float] = None
    supplier: Optional[str] = None
    location: Optional[str] = None
    barcode: Optional[str] = None
    used_on_asset: Optional[str] = None

class SparePartUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    quantity_on_hand: Optional[int] = None
    reorder_level: Optional[int] = None
    unit_cost: Optional[float] = None
    supplier: Optional[str] = None
    location: Optional[str] = None
    barcode: Optional[str] = None
    used_on_asset: Optional[str] = None

class SparePartOut(BaseModel):
    id: UUID
    part_code: str
    name: str
    description: Optional[str]
    category: Optional[str]
    unit: str
    quantity_on_hand: int
    reorder_level: int
    unit_cost: Optional[float]
    supplier: Optional[str]
    location: Optional[str]
    barcode: Optional[str]
    used_on_asset: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Parts Used ─────────────────────────────────────────────────────────────

class PartsUsedCreate(BaseModel):
    spare_part_id: UUID
    quantity_used: int = Field(gt=0)

class PartsUsedOut(BaseModel):
    id: UUID
    work_order_id: UUID
    spare_part_id: UUID
    spare_part: Optional[SparePartOut] = None
    quantity_used: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Dashboard KPI ──────────────────────────────────────────────────────────

class DashboardKPI(BaseModel):
    total_assets: int
    assets_under_maintenance: int
    open_work_orders: int
    overdue_work_orders: int
    work_orders_completed_today: int
    pm_schedules_due_soon: int
    low_stock_parts: int
    total_technicians: int