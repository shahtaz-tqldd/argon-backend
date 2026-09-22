import textwrap
from google.adk.agents import LlmAgent

from agent.helpers.model import chat_model, generation_config
from agent.helpers.global_tools import create_global_tools

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
                You are the coordinator.

                Delegate business requests to the appropriate specialist.
                For multiple independent intents, delegate each relevant task.

                Handle greetings and brief conversational replies yourself.

                Do not answer specialist tasks yourself.
                If no specialist can handle a business request, use the configured fallback.
            """)
        tools = create_global_tools(chatbot, session)



    else:
        def instruction(_context):
            return (
                "Answer business-related requests using the available business instructions. "
                "If the required information is unavailable, use the configured fallback."
            )
        tools = create_global_tools(chatbot, session)



    return LlmAgent(
        name="root_agent",
        mode="chat",
        model=chat_model(),
        description=(
            "Coordinates chatbot's conversations, handles simple conversational turns, "
            "and delegates specialized business tasks to the appropriate enabled agent "
            "identifying the visitor's intent."
        ),
        instruction=instruction,
        sub_agents=specialists,
        tools=tools,
        generate_content_config=generation_config(),
    )