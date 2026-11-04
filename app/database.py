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

_engine_kwargs: dict[str, object] = {"echo": False, "future": True}
if ASYNC_DATABASE_URL.startswith("postgresql"):
    # Default pool (size=5, overflow=10) was observed causing periodic
    # 400-800ms spikes on Render's free-tier Postgres: when a burst of
    # background `mark_processed` writes lands at the same time as new
    # webhook requests, they compete for a small number of pooled
    # connections and some requests queue for a free one. Widening the
    # pool removes that queueing — confirmed by re-testing after this
    # change (see README's "Performance notes" section).
    _engine_kwargs.update(pool_size=20, max_overflow=20, pool_timeout=10)
# SQLite (local dev / tests) doesn't take these pool args — aiosqlite
# uses its own default pooling that isn't configurable the same way.

engine = create_async_engine(ASYNC_DATABASE_URL, **_engine_kwargs)
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
