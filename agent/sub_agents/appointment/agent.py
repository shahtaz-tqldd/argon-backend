from google.adk.agents import LlmAgent

from agent.helpers.load_instruction import load_sub_agent_instruction


def appointment_agent(model, common_instruction, tools):
    instruction = load_sub_agent_instruction("appointment")
    return LlmAgent(
        name="appointment_agent", model=model,
        description="Handles appointment interest, collects configured details, checks slots, and books appointments.",
        instruction=f"{common_instruction}\n{instruction}",
        tools=list(tools),
    )
