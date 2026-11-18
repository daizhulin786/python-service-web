from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from superagent_status.store import load_status, resolve_state_dir
from tests.fixtures import build_demo_db

NOW = datetime(2026, 11, 12, 3, 30, tzinfo=timezone.utc)
PR_URL = "https://github.com/acme/widgets/pull/99"


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class StatusStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)
        build_demo_db(self.state_dir / "orchestrator.sqlite", now=NOW)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _insert_ticket(self, ticket: dict) -> None:
        connection = sqlite3.connect(self.state_dir / "orchestrator.sqlite")
        try:
            connection.execute(
                "INSERT INTO ticket (id, value) VALUES (?, ?)",
                (ticket["id"], json.dumps(ticket, ensure_ascii=False)),
            )
            connection.commit()
        finally:
            connection.close()

    def _insert_pr_thread(self, number: int = 99) -> None:
        value = {
            "key": f"github:{number}",
            "ref": {"source": "github", "id": str(number)},
            "subscriptions": [],
            "pending": [],
            "meta": {
                "number": number,
                "isPr": True,
                "url": PR_URL,
                "title": "等待审核的 PR",
            },
        }
        connection = sqlite3.connect(self.state_dir / "orchestrator.sqlite")
        try:
            connection.execute(
                "INSERT INTO thread (key, value) VALUES (?, ?)",
                (value["key"], json.dumps(value, ensure_ascii=False)),
            )
            connection.commit()
        finally:
            connection.close()

    def _pr_review_ticket(
        self,
        ticket_id: str,
        *,
        delivered_after_report: bool = False,
        closed: bool = False,
    ) -> dict:
        started = NOW - timedelta(minutes=10)
        reported = NOW - timedelta(minutes=4)
        assignment: dict = {
            "id": f"as-{ticket_id[:8]}",
            "state": "running",
            "createdAt": _iso(started - timedelta(minutes=1)),
            "startedAt": _iso(started),
            "idleAt": _iso(reported + timedelta(seconds=3)),
            "report": {
                "state": "ready",
                "summary": "Ready for review.",
                "at": _iso(reported),
            },
        }
        if delivered_after_report:
            assignment["deliveredAt"] = _iso(reported + timedelta(minutes=1))
        else:
            assignment["deliveredAt"] = _iso(started)
        follow: dict = {"thread": "github:99", "own": True}
        if closed:
            follow["closed"] = {"at": _iso(NOW), "outcome": "merged"}
        return {
            "id": ticket_id,
            "title": "等待 PR 审核的工单",
            "mandate": {"strength": "explicit", "scope": "等待 PR 审核的工单"},
            "channels": [{"source": "github", "thread": "github:99"}],
            "assignments": [assignment],
            "inputs": [],
            "follows": [follow],
            "createdAt": _iso(started - timedelta(minutes=1)),
            "updatedAt": _iso(reported),
            "execution": {"name": "pr-review-worker", "directory": "C:\\fixture\\pr-review-worker"},
        }

    def test_summary_counts(self) -> None:
        payload = load_status(self.state_dir, now=NOW)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"]["openTickets"], 4)
        self.assertEqual(payload["summary"]["runningAssignments"], 1)
        self.assertEqual(payload["summary"]["reviewTickets"], 1)
        self.assertEqual(payload["summary"]["prReviewTickets"], 1)
        self.assertEqual(payload["summary"]["pendingMessages"], 3)
        self.assertEqual(payload["system"]["pendingDeliveries"], 1)
        self.assertEqual(payload["system"]["triggersLastHour"], 5)
        self.assertEqual(payload["system"]["totalTickets"], 6)
        self.assertEqual(payload["system"]["endedToday"], 1)
        self.assertEqual(len(payload["recentTriggers"]), 6)
        self.assertEqual(payload["recentTriggers"][0]["text"], "fixture trigger 0")

    def test_dingtalk_group_messages(self) -> None:
        payload = load_status(self.state_dir, now=NOW)
        dingtalk = payload["dingtalk"]
        self.assertEqual(dingtalk["groupCount"], 2)
        self.assertEqual(dingtalk["messageCount"], 4)
        self.assertTrue(dingtalk["lastMessageAt"].startswith("2026-11-12T03:27"))

        groups = {group["id"]: group for group in dingtalk["groups"]}
        self.assertEqual(set(groups), {"cidFixtureGroupA==", "cidFixtureGroupB=="})
        self.assertNotIn("cidFixtureDirectd1", groups)

        main = groups["cidFixtureGroupA=="]
        self.assertEqual(main["title"], "测试自用")
        self.assertEqual(main["messageCount"], 3)
        self.assertEqual(len(main["messages"]), 3)
        self.assertEqual(main["messages"][0]["text"], "README 的标题层级需要统一一下，顺便确认锚点是否可用。")
        self.assertEqual(main["messages"][0]["sender"], "代竹林")
        self.assertEqual(main["messages"][1]["sender"], "林小满")

        self.assertEqual(groups["cidFixtureGroupB=="]["title"], "产品讨论")
        self.assertEqual(groups["cidFixtureGroupB=="]["messages"][0]["sender"], "周航")

    def test_dingtalk_messages_exclude_direct_and_other_sources(self) -> None:
        payload = load_status(self.state_dir, now=NOW)
        texts = [
            message["text"]
            for group in payload["dingtalk"]["groups"]
            for message in group["messages"]
        ]
        self.assertNotIn("私聊消息不应出现在群消息面板。", texts)
        self.assertNotIn("fixture trigger 0", texts)

    def test_group_detection_falls_back_to_channel_address(self) -> None:
        connection = sqlite3.connect(self.state_dir / "orchestrator.sqlite")
        try:
            for trigger_id, conversation_id, direct in (
                ("trg-address-group", "cidAddressOnly==", "false"),
                ("trg-address-direct", "cidAddressDirect==", "true"),
                ("trg-address-unknown", "cidAddressUnknown==", None),
            ):
                address = {"conversationId": conversation_id}
                if direct is not None:
                    address["direct"] = direct
                value = {
                    "id": trigger_id,
                    "source": "dingtalk",
                    "at": "2026-11-12T03:29:00Z",
                    "text": f"address fallback {trigger_id}",
                    "channel": {
                        "source": "dingtalk",
                        "thread": f"dingtalk:{conversation_id}:msg-{trigger_id}",
                        "address": address,
                    },
                }
                connection.execute(
                    "INSERT INTO trigger (id, value, at) VALUES (?, ?, ?)",
                    (trigger_id, json.dumps(value, ensure_ascii=False), value["at"]),
                )
            connection.commit()
        finally:
            connection.close()

        payload = load_status(self.state_dir, now=NOW)
        groups = {group["id"]: group for group in payload["dingtalk"]["groups"]}
        self.assertIn("cidAddressOnly==", groups)
        self.assertNotIn("cidAddressDirect==", groups)
        self.assertNotIn("cidAddressUnknown==", groups)

    def test_ticket_states_and_pr(self) -> None:
        payload = load_status(self.state_dir, now=NOW)
        tickets = {ticket["id"]: ticket for ticket in payload["tickets"]}
        self.assertEqual(tickets["a1b2c3d4e5f60718293a4b5c6d7e8f90"]["state"], "working")
        self.assertEqual(
            tickets["b2c3d4e5f60718293a4b5c6d7e8f9012"]["state"], "pr_review"
        )
        self.assertEqual(tickets["c3d4e5f60718293a4b5c6d7e8f901234"]["state"], "blocked")
        self.assertEqual(tickets["d4e5f60718293a4b5c6d7e8f90123456"]["state"], "ended")
        self.assertEqual(tickets["f60718293a4b5c6d7e8f901234567890"]["state"], "queued")

        review = tickets["b2c3d4e5f60718293a4b5c6d7e8f9012"]
        self.assertEqual(review["pr"]["number"], 42)
        self.assertEqual(review["pr"]["url"], "https://github.com/acme/widgets/pull/42")
        self.assertIsNotNone(review["report"])
        self.assertEqual(review["report"]["state"], "ready")

    def test_running_ready_pr_is_awaiting_review(self) -> None:
        ticket_id = "aa11bb22cc33dd44ee55ff6677889900"
        self._insert_ticket(self._pr_review_ticket(ticket_id))
        self._insert_pr_thread()

        payload = load_status(self.state_dir, now=NOW)
        tickets = {ticket["id"]: ticket for ticket in payload["tickets"]}
        ticket = tickets[ticket_id]
        self.assertEqual(ticket["state"], "pr_review")
        self.assertEqual(ticket["pr"]["number"], 99)
        self.assertEqual(ticket["pr"]["url"], PR_URL)
        self.assertEqual(ticket["elapsedSeconds"], 600)
        self.assertEqual(payload["summary"]["prReviewTickets"], 2)
        self.assertEqual(payload["summary"]["reviewTickets"], 2)
        self.assertEqual(payload["summary"]["runningAssignments"], 1)

    def test_new_input_after_ready_keeps_working(self) -> None:
        ticket_id = "bb22cc33dd44ee55ff66778899001122"
        self._insert_ticket(self._pr_review_ticket(ticket_id, delivered_after_report=True))
        self._insert_pr_thread()

        payload = load_status(self.state_dir, now=NOW)
        tickets = {ticket["id"]: ticket for ticket in payload["tickets"]}
        self.assertEqual(tickets[ticket_id]["state"], "working")
        self.assertEqual(payload["summary"]["prReviewTickets"], 1)
        self.assertEqual(payload["summary"]["runningAssignments"], 2)

    def test_ready_without_pr_keeps_working(self) -> None:
        ticket_id = "cc33dd44ee55ff6677889900112233"
        ticket = self._pr_review_ticket(ticket_id)
        ticket["follows"] = []
        self._insert_ticket(ticket)

        payload = load_status(self.state_dir, now=NOW)
        tickets = {ticket["id"]: ticket for ticket in payload["tickets"]}
        self.assertEqual(tickets[ticket_id]["state"], "working")
        self.assertIsNone(tickets[ticket_id]["pr"])
        self.assertEqual(payload["summary"]["prReviewTickets"], 1)

    def test_closed_pr_is_not_awaiting_review(self) -> None:
        ticket_id = "dd44ee55ff66778899001122334455"
        ticket = self._pr_review_ticket(ticket_id, closed=True)
        ticket["assignments"][0]["state"] = "ended"
        ticket["assignments"][0]["endedAt"] = _iso(NOW)
        ticket["assignments"][0]["report"]["summary"] = f"PR merged: {PR_URL}"
        self._insert_ticket(ticket)
        self._insert_pr_thread()

        payload = load_status(self.state_dir, now=NOW)
        tickets = {ticket["id"]: ticket for ticket in payload["tickets"]}
        self.assertEqual(tickets[ticket_id]["state"], "review")
        self.assertEqual(tickets[ticket_id]["pr"]["number"], 99)
        self.assertEqual(payload["summary"]["prReviewTickets"], 1)

    def test_active_work_first(self) -> None:
        payload = load_status(self.state_dir, now=NOW)
        self.assertEqual(payload["tickets"][0]["state"], "working")
        self.assertEqual(payload["tickets"][-1]["state"], "ended")

    def test_missing_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = load_status(directory, now=NOW)
        self.assertFalse(payload["ok"])
        self.assertIn("没有找到状态数据库", payload["error"])
        self.assertEqual(payload["tickets"], [])
        self.assertEqual(payload["summary"]["openTickets"], 0)

    def test_database_without_ticket_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orchestrator.sqlite"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE unrelated (id TEXT)")
            connection.commit()
            connection.close()
            payload = load_status(directory, now=NOW)
        self.assertFalse(payload["ok"])
        self.assertIn("ticket 表", payload["error"])

    def test_invalid_ticket_json_is_ignored(self) -> None:
        connection = sqlite3.connect(self.state_dir / "orchestrator.sqlite")
        connection.execute("INSERT INTO ticket (id, value) VALUES (?, ?)", ("broken", "{not json"))
        connection.commit()
        connection.close()

        payload = load_status(self.state_dir, now=NOW)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["system"]["totalTickets"], 6)
        self.assertNotIn("broken", {ticket["id"] for ticket in payload["tickets"]})

    def test_resolve_state_dir_environment(self) -> None:
        import os

        old = os.environ.get("SUPERAGENT_STATE_DIR")
        try:
            os.environ["SUPERAGENT_STATE_DIR"] = str(self.state_dir)
            self.assertEqual(resolve_state_dir(), self.state_dir)
            self.assertEqual(resolve_state_dir("/tmp/explicit"), Path("/tmp/explicit"))
        finally:
            if old is None:
                os.environ.pop("SUPERAGENT_STATE_DIR", None)
            else:
                os.environ["SUPERAGENT_STATE_DIR"] = old

    def test_state_directory_with_spaces_and_unicode(self) -> None:
        with tempfile.TemporaryDirectory(prefix="状态 页 ") as directory:
            build_demo_db(Path(directory) / "orchestrator.sqlite", now=NOW)
            payload = load_status(directory, now=NOW)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["system"]["totalTickets"], 6)

    def test_payload_is_json_serialisable(self) -> None:
        payload = load_status(self.state_dir, now=NOW)
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertIn("深色模式", encoded)
        self.assertGreater(len(encoded), 1000)


if __name__ == "__main__":
    unittest.main()
