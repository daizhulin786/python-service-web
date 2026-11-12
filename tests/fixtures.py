"""Build a small synthetic superagent state database for tests and previews."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE ticket (id TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE thread (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE trigger (id TEXT PRIMARY KEY, value TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE outbox (id TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _ticket(
    ticket_id: str,
    title: str,
    *,
    source: str,
    created: datetime,
    updated: datetime,
    assignment: dict[str, Any] | None,
    worker: str,
    scope: str | None = None,
    ended: dict[str, Any] | None = None,
    url: str | None = None,
    undelivered: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    channel: dict[str, Any] = {"source": source, "thread": f"{source}:fixture"}
    if url:
        channel["address"] = {"url": url}
    ticket: dict[str, Any] = {
        "id": ticket_id,
        "title": title,
        "decisionId": f"decision-{ticket_id[:6]}",
        "mandate": {
            "strength": "explicit",
            "basis": {"source": source, "sender": "fixture"},
            "scope": scope or title,
        },
        "channels": [channel],
        "assignments": [assignment] if assignment else [],
        "inputs": [],
        "undelivered": undelivered or [],
        "createdAt": _iso(created),
        "updatedAt": _iso(updated),
        "execution": {
            "name": worker,
            "directory": f"C:\\fixture\\{worker}",
            "assignment": "",
        },
        "origin": {"triggerId": f"trigger-{ticket_id[:6]}", "triggerIds": []},
    }
    if ended:
        ticket["ended"] = ended
    return ticket


def build_demo_db(path: Path, now: datetime | None = None) -> Path:
    """Create the synthetic state database used by tests and screenshots."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    tickets: list[dict[str, Any]] = [
        _ticket(
            "a1b2c3d4e5f60718293a4b5c6d7e8f90",
            "为控制台添加深色模式切换",
            source="github",
            worker="dark-mode-toggle-a1b2c3d4",
            scope="为控制台添加深色模式切换，并记住用户选择",
            created=now - timedelta(minutes=9),
            updated=now - timedelta(minutes=2),
            url="https://github.com/acme/widgets/issues/12",
            undelivered=[{"id": "undelivered-progress"}],
            assignment={
                "id": "as-running",
                "state": "running",
                "createdAt": _iso(now - timedelta(minutes=9)),
                "startedAt": _iso(now - timedelta(minutes=8)),
                "progress": [
                    {
                        "state": "progress",
                        "summary": "已完成颜色变量梳理，正在改造主题切换组件并补键盘操作。",
                        "at": _iso(now - timedelta(minutes=2)),
                    }
                ],
            },
        ),
        _ticket(
            "b2c3d4e5f60718293a4b5c6d7e8f9012",
            "README 末尾追加版本发布说明",
            source="dingtalk",
            worker="readme-release-notes-b2c3d4e5",
            created=now - timedelta(minutes=55),
            updated=now - timedelta(minutes=20),
            assignment={
                "id": "as-review",
                "state": "ended",
                "createdAt": _iso(now - timedelta(minutes=55)),
                "startedAt": _iso(now - timedelta(minutes=54)),
                "endedAt": _iso(now - timedelta(minutes=20)),
                "report": {
                    "state": "ready",
                    "summary": (
                        "PR #42 is ready for review: "
                        "https://github.com/acme/widgets/pull/42\n\n"
                        "README 末尾新增了 v0.4 发布说明，改动共 3 行；"
                        "已自查链接和换行，工作区干净。"
                    ),
                    "at": _iso(now - timedelta(minutes=20)),
                },
            },
        ),
        _ticket(
            "c3d4e5f60718293a4b5c6d7e8f901234",
            "修复移动端表格横向溢出",
            source="github",
            worker="mobile-table-overflow-c3d4e5f6",
            created=now - timedelta(hours=2),
            updated=now - timedelta(minutes=48),
            assignment={
                "id": "as-blocked",
                "state": "ended",
                "createdAt": _iso(now - timedelta(hours=2)),
                "startedAt": _iso(now - timedelta(hours=2) + timedelta(minutes=1)),
                "endedAt": _iso(now - timedelta(minutes=48)),
                "report": {
                    "state": "blocked",
                    "summary": (
                        "缺少 375px 宽度下的设备截图，无法确认横向溢出是否由表格容器引起。"
                        "需要一张移动端截图或明确需要调整的断点后再继续。"
                    ),
                    "at": _iso(now - timedelta(minutes=48)),
                },
            },
        ),
        _ticket(
            "d4e5f60718293a4b5c6d7e8f90123456",
            "更新依赖并修复测试告警",
            source="github",
            worker="dependency-refresh-d4e5f607",
            created=now - timedelta(hours=3),
            updated=now - timedelta(minutes=31),
            ended={
                "at": _iso(now - timedelta(minutes=31)),
                "by": "executor",
                "outcome": "merged",
            },
            assignment={
                "id": "as-merged",
                "state": "ended",
                "createdAt": _iso(now - timedelta(hours=3)),
                "startedAt": _iso(now - timedelta(hours=3) + timedelta(minutes=1)),
                "endedAt": _iso(now - timedelta(minutes=34)),
                "report": {
                    "state": "ready",
                    "summary": "PR #41 已合并，依赖更新到最新补丁版本，全部测试通过。",
                    "at": _iso(now - timedelta(minutes=34)),
                },
            },
        ),
        _ticket(
            "e5f60718293a4b5c6d7e8f9012345678",
            "整理历史构建产物",
            source="dingtalk",
            worker="cleanup-build-artifacts-e5f60718",
            created=now - timedelta(hours=26),
            updated=now - timedelta(hours=25),
            ended={
                "at": _iso(now - timedelta(hours=25)),
                "by": "human",
                "outcome": "dropped",
            },
            assignment={
                "id": "as-dropped",
                "state": "ended",
                "createdAt": _iso(now - timedelta(hours=26)),
                "startedAt": _iso(now - timedelta(hours=26) + timedelta(minutes=1)),
                "endedAt": _iso(now - timedelta(hours=25)),
                "report": {
                    "state": "dropped",
                    "summary": "人工终止：构建产物仍在被归档任务使用，本次不再清理。",
                    "at": _iso(now - timedelta(hours=25)),
                },
            },
        ),
        _ticket(
            "f60718293a4b5c6d7e8f901234567890",
            "补充 API 限流说明",
            source="feishu",
            worker="api-rate-limit-docs-f6071829",
            created=now - timedelta(minutes=3),
            updated=now - timedelta(minutes=3),
            assignment={
                "id": "as-queued",
                "state": "pending",
                "createdAt": _iso(now - timedelta(minutes=3)),
            },
        ),
    ]

    threads = [
        {
            "key": "dingtalk:fixture",
            "ref": {"source": "dingtalk", "id": "fixture"},
            "subscriptions": ["b2c3d4e5f60718293a4b5c6d7e8f9012"],
            "pending": [{"id": "pending-1"}, {"id": "pending-2"}],
        }
    ]
    triggers = []
    for index, minutes in enumerate((5, 18, 42, 180, 190)):
        at = now - timedelta(minutes=minutes)
        triggers.append(
            {
                "id": f"trg-fixture-{index}",
                "source": "github" if index % 2 else "dingtalk",
                "threads": [{"source": "github", "id": "12"}],
                "human": True,
                "at": _iso(at),
                "text": f"fixture trigger {index}",
                "url": "https://github.com/acme/widgets/issues/12",
            }
        )

    outbox = [
        {"id": "outbox-pending", "kind": "reply", "status": "pending", "text": "pending reply"},
        {"id": "outbox-sent", "kind": "reply", "status": "sent", "text": "sent reply"},
    ]

    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA)
        for ticket in tickets:
            connection.execute(
                "INSERT INTO ticket (id, value) VALUES (?, ?)",
                (ticket["id"], json.dumps(ticket, ensure_ascii=False)),
            )
        for thread in threads:
            connection.execute(
                "INSERT INTO thread (key, value) VALUES (?, ?)",
                (thread["key"], json.dumps(thread, ensure_ascii=False)),
            )
        for trigger in triggers:
            connection.execute(
                "INSERT INTO trigger (id, value, at) VALUES (?, ?, ?)",
                (trigger["id"], json.dumps(trigger, ensure_ascii=False), trigger["at"]),
            )
        for item in outbox:
            connection.execute(
                "INSERT INTO outbox (id, value) VALUES (?, ?)",
                (item["id"], json.dumps(item, ensure_ascii=False)),
            )
        connection.commit()
    finally:
        connection.close()
    return path
