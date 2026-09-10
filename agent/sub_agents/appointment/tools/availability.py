from asgiref.sync import sync_to_async
from google.adk.tools import FunctionTool, ToolContext

from . import booking


def create_appointment_tools(chatbot):
    async def find_appointment_availability(
            requested_date: str, 
            tool_context: ToolContext
        ) -> dict:
        """Find the first available date within seven days including requested_date.

        Args:
            requested_date: Visitor's preferred date as YYYY-MM-DD in the business timezone.
        """
        # Invocation-local state enforces the budget even if the model tries again.
        previous = tool_context.state.get("temp:appointment_search")

        if previous is not None:
            return previous

        if tool_context.state.get("temp:booking_confirmation"):
            return {
                "status": "disabled", 
                "available": False,
                "message": "Acknowledge the saved booking; no search is needed."
            }

        # Reserve before yielding so parallel tool calls cannot search another window.
        tool_context.state["temp:appointment_search"] = {
            "status": "search_in_progress", 
            "available": False,
            "message": "An availability search is already in progress this turn.",
        }

        result = await sync_to_async(booking.find_availability)(chatbot.id, requested_date)

        tool_context.state["temp:appointment_search"] = result

        return result

    return [FunctionTool(find_appointment_availability)]
