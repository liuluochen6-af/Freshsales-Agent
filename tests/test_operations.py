import os
import tempfile
import unittest
from pathlib import Path

import app as agent_app


class OperationsProtocolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        agent_app.DB_PATH = Path(self.tmp.name) / "operations.db"
        agent_app.init_db()
        agent_app.app.config.update(TESTING=True)
        self.client = agent_app.app.test_client()
        os.environ["AGENT_BOOTSTRAP_TOKEN"] = "test-bootstrap-token"
        os.environ.pop("AGENT_ADMIN_TOKEN", None)

        response = self.client.post(
            "/api/worker/register",
            headers={"X-Bootstrap-Token": "test-bootstrap-token"},
            json={
                "node_id": "test-node-01",
                "display_name": "测试节点",
                "machine_name": "TEST-PC",
                "account_ref": "测试微信号",
                "max_active_conversations": 200,
                "dry_run": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.token = response.json["node_token"]
        self.worker_headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Node-ID": "test-node-01",
        }

    def tearDown(self):
        os.environ.pop("AGENT_BOOTSTRAP_TOKEN", None)
        os.environ.pop("AGENT_ADMIN_TOKEN", None)
        self.tmp.cleanup()

    def create_conversation(self, number: int) -> int:
        with agent_app.db() as conn:
            cur = conn.execute(
                """
                INSERT INTO leads(store_name,phone,wechat_id,source,source_basis,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)
                """,
                (f"门店{number}", f"1380000{number:04d}", f"wx_{number}", "测试", "授权测试", agent_app.now(), agent_app.now()),
            )
            conv = conn.execute(
                "INSERT INTO conversations(lead_id,updated_at) VALUES(?,?)",
                (cur.lastrowid, agent_app.now()),
            )
            return int(conv.lastrowid)

    def bind(self, conversation_id: int, contact: str, auto_reply: bool = True):
        return self.client.post("/api/operations/bindings", json={
            "node_id": "test-node-01",
            "conversation_id": conversation_id,
            "contact_ref": contact,
            "auto_reply": auto_reply,
        })

    def test_node_can_manage_more_than_twenty_conversations(self):
        for number in range(1, 36):
            response = self.bind(self.create_conversation(number), f"联系人{number}")
            self.assertEqual(response.status_code, 201)

        sessions = self.client.get("/api/worker/sessions", headers=self.worker_headers)
        self.assertEqual(sessions.status_code, 200)
        self.assertEqual(len(sessions.json["sessions"]), 35)
        self.assertEqual(sessions.json["max_active"], 200)

    def test_new_local_device_can_register_itself_without_manual_node_setup(self):
        response = self.client.post("/api/worker/register", json={
            "node_id": "automatic-local-device",
            "display_name": "自动配置设备",
            "machine_name": "LOCAL-PC",
            "account_ref": "本机微信自动槽位",
            "max_active_conversations": 500,
            "dry_run": True,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["node_id"], "automatic-local-device")
        self.assertTrue(response.json["node_token"])

    def test_inbound_deduplication_queue_lease_and_delivery_receipt(self):
        conversation_id = self.create_conversation(1)
        self.assertEqual(self.bind(conversation_id, "天使").status_code, 201)
        payload = {
            "account_ref": "测试微信号",
            "contact_ref": "天使",
            "external_message_id": "wechat-message-001",
            "content": "你好",
            "observed_at": agent_app.now(),
        }
        first = self.client.post("/api/worker/inbound", headers=self.worker_headers, json=payload)
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json["duplicate"])
        self.assertIsNotNone(first.json["reply_job_id"])

        duplicate = self.client.post("/api/worker/inbound", headers=self.worker_headers, json=payload)
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.json["duplicate"])
        with agent_app.db() as conn:
            inbound_count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id=? AND sender='customer'",
                (conversation_id,),
            ).fetchone()[0]
        self.assertEqual(inbound_count, 1)

        pulled = self.client.post(
            "/api/worker/jobs/pull", headers=self.worker_headers,
            json={"limit": 1, "lease_seconds": 90},
        )
        self.assertEqual(pulled.status_code, 200)
        self.assertEqual(len(pulled.json["jobs"]), 1)
        job = pulled.json["jobs"][0]
        self.assertIn("老板您好", job["payload"]["content"])

        completed = self.client.post(
            f"/api/worker/jobs/{job['id']}/complete", headers=self.worker_headers,
            json={"lease_token": job["lease_token"], "outcome": "succeeded"},
        )
        self.assertEqual(completed.status_code, 200)
        replay = self.client.post(
            f"/api/worker/jobs/{job['id']}/complete", headers=self.worker_headers,
            json={"lease_token": job["lease_token"], "outcome": "succeeded"},
        )
        self.assertEqual(replay.status_code, 409)
        with agent_app.db() as conn:
            sales_count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id=? AND message_type=?",
                (conversation_id, f"worker-out:{job['id']}"),
            ).fetchone()[0]
        self.assertEqual(sales_count, 1)

    def test_dry_run_node_never_leases_send_job(self):
        conversation_id = self.create_conversation(1)
        self.bind(conversation_id, "测试联系人", auto_reply=False)
        queued = self.client.post(
            f"/api/operations/conversations/{conversation_id}/queue-message",
            json={"content": "这是一条人工排队消息", "idempotency_key": "manual-test-001"},
        )
        self.assertEqual(queued.status_code, 202)
        heartbeat = self.client.post(
            "/api/worker/heartbeat", headers=self.worker_headers,
            json={"active_conversations": 1, "dry_run": True},
        )
        self.assertEqual(heartbeat.status_code, 200)
        pulled = self.client.post("/api/worker/jobs/pull", headers=self.worker_headers, json={"limit": 1})
        self.assertEqual(pulled.status_code, 200)
        self.assertTrue(pulled.json["dry_run"])
        self.assertEqual(pulled.json["jobs"], [])

    def test_newer_customer_message_supersedes_unsent_auto_reply(self):
        conversation_id = self.create_conversation(1)
        self.bind(conversation_id, "连续提问客户")
        base = {
            "account_ref": "测试微信号",
            "contact_ref": "连续提问客户",
            "observed_at": agent_app.now(),
        }
        first = self.client.post("/api/worker/inbound", headers=self.worker_headers, json={
            **base, "external_message_id": "batch-1", "content": "你好",
        })
        second = self.client.post("/api/worker/inbound", headers=self.worker_headers, json={
            **base, "external_message_id": "batch-2", "content": "价格怎么拿",
        })
        self.assertIsNotNone(first.json["reply_job_id"])
        self.assertIsNotNone(second.json["reply_job_id"])
        with agent_app.db() as conn:
            states = conn.execute(
                "SELECT status,COUNT(*) total FROM message_jobs WHERE conversation_id=? GROUP BY status",
                (conversation_id,),
            ).fetchall()
        self.assertEqual({row["status"]: row["total"] for row in states}, {"cancelled": 1, "queued": 1})

    def test_risk_message_pauses_auto_reply_until_operator_resumes(self):
        conversation_id = self.create_conversation(1)
        self.bind(conversation_id, "售后客户")
        inbound = self.client.post("/api/worker/inbound", headers=self.worker_headers, json={
            "account_ref": "测试微信号",
            "contact_ref": "售后客户",
            "external_message_id": "risk-001",
            "content": "我要退款赔偿",
        })
        self.assertEqual(inbound.status_code, 200)
        self.assertTrue(inbound.json["requires_human"])
        self.assertIsNone(inbound.json["reply_job_id"])
        resumed = self.client.patch(
            f"/api/operations/conversations/{conversation_id}/takeover", json={"enabled": False},
        )
        self.assertEqual(resumed.status_code, 200)
        self.assertFalse(resumed.json["human_takeover"])

    def test_public_source_question_is_answered_automatically_from_lead_record(self):
        conversation_id = self.create_conversation(31)
        with agent_app.db() as conn:
            conn.execute(
                """UPDATE leads SET source=?,source_basis=?
                   WHERE id=(SELECT lead_id FROM conversations WHERE id=?)""",
                ("公开网站的商户公开页面", "业务方确认：商户公开信息", conversation_id),
            )
        self.assertEqual(self.bind(conversation_id, "来源询问客户").status_code, 201)
        inbound = self.client.post("/api/worker/inbound", headers=self.worker_headers, json={
            "account_ref": "测试微信号", "contact_ref": "来源询问客户",
            "external_message_id": "source-001", "content": "你是怎么获得我联系方式的",
        })
        self.assertEqual(inbound.status_code, 200)
        self.assertFalse(inbound.json["requires_human"])
        self.assertIsNotNone(inbound.json["reply_job_id"])
        with agent_app.db() as conn:
            job = conn.execute("SELECT payload_json FROM message_jobs WHERE id=?", (inbound.json["reply_job_id"],)).fetchone()
        self.assertIn("公开网站", job["payload_json"])
        self.assertNotIn("朋友推荐", job["payload_json"])

    def test_stop_request_disables_future_marketing_and_auto_reply(self):
        conversation_id = self.create_conversation(32)
        self.assertEqual(self.bind(conversation_id, "停止联系客户").status_code, 201)
        inbound = self.client.post("/api/worker/inbound", headers=self.worker_headers, json={
            "account_ref": "测试微信号", "contact_ref": "停止联系客户",
            "external_message_id": "stop-001", "content": "把我的联系方式删掉，不要再联系",
        })
        self.assertEqual(inbound.status_code, 200)
        self.assertTrue(inbound.json["requires_human"])
        self.assertIsNone(inbound.json["reply_job_id"])
        with agent_app.db() as conn:
            lead = conn.execute(
                "SELECT l.stop_marketing,l.status,c.human_takeover,b.auto_reply FROM conversations c JOIN leads l ON l.id=c.lead_id JOIN chat_bindings b ON b.conversation_id=c.id WHERE c.id=?",
                (conversation_id,),
            ).fetchone()
        self.assertEqual(lead["stop_marketing"], 1)
        self.assertEqual(lead["status"], "停止联系")
        self.assertEqual(lead["human_takeover"], 1)
        self.assertEqual(lead["auto_reply"], 0)

    def test_admin_token_protects_central_apis(self):
        os.environ["AGENT_ADMIN_TOKEN"] = "admin-test-token"
        denied = self.client.get("/api/operations/overview")
        self.assertEqual(denied.status_code, 401)
        allowed = self.client.get(
            "/api/operations/overview", headers={"X-Admin-Token": "admin-test-token"},
        )
        self.assertEqual(allowed.status_code, 200)
        worker_still_allowed = self.client.get("/api/worker/sessions", headers=self.worker_headers)
        self.assertEqual(worker_still_allowed.status_code, 200)


if __name__ == "__main__":
    unittest.main()
