"""Persistent SQLite-backed workflow execution history and event tracking."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("backend.workflows")

# Workflow statuses
STATUS_PENDING = "PENDING"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"
STATUS_CANCELLED = "CANCELLED"

VALID_STATUSES = {
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    STATUS_FAILED,
    STATUS_CANCELLED,
}

# Agent statuses
AGENT_PENDING = "PENDING"
AGENT_RUNNING = "RUNNING"
AGENT_COMPLETED = "COMPLETED"
AGENT_FAILED = "FAILED"
AGENT_SKIPPED = "SKIPPED"

# Workflow stages
STAGE_INITIALIZING = "INITIALIZING"
STAGE_PLANNING = "PLANNING"
STAGE_CODING = "CODING"
STAGE_TESTING = "TESTING"
STAGE_REVIEWING = "REVIEWING"
STAGE_FINALIZING = "FINALIZING"
STAGE_COMPLETED = "COMPLETED"
STAGE_FAILED = "FAILED"

# Event types
EVENT_WORKFLOW_STARTED = "WORKFLOW_STARTED"
EVENT_PLANNER_STARTED = "PLANNER_STARTED"
EVENT_PLANNER_COMPLETED = "PLANNER_COMPLETED"
EVENT_PLANNER_FAILED = "PLANNER_FAILED"
EVENT_CODE_GENERATION_STARTED = "CODE_GENERATION_STARTED"
EVENT_CODE_GENERATION_COMPLETED = "CODE_GENERATION_COMPLETED"
EVENT_CODE_GENERATION_FAILED = "CODE_GENERATION_FAILED"
EVENT_TESTS_STARTED = "TESTS_STARTED"
EVENT_TESTS_COMPLETED = "TESTS_COMPLETED"
EVENT_TESTS_FAILED = "TESTS_FAILED"
EVENT_REVIEWER_STARTED = "REVIEWER_STARTED"
EVENT_REVIEWER_COMPLETED = "REVIEWER_COMPLETED"
EVENT_REVIEWER_FAILED = "REVIEWER_FAILED"
EVENT_PROPOSAL_CREATED = "PROPOSAL_CREATED"
EVENT_WORKFLOW_SUCCEEDED = "WORKFLOW_SUCCEEDED"
EVENT_WORKFLOW_FAILED = "WORKFLOW_FAILED"
EVENT_WORKFLOW_CANCELLED = "WORKFLOW_CANCELLED"
EVENT_DIAGNOSIS_REQUESTED = "DIAGNOSIS_REQUESTED"
EVENT_DIAGNOSIS_COMPLETED = "DIAGNOSIS_COMPLETED"
EVENT_WORKFLOW_RETRIED = "WORKFLOW_RETRIED"


class WorkflowStorageError(RuntimeError):
    """Storage failure that callers can report without database details."""


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _sanitize_message(message: str) -> str:
    """Redact tokens, passwords, and sensitive keys from log/event messages."""
    if not message:
        return ""
    # Redact common token patterns: ghp_..., bearer tokens, api keys
    cleaned = re.sub(r"(ghp_[A-Za-z0-9_]{30,})", "[REDACTED_GITHUB_TOKEN]", message)
    cleaned = re.sub(r"(gsk_[A-Za-z0-9_]{30,})", "[REDACTED_API_KEY]", cleaned)
    cleaned = re.sub(r"(Bearer\s+[A-Za-z0-9_.-]{15,})", "Bearer [REDACTED_TOKEN]", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(password|token|secret|api[_-]?key)=['\"][^'\"]+['\"]", r"\1='[REDACTED]'", cleaned, flags=re.IGNORECASE)
    return cleaned[:1000]


@dataclass
class WorkflowExecution:
    workflow_id: str
    repository: str
    issue_number: Optional[int]
    status: str
    stage: str
    planner_status: str
    coder_status: str
    tester_status: str
    reviewer_status: str
    error_summary: Optional[str]
    proposal_id: Optional[str]
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    metadata_json: Optional[str] = None
    retry_of_workflow_id: Optional[str] = None
    diagnosis_json: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["started_at"] = self.started_at.isoformat() if self.started_at else None
        data["completed_at"] = self.completed_at.isoformat() if self.completed_at else None
        if self.metadata_json:
            try:
                data["metadata"] = json.loads(self.metadata_json)
            except Exception:
                data["metadata"] = {}
        else:
            data["metadata"] = {}
        data.pop("metadata_json", None)

        if self.diagnosis_json:
            try:
                data["diagnosis"] = json.loads(self.diagnosis_json)
            except Exception:
                data["diagnosis"] = None
        else:
            data["diagnosis"] = None
        data.pop("diagnosis_json", None)

        return data


@dataclass
class WorkflowEvent:
    event_id: int
    workflow_id: str
    event_type: str
    stage: str
    message: str
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "workflow_id": self.workflow_id,
            "event_type": self.event_type,
            "stage": self.stage,
            "message": self.message,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


class WorkflowStore:
    """SQLite persistence for workflow execution history and event logs."""

    _MIGRATIONS = {
        101: """
            CREATE TABLE IF NOT EXISTS workflows (
                workflow_id TEXT PRIMARY KEY,
                repository TEXT NOT NULL,
                issue_number INTEGER,
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                planner_status TEXT NOT NULL DEFAULT 'PENDING',
                coder_status TEXT NOT NULL DEFAULT 'PENDING',
                tester_status TEXT NOT NULL DEFAULT 'PENDING',
                reviewer_status TEXT NOT NULL DEFAULT 'PENDING',
                error_summary TEXT,
                proposal_id TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                duration_seconds REAL,
                metadata_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows (status);
            CREATE INDEX IF NOT EXISTS idx_workflows_repository ON workflows (repository);
            CREATE INDEX IF NOT EXISTS idx_workflows_started_at ON workflows (started_at DESC);

            CREATE TABLE IF NOT EXISTS workflow_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                stage TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (workflow_id) REFERENCES workflows (workflow_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_workflow_events_workflow ON workflow_events (workflow_id, event_id ASC);
        """,
        102: """
            ALTER TABLE workflows ADD COLUMN retry_of_workflow_id TEXT;
            ALTER TABLE workflows ADD COLUMN diagnosis_json TEXT;
            CREATE INDEX IF NOT EXISTS idx_workflows_retry_of ON workflows (retry_of_workflow_id);
        """,
    }

    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        """Create schema and apply migrations safely."""
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                applied = {row["version"] for row in connection.execute("SELECT version FROM schema_migrations")}
                for version, migration in self._MIGRATIONS.items():
                    if version not in applied:
                        for statement in migration.split(";"):
                            if statement.strip():
                                connection.execute(statement)
                        connection.execute(
                            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                            (version, datetime.now(UTC).isoformat()),
                        )
                connection.commit()
        except sqlite3.Error as error:
            logger.error("Failed to initialize workflow store: %s", error)
            raise WorkflowStorageError("Workflow storage is unavailable.") from error

    @staticmethod
    def _now(now: datetime | None = None) -> datetime:
        return _as_utc(now) if now else datetime.now(UTC)

    def create_workflow(
        self,
        *,
        repository: str,
        issue_number: Optional[int] = None,
        workflow_id: Optional[str] = None,
        status: str = STATUS_RUNNING,
        stage: str = STAGE_INITIALIZING,
        metadata: Optional[dict[str, Any]] = None,
        retry_of_workflow_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> WorkflowExecution:
        """Create and persist a new workflow execution record."""
        started_at = self._now(now)
        wf_id = workflow_id or str(uuid.uuid4())
        execution = WorkflowExecution(
            workflow_id=wf_id,
            repository=repository.strip(),
            issue_number=issue_number,
            status=status if status in VALID_STATUSES else STATUS_RUNNING,
            stage=stage,
            planner_status=AGENT_PENDING,
            coder_status=AGENT_PENDING,
            tester_status=AGENT_PENDING,
            reviewer_status=AGENT_PENDING,
            error_summary=None,
            proposal_id=None,
            started_at=started_at,
            completed_at=None,
            duration_seconds=None,
            metadata_json=json.dumps(metadata) if metadata else None,
            retry_of_workflow_id=retry_of_workflow_id,
            diagnosis_json=None,
        )

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO workflows (
                        workflow_id, repository, issue_number, status, stage,
                        planner_status, coder_status, tester_status, reviewer_status,
                        error_summary, proposal_id, started_at, completed_at,
                        duration_seconds, metadata_json, retry_of_workflow_id, diagnosis_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        execution.workflow_id,
                        execution.repository,
                        execution.issue_number,
                        execution.status,
                        execution.stage,
                        execution.planner_status,
                        execution.coder_status,
                        execution.tester_status,
                        execution.reviewer_status,
                        execution.error_summary,
                        execution.proposal_id,
                        execution.started_at.isoformat(),
                        None,
                        None,
                        execution.metadata_json,
                        execution.retry_of_workflow_id,
                        None,
                    ),
                )
            return execution
        except sqlite3.Error as error:
            logger.error("Failed to create workflow record: %s", error)
            raise WorkflowStorageError("Failed to store workflow execution.") from error

    def update_workflow(
        self,
        workflow_id: str,
        *,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        planner_status: Optional[str] = None,
        coder_status: Optional[str] = None,
        tester_status: Optional[str] = None,
        reviewer_status: Optional[str] = None,
        error_summary: Optional[str] = None,
        proposal_id: Optional[str] = None,
        completed_at: Optional[datetime] = None,
        metadata: Optional[dict[str, Any]] = None,
        diagnosis: Optional[dict[str, Any]] = None,
        retry_of_workflow_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Optional[WorkflowExecution]:
        """Update an existing workflow execution record."""
        current = self.get_workflow(workflow_id)
        if not current:
            return None

        updates = []
        params = []

        if status is not None and status in VALID_STATUSES:
            updates.append("status = ?")
            params.append(status)

        if stage is not None:
            updates.append("stage = ?")
            params.append(stage)

        if planner_status is not None:
            updates.append("planner_status = ?")
            params.append(planner_status)

        if coder_status is not None:
            updates.append("coder_status = ?")
            params.append(coder_status)

        if tester_status is not None:
            updates.append("tester_status = ?")
            params.append(tester_status)

        if reviewer_status is not None:
            updates.append("reviewer_status = ?")
            params.append(reviewer_status)

        if error_summary is not None:
            updates.append("error_summary = ?")
            params.append(_sanitize_message(error_summary))

        if proposal_id is not None:
            updates.append("proposal_id = ?")
            params.append(proposal_id)

        if metadata is not None:
            updates.append("metadata_json = ?")
            params.append(json.dumps(metadata))

        if diagnosis is not None:
            updates.append("diagnosis_json = ?")
            params.append(json.dumps(diagnosis))

        if retry_of_workflow_id is not None:
            updates.append("retry_of_workflow_id = ?")
            params.append(retry_of_workflow_id)

        # Check if finishing
        is_finishing = (status in {STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELLED})
        if completed_at or (is_finishing and not current.completed_at):
            finish_time = self._now(completed_at or now)
            updates.append("completed_at = ?")
            params.append(finish_time.isoformat())

            # compute duration
            duration = max(0.0, (finish_time - current.started_at).total_seconds())
            updates.append("duration_seconds = ?")
            params.append(round(duration, 2))

        if not updates:
            return current

        params.append(workflow_id)
        sql = f"UPDATE workflows SET {', '.join(updates)} WHERE workflow_id = ?"

        try:
            with self._connect() as connection:
                connection.execute(sql, params)
            return self.get_workflow(workflow_id)
        except sqlite3.Error as error:
            logger.error("Failed to update workflow %s: %s", workflow_id, error)
            raise WorkflowStorageError("Failed to update workflow execution.") from error

    def record_event(
        self,
        workflow_id: str,
        *,
        event_type: str,
        stage: str,
        message: str,
        timestamp: Optional[datetime] = None,
    ) -> WorkflowEvent:
        """Record an event for a workflow."""
        event_time = self._now(timestamp)
        clean_msg = _sanitize_message(message)
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO workflow_events (workflow_id, event_type, stage, message, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (workflow_id, event_type, stage, clean_msg, event_time.isoformat()),
                )
                event_id = cursor.lastrowid
            return WorkflowEvent(
                event_id=event_id,
                workflow_id=workflow_id,
                event_type=event_type,
                stage=stage,
                message=clean_msg,
                timestamp=event_time,
            )
        except sqlite3.Error as error:
            logger.error("Failed to record event for workflow %s: %s", workflow_id, error)
            raise WorkflowStorageError("Failed to record workflow event.") from error

    def get_workflow(self, workflow_id: str) -> Optional[WorkflowExecution]:
        """Fetch a single workflow by ID."""
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM workflows WHERE workflow_id = ?",
                    (workflow_id,),
                ).fetchone()
                if not row:
                    return None
                return self._row_to_execution(row)
        except sqlite3.Error as error:
            logger.error("Failed to fetch workflow %s: %s", workflow_id, error)
            raise WorkflowStorageError("Failed to retrieve workflow execution.") from error

    def list_workflows(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: Optional[str] = None,
        repository: Optional[str] = None,
    ) -> tuple[list[WorkflowExecution], int]:
        """Return paginated workflows with optional status and repository filtering, plus total count."""
        where_clauses = []
        params: list[Any] = []

        if status:
            clean_status = status.strip().upper()
            if clean_status in VALID_STATUSES:
                where_clauses.append("status = ?")
                params.append(clean_status)

        if repository:
            clean_repo = repository.strip()
            if clean_repo:
                where_clauses.append("repository LIKE ?")
                params.append(f"%{clean_repo}%")

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        try:
            with self._connect() as connection:
                count_query = f"SELECT COUNT(*) as total FROM workflows {where_sql}"
                total = connection.execute(count_query, params).fetchone()["total"]

                query = f"""
                    SELECT * FROM workflows
                    {where_sql}
                    ORDER BY started_at DESC, workflow_id DESC
                    LIMIT ? OFFSET ?
                """
                query_params = list(params) + [max(1, limit), max(0, offset)]
                rows = connection.execute(query, query_params).fetchall()
                workflows = [self._row_to_execution(row) for row in rows]
                return workflows, total
        except sqlite3.Error as error:
            logger.error("Failed to list workflows: %s", error)
            raise WorkflowStorageError("Failed to list workflow executions.") from error

    def get_workflow_events(self, workflow_id: str) -> list[WorkflowEvent]:
        """Fetch all events for a workflow in chronological order."""
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM workflow_events WHERE workflow_id = ? ORDER BY event_id ASC",
                    (workflow_id,),
                ).fetchall()
                events = []
                for row in rows:
                    ts = datetime.fromisoformat(row["timestamp"])
                    events.append(
                        WorkflowEvent(
                            event_id=row["event_id"],
                            workflow_id=row["workflow_id"],
                            event_type=row["event_type"],
                            stage=row["stage"],
                            message=row["message"],
                            timestamp=ts,
                        )
                    )
                return events
        except sqlite3.Error as error:
            logger.error("Failed to fetch events for workflow %s: %s", workflow_id, error)
            raise WorkflowStorageError("Failed to retrieve workflow events.") from error

    def get_stats(self) -> dict[str, Any]:
        """Return aggregate statistics across all workflow executions."""
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT
                        COUNT(*) as total_executions,
                        SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) as successful_executions,
                        SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed_executions,
                        SUM(CASE WHEN status = 'RUNNING' THEN 1 ELSE 0 END) as running_executions,
                        SUM(CASE WHEN status = 'CANCELLED' THEN 1 ELSE 0 END) as cancelled_executions,
                        AVG(CASE WHEN duration_seconds IS NOT NULL THEN duration_seconds ELSE NULL END) as avg_duration_seconds
                    FROM workflows
                    """
                ).fetchone()

                total = row["total_executions"] or 0
                successful = row["successful_executions"] or 0
                failed = row["failed_executions"] or 0
                running = row["running_executions"] or 0
                cancelled = row["cancelled_executions"] or 0
                avg_duration = round(row["avg_duration_seconds"] or 0.0, 2)
                success_rate = round((successful / total) * 100.0, 1) if total > 0 else 0.0

                return {
                    "total_executions": total,
                    "successful_executions": successful,
                    "failed_executions": failed,
                    "running_executions": running,
                    "cancelled_executions": cancelled,
                    "success_rate": success_rate,
                    "average_duration_seconds": avg_duration,
                }
        except sqlite3.Error as error:
            logger.error("Failed to compute workflow stats: %s", error)
            raise WorkflowStorageError("Failed to calculate workflow statistics.") from error

    def store_diagnosis(self, workflow_id: str, diagnosis: dict[str, Any]) -> None:
        """Store or update AI failure diagnosis for a workflow."""
        try:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE workflows SET diagnosis_json = ? WHERE workflow_id = ?",
                    (json.dumps(diagnosis), workflow_id),
                )
        except sqlite3.Error as error:
            logger.error("Failed to store diagnosis for workflow %s: %s", workflow_id, error)
            raise WorkflowStorageError("Failed to store workflow diagnosis.") from error

    def get_diagnosis(self, workflow_id: str) -> Optional[dict[str, Any]]:
        """Retrieve stored diagnosis for a workflow if present."""
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT diagnosis_json FROM workflows WHERE workflow_id = ?",
                    (workflow_id,),
                ).fetchone()
                if not row or not row["diagnosis_json"]:
                    return None
                return json.loads(row["diagnosis_json"])
        except Exception as error:
            logger.warning("Failed to retrieve diagnosis for %s: %s", workflow_id, error)
            return None

    def find_retries(self, original_workflow_id: str) -> list[WorkflowExecution]:
        """Find all workflows retried from the given original workflow ID."""
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM workflows WHERE retry_of_workflow_id = ? ORDER BY started_at DESC, workflow_id DESC",
                    (original_workflow_id,),
                ).fetchall()
                return [self._row_to_execution(row) for row in rows]
        except sqlite3.Error as error:
            logger.error("Failed to find retries for %s: %s", original_workflow_id, error)
            raise WorkflowStorageError("Failed to find retried workflows.") from error

    @staticmethod
    def _row_to_execution(row: sqlite3.Row) -> WorkflowExecution:
        started_at = datetime.fromisoformat(row["started_at"])
        completed_at = datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
        keys = row.keys()
        retry_of_wf = row["retry_of_workflow_id"] if "retry_of_workflow_id" in keys else None
        diag_json = row["diagnosis_json"] if "diagnosis_json" in keys else None
        return WorkflowExecution(
            workflow_id=row["workflow_id"],
            repository=row["repository"],
            issue_number=row["issue_number"],
            status=row["status"],
            stage=row["stage"],
            planner_status=row["planner_status"],
            coder_status=row["coder_status"],
            tester_status=row["tester_status"],
            reviewer_status=row["reviewer_status"],
            error_summary=row["error_summary"],
            proposal_id=row["proposal_id"],
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=row["duration_seconds"],
            metadata_json=row["metadata_json"],
            retry_of_workflow_id=retry_of_wf,
            diagnosis_json=diag_json,
        )
