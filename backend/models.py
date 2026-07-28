import uuid
from datetime import datetime
from sqlalchemy import (
    String, Integer, Boolean, Text, DateTime, ForeignKey, Enum as SAEnum, Float
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from db import Base
import enum


# ── Enums ──────────────────────────────────────────────────────────────────

class AssetStatus(str, enum.Enum):
    operational = "operational"
    under_maintenance = "under_maintenance"
    out_of_service = "out_of_service"
    decommissioned = "decommissioned"

class WorkOrderType(str, enum.Enum):
    corrective = "corrective"
    preventive = "preventive"
    inspection = "inspection"
    emergency = "emergency"

class WorkOrderStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    on_hold = "on_hold"
    completed = "completed"
    cancelled = "cancelled"

class Priority(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"

class TaskStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    in_progress = "in_progress"
    completed = "completed"

class UserRole(str, enum.Enum):
    admin = "admin"
    manager = "manager"
    technician = "technician"
    production = "production"
    viewer = "viewer"

class PMFrequency(str, enum.Enum):
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"
    quarterly = "quarterly"
    biannual = "biannual"
    annual = "annual"
    custom = "custom"


# ── Models ──────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.technician)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_present: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    assignments: Mapped[list["TaskAssignment"]] = relationship(back_populates="user")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100))
    location: Mapped[str] = mapped_column(String(200))
    manufacturer: Mapped[str | None] = mapped_column(String(150))
    model: Mapped[str | None] = mapped_column(String(150))
    serial_number: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[AssetStatus] = mapped_column(SAEnum(AssetStatus), default=AssetStatus.operational)
    purchase_date: Mapped[datetime | None] = mapped_column(DateTime)
    last_maintained: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    work_orders: Mapped[list["WorkOrder"]] = relationship(back_populates="asset")
    pm_schedules: Mapped[list["PMSchedule"]] = relationship(back_populates="asset")


class WorkOrder(Base):
    __tablename__ = "work_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wo_number: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id"))
    type: Mapped[WorkOrderType] = mapped_column(SAEnum(WorkOrderType))
    priority: Mapped[Priority] = mapped_column(SAEnum(Priority), default=Priority.medium)
    status: Mapped[WorkOrderStatus] = mapped_column(SAEnum(WorkOrderStatus), default=WorkOrderStatus.open)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[datetime | None] = mapped_column(DateTime)
    estimated_hours: Mapped[float | None] = mapped_column(Float)
    actual_hours: Mapped[float | None] = mapped_column(Float)
    affected_downtime: Mapped[bool] = mapped_column(Boolean, default=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Downtime-pause tracking for the "on_hold" status — while a work order
    # is on hold, elapsed time shouldn't count against its downtime. When it
    # goes on hold, hold_started_at records when; when it leaves on_hold
    # (back to in_progress/open, or completed), the working hours spent in
    # that hold window get added to held_hours and hold_started_at is
    # cleared. Downtime is then computed as
    # working_hours_between(created_at, end) - held_hours.
    hold_started_at: Mapped[datetime | None] = mapped_column(DateTime)
    held_hours: Mapped[float] = mapped_column(Float, default=0.0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # Soft delete — admin can delete a work order from History Log and
    # restore it later. Nothing is ever hard-deleted from the DB this way,
    # and we keep a light audit trail of who deleted/restored it and when.
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    deleted_by: Mapped[str | None] = mapped_column(String(150))
    restored_at: Mapped[datetime | None] = mapped_column(DateTime)
    restored_by: Mapped[str | None] = mapped_column(String(150))

    asset: Mapped["Asset"] = relationship(back_populates="work_orders")
    assignments: Mapped[list["TaskAssignment"]] = relationship(back_populates="work_order")
    parts_used: Mapped[list["PartsUsed"]] = relationship(back_populates="work_order")
    photos: Mapped[list["WorkOrderPhoto"]] = relationship(back_populates="work_order", cascade="all, delete-orphan")


class WorkOrderPhoto(Base):
    """
    Photos attached to a work order — stored as compressed JPEG files on
    disk (see photo_storage.py), not as base64 text in `description`.
    Only thumb_path/full_path (short strings) are ever loaded into memory
    by a normal query; the actual image bytes are streamed from disk
    straight to the browser by the /photos static file route.
    """
    __tablename__ = "work_order_photos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_orders.id"))
    kind: Mapped[str] = mapped_column(String(20))  # 'operator' | 'completion'
    filename: Mapped[str | None] = mapped_column(String(255))
    thumb_path: Mapped[str] = mapped_column(String(255))
    full_path: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    work_order: Mapped["WorkOrder"] = relationship(back_populates="photos")


class PMSchedule(Base):
    __tablename__ = "pm_schedules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assets.id"))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    frequency: Mapped[PMFrequency] = mapped_column(SAEnum(PMFrequency))
    interval_days: Mapped[int] = mapped_column(Integer)
    estimated_hours: Mapped[float | None] = mapped_column(Float)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    last_triggered: Mapped[datetime | None] = mapped_column(DateTime)
    next_due: Mapped[datetime] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    asset: Mapped["Asset"] = relationship(back_populates="pm_schedules")


class TaskAssignment(Base):
    __tablename__ = "task_assignments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_orders.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[TaskStatus] = mapped_column(SAEnum(TaskStatus), default=TaskStatus.pending)
    notes: Mapped[str | None] = mapped_column(Text)
    assigned_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    work_order: Mapped["WorkOrder"] = relationship(back_populates="assignments")
    user: Mapped["User"] = relationship(back_populates="assignments")


class SparePart(Base):
    __tablename__ = "spare_parts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    part_code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(100))
    unit: Mapped[str] = mapped_column(String(30), default="pcs")
    quantity_on_hand: Mapped[int] = mapped_column(Integer, default=0)
    reorder_level: Mapped[int] = mapped_column(Integer, default=5)
    unit_cost: Mapped[float | None] = mapped_column(Float)
    supplier: Mapped[str | None] = mapped_column(String(150))
    location: Mapped[str | None] = mapped_column(String(100))
    barcode: Mapped[str | None] = mapped_column(String(100), index=True)
    used_on_asset: Mapped[str | None] = mapped_column(String(200))
    # Photos are compressed JPEG files on disk (see photo_storage.py), not
    # base64 text on the row — same reasoning as WorkOrderPhoto below: keeps
    # the Inventory list endpoint (which returns every part in one response)
    # small regardless of how many parts have a photo.
    photo_thumb_path: Mapped[str | None] = mapped_column(String(255))
    photo_full_path: Mapped[str | None] = mapped_column(String(255))
    last_stock_take_at: Mapped[datetime | None] = mapped_column(DateTime)  # set only when verified via the Stock Take tab
    last_stock_take_by: Mapped[str | None] = mapped_column(String(150))    # name of who performed that check
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    parts_used: Mapped[list["PartsUsed"]] = relationship(back_populates="spare_part")


class PartsUsed(Base):
    __tablename__ = "parts_used"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_orders.id"))
    spare_part_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("spare_parts.id"))
    quantity_used: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    work_order: Mapped["WorkOrder"] = relationship(back_populates="parts_used")
    spare_part: Mapped["SparePart"] = relationship(back_populates="parts_used")