"""DB 세션 및 엔진 (SQLite)."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import config
from app.db.models import Base

# SQLite 파일 디렉토리 보장
Path(config.BASE_DIR / config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(config.DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """개발 편의용: 마이그레이션 없이 스키마 생성 (Alembic 미실행 시)."""
    Base.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI 의존성."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
