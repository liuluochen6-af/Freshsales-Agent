from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any, Callable
from uuid import uuid4

from flask import jsonify, request


INVENTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    contact_name TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    payment_terms TEXT DEFAULT '现款',
    lead_time_days INTEGER DEFAULT 3,
    status TEXT DEFAULT '启用',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS warehouses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    location TEXT DEFAULT '',
    status TEXT DEFAULT '启用',
    is_default INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_inventory_settings (
    product_id INTEGER PRIMARY KEY,
    reorder_point REAL DEFAULT 10,
    safety_stock REAL DEFAULT 5,
    shelf_life_days INTEGER DEFAULT 7,
    preferred_supplier_id INTEGER,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(product_id) REFERENCES products(id),
    FOREIGN KEY(preferred_supplier_id) REFERENCES suppliers(id)
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_no TEXT NOT NULL UNIQUE,
    supplier_id INTEGER NOT NULL,
    warehouse_id INTEGER NOT NULL,
    status TEXT DEFAULT '草稿',
    payment_status TEXT DEFAULT '未付款',
    expected_at TEXT,
    total_amount REAL DEFAULT 0,
    notes TEXT DEFAULT '',
    approved_at TEXT,
    received_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(supplier_id) REFERENCES suppliers(id),
    FOREIGN KEY(warehouse_id) REFERENCES warehouses(id)
);

CREATE TABLE IF NOT EXISTS purchase_order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    ordered_qty REAL NOT NULL,
    received_qty REAL DEFAULT 0,
    unit_cost REAL NOT NULL,
    subtotal REAL NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(id),
    FOREIGN KEY(product_id) REFERENCES products(id)
);

CREATE TABLE IF NOT EXISTS inventory_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_no TEXT NOT NULL UNIQUE,
    product_id INTEGER NOT NULL,
    warehouse_id INTEGER NOT NULL,
    supplier_id INTEGER,
    purchase_order_id INTEGER,
    received_qty REAL NOT NULL,
    quality_status TEXT DEFAULT '待质检',
    production_date TEXT,
    expiry_date TEXT,
    unit_cost REAL DEFAULT 0,
    source_note TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(product_id) REFERENCES products(id),
    FOREIGN KEY(warehouse_id) REFERENCES warehouses(id),
    FOREIGN KEY(supplier_id) REFERENCES suppliers(id),
    FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(id)
);

CREATE TABLE IF NOT EXISTS inventory_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    warehouse_id INTEGER NOT NULL,
    batch_id INTEGER,
    movement_type TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit_cost REAL DEFAULT 0,
    reference_type TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    operator TEXT DEFAULT '系统',
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(product_id) REFERENCES products(id),
    FOREIGN KEY(warehouse_id) REFERENCES warehouses(id),
    FOREIGN KEY(batch_id) REFERENCES inventory_batches(id)
);
CREATE INDEX IF NOT EXISTS idx_inventory_movements_stock
    ON inventory_movements(product_id,warehouse_id,batch_id,id);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity REAL NOT NULL,
    unit_price REAL NOT NULL,
    fulfilled_qty REAL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(id),
    FOREIGN KEY(product_id) REFERENCES products(id)
);

CREATE TABLE IF NOT EXISTS stock_reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    order_item_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    warehouse_id INTEGER NOT NULL,
    batch_id INTEGER NOT NULL,
    quantity REAL NOT NULL,
    status TEXT DEFAULT '生效',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(id),
    FOREIGN KEY(order_item_id) REFERENCES order_items(id),
    FOREIGN KEY(product_id) REFERENCES products(id),
    FOREIGN KEY(warehouse_id) REFERENCES warehouses(id),
    FOREIGN KEY(batch_id) REFERENCES inventory_batches(id)
);
CREATE INDEX IF NOT EXISTS idx_stock_reservations_active
    ON stock_reservations(product_id,warehouse_id,batch_id,status);

CREATE TABLE IF NOT EXISTS fulfillment_shipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_no TEXT NOT NULL UNIQUE,
    order_id INTEGER NOT NULL,
    warehouse_id INTEGER NOT NULL,
    carrier TEXT NOT NULL,
    tracking_no TEXT NOT NULL UNIQUE,
    status TEXT DEFAULT '已发出',
    freight_cost REAL DEFAULT 0,
    inventory_posted INTEGER DEFAULT 1,
    shipped_at TEXT NOT NULL,
    delivered_at TEXT,
    exception_note TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(id),
    FOREIGN KEY(warehouse_id) REFERENCES warehouses(id)
);

CREATE TABLE IF NOT EXISTS fulfillment_shipment_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id INTEGER NOT NULL,
    order_item_id INTEGER NOT NULL,
    batch_id INTEGER NOT NULL,
    quantity REAL NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(shipment_id) REFERENCES fulfillment_shipments(id),
    FOREIGN KEY(order_item_id) REFERENCES order_items(id),
    FOREIGN KEY(batch_id) REFERENCES inventory_batches(id)
);

