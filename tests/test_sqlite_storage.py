from app.storage import sqlite as sqlite_storage


def test_init_db_does_not_attempt_duplicate_last_domain_migration(monkeypatch):
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=()):
            normalized = " ".join(sql.split())
            if normalized.startswith("PRAGMA table_info(role_sessions)"):
                return [
                    (0, "session_id", "TEXT", 0, None, 1),
                    (1, "selected_role", "TEXT", 1, None, 0),
                    (2, "conversation_name", "TEXT", 0, None, 0),
                    (3, "last_domain", "TEXT", 0, None, 0),
                    (4, "last_active", "REAL", 1, None, 0),
                ]
            if normalized == "ALTER TABLE role_sessions ADD COLUMN last_domain TEXT;":
                raise AssertionError("duplicate last_domain migration attempted")
            return []

        def commit(self):
            pass

    monkeypatch.setattr(sqlite_storage.sqlite3, "connect", lambda _: FakeConnection())

    sqlite_storage.init_db()
