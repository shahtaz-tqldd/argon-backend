from google.adk.agents import LlmAgent

from agent.helpers.instructions import business_instruction
from agent.helpers.load_instruction import load_sub_agent_instruction
from agent.helpers.model import chat_model, generation_config
from agent.sub_agents.knowledge.tools import create_knowledge_tools
from agent.utils.schema import KnowledgeBaseAgentOutputSchema


def knowledge_agent(chatbot, session) -> LlmAgent:
    async def instruction(context):
        return business_instruction(chatbot) + load_sub_agent_instruction("knowledge")

    return LlmAgent(
        name="knowledge_agent",
        mode="single_turn",
        model=chat_model(),
        description="Answers business questions with knowledge source IDs.",
        instruction=instruction,
        tools=create_knowledge_tools(chatbot),
        output_schema=KnowledgeBaseAgentOutputSchema,
        generate_content_config=generation_config(),
    )
