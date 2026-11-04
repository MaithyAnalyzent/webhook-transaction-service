from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import DATABASE_URL


def _to_async_dsn(url: str) -> str:
    """Normalize a plain postgres:// / postgresql:// URL (what Render hands
    you) into the asyncpg driver DSN SQLAlchemy's async engine expects."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://") and "+asyncpg" not in url:
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


ASYNC_DATABASE_URL = _to_async_dsn(DATABASE_URL)

# SQLite needs check_same_thread disabled isn't relevant for the async
# driver, but it does need a shared cache disabled for pytest's fresh
# in-memory DBs per test run; no special args needed for aiosqlite here.
engine = create_async_engine(ASYNC_DATABASE_URL, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session():
    async with AsyncSessionLocal() as session:
        yield session


async def init_models() -> None:
    # No Alembic for a service this size — create_all is fine for an
    # assessment. A real production rollout would use migrations instead.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
