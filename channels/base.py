from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class FriendRequestResult:
    success: bool
    external_task_id: str = ""
    status: str = "已发送"
    note: str = ""


class WechatChannelAdapter(ABC):
    """腾讯批准通道的最小业务接口。真实接入只需实现这一层。"""

    @abstractmethod
    def add_friend(self, *, account: dict, lead: dict, greeting: str) -> FriendRequestResult:
        raise NotImplementedError
