from __future__ import annotations

import os

from .base import FriendRequestResult, WechatChannelAdapter


class TencentApprovedAdapter(WechatChannelAdapter):
    """
    腾讯批准接口占位实现。

    收到正式接口文档后，在这里完成签名、请求、错误码映射和回调校验。
    业务层不需要改动。严禁在此实现客户端注入、协议破解或绕过批准额度。
    """

    def __init__(self):
        self.base_url = os.getenv("TENCENT_WECHAT_BASE_URL", "")
        self.app_id = os.getenv("TENCENT_WECHAT_APP_ID", "")
        self.app_secret = os.getenv("TENCENT_WECHAT_APP_SECRET", "")

    def add_friend(self, *, account: dict, lead: dict, greeting: str) -> FriendRequestResult:
        if not all((self.base_url, self.app_id, self.app_secret)):
            return FriendRequestResult(False, note="正式微信通道尚未配置接口地址或凭证")
        raise NotImplementedError("请根据腾讯批准接口文档实现 add_friend 请求")
