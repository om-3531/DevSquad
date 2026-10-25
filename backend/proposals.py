"""Persistent, expiring records for human review of dry-run candidates."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


PENDING_REVIEW = "PENDING_REVIEW"
APPROVED = "APPROVED"
EXPIRED = "EXPIRED"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
STALE = "STALE"
WRITE_IN_PROGRESS = "WRITE_IN_PROGRESS"
COMMITTED = "COMMITTED"
PR_CREATED = "PR_CREATED"
WRITE_FAILED = "WRITE_FAILED"


class ProposalStorageError(RuntimeError):
    """A storage failure that callers can report without database details."""


class StaleProposalError(RuntimeError):
    """Raised when a proposal's reviewed repository revision no longer matches."""


def _is_safe_branch_name(value: str) -> bool:
    return bool(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value) and ".." not in value and "//" not in value and not value.endswith(("/", ".", ".lock")))


def _is_safe_repository(value: str) -> bool:
    return bool(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value))


def _is_safe_path(value: str | None) -> bool:
    return value is None or bool(isinstance(value, str) and value.endswith(".py") and not value.startswith(("/", "\\")) and "\\" not in value and all(part not in {"", ".", ".."} for part in value.split("/")))


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@dataclass
class Proposal:
    proposal_id: str
    repository: str
    issue_number: int
    target_branch: str
    target_path: str | None
    operation: str | None
    proposed_content: str
    content_digest: str
    review_digest: str
    base_commit_sha: str | None
    target_file_blob_sha: str | None
    write_branch: str | None
    commit_sha: str | None
    pr_number: int | None
    pr_url: str | None
    diff_preview: dict | None
    quality_gate_status: str
    status: str
    created_at: datetime
    expires_at: datetime
    approved_at: datetime | None = None
    approved_by: str | None = None

    def public_dict(self) -> dict:
        data = asdict(self)
        # Candidate text is retained only for a server-side integrity check.
        data.pop("proposed_content")
        data.pop("approved_by")
        for field in ("created_at", "expires_at", "approved_at"):
            value = data[field]
            data[field] = value.isoformat() if value else None
        return data


