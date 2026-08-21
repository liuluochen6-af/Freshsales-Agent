from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class EchoMindCall:
    ok: bool
    data: dict[str, Any] | None = None
    error: str = ""


class EchoMindClient:
    """Optional adapter for an independently deployed EchoMind `/chat` API."""

    VALID_MODES = {"off", "shadow", "active"}

    def __init__(self, base_url: str = "", mode: str = "off", token: str = "", timeout: float = 8.0):
        mode = mode.strip().lower()
        self.mode = mode if mode in self.VALID_MODES else "off"
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.timeout = max(1.0, min(30.0, float(timeout)))

    @classmethod
    def from_env(cls) -> "EchoMindClient":
        return cls(
            base_url=os.environ.get("ECHOMIND_URL", ""),
            mode=os.environ.get("ECHOMIND_MODE", "off"),
            token=os.environ.get("ECHOMIND_TOKEN", ""),
            timeout=float(os.environ.get("ECHOMIND_TIMEOUT_SECONDS", "8")),
        )

    @property
    def enabled(self) -> bool:
        return self.mode != "off" and bool(self.base_url)

    def chat(self, *, message: str, user_id: str, conv_id: str) -> EchoMindCall:
        if not self.enabled:
            return EchoMindCall(ok=False, error="disabled")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return EchoMindCall(ok=False, error="invalid_url")
        payload = json.dumps({"message": message, "user_id": user_id, "conv_id": conv_id}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request_obj = urllib.request.Request(self.base_url + "/chat", data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request_obj, timeout=self.timeout) as response:
                data = json.loads(response.read(512 * 1024).decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            return EchoMindCall(ok=False, error=type(exc).__name__)
        if not isinstance(data, dict) or not isinstance(data.get("response"), str):
            return EchoMindCall(ok=False, error="invalid_response")
        return EchoMindCall(ok=True, data=data)
