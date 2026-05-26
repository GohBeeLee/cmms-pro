from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional
import hashlib
import bcrypt

from db import get_db
from models import User, UserRole
from schemas import UserOut, UserCreate, UserUpdate
from auth import get_current_user, hash_password

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/", response_model=list[UserOut])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(
        select(User).where(User.is_active == True).order_by(User.name)
    )
    return result.scalars().all()


@router.get("/technicians", response_model=list[UserOut])
async def list_technicians(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Returns only technicians — for work order assignment dropdown."""
    result = await db.execute(
        select(User)
        .where(User.is_active == True,
               User.role.in_([UserRole.technician, UserRole.manager, UserRole.admin]))
        .order_by(User.name)
    )
    return result.scalars().all()


@router.post("/", response_model=UserOut, status_code=201)
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Email already registered")
    user = User(
        name=body.name,
        email=body.email,
        hashed_password=hash_password(body.password),
        role=body.role,
    )
    db.add(user)
    await db.flush()
    return user