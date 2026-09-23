import textwrap

from google.adk.agents import LlmAgent

from agent.helpers.global_tools import create_global_tools
from agent.helpers.model import chat_model, generation_config
from agent.sub_agents import SUB_AGENT_FACTORIES


def build_sub_agents(chatbot, session):
    """
    Compose the specialists this chatbot's features enable, in registry order.
    """
    specialists = []
    for factory in SUB_AGENT_FACTORIES:
        agent = factory(chatbot, session)
        if agent is not None:
            specialists.append(agent)
    return specialists


def root_agent(chatbot, session):
    specialists = build_sub_agents(chatbot, session)

    if specialists:
        def instruction(_context):
            return textwrap.dedent("""\
                Coordinate the conversation.
                - Handle greetings, identity, thanks, and cross-cutting lead or
                  escalation actions.
                - Delegate domain work to the matching specialist; delegate each
                  independent intent.
                - Relay specialist facts, dates, statuses, and limitations accurately.
                - Do not answer a specialist's domain from your own knowledge.
                - Use the configured fallback when no specialist can handle an
                  in-scope request.
            """)

        tools = create_global_tools(chatbot, session)
    else:

        def instruction(_context):
            return (
                "Handle the conversation directly using the shared business policy and "
                "available tools. Use the configured fallback for unavailable facts."
            )

        tools = create_global_tools(chatbot, session)

    return LlmAgent(
        name="root_agent",
        mode="chat",
        model=chat_model(),
        description=(
            "Coordinates chatbot conversations, handles simple conversational turns, "
            "and delegates specialized business tasks to the appropriate enabled agent "
            "identifying the visitor's intent."
        ),
        instruction=instruction,
        sub_agents=specialists,
        tools=tools,
        generate_content_config=generation_config(),
    )
