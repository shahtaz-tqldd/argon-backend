from google.adk.agents import LlmAgent

from agent.helpers.load_instruction import load_sub_agent_instruction
from django.conf import settings

def appointment_agent(chatbot, session) -> LlmAgent:
    instruction = load_sub_agent_instruction("appointment")
    tools = [] 
    return LlmAgent(
        name="appointment_agent", 
        model=settings.GEMINI_MODEL,
        description="Handles appointment interest, collects configured details, checks slots, and books appointments.",
        instruction=instruction,
        tools=tools,
    )
