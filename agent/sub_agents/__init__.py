"""Specialist sub-agents composed per chatbot feature.

Each specialist lives in its own package and exposes
``build(chatbot, session) -> LlmAgent | None``. A factory returns ``None``
when the chatbot does not have that feature enabled, and the root
coordinator is assembled from whatever remains.

To add a specialist later (service recommendation, quotation generation,
product recommendation, ...):

1. Create ``agent/sub_agents/<name>/`` with ``agent.py`` exposing ``build``,
   an ``instruction.txt``, and its own ``tools`` module. Gate ``build`` on
   the chatbot feature flag so the specialist only exists when enabled.
2. Import the factory and append it to ``SUB_AGENT_FACTORIES`` below in the
   intended delegation order.

No other wiring is required: the coordinator instruction, delegate tools,
and streaming are derived from this registry automatically.
"""
from agent.sub_agents.appointment.agent import build as build_appointment_agent
from agent.sub_agents.knowledge.agent import build as build_knowledge_agent

SUB_AGENT_FACTORIES = (
    build_knowledge_agent,
    build_appointment_agent,
)

__all__ = ["SUB_AGENT_FACTORIES"]
