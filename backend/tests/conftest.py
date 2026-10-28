import os
import pytest
import pytest_asyncio

# Set env vars before any app imports.
# IMPORTANT: DATABASE_URL must match the conftest test engine (SQLITE_URL below) so
# that routes which open their OWN AsyncSessionLocal (inference/extras/discovery) see
# the SAME database + tables as the injected test session. Otherwise those routes hit
# an unmigrated DB and 500 instead of exercising their real logic. (P1.3 fix.)
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_automl.db"
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("MLFLOW_TRACKING_URI", "http://localhost:5000")
os.environ.setdefault("SANDBOX_URL", "http://localhost:8001")
os.environ.setdefault("DATA_DIR", "/tmp/automl_test")
os.environ.setdefault("LLM_PROVIDER", "groq")
os.environ.setdefault("GROQ_API_KEY", "test_key_for_unit_tests")

# Isolate tests from the developer's .env: force PUBLIC (no-auth) mode so endpoint
# tests are deterministic regardless of whether Supabase/tenant keys are configured
# locally. Explicit assignment (not setdefault) overrides any value from .env.
os.environ["TENANT_API_KEYS"] = ""
os.environ["SUPABASE_JWT_SECRET"] = ""
os.environ["SUPABASE_URL"] = ""
os.environ["API_KEY"] = ""


from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.database import Base, get_db

# Use SQLite in-memory for local tests — no Postgres needed
SQLITE_URL = "sqlite+aiosqlite:///./test_automl.db"

test_engine = create_async_engine(SQLITE_URL, echo=False, connect_args={"check_same_thread": False})
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db():
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_db():
    """Create all tables in SQLite test DB once per test session."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await test_engine.dispose()
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    import os as _os
    try:
        if _os.path.exists("./test_automl.db"):
            _os.remove("./test_automl.db")
    except OSError:
        pass


@pytest_asyncio.fixture
async def db_session():
    async with TestSessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client():
    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()
