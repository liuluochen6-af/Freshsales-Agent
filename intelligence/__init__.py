"""SalesFlow intelligence layer.

Probabilistic routing stays separate from deterministic business rules. Business
state and send authority always remain in SalesFlow.
"""

from .echomind_client import EchoMindClient
from .orchestrator import RoutingDecision, SalesAgentOrchestrator
from .skills import SkillManager

__all__ = ["EchoMindClient", "RoutingDecision", "SalesAgentOrchestrator", "SkillManager"]
