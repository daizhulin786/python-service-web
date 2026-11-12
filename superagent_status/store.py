"""Read superagent's SQLite state and turn it into a small view model.

The state database belongs to the running superagent.  It is opened
strictly read-only so the dashboard can never interfere with a worker or
the orchestrator.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

DEFAULT_STATE_DIR = Path.home() / ".dsh" / "state" / "superagent"
DEFAULT_STATE_DIR_ENV = "SUPERAGENT_STATE_DIR"

MAX_REPORT_CHARS = 6000
MAX_EVENTS = 40

PR_URL_RE = re.compile(r"https?://github\.com/[^/\s]+/[^/\s]+/pull/(\d+)")

TERMINAL_REPORT_STATES = {"ready", "blocked", "failed", "dropped"}
ACTIVE_REPORT_STATES = {"progress", "running"}


def resolve_state_dir(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Return the state directory, honouring the CLI, env var and default."""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get(DEFAULT_STATE_DIR_ENV)
    if env:
        return Path(env).expanduser()
    return DEFAULT_STATE_DIR


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_now(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _truncate(text: Any, limit: int = MAX_REPORT_CHARS) -> str:
    if not isinstance(text, str):
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n…（内容过长，已截断）"


def _elapsed_seconds(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds()))


def _source_of(ticket: dict[str, Any]) -> str:
    for channel in _as_list(ticket.get("channels")):
        source = _as_dict(channel).get("source")
        if isinstance(source, str) and source:
            return source
    basis = _as_dict(_as_dict(ticket.get("mandate")).get("basis"))
    source = basis.get("source")
    return source if isinstance(source, str) and source else "unknown"


def _assignment_reports(assignment: dict[str, Any]) -> list[dict[str, Any]]:
    """Return progress entries followed by the final report, if any."""
    reports: list[dict[str, Any]] = []
    for entry in _as_list(assignment.get("progress")):
        item = _as_dict(entry)
        summary = item.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            continue
        reports.append(
            {
                "state": item.get("state") if isinstance(item.get("state"), str) else "progress",
                "summary": summary.strip(),
                "at": item.get("at") if isinstance(item.get("at"), str) else None,
            }
        )
    report = _as_dict(assignment.get("report"))
    if isinstance(report.get("summary"), str) and report["summary"].strip():
        reports.append(
            {
                "state": report.get("state") if isinstance(report.get("state"), str) else "ready",
                "summary": report["summary"].strip(),
                "at": report.get("at") if isinstance(report.get("at"), str) else None,
            }
        )
    return reports


def _latest_assignment(ticket: dict[str, Any]) -> dict[str, Any] | None:
    assignments = [_as_dict(item) for item in _as_list(ticket.get("assignments"))]
    assignments = [item for item in assignments if item.get("id")]
    if not assignments:
        return None
    assignments.sort(key=lambda item: _parse_dt(item.get("createdAt")) or datetime.min.replace(tzinfo=timezone.utc))
    return assignments[-1]


def _ticket_state(ticket: dict[str, Any], assignment: dict[str, Any] | None) -> str:
    if _as_dict(ticket.get("ended")).get("at"):
        return "ended"
    if assignment is None:
        return "waiting"
    state = assignment.get("state")
    if state == "running":
        return "working"
    if state == "pending":
        return "queued"
    if state in ("blocked", "failed", "dropped"):
        return state
    if state == "ended":
        report_state = _as_dict(assignment.get("report")).get("state")
        if report_state == "ready":
            return "review"
        if report_state in TERMINAL_REPORT_STATES:
            return report_state
        return "idle"
    if isinstance(state, str) and state:
        return state
    return "waiting"


def _extract_pr(ticket: dict[str, Any], reports: list[dict[str, Any]]) -> dict[str, Any] | None:
    texts = [report.get("summary", "") for report in reports]
    for input_item in _as_list(ticket.get("inputs")):
        text = _as_dict(input_item).get("text")
        if isinstance(text, str):
            texts.append(text)
    for text in texts:
        match = PR_URL_RE.search(text)
        if match:
            return {"number": int(match.group(1)), "url": match.group(0)}
    return None


