from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import platform
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from rpa.weixin_driver import ObservedMessage, RPAError, WeixinDriver

try:
    import win32crypt
except ImportError:  # pragma: no cover - real workers target Windows
    win32crypt = None


VERSION = "1.1.0"
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "worker.json"
LOG_PATH: Path | None = None
DEFAULT_MANAGED_CONVERSATIONS = 500
MAX_MANAGED_CONVERSATIONS = 5000
DEFAULT_SCAN_BATCH_SIZE = 50


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def emit(event: str, **detail: Any) -> None:
    line = json.dumps({"time": timestamp(), "event": event, **detail}, ensure_ascii=False)
    print(line, flush=True)
    if LOG_PATH:
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            if LOG_PATH.exists() and LOG_PATH.stat().st_size > 10 * 1024 * 1024:
                rotated = LOG_PATH.with_suffix(LOG_PATH.suffix + ".1")
                if rotated.exists():
                    rotated.unlink()
                os.replace(LOG_PATH, rotated)
            with LOG_PATH.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except OSError:
            pass


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def automatic_node_id() -> str:
    """Build a stable, non-secret node id for this Windows user and machine."""
    machine = platform.node().strip() or "windows-pc"
    safe_machine = re.sub(r"[^a-zA-Z0-9_.-]+", "-", machine).strip("-._") or "windows-pc"
    identity = f"{machine}\0{getpass.getuser()}".encode("utf-8", errors="replace")
    suffix = hashlib.sha256(identity).hexdigest()[:10]
    return f"wechat-{safe_machine[:38]}-{suffix}"[:64]


def create_automatic_config(path: Path) -> dict[str, Any]:
    """Create a usable local worker configuration without manual JSON editing."""
    node_id = automatic_node_id()
    server_url = os.environ.get("AGENT_SERVER_URL", "http://127.0.0.1:8015").strip()
    account_ref = os.environ.get("AGENT_WECHAT_ACCOUNT", "").strip() or f"wechat@{node_id}"
    config = {
        "config_version": 2,
        "auto_configured": True,
        "server_url": server_url,
        "node_id": node_id,
        "display_name": os.environ.get("AGENT_NODE_NAME", "").strip() or f"{platform.node() or '本机'} 微信执行设备",
        "account_ref": account_ref,
        "bootstrap_token": os.environ.get("AGENT_BOOTSTRAP_TOKEN", "").strip(),
        "node_token_encrypted": "",
        "max_active_conversations": DEFAULT_MANAGED_CONVERSATIONS,
        "scan_batch_size": DEFAULT_SCAN_BATCH_SIZE,
        "scan_interval_seconds": 0.8,
        "cycle_pause_seconds": 0.5,
        "uia_timeout_seconds": 8,
        "state_file": "worker-state.json",
        "log_file": "worker-events.jsonl",
    }
    write_json(path, config)
    emit(
        "worker_config_created",
        path=str(path),
        node_id=node_id,
        server_url=server_url,
        managed_capacity=DEFAULT_MANAGED_CONVERSATIONS,
    )
    return config


def load_or_create_config(path: Path) -> dict[str, Any]:
    config = read_json(path, {})
    if not config:
        return create_automatic_config(path)

    changed = False
    if int(config.get("config_version") or 1) < 2:
        # Version 1 shipped with a fixed default of 20. Promote that legacy
        # default while retaining deliberately configured non-default values.
        if int(config.get("max_active_conversations") or 20) == 20:
            config["max_active_conversations"] = DEFAULT_MANAGED_CONVERSATIONS
        config.setdefault("scan_batch_size", DEFAULT_SCAN_BATCH_SIZE)
        config["config_version"] = 2
        changed = True
    environment_overrides = {
        "server_url": os.environ.get("AGENT_SERVER_URL", "").strip(),
        "account_ref": os.environ.get("AGENT_WECHAT_ACCOUNT", "").strip(),
        "display_name": os.environ.get("AGENT_NODE_NAME", "").strip(),
        "bootstrap_token": os.environ.get("AGENT_BOOTSTRAP_TOKEN", "").strip(),
    }
    for key, value in environment_overrides.items():
        if value and config.get(key) != value:
            config[key] = value
            changed = True
    if changed:
        write_json(path, config)
        emit("worker_config_upgraded", path=str(path), config_version=2)
    return config


