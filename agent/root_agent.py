from google.adk.agents import LlmAgent

from agent.helpers.instructions import business_instruction
from agent.helpers.model import chat_model, generation_config
from agent.sub_agents.appointment.agent import appointment_agent
from agent.sub_agents.knowledge.agent import knowledge_agent
from agent.tools import create_conversation_tools
from agent.utils.schema import KnowledgeBaseAgentOutputSchema


def root_agent(chatbot, session):
    async def instruction(context):
        lead_available = bool(getattr(session, "lead_id", None))
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
            "When the visitor provides a meaningful qualification signal (a concrete need, "
            "purchase intent, timeline, budget, booking intent, or committed next step), call "
            "record_lead_score with a 0-100 score and one very short evidence-based sentence. "
            "Do not score greetings, casual browsing, or unsupported assumptions. Only call "
            f"the tool when a lead record is available. Lead record available: {lead_available}.\n"
            "When the visitor explicitly requests a person, a configured escalation rule "
            "applies, you cannot safely or confidently answer, or a required tool fails, call "
            "request_human_escalation with a concise, dashboard-ready reason. "
            "After calling it, tell the visitor that human attention has been requested.\n"
            "Return content and source_ids. Use an empty source_ids list for greetings and "
            "booking-only answers. Never mention internal lead scores to the visitor."
        )

    return LlmAgent(
        name="root_agent",
        mode="chat",
        model=chat_model(),
        description="Lightweight coordinator for knowledge and appointment specialists.",
        instruction=instruction,
        sub_agents=[knowledge_agent(chatbot, session), appointment_agent(chatbot)],
        tools=create_conversation_tools(chatbot, session),
        output_schema=KnowledgeBaseAgentOutputSchema,
        generate_content_config=generation_config(),
    )
