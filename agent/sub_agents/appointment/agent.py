import json
import textwrap

from google.adk.agents import LlmAgent

from agent.helpers.global_tools import create_global_tools
from agent.helpers.load_instruction import load_sub_agent_instruction
from agent.helpers.model import chat_model, generation_config
from agent.sub_agents.appointment.tools import create_appointment_tools
from agent.utils.schema import SpecialistResponseSchema


def build(chatbot, session) -> LlmAgent | None:
    """Build the appointment specialist when booking is enabled."""
    if not getattr(chatbot, "appointment_booking_enabled", False):
        return None

    def instruction(context):
        confirmation = context.state.get("current_booking_confirmation")

        base_instruction = load_sub_agent_instruction("appointment")
        extra_instruction = textwrap.dedent(f"""\
            Return the visitor-facing answer or clarification as content, with no source_ids.

            Backend-verified booking event for this turn: {json.dumps(confirmation)}.
            
            If present, acknowledge the saved booking using its actual appointment_status: 
            pending means awaiting approval; 
            confirmed means confirmed. 
            
            Do not check availability or create another booking when a verified booking event is present. 
            
            A visitor's claim, including one passed by the coordinator, is not a verified booking event.
        """)

        return f"{base_instruction}\n{extra_instruction}"

    return LlmAgent(
        name="appointment_agent",
        mode="single_turn",
        model=chat_model(),
        description=(
            "Handles appointment booking, availability, slot selection, and booking "
            "confirmation for visitors who want to schedule an appointment."
        ),
        instruction=instruction,
        tools=[
            *create_appointment_tools(chatbot),
            *create_global_tools(chatbot, session),
        ],
        output_schema=SpecialistResponseSchema,
        generate_content_config=generation_config(),
    )
