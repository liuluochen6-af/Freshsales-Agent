import tempfile
import unittest
from pathlib import Path

import app as agent_app


class AgentFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        agent_app.DB_PATH = Path(self.tmp.name) / "test.db"
        agent_app.init_db()
        agent_app.app.config.update(TESTING=True)
        self.client = agent_app.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_complete_sales_flow(self):
        csv_text = (
            "门店名称,联系人,手机号,微信号,地区,门店类型,来源,来源依据\n"
            "鲜果优选广州店,陈经理,13800001001,fruit_gz_01,广东广州,社区店,行业展会,展会登记授权联系\n"
        )
        result = self.client.post(
            "/api/leads/import",
            data={"file": (self._bytes(csv_text), "leads.csv")},
            content_type="multipart/form-data",
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["imported"], 1)

        result = self.client.post("/api/friend-tasks", json={
            "lead_ids": [1], "account_id": 1, "greeting": "您好，想了解贵店近期榴莲采购计划。"
        })
        self.assertEqual(result.json["created"], 1)
        denied = self.client.post("/api/friend-tasks/1/confirm-sent", json={})
        self.assertEqual(denied.status_code, 400)
        confirmed = self.client.post("/api/friend-tasks/1/confirm-sent", json={"confirmed": True})
        self.assertEqual(confirmed.json["status"], "已发送")
        accepted = self.client.post("/api/friend-tasks/1/accept")
        conversation_id = accepted.json["conversation_id"]

        message = self.client.post(f"/api/conversations/{conversation_id}/messages", json={
            "sender": "customer", "content": "金枕现在多少钱一箱？我先要10箱。"
        })
        self.assertEqual(message.json["intent"], "询价")
        suggestion = self.client.post(f"/api/conversations/{conversation_id}/suggest")
        self.assertTrue(suggestion.json["suggestion"])
        self.assertNotIn("680", suggestion.json["suggestion"])

        quote = self.client.post("/api/quotes", json={
            "lead_id": 1, "product_id": 2, "quantity": 10, "unit_price": 680, "freight": 120
        })
        self.assertEqual(quote.json["total"], 6920)
        order = self.client.post("/api/orders", json={
            "quote_id": quote.json["id"], "receiver": "陈经理", "phone": "13800001001",
            "address": "广东省广州市天河区示例路88号", "payment_status": "待付款"
        })
        order_id = order.json["id"]
        self.client.patch(f"/api/orders/{order_id}", json={"payment_status": "已付款"})
        inventory = self.client.get("/api/inventory/overview").json
        migrated_batch = next(row for row in inventory["batches"] if row["product_id"] == 2)
        self.client.patch(
            f"/api/inventory/batches/{migrated_batch['id']}/quality",
            json={"quality_status": "可售", "note": "测试盘点确认"},
        )
        reserved = self.client.post(f"/api/inventory/orders/{order_id}/reserve", json={})
        self.assertEqual(reserved.status_code, 200)
        shipment = self.client.post("/api/shipments", json={
            "order_id": order_id, "carrier": "顺丰冷运", "tracking_no": "SFTEST001", "batch_no": "TH-A"
        })
        self.assertEqual(shipment.status_code, 201)

        dashboard = self.client.get("/api/dashboard").json
        self.assertEqual(dashboard["metrics"]["leads"], 1)
        self.assertEqual(dashboard["metrics"]["accepted"], 1)
        self.assertEqual(dashboard["metrics"]["orders"], 1)
        self.assertEqual(dashboard["metrics"]["revenue"], 6920)

        order_row = self.client.get("/api/orders").json[0]
        self.assertEqual(order_row["receiver"], "陈经理")
        self.assertEqual(order_row["payment_status"], "已付款")
        self.assertEqual(order_row["status"], "已发货")
        self.assertEqual(order_row["tracking_no"], "SFTEST001")

    def test_manual_task_can_be_skipped_without_using_quota(self):
        csv_text = (
            "门店名称,联系人,手机号,微信号,地区,来源,来源依据\n"
            "江南鲜果店,李店长,13800001002,fruit_jn_02,江苏苏州,客户登记,客户同意微信联系\n"
        )
        imported = self.client.post(
            "/api/leads/import",
            data={"file": (self._bytes(csv_text), "leads.csv")},
            content_type="multipart/form-data",
        )
        self.assertEqual(imported.json["imported"], 1)
        created = self.client.post("/api/friend-tasks", json={
            "lead_ids": [1], "account_id": 1, "greeting": "您好，这是之前约定的微信联系。"
        })
        self.assertEqual(created.json["created"], 1)
        skipped = self.client.post("/api/friend-tasks/1/skip", json={"reason": "客户当天不便"})
        self.assertEqual(skipped.json["status"], "已跳过")
        self.assertEqual(self.client.get("/api/wechat/accounts").json[0]["used_today"], 0)
        self.assertEqual(self.client.post("/api/friend-tasks/1/accept").status_code, 400)

    def test_import_without_source_uses_confirmed_public_website_provenance(self):
        csv_text = "门店名称,联系人,手机号\n公开信息水果店,王老板,13800001003\n"
        imported = self.client.post(
            "/api/leads/import",
            data={"file": (self._bytes(csv_text), "public-stores.csv")},
            content_type="multipart/form-data",
        )
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.json["unverified_source"], 0)
        with agent_app.db() as conn:
            lead = conn.execute("SELECT source,source_basis,import_provenance FROM leads WHERE id=1").fetchone()
        self.assertIn("公开网站", lead["source"])
        self.assertIn("业务方确认", lead["source_basis"])
        self.assertIn("public-stores.csv", lead["import_provenance"])
        self.assertNotIn("public-stores.csv", lead["source_basis"])

    def test_wechat_tasks_keep_imported_region_and_use_150_daily_limit(self):
        csv_text = (
            "门店名称,联系人,手机号,微信号,地区,来源,来源依据\n"
            "无锡鲜果店,陈老板,13800001004,fruit_wuxi_04,无锡,公开网站,无锡商户公开页面\n"
        )
        imported = self.client.post(
            "/api/leads/import",
            data={"file": (self._bytes(csv_text), "wuxi-stores.csv")},
            content_type="multipart/form-data",
        )
        self.assertEqual(imported.json["imported"], 1)

        account = self.client.get("/api/wechat/accounts").json[0]
        self.assertEqual(account["daily_limit"], 150)
        self.assertNotIn("华南", account["nickname"])
        self.assertNotIn("华东", account["nickname"])

        created = self.client.post("/api/friend-tasks", json={"lead_ids": [1], "account_id": account["id"]})
        self.assertEqual(created.json["created"], 1)
        task = self.client.get("/api/friend-tasks").json[0]
        self.assertEqual(task["region"], "无锡")
        self.assertEqual(task["region_snapshot"], "无锡")
        self.assertIn("无锡", task["remark"])
        self.assertEqual(task["result_status"], "待执行")

        added = self.client.post("/api/wechat/accounts", json={
            "nickname": "微信执行账号3", "wechat_no": "durian_sales_03", "daily_limit": 999,
        })
        self.assertEqual(added.status_code, 201)
        accounts = self.client.get("/api/wechat/accounts").json
        created_account = next(row for row in accounts if row["wechat_no"] == "durian_sales_03")
        self.assertEqual(created_account["daily_limit"], 150)

    @staticmethod
    def _bytes(text):
        import io
        return io.BytesIO(text.encode("utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