CREATE TABLE IF NOT EXISTS inventory_adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    adjustment_no TEXT NOT NULL UNIQUE,
    product_id INTEGER NOT NULL,
    warehouse_id INTEGER NOT NULL,
    batch_id INTEGER,
    quantity_delta REAL NOT NULL,
    reason TEXT NOT NULL,
    status TEXT DEFAULT '待审批',
    requested_by TEXT DEFAULT '操作员',
    approved_by TEXT DEFAULT '',
    approved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(product_id) REFERENCES products(id),
    FOREIGN KEY(warehouse_id) REFERENCES warehouses(id),
    FOREIGN KEY(batch_id) REFERENCES inventory_batches(id)
);
"""


class InventoryError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _identifier(prefix: str) -> str:
    return prefix + datetime.now().strftime("%Y%m%d%H%M%S") + uuid4().hex[:4].upper()


def _parse_date(value: Any, field: str, optional: bool = True) -> str | None:
    text = str(value or "").strip()
    if not text and optional:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise InventoryError(f"{field}必须使用YYYY-MM-DD格式") from exc


def _positive(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise InventoryError(f"{field}必须是数字") from exc
    if number <= 0:
        raise InventoryError(f"{field}必须大于0")
    return round(number, 4)


def ensure_order_item_from_quote(conn: sqlite3.Connection, order_id: int, quote: sqlite3.Row, created_at: str) -> int:
    existing = conn.execute("SELECT id FROM order_items WHERE order_id=? LIMIT 1", (order_id,)).fetchone()
    if existing:
        return int(existing["id"])
    cur = conn.execute(
        "INSERT INTO order_items(order_id,product_id,quantity,unit_price,fulfilled_qty,created_at) VALUES(?,?,?,?,0,?)",
        (order_id, quote["product_id"], quote["quantity"], quote["unit_price"], created_at),
    )
    return int(cur.lastrowid)


def _sync_product_stock(conn: sqlite3.Connection, product_id: int, updated_at: str) -> None:
    row = conn.execute(
        """
        WITH batch_stock AS (
          SELECT b.id,b.quality_status,COALESCE(SUM(m.quantity),0) on_hand
          FROM inventory_batches b LEFT JOIN inventory_movements m ON m.batch_id=b.id
          WHERE b.product_id=? GROUP BY b.id
        ), reserved AS (
          SELECT COALESCE(SUM(quantity),0) qty FROM stock_reservations
          WHERE product_id=? AND status='生效'
        )
        SELECT MAX(0,COALESCE(SUM(CASE WHEN quality_status='可售' THEN on_hand ELSE 0 END),0)-(SELECT qty FROM reserved)) available
        FROM batch_stock
        """,
        (product_id, product_id),
    ).fetchone()
    conn.execute("UPDATE products SET stock=?,updated_at=? WHERE id=?", (int(float(row["available"] or 0)), updated_at, product_id))


def initialize_inventory(conn: sqlite3.Connection, timestamp: str) -> None:
    order_columns = {row[1] for row in conn.execute("PRAGMA table_info(orders)").fetchall()}
    additions = {
        "warehouse_id": "INTEGER",
        "inventory_status": "TEXT DEFAULT '未占库'",
        "fulfillment_status": "TEXT DEFAULT '未发货'",
        "sales_channel": "TEXT DEFAULT '微信'",
        "stock_reserved_at": "TEXT",
        "shipped_at": "TEXT",
    }
    for name, sql_type in additions.items():
        if name not in order_columns:
            conn.execute(f"ALTER TABLE orders ADD COLUMN {name} {sql_type}")

    if conn.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO suppliers(code,name,status,notes,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            ("SUP-UNASSIGNED", "待指定供应商", "启用", "迁移占位；正式采购前必须选择真实供应商", timestamp, timestamp),
        )
    if conn.execute("SELECT COUNT(*) FROM warehouses").fetchone()[0] == 0:
        conn.execute(
            "INSERT INTO warehouses(code,name,location,status,is_default,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            ("WH-MAIN", "主仓", "待补充地址", "启用", 1, timestamp, timestamp),
        )
    warehouse_id = int(conn.execute("SELECT id FROM warehouses WHERE is_default=1 ORDER BY id LIMIT 1").fetchone()[0])
    supplier_id = int(conn.execute("SELECT id FROM suppliers ORDER BY id LIMIT 1").fetchone()[0])
    conn.execute("UPDATE orders SET warehouse_id=COALESCE(warehouse_id,?)", (warehouse_id,))

    products = conn.execute("SELECT * FROM products ORDER BY id").fetchall()
    for product in products:
        conn.execute(
            "INSERT OR IGNORE INTO product_inventory_settings(product_id,reorder_point,safety_stock,shelf_life_days,updated_at) VALUES(?,?,?,?,?)",
            (product["id"], 10, 5, 7, timestamp),
        )
        movement_exists = conn.execute(
            "SELECT 1 FROM inventory_movements WHERE product_id=? LIMIT 1", (product["id"],)
        ).fetchone()
        legacy_qty = float(product["stock"] or 0)
        if not movement_exists and legacy_qty > 0:
            batch_no = f"MIG-{product['sku']}"
            batch = conn.execute("SELECT id FROM inventory_batches WHERE batch_no=?", (batch_no,)).fetchone()
            if batch:
                batch_id = int(batch["id"])
            else:
                cur = conn.execute(
                    """
                    INSERT INTO inventory_batches(batch_no,product_id,warehouse_id,supplier_id,received_qty,
                      quality_status,unit_cost,source_note,created_at,updated_at)
                    VALUES(?,?,?,?,?,'待质检',?,'历史库存迁移；释放销售前必须盘点和质检',?,?)
                    """,
                    (batch_no, product["id"], warehouse_id, supplier_id, legacy_qty, float(product["price"] or 0), timestamp, timestamp),
                )
                batch_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT OR IGNORE INTO inventory_movements(product_id,warehouse_id,batch_id,movement_type,
                  quantity,unit_cost,reference_type,reference_id,idempotency_key,operator,note,created_at)
                VALUES(?,?,?,'期初迁移',?,?,'migration',? ,?,'系统','历史库存进入隔离区，待盘点质检',?)
                """,
                (product["id"], warehouse_id, batch_id, legacy_qty, float(product["price"] or 0),
                 str(product["id"]), f"migration-opening:{product['id']}", timestamp),
            )

    orders = conn.execute(
        "SELECT o.id,q.* FROM orders o JOIN quotes q ON q.id=o.quote_id WHERE o.quote_id IS NOT NULL"
    ).fetchall()
    for row in orders:
        ensure_order_item_from_quote(conn, int(row["id"]), row, timestamp)
    for product in products:
        _sync_product_stock(conn, int(product["id"]), timestamp)


