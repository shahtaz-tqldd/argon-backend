import json
import textwrap
from datetime import datetime
from zoneinfo import ZoneInfo

from google.adk.agents import LlmAgent

from agent.helpers.global_tools import create_global_tools
from agent.helpers.load_instruction import load_sub_agent_instruction
from agent.helpers.model import chat_model, generation_config
from agent.sub_agents.appointment.context import current_booking_confirmation
from agent.sub_agents.appointment.tools import create_appointment_tools
from agent.schema import AppointmentAgentResponseSchema


def build(chatbot, session) -> LlmAgent | None:
    """Build the appointment specialist when booking is enabled."""
    if not getattr(chatbot, "appointment_booking_enabled", False):
        return None

    def instruction(_context):
        confirmation = current_booking_confirmation()
        base_instruction = load_sub_agent_instruction("appointment")
        extra_instruction = textwrap.dedent(f"""\
            Business timezone: {chatbot.timezone}.
            Today's business date: {datetime.now(ZoneInfo(chatbot.timezone)).date().isoformat()}.
            Backend-verified booking event for this turn: {json.dumps(confirmation)}.
        """)
        return f"{base_instruction}\n{extra_instruction}"

    return LlmAgent(
        name="appointment_agent",
        mode="single_turn",
        model=chat_model(),
        description=(
            "Checks appointment dates and returns a visitor-facing message plus the "
            "date the visitor has agreed to."
        ),
        instruction=instruction,
        tools=[
            *create_appointment_tools(chatbot),
            *create_global_tools(chatbot, session),
        ],
        output_schema=AppointmentAgentResponseSchema,
        generate_content_config=generation_config(),
    )
