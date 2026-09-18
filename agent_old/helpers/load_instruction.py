
from pathlib import Path

from app.utils.logger import logger

SUB_AGENTS_DIR = Path(__file__).resolve().parent.parent / "sub_agents"


def load_sub_agent_instruction(sub_agent_name: str) -> str:
    """
    Load the instruction stored alongside a sub-agent's agent.py and tools.py.
    """
    if not sub_agent_name or Path(sub_agent_name).name != sub_agent_name:
        raise ValueError("sub_agent_name must be a directory name")

    instruction_path = SUB_AGENTS_DIR / sub_agent_name / "instruction.txt"

    try:
        return instruction_path.read_text(encoding="utf-8")
    except OSError:
        logger.exception(
            "Could not load instruction for sub-agent %s from %s",
            sub_agent_name,
            instruction_path,
        )
        return "No instruction available."