def _batch_available_rows(conn: sqlite3.Connection, product_id: int, warehouse_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        WITH stock AS (
          SELECT b.id,COALESCE(SUM(m.quantity),0) on_hand
          FROM inventory_batches b LEFT JOIN inventory_movements m ON m.batch_id=b.id
          WHERE b.product_id=? AND b.warehouse_id=? AND b.quality_status='可售'
            AND (b.expiry_date IS NULL OR b.expiry_date>=?)
          GROUP BY b.id
        ), reserved AS (
          SELECT batch_id,COALESCE(SUM(quantity),0) qty FROM stock_reservations
          WHERE status='生效' GROUP BY batch_id
        )
        SELECT b.*,s.on_hand,COALESCE(r.qty,0) reserved_qty,
               s.on_hand-COALESCE(r.qty,0) available_qty
        FROM stock s JOIN inventory_batches b ON b.id=s.id
        LEFT JOIN reserved r ON r.batch_id=b.id
        WHERE s.on_hand-COALESCE(r.qty,0)>0
        ORDER BY COALESCE(b.expiry_date,'9999-12-31'),b.created_at,b.id
        """,
        (product_id, warehouse_id, date.today().strftime("%Y-%m-%d")),
    ).fetchall()


def reserve_order_stock(conn: sqlite3.Connection, order_id: int, warehouse_id: int, timestamp: str) -> dict[str, Any]:
    order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        raise InventoryError("销售订单不存在", 404)
    if order["status"] in {"已关闭", "已取消"}:
        raise InventoryError("已关闭或取消的订单不能占库")
    items = conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (order_id,)).fetchall()
    if not items:
        raise InventoryError("订单没有商品明细")

    allocations: list[tuple[int, int, int, int, float]] = []
    shortages = []
    for item in items:
        remaining = max(0.0, float(item["quantity"]) - float(item["fulfilled_qty"] or 0))
        active = float(conn.execute(
            "SELECT COALESCE(SUM(quantity),0) FROM stock_reservations WHERE order_item_id=? AND status='生效'",
            (item["id"],),
        ).fetchone()[0])
        need = max(0.0, remaining - active)
        if need <= 0:
            continue
        rows = _batch_available_rows(conn, int(item["product_id"]), warehouse_id)
        available_total = sum(float(row["available_qty"]) for row in rows)
        if available_total + 1e-9 < need:
            product = conn.execute("SELECT name FROM products WHERE id=?", (item["product_id"],)).fetchone()
            shortages.append({"product": product["name"], "required": need, "available": available_total})
            continue
        left = need
        for row in rows:
            quantity = min(left, float(row["available_qty"]))
            if quantity > 0:
                allocations.append((order_id, int(item["id"]), int(item["product_id"]), int(row["id"]), quantity))
                left -= quantity
            if left <= 1e-9:
                break
    if shortages:
        raise InventoryError("库存不足，未执行任何占库：" + json.dumps(shortages, ensure_ascii=False), 409)

    for allocation in allocations:
        conn.execute(
            """
            INSERT INTO stock_reservations(order_id,order_item_id,product_id,warehouse_id,batch_id,
              quantity,status,created_at,updated_at) VALUES(?,?,?,?,?,?,'生效',?,?)
            """,
            (*allocation[:3], warehouse_id, allocation[3], allocation[4], timestamp, timestamp),
        )
    conn.execute(
        "UPDATE orders SET warehouse_id=?,inventory_status='已占库',stock_reserved_at=?,status=?,updated_at=? WHERE id=?",
        (warehouse_id, timestamp, "待发货" if order["payment_status"] == "已付款" else "待付款", timestamp, order_id),
    )
    for item in items:
        _sync_product_stock(conn, int(item["product_id"]), timestamp)
    total_reserved = float(conn.execute(
        "SELECT COALESCE(SUM(quantity),0) FROM stock_reservations WHERE order_id=? AND status='生效'", (order_id,)
    ).fetchone()[0])
    return {"order_id": order_id, "reserved": total_reserved, "allocations": len(allocations)}


def create_fulfillment_shipment(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    timestamp: str,
    audit_func: Callable[..., None],
) -> dict[str, Any]:
    order_id = int(payload.get("order_id") or 0)
    order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        raise InventoryError("销售订单不存在", 404)
    if order["payment_status"] != "已付款":
        raise InventoryError("订单尚未确认收款，禁止发货", 409)
    reservations = conn.execute(
        """
        SELECT r.*,b.batch_no,b.unit_cost,i.fulfilled_qty,i.quantity ordered_qty
        FROM stock_reservations r
        JOIN inventory_batches b ON b.id=r.batch_id
        JOIN order_items i ON i.id=r.order_item_id
        WHERE r.order_id=? AND r.status='生效'
        ORDER BY COALESCE(b.expiry_date,'9999-12-31'),r.id
        """,
        (order_id,),
    ).fetchall()
    if not reservations:
        raise InventoryError("订单没有生效的库存占用，请先执行占库", 409)
    carrier = str(payload.get("carrier") or "").strip()
    tracking_no = str(payload.get("tracking_no") or "").strip()
    if not carrier or not tracking_no:
        raise InventoryError("承运商和运单号不能为空")
    if len(carrier) > 80 or len(tracking_no) > 100:
        raise InventoryError("承运商或运单号过长")
    requested_qty = float(payload.get("quantity") or 0)
    active_total = sum(float(row["quantity"]) for row in reservations)
    target_qty = active_total if requested_qty <= 0 else round(requested_qty, 4)
    if target_qty <= 0 or target_qty > active_total + 1e-9:
        raise InventoryError(f"本次发货数量必须在0和已占库数量{active_total:g}之间")

    shipment_no = _identifier("SH")
    cur = conn.execute(
        """
        INSERT INTO fulfillment_shipments(shipment_no,order_id,warehouse_id,carrier,tracking_no,status,
          freight_cost,inventory_posted,shipped_at,created_at,updated_at)
        VALUES(?,?,?,?,?,'已发出',?,1,?,?,?)
        """,
        (shipment_no, order_id, int(order["warehouse_id"]), carrier, tracking_no,
         max(0.0, float(payload.get("freight_cost") or 0)), timestamp, timestamp, timestamp),
    )
    shipment_id = int(cur.lastrowid)
    left = target_qty
    affected_products: set[int] = set()
    for reservation in reservations:
        if left <= 1e-9:
            break
        consume = min(left, float(reservation["quantity"]))
        on_hand = float(conn.execute(
            "SELECT COALESCE(SUM(quantity),0) FROM inventory_movements WHERE batch_id=?",
            (reservation["batch_id"],),
        ).fetchone()[0])
        if on_hand + 1e-9 < consume:
            raise InventoryError(f"批次{reservation['batch_no']}账面库存不足，发货已停止", 409)
        conn.execute(
            "INSERT INTO fulfillment_shipment_items(shipment_id,order_item_id,batch_id,quantity,created_at) VALUES(?,?,?,?,?)",
            (shipment_id, reservation["order_item_id"], reservation["batch_id"], consume, timestamp),
        )
        conn.execute(
            """
            INSERT INTO inventory_movements(product_id,warehouse_id,batch_id,movement_type,quantity,
              unit_cost,reference_type,reference_id,idempotency_key,operator,note,created_at)
            VALUES(?,?,?,'销售出库',?,?, 'shipment',?,?, '系统','订单发货自动扣减',?)
            """,
            (reservation["product_id"], reservation["warehouse_id"], reservation["batch_id"], -consume,
             reservation["unit_cost"], str(shipment_id), f"shipment:{shipment_id}:reservation:{reservation['id']}", timestamp),
        )
        original = float(reservation["quantity"])
        conn.execute(
            "UPDATE stock_reservations SET quantity=?,status='已核销',updated_at=? WHERE id=?",
            (consume, timestamp, reservation["id"]),
        )
        if original - consume > 1e-9:
            conn.execute(
                """
                INSERT INTO stock_reservations(order_id,order_item_id,product_id,warehouse_id,batch_id,
                  quantity,status,created_at,updated_at) VALUES(?,?,?,?,?,?,'生效',?,?)
                """,
                (reservation["order_id"], reservation["order_item_id"], reservation["product_id"],
                 reservation["warehouse_id"], reservation["batch_id"], original - consume, timestamp, timestamp),
            )
        conn.execute(
            "UPDATE order_items SET fulfilled_qty=fulfilled_qty+? WHERE id=?",
            (consume, reservation["order_item_id"]),
        )
        affected_products.add(int(reservation["product_id"]))
        left -= consume

    outstanding = float(conn.execute(
        "SELECT COALESCE(SUM(quantity-fulfilled_qty),0) FROM order_items WHERE order_id=?", (order_id,)
    ).fetchone()[0])
    full = outstanding <= 1e-9
    conn.execute(
        """
        UPDATE orders SET status=?,inventory_status=?,fulfillment_status=?,shipped_at=?,updated_at=?
        WHERE id=?
        """,
        ("已发货" if full else "部分发货", "已核销" if full else "部分核销",
         "已全部发货" if full else "部分发货", timestamp if full else order["shipped_at"], timestamp, order_id),
    )
    for product_id in affected_products:
        _sync_product_stock(conn, product_id, timestamp)
    audit_func(conn, "库存扣减并发货", "fulfillment_shipment", shipment_id, f"{shipment_no}/{tracking_no}/qty={target_qty:g}")
    return {
        "id": shipment_id, "shipment_no": shipment_no, "tracking_no": tracking_no,
        "shipped_quantity": target_qty, "order_status": "已发货" if full else "部分发货",
    }


def register_inventory_routes(flask_app, db_factory, now_func, audit_func) -> None:
    def handle_error(exc: InventoryError):
        return jsonify({"error": str(exc)}), exc.status_code

    @flask_app.get("/api/inventory/overview")
    def inventory_overview():
        today = date.today().strftime("%Y-%m-%d")
        expiring = (date.today() + timedelta(days=3)).strftime("%Y-%m-%d")
        with db_factory() as conn:
            rows = conn.execute(
                """
                WITH stock AS (
                  SELECT b.product_id,b.warehouse_id,b.quality_status,b.expiry_date,b.unit_cost,
                         COALESCE(SUM(m.quantity),0) on_hand
                  FROM inventory_batches b LEFT JOIN inventory_movements m ON m.batch_id=b.id
                  GROUP BY b.id
                ), reserved AS (
                  SELECT product_id,warehouse_id,COALESCE(SUM(quantity),0) qty
                  FROM stock_reservations WHERE status='生效' GROUP BY product_id,warehouse_id
                ), incoming AS (
                  SELECT i.product_id,p.warehouse_id,COALESCE(SUM(i.ordered_qty-i.received_qty),0) qty
                  FROM purchase_order_items i JOIN purchase_orders p ON p.id=i.purchase_order_id
                  WHERE p.status IN ('已审批','部分到货') GROUP BY i.product_id,p.warehouse_id
                )
                SELECT p.id product_id,p.sku,p.name,p.unit,p.status,w.id warehouse_id,w.name warehouse_name,
                  COALESCE(SUM(CASE WHEN s.quality_status='可售' AND (s.expiry_date IS NULL OR s.expiry_date>=?) THEN s.on_hand ELSE 0 END),0) sellable_on_hand,
                  COALESCE(SUM(CASE WHEN s.quality_status='待质检' THEN s.on_hand ELSE 0 END),0) quarantine_qty,
                  COALESCE(SUM(CASE WHEN s.expiry_date<? THEN s.on_hand ELSE 0 END),0) expired_qty,
                  COALESCE(r.qty,0) reserved_qty,
                  MAX(0,COALESCE(SUM(CASE WHEN s.quality_status='可售' AND (s.expiry_date IS NULL OR s.expiry_date>=?) THEN s.on_hand ELSE 0 END),0)-COALESCE(r.qty,0)) available_qty,
                  COALESCE(inc.qty,0) incoming_qty,
                  COALESCE(ps.reorder_point,10) reorder_point,
                  COALESCE(SUM(CASE WHEN s.quality_status='可售' THEN s.on_hand*s.unit_cost ELSE 0 END),0) stock_value
                FROM products p CROSS JOIN warehouses w
                LEFT JOIN stock s ON s.product_id=p.id AND s.warehouse_id=w.id
                LEFT JOIN reserved r ON r.product_id=p.id AND r.warehouse_id=w.id
                LEFT JOIN incoming inc ON inc.product_id=p.id AND inc.warehouse_id=w.id
                LEFT JOIN product_inventory_settings ps ON ps.product_id=p.id
                WHERE w.status='启用'
                GROUP BY p.id,w.id ORDER BY p.id,w.id
                """,
                (today, today, today),
            ).fetchall()
            batches = conn.execute(
                """
                SELECT b.*,p.name product_name,p.sku,p.unit,w.name warehouse_name,s.name supplier_name,
                  COALESCE(SUM(m.quantity),0) on_hand,
                  COALESCE((SELECT SUM(quantity) FROM stock_reservations r WHERE r.batch_id=b.id AND r.status='生效'),0) reserved_qty
                FROM inventory_batches b JOIN products p ON p.id=b.product_id
                JOIN warehouses w ON w.id=b.warehouse_id LEFT JOIN suppliers s ON s.id=b.supplier_id
                LEFT JOIN inventory_movements m ON m.batch_id=b.id
                GROUP BY b.id ORDER BY b.id DESC LIMIT 200
                """
            ).fetchall()
            purchase_summary = conn.execute(
                "SELECT status,COUNT(*) count FROM purchase_orders GROUP BY status"
            ).fetchall()
        items = [dict(row) for row in rows]
        metrics = {
            "available": sum(float(row["available_qty"]) for row in rows),
            "reserved": sum(float(row["reserved_qty"]) for row in rows),
            "quarantine": sum(float(row["quarantine_qty"]) for row in rows),
            "incoming": sum(float(row["incoming_qty"]) for row in rows),
            "stock_value": round(sum(float(row["stock_value"]) for row in rows), 2),
            "low_stock": sum(1 for row in rows if float(row["available_qty"]) < float(row["reorder_point"])),
            "expiring_batches": sum(1 for row in batches if row["expiry_date"] and today <= row["expiry_date"] <= expiring and float(row["on_hand"]) > 0),
        }
        return jsonify({
            "metrics": metrics, "inventory": items, "batches": [dict(row) for row in batches],
            "purchase_summary": [dict(row) for row in purchase_summary],
        })

    @flask_app.get("/api/inventory/suppliers")
    def list_suppliers():
        with db_factory() as conn:
            return jsonify([dict(row) for row in conn.execute("SELECT * FROM suppliers ORDER BY status,name").fetchall()])

    @flask_app.post("/api/inventory/suppliers")
    def create_supplier():
        payload = request.get_json(silent=True) or {}
        name = str(payload.get("name") or "").strip()
        if not name:
            return jsonify({"error": "供应商名称不能为空"}), 400
        code = str(payload.get("code") or _identifier("SUP-")).strip().upper()
        with db_factory() as conn:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO suppliers(code,name,contact_name,phone,payment_terms,lead_time_days,status,notes,created_at,updated_at)
                    VALUES(?,?,?,?,?,?, '启用',?,?,?)
                    """,
                    (code, name, str(payload.get("contact_name") or "")[:80], str(payload.get("phone") or "")[:40],
                     str(payload.get("payment_terms") or "现款")[:80], max(0, int(payload.get("lead_time_days") or 3)),
                     str(payload.get("notes") or "")[:500], now_func(), now_func()),
                )
            except sqlite3.IntegrityError:
                return jsonify({"error": "供应商编码已存在"}), 409
            audit_func(conn, "新增供应商", "supplier", cur.lastrowid, f"{code}/{name}")
        return jsonify({"id": cur.lastrowid, "code": code}), 201

    @flask_app.get("/api/inventory/warehouses")
    def list_warehouses():
        with db_factory() as conn:
            return jsonify([dict(row) for row in conn.execute("SELECT * FROM warehouses ORDER BY is_default DESC,id").fetchall()])

    @flask_app.post("/api/inventory/warehouses")
    def create_warehouse():
        payload = request.get_json(silent=True) or {}
        name = str(payload.get("name") or "").strip()
        if not name:
            return jsonify({"error": "仓库名称不能为空"}), 400
        code = str(payload.get("code") or _identifier("WH-")).strip().upper()
        with db_factory() as conn:
            try:
                cur = conn.execute(
                    "INSERT INTO warehouses(code,name,location,status,is_default,created_at,updated_at) VALUES(?,?,?,'启用',0,?,?)",
                    (code, name, str(payload.get("location") or "")[:200], now_func(), now_func()),
                )
            except sqlite3.IntegrityError:
                return jsonify({"error": "仓库编码已存在"}), 409
            audit_func(conn, "新增仓库", "warehouse", cur.lastrowid, f"{code}/{name}")
        return jsonify({"id": cur.lastrowid, "code": code}), 201

    @flask_app.get("/api/inventory/purchase-orders")
    def list_purchase_orders():
        with db_factory() as conn:
            rows = conn.execute(
                """
                SELECT po.*,s.name supplier_name,w.name warehouse_name,
                  GROUP_CONCAT(p.name||' ×'||i.ordered_qty,'；') item_summary,
                  COALESCE(SUM(i.ordered_qty),0) ordered_qty,COALESCE(SUM(i.received_qty),0) received_qty
                FROM purchase_orders po JOIN suppliers s ON s.id=po.supplier_id
                JOIN warehouses w ON w.id=po.warehouse_id
                JOIN purchase_order_items i ON i.purchase_order_id=po.id
                JOIN products p ON p.id=i.product_id
                GROUP BY po.id ORDER BY po.id DESC
                """
            ).fetchall()
        return jsonify([dict(row) for row in rows])

    @flask_app.post("/api/inventory/purchase-orders")
    def create_purchase_order():
        payload = request.get_json(silent=True) or {}
        items = payload.get("items") or []
        if not isinstance(items, list) or not items:
            return jsonify({"error": "采购单至少包含一条商品"}), 400
        try:
            supplier_id = int(payload.get("supplier_id"))
            warehouse_id = int(payload.get("warehouse_id"))
            expected_at = _parse_date(payload.get("expected_at"), "预计到货日期")
            prepared = []
            total = 0.0
            for item in items:
                product_id = int(item.get("product_id"))
                qty = _positive(item.get("quantity"), "采购数量")
                cost = _positive(item.get("unit_cost"), "采购单价")
                subtotal = round(qty * cost, 2)
                prepared.append((product_id, qty, cost, subtotal))
                total += subtotal
        except (TypeError, ValueError, InventoryError) as exc:
            return handle_error(exc if isinstance(exc, InventoryError) else InventoryError("采购单字段无效"))
        po_no = _identifier("PO")
        with db_factory() as conn:
            if not conn.execute("SELECT 1 FROM suppliers WHERE id=? AND status='启用'", (supplier_id,)).fetchone():
                return jsonify({"error": "供应商不存在或未启用"}), 404
            if not conn.execute("SELECT 1 FROM warehouses WHERE id=? AND status='启用'", (warehouse_id,)).fetchone():
                return jsonify({"error": "仓库不存在或未启用"}), 404
            for product_id, *_ in prepared:
                if not conn.execute("SELECT 1 FROM products WHERE id=?", (product_id,)).fetchone():
                    return jsonify({"error": f"商品{product_id}不存在"}), 404
            cur = conn.execute(
                """
                INSERT INTO purchase_orders(po_no,supplier_id,warehouse_id,status,payment_status,expected_at,total_amount,notes,created_at,updated_at)
                VALUES(?,?,?,'草稿','未付款',?,?,?, ?,?)
                """,
                (po_no, supplier_id, warehouse_id, expected_at, round(total, 2), str(payload.get("notes") or "")[:500], now_func(), now_func()),
            )
            po_id = int(cur.lastrowid)
            conn.executemany(
                "INSERT INTO purchase_order_items(purchase_order_id,product_id,ordered_qty,received_qty,unit_cost,subtotal,created_at) VALUES(?,?,?,0,?,?,?)",
                [(po_id, *item, now_func()) for item in prepared],
            )
            audit_func(conn, "创建采购单", "purchase_order", po_id, f"{po_no}/amount={total:.2f}")
        return jsonify({"id": po_id, "po_no": po_no, "total_amount": round(total, 2)}), 201

    @flask_app.post("/api/inventory/purchase-orders/<int:po_id>/approve")
    def approve_purchase_order(po_id: int):
        with db_factory() as conn:
            cur = conn.execute(
                "UPDATE purchase_orders SET status='已审批',approved_at=?,updated_at=? WHERE id=? AND status='草稿'",
                (now_func(), now_func(), po_id),
            )
            if not cur.rowcount:
                return jsonify({"error": "只有草稿采购单可以审批"}), 409
            audit_func(conn, "审批采购单", "purchase_order", po_id, "status=已审批")
        return jsonify({"ok": True, "status": "已审批"})

    @flask_app.get("/api/inventory/purchase-orders/<int:po_id>/items")
    def purchase_order_items(po_id: int):
        with db_factory() as conn:
            rows = conn.execute(
                "SELECT i.*,p.name product_name,p.sku,p.unit FROM purchase_order_items i JOIN products p ON p.id=i.product_id WHERE i.purchase_order_id=? ORDER BY i.id",
                (po_id,),
            ).fetchall()
        return jsonify([dict(row) for row in rows])

    @flask_app.post("/api/inventory/purchase-orders/<int:po_id>/receive")
    def receive_purchase_order(po_id: int):
        payload = request.get_json(silent=True) or {}
        try:
            item_id = int(payload.get("item_id"))
            quantity = _positive(payload.get("quantity"), "到货数量")
            production_date = _parse_date(payload.get("production_date"), "生产日期")
            expiry_date = _parse_date(payload.get("expiry_date"), "到期日期")
        except (TypeError, ValueError, InventoryError) as exc:
            return handle_error(exc if isinstance(exc, InventoryError) else InventoryError("到货字段无效"))
        if production_date and expiry_date and expiry_date <= production_date:
            return jsonify({"error": "到期日期必须晚于生产日期"}), 400
        # 收货与质检必须由两个独立动作完成，禁止到货时直接进入可售库存。
        requested_quality = str(payload.get("quality_status") or "待质检")
        if requested_quality != "待质检":
            return jsonify({"error": "采购到货只能进入待质检区，请在收货后单独完成质检"}), 400
        quality_status = "待质检"
        batch_no = str(payload.get("batch_no") or "").strip().upper()
        if not batch_no:
            return jsonify({"error": "批次号不能为空"}), 400
        with db_factory() as conn:
            conn.execute("BEGIN IMMEDIATE")
            po = conn.execute("SELECT * FROM purchase_orders WHERE id=?", (po_id,)).fetchone()
            item = conn.execute(
                "SELECT * FROM purchase_order_items WHERE id=? AND purchase_order_id=?", (item_id, po_id)
            ).fetchone()
            if not po or not item:
                return jsonify({"error": "采购单或采购明细不存在"}), 404
            if po["status"] not in {"已审批", "部分到货"}:
                return jsonify({"error": "采购单尚未审批或已经关闭"}), 409
            remaining = float(item["ordered_qty"]) - float(item["received_qty"])
            if quantity > remaining + 1e-9:
                return jsonify({"error": f"到货数量超过剩余可收数量{remaining:g}"}), 409
            if conn.execute("SELECT 1 FROM inventory_batches WHERE batch_no=?", (batch_no,)).fetchone():
                return jsonify({"error": "批次号已经存在，禁止重复入库"}), 409
            cur = conn.execute(
                """
                INSERT INTO inventory_batches(batch_no,product_id,warehouse_id,supplier_id,purchase_order_id,
                  received_qty,quality_status,production_date,expiry_date,unit_cost,source_note,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?, '采购到货',?,?)
                """,
                (batch_no, item["product_id"], po["warehouse_id"], po["supplier_id"], po_id,
                 quantity, quality_status, production_date, expiry_date, item["unit_cost"], now_func(), now_func()),
            )
            batch_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO inventory_movements(product_id,warehouse_id,batch_id,movement_type,quantity,unit_cost,
                  reference_type,reference_id,idempotency_key,operator,note,created_at)
                VALUES(?,?,?,'采购入库',?,?,'purchase_receipt',?,?, '系统',?,?)
                """,
                (item["product_id"], po["warehouse_id"], batch_id, quantity, item["unit_cost"],
                 str(po_id), f"purchase-receipt:{po_id}:{item_id}:{batch_no}", f"质检状态：{quality_status}", now_func()),
            )
            conn.execute("UPDATE purchase_order_items SET received_qty=received_qty+? WHERE id=?", (quantity, item_id))
            outstanding = float(conn.execute(
                "SELECT COALESCE(SUM(ordered_qty-received_qty),0) FROM purchase_order_items WHERE purchase_order_id=?", (po_id,)
            ).fetchone()[0])
            status = "已全部到货" if outstanding <= 1e-9 else "部分到货"
            conn.execute(
                "UPDATE purchase_orders SET status=?,received_at=CASE WHEN ?='已全部到货' THEN ? ELSE received_at END,updated_at=? WHERE id=?",
                (status, status, now_func(), now_func(), po_id),
            )
            _sync_product_stock(conn, int(item["product_id"]), now_func())
            audit_func(conn, "采购到货入库", "inventory_batch", batch_id, f"{batch_no}/qty={quantity:g}/{quality_status}")
        return jsonify({"id": batch_id, "batch_no": batch_no, "purchase_status": status}), 201

    @flask_app.patch("/api/inventory/batches/<int:batch_id>/quality")
    def update_batch_quality(batch_id: int):
        payload = request.get_json(silent=True) or {}
        status = str(payload.get("quality_status") or "")
        note = str(payload.get("note") or "").strip()
        if status not in {"可售", "拒收"}:
            return jsonify({"error": "质检状态无效"}), 400
        if not note:
            return jsonify({"error": "质检结论必须填写说明"}), 400
        with db_factory() as conn:
            batch = conn.execute("SELECT * FROM inventory_batches WHERE id=?", (batch_id,)).fetchone()
            if not batch:
                return jsonify({"error": "批次不存在"}), 404
            if batch["quality_status"] != "待质检":
                return jsonify({"error": "只有待质检批次可以做质检结论，已结论批次不得直接改写"}), 409
            if status == "可售" and batch["expiry_date"] and batch["expiry_date"] < date.today().strftime("%Y-%m-%d"):
                return jsonify({"error": "过期批次不能质检放行"}), 409
            conn.execute(
                "UPDATE inventory_batches SET quality_status=?,source_note=?,updated_at=? WHERE id=?",
                (status, note[:500], now_func(), batch_id),
            )
            _sync_product_stock(conn, int(batch["product_id"]), now_func())
            audit_func(conn, "更新批次质检", "inventory_batch", batch_id, status)
        return jsonify({"ok": True, "quality_status": status})

    @flask_app.get("/api/inventory/movements")
    def list_inventory_movements():
        limit = max(1, min(500, int(request.args.get("limit", 200))))
        with db_factory() as conn:
            rows = conn.execute(
                """
                SELECT m.*,p.name product_name,p.sku,p.unit,w.name warehouse_name,b.batch_no
                FROM inventory_movements m JOIN products p ON p.id=m.product_id
                JOIN warehouses w ON w.id=m.warehouse_id LEFT JOIN inventory_batches b ON b.id=m.batch_id
                ORDER BY m.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return jsonify([dict(row) for row in rows])

    @flask_app.post("/api/inventory/orders/<int:order_id>/reserve")
    def reserve_order(order_id: int):
        payload = request.get_json(silent=True) or {}
        with db_factory() as conn:
            conn.execute("BEGIN IMMEDIATE")
            order = conn.execute("SELECT warehouse_id FROM orders WHERE id=?", (order_id,)).fetchone()
            if not order:
                return jsonify({"error": "订单不存在"}), 404
            warehouse_id = int(payload.get("warehouse_id") or order["warehouse_id"] or 0)
            try:
                result = reserve_order_stock(conn, order_id, warehouse_id, now_func())
            except InventoryError as exc:
                return handle_error(exc)
            audit_func(conn, "销售订单占库", "order", order_id, f"warehouse={warehouse_id},qty={result['reserved']:g}")
        return jsonify(result)

    @flask_app.post("/api/inventory/orders/<int:order_id>/release")
    def release_order_stock(order_id: int):
        with db_factory() as conn:
            conn.execute("BEGIN IMMEDIATE")
            product_rows = conn.execute(
                "SELECT DISTINCT product_id FROM stock_reservations WHERE order_id=? AND status='生效'", (order_id,)
            ).fetchall()
            conn.execute(
                "UPDATE stock_reservations SET status='已释放',updated_at=? WHERE order_id=? AND status='生效'",
                (now_func(), order_id),
            )
            conn.execute(
                "UPDATE orders SET inventory_status='已释放',stock_reserved_at=NULL,status='待占库',updated_at=? WHERE id=?",
                (now_func(), order_id),
            )
            for row in product_rows:
                _sync_product_stock(conn, int(row["product_id"]), now_func())
            audit_func(conn, "释放销售占库", "order", order_id, "operator")
        return jsonify({"ok": True})

    @flask_app.post("/api/inventory/shipments")
    def create_inventory_shipment():
        payload = request.get_json(silent=True) or {}
        with db_factory() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = create_fulfillment_shipment(conn, payload, now_func(), audit_func)
            except (InventoryError, sqlite3.IntegrityError) as exc:
                if isinstance(exc, sqlite3.IntegrityError):
                    return jsonify({"error": "运单号已经存在，禁止重复发货"}), 409
                return handle_error(exc)
        return jsonify(result), 201

    @flask_app.get("/api/inventory/shipments")
    def list_inventory_shipments():
        with db_factory() as conn:
            rows = conn.execute(
                """
                SELECT s.*,o.order_no,l.store_name,w.name warehouse_name,
                  COALESCE(SUM(i.quantity),0) shipped_quantity
                FROM fulfillment_shipments s JOIN orders o ON o.id=s.order_id
                JOIN leads l ON l.id=o.lead_id JOIN warehouses w ON w.id=s.warehouse_id
                LEFT JOIN fulfillment_shipment_items i ON i.shipment_id=s.id
                GROUP BY s.id ORDER BY s.id DESC
                """
            ).fetchall()
        return jsonify([dict(row) for row in rows])

    @flask_app.patch("/api/inventory/shipments/<int:shipment_id>")
    def update_inventory_shipment(shipment_id: int):
        payload = request.get_json(silent=True) or {}
        status = str(payload.get("status") or "")
        if status not in {"已发出", "运输中", "已签收", "物流异常"}:
            return jsonify({"error": "物流状态无效"}), 400
        with db_factory() as conn:
            cur = conn.execute(
                """
                UPDATE fulfillment_shipments SET status=?,delivered_at=CASE WHEN ?='已签收' THEN ? ELSE delivered_at END,
                  exception_note=?,updated_at=? WHERE id=?
                """,
                (status, status, now_func(), str(payload.get("exception_note") or "")[:500], now_func(), shipment_id),
            )
            if not cur.rowcount:
                return jsonify({"error": "发货单不存在"}), 404
            audit_func(conn, "更新物流状态", "fulfillment_shipment", shipment_id, status)
        return jsonify({"ok": True, "status": status})

    @flask_app.get("/api/inventory/adjustments")
    def list_adjustments():
        with db_factory() as conn:
            rows = conn.execute(
                """
                SELECT a.*,p.name product_name,p.sku,p.unit,w.name warehouse_name,b.batch_no
                FROM inventory_adjustments a JOIN products p ON p.id=a.product_id
                JOIN warehouses w ON w.id=a.warehouse_id LEFT JOIN inventory_batches b ON b.id=a.batch_id
                ORDER BY a.id DESC
                """
            ).fetchall()
        return jsonify([dict(row) for row in rows])

    @flask_app.post("/api/inventory/adjustments")
    def create_adjustment():
        payload = request.get_json(silent=True) or {}
        try:
            product_id = int(payload.get("product_id"))
            warehouse_id = int(payload.get("warehouse_id"))
            batch_id = int(payload["batch_id"]) if payload.get("batch_id") else None
            delta = round(float(payload.get("quantity_delta")), 4)
        except (TypeError, ValueError):
            return jsonify({"error": "盘点调整字段无效"}), 400
        reason = str(payload.get("reason") or "").strip()
        if delta == 0 or not reason:
            return jsonify({"error": "调整数量不能为0且必须填写原因"}), 400
        if delta < 0 and not batch_id:
            return jsonify({"error": "减少库存必须指定批次"}), 400
        number = _identifier("ADJ")
        with db_factory() as conn:
            cur = conn.execute(
                """
                INSERT INTO inventory_adjustments(adjustment_no,product_id,warehouse_id,batch_id,quantity_delta,
                  reason,status,requested_by,created_at,updated_at) VALUES(?,?,?,?,?,?,'待审批','操作员',?,?)
                """,
                (number, product_id, warehouse_id, batch_id, delta, reason[:500], now_func(), now_func()),
            )
            audit_func(conn, "提交库存调整", "inventory_adjustment", cur.lastrowid, f"{number}/delta={delta:g}")
        return jsonify({"id": cur.lastrowid, "adjustment_no": number}), 201

    @flask_app.post("/api/inventory/adjustments/<int:adjustment_id>/approve")
    def approve_adjustment(adjustment_id: int):
        with db_factory() as conn:
            conn.execute("BEGIN IMMEDIATE")
            adjustment = conn.execute("SELECT * FROM inventory_adjustments WHERE id=?", (adjustment_id,)).fetchone()
            if not adjustment or adjustment["status"] != "待审批":
                return jsonify({"error": "调整单不存在或已处理"}), 409
            batch_id = adjustment["batch_id"]
            if float(adjustment["quantity_delta"]) < 0:
                on_hand = float(conn.execute(
                    "SELECT COALESCE(SUM(quantity),0) FROM inventory_movements WHERE batch_id=?", (batch_id,)
                ).fetchone()[0])
                reserved = float(conn.execute(
                    "SELECT COALESCE(SUM(quantity),0) FROM stock_reservations WHERE batch_id=? AND status='生效'", (batch_id,)
                ).fetchone()[0])
                if on_hand - reserved + float(adjustment["quantity_delta"]) < -1e-9:
                    return jsonify({"error": "调整会侵占已锁定库存或造成负库存"}), 409
            if not batch_id:
                batch_no = _identifier("ADJ-BATCH-")
                cur = conn.execute(
                    """
                    INSERT INTO inventory_batches(batch_no,product_id,warehouse_id,received_qty,quality_status,
                      unit_cost,source_note,created_at,updated_at)
                    VALUES(?,?,?,?, '可售',0,'盘盈调整',?,?)
                    """,
                    (batch_no, adjustment["product_id"], adjustment["warehouse_id"], adjustment["quantity_delta"], now_func(), now_func()),
                )
                batch_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO inventory_movements(product_id,warehouse_id,batch_id,movement_type,quantity,unit_cost,
                  reference_type,reference_id,idempotency_key,operator,note,created_at)
                VALUES(?,?,?,'盘点调整',?,0,'inventory_adjustment',?,?, '审批人',?,?)
                """,
                (adjustment["product_id"], adjustment["warehouse_id"], batch_id, adjustment["quantity_delta"],
                 str(adjustment_id), f"inventory-adjustment:{adjustment_id}", adjustment["reason"], now_func()),
            )
            conn.execute(
                "UPDATE inventory_adjustments SET batch_id=?,status='已审批',approved_by='管理员',approved_at=?,updated_at=? WHERE id=?",
                (batch_id, now_func(), now_func(), adjustment_id),
            )
            _sync_product_stock(conn, int(adjustment["product_id"]), now_func())
            audit_func(conn, "审批库存调整", "inventory_adjustment", adjustment_id, f"delta={adjustment['quantity_delta']:g}")
        return jsonify({"ok": True, "status": "已审批", "batch_id": batch_id})