def _channel_url(ticket: dict[str, Any], trigger_urls: dict[str, str]) -> str | None:
    for channel in _as_list(ticket.get("channels")):
        address = _as_dict(_as_dict(channel).get("address"))
        url = address.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url
    origin = _as_dict(ticket.get("origin"))
    trigger_id = origin.get("triggerId")
    if isinstance(trigger_id, str) and trigger_id in trigger_urls:
        return trigger_urls[trigger_id]
    return None


def _safe_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _build_ticket(
    ticket: dict[str, Any],
    trigger_urls: dict[str, str],
    now: datetime,
) -> dict[str, Any] | None:
    ticket_id = ticket.get("id")
    if not isinstance(ticket_id, str) or not ticket_id:
        return None

    assignment = _latest_assignment(ticket)
    reports = _assignment_reports(assignment) if assignment else []
    report = reports[-1] if reports else None
    report_history = [
        {"state": item["state"], "at": item["at"]} for item in reports if item.get("state")
    ]
    state = _ticket_state(ticket, assignment)
    source = _source_of(ticket)
    created = _parse_dt(ticket.get("createdAt"))
    ended = _as_dict(ticket.get("ended"))
    ended_at = _parse_dt(ended.get("at"))

    started_at = None
    if assignment:
        started_at = _parse_dt(assignment.get("startedAt"))
    if started_at is None:
        started_at = created

    if state == "ended":
        elapsed = _elapsed_seconds(created, ended_at)
    elif state in ("review", "blocked", "failed", "dropped") and assignment is not None:
        elapsed = _elapsed_seconds(started_at, _parse_dt(assignment.get("endedAt")))
    else:
        elapsed = _elapsed_seconds(started_at, now)

    activities: list[dict[str, Any]] = []
    if created:
        activities.append({"at": ticket.get("createdAt"), "kind": "ticket.created", "state": "waiting"})
    for item in _as_list(ticket.get("assignments")):
        item = _as_dict(item)
        if item.get("startedAt"):
            activities.append(
                {"at": item.get("startedAt"), "kind": "assignment.started", "state": "working"}
            )
        for entry in _assignment_reports(item):
            kind = "assignment.progress" if entry.get("state") in ACTIVE_REPORT_STATES else "assignment.report"
            activities.append({"at": entry.get("at"), "kind": kind, "state": entry.get("state")})
        if item.get("endedAt") and item.get("state") == "ended":
            activities.append(
                {"at": item.get("endedAt"), "kind": "assignment.ended", "state": "ended"}
            )
    if ended_at:
        activities.append(
            {
                "at": ended.get("at"),
                "kind": "ticket.ended",
                "state": "ended",
                "outcome": ended.get("outcome"),
            }
        )
    activities.sort(key=lambda item: _parse_dt(item.get("at")) or datetime.min.replace(tzinfo=timezone.utc))

    pr = _extract_pr(ticket, reports)
    execution = _as_dict(ticket.get("execution"))
    mandate = _as_dict(ticket.get("mandate"))

    warning_count = len(_as_list(ticket.get("undelivered")))

    return {
        "id": ticket_id,
        "title": _safe_text(ticket.get("title")) or "未命名工单",
        "scope": _safe_text(mandate.get("scope")) or _safe_text(ticket.get("title")),
        "state": state,
        "source": source,
        "sourceUrl": _channel_url(ticket, trigger_urls),
        "worker": _safe_text(execution.get("name")),
        "directory": _safe_text(execution.get("directory")),
        "createdAt": ticket.get("createdAt"),
        "updatedAt": ticket.get("updatedAt"),
        "startedAt": assignment.get("startedAt") if assignment else None,
        "endedAt": ended.get("at"),
        "elapsedSeconds": elapsed,
        "assignment": {
            "id": assignment.get("id") if assignment else None,
            "state": assignment.get("state") if assignment else None,
            "startedAt": assignment.get("startedAt") if assignment else None,
            "endedAt": assignment.get("endedAt") if assignment else None,
            "progressCount": sum(
                1 for item in reports if item.get("state") in ACTIVE_REPORT_STATES
            ),
        },
        "report": (
            {
                "state": report["state"],
                "summary": _truncate(report["summary"]),
                "at": report.get("at"),
            }
            if report
            else None
        ),
        "reportHistory": report_history,
        "pr": pr,
        "ended": ended or None,
        "undelivered": warning_count,
        "activities": activities,
    }


