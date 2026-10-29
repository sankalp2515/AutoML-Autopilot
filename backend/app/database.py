import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# SQLite (tests/CI) uses NullPool, which rejects pool sizing args — those are only
# valid for a real pooled backend like Postgres. Pass them conditionally so the
# same code boots against both.
_engine_kwargs: dict = {"echo": settings.DEBUG}
if os.environ.get("DB_NULL_POOL"):
    # Test runs against Postgres: each test has its own event loop, so no pooled connections.
    from sqlalchemy.pool import NullPool
    _engine_kwargs["poolclass"] = NullPool
elif not settings.DATABASE_URL.startswith("sqlite"):
    _engine_kwargs.update(pool_pre_ping=True, pool_size=10, max_overflow=20)

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        from app.repair.models import ensure_columns
        await conn.run_sync(ensure_columns)
