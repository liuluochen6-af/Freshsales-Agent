from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Callable

from flask import jsonify, request


DEFAULT_MANAGED_CONVERSATIONS = 500
MAX_MANAGED_CONVERSATIONS = 5000


OPERATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS worker_nodes (
    node_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    machine_name TEXT DEFAULT '',
    account_ref TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    status TEXT DEFAULT 'offline',
    version TEXT DEFAULT '',
    capabilities_json TEXT DEFAULT '{}',
    max_active_conversations INTEGER DEFAULT 500,
    active_conversations INTEGER DEFAULT 0,
    dry_run INTEGER DEFAULT 1,
    last_error TEXT DEFAULT '',
    last_heartbeat TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_bindings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    account_ref TEXT NOT NULL,
    conversation_id INTEGER NOT NULL,
    contact_ref TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    auto_reply INTEGER DEFAULT 1,
    priority INTEGER DEFAULT 50,
    last_inbound_at TEXT,
    last_outbound_at TEXT,
    last_error TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(node_id) REFERENCES worker_nodes(node_id),
    FOREIGN KEY(conversation_id) REFERENCES conversations(id),
    UNIQUE(node_id, account_ref, conversation_id),
    UNIQUE(node_id, account_ref, contact_ref)
);

CREATE TABLE IF NOT EXISTS worker_inbound_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    account_ref TEXT NOT NULL,
    contact_ref TEXT NOT NULL,
    conversation_id INTEGER NOT NULL,
    external_message_id TEXT NOT NULL,
    content TEXT NOT NULL,
    observed_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(node_id) REFERENCES worker_nodes(node_id),
    FOREIGN KEY(conversation_id) REFERENCES conversations(id),
    UNIQUE(node_id, account_ref, external_message_id)
);

CREATE TABLE IF NOT EXISTS message_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    account_ref TEXT NOT NULL,
    conversation_id INTEGER NOT NULL,
    contact_ref TEXT NOT NULL,
    kind TEXT DEFAULT 'send_message',
    payload_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT DEFAULT 'queued',
    priority INTEGER DEFAULT 50,
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 1,
    available_at TEXT NOT NULL,
    leased_until TEXT,
    lease_token TEXT,
    last_error TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY(node_id) REFERENCES worker_nodes(node_id),
    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);
CREATE INDEX IF NOT EXISTS idx_message_jobs_pull
    ON message_jobs(node_id, status, available_at, priority, id);

