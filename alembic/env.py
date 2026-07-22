"""Alembic 마이그레이션 환경. URL/메타데이터는 app.config / app.db.models 에서 가져온다."""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import config as app_config
from app.db.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", app_config.DB_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=app_config.DB_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,  # SQLite ALTER 지원
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
