from google.adk.agents import LlmAgent

from agent.helpers.load_instruction import load_sub_agent_instruction
from agent.helpers.model import chat_model, generation_config
from agent.utils.schema import LeadSummaryAgentOutputSchema


def lead_summary_agent(chatbot) -> LlmAgent:
    return LlmAgent(
        name="lead_summary_agent",
        mode="single_turn",
        model=chat_model(),
        description="Admin-only conversation summary and lead score.",
        instruction=load_sub_agent_instruction("lead_summary")
        + f"\nBusiness: {chatbot.business_name}.",
        output_schema=LeadSummaryAgentOutputSchema,
        generate_content_config=generation_config(),
    )
