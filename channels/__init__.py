from .mock_wechat import MockWechatAdapter


def get_adapter(mode: str = "mock"):
    if mode == "mock":
        return MockWechatAdapter()
    if mode == "tencent_approved":
        from .tencent_approved import TencentApprovedAdapter
        return TencentApprovedAdapter()
    raise ValueError(f"未知微信通道模式: {mode}")
