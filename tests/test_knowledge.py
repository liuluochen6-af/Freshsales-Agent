import tempfile
import unittest
from pathlib import Path

import app as agent_app


class KnowledgeIdentityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        agent_app.DB_PATH = Path(self.tmp.name) / "knowledge.db"
        agent_app.init_db()
        agent_app.app.config.update(TESTING=True)
        self.client = agent_app.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def ask(self, question: str):
        response = self.client.post(
            "/api/knowledge/answer",
            json={"question": question, "use_ai": False},
        )
        self.assertEqual(response.status_code, 200)
        return response.json

    def test_identity_questions_use_verified_business_identity(self):
        cases = {
            "请问你是": ("跨境云有限公司", "身份介绍-基础"),
            "你们是做什么的": ("榴莲供货", "身份介绍-公司"),
            "你们是哪家公司": ("跨境云有限公司", "身份介绍-公司"),
            "怎么称呼你": ("小刘", "身份介绍-称呼"),
        }
        for question, (expected_text, expected_source) in cases.items():
            with self.subTest(question=question):
                result = self.ask(question)
                self.assertIn(expected_text, result["answer"])
                self.assertTrue(result["knowledge_hit"])
                self.assertFalse(result["requires_human"])
                self.assertEqual(result["risk"], "低")
                self.assertIn(expected_source, result["sources"])
                self.assertEqual(result["decision_basis"], "身份信息")

    def test_unknown_inventory_fact_remains_high_risk(self):
        result = self.ask("今天到底还有多少现货库存")
        self.assertTrue(result["requires_human"])
        self.assertEqual(result["risk"], "高")
        self.assertIn("帮您核一下准确数量", result["answer"])
        self.assertNotIn("知识库", result["answer"])

    def test_contact_source_questions_use_confirmed_public_source_without_handoff(self):
        questions = [
            "你是怎么获得我联系方式的", "我的联系方式哪来的", "你怎么有我号码",
            "你从哪里找到我的", "谁给你的号码", "为什么加我", "我的电话来源是什么",
        ]
        for question in questions:
            with self.subTest(question=question):
                result = self.ask(question)
                self.assertIn("公开网站", result["answer"])
                self.assertIn("商户公开页面", result["answer"])
                self.assertFalse(result["requires_human"])
                self.assertEqual(result["risk"], "中")
                self.assertEqual(result["action"], "可自动建议")
                self.assertNotIn("朋友推荐", result["answer"])

    def test_source_denial_and_stop_signal_remain_fail_closed(self):
        denial = self.ask("我根本没在网站登记过，你说的来源不对")
        self.assertTrue(denial["requires_human"])
        self.assertEqual(denial["risk"], "高")
        self.assertIn("暂停联系", denial["answer"])
        stop = self.ask("把我的联系方式删掉，不要再联系")
        self.assertTrue(stop["requires_human"])
        self.assertIn("不再联系", stop["answer"])

    def test_console_assets_cannot_reuse_stale_cache(self):
        page = self.client.get("/")
        script = self.client.get("/static/app.js?v=20260719-provenance-training")
        self.assertIn("no-store", page.headers.get("Cache-Control", ""))
        self.assertIn("no-store", script.headers.get("Cache-Control", ""))
        self.assertIn(b"20260719-provenance-training", page.data)
        self.assertIn("回答方式：", script.get_data(as_text=True))
        page.close()
        script.close()


if __name__ == "__main__":
    unittest.main()
