import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from intelligence.echomind_client import EchoMindClient
from intelligence.orchestrator import SalesAgentOrchestrator
from intelligence.skills import SkillManager


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, limit: int):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class SalesIntelligenceTest(unittest.TestCase):
    def test_composite_price_and_inventory_routes_to_two_agents(self):
        decision = SalesAgentOrchestrator().route("金枕现在多少钱，10箱有现货吗？")
        self.assertEqual(decision.primary_agent, "quotation_agent")
        self.assertIn("inventory_agent", decision.supporting_agents)
        self.assertEqual(decision.intent, "price_inquiry")
        self.assertEqual(decision.entities["quantity"], ["10箱"])
        self.assertEqual(decision.entities["product"], ["金枕"])
        self.assertFalse(decision.requires_human)

    def test_stop_contact_is_high_risk_and_human_gated(self):
        decision = SalesAgentOrchestrator().route("不要再联系，把我的信息删掉")
        self.assertEqual(decision.primary_agent, "compliance_agent")
        self.assertEqual(decision.intent, "stop_contact")
        self.assertEqual(decision.risk, "high")
        self.assertTrue(decision.requires_human)
        self.assertIn("stop_marketing", decision.proposed_actions)

    def test_skill_loader_matches_agent_and_keyword(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quote"
            path.mkdir()
            (path / "SKILL.md").write_text(
                "---\nname: 报价规范\nagents: quotation_agent\nkeywords: 报价,价格\npriority: 90\n---\n不得猜测价格。",
                encoding="utf-8",
            )
            manager = SkillManager(Path(directory))
            prompt, names = manager.render(["quotation_agent"], "请给我报价")
        self.assertEqual(names, ["报价规范"])
        self.assertIn("不得猜测价格", prompt)

    def test_echomind_client_normalizes_chat_response(self):
        client = EchoMindClient("http://127.0.0.1:8000", "shadow", timeout=2)
        payload = {"response": "您好老板", "intent": "general", "primary_agent": "general"}
        with patch("urllib.request.urlopen", return_value=_Response(payload)) as request_mock:
            result = client.chat(message="你好", user_id="lead:1", conv_id="salesflow:1")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["response"], "您好老板")
        sent = json.loads(request_mock.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(sent["conv_id"], "salesflow:1")


if __name__ == "__main__":
    unittest.main()