def _empty_status(state_dir: Path, now: datetime) -> dict[str, Any]:
    db_path = state_dir / "orchestrator.sqlite"
    return {
        "generatedAt": _iso_now(now),
        "ok": False,
        "error": "状态数据库尚不可用。",
        "system": {
            "stateDir": str(state_dir),
            "dbPath": str(db_path),
            "dbExists": db_path.exists(),
            "readable": False,
            "dbUpdatedAt": None,
            "dbSizeBytes": _file_size(db_path),
            "walSizeBytes": _file_size(state_dir / "orchestrator.sqlite-wal"),
            "totalTickets": 0,
            "openTickets": 0,
            "runningAssignments": 0,
            "reviewTickets": 0,
            "pendingMessages": 0,
            "pendingDeliveries": 0,
            "triggersLastHour": 0,
            "endedToday": 0,
            "lastTriggerAt": None,
            "sources": [],
        },
        "summary": {
            "openTickets": 0,
            "runningAssignments": 0,
            "reviewTickets": 0,
            "pendingMessages": 0,
            "triggersLastHour": 0,
        },
        "tickets": [],
        "events": [],
        "recentTriggers": [],
    }


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _connect(db_path: Path) -> sqlite3.Connection:
    encoded = quote(db_path.as_posix(), safe="/:")
    uri = f"file:{encoded}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=2.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 2000")
    connection.execute("PRAGMA query_only = ON")
    return connection


def _read_rows(connection: sqlite3.Connection, table: str) -> list[Any]:
    try:
        rows = connection.execute(f"SELECT value FROM {table}").fetchall()
    except sqlite3.DatabaseError:
        return []
    values = []
    for row in rows:
        parsed = _loads(row["value"])
        if isinstance(parsed, dict):
            values.append(parsed)
    return values


