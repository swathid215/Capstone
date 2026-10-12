import sqlite3
import json
import uuid
from pathlib import Path
from typing import Any, Optional
from datetime import datetime, timezone


class ProjectStore:
    """Simple SQLite-backed project store for the local-first UIDT app."""

    def __init__(self, db_path: str = "uidt_projects.db"):
        self.db_path = db_path
        self.db_file = Path(db_path)
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    phone TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    route_json TEXT NOT NULL DEFAULT '{}',
                    interventions_json TEXT NOT NULL DEFAULT '[]',
                    scenario_json TEXT NOT NULL DEFAULT '{}',
                    options_json TEXT NOT NULL DEFAULT '{}',
                    report_json TEXT NOT NULL DEFAULT '{}',
                    active_infra_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
            if "user_id" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN user_id TEXT")

    def _now(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def _row_to_project(self, row: sqlite3.Row) -> dict:
        if row is None:
            return None
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "name": row["name"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "route": json.loads(row["route_json"] or "{}"),
            "interventions": json.loads(row["interventions_json"] or "[]"),
            "scenario": json.loads(row["scenario_json"] or "{}"),
            "options": json.loads(row["options_json"] or "{}"),
            "report": json.loads(row["report_json"] or "{}"),
            "active_infra": json.loads(row["active_infra_json"] or "[]"),
        }

    def create_project(self, name: str, user_id: Optional[str] = None) -> dict:
        now = self._now()
        project_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO projects (
                    id, user_id, name, created_at, updated_at,
                    route_json, interventions_json,
                    scenario_json, options_json,
                    report_json, active_infra_json
                ) VALUES (?, ?, ?, ?, ?, '{}', '[]', '{}', '{}', '{}', '[]')
                """,
                (project_id, user_id, name, now, now),
            )
        return self.get_project(project_id)

    def count_projects(self, user_id: Optional[str] = None) -> int:
        with self._connect() as conn:
            if user_id is None:
                return conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM projects WHERE user_id = ?", (user_id,)).fetchone()[0]

    def list_projects(self, user_id: Optional[str] = None) -> list[dict]:
        with self._connect() as conn:
            if user_id is None:
                rows = conn.execute("SELECT id, name, updated_at FROM projects ORDER BY updated_at DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, name, updated_at FROM projects WHERE user_id = ? ORDER BY updated_at DESC",
                    (user_id,),
                ).fetchall()
        return [{"id": row["id"], "name": row["name"], "updated_at": row["updated_at"]} for row in rows]

    def get_project(self, project_id: str, user_id: Optional[str] = None) -> Optional[dict]:
        with self._connect() as conn:
            query = "SELECT * FROM projects WHERE id = ?"
            params: tuple = (project_id,)
            if user_id is not None:
                query += " AND user_id = ?"
                params += (user_id,)
            row = conn.execute(query, params).fetchone()
        return self._row_to_project(row)

    def update_project(self, project_id: str, patch: dict, user_id: Optional[str] = None) -> Optional[dict]:
        existing = self.get_project(project_id, user_id)
        if existing is None:
            return None
        updated = dict(existing)
        for key in ("name", "route", "interventions", "scenario", "options", "report", "active_infra"):
            if key in patch:
                updated[key] = patch[key]
        if "name" in patch:
            updated["name"] = patch["name"]

        now = self._now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE projects
                SET name = ?, updated_at = ?,
                    route_json = ?,
                    interventions_json = ?,
                    scenario_json = ?,
                    options_json = ?,
                    report_json = ?,
                    active_infra_json = ?
                WHERE id = ? AND (? IS NULL OR user_id = ?)
                """,
                (
                    updated.get("name"),
                    now,
                    json.dumps(updated.get("route", {})),
                    json.dumps(updated.get("interventions", [])),
                    json.dumps(updated.get("scenario", {})),
                    json.dumps(updated.get("options", {})),
                    json.dumps(updated.get("report", {})),
                    json.dumps(updated.get("active_infra", [])),
                    project_id,
                    user_id,
                    user_id,
                ),
            )
        return self.get_project(project_id, user_id)

    def delete_project(self, project_id: str, user_id: Optional[str] = None) -> bool:
        with self._connect() as conn:
            query = "DELETE FROM projects WHERE id = ?"
            params: tuple = (project_id,)
            if user_id is not None:
                query += " AND user_id = ?"
                params += (user_id,)
            cursor = conn.execute(query, params)
        return cursor.rowcount > 0

    def create_user(self, username: str, phone: str, email: str, password_hash: str) -> dict:
        user_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO users (id, username, phone, email, password_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, username, phone, email, password_hash, self._now()),
            )
        return self.get_user_by_id(user_id)

    def get_user_by_id(self, user_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_login(self, phone: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        return dict(row) if row else None

    def user_exists(self, username: str, phone: str, email: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT username, phone, email FROM users WHERE username = ? COLLATE NOCASE OR phone = ? OR email = ? COLLATE NOCASE",
                (username, phone, email),
            ).fetchone()
        if not row:
            return None
        if row["username"].casefold() == username.casefold():
            return "Username is already registered"
        if row["phone"] == phone:
            return "Phone number is already registered"
        return "Email is already registered"

    def save_session(self, token_hash: str, user_id: str, expires_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token_hash, user_id, self._now(), expires_at),
            )

    def get_user_by_session(self, token_hash: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id WHERE sessions.token_hash = ? AND sessions.expires_at > ?",
                (token_hash, self._now()),
            ).fetchone()
        return dict(row) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
