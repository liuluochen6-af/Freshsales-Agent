from __future__ import annotations

from uuid import uuid4

from .base import FriendRequestResult, WechatChannelAdapter


class MockWechatAdapter(WechatChannelAdapter):
    def add_friend(self, *, account: dict, lead: dict, greeting: str) -> FriendRequestResult:
        target = lead.get("wechat_id") or lead.get("phone")
        if not target:
            return FriendRequestResult(False, note="线索缺少微信号或手机号")
        return FriendRequestResult(
            True,
            external_task_id=f"mock-{uuid4().hex[:12]}",
            note=f"模拟通道已向 {target} 提交好友申请",
        )
