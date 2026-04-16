"""Lightweight migration entrypoint for auth and user-scoping columns."""

from __future__ import annotations

from backend.app.database import init_db


def main() -> None:
    """Apply the lightweight SQLite migrations used by the project."""
    init_db()
    print("Migration finished: auth tables and user-scoped columns are ready.")


if __name__ == "__main__":
    main()
