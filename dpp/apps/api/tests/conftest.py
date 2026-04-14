"""Pytest configuration and fixtures."""

import sys
from pathlib import Path
# P0-3: Inject sys.path for reliable pytest imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # => .../apps/api

import hashlib
import os
import uuid
from unittest.mock import MagicMock, patch

import pytest
import redis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from dpp_api.budget.redis_scripts import BudgetScripts
from dpp_api.db.engine import build_engine
from dpp_api.db.models import APIKey, Base, Plan, Tenant
from dpp_api.db.redis_client import RedisClient
from dpp_api.db.repo_api_keys import APIKeyRepository
from dpp_api.db.repo_plans import TenantPlanRepository
from dpp_api.db.repo_tenants import TenantRepository
from dpp_api.db.session import get_db
from dpp_api.main import app

# Use PostgreSQL for monetization tests (BIGINT autoincrement requires PostgreSQL)
# Set to "sqlite:///:memory:" for fast unit tests
TEST_DATABASE_URL = os.getenv("DATABASE_URL") or "postgresql://dpp_user:dpp_pass@localhost:5432/dpp"

# Redis test settings
REDIS_TEST_HOST = "localhost"
REDIS_TEST_PORT = 6379
REDIS_TEST_DB = 15  # Use separate DB for tests


@pytest.fixture(scope="session", autouse=True)
def setup_test_env_vars():
    """
    Setup required environment variables for tests.

    Ops Hardening v2: S3_RESULT_BUCKET and SQS_QUEUE_URL are now required.
    This fixture ensures they're set for all tests.
    """
    # Save original env vars
    original_env = {}

    # Define test env vars
    test_env = {
        "S3_RESULT_BUCKET": os.getenv("S3_RESULT_BUCKET", "dpp-results-test"),
        "SQS_QUEUE_URL": os.getenv("SQS_QUEUE_URL", "http://localhost:4566/000000000000/dpp-runs"),
        "SQS_ENDPOINT_URL": os.getenv("SQS_ENDPOINT_URL", "http://localhost:4566"),
        "S3_ENDPOINT_URL": os.getenv("S3_ENDPOINT_URL", "http://localhost:4566"),
    }

    # Set test env vars (only if not already set)
    for key, value in test_env.items():
        if key not in os.environ:
            original_env[key] = None
            os.environ[key] = value
        else:
            original_env[key] = os.environ[key]

    yield

    # Restore original env vars
    for key, original_value in original_env.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value


