"""
CMMS Seed Script
================
Populates the database with realistic sample data for exploration.

Run AFTER docker compose up --build:
    docker compose exec backend python seed.py

Or locally (with venv activated):
    cd backend && python seed.py
"""

import asyncio
from datetime import datetime, timedelta
import random
from db import AsyncSessionLocal, engine, Base
from models import (
    User, Asset, WorkOrder, PMSchedule,
    TaskAssignment, SparePart, PartsUsed,
    AssetStatus, WorkOrderType, WorkOrderStatus,
    Priority, TaskStatus, UserRole, PMFrequency
)
from auth import hash_password


# ── Sample data definitions ────────────────────────────────────────────────

USERS = [
    {"name": "Ahmad Razif",    "email": "admin@cmms.com",      "password": "password123", "role": UserRole.admin},
    {"name": "Siti Norizan",   "email": "manager@cmms.com",    "password": "password123", "role": UserRole.manager},
    {"name": "Hafiz Rosli",    "email": "hafiz@cmms.com",      "password": "password123", "role": UserRole.technician},
    {"name": "Farid Ismail",   "email": "farid@cmms.com",      "password": "password123", "role": UserRole.technician},
    {"name": "Nurul Ain",      "email": "nurul@cmms.com",      "password": "password123", "role": UserRole.technician},
    {"name": "Zulkifli Omar",  "email": "zul@cmms.com",        "password": "password123", "role": UserRole.viewer},
]

ASSETS = [
    {
        "asset_code": "PMP-001", "name": "Centrifugal Pump A",
        "category": "Pump", "location": "Building A — Ground Floor",
        "manufacturer": "Grundfos", "model": "CM10-4",
        "serial_number": "GF-2021-00341", "status": AssetStatus.operational,
        "notes": "Primary water supply pump. Critical asset.",
    },
    {
        "asset_code": "PMP-002", "name": "Centrifugal Pump B",
        "category": "Pump", "location": "Building A — Ground Floor",
        "manufacturer": "Grundfos", "model": "CM10-4",
        "serial_number": "GF-2021-00342", "status": AssetStatus.under_maintenance,
        "notes": "Backup pump. Currently under repair.",
    },
    {
        "asset_code": "MTR-001", "name": "Conveyor Drive Motor #1",
        "category": "Motor", "location": "Production Line 1",
        "manufacturer": "ABB", "model": "M2BAX 90",
        "serial_number": "ABB-MTR-9921", "status": AssetStatus.operational,
    },
    {
        "asset_code": "MTR-002", "name": "Conveyor Drive Motor #2",
        "category": "Motor", "location": "Production Line 2",
        "manufacturer": "ABB", "model": "M2BAX 90",
        "serial_number": "ABB-MTR-9922", "status": AssetStatus.operational,
    },
    {
        "asset_code": "CMP-001", "name": "Air Compressor Unit 1",
        "category": "Compressor", "location": "Utility Room B",
        "manufacturer": "Atlas Copco", "model": "GA15",
        "serial_number": "AC-GA15-4423", "status": AssetStatus.operational,
        "notes": "Supplies compressed air to production lines.",
    },
    {
        "asset_code": "CMP-002", "name": "Air Compressor Unit 2",
        "category": "Compressor", "location": "Utility Room B",
        "manufacturer": "Atlas Copco", "model": "GA15",
        "serial_number": "AC-GA15-4424", "status": AssetStatus.out_of_service,
        "notes": "Awaiting spare part delivery.",
    },
    {
        "asset_code": "HVAC-001", "name": "AHU — Production Hall",
        "category": "HVAC", "location": "Rooftop Level 2",
        "manufacturer": "Carrier", "model": "AHU-30XA",
        "serial_number": "CAR-30XA-881", "status": AssetStatus.operational,
    },
    {
        "asset_code": "HVAC-002", "name": "Chiller Unit 1",
        "category": "HVAC", "location": "Rooftop Level 2",
        "manufacturer": "Trane", "model": "CGAM030",
        "serial_number": "TRN-CGAM-220", "status": AssetStatus.operational,
    },
    {
        "asset_code": "GEN-001", "name": "Standby Generator",
        "category": "Generator", "location": "Genset Room — Block C",
        "manufacturer": "Caterpillar", "model": "C15",
        "serial_number": "CAT-C15-0042", "status": AssetStatus.operational,
        "notes": "500 kVA. Monthly load test required.",
    },
    {
        "asset_code": "CVY-001", "name": "Belt Conveyor — Line 1",
        "category": "Conveyor", "location": "Production Line 1",
        "manufacturer": "Omni", "model": "BC-500",
        "serial_number": "OMN-BC-1101", "status": AssetStatus.operational,
    },
]

