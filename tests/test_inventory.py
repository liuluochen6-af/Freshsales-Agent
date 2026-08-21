import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import app as agent_app


class InventoryLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        agent_app.DB_PATH = Path(self.tmp.name) / "inventory.db"
        agent_app.init_db()
        agent_app.app.config.update(TESTING=True)
        self.client = agent_app.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def create_supplier(self) -> int:
        response = self.client.post("/api/inventory/suppliers", json={
            "code": "SUP-MY-001",
            "name": "马来西亚果园供应商",
            "contact_name": "采购对接人",
            "phone": "60123456789",
            "payment_terms": "现款",
            "lead_time_days": 2,
        })
        self.assertEqual(response.status_code, 201)
        return int(response.json["id"])

    def default_warehouse_id(self) -> int:
        rows = self.client.get("/api/inventory/warehouses").json
        return int(next(row["id"] for row in rows if row["is_default"]))

    def purchase_and_receive(self, quantity: float = 20) -> tuple[int, int]:
        supplier_id = self.create_supplier()
        warehouse_id = self.default_warehouse_id()
        production_date = date.today()
        expected_at = production_date + timedelta(days=3)
        expiry_date = production_date + timedelta(days=30)
        created = self.client.post("/api/inventory/purchase-orders", json={
            "supplier_id": supplier_id,
            "warehouse_id": warehouse_id,
            "expected_at": expected_at.isoformat(),
            "notes": "产地直采测试",
            "items": [{"product_id": 1, "quantity": quantity, "unit_cost": 900}],
        })
        self.assertEqual(created.status_code, 201)
        po_id = int(created.json["id"])
        approved = self.client.post(f"/api/inventory/purchase-orders/{po_id}/approve")
        self.assertEqual(approved.status_code, 200)
        item = self.client.get(f"/api/inventory/purchase-orders/{po_id}/items").json[0]
        received = self.client.post(f"/api/inventory/purchase-orders/{po_id}/receive", json={
            "item_id": item["id"],
            "quantity": quantity,
            "batch_no": "MY-BATCH-001",
            "production_date": production_date.isoformat(),
            "expiry_date": expiry_date.isoformat(),
        })
        self.assertEqual(received.status_code, 201)
        return po_id, int(received.json["id"])

    def release_batch(self, batch_id: int) -> None:
        response = self.client.patch(
            f"/api/inventory/batches/{batch_id}/quality",
            json={"quality_status": "可售", "note": "到货抽检通过"},
        )
        self.assertEqual(response.status_code, 200)

    def create_sales_order(self, quantity: int, paid: bool = True) -> int:
        csv_text = (
            "门店名称,联系人,手机号,微信号,地区,门店类型,来源,来源依据\n"
            f"库存测试门店{quantity},采购经理,1380000{quantity:04d},stock_{quantity},广东广州,水果店,客户授权,测试数据\n"
        )
        imported = self.client.post(
            "/api/leads/import",
            data={"file": (self._bytes(csv_text), f"lead-{quantity}.csv")},
            content_type="multipart/form-data",
        )
        self.assertEqual(imported.status_code, 200)
        lead_id = self.client.get("/api/leads").json[0]["id"]
        quote = self.client.post("/api/quotes", json={
            "lead_id": lead_id,
            "product_id": 1,
            "quantity": quantity,
            "unit_price": 1000,
            "freight": 60,
        })
        self.assertEqual(quote.status_code, 201)
        order = self.client.post("/api/orders", json={
            "quote_id": quote.json["id"],
            "receiver": "采购经理",
            "phone": "13800000000",
            "address": "广东省广州市测试地址",
            "payment_status": "已付款" if paid else "待付款",
        })
        self.assertEqual(order.status_code, 201)
        return int(order.json["id"])

    def test_purchase_quality_reserve_partial_ship_and_ledger(self):
        opening = self.client.get("/api/inventory/overview").json
        self.assertEqual(opening["metrics"]["available"], 0)
        self.assertGreater(opening["metrics"]["quarantine"], 0)

        po_id, batch_id = self.purchase_and_receive()
        after_receipt = self.client.get("/api/inventory/overview").json
        product_row = next(row for row in after_receipt["inventory"] if row["product_id"] == 1)
        self.assertEqual(product_row["available_qty"], 0)
        self.assertGreaterEqual(product_row["quarantine_qty"], 20)

        released = self.client.patch(
            f"/api/inventory/batches/{batch_id}/quality",
            json={"quality_status": "可售", "note": "到货抽检通过"},
        )
        self.assertEqual(released.status_code, 200)
        available = self.client.get("/api/inventory/overview").json
        product_row = next(row for row in available["inventory"] if row["product_id"] == 1)
        self.assertEqual(product_row["available_qty"], 20)

        order_id = self.create_sales_order(10, paid=True)
        no_reservation = self.client.post("/api/inventory/shipments", json={
            "order_id": order_id, "carrier": "顺丰冷运", "tracking_no": "SF-NO-RESERVE",
        })
        self.assertEqual(no_reservation.status_code, 409)

        reserved = self.client.post(f"/api/inventory/orders/{order_id}/reserve", json={})
        self.assertEqual(reserved.status_code, 200)
        self.assertEqual(reserved.json["reserved"], 10)
        reserved_again = self.client.post(f"/api/inventory/orders/{order_id}/reserve", json={})
        self.assertEqual(reserved_again.json["reserved"], 10)

        first = self.client.post("/api/inventory/shipments", json={
            "order_id": order_id,
            "carrier": "顺丰冷运",
            "tracking_no": "SF-PART-001",
            "quantity": 4,
            "freight_cost": 25,
        })
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json["order_status"], "部分发货")
        duplicate_tracking = self.client.post("/api/inventory/shipments", json={
            "order_id": order_id,
            "carrier": "顺丰冷运",
            "tracking_no": "SF-PART-001",
            "quantity": 1,
        })
        self.assertEqual(duplicate_tracking.status_code, 409)

        second = self.client.post("/api/inventory/shipments", json={
            "order_id": order_id,
            "carrier": "顺丰冷运",
            "tracking_no": "SF-PART-002",
            "quantity": 6,
        })
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.json["order_status"], "已发货")

        order = next(row for row in self.client.get("/api/orders").json if row["id"] == order_id)
        self.assertEqual(order["fulfilled_quantity"], 10)
        self.assertEqual(order["reserved_quantity"], 0)
        self.assertEqual(order["fulfillment_status"], "已全部发货")
        shipments = self.client.get("/api/inventory/shipments").json
        self.assertEqual(len([row for row in shipments if row["order_id"] == order_id]), 2)
        movements = self.client.get("/api/inventory/movements").json
        sales_out = [row for row in movements if row["reference_type"] == "shipment"]
        self.assertEqual(sum(row["quantity"] for row in sales_out), -10)
        purchase_in = [row for row in movements if row["reference_type"] == "purchase_receipt"]
        self.assertEqual(sum(row["quantity"] for row in purchase_in), 20)

        purchase = next(row for row in self.client.get("/api/inventory/purchase-orders").json if row["id"] == po_id)
        self.assertEqual(purchase["status"], "已全部到货")

    def test_shortage_is_atomic_and_unpaid_order_cannot_ship(self):
        _, batch_id = self.purchase_and_receive(quantity=5)
        self.release_batch(batch_id)
        shortage_order = self.create_sales_order(50, paid=True)
        shortage = self.client.post(f"/api/inventory/orders/{shortage_order}/reserve", json={})
        self.assertEqual(shortage.status_code, 409)
        with agent_app.db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM stock_reservations WHERE order_id=?", (shortage_order,)
            ).fetchone()[0]
        self.assertEqual(count, 0)

        unpaid_order = self.create_sales_order(3, paid=False)
        reserved = self.client.post(f"/api/inventory/orders/{unpaid_order}/reserve", json={})
        self.assertEqual(reserved.status_code, 200)
        blocked = self.client.post("/api/inventory/shipments", json={
            "order_id": unpaid_order,
            "carrier": "顺丰冷运",
            "tracking_no": "SF-UNPAID",
            "quantity": 1,
        })
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("尚未确认收款", blocked.json["error"])
        batch = next(row for row in self.client.get("/api/inventory/overview").json["batches"] if row["id"] == batch_id)
        self.assertEqual(batch["quality_status"], "可售")

    def test_inventory_adjustment_requires_approval_and_protects_reserved_stock(self):
        _, batch_id = self.purchase_and_receive(quantity=10)
        self.release_batch(batch_id)
        warehouse_id = self.default_warehouse_id()
        positive = self.client.post("/api/inventory/adjustments", json={
            "product_id": 1,
            "warehouse_id": warehouse_id,
            "quantity_delta": 3,
            "reason": "盘点盘盈",
        })
        self.assertEqual(positive.status_code, 201)
        before = self.client.get("/api/inventory/overview").json
        before_available = next(row["available_qty"] for row in before["inventory"] if row["product_id"] == 1)
        self.assertEqual(before_available, 10)
        approved = self.client.post(f"/api/inventory/adjustments/{positive.json['id']}/approve")
        self.assertEqual(approved.status_code, 200)
        after = self.client.get("/api/inventory/overview").json
        after_available = next(row["available_qty"] for row in after["inventory"] if row["product_id"] == 1)
        self.assertEqual(after_available, 13)

        missing_batch = self.client.post("/api/inventory/adjustments", json={
            "product_id": 1,
            "warehouse_id": warehouse_id,
            "quantity_delta": -1,
            "reason": "破损",
        })
        self.assertEqual(missing_batch.status_code, 400)

        order_id = self.create_sales_order(8, paid=True)
        self.assertEqual(self.client.post(f"/api/inventory/orders/{order_id}/reserve", json={}).status_code, 200)
        excessive = self.client.post("/api/inventory/adjustments", json={
            "product_id": 1,
            "warehouse_id": warehouse_id,
            "batch_id": batch_id,
            "quantity_delta": -5,
            "reason": "异常损耗",
        })
        self.assertEqual(excessive.status_code, 201)
        blocked = self.client.post(f"/api/inventory/adjustments/{excessive.json['id']}/approve")
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("侵占已锁定库存", blocked.json["error"])

    @staticmethod
    def _bytes(text: str):
        import io
        return io.BytesIO(text.encode("utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
