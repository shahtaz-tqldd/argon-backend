from google.adk.agents import LlmAgent

from agent.helpers.global_tools import create_global_tools
from agent.helpers.load_instruction import load_sub_agent_instruction
from agent.helpers.model import chat_model, generation_config
from agent.sub_agents.knowledge.tools import create_knowledge_tools
from agent.schema import SpecialistResponseSchema


def build(chatbot, session) -> LlmAgent | None:
    """Answer grounded business questions; None when the feature is off."""
    if not getattr(chatbot, "knowledge_base_enabled", False):
        return None

    def instruction(_context):
        # GlobalInstructionPlugin supplies identity and shared business policy.
        return load_sub_agent_instruction("knowledge")

    return LlmAgent(
        name="knowledge_agent",
        mode="single_turn",
        model=chat_model(),
        description=(
            "Answers business, service, and product questions from the knowledge "
            "base, the conversation context, or the configured business facts, "
            "returning content with the source_ids it used."
        ),
        instruction=instruction,
        tools=[
            *create_knowledge_tools(chatbot),
            *create_global_tools(chatbot, session),
        ],
        output_schema=SpecialistResponseSchema,
        generate_content_config=generation_config(),
    )
