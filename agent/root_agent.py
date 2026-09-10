from google.adk.agents import LlmAgent

from agent.helpers.instructions import business_instruction
from agent.helpers.model import chat_model, generation_config
from agent.sub_agents.appointment.agent import appointment_agent
from agent.sub_agents.knowledge.agent import knowledge_agent
from agent.utils.schema import KnowledgeBaseAgentOutputSchema


def root_agent(chatbot, session):
    async def instruction(context):
        return (
            business_instruction(chatbot)
            + "You coordinate the conversation. Handle greetings yourself. Delegate business "
            "questions to knowledge_agent and appointment requests or backend booking events "
            "to appointment_agent. For mixed requests, consult both specialists.\n"
            "Pass each specialist a self-contained request with relevant conversation context, "
            "including the visitor's preferred date and any agreement to search next week. "
            "Specialists return a result to you; relay their answer or clarification question. "
            "Preserve the knowledge specialist's source_ids and the appointment specialist's "
            "reported dates and booking status. Do not answer specialist tasks from memory.\n"
            "Return content and source_ids. Use an empty source_ids list for greetings and "
            "booking-only answers. Lead scoring is admin-only and not available in visitor chat."
        )

    return LlmAgent(
        name="root_agent",
        mode="chat",
        model=chat_model(),
        description="Lightweight coordinator for knowledge and appointment specialists.",
        instruction=instruction,
        sub_agents=[knowledge_agent(chatbot, session), appointment_agent(chatbot)],
        output_schema=KnowledgeBaseAgentOutputSchema,
        generate_content_config=generation_config(),
    )
