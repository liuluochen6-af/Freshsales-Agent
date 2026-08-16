from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pywinauto import Desktop
from pywinauto.base_wrapper import BaseWrapper


BASE_DIR = Path(__file__).resolve().parent
AUTHORIZATION_PATH = BASE_DIR / "authorization.json"
AUTHORIZATION_EXAMPLE_PATH = BASE_DIR / "authorization.example.json"


class SearchStatus(str, Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    ALREADY_FRIEND = "already_friend"
    PENDING_VERIFICATION = "pending_verification"
    PAUSED = "paused"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SearchResult:
    status: SearchStatus
    target: str
    message: str
    visible_buttons: tuple[str, ...] = ()


@dataclass(frozen=True)
class MessageResult:
    contact: str
    status: str
    message: str
    verified: bool


@dataclass(frozen=True)
class ObservedMessage:
    contact: str
    sender: str
    content: str


class RPAError(RuntimeError):
    pass


def load_authorization(path: Path | None = None) -> dict:
    selected = path or AUTHORIZATION_PATH
    if not selected.exists():
        raise RPAError(
            "缺少本机RPA授权配置。请先复制rpa/authorization.example.json为"
            "rpa/authorization.json，并按实际授权填写；微信未被访问。"
        )
    data = json.loads(selected.read_text(encoding="utf-8"))
    required_actions = {
        "rpa_launch_weixin",
        "search_contact",
        "fill_friend_request_greeting",
        "submit_friend_request",
        "read_result",
        "write_result_to_system",
    }
    missing = required_actions.difference(data.get("allowed_actions", []))
    if missing:
        raise RPAError(f"授权配置缺少动作：{', '.join(sorted(missing))}")
    if not data.get("fail_closed"):
        raise RPAError("授权配置必须启用 fail_closed")
    if not 1 <= int(data.get("daily_limit", 0)) <= 150:
        raise RPAError("每日上限必须在 1-150 之间")
    return data


def normalize_target(value: str) -> str:
    target = value.strip()
    if not 3 <= len(target) <= 64:
        raise RPAError("微信号或手机号长度不合法")
    if not re.fullmatch(r"[0-9A-Za-z_+\-@.]+", target):
        raise RPAError("微信号或手机号包含不允许的字符")
    return target


def classify_search_result(target: str, texts: list[str], buttons: list[str]) -> SearchResult:
    joined = "\n".join(item for item in texts if item)
    unique_buttons = tuple(dict.fromkeys(item for item in buttons if item))
    pause_words = ("验证码", "安全验证", "操作频繁", "请稍后", "风险", "异常")
    if any(word in joined for word in pause_words):
        return SearchResult(SearchStatus.PAUSED, target, joined, unique_buttons)
    if "无法找到该用户" in joined or "用户不存在" in joined:
        return SearchResult(SearchStatus.NOT_FOUND, target, joined, unique_buttons)
    if any(name in unique_buttons for name in ("添加到通讯录", "添加朋友")):
        return SearchResult(SearchStatus.FOUND, target, joined, unique_buttons)
    if "等待验证" in unique_buttons:
        return SearchResult(SearchStatus.PENDING_VERIFICATION, target, joined, unique_buttons)
    if any(name in unique_buttons for name in ("发消息", "发送消息")):
        return SearchResult(SearchStatus.ALREADY_FRIEND, target, joined, unique_buttons)
    return SearchResult(SearchStatus.UNKNOWN, target, joined, unique_buttons)


class WeixinDriver:
    def __init__(self, timeout: float = 8.0):
        self.timeout = timeout
        self.authorization = load_authorization()
        self.desktop = Desktop(backend="uia")

    @staticmethod
    def _rect_size(item: BaseWrapper) -> tuple[int, int]:
        rect = item.rectangle()
        return rect.width(), rect.height()

    @staticmethod
    def _unique_descendant(window: BaseWrapper, *, control_type: str, name: str) -> BaseWrapper:
        matches = [
            item
            for item in window.descendants(control_type=control_type)
            if item.window_text() == name and item.is_visible() and item.is_enabled()
        ]
        if len(matches) != 1:
            raise RPAError(
                f"预期找到 1 个 {control_type} 控件“{name}”，实际找到 {len(matches)} 个"
            )
        return matches[0]

    def main_window(self):
        candidates = []
        for window in self.desktop.windows():
            if window.window_text() != "WeChat":
                continue
            if (window.is_visible() and window.is_enabled()
                    and window.element_info.class_name != "mmui::LoginWindow"):
                candidates.append(window)
        if len(candidates) != 1:
            raise RPAError(f"预期 1 个微信主窗口，实际找到 {len(candidates)} 个")
        return candidates[0]

    def add_friend_window(self):
        matches = [
            item
            for item in self.desktop.windows()
            if item.window_text() == "添加朋友" and item.is_visible()
        ]
        if len(matches) > 1:
            raise RPAError("检测到多个添加朋友窗口，已停止")
        return matches[0] if matches else None

    def request_window(self):
        matches = [
            item
            for item in self.desktop.windows()
            if item.window_text() == "申请添加朋友" and item.is_visible()
        ]
        if len(matches) > 1:
            raise RPAError("检测到多个好友申请窗口，已停止")
        return matches[0] if matches else None

    def open_add_friend(self):
        existing = self.add_friend_window()
        if existing:
            return existing
        main = self.main_window()
        quick = self._unique_descendant(main, control_type="Button", name="快捷操作")
        quick.click_input()
        time.sleep(0.35)
        entry = self._unique_descendant(main, control_type="ListItem", name="添加朋友")
        entry.click_input()
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            window = self.add_friend_window()
            if window:
                return window
            time.sleep(0.2)
        raise RPAError("添加朋友窗口未在限定时间内出现")

    def search(self, value: str) -> SearchResult:
        target = normalize_target(value)
        window = self.open_add_friend()
        edits = [item for item in window.descendants(control_type="Edit") if item.is_visible() and item.is_enabled()]
        if len(edits) != 1:
            raise RPAError(f"预期 1 个搜索输入框，实际找到 {len(edits)} 个")
        edits[0].set_edit_text(target)
        search_button = self._unique_descendant(window, control_type="Button", name="搜索")
        search_button.click_input()
        deadline = time.monotonic() + self.timeout
        last_result = SearchResult(SearchStatus.UNKNOWN, target, "")
        while time.monotonic() < deadline:
            texts = [item.window_text() for item in window.descendants(control_type="Text") if item.is_visible()]
            buttons = [item.window_text() for item in window.descendants(control_type="Button") if item.is_visible()]
            last_result = classify_search_result(target, texts, buttons)
            if last_result.status != SearchStatus.UNKNOWN:
                return last_result
            time.sleep(0.25)
        return last_result

    def submit_friend_request(self, value: str, greeting: str, remark: str | None = None) -> SearchResult:
        greeting = greeting.strip()
        if not 1 <= len(greeting) <= 50:
            raise RPAError("好友验证语长度必须在 1-50 个字符之间")
        result = self.search(value)
        if result.status in (
            SearchStatus.NOT_FOUND,
            SearchStatus.ALREADY_FRIEND,
            SearchStatus.PENDING_VERIFICATION,
            SearchStatus.PAUSED,
        ):
            return result
        if result.status != SearchStatus.FOUND:
            raise RPAError("搜索结果未明确，已停止提交")

        profile = self.add_friend_window()
        if profile is None:
            raise RPAError("搜索结果窗口已消失")
        add_buttons = [
            item
            for item in profile.descendants(control_type="Button")
            if item.element_info.automation_id == "content_v_view.ProfileActionUi.add_friend_button"
            and item.window_text() == "添加到通讯录"
            and item.is_visible()
            and item.is_enabled()
        ]
        if len(add_buttons) != 1:
            raise RPAError(f"预期 1 个“添加到通讯录”按钮，实际找到 {len(add_buttons)} 个")
        add_buttons[0].click_input()

        deadline = time.monotonic() + self.timeout
        request_form = None
        while time.monotonic() < deadline:
            request_form = self.request_window()
            if request_form:
                break
            time.sleep(0.2)
        if request_form is None:
            raise RPAError("好友申请表单未出现")

        edits = sorted(
            [item for item in request_form.descendants(control_type="Edit") if item.is_visible() and item.is_enabled()],
            key=lambda item: item.rectangle().top,
        )
        if len(edits) != 2:
            raise RPAError(f"预期 2 个申请表单输入框，实际找到 {len(edits)} 个")
        edits[0].set_edit_text(greeting)
        if remark is not None:
            edits[1].set_edit_text(remark.strip()[:32])
        confirm = self._unique_descendant(request_form, control_type="Button", name="确定")
        confirm.click_input()

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.request_window() is None:
                break
            time.sleep(0.2)
        if self.request_window() is not None:
            texts = [
                item.window_text()
                for item in self.request_window().descendants(control_type="Text")
                if item.is_visible()
            ]
            if any(word in "\n".join(texts) for word in ("验证码", "安全验证", "操作频繁", "请稍后")):
                return SearchResult(SearchStatus.PAUSED, normalize_target(value), "\n".join(texts))
            raise RPAError("提交后申请表单未关闭，已停止重试")

        profile = self.add_friend_window()
        if profile:
            buttons = [item.window_text() for item in profile.descendants(control_type="Button") if item.is_visible()]
            texts = [item.window_text() for item in profile.descendants(control_type="Text") if item.is_visible()]
            final_result = classify_search_result(normalize_target(value), texts, buttons)
            if final_result.status == SearchStatus.PENDING_VERIFICATION:
                return final_result
        raise RPAError("提交后未检测到“等待验证”，请人工核对")

    def open_contact(self, contact: str):
        """Open one exact existing contact and verify the active chat title."""
        contact = contact.strip()
        if not contact or len(contact) > 64:
            raise RPAError("联系人名称不合法")

        window = self.main_window()
        search_edits = [
            item for item in window.descendants(control_type="Edit")
            if item.is_visible() and item.is_enabled()
            and item.element_info.automation_id != "chat_input_field"
        ]
        if len(search_edits) != 1:
            raise RPAError(f"预期1个微信搜索框，实际找到{len(search_edits)}个")
        search_edits[0].set_edit_text("")
        time.sleep(0.2)
        search_edits[0].set_edit_text(contact)

        deadline = time.monotonic() + self.timeout
        result = None
        while time.monotonic() < deadline:
            matches = [
                item for item in window.descendants(control_type="ListItem")
                if item.window_text() == contact
                and item.element_info.automation_id == f"search_item_{contact}"
                and item.is_visible() and item.is_enabled()
            ]
            if len(matches) == 1:
                result = matches[0]
                break
            if len(matches) > 1:
                raise RPAError("联系人搜索结果不唯一，已停止")
            time.sleep(0.2)
        if result is None:
            raise RPAError("没有找到唯一联系人搜索结果")
        result.click_input()

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            titles = [
                item for item in window.descendants(control_type="Text")
                if item.window_text() == contact
                and item.element_info.automation_id.endswith(("current_chat_n", "current_chat_name_label"))
                and item.is_visible()
            ]
            if len(titles) == 1:
                break
            if len(titles) > 1:
                raise RPAError("聊天标题不唯一，已停止")
            time.sleep(0.2)
        else:
            raise RPAError("未能确认当前聊天对象，已停止")

        search_edits[0].set_edit_text("")
        return window

    def read_current_messages(self, contact: str) -> list[ObservedMessage]:
        """Read visible text bubbles and classify their side; ambiguous bubbles fail closed."""
        window = self.main_window()
        contact = contact.strip()
        titles = [
            item for item in window.descendants(control_type="Text")
            if item.window_text() == contact
            and (item.element_info.automation_id or "").endswith(("current_chat_n", "current_chat_name_label"))
            and item.is_visible()
        ]
        if len(titles) != 1:
            raise RPAError(f"当前聊天标题不能唯一确认是{contact}，已停止读取")

        window_rect = window.rectangle()
        midpoint = window_rect.left + window_rect.width() / 2
        margin = max(24, window_rect.width() * 0.035)
        observed: list[ObservedMessage] = []
        for item in window.descendants(control_type="ListItem"):
            if "chat_bubble_item_view" not in (item.element_info.automation_id or "") or not item.is_visible():
                continue
            content = item.window_text().strip()
            if not content:
                continue
            candidates = []
            for child in item.descendants():
                try:
                    if child.is_visible() and child.window_text().strip():
                        rect = child.rectangle()
                        if rect.width() > 0 and rect.height() > 0:
                            candidates.append(rect)
                except Exception:
                    continue
            rect = max(candidates, key=lambda value: value.width() * value.height(), default=item.rectangle())
            bubble_midpoint = rect.left + rect.width() / 2
            if bubble_midpoint < midpoint - margin:
                sender = "customer"
            elif bubble_midpoint > midpoint + margin:
                sender = "sales"
            else:
                raise RPAError("消息气泡方向无法可靠判断，已停止读取并等待人工核对")
            observed.append(ObservedMessage(contact=contact, sender=sender, content=content))
        return observed

    def send_message_to_contact(self, contact: str, message: str) -> MessageResult:
        if "send_message_to_existing_contact" not in self.authorization.get("allowed_actions", []):
            raise RPAError("授权配置不允许向现有联系人发送消息")
        contact = contact.strip()
        message = message.strip()
        if not contact or len(contact) > 64:
            raise RPAError("联系人名称不合法")
        if not 1 <= len(message) <= 500:
            raise RPAError("消息长度必须在1-500字符之间")

        window = self.open_contact(contact)

        visible_text = "\n".join(
            item.window_text() for item in window.descendants(control_type="Text") if item.is_visible()
        )
        if any(word in visible_text for word in ("验证码", "安全验证", "操作频繁", "请稍后", "风险提示")):
            raise RPAError("检测到安全提示，已停止发送")

        inputs = [
            item for item in window.descendants(control_type="Edit")
            if item.element_info.automation_id == "chat_input_field"
            and item.is_visible() and item.is_enabled()
        ]
        if len(inputs) != 1:
            raise RPAError(f"预期1个聊天输入框，实际找到{len(inputs)}个")
        inputs[0].set_edit_text(message)
        send_buttons = [
            item for item in window.descendants(control_type="Button")
            if item.window_text() == "发送" and item.is_visible() and item.is_enabled()
        ]
        if len(send_buttons) != 1:
            inputs[0].set_edit_text("")
            raise RPAError(f"预期1个发送按钮，实际找到{len(send_buttons)}个")
        send_buttons[0].click_input()
        time.sleep(0.8)

        verified = inputs[0].window_text() == ""
        if not verified:
            raise RPAError("发送后输入框未清空；不会自动重试，请人工核对")
        return MessageResult(contact=contact, status="sent", message=message, verified=True)

    def send_message_to_current_contact(self, contact: str, message: str) -> MessageResult:
        """Send only when the already-open chat title uniquely matches contact."""
        if "send_message_to_existing_contact" not in self.authorization.get("allowed_actions", []):
            raise RPAError("授权配置不允许向现有联系人发送消息")
        contact = contact.strip()
        message = message.strip()
        if not contact or len(contact) > 64:
            raise RPAError("联系人名称不合法")
        if not 1 <= len(message) <= 500:
            raise RPAError("消息长度必须在1-500字符之间")

        window = self.main_window()
        titles = [
            item for item in window.descendants(control_type="Text")
            if item.window_text() == contact
            and (item.element_info.automation_id or "").endswith(("current_chat_n", "current_chat_name_label"))
            and item.is_visible()
        ]
        if len(titles) != 1:
            raise RPAError(f"当前聊天标题不能唯一确认是{contact}，已停止发送")

        visible_text = "\n".join(
            item.window_text() for item in window.descendants(control_type="Text") if item.is_visible()
        )
        if any(word in visible_text for word in ("验证码", "安全验证", "操作频繁", "请稍后", "风险提示")):
            raise RPAError("检测到安全提示，已停止发送")

        inputs = [
            item for item in window.descendants(control_type="Edit")
            if item.element_info.automation_id == "chat_input_field"
            and item.is_visible() and item.is_enabled()
        ]
        if len(inputs) != 1:
            raise RPAError(f"预期1个聊天输入框，实际找到{len(inputs)}个")
        inputs[0].set_edit_text(message)
        send_buttons = [
            item for item in window.descendants(control_type="Button")
            if item.window_text() == "发送" and item.is_visible() and item.is_enabled()
        ]
        if len(send_buttons) != 1:
            inputs[0].set_edit_text("")
            raise RPAError(f"预期1个发送按钮，实际找到{len(send_buttons)}个")
        send_buttons[0].click_input()
        time.sleep(0.25)
        if inputs[0].window_text() != "":
            raise RPAError("发送后输入框未清空；不会自动重试，请人工核对")
        return MessageResult(contact=contact, status="sent", message=message, verified=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="微信RPA搜索阶段驱动器（不提交好友申请）")
    parser.add_argument("target")
    parser.add_argument("--greeting")
    parser.add_argument("--submit", action="store_true")
    args = parser.parse_args()
    if args.submit:
        if not args.greeting:
            parser.error("--submit 必须同时提供 --greeting")
        result = WeixinDriver().submit_friend_request(args.target, args.greeting)
    else:
        result = WeixinDriver().search(args.target)
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2, default=str))
