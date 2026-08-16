import unittest

from rpa.weixin_driver import (
    AUTHORIZATION_EXAMPLE_PATH,
    ObservedMessage,
    RPAError,
    SearchStatus,
    classify_search_result,
    load_authorization,
    normalize_target,
)
from rpa.worker_agent import fingerprint_messages, reliable_new_messages


class RpaDriverTest(unittest.TestCase):
    def test_authorization_is_fail_closed_and_limited(self):
        authorization = load_authorization(AUTHORIZATION_EXAMPLE_PATH)
        self.assertTrue(authorization["fail_closed"])
        self.assertGreaterEqual(authorization["daily_limit"], 1)
        self.assertLessEqual(authorization["daily_limit"], 150)

    def test_target_validation(self):
        self.assertEqual(normalize_target(" fruit_store_01 "), "fruit_store_01")
        with self.assertRaises(RPAError):
            normalize_target("bad target with spaces")

    def test_not_found_result(self):
        result = classify_search_result(
            "missing_user",
            ["无法找到该用户，请检查你填写的账号是否正确。"],
            ["搜索", "关闭"],
        )
        self.assertEqual(result.status, SearchStatus.NOT_FOUND)

    def test_risk_result_always_pauses(self):
        result = classify_search_result("target", ["操作频繁，请稍后再试"], ["确定"])
        self.assertEqual(result.status, SearchStatus.PAUSED)

    def test_pending_verification_is_idempotent(self):
        result = classify_search_result("target", ["Nomis"], ["等待验证"])
        self.assertEqual(result.status, SearchStatus.PENDING_VERIFICATION)

    def test_worker_only_reports_messages_after_reliable_anchor(self):
        initial = fingerprint_messages([
            ObservedMessage("客户甲", "customer", "你好"),
            ObservedMessage("客户甲", "sales", "老板您好"),
        ])
        current = fingerprint_messages([
            ObservedMessage("客户甲", "customer", "你好"),
            ObservedMessage("客户甲", "sales", "老板您好"),
            ObservedMessage("客户甲", "customer", "价格怎么拿"),
        ])
        new_rows = reliable_new_messages([row["fingerprint"] for row in initial], current)
        self.assertEqual([row["content"] for row in new_rows], ["价格怎么拿"])

    def test_worker_refuses_to_guess_when_message_anchor_disappears(self):
        initial = fingerprint_messages([ObservedMessage("客户甲", "customer", "你好")])
        replaced = fingerprint_messages([ObservedMessage("客户甲", "customer", "完全不同的可见历史")])
        with self.assertRaises(RPAError):
            reliable_new_messages([row["fingerprint"] for row in initial], replaced)


if __name__ == "__main__":
    unittest.main()
