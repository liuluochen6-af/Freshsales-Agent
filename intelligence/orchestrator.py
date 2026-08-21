from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Iterable


AGENT_LABELS = {
    "lead_agent": "线索培育 Agent",
    "product_agent": "商品咨询 Agent",
    "quotation_agent": "报价 Agent",
    "inventory_agent": "库存 Agent",
    "order_agent": "订单 Agent",
    "fulfillment_agent": "履约物流 Agent",
    "after_sales_agent": "售后 Agent",
    "compliance_agent": "合规 Agent",
}


@dataclass(frozen=True)
class IntentRule:
    intent: str
    agent: str
    terms: tuple[str, ...]
    weight: int = 10


@dataclass
class RoutingDecision:
    intent: str
    intent_group: str
    primary_agent: str
    supporting_agents: list[str] = field(default_factory=list)
    routing_reason: str = ""
    routing_confidence: float = 0.0
    entities: dict[str, list[str]] = field(default_factory=dict)
    risk: str = "low"
    requires_human: bool = False
    proposed_actions: list[str] = field(default_factory=list)

    @property
    def agent_types(self) -> list[str]:
        return [self.primary_agent, *self.supporting_agents]

    def to_dict(self) -> dict:
        result = asdict(self)
        result["agent_types"] = self.agent_types
        result["primary_agent_label"] = AGENT_LABELS.get(self.primary_agent, self.primary_agent)
        result["supporting_agent_labels"] = [AGENT_LABELS.get(item, item) for item in self.supporting_agents]
        return result


