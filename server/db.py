# This file was modified in 2026 by YiQiao contributors. See NOTICE.

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


def _build_database_url() -> str:
    configured_url = os.environ.get("DATABASE_URL", "").strip()
    if configured_url:
        return configured_url

    host = os.environ.get("POSTGRES_HOST", "postgres")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ.get("POSTGRES_USER", "postgres")
    password = os.environ.get("POSTGRES_PASSWORD", "").strip()
    if not password:
        raise RuntimeError(
            "POSTGRES_PASSWORD is required when DATABASE_URL is not set. "
            "Generate it with scripts/init.sh or scripts/init.ps1."
        )
    db = os.environ.get("APP_DB_NAME", "yiqiao_app")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


_database_url = _build_database_url()
_engine_options = {"pool_pre_ping": True}
if _database_url.startswith("sqlite"):
    _engine_options["connect_args"] = {"check_same_thread": False}

engine = create_engine(_database_url, **_engine_options)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency that yields a SQLAlchemy session."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