def rotating_session_batch(
    sessions: list[dict[str, Any]], cursor: int, batch_size: int
) -> tuple[list[dict[str, Any]], int]:
    """Round-robin a large managed contact set without starving later rows."""
    if not sessions:
        return [], 0
    size = max(1, min(len(sessions), batch_size))
    start = cursor % len(sessions)
    selected = [sessions[(start + offset) % len(sessions)] for offset in range(size)]
    return selected, (start + size) % len(sessions)


def protect_node_token(value: str) -> str:
    if not win32crypt:
        raise RuntimeError("当前环境不支持Windows DPAPI，不能安全保存节点令牌")
    encrypted_result = win32crypt.CryptProtectData(value.encode("utf-8"), "durian-agent-worker", None, None, None, 0)
    encrypted = encrypted_result[1] if isinstance(encrypted_result, tuple) else encrypted_result
    return base64.b64encode(encrypted).decode("ascii")


def reveal_node_token(value: str) -> str:
    if not value:
        return ""
    if not win32crypt:
        raise RuntimeError("当前环境不支持Windows DPAPI，不能读取节点令牌")
    raw = base64.b64decode(value.encode("ascii"))
    decrypted_result = win32crypt.CryptUnprotectData(raw, None, None, None, 0)
    decrypted = decrypted_result[1] if isinstance(decrypted_result, tuple) else decrypted_result
    return decrypted.decode("utf-8")


