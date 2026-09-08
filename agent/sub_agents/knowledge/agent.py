from google.adk.agents import LlmAgent

from agent.helpers.load_instruction import load_sub_agent_instruction
from django.conf import settings

def knowledge_agent(chatbot, session) -> LlmAgent:
    instruction = load_sub_agent_instruction("knowledge")
    tools = []

    return LlmAgent(
        name="knowledge_agent",
        model=settings.GEMINI_MODEL,
        description="Answers business questions using the chatbot's configured knowledge sources.",
        instruction=instruction,
        tools=tools,
    )