CREATE TABLE IF NOT EXISTS worker_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT DEFAULT 'info',
    detail TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(node_id) REFERENCES worker_nodes(node_id)
);
CREATE INDEX IF NOT EXISTS idx_worker_events_node ON worker_events(node_id, id DESC);
"""


NODE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,63}$")
STOP_CONTACT_TERMS = ("不要联系", "不要再联系", "别联系", "别再联系", "不再联系", "别再发", "不要再发", "停止联系", "把我删了", "删掉我", "删除我的联系方式", "删除我信息", "不需要了", "别打扰")
SOURCE_DENIAL_TERMS = ("我没登记", "我没有登记", "我没参加", "我没有参加", "我不认识", "我没授权", "我没有授权", "我没同意", "我没有同意", "没在网站登记", "没有在网站登记", "记录不对", "来源不对", "你说的不对")
PRIVACY_COMPLAINT_TERMS = ("信息泄露", "泄露隐私", "侵犯隐私", "非法获取", "买卖信息", "买的名单", "举报你们", "投诉你们")


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _bearer_token() -> str:
    value = request.headers.get("Authorization", "")
    return value[7:].strip() if value.lower().startswith("bearer ") else ""


def _json_detail(value: Any, limit: int = 1000) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:limit]


def register_operations_routes(
    flask_app,
    db_factory,
    now_func: Callable[[], str],
    audit_func,
    suggestion_builder: Callable[[int], dict[str, Any] | None],
) -> None:
    def authenticate_node(conn: sqlite3.Connection):
        node_id = (request.headers.get("X-Node-ID") or "").strip()
        token = _bearer_token()
        if not node_id or not token:
            return None
        node = conn.execute("SELECT * FROM worker_nodes WHERE node_id=?", (node_id,)).fetchone()
        if not node or not hmac.compare_digest(node["token_hash"], _token_hash(token)):
            return None
        return node

    def record_event(conn, node_id: str, event_type: str, detail: Any = "", severity: str = "info"):
        conn.execute(
            "INSERT INTO worker_events(node_id,event_type,severity,detail,created_at) VALUES(?,?,?,?,?)",
            (node_id, event_type[:60], severity[:20], _json_detail(detail), now_func()),
        )

    def enqueue_message(
        conn,
        *,
        node_id: str,
        account_ref: str,
        conversation_id: int,
        contact_ref: str,
        content: str,
        idempotency_key: str,
        basis: str = "",
        sources: list[str] | None = None,
        priority: int = 50,
    ) -> int:
        payload = {
            "content": content,
            "basis": basis,
            "sources": sources or [],
        }
        conn.execute(
            """
            INSERT OR IGNORE INTO message_jobs(
                node_id,account_ref,conversation_id,contact_ref,kind,payload_json,
                idempotency_key,status,priority,attempts,max_attempts,available_at,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                node_id, account_ref, conversation_id, contact_ref, "send_message",
                _json_detail(payload, 5000), idempotency_key, "queued",
                max(0, min(100, int(priority))), 0, 1, now_func(), now_func(), now_func(),
            ),
        )
        row = conn.execute("SELECT id FROM message_jobs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        return int(row["id"])

    @flask_app.post("/api/worker/register")
    def worker_register():
        payload = request.get_json(silent=True) or {}
        node_id = str(payload.get("node_id") or "").strip()
        if not NODE_ID_RE.fullmatch(node_id):
            return jsonify({"error": "node_id只能包含字母、数字、点、下划线和短横线，长度3-64"}), 400
        account_ref = str(payload.get("account_ref") or "").strip()
        if not account_ref or len(account_ref) > 100:
            return jsonify({"error": "account_ref不能为空且不能超过100字符"}), 400

        expected_bootstrap = os.environ.get("AGENT_BOOTSTRAP_TOKEN", "").strip()
        supplied_bootstrap = request.headers.get("X-Bootstrap-Token", "").strip()
        remote = request.remote_addr or ""
        # A worker running on the same computer as the coordinator may enroll
        # itself. Remote computers still need the one-time bootstrap token.
        local_bootstrap = remote in {"127.0.0.1", "::1", "localhost"}

        with db_factory() as conn:
            existing = conn.execute("SELECT * FROM worker_nodes WHERE node_id=?", (node_id,)).fetchone()
            active_conflict = conn.execute(
                """
                SELECT node_id FROM worker_nodes
                WHERE node_id<>? AND account_ref=? AND status IN ('online','degraded')
                  AND last_heartbeat>=?
                LIMIT 1
                """,
                (node_id, account_ref, (datetime.now() - timedelta(seconds=45)).strftime("%Y-%m-%d %H:%M:%S")),
            ).fetchone()
            if active_conflict:
                return jsonify({"error": f"微信账号标识已由在线节点{active_conflict['node_id']}占用"}), 409
            bearer_ok = bool(
                existing and _bearer_token()
                and hmac.compare_digest(existing["token_hash"], _token_hash(_bearer_token()))
            )
            bootstrap_ok = bool(expected_bootstrap and hmac.compare_digest(expected_bootstrap, supplied_bootstrap))
            if not (bearer_ok or bootstrap_ok or (local_bootstrap and not existing)):
                if not expected_bootstrap and not local_bootstrap:
                    return jsonify({"error": "中央端未设置AGENT_BOOTSTRAP_TOKEN，拒绝远程节点注册"}), 503
                return jsonify({"error": "节点注册凭证无效"}), 401

            token = secrets.token_urlsafe(32) if not bearer_ok else _bearer_token()
            try:
                requested_capacity = int(
                    payload.get("max_active_conversations") or DEFAULT_MANAGED_CONVERSATIONS
                )
            except (TypeError, ValueError):
                return jsonify({"error": "会话管理容量必须是整数"}), 400
            max_active = max(1, min(MAX_MANAGED_CONVERSATIONS, requested_capacity))
            capabilities = payload.get("capabilities") or {}
            fields = (
                str(payload.get("display_name") or node_id)[:100],
                str(payload.get("machine_name") or "")[:100],
                account_ref,
                _token_hash(token),
                "online",
                str(payload.get("version") or "")[:40],
                _json_detail(capabilities, 3000),
                max_active,
                1 if payload.get("dry_run", True) else 0,
                "",
                now_func(),
                now_func(),
            )
            if existing:
                conn.execute(
                    """
                    UPDATE worker_nodes SET display_name=?,machine_name=?,account_ref=?,token_hash=?,
                      status=?,version=?,capabilities_json=?,max_active_conversations=?,dry_run=?,
                      last_error=?,last_heartbeat=?,updated_at=? WHERE node_id=?
                    """,
                    (*fields, node_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO worker_nodes(node_id,display_name,machine_name,account_ref,token_hash,
                      status,version,capabilities_json,max_active_conversations,dry_run,last_error,
                      last_heartbeat,updated_at,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (node_id, *fields, now_func()),
                )
            record_event(conn, node_id, "registered", {"account_ref": account_ref, "dry_run": bool(payload.get("dry_run", True))})
            audit_func(conn, "工作节点注册", "worker_node", node_id, f"account={account_ref},max={max_active}")
        return jsonify({
            "ok": True,
            "node_id": node_id,
            "node_token": token,
            "heartbeat_seconds": 15,
            "server_time": now_func(),
        })

    @flask_app.post("/api/worker/heartbeat")
    def worker_heartbeat():
        payload = request.get_json(silent=True) or {}
        with db_factory() as conn:
            node = authenticate_node(conn)
            if not node:
                return jsonify({"error": "节点认证失败"}), 401
            active = max(0, min(int(node["max_active_conversations"]), int(payload.get("active_conversations") or 0)))
            error = str(payload.get("last_error") or "")[:500]
            conn.execute(
                "UPDATE worker_nodes SET status=?,active_conversations=?,dry_run=?,last_error=?,last_heartbeat=?,updated_at=? WHERE node_id=?",
                ("degraded" if error else "online", active, 1 if payload.get("dry_run", node["dry_run"]) else 0,
                 error, now_func(), now_func(), node["node_id"]),
            )
        return jsonify({"ok": True, "server_time": now_func()})

    @flask_app.post("/api/worker/events")
    def worker_report_event():
        payload = request.get_json(silent=True) or {}
        with db_factory() as conn:
            node = authenticate_node(conn)
            if not node:
                return jsonify({"error": "节点认证失败"}), 401
            record_event(
                conn, node["node_id"], str(payload.get("event_type") or "worker_event"),
                payload.get("detail") or "", str(payload.get("severity") or "info"),
            )
        return jsonify({"ok": True})

    @flask_app.get("/api/worker/sessions")
    def worker_sessions():
        with db_factory() as conn:
            node = authenticate_node(conn)
            if not node:
                return jsonify({"error": "节点认证失败"}), 401
            rows = conn.execute(
                """
                SELECT b.id,b.conversation_id,b.account_ref,b.contact_ref,b.auto_reply,b.priority,
                       b.last_inbound_at,b.last_outbound_at,l.store_name,c.human_takeover
                FROM chat_bindings b
                JOIN conversations c ON c.id=b.conversation_id
                JOIN leads l ON l.id=c.lead_id
                WHERE b.node_id=? AND b.enabled=1
                ORDER BY b.priority DESC,b.id
                LIMIT ?
                """,
                (node["node_id"], int(node["max_active_conversations"])),
            ).fetchall()
        return jsonify({"sessions": [dict(row) for row in rows], "max_active": int(node["max_active_conversations"])})

    @flask_app.post("/api/worker/inbound")
    def worker_inbound():
        payload = request.get_json(silent=True) or {}
        contact_ref = str(payload.get("contact_ref") or "").strip()
        external_id = str(payload.get("external_message_id") or "").strip()
        content = str(payload.get("content") or "").strip()
        account_ref = str(payload.get("account_ref") or "").strip()
        if not contact_ref or not external_id or not content:
            return jsonify({"error": "contact_ref、external_message_id和content不能为空"}), 400
        if len(content) > 2000 or len(external_id) > 160:
            return jsonify({"error": "消息内容或唯一编号过长"}), 400

        with db_factory() as conn:
            node = authenticate_node(conn)
            if not node:
                return jsonify({"error": "节点认证失败"}), 401
            if account_ref != node["account_ref"]:
                return jsonify({"error": "消息账号与节点绑定账号不一致"}), 409
            binding = conn.execute(
                """
                SELECT * FROM chat_bindings
                WHERE node_id=? AND account_ref=? AND contact_ref=? AND enabled=1
                """,
                (node["node_id"], account_ref, contact_ref),
            ).fetchone()
            if not binding:
                return jsonify({"error": "该联系人未绑定到此节点"}), 404
            duplicate = conn.execute(
                "SELECT id FROM worker_inbound_events WHERE node_id=? AND account_ref=? AND external_message_id=?",
                (node["node_id"], account_ref, external_id),
            ).fetchone()
            if duplicate:
                return jsonify({"ok": True, "duplicate": True, "event_id": duplicate["id"]})
            cur = conn.execute(
                """
                INSERT INTO worker_inbound_events(node_id,account_ref,contact_ref,conversation_id,
                  external_message_id,content,observed_at,created_at) VALUES(?,?,?,?,?,?,?,?)
                """,
                (node["node_id"], account_ref, contact_ref, binding["conversation_id"],
                 external_id, content, str(payload.get("observed_at") or "")[:30], now_func()),
            )
            event_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO messages(conversation_id,sender,content,message_type,created_at) VALUES(?,?,?,?,?)",
                (binding["conversation_id"], "customer", content, f"worker-in:{event_id}", now_func()),
            )
            stop_request = _contains_any(content, STOP_CONTACT_TERMS)
            source_denial = _contains_any(content, SOURCE_DENIAL_TERMS)
            privacy_complaint = _contains_any(content, PRIVACY_COMPLAINT_TERMS)
            sentiment = "负面" if any(k in content for k in ("投诉", "太差", "退款", "赔偿")) or privacy_complaint else "中性"
            takeover = 1 if sentiment == "负面" or source_denial or stop_request or any(k in content for k in ("账期", "合同", "退款", "赔偿")) else 0
            conn.execute(
                "UPDATE conversations SET unread=unread+1,sentiment=?,intent=?,human_takeover=MAX(human_takeover,?),updated_at=? WHERE id=?",
                (sentiment, "停止联系" if stop_request else ("隐私与来源异议" if source_denial or privacy_complaint else "待识别"),
                 takeover, now_func(), binding["conversation_id"]),
            )
            if stop_request:
                conn.execute(
                    "UPDATE leads SET stop_marketing=1,status='停止联系',updated_at=? WHERE id=(SELECT lead_id FROM conversations WHERE id=?)",
                    (now_func(), binding["conversation_id"]),
                )
                conn.execute(
                    "UPDATE chat_bindings SET auto_reply=0,last_error='客户要求停止联系',updated_at=? WHERE id=?",
                    (now_func(), binding["id"]),
                )
            takeover_state = bool(conn.execute(
                "SELECT human_takeover FROM conversations WHERE id=?", (binding["conversation_id"],)
            ).fetchone()[0])
            conn.execute(
                "UPDATE chat_bindings SET last_inbound_at=?,last_error=?,updated_at=? WHERE id=?",
                (now_func(), "客户要求停止联系" if stop_request else "", now_func(), binding["id"]),
            )
            record_event(conn, node["node_id"], "inbound_received", {"contact_ref": contact_ref, "event_id": event_id})
            audit_func(conn, "工作节点收到消息", "conversation", binding["conversation_id"], f"event={event_id}")
            conversation_id = int(binding["conversation_id"])
            auto_reply = bool(binding["auto_reply"]) and not takeover_state

        suggestion = suggestion_builder(conversation_id)
        job_id = None
        requires_human = takeover_state
        if suggestion:
            requires_human = requires_human or bool(suggestion.get("requires_human"))
        if auto_reply and suggestion and not requires_human and suggestion.get("suggestion"):
            with db_factory() as conn:
                conn.execute(
                    """
                    UPDATE message_jobs SET status='cancelled',last_error='被同一客户更新的消息合并',
                      completed_at=?,updated_at=?
                    WHERE conversation_id=? AND status='queued' AND idempotency_key LIKE 'auto-reply:%'
                    """,
                    (now_func(), now_func(), conversation_id),
                )
                job_id = enqueue_message(
                    conn,
                    node_id=node["node_id"], account_ref=account_ref,
                    conversation_id=conversation_id, contact_ref=contact_ref,
                    content=str(suggestion["suggestion"])[:500],
                    idempotency_key=f"auto-reply:{event_id}",
                    basis=str(suggestion.get("basis") or ""),
                    sources=list(suggestion.get("sources") or []),
                    priority=int(binding["priority"]),
                )
                record_event(conn, node["node_id"], "reply_queued", {"job_id": job_id, "event_id": event_id})
        elif auto_reply:
            with db_factory() as conn:
                reason = "回复需要人工确认" if requires_human else "未生成可靠回复"
                conn.execute(
                    "UPDATE chat_bindings SET last_error=?,updated_at=? WHERE id=?",
                    (reason, now_func(), binding["id"]),
                )
                record_event(conn, node["node_id"], "reply_paused", {"event_id": event_id, "reason": reason}, "warning")
        return jsonify({
            "ok": True,
            "duplicate": False,
            "event_id": event_id,
            "conversation_id": conversation_id,
            "reply_job_id": job_id,
            "requires_human": requires_human,
        })

    @flask_app.post("/api/worker/jobs/pull")
    def worker_pull_jobs():
        payload = request.get_json(silent=True) or {}
        limit = max(1, min(10, int(payload.get("limit") or 1)))
        lease_seconds = max(30, min(180, int(payload.get("lease_seconds") or 90)))
        with db_factory() as conn:
            conn.execute("BEGIN IMMEDIATE")
            node = authenticate_node(conn)
            if not node:
                return jsonify({"error": "节点认证失败"}), 401
            if node["dry_run"]:
                return jsonify({"jobs": [], "dry_run": True})
            conn.execute(
                """
                UPDATE message_jobs SET status='failed',last_error='任务租约过期；为防止重复发送不自动重试',
                  updated_at=?,completed_at=?
                WHERE node_id=? AND status='leased' AND leased_until<?
                """,
                (now_func(), now_func(), node["node_id"], now_func()),
            )
            rows = conn.execute(
                """
                SELECT * FROM message_jobs
                WHERE node_id=? AND status='queued' AND available_at<=?
                ORDER BY priority DESC,id LIMIT ?
                """,
                (node["node_id"], now_func(), limit),
            ).fetchall()
            jobs = []
            for row in rows:
                lease_token = secrets.token_urlsafe(24)
                leased_until = (datetime.now() + timedelta(seconds=lease_seconds)).strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    """
                    UPDATE message_jobs SET status='leased',attempts=attempts+1,lease_token=?,leased_until=?,updated_at=?
                    WHERE id=? AND status='queued'
                    """,
                    (lease_token, leased_until, now_func(), row["id"]),
                )
                jobs.append({
                    "id": row["id"], "kind": row["kind"], "conversation_id": row["conversation_id"],
                    "account_ref": row["account_ref"], "contact_ref": row["contact_ref"],
                    "payload": json.loads(row["payload_json"]), "lease_token": lease_token,
                    "leased_until": leased_until, "attempt": int(row["attempts"]) + 1,
                })
            if jobs:
                record_event(conn, node["node_id"], "jobs_leased", {"ids": [item["id"] for item in jobs]})
        return jsonify({"jobs": jobs, "dry_run": False})

    @flask_app.post("/api/worker/jobs/<int:job_id>/complete")
    def worker_complete_job(job_id: int):
        payload = request.get_json(silent=True) or {}
        outcome = str(payload.get("outcome") or "failed")
        lease_token = str(payload.get("lease_token") or "")
        with db_factory() as conn:
            node = authenticate_node(conn)
            if not node:
                return jsonify({"error": "节点认证失败"}), 401
            job = conn.execute("SELECT * FROM message_jobs WHERE id=? AND node_id=?", (job_id, node["node_id"])).fetchone()
            if not job:
                return jsonify({"error": "任务不存在"}), 404
            if job["status"] != "leased" or not hmac.compare_digest(str(job["lease_token"] or ""), lease_token):
                return jsonify({"error": "任务租约无效或已经处理"}), 409
            if outcome == "succeeded":
                conn.execute(
                    "UPDATE message_jobs SET status='succeeded',last_error='',completed_at=?,updated_at=? WHERE id=?",
                    (now_func(), now_func(), job_id),
                )
                payload_data = json.loads(job["payload_json"])
                message_type = f"worker-out:{job_id}"
                exists = conn.execute(
                    "SELECT id FROM messages WHERE conversation_id=? AND message_type=?",
                    (job["conversation_id"], message_type),
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO messages(conversation_id,sender,content,message_type,created_at) VALUES(?,?,?,?,?)",
                        (job["conversation_id"], "sales", str(payload_data.get("content") or ""), message_type, now_func()),
                    )
                conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now_func(), job["conversation_id"]))
                conn.execute(
                    "UPDATE chat_bindings SET last_outbound_at=?,last_error='',updated_at=? WHERE node_id=? AND account_ref=? AND contact_ref=?",
                    (now_func(), now_func(), node["node_id"], job["account_ref"], job["contact_ref"]),
                )
                record_event(conn, node["node_id"], "job_succeeded", {"job_id": job_id, "contact_ref": job["contact_ref"]})
                audit_func(conn, "工作节点发送成功", "message_job", job_id, f"conversation={job['conversation_id']}")
            else:
                error = str(payload.get("error") or "节点未确认发送成功")[:500]
                conn.execute(
                    "UPDATE message_jobs SET status='failed',last_error=?,completed_at=?,updated_at=? WHERE id=?",
                    (error, now_func(), now_func(), job_id),
                )
                conn.execute(
                    "UPDATE chat_bindings SET last_error=?,updated_at=? WHERE node_id=? AND account_ref=? AND contact_ref=?",
                    (error, now_func(), node["node_id"], job["account_ref"], job["contact_ref"]),
                )
                record_event(conn, node["node_id"], "job_failed", {"job_id": job_id, "error": error}, "error")
                audit_func(conn, "工作节点发送失败", "message_job", job_id, error)
        return jsonify({"ok": True, "status": "succeeded" if outcome == "succeeded" else "failed"})

    @flask_app.get("/api/operations/overview")
    def operations_overview():
        with db_factory() as conn:
            conn.execute(
                "UPDATE worker_nodes SET status='offline',updated_at=? WHERE last_heartbeat IS NULL OR last_heartbeat<?",
                (now_func(), (datetime.now() - timedelta(seconds=45)).strftime("%Y-%m-%d %H:%M:%S")),
            )
            nodes = conn.execute(
                """
                SELECT n.*,
                  (SELECT COUNT(*) FROM chat_bindings b WHERE b.node_id=n.node_id AND b.enabled=1) binding_count,
                  (SELECT COUNT(*) FROM message_jobs j WHERE j.node_id=n.node_id AND j.status IN ('queued','leased')) pending_jobs,
                  (SELECT COUNT(*) FROM message_jobs j WHERE j.node_id=n.node_id AND j.status='failed') failed_jobs
                FROM worker_nodes n ORDER BY n.updated_at DESC
                """
            ).fetchall()
            bindings = conn.execute(
                """
                SELECT b.*,n.display_name,l.store_name,c.human_takeover,
                  (SELECT COUNT(*) FROM message_jobs j WHERE j.conversation_id=b.conversation_id AND j.status IN ('queued','leased')) pending_jobs
                FROM chat_bindings b
                JOIN worker_nodes n ON n.node_id=b.node_id
                JOIN conversations c ON c.id=b.conversation_id
                JOIN leads l ON l.id=c.lead_id
                ORDER BY b.updated_at DESC
                """
            ).fetchall()
            jobs = conn.execute(
                "SELECT id,node_id,conversation_id,contact_ref,status,attempts,last_error,created_at,completed_at FROM message_jobs ORDER BY id DESC LIMIT 100"
            ).fetchall()
            events = conn.execute(
                "SELECT * FROM worker_events ORDER BY id DESC LIMIT 100"
            ).fetchall()
        clean_nodes = []
        for row in nodes:
            item = dict(row)
            item.pop("token_hash", None)
            item["capabilities"] = json.loads(item.pop("capabilities_json") or "{}")
            item["dry_run"] = bool(item["dry_run"])
            clean_nodes.append(item)
        return jsonify({
            "nodes": clean_nodes,
            "bindings": [dict(row) for row in bindings],
            "jobs": [dict(row) for row in jobs],
            "events": [dict(row) for row in events],
        })

    @flask_app.post("/api/operations/bindings")
    def create_binding():
        payload = request.get_json(silent=True) or {}
        node_id = str(payload.get("node_id") or "").strip()
        contact_ref = str(payload.get("contact_ref") or "").strip()
        try:
            conversation_id = int(payload.get("conversation_id"))
        except (TypeError, ValueError):
            return jsonify({"error": "conversation_id无效"}), 400
        if not contact_ref or len(contact_ref) > 64:
            return jsonify({"error": "微信联系人显示名不能为空且不能超过64字符"}), 400
        with db_factory() as conn:
            node = conn.execute("SELECT * FROM worker_nodes WHERE node_id=?", (node_id,)).fetchone()
            conv = conn.execute("SELECT id FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if not node or not conv:
                return jsonify({"error": "节点或会话不存在"}), 404
            current = conn.execute("SELECT COUNT(*) FROM chat_bindings WHERE node_id=? AND enabled=1", (node_id,)).fetchone()[0]
            existing = conn.execute(
                "SELECT id FROM chat_bindings WHERE node_id=? AND account_ref=? AND (conversation_id=? OR contact_ref=?)",
                (node_id, node["account_ref"], conversation_id, contact_ref),
            ).fetchone()
            if not existing and current >= int(node["max_active_conversations"]):
                return jsonify({"error": f"节点活跃会话已达到上限{node['max_active_conversations']}"}), 409
            if existing:
                conn.execute(
                    "UPDATE chat_bindings SET conversation_id=?,contact_ref=?,enabled=1,auto_reply=?,priority=?,last_error='',updated_at=? WHERE id=?",
                    (conversation_id, contact_ref, 1 if payload.get("auto_reply", True) else 0,
                     max(0, min(100, int(payload.get("priority") or 50))), now_func(), existing["id"]),
                )
                binding_id = int(existing["id"])
            else:
                cur = conn.execute(
                    """
                    INSERT INTO chat_bindings(node_id,account_ref,conversation_id,contact_ref,enabled,
                      auto_reply,priority,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (node_id, node["account_ref"], conversation_id, contact_ref, 1,
                     1 if payload.get("auto_reply", True) else 0,
                     max(0, min(100, int(payload.get("priority") or 50))), now_func(), now_func()),
                )
                binding_id = int(cur.lastrowid)
            audit_func(conn, "绑定工作会话", "chat_binding", binding_id, f"node={node_id},conversation={conversation_id}")
        return jsonify({"ok": True, "id": binding_id}), 201

    @flask_app.patch("/api/operations/bindings/<int:binding_id>")
    def update_binding(binding_id: int):
        payload = request.get_json(silent=True) or {}
        allowed = {}
        if "enabled" in payload:
            allowed["enabled"] = 1 if payload["enabled"] else 0
        if "auto_reply" in payload:
            allowed["auto_reply"] = 1 if payload["auto_reply"] else 0
        if "priority" in payload:
            allowed["priority"] = max(0, min(100, int(payload["priority"])))
        if not allowed:
            return jsonify({"error": "没有可更新字段"}), 400
        allowed["updated_at"] = now_func()
        with db_factory() as conn:
            cur = conn.execute(
                f"UPDATE chat_bindings SET {','.join(f'{key}=?' for key in allowed)} WHERE id=?",
                (*allowed.values(), binding_id),
            )
            if not cur.rowcount:
                return jsonify({"error": "绑定不存在"}), 404
            audit_func(conn, "更新工作会话", "chat_binding", binding_id, _json_detail(allowed))
        return jsonify({"ok": True})

    @flask_app.post("/api/operations/conversations/<int:conversation_id>/queue-message")
    def queue_manual_message(conversation_id: int):
        payload = request.get_json(silent=True) or {}
        content = str(payload.get("content") or "").strip()
        if not 1 <= len(content) <= 500:
            return jsonify({"error": "消息长度必须在1-500字符之间"}), 400
        with db_factory() as conn:
            binding = conn.execute(
                "SELECT * FROM chat_bindings WHERE conversation_id=? AND enabled=1 ORDER BY id LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if not binding:
                return jsonify({"error": "会话尚未绑定工作节点"}), 409
            key = str(payload.get("idempotency_key") or f"manual:{conversation_id}:{secrets.token_hex(12)}")[:160]
            job_id = enqueue_message(
                conn,
                node_id=binding["node_id"], account_ref=binding["account_ref"],
                conversation_id=conversation_id, contact_ref=binding["contact_ref"],
                content=content, idempotency_key=key, basis="人工工作台",
                priority=int(binding["priority"]) + 10,
            )
            audit_func(conn, "人工消息排队", "message_job", job_id, f"conversation={conversation_id}")
        return jsonify({"ok": True, "job_id": job_id, "status": "queued"}), 202

    @flask_app.patch("/api/operations/conversations/<int:conversation_id>/takeover")
    def set_conversation_takeover(conversation_id: int):
        payload = request.get_json(silent=True) or {}
        enabled = 1 if payload.get("enabled", True) else 0
        with db_factory() as conn:
            cur = conn.execute(
                "UPDATE conversations SET human_takeover=?,updated_at=? WHERE id=?",
                (enabled, now_func(), conversation_id),
            )
            if not cur.rowcount:
                return jsonify({"error": "会话不存在"}), 404
            if not enabled:
                conn.execute(
                    "UPDATE chat_bindings SET last_error='',updated_at=? WHERE conversation_id=?",
                    (now_func(), conversation_id),
                )
            audit_func(
                conn, "会话人工接管" if enabled else "恢复自动处理",
                "conversation", conversation_id, "operator",
            )
        return jsonify({"ok": True, "human_takeover": bool(enabled)})

    @flask_app.post("/api/operations/jobs/<int:job_id>/cancel")
    def cancel_job(job_id: int):
        with db_factory() as conn:
            cur = conn.execute(
                "UPDATE message_jobs SET status='cancelled',updated_at=?,completed_at=? WHERE id=? AND status='queued'",
                (now_func(), now_func(), job_id),
            )
            if not cur.rowcount:
                return jsonify({"error": "只有尚未租出的排队任务可以取消"}), 409
            audit_func(conn, "取消消息任务", "message_job", job_id, "操作员取消")
        return jsonify({"ok": True})