class SalesAgentOrchestrator:
    """Sales-domain routing inspired by EchoMind's structured RoutingDecision.

    The router only classifies and proposes actions. It never changes prices,
    inventory, orders, contact permissions, or message queues.
    """

    RULES = (
        IntentRule("stop_contact", "compliance_agent", ("不要联系", "不要再联系", "别联系", "别再联系", "别再发", "停止联系", "删掉我", "把我的信息删掉", "删除我的信息"), 100),
        IntentRule("privacy_dispute", "compliance_agent", ("隐私", "泄露", "非法获取", "买卖信息", "没授权", "号码哪来", "联系方式哪来", "举报"), 90),
        IntentRule("contract_or_credit", "compliance_agent", ("合同", "账期", "月结", "先货后款", "营业执照", "资质"), 70),
        IntentRule("refund_or_compensation", "after_sales_agent", ("退款", "赔偿", "赔付", "投诉", "坏果", "破损", "漏液", "拒收", "质量问题"), 80),
        IntentRule("shipment_tracking", "fulfillment_agent", ("物流", "快递", "运单", "到哪", "发货", "几天到", "催单"), 55),
        IntentRule("order_change", "order_agent", ("订单", "下单", "取消订单", "改地址", "收货", "付款", "打款"), 50),
        IntentRule("inventory_check", "inventory_agent", ("库存", "现货", "有货", "缺货", "到货", "批次"), 45),
        IntentRule("price_inquiry", "quotation_agent", ("价格", "多少钱", "报价", "运费", "优惠", "便宜", "批量价", "起订"), 45),
        IntentRule("product_question", "product_agent", ("猫山王", "金枕", "干尧", "黑刺", "品种", "规格", "等级", "成熟度", "甜度", "口感", "包装"), 30),
        IntentRule("purchase_intent", "lead_agent", ("采购", "合作", "试单", "先来", "来一箱", "来两箱", "长期合作", "怎么拿货"), 25),
    )

    HIGH_RISK_INTENTS = {"stop_contact", "privacy_dispute", "contract_or_credit", "refund_or_compensation"}
    ACTIONS = {
        "stop_contact": ["stop_marketing", "human_review"],
        "privacy_dispute": ["human_review"],
        "contract_or_credit": ["human_review"],
        "refund_or_compensation": ["collect_evidence", "human_review"],
        "shipment_tracking": ["get_shipment_status"],
        "order_change": ["get_order_status", "prepare_order_change"],
        "inventory_check": ["check_inventory"],
        "price_inquiry": ["check_current_price", "calculate_freight", "prepare_quote"],
        "product_question": ["search_products"],
        "purchase_intent": ["qualify_lead"],
        "general_conversation": ["continue_conversation"],
    }

    def route(self, message: str, history: Iterable[str] | None = None) -> RoutingDecision:
        text = (message or "").strip()
        scores: dict[str, int] = {name: 0 for name in AGENT_LABELS}
        intent_scores: list[tuple[int, IntentRule, list[str]]] = []

        for rule in self.RULES:
            hits = [term for term in rule.terms if term.lower() in text.lower()]
            if not hits:
                continue
            score = rule.weight + min(20, (len(hits) - 1) * 5)
            scores[rule.agent] += score
            intent_scores.append((score, rule, hits))

        if not intent_scores:
            primary_agent = "lead_agent"
            intent = "general_conversation"
            confidence = 0.55
            reason = "未命中高风险或交易事实意图，由线索培育 Agent 继续低风险对话"
        else:
            intent_scores.sort(key=lambda item: item[0], reverse=True)
            primary_agent = max(scores, key=scores.get)
            _, winning_rule, winning_hits = next(
                item for item in intent_scores if item[1].agent == primary_agent
            )
            intent = winning_rule.intent
            total = sum(scores.values()) or 1
            confidence = min(0.98, 0.58 + scores[primary_agent] / total * 0.35 + min(len(winning_hits), 3) * 0.02)
            reason = f"命中{','.join(dict.fromkeys(winning_hits))}，{AGENT_LABELS[primary_agent]}得分最高"

        supporting = [
            agent for agent, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
            if agent != primary_agent and score > 0 and score >= max(20, scores[primary_agent] * 0.35)
        ][:2]
        if supporting:
            reason += "；辅助处理：" + "、".join(AGENT_LABELS[item] for item in supporting)

        requires_human = intent in self.HIGH_RISK_INTENTS
        risk = "high" if requires_human else ("medium" if intent in {"shipment_tracking", "order_change", "inventory_check", "price_inquiry"} else "low")
        return RoutingDecision(
            intent=intent,
            intent_group=self._intent_group(intent),
            primary_agent=primary_agent,
            supporting_agents=supporting,
            routing_reason=reason,
            routing_confidence=round(confidence, 4),
            entities=self._extract_entities(text),
            risk=risk,
            requires_human=requires_human,
            proposed_actions=self.ACTIONS.get(intent, []),
        )

    @staticmethod
    def _intent_group(intent: str) -> str:
        if intent in {"price_inquiry", "inventory_check", "purchase_intent", "product_question"}:
            return "sales"
        if intent in {"order_change", "shipment_tracking"}:
            return "fulfillment"
        if intent == "refund_or_compensation":
            return "after_sales"
        if intent in {"stop_contact", "privacy_dispute", "contract_or_credit"}:
            return "compliance"
        return "general"

    @staticmethod
    def _extract_entities(text: str) -> dict[str, list[str]]:
        entities: dict[str, list[str]] = {}
        patterns = {
            "quantity": r"\d+(?:\.\d+)?\s*(?:箱|件|粒|斤|公斤|kg|KG)",
            "money": r"\d+(?:\.\d+)?\s*元",
            "order_no": r"\b(?:DR)?\d{10,22}\b",
            "tracking_no": r"\b[A-Z]{1,4}[A-Z0-9-]{7,24}\b",
        }
        for name, pattern in patterns.items():
            values = list(dict.fromkeys(re.findall(pattern, text)))
            if values:
                entities[name] = values
        products = [item for item in ("猫山王", "金枕", "干尧", "黑刺") if item in text]
        if products:
            entities["product"] = products
        return entities
