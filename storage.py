import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "accounts.db")


def _conn():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    return con


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


def create_bulk_run(mode: str, target_count, run_forever: bool, verify_email: bool) -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO bulk_runs (mode, target_count, run_forever, verify_email, status) VALUES (?,?,?,?,'running')",
            (mode, target_count, int(run_forever), int(verify_email)),
        )
        con.commit()
        return cur.lastrowid


def get_bulk_run(run_id: int) -> dict | None:
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM bulk_runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None


def update_bulk_run_status(run_id: int, status: str):
    with _conn() as con:
        con.execute("UPDATE bulk_runs SET status=? WHERE id=?", (status, run_id))
        con.commit()


def increment_bulk_run_counts(run_id: int, created: int = 0, failed: int = 0):
    with _conn() as con:
        con.execute(
            "UPDATE bulk_runs SET total_created=total_created+?, total_failed=total_failed+? WHERE id=?",
            (created, failed, run_id),
        )
        con.commit()


def save_bulk_account(run_id: int, email: str, password: str, status: str, error: str = None):
    verified = 1 if status == "verified" else 0
    with _conn() as con:
        con.execute(
            "INSERT INTO bulk_accounts (run_id, email, password, verified, status, error) VALUES (?,?,?,?,?,?)",
            (run_id, email, password, verified, status, error),
        )
        con.commit()


def delete_bulk_accounts(ids: list) -> int:
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    with _conn() as con:
        cur = con.execute(
            f"DELETE FROM bulk_accounts WHERE id IN ({placeholders})",
            list(ids),
        )
        con.commit()
        return cur.rowcount


def get_bulk_accounts(run_id: int = None, status: str = None, mode: str = None) -> list[dict]:
    query = """
        SELECT ba.*, br.mode
        FROM bulk_accounts ba
        JOIN bulk_runs br ON ba.run_id = br.id
        WHERE 1=1
    """
    params = []
    if run_id is not None:
        query += " AND ba.run_id=?"
        params.append(run_id)
    if status:
        query += " AND ba.status=?"
        params.append(status)
    if mode:
        query += " AND br.mode=?"
        params.append(mode)
    query += " ORDER BY ba.id DESC"
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(query, params).fetchall()
        return [dict(r) for r in rows]


init_db()
_migrate()
init_bulk_db()