class CoordinatorClient:
    def __init__(self, config_path: Path, config: dict[str, Any], dry_run: bool):
        self.config_path = config_path
        self.config = config
        self.server_url = str(config.get("server_url") or "").rstrip("/")
        self.node_id = str(config.get("node_id") or "").strip()
        self.account_ref = str(config.get("account_ref") or "").strip()
        self.token = reveal_node_token(str(config.get("node_token_encrypted") or ""))
        if not self.token:
            self.token = str(config.get("node_token") or "").strip()
        self.bootstrap_token = str(config.get("bootstrap_token") or "").strip()
        self.dry_run = dry_run
        if not self.server_url.startswith(("http://", "https://")):
            raise ValueError("worker.json中的server_url必须以http://或https://开头")

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None,
                *, bootstrap: bool = False, timeout: int = 30) -> Any:
        headers = {"Accept": "application/json", "Content-Type": "application/json; charset=utf-8"}
        if bootstrap and self.bootstrap_token:
            headers["X-Bootstrap-Token"] = self.bootstrap_token
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
            headers["X-Node-ID"] = self.node_id
        data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if method != "GET" else None
        req = urllib.request.Request(self.server_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(body).get("error") or body
            except json.JSONDecodeError:
                message = body
            raise RuntimeError(f"中央端返回HTTP {exc.code}：{message}") from exc

    def register(self) -> None:
        payload = {
            "node_id": self.node_id,
            "display_name": self.config.get("display_name") or self.node_id,
            "machine_name": platform.node(),
            "account_ref": self.account_ref,
            "version": VERSION,
            "max_active_conversations": int(
                self.config.get("max_active_conversations") or DEFAULT_MANAGED_CONVERSATIONS
            ),
            "dry_run": self.dry_run,
            "capabilities": {
                "wechat_windows_uia": True,
                "read_visible_messages": True,
                "send_existing_contact": True,
                "fail_closed": True,
                "python": platform.python_version(),
            },
        }
        result = self.request("POST", "/api/worker/register", payload, bootstrap=True)
        if result.get("node_token"):
            self.token = str(result["node_token"])
            if not self.config.get("node_token_encrypted") or self.config.get("node_token"):
                self.config["node_token_encrypted"] = protect_node_token(self.token)
                self.config.pop("node_token", None)
                self.config.pop("bootstrap_token", None)
                write_json(self.config_path, self.config)
        emit("registered", node_id=self.node_id, account_ref=self.account_ref, dry_run=self.dry_run)

    def heartbeat(self, active: int, last_error: str = "") -> None:
        self.request("POST", "/api/worker/heartbeat", {
            "active_conversations": active,
            "dry_run": self.dry_run,
            "last_error": last_error[:500],
        })

    def sessions(self) -> list[dict[str, Any]]:
        return list(self.request("GET", "/api/worker/sessions").get("sessions") or [])

    def report_event(self, event_type: str, detail: Any, severity: str = "info") -> None:
        try:
            self.request("POST", "/api/worker/events", {
                "event_type": event_type, "detail": detail, "severity": severity,
            }, timeout=10)
        except Exception as exc:
            emit("event_report_failed", error=str(exc))

    def report_inbound(self, session: dict[str, Any], external_id: str, content: str) -> dict[str, Any]:
        return self.request("POST", "/api/worker/inbound", {
            "account_ref": self.account_ref,
            "contact_ref": session["contact_ref"],
            "conversation_id": session["conversation_id"],
            "external_message_id": external_id,
            "content": content,
            "observed_at": timestamp(),
        }, timeout=40)

    def pull_jobs(self) -> list[dict[str, Any]]:
        return list(self.request("POST", "/api/worker/jobs/pull", {"limit": 1, "lease_seconds": 90}).get("jobs") or [])

    def complete_job(self, job: dict[str, Any], outcome: str, error: str = "") -> None:
        self.request("POST", f"/api/worker/jobs/{job['id']}/complete", {
            "lease_token": job["lease_token"],
            "outcome": outcome,
            "error": error[:500],
        })


def fingerprint_messages(messages: list[ObservedMessage]) -> list[dict[str, str]]:
    occurrences: dict[tuple[str, str], int] = {}
    result = []
    for message in messages:
        key = (message.sender, message.content)
        occurrences[key] = occurrences.get(key, 0) + 1
        raw = f"{message.sender}\0{message.content}\0{occurrences[key]}".encode("utf-8")
        result.append({
            "fingerprint": hashlib.sha256(raw).hexdigest(),
            "sender": message.sender,
            "content": message.content,
        })
    return result


def reliable_new_messages(previous: list[str], current: list[dict[str, str]]) -> list[dict[str, str]]:
    current_ids = [item["fingerprint"] for item in current]
    if not previous:
        return []
    maximum = min(len(previous), len(current_ids))
    for overlap in range(maximum, 0, -1):
        if previous[-overlap:] == current_ids[:overlap]:
            return current[overlap:]
    if current_ids == previous:
        return []
    raise RPAError("重新打开会话后找不到可靠消息锚点；为避免重复回复已暂停该会话")


class WorkerAgent:
    def __init__(self, config_path: Path, live: bool):
        global LOG_PATH
        self.config_path = config_path
        self.config = load_or_create_config(config_path)
        LOG_PATH = (config_path.parent / str(self.config.get("log_file") or "worker-events.jsonl")).resolve()
        self.dry_run = not live
        self.client = CoordinatorClient(config_path, self.config, self.dry_run)
        state_name = str(self.config.get("state_file") or "worker-state.json")
        self.state_path = (config_path.parent / state_name).resolve()
        self.state = read_json(self.state_path, {"sessions": {}})
        self.scan_interval = max(0.3, min(10.0, float(self.config.get("scan_interval_seconds") or 0.8)))
        self.max_active = max(
            1,
            min(
                MAX_MANAGED_CONVERSATIONS,
                int(self.config.get("max_active_conversations") or DEFAULT_MANAGED_CONVERSATIONS),
            ),
        )
        self.scan_batch_size = max(
            1,
            min(self.max_active, int(self.config.get("scan_batch_size") or DEFAULT_SCAN_BATCH_SIZE)),
        )
        self.driver: WeixinDriver | None = None

    def save_state(self) -> None:
        write_json(self.state_path, self.state)

    def execute_one_job(self) -> bool:
        jobs = self.client.pull_jobs()
        if not jobs:
            return False
        job = jobs[0]
        try:
            if job.get("kind") != "send_message":
                raise RPAError(f"不支持的任务类型：{job.get('kind')}")
            content = str((job.get("payload") or {}).get("content") or "").strip()
            if not self.driver:
                raise RPAError("微信驱动未启动")
            result = self.driver.send_message_to_contact(str(job["contact_ref"]), content)
            if not result.verified:
                raise RPAError("发送后未通过输入框清空校验")
            self.client.complete_job(job, "succeeded")
            emit("message_sent", job_id=job["id"], contact=job["contact_ref"])
        except Exception as exc:
            error = str(exc)
            try:
                self.client.complete_job(job, "failed", error)
            finally:
                self.client.report_event("send_failed", {"job_id": job.get("id"), "error": error}, "error")
            emit("message_failed", job_id=job.get("id"), contact=job.get("contact_ref"), error=error)
        return True

    def scan_session(self, session: dict[str, Any]) -> None:
        if not self.driver:
            raise RPAError("微信驱动未启动")
        contact = str(session["contact_ref"])
        self.driver.open_contact(contact)
        time.sleep(self.scan_interval)
        observed = fingerprint_messages(self.driver.read_current_messages(contact))
        state_key = f"{self.client.account_ref}\0{contact}"
        old = list((self.state.get("sessions") or {}).get(state_key) or [])
        if not old:
            self.state.setdefault("sessions", {})[state_key] = [item["fingerprint"] for item in observed]
            self.save_state()
            emit("session_baselined", contact=contact, visible_messages=len(observed))
            return
        new_messages = reliable_new_messages(old, observed)
        reported_any = False
        for message in new_messages:
            if message["sender"] != "customer":
                continue
            external_id = hashlib.sha256(
                f"{self.client.node_id}\0{self.client.account_ref}\0{contact}\0{message['fingerprint']}".encode("utf-8")
            ).hexdigest()
            result = self.client.report_inbound(session, external_id, message["content"])
            reported_any = True
            emit(
                "inbound_reported", contact=contact, duplicate=bool(result.get("duplicate")),
                reply_job_id=result.get("reply_job_id"), requires_human=result.get("requires_human"),
            )
        if reported_any:
            while self.execute_one_job():
                pass
        self.state.setdefault("sessions", {})[state_key] = [item["fingerprint"] for item in observed]
        self.save_state()

    def run(self, once: bool = False) -> None:
        self.client.register()
        sessions = self.client.sessions()
        self.client.heartbeat(min(len(sessions), self.max_active))
        if self.dry_run:
            emit(
                "dry_run_ready",
                message="配置和中央端连接正常；未读取微信、未发送消息。使用--live才会启动界面自动化。",
                sessions=len(sessions), max_active=self.max_active,
            )
            return

        self.driver = WeixinDriver(timeout=float(self.config.get("uia_timeout_seconds") or 8))
        heartbeat_at = 0.0
        while True:
            last_error = ""
            try:
                while self.execute_one_job():
                    pass
                sessions = self.client.sessions()[:self.max_active]
                scan_sessions, next_cursor = rotating_session_batch(
                    sessions,
                    int(self.state.get("scheduler_cursor") or 0),
                    self.scan_batch_size,
                )
                self.state["scheduler_cursor"] = next_cursor
                self.save_state()
                for session in scan_sessions:
                    try:
                        self.scan_session(session)
                    except Exception as exc:
                        last_error = str(exc)
                        self.client.report_event(
                            "session_scan_failed",
                            {"conversation_id": session.get("conversation_id"), "contact_ref": session.get("contact_ref"), "error": last_error},
                            "warning",
                        )
                        emit("session_scan_failed", contact=session.get("contact_ref"), error=last_error)
                    if time.monotonic() - heartbeat_at >= 15:
                        self.client.heartbeat(len(sessions), last_error)
                        heartbeat_at = time.monotonic()
                self.client.heartbeat(len(sessions), last_error)
                heartbeat_at = time.monotonic()
            except KeyboardInterrupt:
                emit("stopped", reason="operator_interrupt")
                return
            except Exception as exc:
                last_error = str(exc)
                emit("worker_cycle_failed", error=last_error)
                try:
                    self.client.heartbeat(0, last_error)
                except Exception:
                    pass
            if once:
                return
            time.sleep(max(0.5, float(self.config.get("cycle_pause_seconds") or 1.0)))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="榴莲销售智能体 Windows 微信工作节点")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--live", action="store_true", help="明确启用真实微信界面读取和发送；默认仅检查配置")
    parser.add_argument("--once", action="store_true", help="只执行一轮后退出")
    args = parser.parse_args()
    try:
        WorkerAgent(args.config.resolve(), args.live).run(args.once)
        return 0
    except Exception as exc:
        emit("fatal", error=str(exc), machine=socket.gethostname())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
