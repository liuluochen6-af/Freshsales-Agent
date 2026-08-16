import tempfile
import unittest
from pathlib import Path

import app as agent_app


class DialogueTrainingRegressionTest(unittest.TestCase):
    """Production dialogue matrix: facts come from rules; tone may vary, boundaries may not."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        agent_app.DB_PATH = Path(self.tmp.name) / "dialogue-training.db"
        agent_app.init_db()
        agent_app.app.config.update(TESTING=True)
        self.client = agent_app.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def ask(self, question):
        response = self.client.post("/api/knowledge/answer", json={"question": question, "use_ai": False})
        self.assertEqual(response.status_code, 200)
        return response.json

    def test_bulk_customer_scenario_matrix(self):
        cases = [
            # 打招呼与身份
            ("你好", "老板您好", None), ("您好", "老板您好", None),
            ("你是谁", "跨境云有限公司", False), ("请问你是哪位", "小刘", False),
            ("你们是哪家公司", "跨境云有限公司", False), ("你们做什么的", "榴莲供货", False),
            ("怎么称呼你", "小刘", False), ("你们是做榴莲的吗", "猫山王", False),
            # 联系方式来源的不同问法
            ("你是怎么获得我联系方式的", "公开网站", False),
            ("我联系方式哪来的", "公开网站", False), ("号码哪里来的", "公开网站", False),
            ("你怎么有我电话", "公开网站", False), ("从哪找到我的", "公开网站", False),
            ("谁给你的号码", "公开网站", False), ("为什么加我", "公开网站", False),
            ("我的电话来源是什么", "公开网站", False),
            # 询价与规格
            ("价格怎么样", "整箱", None), ("给我报价", "整箱", None),
            ("都有哪些规格", "整箱", None), ("整箱多少钱", "BB6-7猫", None),
            ("整箱有哪些档", "BB6-7猫", None), ("整箱价格都发我", "AA4-5猫", None),
            ("单粒多少钱", "先说想看哪一种", None), ("单粒有哪些规格", "猫山王", None),
            # 运费、物流、品质
            ("运费怎么算", "华南", None), ("华南运费多少", "60", None),
            ("偏远地区运费", "180", None), ("一般几天能到", "2至3天", None),
            ("物流多久到", "2至3天", None), ("果子新鲜吗", "生鲜", None),
            ("品质每一颗都稳定吗", "不能承诺", None), ("成熟度能保证吗", "不能承诺", None),
            # 常见合作异议
            ("太贵了", "同品种", None), ("你们价格贵", "同等级", None),
            ("我已经有供应商了", "备用渠道", None), ("我有固定供应商", "不催", None),
            ("我想先试一粒", "单粒试单", None), ("第一次不想拿整箱", "单粒", None),
            ("我现在忙，晚点说", "您先忙", None), ("晚点再聊", "不连续发", None),
            # 不得猜测的事实
            ("今天还有多少库存", "核一下准确数量", True),
            ("能发我今天的实拍视频吗", "当批", True), ("有营业执照吗", "正式文件", True),
            ("怎么付款", "可核验的付款信息", True), ("能开专票吗", "没有确认", True),
            ("最低多少起订", "没有统一起订量", True), ("长期合作能固定最低价吗", "不能先承诺", True),
            ("少房怎么赔", "榴莲头", True), ("虫果三房怎么处理", "补整果", True),
            ("我要退款赔偿", "售后人员", True),
            # 来源冲突、隐私投诉与停止信号
            ("我根本没在网站登记过，来源不对", "暂停联系", True),
            ("你们是不是买卖信息", "不会继续营销", True),
            ("这是隐私泄露，我要投诉你们", "负责人核查", True),
            ("不要再联系我", "不再联系", True), ("把我的联系方式删掉", "不再联系", True),
            ("别再发消息了", "不再发送", True), ("停止联系", "不再联系", True),
        ]
        self.assertGreaterEqual(len(cases), 55)
        forbidden_claims = ("朋友推荐", "同行给的", "保证新鲜", "保证每颗", "绝对没问题")
        for question, expected, expected_human in cases:
            with self.subTest(question=question):
                result = self.ask(question)
                self.assertTrue(result["answer"].strip())
                self.assertIn(expected, result["answer"])
                if expected_human is not None:
                    self.assertEqual(result["requires_human"], expected_human)
                for forbidden in forbidden_claims:
                    self.assertNotIn(forbidden, result["answer"])

    def test_goal_directed_multi_turn_flow_and_exact_price(self):
        with agent_app.db() as conn:
            lead = conn.execute(
                """INSERT INTO leads(store_name,phone,source,source_basis,created_at,updated_at)
                   VALUES(?,?,?,?,?,?)""",
                ("多轮测试水果店", "13800009999", agent_app.DEFAULT_CONTACT_SOURCE,
                 agent_app.DEFAULT_CONTACT_SOURCE_BASIS, agent_app.now(), agent_app.now()),
            )
            conv = conn.execute(
                "INSERT INTO conversations(lead_id,updated_at) VALUES(?,?)",
                (lead.lastrowid, agent_app.now()),
            )
            conversation_id = int(conv.lastrowid)

        turns = [
            ("你好", "老板您好"),
            ("好，可以交流", "整箱"),
            ("我做单粒代发", "单粒"),
            ("主要看猫山王A级3-3.5斤，多少钱", "230元/粒"),
            ("你是怎么获得我联系方式的", "公开网站"),
            ("行，继续", "收货在哪个地区"),
        ]
        for question, expected in turns:
            sent = self.client.post(
                f"/api/conversations/{conversation_id}/messages",
                json={"sender": "customer", "content": question},
            )
            self.assertEqual(sent.status_code, 200)
            suggestion = self.client.post(f"/api/conversations/{conversation_id}/suggest")
            self.assertEqual(suggestion.status_code, 200)
            self.assertIn(expected, suggestion.json["suggestion"])
            self.assertNotIn("朋友推荐", suggestion.json["suggestion"])

        source_result = self.client.post(f"/api/conversations/{conversation_id}/suggest").json
        self.assertFalse(source_result["requires_human"])


if __name__ == "__main__":
    unittest.main()
