from google.adk.agents import LlmAgent

from agent.helpers.instructions import business_instruction
from agent.helpers.model import chat_model, generation_config
from agent.sub_agents import SUB_AGENT_FACTORIES
from agent.tools import create_conversation_tools


def build_sub_agents(chatbot, session):
    """Compose the specialists this chatbot's features enable, in registry order."""
    specialists = []
    for factory in SUB_AGENT_FACTORIES:
        agent = factory(chatbot, session)
        if agent is not None:
            specialists.append(agent)
    return specialists


def _specialist_roster(specialists):
    return "\n".join(f"- {agent.name}: {agent.description}" for agent in specialists)


def root_agent(chatbot, session):
    """Thin coordinator: greet, delegate, relay. Specialists own tools and facts."""
    specialists = build_sub_agents(chatbot, session)
    roster = _specialist_roster(specialists)

    async def instruction(context):
        base = business_instruction(chatbot)

        if not specialists:
            return base + (
                "No specialist agents are enabled for this conversation. "
                "Answer the visitor directly and briefly using only the business "
                "instructions above, or the fallback when they do not cover it. "
                "When the visitor shows a meaningful buying signal, call "
                "record_lead_score with a 0-100 score and one short evidence "
                "sentence. When the visitor explicitly requests a person, a "
                "configured escalation rule applies, or you cannot safely answer, "
                "call request_human_escalation and say human help was requested."
            )

        return base + (
            "You are the conversation coordinator. You never answer business "
            "questions yourself; specialists do.\n"
            "Answer greetings and small talk yourself, briefly and warmly, with an "
            "empty context otherwise.\n"
            "Specialists available for this conversation:\n"
            f"{roster}\n"
            "Delegate each business task to exactly one specialist whose "
            "description matches the visitor's latest intent. Write a "
            "self-contained request that carries the relevant conversation "
            "context, such as the visitor's preferred date, any agreement to "
            "search next week, and known booking facts. For a mixed request, "
            "consult each relevant specialist once.\n"
            "Relay the specialist's answer to the visitor faithfully and "
            "completely: keep dates, slot offers, booking statuses, and cited "
            "sources intact. Do not invent, drop, or soften facts, and never "
            "answer a specialist's task from memory. If no specialist matches "
            "the request, answer briefly using only the business instructions "
            "above, or the fallback when they do not cover it."
        )

    return LlmAgent(
        name="root_agent",
        mode="chat",
        model=chat_model(),
        description="Routes visitor messages to the enabled specialists.",
        instruction=instruction,
        sub_agents=specialists,
        # Specialists own every tool; the root only carries conversation tools
        # when it is the whole chatbot (no specialist is enabled).
        tools=[] if specialists else create_conversation_tools(chatbot, session),
        generate_content_config=generation_config(),
    )
