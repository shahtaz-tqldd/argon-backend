from google.adk.agents import LlmAgent

from agent.helpers.load_instruction import load_sub_agent_instruction


def knowledge_agent(model, common_instruction, search_tool) -> LlmAgent:
    instruction = load_sub_agent_instruction("knowledge")
    return LlmAgent(
        name="knowledge_agent",
        model=model,
        description="Answers business questions using the chatbot's configured knowledge sources.",
        instruction=f"{common_instruction}\n{instruction}",
        tools=[search_tool],
    )
