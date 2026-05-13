"""SQLite-backed idempotency tracker."""

import sqlite3
from datetime import datetime, timezone


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS migrations (
    attachment_id         TEXT PRIMARY KEY,
    status                TEXT NOT NULL,
    hs_note_id            TEXT,
    hs_contact_id         TEXT,
    salesforce_contact_id TEXT,
    processed_at          TEXT NOT NULL
)
"""

_ADD_COLUMNS = [
    "ALTER TABLE migrations ADD COLUMN hs_contact_id TEXT",
    "ALTER TABLE migrations ADD COLUMN salesforce_contact_id TEXT",
]


class StateManager:
    def __init__(self, db_path: str = "migration_state.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(_CREATE_TABLE)
            # Add columns to existing databases that predate this schema
            for sql in _ADD_COLUMNS:
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    pass  # column already exists
            conn.commit()

    def is_processed(self, attachment_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM migrations WHERE attachment_id = ? AND status = 'SUCCESS'",
                (attachment_id,),
            ).fetchone()
        return row is not None

    def mark(
        self,
        attachment_id: str,
        status: str,
        hs_note_id: str = None,
        hs_contact_id: str = None,
        salesforce_contact_id: str = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO migrations
                   (attachment_id, status, hs_note_id, hs_contact_id,
                    salesforce_contact_id, processed_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (attachment_id, status, hs_note_id, hs_contact_id,
                 salesforce_contact_id, now),
            )
            conn.commit()