SPARE_PARTS = [
    {"part_code": "BRG-001", "name": "Deep Groove Ball Bearing 6205",  "category": "Bearing",    "unit": "pcs", "quantity_on_hand": 12, "reorder_level": 5,  "unit_cost": 18.50,  "supplier": "SKF Malaysia",      "location": "Rack A-1"},
    {"part_code": "BRG-002", "name": "Cylindrical Roller Bearing NU208","category": "Bearing",   "unit": "pcs", "quantity_on_hand": 3,  "reorder_level": 4,  "unit_cost": 65.00,  "supplier": "SKF Malaysia",      "location": "Rack A-1"},
    {"part_code": "SEAL-001","name": "Mechanical Seal 25mm",            "category": "Seal",       "unit": "pcs", "quantity_on_hand": 8,  "reorder_level": 3,  "unit_cost": 42.00,  "supplier": "Flowserve",         "location": "Rack A-2"},
    {"part_code": "SEAL-002","name": "O-Ring Kit — Pump",               "category": "Seal",       "unit": "set", "quantity_on_hand": 15, "reorder_level": 5,  "unit_cost": 12.00,  "supplier": "Parker Hannifin",   "location": "Rack A-2"},
    {"part_code": "BLT-001", "name": "V-Belt A-42",                     "category": "Belt",       "unit": "pcs", "quantity_on_hand": 6,  "reorder_level": 4,  "unit_cost": 22.00,  "supplier": "Gates Malaysia",    "location": "Rack B-1"},
    {"part_code": "BLT-002", "name": "Timing Belt HTD 8M-1200",         "category": "Belt",       "unit": "pcs", "quantity_on_hand": 2,  "reorder_level": 3,  "unit_cost": 85.00,  "supplier": "Gates Malaysia",    "location": "Rack B-1"},
    {"part_code": "FLT-001", "name": "Air Filter — Compressor",         "category": "Filter",     "unit": "pcs", "quantity_on_hand": 10, "reorder_level": 4,  "unit_cost": 35.00,  "supplier": "Atlas Copco Parts", "location": "Rack B-2"},
    {"part_code": "FLT-002", "name": "Oil Filter — Compressor",         "category": "Filter",     "unit": "pcs", "quantity_on_hand": 8,  "reorder_level": 3,  "unit_cost": 28.00,  "supplier": "Atlas Copco Parts", "location": "Rack B-2"},
    {"part_code": "LUB-001", "name": "Synthetic Compressor Oil 20L",    "category": "Lubricant",  "unit": "can", "quantity_on_hand": 4,  "reorder_level": 2,  "unit_cost": 180.00, "supplier": "Castrol Malaysia",  "location": "Rack C-1"},
    {"part_code": "LUB-002", "name": "Grease Cartridge — EP2",          "category": "Lubricant",  "unit": "pcs", "quantity_on_hand": 20, "reorder_level": 8,  "unit_cost": 15.00,  "supplier": "Shell Malaysia",    "location": "Rack C-1"},
    {"part_code": "ELC-001", "name": "Contactor 3P 32A",                "category": "Electrical", "unit": "pcs", "quantity_on_hand": 5,  "reorder_level": 2,  "unit_cost": 95.00,  "supplier": "Schneider Electric","location": "Rack D-1"},
    {"part_code": "ELC-002", "name": "Thermal Overload Relay",          "category": "Electrical", "unit": "pcs", "quantity_on_hand": 3,  "reorder_level": 2,  "unit_cost": 78.00,  "supplier": "Schneider Electric","location": "Rack D-1"},
    {"part_code": "ELC-003", "name": "Control Fuse 6A",                 "category": "Electrical", "unit": "pcs", "quantity_on_hand": 1,  "reorder_level": 10, "unit_cost": 3.50,   "supplier": "RS Components",     "location": "Rack D-2"},
    {"part_code": "HSE-001", "name": "Hydraulic Hose 1/2\" x 1m",       "category": "Hose",       "unit": "pcs", "quantity_on_hand": 7,  "reorder_level": 3,  "unit_cost": 55.00,  "supplier": "Parker Hannifin",   "location": "Rack E-1"},
    {"part_code": "GSK-001", "name": "Pump Casing Gasket Set",          "category": "Gasket",     "unit": "set", "quantity_on_hand": 4,  "reorder_level": 2,  "unit_cost": 38.00,  "supplier": "Grundfos Parts",    "location": "Rack A-3"},
]