def load_status(
    state_dir: str | os.PathLike[str] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the dashboard payload from superagent's state directory."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    directory = Path(state_dir) if state_dir is not None else resolve_state_dir()
    db_path = directory / "orchestrator.sqlite"
    payload = _empty_status(directory, now)

    if not db_path.exists():
        payload["error"] = f"没有找到状态数据库：{db_path}"
        return payload

    try:
        connection = _connect(db_path)
    except sqlite3.Error as exc:
        payload["error"] = f"状态数据库无法读取：{exc}"
        return payload

    try:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "ticket" not in tables:
            payload["error"] = "状态数据库中没有找到 ticket 表，可能不是 superagent 状态库。"
            return payload
        tickets_raw = _read_rows(connection, "ticket")
        threads_raw = _read_rows(connection, "thread") if "thread" in tables else []
        triggers_raw = _read_rows(connection, "trigger") if "trigger" in tables else []
        outbox_raw = _read_rows(connection, "outbox") if "outbox" in tables else []
    except sqlite3.DatabaseError as exc:
        payload["error"] = f"读取状态数据库失败：{exc}"
        return payload
    finally:
        connection.close()

    payload["ok"] = True
    payload.pop("error", None)

    trigger_urls: dict[str, str] = {}
    trigger_times: list[datetime] = []
    for trigger in triggers_raw:
        trigger_id = trigger.get("id")
        url = trigger.get("url")
        if isinstance(trigger_id, str) and isinstance(url, str):
            trigger_urls[trigger_id] = url
        parsed = _parse_dt(trigger.get("at"))
        if parsed:
            trigger_times.append(parsed)

    tickets: list[dict[str, Any]] = []
    for ticket in tickets_raw:
        built = _build_ticket(ticket, trigger_urls, now)
        if built is not None:
            tickets.append(built)

    tickets.sort(
        key=lambda item: (
            item["state"] == "ended",
            -(
                _parse_dt(item.get("updatedAt")) or datetime.min.replace(tzinfo=timezone.utc)
            ).timestamp(),
        )
    )

    open_tickets = [item for item in tickets if item["state"] != "ended"]
    running = [item for item in tickets if item["state"] == "working"]
    review = [item for item in tickets if item["state"] == "review"]

    pending_messages = 0
    for thread in threads_raw:
        pending_messages += len(_as_list(thread.get("pending")))
    for ticket in tickets_raw:
        pending_messages += len(_as_list(ticket.get("undelivered")))

    pending_deliveries = 0
    for item in outbox_raw:
        if item.get("status") == "pending":
            pending_deliveries += 1

    one_hour_ago = now - timedelta(hours=1)
    triggers_last_hour = sum(1 for at in trigger_times if at >= one_hour_ago)
    last_trigger_at = max(trigger_times).isoformat().replace("+00:00", "Z") if trigger_times else None

    today = now.astimezone().date()
    ended_today = 0
    for item in tickets:
        ended_at = _parse_dt(_as_dict(item.get("ended")).get("at"))
        if ended_at and ended_at.astimezone().date() == today:
            ended_today += 1

    source_counts: dict[str, int] = {}
    for item in tickets:
        source_counts[item["source"]] = source_counts.get(item["source"], 0) + 1
    sources = [
        {"source": source, "count": count}
        for source, count in sorted(source_counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]

    events: list[dict[str, Any]] = []
    for ticket in tickets:
        for activity in ticket["activities"]:
            events.append(
                {
                    "at": activity.get("at"),
                    "kind": activity.get("kind"),
                    "state": activity.get("state"),
                    "outcome": activity.get("outcome"),
                    "ticketId": ticket["id"],
                    "title": ticket["title"],
                    "source": ticket["source"],
                }
            )
    events.sort(key=lambda item: _parse_dt(item.get("at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    recent_triggers: list[dict[str, Any]] = []
    for trigger in triggers_raw:
        at = _parse_dt(trigger.get("at"))
        if at is None:
            continue
        text = trigger.get("text")
        preview = " ".join(text.split()) if isinstance(text, str) else ""
        if len(preview) > 180:
            preview = preview[:180].rstrip() + "…"
        recent_triggers.append(
            {
                "id": trigger.get("id"),
                "source": trigger.get("source") or "unknown",
                "at": trigger.get("at"),
                "text": preview,
                "human": bool(trigger.get("human")),
                "url": trigger.get("url") if isinstance(trigger.get("url"), str) else None,
            }
        )
    recent_triggers.sort(
        key=lambda item: _parse_dt(item.get("at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    mtimes = [
        value
        for value in (
            _file_mtime(db_path),
            _file_mtime(directory / "orchestrator.sqlite-wal"),
        )
        if value
    ]
    db_mtime = max(mtimes) if mtimes else None

    system = payload["system"]
    system.update(
        {
            "dbPath": str(db_path),
            "dbExists": True,
            "readable": True,
            "dbUpdatedAt": db_mtime,
            "dbSizeBytes": _file_size(db_path),
            "walSizeBytes": _file_size(directory / "orchestrator.sqlite-wal"),
            "totalTickets": len(tickets),
            "openTickets": len(open_tickets),
            "runningAssignments": len(running),
            "reviewTickets": len(review),
            "pendingMessages": pending_messages,
            "pendingDeliveries": pending_deliveries,
            "triggersLastHour": triggers_last_hour,
            "endedToday": ended_today,
            "lastTriggerAt": last_trigger_at,
            "sources": sources,
        }
    )
    payload["summary"] = {
        "openTickets": len(open_tickets),
        "runningAssignments": len(running),
        "reviewTickets": len(review),
        "pendingMessages": pending_messages,
        "triggersLastHour": triggers_last_hour,
    }
    payload["tickets"] = tickets
    payload["events"] = events[:MAX_EVENTS]
    payload["recentTriggers"] = recent_triggers[:6]
    return payload


def _file_mtime(path: Path) -> str | None:
    try:
        timestamp = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")
