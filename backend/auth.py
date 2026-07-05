import hashlib
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db, settings
from models import User, UserRole
from schemas import TokenResponse, UserCreate, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# ── Password helpers ───────────────────────────────────────────────────────

def _prepare(password: str) -> bytes:
    """SHA256 digest — always 32 bytes, safe for bcrypt."""
    return hashlib.sha256(password.encode()).digest()

def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prepare(password), bcrypt.gensalt()).decode()

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(plain), hashed.encode())
    except Exception:
        return False


# ── JWT helpers ────────────────────────────────────────────────────────────

def create_access_token(user_id: UUID, role: UserRole) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "role": role.value, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


# ── Current user dependency ────────────────────────────────────────────────

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id: Optional[str] = payload.get("sub")
        if user_id is None:
            raise credentials_exc
    except JWTError:
        raise credentials_exc

    result = await db.execute(select(User).where(User.id == UUID(user_id)))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise credentials_exc
    return user


def require_roles(*roles: UserRole):
    async def _check(current_user: User = Depends(get_current_user)):
        if current_user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return current_user
    return _check


async def forbid_viewer(current_user: User = Depends(get_current_user)) -> User:
    """
    Viewer accounts are read-only observers of the Alert Board only.
    Use this in place of get_current_user on any route (or as a whole
    router's `dependencies=[...]`) that viewers must not be able to reach —
    i.e. everything except GET /work-orders and GET /users/technicians.
    """
    if current_user.role == UserRole.viewer:
        raise HTTPException(status_code=403, detail="Viewer accounts have read-only access to the Alert Board only")
    return current_user


# ── Routes ─────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.email == form.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Account is disabled")
    token = create_access_token(user.id, user.role)
    return TokenResponse(access_token=token)


@router.post("/register", response_model=UserOut, status_code=201)
async def register(body: UserCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        name=body.name,
        email=body.email,
        hashed_password=hash_password(body.password),
        role=body.role,
    )
    db.add(user)
    await db.flush()
    return user


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    return current_user