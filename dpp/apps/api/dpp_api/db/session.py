"""Database session management.

Spec Lock: Uses unified engine builder (SSOT) with NullPool default.
"""

import os
from typing import Generator

from sqlalchemy.orm import Session

from dpp_api.db.engine import build_engine, build_sessionmaker

# ENV-01: Database URL from environment with fallback to docker-compose default
# Fallback needed for Docker build smoke checks (no runtime env at build time)
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://dpp_user:dpp_pass@localhost:5432/dpp"
)

# Create engine using SSOT builder (default: NullPool)
engine = build_engine(DATABASE_URL)

# Create session factory
SessionLocal = build_sessionmaker(engine)


def get_db() -> Generator[Session, None, None]:
    """
    Get database session.

    Yields:
        Session: SQLAlchemy session
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
