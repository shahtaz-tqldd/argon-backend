from asgiref.sync import sync_to_async
from google.adk.tools import FunctionTool, ToolContext

from app.utils.logger import logger
from vector_store.services.vectorize import KnowledgeVectorService


RETRIEVED_SOURCE_IDS_KEY = "knowledge_source_ids"


class KnowledgeTools:
    def __init__(self, chatbot, *, vector_service=None):
        self.chatbot = chatbot
        self.vector_service = vector_service or KnowledgeVectorService()

    async def search_knowledge(self, query: str, tool_context: ToolContext) -> dict:
        """
        Search this chatbot's knowledge base for facts relevant to the
        customer's question.

        Args:
            query: A concise standalone search query.

        Returns:
            Relevant knowledge sources for this chatbot.
        """

        if not self.chatbot.knowledge_base_enabled:
            return {
                "status": "disabled",
                "sources": [],
            }

        try:
            results = await sync_to_async(
                self.vector_service.search,
                thread_sensitive=False,
            )(
                query,
                chatbot_id=self.chatbot.id,
                limit=5,
            )

            if not results:
                return {
                    "status": "not_found",
                    "sources": [],
                }

            # Remember every retrieved source so later turns may answer from
            # conversation context and still cite what was retrieved before.
            retrieved_ids = {
                str(result.knowledge_base_id)
                for result in results
            }
            known_ids = set(
                tool_context.state.get(RETRIEVED_SOURCE_IDS_KEY) or []
            )
            known_ids.update(retrieved_ids)
            tool_context.state[RETRIEVED_SOURCE_IDS_KEY] = sorted(known_ids)

            return {
                "status": "ok",
                "sources": [
                    {
                        "source_id": str(result.knowledge_base_id),
                        "content": result.content,
                    }
                    for result in results
                ],
            }

        except Exception:
            logger.exception(
                "Knowledge retrieval failed for chatbot %s",
                self.chatbot.id,
            )

            return {
                "status": "error",
                "sources": [],
                "message": "Knowledge search is temporarily unavailable.",
            }


def create_knowledge_tools(chatbot):
    knowledge_tools = KnowledgeTools(chatbot)

    return [
        FunctionTool(
            func=knowledge_tools.search_knowledge,
        ),
    ]
