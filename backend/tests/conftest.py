# backend/tests/conftest.py
#
# Responsibility: Shared pytest fixtures for all API tests.
#
# ─── Test strategy ───────────────────────────────────────────────────────────
# We use FastAPI's dependency_overrides mechanism to inject a mock AsyncSession
# instead of a real database connection.  This means:
#
#   1. Tests run without a running PostgreSQL instance.
#   2. Tests are fast (no network I/O, no DB setup/teardown per test).
#   3. Tests isolate HTTP layer concerns from DB layer concerns.
#   4. Service layer correctness is verified separately in service unit tests.
#
# ─── Dependency override pattern ─────────────────────────────────────────────
# FastAPI's dependency_overrides is a dict:
#   {original_dependency_fn: replacement_fn}
#
# When FastAPI resolves Depends(get_db) for a request, it checks
# dependency_overrides first.  If the original is overridden, the
# replacement is called instead.
#
# In tests:
#   app.dependency_overrides[get_db] = lambda: mock_session
#
# After the test:
#   app.dependency_overrides.clear()
#
# ─── httpx.AsyncClient + ASGITransport ───────────────────────────────────────
# In tests, we don't want a real HTTP server.  ASGITransport passes requests
# directly to the ASGI app in-process:
#
#   httpx.AsyncClient(
#       transport=ASGITransport(app=app),
#       base_url="http://test"
#   )
#
# This is equivalent to using TestClient but supports async test functions.
# The HTTP request goes through all FastAPI middleware, routing, and validation
# just like a real request — but without network overhead.
#
# ─── pytest-asyncio mode ─────────────────────────────────────────────────────
# pytest-asyncio must be configured to run async test functions.
# We use asyncio_mode="auto" (set in pytest.ini or pyproject.toml) so that
# every `async def test_*` is automatically treated as an asyncio test.
# No need for @pytest.mark.asyncio on every test function.

from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.database import get_db
from app.main import app


# ─────────────────────────────────────────────────────────────────────────────
# Mock AsyncSession factory
# ─────────────────────────────────────────────────────────────────────────────

def make_mock_db_session() -> AsyncMock:
    """
    Create a fully-mocked AsyncSession suitable for dependency injection.

    The mock auto-specs AsyncSession so that attribute access and method calls
    behave like the real object, raising AttributeError on unknown attributes.

    Key mocked behaviours:
        execute()   → AsyncMock (awaitable, returns a configurable result)
        commit()    → AsyncMock (awaitable no-op by default)
        rollback()  → AsyncMock (awaitable no-op by default)
        close()     → AsyncMock (awaitable no-op by default)
        refresh()   → AsyncMock (awaitable no-op by default)
        add()       → MagicMock (sync, no-op by default)
    """
    mock = AsyncMock(spec=AsyncSession)
    mock.execute = AsyncMock()
    mock.commit  = AsyncMock()
    mock.rollback = AsyncMock()
    mock.close   = AsyncMock()
    mock.refresh = AsyncMock()
    mock.add     = MagicMock()
    return mock


# ─────────────────────────────────────────────────────────────────────────────
# HTTP client fixture
# ─────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """
    Async HTTP client backed by the FastAPI ASGI app (no network).

    This fixture:
        1. Creates an httpx.AsyncClient using ASGITransport.
        2. Yields it to the test.
        3. Closes it after the test completes.

    Tests use this to fire requests at the app:
        response = await client.get("/api/sensors")
        assert response.status_code == 200

    The client goes through all FastAPI routing, middleware, and validation
    just like a real HTTP request, ensuring tests reflect production behaviour.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ─────────────────────────────────────────────────────────────────────────────
# Mock DB session fixture
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_db() -> AsyncMock:
    """
    Return a fresh mock AsyncSession for each test.

    Each test gets an independent mock — no shared state between tests.
    """
    return make_mock_db_session()


# ─────────────────────────────────────────────────────────────────────────────
# App with overridden DB dependency
# ─────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client_with_mock_db(mock_db: AsyncMock) -> AsyncGenerator[AsyncClient, None]:
    """
    HTTP client with the real database dependency replaced by a mock session.

    This fixture:
        1. Overrides get_db with an async generator that yields mock_db.
        2. Creates the AsyncClient.
        3. Clears the override after the test to avoid test pollution.

    Usage:
        async def test_something(client_with_mock_db, mock_db):
            mock_db.execute.return_value = ...
            response = await client_with_mock_db.get("/api/sensors")
            assert response.status_code == 200

    Why clear dependency_overrides after the test?
        pytest fixtures share the app module.  Leaving overrides in place
        would affect subsequent tests that expect the real get_db.
    """
    async def override_get_db() -> AsyncGenerator[AsyncMock, None]:
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
