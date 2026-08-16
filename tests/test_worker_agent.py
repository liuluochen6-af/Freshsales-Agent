import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rpa.worker_agent import (
    DEFAULT_MANAGED_CONVERSATIONS,
    DEFAULT_SCAN_BATCH_SIZE,
    load_or_create_config,
    rotating_session_batch,
)


class WorkerAutomaticConfigurationTest(unittest.TestCase):
    def test_missing_config_is_created_with_stable_device_defaults(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {
                "AGENT_SERVER_URL": "http://10.0.0.8:8015",
                "AGENT_WECHAT_ACCOUNT": "销售微信A",
                "AGENT_BOOTSTRAP_TOKEN": "one-time-token",
            },
            clear=False,
        ), mock.patch("rpa.worker_agent.platform.node", return_value="SALES-PC"), mock.patch(
            "rpa.worker_agent.getpass.getuser", return_value="operator"
        ):
            path = Path(tmp) / "worker.json"
            first = load_or_create_config(path)
            second = load_or_create_config(path)

        self.assertTrue(first["auto_configured"])
        self.assertEqual(first["node_id"], second["node_id"])
        self.assertTrue(first["node_id"].startswith("wechat-SALES-PC-"))
        self.assertEqual(first["server_url"], "http://10.0.0.8:8015")
        self.assertEqual(first["account_ref"], "销售微信A")
        self.assertEqual(first["max_active_conversations"], DEFAULT_MANAGED_CONVERSATIONS)
        self.assertEqual(first["scan_batch_size"], DEFAULT_SCAN_BATCH_SIZE)

    def test_round_robin_batch_reaches_all_managed_sessions(self):
        sessions = [{"conversation_id": number} for number in range(125)]
        cursor = 0
        visited = []
        for _ in range(3):
            batch, cursor = rotating_session_batch(sessions, cursor, 50)
            visited.extend(item["conversation_id"] for item in batch)

        self.assertEqual(set(visited), set(range(125)))
        self.assertEqual(cursor, 25)


if __name__ == "__main__":
    unittest.main()