def now() -> datetime:
    return datetime.utcnow()

def days_ago(n: int) -> datetime:
    return now() - timedelta(days=n)

def days_ahead(n: int) -> datetime:
    return now() + timedelta(days=n)


async def seed():
    print("🌱  CMMS Seed Script starting...")

    # Ensure tables exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:

        # ── 1. Users ───────────────────────────────────────────────────────
        print("👤  Creating users...")
        user_objs = []
        for u in USERS:
            user = User(
                name=u["name"],
                email=u["email"],
                hashed_password=hash_password(u["password"]),
                role=u["role"],
            )
            db.add(user)
            user_objs.append(user)
        await db.flush()
        technicians = [u for u in user_objs if u.role == UserRole.technician]
        print(f"   ✓ {len(user_objs)} users created")

        # ── 2. Assets ──────────────────────────────────────────────────────
        print("🏭  Creating assets...")
        asset_objs = []
        for a in ASSETS:
            asset = Asset(
                **a,
                purchase_date=days_ago(random.randint(365, 1825)),
                last_maintained=days_ago(random.randint(7, 90)),
            )
            db.add(asset)
            asset_objs.append(asset)
        await db.flush()
        print(f"   ✓ {len(asset_objs)} assets created")

        # ── 3. Spare Parts ─────────────────────────────────────────────────
        print("📦  Creating spare parts...")
        part_objs = []
        for p in SPARE_PARTS:
            part = SparePart(**p)
            db.add(part)
            part_objs.append(part)
        await db.flush()
        print(f"   ✓ {len(part_objs)} spare parts created")

        # ── 4. Work Orders ─────────────────────────────────────────────────
        print("📋  Creating work orders...")
        wo_data = [
            # Completed WOs
            {
                "wo_number": "WO-202505-0001",
                "asset": asset_objs[0],  # Pump A
                "type": WorkOrderType.corrective,
                "priority": Priority.high,
                "status": WorkOrderStatus.completed,
                "title": "Replace mechanical seal — Pump A",
                "description": "Pump A showing seal leakage. Replace mechanical seal and inspect impeller.",
                "due_date": days_ago(15),
                "estimated_hours": 4.0,
                "actual_hours": 3.5,
                "completed_at": days_ago(14),
                "created_at": days_ago(16),
            },
            {
                "wo_number": "WO-202505-0002",
                "asset": asset_objs[4],  # Compressor 1
                "type": WorkOrderType.preventive,
                "priority": Priority.medium,
                "status": WorkOrderStatus.completed,
                "title": "Quarterly oil and filter change — Compressor 1",
                "description": "Scheduled quarterly maintenance. Change oil, air filter, oil filter.",
                "due_date": days_ago(10),
                "estimated_hours": 2.0,
                "actual_hours": 2.0,
                "completed_at": days_ago(9),
                "created_at": days_ago(11),
            },
            # In-progress WOs
            {
                "wo_number": "WO-202505-0003",
                "asset": asset_objs[1],  # Pump B
                "type": WorkOrderType.corrective,
                "priority": Priority.critical,
                "status": WorkOrderStatus.in_progress,
                "title": "Pump B — bearing failure investigation",
                "description": "Excessive vibration detected. Disassemble and inspect bearings.",
                "due_date": days_ahead(1),
                "estimated_hours": 6.0,
                "created_at": days_ago(2),
            },
            {
                "wo_number": "WO-202505-0004",
                "asset": asset_objs[6],  # HVAC AHU
                "type": WorkOrderType.inspection,
                "priority": Priority.medium,
                "status": WorkOrderStatus.in_progress,
                "title": "Monthly AHU inspection — Production Hall",
                "description": "Check filters, belts, drain pans, coils, and airflow.",
                "due_date": days_ahead(3),
                "estimated_hours": 3.0,
                "created_at": days_ago(1),
            },
            # Open WOs
            {
                "wo_number": "WO-202505-0005",
                "asset": asset_objs[5],  # Compressor 2
                "type": WorkOrderType.corrective,
                "priority": Priority.high,
                "status": WorkOrderStatus.open,
                "title": "Compressor 2 — unloader valve replacement",
                "description": "Compressor 2 out of service. Replace unloader valve assembly.",
                "due_date": days_ahead(2),
                "estimated_hours": 5.0,
                "created_at": days_ago(3),
            },
            {
                "wo_number": "WO-202505-0006",
                "asset": asset_objs[8],  # Generator
                "type": WorkOrderType.preventive,
                "priority": Priority.medium,
                "status": WorkOrderStatus.open,
                "title": "Monthly generator load test",
                "description": "Run generator under 50% load for 30 minutes. Check coolant, oil, battery.",
                "due_date": days_ahead(5),
                "estimated_hours": 1.5,
                "created_at": days_ago(1),
            },
            {
                "wo_number": "WO-202505-0007",
                "asset": asset_objs[2],  # Motor 1
                "type": WorkOrderType.inspection,
                "priority": Priority.low,
                "status": WorkOrderStatus.open,
                "title": "Motor #1 — lubrication and vibration check",
                "description": "Apply grease to bearings and take vibration readings.",
                "due_date": days_ahead(7),
                "estimated_hours": 1.0,
                "created_at": now(),
            },
            # Overdue WO
            {
                "wo_number": "WO-202505-0008",
                "asset": asset_objs[9],  # Conveyor
                "type": WorkOrderType.preventive,
                "priority": Priority.high,
                "status": WorkOrderStatus.open,
                "title": "Belt conveyor — belt tension and alignment check",
                "description": "Check belt tension, alignment, and lubricate idler rollers.",
                "due_date": days_ago(2),  # OVERDUE
                "estimated_hours": 2.0,
                "created_at": days_ago(5),
            },
            {
                "wo_number": "WO-202505-0009",
                "asset": asset_objs[7],  # Chiller
                "type": WorkOrderType.preventive,
                "priority": Priority.medium,
                "status": WorkOrderStatus.on_hold,
                "title": "Chiller condenser coil cleaning",
                "description": "Clean condenser coils with chemical solution. Awaiting chemical supply.",
                "due_date": days_ahead(10),
                "estimated_hours": 4.0,
                "created_at": days_ago(3),
            },
        ]

        wo_objs = []
        for w in wo_data:
            asset = w.pop("asset")
            wo = WorkOrder(
                **w,
                asset_id=asset.id,
                created_by=user_objs[0].id,
            )
            db.add(wo)
            wo_objs.append(wo)
        await db.flush()
        print(f"   ✓ {len(wo_objs)} work orders created")

        # ── 5. Task Assignments ────────────────────────────────────────────
        print("👷  Creating task assignments...")
        assignments = [
            # Assign in-progress WOs to technicians
            TaskAssignment(
                work_order_id=wo_objs[2].id,  # Pump B
                user_id=technicians[0].id,
                status=TaskStatus.in_progress,
                notes="Ordered replacement bearings. Waiting for delivery.",
                assigned_at=days_ago(2),
            ),
            TaskAssignment(
                work_order_id=wo_objs[3].id,  # AHU
                user_id=technicians[1].id,
                status=TaskStatus.accepted,
                assigned_at=days_ago(1),
            ),
            # Assign open WOs
            TaskAssignment(
                work_order_id=wo_objs[4].id,  # Compressor 2
                user_id=technicians[0].id,
                status=TaskStatus.pending,
                assigned_at=days_ago(1),
            ),
            TaskAssignment(
                work_order_id=wo_objs[5].id,  # Generator
                user_id=technicians[1].id,
                status=TaskStatus.pending,
                assigned_at=now(),
            ),
        ]
        for a in assignments:
            db.add(a)
        await db.flush()
        print(f"   ✓ {len(assignments)} task assignments created")

        # ── 6. Parts Used (on completed WOs) ──────────────────────────────
        print("🔩  Logging parts used...")
        parts_used_data = [
            # Pump A seal replacement
            PartsUsed(work_order_id=wo_objs[0].id, spare_part_id=part_objs[2].id, quantity_used=1),  # Mech seal
            PartsUsed(work_order_id=wo_objs[0].id, spare_part_id=part_objs[3].id, quantity_used=1),  # O-ring kit
            # Compressor service
            PartsUsed(work_order_id=wo_objs[1].id, spare_part_id=part_objs[6].id, quantity_used=1),  # Air filter
            PartsUsed(work_order_id=wo_objs[1].id, spare_part_id=part_objs[7].id, quantity_used=1),  # Oil filter
            PartsUsed(work_order_id=wo_objs[1].id, spare_part_id=part_objs[8].id, quantity_used=1),  # Oil
        ]
        for pu in parts_used_data:
            db.add(pu)
        await db.flush()
        print(f"   ✓ {len(parts_used_data)} parts-used records created")

        # ── 7. PM Schedules ────────────────────────────────────────────────
        print("📅  Creating PM schedules...")
        pm_data = [
            {
                "asset": asset_objs[0], "title": "Pump A — Monthly Inspection",
                "description": "Inspect seals, check vibration, lubricate bearings, verify flow rate.",
                "frequency": PMFrequency.monthly, "interval_days": 30,
                "estimated_hours": 2.0, "next_due": days_ahead(5), "is_active": True,
            },
            {
                "asset": asset_objs[4], "title": "Compressor 1 — Quarterly Service",
                "description": "Change oil, air filter, oil filter. Check belt tension and safety valves.",
                "frequency": PMFrequency.quarterly, "interval_days": 90,
                "estimated_hours": 3.0, "next_due": days_ahead(15), "is_active": True,
            },
            {
                "asset": asset_objs[6], "title": "AHU — Monthly Filter & Belt Check",
                "description": "Replace air filters, check belt tension, clean drain pan.",
                "frequency": PMFrequency.monthly, "interval_days": 30,
                "estimated_hours": 2.5, "next_due": days_ahead(3), "is_active": True,
            },
            {
                "asset": asset_objs[8], "title": "Generator — Monthly Load Test",
                "description": "Run on 50% load for 30 min. Check coolant level, oil, battery voltage.",
                "frequency": PMFrequency.monthly, "interval_days": 30,
                "estimated_hours": 1.5, "next_due": days_ahead(5), "is_active": True,
            },
            {
                "asset": asset_objs[7], "title": "Chiller — Annual Refrigerant Check",
                "description": "Check refrigerant levels, inspect heat exchangers, verify controls.",
                "frequency": PMFrequency.annual, "interval_days": 365,
                "estimated_hours": 6.0, "next_due": days_ahead(60), "is_active": True,
            },
            {
                "asset": asset_objs[2], "title": "Motor #1 — Biannual Overhaul",
                "description": "Disassemble, replace bearings, clean windings, test insulation resistance.",
                "frequency": PMFrequency.biannual, "interval_days": 182,
                "estimated_hours": 8.0, "next_due": days_ahead(45), "is_active": True,
            },
            {
                "asset": asset_objs[9], "title": "Conveyor Belt — Weekly Inspection",
                "description": "Check belt alignment, tension, and idler rollers. Lubricate as needed.",
                "frequency": PMFrequency.weekly, "interval_days": 7,
                "estimated_hours": 1.0, "next_due": days_ahead(2), "is_active": True,
            },
            {
                "asset": asset_objs[0], "title": "Pump A — Annual Overhaul",
                "description": "Full disassembly, replace all wear parts, performance test.",
                "frequency": PMFrequency.annual, "interval_days": 365,
                "estimated_hours": 12.0, "next_due": days_ahead(90), "is_active": True,
            },
        ]

        pm_objs = []
        for p in pm_data:
            asset = p.pop("asset")
            pm = PMSchedule(
                **p,
                asset_id=asset.id,
                assigned_to=random.choice(technicians).id,
                last_triggered=days_ago(random.randint(30, 180)),
            )
            db.add(pm)
            pm_objs.append(pm)
        await db.flush()
        print(f"   ✓ {len(pm_objs)} PM schedules created")

        # Commit everything
        await db.commit()

    print("")
    print("✅  Seed complete! Summary:")
    print(f"   👤 Users         : {len(USERS)}")
    print(f"   🏭 Assets        : {len(ASSETS)}")
    print(f"   📋 Work Orders   : {len(wo_data)}")
    print(f"   📦 Spare Parts   : {len(SPARE_PARTS)}")
    print(f"   📅 PM Schedules  : {len(pm_data)}")
    print("")
    print("🔐  Login credentials:")
    for u in USERS:
        print(f"   {u['role'].value:<12}  {u['email']}  /  {u['password']}")
    print("")
    print("🌐  Open the app at: http://localhost:5173")
    print("📖  API docs at    : http://localhost:8000/docs")


if __name__ == "__main__":
    asyncio.run(seed())