@pytest.fixture(scope="function")
def db_session() -> Session:
    """
    Create a fresh database session for each test.

    Uses PostgreSQL if DATABASE_URL is set, otherwise in-memory SQLite.
    """
    # Create engine (using SSOT builder for PostgreSQL, StaticPool for SQLite)
    if "postgresql" in TEST_DATABASE_URL:
        # PostgreSQL: use SSOT engine builder (respects NullPool policy)
        engine = build_engine(TEST_DATABASE_URL)
    else:
        # SQLite: use in-memory with special settings (StaticPool required for :memory:)
        engine = create_engine(
            TEST_DATABASE_URL,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

    # Create all tables (idempotent for PostgreSQL)
    Base.metadata.create_all(engine)

    # Create session
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()

    try:
        yield session
        # Rollback any uncommitted changes
        session.rollback()
    finally:
        # Cleanup test data for PostgreSQL (to avoid cross-test contamination)
        if "postgresql" in TEST_DATABASE_URL:
            from dpp_api.db.models import TenantPlan, TenantUsageDaily

            # Delete test tenant's data (tenant will be recreated by fixtures)
            session.query(TenantUsageDaily).filter(
                TenantUsageDaily.tenant_id.like("tenant_%_test%")
            ).delete(synchronize_session=False)
            session.query(TenantPlan).filter(
                TenantPlan.tenant_id.like("tenant_%_test%")
            ).delete(synchronize_session=False)
            session.commit()

        session.close()
        # Only drop tables for SQLite (PostgreSQL tables persist)
        if "sqlite" in TEST_DATABASE_URL:
            Base.metadata.drop_all(engine)


@pytest.fixture(scope="function")
def redis_client() -> redis.Redis:
    """
    Create a fresh Redis client for each test.

    Uses Redis DB 15 for tests and flushes it before each test.
    RC-4: Force production RedisClient singleton to use TEST DB.
    """
    from dpp_api.db.redis_client import RedisClient

    # RC-4 Safety: Reset singleton before test
    RedisClient.reset()

    # Create test client with explicit test DB
    client = redis.Redis(
        host=REDIS_TEST_HOST,
        port=REDIS_TEST_PORT,
        db=REDIS_TEST_DB,
        decode_responses=True,
        socket_connect_timeout=10,
        socket_timeout=10,
    )

    # Flush test database before each test
    client.flushdb()

    # RC-4: Override singleton so production code uses test client
    RedisClient._instance = client

    try:
        yield client
    finally:
        # Clean up after test
        client.flushdb()
        # Reset singleton to clean state
        RedisClient.reset()


@pytest.fixture
def mock_sqs_client():
    """Mock SQS client to avoid LocalStack dependency in tests."""
    with patch("dpp_api.routers.runs.get_sqs_client") as mock_get_sqs:
        mock_client = MagicMock()
        mock_client.enqueue_run.return_value = "mock-message-id-123"
        mock_get_sqs.return_value = mock_client
        yield mock_client


@pytest.fixture
def test_client(db_session: Session, mock_sqs_client):
    """TestClient with db_session dependency override and mocked SQS."""

    def override_get_db():
        try:
            yield db_session
        finally:
            pass  # Don't close - conftest will handle it

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def basic_plan(db_session: Session) -> Plan:
    """Create a basic plan for E2E tests (cleanup existing if present)."""
    plan_id = "plan_e2e_basic"

    # Delete existing if present
    existing = db_session.query(Plan).filter_by(plan_id=plan_id).first()
    if existing:
        db_session.delete(existing)
        db_session.commit()

    plan = Plan(
        plan_id=plan_id,
        name="E2E Basic Plan",
        status="ACTIVE",
        default_profile_version="v0.4.2.2",
        features_json={
            "allowed_pack_types": ["decision", "url", "ocr", "video"],
            "max_concurrent_runs": 50,
        },
        limits_json={
            "rate_limit_post_per_min": 1000,  # High limit for E2E tests
            "rate_limit_poll_per_min": 10000,
            "pack_type_limits": {
                "decision": {"max_cost_usd_micros": 10_000_000},  # $10.00
                "url": {"max_cost_usd_micros": 10_000_000},
                "ocr": {"max_cost_usd_micros": 10_000_000},
                "video": {"max_cost_usd_micros": 10_000_000},
            },
        },
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


@pytest.fixture
def test_tenant_with_api_key(db_session: Session, basic_plan: Plan) -> tuple[str, str, str]:
    """Create a test tenant with API key and budget.

    Returns:
        Tuple of (tenant_id, api_key, key_hash)
    """
    tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"
    key_id = str(uuid.uuid4())
    secret = uuid.uuid4().hex
    api_key = f"sk_{key_id}_{secret}"

    # Hash the key
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    # Create tenant
    tenant_repo = TenantRepository(db_session)
    tenant_repo.create(
        Tenant(tenant_id=tenant_id, display_name=f"Test Tenant {tenant_id}", status="ACTIVE")
    )

    # Create API key
    api_key_repo = APIKeyRepository(db_session)
    api_key_repo.create(
        APIKey(
            key_id=key_id,
            tenant_id=tenant_id,
            key_hash=key_hash,
            label="Test Key",
            status="ACTIVE",
        )
    )

    # Assign plan to tenant
    tenant_plan_repo = TenantPlanRepository(db_session)
    tenant_plan_repo.assign_plan(
        tenant_id=tenant_id,
        plan_id=basic_plan.plan_id,
        changed_by="e2e_test",
        change_reason="E2E test setup",
    )

    # Set budget
    redis_client = RedisClient.get_client()
    budget_scripts = BudgetScripts(redis_client)
    budget_scripts.set_balance(tenant_id, 10_000_000)  # $10.00

    return (tenant_id, api_key, key_hash)
