from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from pywinauto import Desktop

from weixin_driver import RPAError, WeixinDriver


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "durian_agent.db"
ANSWER_URL = "http://127.0.0.1:8015/api/knowledge/answer"
RISK_WORDS = ("退款", "赔偿", "赔付", "投诉", "合同", "账期")
STOP_WORDS = (
    "不需要", "别联系", "不要联系", "别再发", "停止联系",
    "这次对话结束", "结束对话", "今天先聊到这里", "先聊到这里", "不聊了",
    "暂停测试", "停止测试", "暂停回复", "结束测试",
)
GREETINGS = {"你好", "您好", "哈喽", "hi", "hello"}


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def emit(event: str, **data) -> None:
    print(json.dumps({"event": event, "time": now(), **data}, ensure_ascii=False), flush=True)


def open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_conversation(contact: str, new_session: bool = False) -> int:
    with open_db() as conn:
        if new_session:
            reusable = conn.execute(
                """SELECT c.id FROM conversations c
                   JOIN leads l ON l.id=c.lead_id
                   WHERE l.contact_name=? AND l.notes='从你好开始的完整成单测试'
                     AND NOT EXISTS(SELECT 1 FROM messages m WHERE m.conversation_id=c.id)
                   ORDER BY c.id DESC LIMIT 1""",
                (contact,),
            ).fetchone()
            if reusable:
                conn.execute(
                    "UPDATE conversations SET stage='等待客户问候',human_takeover=0,updated_at=? WHERE id=?",
                    (now(), reusable["id"]),
                )
                return int(reusable["id"])
            stamp = datetime.now().strftime("%m%d-%H%M%S")
            cursor = conn.execute(
                """INSERT INTO leads(
                    store_name,contact_name,wechat_id,source,source_basis,status,
                    notes,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (f"{contact}（完整测试-{stamp}）", contact, f"{contact}#full-test-{stamp}",
                 "微信测试", "用户指定现有好友", "测试会话", "从你好开始的完整成单测试", now(), now()),
            )
            lead_id = cursor.lastrowid
            cursor = conn.execute(
                """INSERT INTO conversations(
                    lead_id,stage,intent,sentiment,ai_mode,unread,human_takeover,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (lead_id, "等待客户问候", "待识别", "中性", "DeepSeek+知识库", 0, 0, now()),
            )
            return int(cursor.lastrowid)
        lead = conn.execute(
            "SELECT id FROM leads WHERE wechat_id=? AND store_name=?",
            (contact, f"{contact}（微信测试）"),
        ).fetchone()
        if lead is None:
            cursor = conn.execute(
                """INSERT INTO leads(
                    store_name,contact_name,wechat_id,source,source_basis,status,
                    notes,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (f"{contact}（微信测试）", contact, contact, "微信测试", "用户指定现有好友",
                 "测试会话", "受控知识库对话测试", now(), now()),
            )
            lead_id = cursor.lastrowid
        else:
            lead_id = lead["id"]
        conversation = conn.execute(
            "SELECT id FROM conversations WHERE lead_id=?", (lead_id,)
        ).fetchone()
        if conversation is None:
            cursor = conn.execute(
                """INSERT INTO conversations(
                    lead_id,stage,intent,sentiment,ai_mode,unread,human_takeover,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (lead_id, "测试中", "待识别", "中性", "DeepSeek+知识库", 0, 0, now()),
            )
            return int(cursor.lastrowid)
        conversation_id = int(conversation["id"])
        conn.execute(
            "UPDATE conversations SET stage=?,ai_mode=?,human_takeover=0,updated_at=? WHERE id=?",
            ("测试中", "DeepSeek+知识库", now(), conversation_id),
        )
        return conversation_id


def log_message(conversation_id: int, sender: str, content: str) -> None:
    with open_db() as conn:
        conn.execute(
            "INSERT INTO messages(conversation_id,sender,content,created_at) VALUES(?,?,?,?)",
            (conversation_id, sender, content, now()),
        )
        conn.execute(
            "UPDATE conversations SET unread=?,updated_at=? WHERE id=?",
            (1 if sender == "customer" else 0, now(), conversation_id),
        )


def log_incoming_once(conversation_id: int, content: str) -> bool:
    with open_db() as conn:
        last = conn.execute(
            "SELECT sender,content FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
    if last and last["sender"] == "customer" and last["content"] == content:
        return False
    log_message(conversation_id, "customer", content)
    return True


def knowledge_content(title: str) -> str:
    with open_db() as conn:
        row = conn.execute(
            "SELECT content FROM knowledge_entries WHERE title=? AND status='已发布'",
            (title,),
        ).fetchone()
    return row["content"].strip() if row else ""


def set_takeover(conversation_id: int, reason: str) -> None:
    with open_db() as conn:
        conn.execute(
            "UPDATE conversations SET human_takeover=1,stage=?,updated_at=? WHERE id=?",
            (f"转人工：{reason}", now(), conversation_id),
        )


def finish_conversation(conversation_id: int, stage: str) -> None:
    with open_db() as conn:
        conn.execute(
            "UPDATE conversations SET stage=?,updated_at=? WHERE id=?",
            (stage, now(), conversation_id),
        )


def last_sales_message(conversation_id: int) -> str:
    with open_db() as conn:
        row = conn.execute(
            "SELECT content FROM messages WHERE conversation_id=? AND sender='sales' ORDER BY id DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
    return row["content"].strip() if row else ""


def all_sales_messages(conversation_id: int) -> set[str]:
    with open_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT content FROM messages WHERE conversation_id=? AND sender='sales'",
            (conversation_id,),
        ).fetchall()
    return {row["content"].strip() for row in rows if row["content"].strip()}


def request_answer(question: str) -> dict:
    payload = json.dumps({"question": question, "use_ai": True}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        ANSWER_URL, data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def request_local_answer(question: str) -> dict:
    payload = json.dumps({"question": question, "use_ai": False}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        ANSWER_URL, data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def request_conversation_suggestion(conversation_id: int) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:8015/api/conversations/{conversation_id}/suggest",
        data=b"{}", headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    return {
        "answer": data.get("suggestion", ""),
        "mode": data.get("basis", ""),
        "sources": data.get("sources", []),
        "requires_human": bool(data.get("requires_human")),
    }


def wechat_window():
    windows = [w for w in Desktop(backend="uia").windows()
               if w.window_text() == "WeChat" and w.is_visible() and w.is_enabled()
               and w.element_info.class_name != "mmui::LoginWindow"]
    if len(windows) != 1:
        raise RPAError(f"微信主窗口数量异常：{len(windows)}")
    return windows[0]


def verify_contact(window, contact: str) -> None:
    titles = [item for item in window.descendants(control_type="Text")
              if item.window_text() == contact
              and (item.element_info.automation_id or "").endswith(("current_chat_n", "current_chat_name_label"))
              and item.is_visible()]
    if len(titles) != 1:
        raise RPAError(f"无法唯一确认当前聊天对象为{contact}")


def bubbles(window) -> list[tuple[tuple[int, ...], str]]:
    result = []
    for item in window.descendants(control_type="ListItem"):
        if "chat_bubble_item_view" not in (item.element_info.automation_id or ""):
            continue
        text = item.window_text().strip()
        if text:
            result.append((tuple(item.element_info.runtime_id or ()), text))
    return result


def run(contact: str, timeout: int, use_latest_greeting: bool, new_session: bool,
        conversation_id: int | None = None, continuous_test: bool = False) -> None:
    conversation_id = conversation_id or ensure_conversation(contact, new_session)
    resume_last_sent = last_sales_message(conversation_id)
    deadline = time.monotonic() + timeout
    wait_reason = ""
    while True:
        if time.monotonic() >= deadline:
            finish_conversation(conversation_id, "测试结束")
            emit("timeout", message="等待微信主窗口超时，未发送消息")
            return
        try:
            window = wechat_window()
            verify_contact(window, contact)
            initial = bubbles(window)
            break
        except Exception as exc:
            reason = str(exc)
            if reason != wait_reason:
                emit("waiting_wechat", reason=reason)
                wait_reason = reason
            time.sleep(1)
    seen = {runtime_id for runtime_id, _ in initial}
    pending: list[tuple[tuple[int, ...], str]] = []
    if use_latest_greeting and initial and initial[-1][1].lower() in GREETINGS:
        pending.append(initial[-1])
        seen.discard(initial[-1][0])
    elif resume_last_sent:
        anchor_indexes = [index for index, (_, text) in enumerate(initial) if text == resume_last_sent]
        if anchor_indexes:
            anchor = anchor_indexes[-1]
            seen = {runtime_id for runtime_id, _ in initial[:anchor + 1]}
            pending.extend(initial[anchor + 1:])
    emit("started", contact=contact, conversation_id=conversation_id, timeout_seconds=timeout)
    driver = WeixinDriver()
    last_sent = resume_last_sent
    outgoing_texts = all_sales_messages(conversation_id)
    disconnected = False

    while time.monotonic() < deadline:
        try:
            window = wechat_window()
            verify_contact(window, contact)
            current = bubbles(window)
        except Exception as exc:
            if not disconnected:
                emit("waiting_wechat", reason=str(exc))
                disconnected = True
            time.sleep(1)
            continue

        if disconnected:
            if last_sent:
                anchor_indexes = [index for index, (_, text) in enumerate(current) if text == last_sent]
                if anchor_indexes:
                    anchor = anchor_indexes[-1]
                    seen = {runtime_id for runtime_id, _ in current[:anchor + 1]}
                else:
                    emit("resync_blocked", reason="恢复后看不到上一条已发送消息，等待人工核对")
                    time.sleep(1)
                    continue
            else:
                seen = {runtime_id for runtime_id, _ in current}
            emit("resumed", contact=contact)
            disconnected = False

        new_items = pending or [(rid, text) for rid, text in current if rid not in seen]
        pending = []
        if not new_items:
            time.sleep(0.25)
            continue

        for runtime_id, incoming in new_items:
            seen.add(runtime_id)
            if incoming in outgoing_texts:
                continue
            verify_contact(window, contact)
            emit("received", contact=contact, message=incoming)
            log_incoming_once(conversation_id, incoming)

            if any(word in incoming for word in STOP_WORDS):
                reply = knowledge_content("对话结束-01") or "好的老板，感谢交流，祝您生意兴隆！🤝"
                result = driver.send_message_to_current_contact(contact, reply)
                if not result.verified:
                    raise RPAError("结束语发送后未通过输入框清空校验")
                log_message(conversation_id, "sales", reply)
                outgoing_texts.add(reply)
                finish_conversation(conversation_id, "客户结束对话")
                emit("sent", contact=contact, reply=reply, mode="知识库固定收口", sources=["对话结束-01"])
                emit("stopped", reason="客户明确表示本次对话结束")
                return

            if any(word in incoming for word in RISK_WORDS) and not continuous_test:
                set_takeover(conversation_id, "高风险问题")
                emit("paused", reason="高风险问题需人工确认", message=incoming)
                return

            if incoming.lower() in GREETINGS:
                opening = knowledge_content("首次开场-01")
                answer_data = {
                    "answer": opening,
                    "mode": "知识库固定开场",
                    "sources": ["首次开场-01"],
                    "requires_human": False,
                }
            else:
                answer_data = request_conversation_suggestion(conversation_id)
            if answer_data.get("requires_human") and not continuous_test:
                set_takeover(conversation_id, "知识库要求人工确认")
                emit("paused", reason="知识库要求人工确认", suggestion=answer_data.get("answer", ""))
                return

            reply = str(answer_data.get("answer", "")).strip()
            if not reply and continuous_test:
                fallback = request_local_answer(incoming)
                answer_data = {
                    "answer": fallback.get("answer", ""),
                    "mode": "本地知识库快速回退",
                    "sources": fallback.get("sources", []),
                    "requires_human": bool(fallback.get("requires_human")),
                }
                reply = str(answer_data.get("answer", "")).strip()
            if not reply:
                set_takeover(conversation_id, "未生成可靠答案")
                emit("paused", reason="未生成可靠答案")
                return

            result = driver.send_message_to_current_contact(contact, reply)
            if not result.verified:
                raise RPAError("消息发送后未通过输入框清空校验")
            last_sent = reply
            log_message(conversation_id, "sales", reply)
            outgoing_texts.add(reply)
            emit("sent", contact=contact, reply=reply, mode=answer_data.get("mode"), sources=answer_data.get("sources", []))


        time.sleep(0.25)

    finish_conversation(conversation_id, "测试结束")
    emit("timeout", message="监听时限已到，未继续发送")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--contact", default="天使")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--use-latest-greeting", action="store_true")
    parser.add_argument("--new-session", action="store_true")
    parser.add_argument("--conversation-id", type=int)
    parser.add_argument("--continuous-test", action="store_true")
    args = parser.parse_args()
    try:
        run(args.contact, max(30, min(args.timeout, 1800)), args.use_latest_greeting,
            args.new_session, args.conversation_id, args.continuous_test)
    except Exception as exc:
        emit("error", error=str(exc))
        raise
