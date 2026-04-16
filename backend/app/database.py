"""Database configuration for the lesson plan backend."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from sqlalchemy import inspect
from sqlalchemy import event
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./lesson_plans.db")


class Base(DeclarativeBase):
    """Shared declarative base for ORM models."""


engine = create_engine(DATABASE_URL, future=True, echo=False, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
    """Ensure SQLite enforces foreign-key constraints."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


session_maker = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session)


async def get_db() -> AsyncGenerator[Session, None]:
    """Yield a database session for request handlers."""
    with session_maker() as session:
        yield session


def init_db() -> None:
    """Create database tables if they do not already exist."""
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _apply_sqlite_migrations()


def _apply_sqlite_migrations() -> None:
    """Apply lightweight SQLite migrations for newly added columns."""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    user_scoped_tables = {
        "plans": "default",
        "conversations": "default",
        "operations": "default",
        "savepoints": "default",
        "knowledge_files": "default",
        "preference_presets": "default",
    }
    with engine.begin() as connection:
        for table_name, default_user_id in user_scoped_tables.items():
            if table_name not in table_names:
                continue

            columns = {column["name"] for column in inspector.get_columns(table_name)}
            if "user_id" not in columns:
                connection.execute(
                    text(
                        f"ALTER TABLE {table_name} "
                        f"ADD COLUMN user_id VARCHAR(36) NOT NULL DEFAULT '{default_user_id}'"
                    )
                )
                connection.execute(
                    text(f"CREATE INDEX IF NOT EXISTS ix_{table_name}_user_id ON {table_name} (user_id)")
                )

    inspector = inspect(engine)

    if "plans" in inspector.get_table_names():
        plan_columns = {column["name"] for column in inspector.get_columns("plans")}
        if "doc_type" not in plan_columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE plans ADD COLUMN doc_type VARCHAR(20) NOT NULL DEFAULT 'lesson'"))

    if "conversations" not in inspector.get_table_names():
        inspector = inspect(engine)
    else:
        columns = {column["name"] for column in inspector.get_columns("conversations")}
        if "metadata" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE conversations ADD COLUMN metadata JSON NOT NULL DEFAULT '{}'"))

    inspector = inspect(engine)
    if "knowledge_files" not in inspector.get_table_names():
        return

    knowledge_columns = {column["name"] for column in inspector.get_columns("knowledge_files")}
    if "full_text" in knowledge_columns:
        return

    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE knowledge_files ADD COLUMN full_text TEXT"))
