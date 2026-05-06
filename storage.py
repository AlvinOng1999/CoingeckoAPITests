import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "accounts.db")


def _conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                email       TEXT UNIQUE,
                cg_password TEXT,
                api_key     TEXT,
                calls_used  INTEGER DEFAULT 0,
                calls_left  INTEGER DEFAULT 10000,
                is_pinned   INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.commit()


def _migrate():
    with _conn() as con:
        try:
            con.execute("ALTER TABLE accounts ADD COLUMN is_pinned INTEGER DEFAULT 0")
            con.commit()
        except Exception:
            pass  # column already exists


def save_account(email, password, api_key):
    with _conn() as con:
        con.execute(
            "INSERT OR IGNORE INTO accounts (email, cg_password, api_key) VALUES (?,?,?)",
            (email, password, api_key),
        )
        con.commit()


def update_usage(api_key, calls_used, calls_left):
    with _conn() as con:
        con.execute(
            "UPDATE accounts SET calls_used=?, calls_left=? WHERE api_key=?",
            (calls_used, calls_left, api_key),
        )
        con.commit()


def increment_calls_used(api_key: str, n: int = 1):
    """Optimistically increment local call counter — gives real-time UI feedback."""
    with _conn() as con:
        con.execute(
            "UPDATE accounts SET calls_used = calls_used + ?, calls_left = MAX(0, calls_left - ?) WHERE api_key = ?",
            (n, n, api_key),
        )
        con.commit()


def delete_account(account_id):
    with _conn() as con:
        con.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        con.commit()


def pin_account(account_id):
    with _conn() as con:
        con.execute("UPDATE accounts SET is_pinned = 0")
        con.execute("UPDATE accounts SET is_pinned = 1 WHERE id = ?", (account_id,))
        con.commit()


def unpin_all():
    with _conn() as con:
        con.execute("UPDATE accounts SET is_pinned = 0")
        con.commit()


def get_all_accounts():
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM accounts ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def get_active_account():
    """Pinned key takes priority; otherwise oldest key with calls remaining."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM accounts WHERE is_pinned = 1 LIMIT 1"
        ).fetchone()
        if row:
            return dict(row)
        row = con.execute(
            "SELECT * FROM accounts WHERE calls_left > 0 ORDER BY id ASC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def init_bulk_db():
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS bulk_runs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                mode          TEXT,
                target_count  INTEGER,
                run_forever   INTEGER DEFAULT 0,
                verify_email  INTEGER DEFAULT 0,
                status        TEXT DEFAULT 'running',
                started_at    TEXT DEFAULT CURRENT_TIMESTAMP,
                total_created INTEGER DEFAULT 0,
                total_failed  INTEGER DEFAULT 0
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS bulk_accounts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id     INTEGER REFERENCES bulk_runs(id),
                email      TEXT,
                password   TEXT,
                verified   INTEGER DEFAULT 0,
                status     TEXT DEFAULT 'pending',
                error      TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.commit()


init_db()
_migrate()
init_bulk_db()