class ProposalStore:
    """SQLite-backed proposal registry for one backend installation.

    SQLite provides durable local storage and transactional state transitions. It
    is not a distributed locking service or an authentication mechanism.
    """

    _MIGRATIONS = {
        1: """
            CREATE TABLE IF NOT EXISTS proposals (
                proposal_id TEXT PRIMARY KEY,
                repository TEXT NOT NULL,
                issue_number INTEGER NOT NULL,
                target_branch TEXT NOT NULL,
                target_path TEXT,
                operation TEXT,
                proposed_content TEXT NOT NULL,
                content_digest TEXT NOT NULL,
                diff_preview_json TEXT,
                quality_gate_status TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                approved_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_proposals_expiration ON proposals (expires_at);
        """,
        2: """
            ALTER TABLE proposals ADD COLUMN approved_by TEXT;
            CREATE TABLE IF NOT EXISTS approval_audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id TEXT NOT NULL,
                actor_subject TEXT NOT NULL,
                action TEXT NOT NULL,
                content_digest TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                FOREIGN KEY (proposal_id) REFERENCES proposals (proposal_id)
            );
            CREATE INDEX IF NOT EXISTS idx_approval_audit_proposal
                ON approval_audit (proposal_id, audit_id);
        """,
        3: """
            ALTER TABLE proposals ADD COLUMN review_digest TEXT;
            ALTER TABLE proposals ADD COLUMN base_commit_sha TEXT;
            ALTER TABLE proposals ADD COLUMN target_file_blob_sha TEXT;
        """,
        4: """
            ALTER TABLE proposals ADD COLUMN write_branch TEXT;
            ALTER TABLE proposals ADD COLUMN commit_sha TEXT;
            ALTER TABLE proposals ADD COLUMN pr_number INTEGER;
            ALTER TABLE proposals ADD COLUMN pr_url TEXT;
        """,
    }

    def __init__(self, database_path: str | Path, ttl_seconds: int = 3600):
        self.database_path = str(database_path)
        self.ttl_seconds = ttl_seconds
        if self.database_path != ":memory:":
            Path(self.database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @staticmethod
    def digest(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self) -> None:
        """Create the schema and apply numbered migrations exactly once."""
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
                applied = {row["version"] for row in connection.execute("SELECT version FROM schema_migrations")}
                for version, migration in self._MIGRATIONS.items():
                    if version not in applied:
                        for statement in migration.split(";"):
                            if statement.strip():
                                connection.execute(statement)
                        connection.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", (version, datetime.now(UTC).isoformat()))
                connection.commit()
        except sqlite3.Error as error:
            raise ProposalStorageError("Proposal storage is unavailable.") from error

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Proposal:
        try:
            return Proposal(
                proposal_id=row["proposal_id"], repository=row["repository"], issue_number=row["issue_number"],
                target_branch=row["target_branch"], target_path=row["target_path"], operation=row["operation"],
                proposed_content=row["proposed_content"], content_digest=row["content_digest"],
                review_digest=row["review_digest"] or "", base_commit_sha=row["base_commit_sha"],
                target_file_blob_sha=row["target_file_blob_sha"],
                write_branch=row["write_branch"], commit_sha=row["commit_sha"],
                pr_number=row["pr_number"], pr_url=row["pr_url"],
                diff_preview=json.loads(row["diff_preview_json"]) if row["diff_preview_json"] else None,
                quality_gate_status=row["quality_gate_status"], status=row["status"],
                created_at=datetime.fromisoformat(row["created_at"]), expires_at=datetime.fromisoformat(row["expires_at"]),
                approved_at=datetime.fromisoformat(row["approved_at"]) if row["approved_at"] else None,
                approved_by=row["approved_by"],
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ProposalStorageError("Proposal storage contains an invalid record.") from error

    @staticmethod
    def _now(now: datetime | None) -> datetime:
        return _as_utc(now) if now else datetime.now(UTC)

    @staticmethod
    def review_digest(*, content_digest: str, repository: str, issue_number: int, target_branch: str, target_path: str | None, operation: str | None, base_commit_sha: str | None, target_file_blob_sha: str | None, diff_preview: dict | None) -> str:
        """Bind reviewed content, target, revisions, and rendered diff together."""
        material = {
            "content_digest": content_digest,
            "repository": repository,
            "issue_number": issue_number,
            "target_branch": target_branch,
            "target_path": target_path,
            "operation": operation,
            "base_commit_sha": base_commit_sha,
            "target_file_blob_sha": target_file_blob_sha,
            "diff_preview": diff_preview,
        }
        return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def create(self, *, repository: str, issue_number: int, target_branch: str, target_path: str | None, operation: str | None, proposed_content: str, diff_preview: dict | None, quality_gate_status: str, base_commit_sha: str | None = None, target_file_blob_sha: str | None = None, now: datetime | None = None) -> Proposal:
        if not _is_safe_repository(repository):
            raise ValueError("Proposal repository is invalid.")
        if not _is_safe_branch_name(target_branch):
            raise ValueError("Proposal target branch is invalid.")
        if not _is_safe_path(target_path):
            raise ValueError("Proposal target path is invalid.")
        if not isinstance(issue_number, int) or issue_number < 1:
            raise ValueError("Proposal issue number is invalid.")
        if not isinstance(proposed_content, str):
            raise ValueError("Proposal content is invalid.")
        if base_commit_sha is not None and not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", base_commit_sha):
            raise ValueError("Proposal base revision is invalid.")
        if target_file_blob_sha is not None and not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", target_file_blob_sha):
            raise ValueError("Proposal file revision is invalid.")

        created_at = self._now(now)
        eligible = bool(target_path and diff_preview and diff_preview.get("reliable") and diff_preview.get("changed") and quality_gate_status == "REVIEW_APPROVED_GENERATED_TESTS_PASSED")
        content_digest = self.digest(proposed_content)
        review_digest = self.review_digest(content_digest=content_digest, repository=repository, issue_number=issue_number, target_branch=target_branch, target_path=target_path, operation=operation, base_commit_sha=base_commit_sha, target_file_blob_sha=target_file_blob_sha, diff_preview=diff_preview)
        proposal = Proposal(str(uuid.uuid4()), repository, issue_number, target_branch, target_path, operation, proposed_content, content_digest, review_digest, base_commit_sha, target_file_blob_sha, None, None, None, None, diff_preview, quality_gate_status, PENDING_REVIEW if eligible else NOT_ELIGIBLE, created_at, created_at + timedelta(seconds=self.ttl_seconds))
        try:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO proposals (proposal_id, repository, issue_number, target_branch, target_path, operation, proposed_content, content_digest, diff_preview_json, quality_gate_status, status, created_at, expires_at, approved_at, approved_by, review_digest, base_commit_sha, target_file_blob_sha, write_branch, commit_sha, pr_number, pr_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (proposal.proposal_id, proposal.repository, proposal.issue_number, proposal.target_branch, proposal.target_path, proposal.operation, proposal.proposed_content, proposal.content_digest, json.dumps(proposal.diff_preview, separators=(",", ":")), proposal.quality_gate_status, proposal.status, proposal.created_at.isoformat(), proposal.expires_at.isoformat(), None, None, proposal.review_digest, proposal.base_commit_sha, proposal.target_file_blob_sha, None, None, None, None),
                )
        except sqlite3.Error as error:
            raise ProposalStorageError("Proposal storage is unavailable.") from error
        return proposal

    def get(self, proposal_id: str, now: datetime | None = None) -> Proposal | None:
        current_time = self._now(now)
        try:
            with self._connect() as connection:
                connection.execute("UPDATE proposals SET status = ? WHERE proposal_id = ? AND status = ? AND expires_at <= ?", (EXPIRED, proposal_id, PENDING_REVIEW, current_time.isoformat()))
                row = connection.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
        except sqlite3.Error as error:
            raise ProposalStorageError("Proposal storage is unavailable.") from error
        return self._from_row(row) if row else None

    def approve(self, proposal_id: str, content_digest: str, approved_by: str, now: datetime | None = None) -> Proposal:
        """Atomically approve exactly one current, unexpired proposal."""
        current_time = self._now(now)
        if not isinstance(approved_by, str) or not approved_by.strip() or len(approved_by) > 256 or any(ord(character) < 32 for character in approved_by):
            raise ValueError("Approval identity is invalid.")
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
                if row is None:
                    raise KeyError(proposal_id)
                proposal = self._from_row(row)
                if proposal.status == PENDING_REVIEW and proposal.expires_at <= current_time:
                    connection.execute("UPDATE proposals SET status = ? WHERE proposal_id = ? AND status = ?", (EXPIRED, proposal_id, PENDING_REVIEW))
                    connection.commit()
                    raise TimeoutError(proposal_id)
                if proposal.status == EXPIRED:
                    raise TimeoutError(proposal_id)
                if proposal.status == APPROVED:
                    raise FileExistsError(proposal_id)
                if proposal.status != PENDING_REVIEW:
                    raise ValueError("Proposal is not eligible for approval.")
                current_digest = self.digest(proposal.proposed_content)
                current_review_digest = self.review_digest(content_digest=current_digest, repository=proposal.repository, issue_number=proposal.issue_number, target_branch=proposal.target_branch, target_path=proposal.target_path, operation=proposal.operation, base_commit_sha=proposal.base_commit_sha, target_file_blob_sha=proposal.target_file_blob_sha, diff_preview=proposal.diff_preview)
                if not hmac.compare_digest(proposal.content_digest, current_digest) or not hmac.compare_digest(proposal.review_digest, current_review_digest) or not hmac.compare_digest(proposal.content_digest, content_digest):
                    raise PermissionError("Proposal content no longer matches the reviewed candidate.")
                result = connection.execute("UPDATE proposals SET status = ?, approved_at = ?, approved_by = ? WHERE proposal_id = ? AND status = ? AND content_digest = ?", (APPROVED, current_time.isoformat(), approved_by, proposal_id, PENDING_REVIEW, proposal.content_digest))
                if result.rowcount != 1:
                    raise FileExistsError(proposal_id)
                connection.execute("INSERT INTO approval_audit (proposal_id, actor_subject, action, content_digest, occurred_at) VALUES (?, ?, ?, ?, ?)", (proposal_id, approved_by, "APPROVED", proposal.content_digest, current_time.isoformat()))
                connection.commit()
                proposal.status = APPROVED
                proposal.approved_at = current_time
                proposal.approved_by = approved_by
                return proposal
        except (KeyError, TimeoutError, FileExistsError, PermissionError, ValueError):
            raise
        except sqlite3.Error as error:
            raise ProposalStorageError("Proposal storage is unavailable.") from error

    def verify_revision(self, proposal_id: str, base_commit_sha: str | None, target_file_blob_sha: str | None) -> Proposal:
        """Atomically reject a proposal when a read-only remote revision check differs."""
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
                if row is None:
                    raise KeyError(proposal_id)
                proposal = self._from_row(row)
                matches = bool(proposal.base_commit_sha) and hmac.compare_digest(proposal.base_commit_sha, base_commit_sha or "")
                if proposal.operation == "modify":
                    matches = matches and bool(proposal.target_file_blob_sha) and hmac.compare_digest(proposal.target_file_blob_sha, target_file_blob_sha or "")
                elif proposal.operation == "create":
                    matches = matches and proposal.target_file_blob_sha is None and target_file_blob_sha is None
                if not matches:
                    connection.execute("UPDATE proposals SET status = ? WHERE proposal_id = ? AND status IN (?, ?, ?, ?)", (STALE, proposal_id, PENDING_REVIEW, APPROVED, COMMITTED, WRITE_FAILED))
                    connection.execute("INSERT INTO approval_audit (proposal_id, actor_subject, action, content_digest, occurred_at) VALUES (?, ?, ?, ?, ?)", (proposal_id, "system", "REJECTED_STALE_REVISION", proposal.content_digest, self._now(None).isoformat()))
                    connection.commit()
                    raise StaleProposalError("Proposal revision no longer matches the repository.")
                connection.commit()
                return proposal
        except (KeyError, StaleProposalError):
            raise
        except sqlite3.Error as error:
            raise ProposalStorageError("Proposal storage is unavailable.") from error

    def begin_write(self, proposal_id: str, actor_subject: str, branch: str) -> Proposal:
        """Claim one approved proposal for a single write attempt."""
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
                if row is None:
                    raise KeyError(proposal_id)
                proposal = self._from_row(row)
                if proposal.expires_at <= self._now(None):
                    raise TimeoutError(proposal_id)
                can_write = (
                    proposal.status == APPROVED
                    or (proposal.status in (COMMITTED, WRITE_FAILED) and proposal.commit_sha is not None and proposal.pr_number is None)
                )
                if not can_write:
                    raise ValueError("Proposal is not approved for writing.")
                current_digest = self.digest(proposal.proposed_content)
                current_review = self.review_digest(content_digest=current_digest, repository=proposal.repository, issue_number=proposal.issue_number, target_branch=proposal.target_branch, target_path=proposal.target_path, operation=proposal.operation, base_commit_sha=proposal.base_commit_sha, target_file_blob_sha=proposal.target_file_blob_sha, diff_preview=proposal.diff_preview)
                if not hmac.compare_digest(proposal.content_digest, current_digest) or not hmac.compare_digest(proposal.review_digest, current_review):
                    raise PermissionError("Proposal integrity verification failed.")
                result = connection.execute(
                    "UPDATE proposals SET status = ?, write_branch = COALESCE(write_branch, ?) WHERE proposal_id = ? AND status IN (?, ?, ?)",
                    (WRITE_IN_PROGRESS, branch, proposal_id, APPROVED, COMMITTED, WRITE_FAILED),
                )
                if result.rowcount != 1:
                    raise FileExistsError(proposal_id)
                connection.execute("INSERT INTO approval_audit (proposal_id, actor_subject, action, content_digest, occurred_at) VALUES (?, ?, ?, ?, ?)", (proposal_id, actor_subject, "WRITE_STARTED", proposal.content_digest, self._now(None).isoformat()))
                connection.commit()
                proposal.status = WRITE_IN_PROGRESS
                proposal.write_branch = proposal.write_branch or branch
                return proposal
        except (KeyError, TimeoutError, ValueError, PermissionError, FileExistsError):
            raise
        except sqlite3.Error as error:
            raise ProposalStorageError("Proposal storage is unavailable.") from error

    def record_commit(self, proposal_id: str, actor_subject: str, commit_sha: str) -> Proposal:
        return self._complete_write_step(proposal_id, actor_subject, COMMITTED, "COMMIT_CREATED", commit_sha=commit_sha)

    def record_pull_request(self, proposal_id: str, actor_subject: str, number: int, url: str) -> Proposal:
        return self._complete_write_step(proposal_id, actor_subject, PR_CREATED, "DRAFT_PR_CREATED", pr_number=number, pr_url=url)

    def record_write_failure(self, proposal_id: str, actor_subject: str) -> None:
        self._complete_write_step(proposal_id, actor_subject, WRITE_FAILED, "WRITE_FAILED")

    def _complete_write_step(self, proposal_id: str, actor_subject: str, status: str, action: str, *, commit_sha: str | None = None, pr_number: int | None = None, pr_url: str | None = None) -> Proposal:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                result = connection.execute("UPDATE proposals SET status = ?, commit_sha = COALESCE(?, commit_sha), pr_number = COALESCE(?, pr_number), pr_url = COALESCE(?, pr_url) WHERE proposal_id = ? AND status IN (?, ?)", (status, commit_sha, pr_number, pr_url, proposal_id, WRITE_IN_PROGRESS, COMMITTED))
                if result.rowcount != 1:
                    raise ValueError("Proposal is not in a writable state.")
                row = connection.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
                proposal = self._from_row(row)
                connection.execute("INSERT INTO approval_audit (proposal_id, actor_subject, action, content_digest, occurred_at) VALUES (?, ?, ?, ?, ?)", (proposal_id, actor_subject, action, proposal.content_digest, self._now(None).isoformat()))
                connection.commit()
                return proposal
        except ValueError:
            raise
        except sqlite3.Error as error:
            raise ProposalStorageError("Proposal storage is unavailable.") from error

    def audit_entries(self, proposal_id: str) -> list[dict[str, str]]:
        """Return internal audit data; this is intentionally not an API response."""
        try:
            with self._connect() as connection:
                rows = connection.execute("SELECT proposal_id, actor_subject, action, content_digest, occurred_at FROM approval_audit WHERE proposal_id = ? ORDER BY audit_id", (proposal_id,)).fetchall()
        except sqlite3.Error as error:
            raise ProposalStorageError("Proposal storage is unavailable.") from error
        return [dict(row) for row in rows]

    def record_audit(self, proposal_id: str, actor_subject: str, action: str, content_digest: str = "", now: datetime | None = None) -> None:
        """Record an audit event (such as rejected approval attempts) without storing private code or secrets."""
        current_time = self._now(now)
        safe_action = re.sub(r"[^A-Za-z0-9_:-]", "_", action)[:64]
        safe_subject = actor_subject[:256] if actor_subject else "unknown"
        safe_digest = content_digest if (isinstance(content_digest, str) and re.fullmatch(r"[0-9a-f]{64}", content_digest)) else ""
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT proposal_id FROM proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
                if row:
                    connection.execute(
                        "INSERT INTO approval_audit (proposal_id, actor_subject, action, content_digest, occurred_at) VALUES (?, ?, ?, ?, ?)",
                        (proposal_id, safe_subject, safe_action, safe_digest, current_time.isoformat()),
                    )
        except sqlite3.Error:
            pass

