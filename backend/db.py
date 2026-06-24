from pydantic_settings import BaseSettings
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from typing import AsyncGenerator
import os

# Anchor the SQLite file to THIS file's directory (backend/), not the
# process's current working directory. Without this, starting the app from
# a different folder (a different terminal session, a scheduler, a restart
# via a different working dir, etc.) silently creates/opens a brand new
# empty cmms.db in that other location — the old data isn't deleted, the
# app just stops seeing it, which looks exactly like "my data disappeared
# overnight". Using an absolute path makes the database location stable
# no matter how or from where the server process is launched.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "cmms.db")

class Settings(BaseSettings):
    # SQLite — stored in a local file, zero install required.
    # Absolute path anchored to backend/ so the working directory the
    # server is launched from never matters.
    DATABASE_URL: str = f"sqlite+aiosqlite:///{DEFAULT_DB_PATH}"
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    ENVIRONMENT: str = "development"

    class Config:
        env_file = ".env"


settings = Settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.ENVIRONMENT == "development",
    connect_args={"check_same_thread": False},  # required for SQLite
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()