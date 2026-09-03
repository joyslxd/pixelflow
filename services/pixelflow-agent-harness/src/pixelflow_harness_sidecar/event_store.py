"""以 SQLite 持久化 Sidecar Run 和公开事件，禁止写入用户原文与 Harness 原始事件。"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from .contracts import (
    HarnessRunEvent,
    HarnessRunRequest,
    HarnessRunState,
    RunStatus,
    TerminationReason,
)
from .skill_snapshot import SkillCatalogSnapshot, SkillSnapshotEntry


def _utc_now() -> str:
    """生成协议规定的 UTC 时间文本。"""

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class RunRequestConflictError(ValueError):
    """表示同一稳定 Run 身份携带了不同的规范请求摘要。"""


class SqliteRunEventStore:
    """管理 Run 状态和单调事件序列的最小持久化 Repository。"""

    def __init__(self, path: Path) -> None:
        """打开指定 SQLite 文件；目录仅用于 Sidecar 自己的运行状态。"""

        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS harness_runs (
                run_id TEXT PRIMARY KEY,
                run_request_key TEXT NOT NULL UNIQUE,
                request_digest TEXT NOT NULL,
                status TEXT NOT NULL,
                termination_reason TEXT,
                engine_id TEXT NOT NULL,
                engine_version TEXT NOT NULL,
                skill_catalog_digest TEXT NOT NULL,
                accepted_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS harness_run_events (
                run_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (run_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS harness_run_skills (
                run_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                body TEXT NOT NULL,
                PRIMARY KEY (run_id, name),
                FOREIGN KEY (run_id) REFERENCES harness_runs(run_id)
            );
            """,
        )
        self._connection.commit()

    async def create_or_get(
        self,
        request: HarnessRunRequest,
        *,
        engine_id: str,
        engine_version: str,
        skill_snapshot: SkillCatalogSnapshot,
    ) -> tuple[HarnessRunState, bool]:
        """原子创建或回读 Run；摘要漂移必须失败关闭。"""

        async with self._lock:
            existing = self._connection.execute(
                "SELECT * FROM harness_runs WHERE run_request_key = ?",
                (request.run_request_key,),
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] != request.request_digest:
                    raise RunRequestConflictError("同一 run_request_key 的请求摘要不一致")
                return self._state(existing), False
            run_id = "hrun_" + hashlib.sha256(
                request.run_request_key.encode("utf-8"),
            ).hexdigest()[:32]
            accepted_at = _utc_now()
            self._connection.execute(
                """
                INSERT INTO harness_runs (
                    run_id, run_request_key, request_digest, status, termination_reason,
                    engine_id, engine_version, skill_catalog_digest, accepted_at, completed_at
                ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, NULL)
                """,
                (
                    run_id,
                    request.run_request_key,
                    request.request_digest,
                    RunStatus.ACCEPTED.value,
                    engine_id,
                    engine_version,
                    skill_snapshot.catalog_digest,
                    accepted_at,
                ),
            )
            self._connection.executemany(
                """
                INSERT INTO harness_run_skills (
                    run_id, name, description, content_sha256, body
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (run_id, entry.name, entry.description, entry.content_sha256, entry.body)
                    for entry in skill_snapshot.entries.values()
                ],
            )
            self._append_locked(
                run_id,
                "run.accepted",
                {"status": RunStatus.ACCEPTED.value},
            )
            self._connection.commit()
            row = self._connection.execute(
                "SELECT * FROM harness_runs WHERE run_id = ?", (run_id,),
            ).fetchone()
            assert row is not None
            return self._state(row), True

    async def get_by_request(self, request: HarnessRunRequest) -> HarnessRunState | None:
        """按稳定请求身份先回读旧 Run，管理员更新不应影响已接受 Run 的重试。"""

        async with self._lock:
            row = self._connection.execute(
                "SELECT * FROM harness_runs WHERE run_request_key = ?",
                (request.run_request_key,),
            ).fetchone()
            if row is None:
                return None
            if row["request_digest"] != request.request_digest:
                raise RunRequestConflictError("同一 run_request_key 的请求摘要不一致")
            return self._state(row)

    async def get_skill_snapshot(self, run_id: str) -> SkillCatalogSnapshot:
        """读取 Run 接受时持久化的 Skill 正文，不重新读取管理员源目录。"""

        async with self._lock:
            run = self._connection.execute(
                "SELECT skill_catalog_digest FROM harness_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise LookupError("Sidecar Run 不存在")
            rows = self._connection.execute(
                """
                SELECT name, description, content_sha256, body
                FROM harness_run_skills WHERE run_id = ? ORDER BY name ASC
                """,
                (run_id,),
            ).fetchall()
        entries = {
            str(row["name"]): SkillSnapshotEntry(
                name=str(row["name"]),
                description=str(row["description"]),
                content_sha256=str(row["content_sha256"]),
                body=str(row["body"]),
            )
            for row in rows
        }
        snapshot = SkillCatalogSnapshot(
            catalog_digest=str(run["skill_catalog_digest"]),
            entries=MappingProxyType(entries),
        )
        digest_source = "\n".join(
            f"{entry.name}:{entry.content_sha256}" for entry in snapshot.entries.values()
        ).encode("utf-8")
        actual_digest = f"sha256:{hashlib.sha256(digest_source).hexdigest()}"
        if actual_digest != snapshot.catalog_digest:
            raise RuntimeError("Run Skill 快照摘要漂移")
        return snapshot

    async def get(self, run_id: str) -> HarnessRunState | None:
        """读取公开 Run 状态。"""

        async with self._lock:
            row = self._connection.execute(
                "SELECT * FROM harness_runs WHERE run_id = ?", (run_id,),
            ).fetchone()
            return None if row is None else self._state(row)

    async def transition(
        self,
        run_id: str,
        *,
        status: RunStatus,
        termination_reason: TerminationReason | None = None,
    ) -> HarnessRunState:
        """提交 Run 状态并在终态写入完成时间。"""

        completed_at = (
            _utc_now()
            if status not in {RunStatus.ACCEPTED, RunStatus.RUNNING}
            else None
        )
        async with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE harness_runs
                SET status = ?, termination_reason = ?, completed_at = COALESCE(?, completed_at)
                WHERE run_id = ?
                """,
                (status.value, termination_reason.value if termination_reason else None, completed_at, run_id),
            )
            if cursor.rowcount != 1:
                raise LookupError("Sidecar Run 不存在")
            self._connection.commit()
            row = self._connection.execute(
                "SELECT * FROM harness_runs WHERE run_id = ?", (run_id,),
            ).fetchone()
            assert row is not None
            return self._state(row)

    async def start_if_accepted(self, run_id: str) -> HarnessRunState | None:
        """仅把已接受 Run 原子切换为运行中，避免取消竞争后重新启动。"""

        async with self._lock:
            row = self._connection.execute(
                "SELECT * FROM harness_runs WHERE run_id = ?", (run_id,),
            ).fetchone()
            if row is None:
                return None
            if RunStatus(row["status"]) is not RunStatus.ACCEPTED:
                return self._state(row)
            self._connection.execute(
                "UPDATE harness_runs SET status = ? WHERE run_id = ? AND status = ?",
                (RunStatus.RUNNING.value, run_id, RunStatus.ACCEPTED.value),
            )
            self._append_locked(run_id, "run.started", {"status": RunStatus.RUNNING.value})
            self._connection.commit()
            updated = self._connection.execute(
                "SELECT * FROM harness_runs WHERE run_id = ?", (run_id,),
            ).fetchone()
            assert updated is not None
            return self._state(updated)

    async def cancel(self, run_id: str) -> HarnessRunState | None:
        """幂等地收口本 Sidecar Run；不声明取消任何外部 Provider。"""

        terminal_statuses = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
        async with self._lock:
            row = self._connection.execute(
                "SELECT * FROM harness_runs WHERE run_id = ?", (run_id,),
            ).fetchone()
            if row is None:
                return None
            current = RunStatus(row["status"])
            if current in terminal_statuses:
                return self._state(row)
            completed_at = _utc_now()
            self._connection.execute(
                """
                UPDATE harness_runs
                SET status = ?, termination_reason = ?, completed_at = ?
                WHERE run_id = ?
                """,
                (
                    RunStatus.CANCELLED.value,
                    TerminationReason.CANCELLED.value,
                    completed_at,
                    run_id,
                ),
            )
            self._append_locked(
                run_id,
                "run.cancelled",
                {"status": RunStatus.CANCELLED.value},
            )
            self._connection.commit()
            updated = self._connection.execute(
                "SELECT * FROM harness_runs WHERE run_id = ?", (run_id,),
            ).fetchone()
            assert updated is not None
            return self._state(updated)

    async def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> HarnessRunEvent:
        """追加唯一公开事件；调用方不得传入模型 reasoning 或 Provider 原始字段。"""

        async with self._lock:
            event = self._append_locked(run_id, event_type, payload)
            self._connection.commit()
            return event

    async def events_after(self, run_id: str, after_sequence: int) -> list[HarnessRunEvent]:
        """按断点游标回放同一 Run 的后续事件。"""

        async with self._lock:
            rows = self._connection.execute(
                """
                SELECT sequence, event_id, event_type, occurred_at, payload_json
                FROM harness_run_events
                WHERE run_id = ? AND sequence > ?
                ORDER BY sequence ASC
                """,
                (run_id, after_sequence),
            ).fetchall()
        import json

        return [
            HarnessRunEvent(
                protocol_version="v1",
                run_id=run_id,
                event_id=row["event_id"],
                sequence=row["sequence"],
                type=row["event_type"],
                occurred_at=row["occurred_at"],
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    async def has_cursor(self, run_id: str, after_sequence: int) -> bool:
        """确认 SSE 断点未超过该 Run 已持久化序列，未知 cursor 必须失败关闭。"""

        async with self._lock:
            row = self._connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) AS max_sequence
                FROM harness_run_events WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            return row is not None and after_sequence <= int(row["max_sequence"])

    async def fail_unfinished_runs_after_restart(self) -> tuple[str, ...]:
        """将进程中断的非终态 Run 安全收口，禁止依据旧 Harness Session 原位续跑。"""

        unfinished = {
            RunStatus.ACCEPTED.value,
            RunStatus.RUNNING.value,
            RunStatus.SUSPENDED_OPERATION.value,
            RunStatus.SUSPENDED_CONFIRMATION.value,
            RunStatus.SUSPENDED_AUTHORIZATION.value,
        }
        async with self._lock:
            placeholders = ", ".join("?" for _ in unfinished)
            rows = self._connection.execute(
                f"SELECT run_id FROM harness_runs WHERE status IN ({placeholders}) ORDER BY accepted_at ASC",
                tuple(sorted(unfinished)),
            ).fetchall()
            run_ids = tuple(str(row["run_id"]) for row in rows)
            if not run_ids:
                return ()
            completed_at = _utc_now()
            for run_id in run_ids:
                self._connection.execute(
                    """
                    UPDATE harness_runs
                    SET status = ?, termination_reason = ?, completed_at = ?
                    WHERE run_id = ?
                    """,
                    (
                        RunStatus.FAILED.value,
                        TerminationReason.ENGINE_ERROR.value,
                        completed_at,
                        run_id,
                    ),
                )
                self._append_locked(
                    run_id,
                    "run.failed",
                    {"code": "harness_run_recovery_required"},
                )
            self._connection.commit()
            return run_ids

    def close(self) -> None:
        """关闭本进程拥有的 SQLite 连接。"""

        self._connection.close()

    def _append_locked(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> HarnessRunEvent:
        """在持锁事务内生成单调序列和稳定事件标识。"""

        import json

        row = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS max_sequence FROM harness_run_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        sequence = int(row["max_sequence"]) + 1
        event_id = "hevt_" + hashlib.sha256(
            f"{run_id}:{sequence}".encode(),
        ).hexdigest()[:32]
        occurred_at = _utc_now()
        self._connection.execute(
            """
            INSERT INTO harness_run_events (
                run_id, sequence, event_id, event_type, occurred_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                sequence,
                event_id,
                event_type,
                occurred_at,
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ),
        )
        return HarnessRunEvent(
            protocol_version="v1",
            run_id=run_id,
            event_id=event_id,
            sequence=sequence,
            type=event_type,
            occurred_at=occurred_at,
            payload=payload,
        )

    @staticmethod
    def _state(row: sqlite3.Row) -> HarnessRunState:
        """将 SQLite 行映射为稳定 DTO。"""

        reason = row["termination_reason"]
        return HarnessRunState(
            protocol_version="v1",
            run_id=row["run_id"],
            status=RunStatus(row["status"]),
            termination_reason=TerminationReason(reason) if reason else None,
            engine_id=row["engine_id"],
            engine_version=row["engine_version"],
            skill_catalog_digest=row["skill_catalog_digest"],
            accepted_at=row["accepted_at"],
            completed_at=row["completed_at"],
        )
