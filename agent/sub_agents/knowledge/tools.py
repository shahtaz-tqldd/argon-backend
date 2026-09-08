from google.adk.tools import FunctionTool
from app.utils.logger import logger
from vector_store.services.vectorize import KnowledgeVectorService
from asgiref.sync import sync_to_async


class KnowledgeTools:
    def __init__(self, chatbot, *, vector_service=None):
        self.chatbot = chatbot
        self.vector_service = vector_service or KnowledgeVectorService()

    async def search_knowledge(self, query: str) -> dict:
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


# from google.adk.tools import FunctionTool
# from google.adk.tools.tool_context import ToolContext
# from app.app_settings import logger
# from chat.schema import SessionExtendedModel
# from app.base.recommendations.faq_recommender import FAQRecommender




# async def faq_search_tool(session_info: SessionExtendedModel):
#     async def faq_search(tool_context: ToolContext):
#         """
#         Use this tool when `user_query` can be best answered using FAQ data. This tool searches the store's FAQ system to find relevant answers to user queries related to store policies, returns, shipping, payments, and other non-product inquiries.

#         Args:
#             tool_context: The context for the tool, which may include any relevant state information or parameters needed for the search.

#         returns:
#         {
#             "success": True, 
#             "message": "FAQ search completed successfully.",
#             "data": {
#                 "recommended_faqs": [{
# 					"question": "Some question",
# 					"answer": "Some answer"
# 				}]
#             }
#         }
#         """

#         faq_recommender = FAQRecommender(
#             bot_id=session_info.bot_id,
#             org_id=session_info.org_id,
#             query=session_info.query
#         )
#         recommended_faqs = await faq_recommender.recommend()
#         logger.info(f"FAQ search results: {recommended_faqs}")

#         # Store the reformed user query in the tool context state
#         tool_context.state.update({'user_reformed_query': session_info.query})

#         return {
#             "success": True, 
#             "message": "FAQ search completed successfully.",
#             "data": {
#                 "recommended_faqs": recommended_faqs
#             }
#         }

#     return FunctionTool(faq_search)
