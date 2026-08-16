import tempfile
import unittest
from pathlib import Path

import app as agent_app
from dialogue_training_corpus import (
    GLOBAL_FORBIDDEN_CLAIMS,
    MULTI_TURN_FLOWS,
    SINGLE_TURN_GROUPS,
    expected_multi_turn_count,
    expected_single_turn_count,
)


class FullDialogueCorpusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        agent_app.DB_PATH = Path(self.tmp.name) / "full-dialogue-corpus.db"
        agent_app.init_db()
        agent_app.app.config.update(TESTING=True)
        self.client = agent_app.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def ask(self, question):
        response = self.client.post("/api/knowledge/answer", json={"question": question, "use_ai": False})
        self.assertEqual(response.status_code, 200)
        return response.json

    def assert_truthful(self, answer):
        self.assertTrue(answer.strip())
        for forbidden in GLOBAL_FORBIDDEN_CLAIMS:
            self.assertNotIn(forbidden, answer)

    def test_all_single_turn_language_variants(self):
        self.assertGreaterEqual(expected_single_turn_count(), 140)
        for group in SINGLE_TURN_GROUPS:
            for prompt in group["prompts"]:
                with self.subTest(scene=group["scene"], prompt=prompt):
                    result = self.ask(prompt)
                    self.assert_truthful(result["answer"])
                    for text in group.get("contains_all", []):
                        self.assertIn(text, result["answer"])
                    expected_any = group.get("contains_any", [])
                    if expected_any:
                        self.assertTrue(
                            any(text in result["answer"] for text in expected_any),
                            f"answer did not match scene {group['scene']}: {result['answer']}",
                        )
                    if "requires_human" in group:
                        self.assertEqual(result["requires_human"], group["requires_human"])

    def test_multi_turn_customer_journeys(self):
        self.assertGreaterEqual(expected_multi_turn_count(), 50)
        for flow_index, flow in enumerate(MULTI_TURN_FLOWS, start=1):
            with agent_app.db() as conn:
                lead = conn.execute(
                    """INSERT INTO leads(store_name,phone,source,source_basis,created_at,updated_at)
                       VALUES(?,?,?,?,?,?)""",
                    (f"场景门店{flow_index}", f"1390000{flow_index:04d}", agent_app.DEFAULT_CONTACT_SOURCE,
                     agent_app.DEFAULT_CONTACT_SOURCE_BASIS, agent_app.now(), agent_app.now()),
                )
                conv = conn.execute(
                    "INSERT INTO conversations(lead_id,updated_at) VALUES(?,?)",
                    (lead.lastrowid, agent_app.now()),
                )
                conversation_id = int(conv.lastrowid)

            for turn_index, (customer_text, expected_any) in enumerate(flow["turns"], start=1):
                with self.subTest(flow=flow["name"], turn=turn_index, customer=customer_text):
                    sent = self.client.post(
                        f"/api/conversations/{conversation_id}/messages",
                        json={"sender": "customer", "content": customer_text},
                    )
                    self.assertEqual(sent.status_code, 200)
                    response = self.client.post(f"/api/conversations/{conversation_id}/suggest")
                    self.assertEqual(response.status_code, 200)
                    suggestion = response.json["suggestion"]
                    self.assert_truthful(suggestion)
                    self.assertTrue(
                        any(text in suggestion for text in expected_any),
                        f"flow {flow['name']} turn {turn_index}: {suggestion}",
                    )

    def test_direct_automation_question_never_claims_human_identity(self):
        for prompt in ("你是真人吗", "你是机器人吗", "这是AI回复吗", "是不是自动回复"):
            result = self.ask(prompt)
            self.assertFalse(result["requires_human"])
            self.assertEqual(result["risk"], "低")
            self.assertIn("销售人员和服务系统共同维护", result["answer"])
            self.assert_truthful(result["answer"])

    def test_stop_request_is_written_to_lead_state(self):
        with agent_app.db() as conn:
            lead = conn.execute(
                """INSERT INTO leads(store_name,phone,source,source_basis,created_at,updated_at)
                   VALUES(?,?,?,?,?,?)""",
                ("停止联系测试门店", "13800008888", agent_app.DEFAULT_CONTACT_SOURCE,
                 agent_app.DEFAULT_CONTACT_SOURCE_BASIS, agent_app.now(), agent_app.now()),
            )
            conv = conn.execute(
                "INSERT INTO conversations(lead_id,updated_at) VALUES(?,?)",
                (lead.lastrowid, agent_app.now()),
            )
            lead_id = int(lead.lastrowid)
            conversation_id = int(conv.lastrowid)

        response = self.client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"sender": "customer", "content": "不要再联系我，删除我的信息"},
        )
        self.assertEqual(response.status_code, 200)
        with agent_app.db() as conn:
            saved = conn.execute("SELECT stop_marketing,status FROM leads WHERE id=?", (lead_id,)).fetchone()
        self.assertEqual(saved["stop_marketing"], 1)
        self.assertEqual(saved["status"], "停止联系")


if __name__ == "__main__":
    unittest.main()
