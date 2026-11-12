from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from superagent_status.store import load_status, resolve_state_dir
from tests.fixtures import build_demo_db

NOW = datetime(2026, 11, 12, 3, 30, tzinfo=timezone.utc)


class StatusStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)
        build_demo_db(self.state_dir / "orchestrator.sqlite", now=NOW)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_summary_counts(self) -> None:
        payload = load_status(self.state_dir, now=NOW)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"]["openTickets"], 4)
        self.assertEqual(payload["summary"]["runningAssignments"], 1)
        self.assertEqual(payload["summary"]["reviewTickets"], 1)
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
        self.assertEqual(tickets["b2c3d4e5f60718293a4b5c6d7e8f9012"]["state"], "review")
        self.assertEqual(tickets["c3d4e5f60718293a4b5c6d7e8f901234"]["state"], "blocked")
        self.assertEqual(tickets["d4e5f60718293a4b5c6d7e8f90123456"]["state"], "ended")
        self.assertEqual(tickets["f60718293a4b5c6d7e8f901234567890"]["state"], "queued")

        review = tickets["b2c3d4e5f60718293a4b5c6d7e8f9012"]
        self.assertEqual(review["pr"]["number"], 42)
        self.assertEqual(review["pr"]["url"], "https://github.com/acme/widgets/pull/42")
        self.assertIsNotNone(review["report"])
        self.assertEqual(review["report"]["state"], "ready")

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
