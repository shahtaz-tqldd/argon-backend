# Chatbot agents (Google ADK 2.1.0)

`AgentClient` has three entry points. The root `LlmAgent` is a lightweight
coordinator with `knowledge_agent` and `appointment_agent` in `sub_agents`.
Specialists own their tools and business instructions. They use ADK's
`mode="single_turn"` delegation: each returns its answer or clarification question
to the root, which maintains the visitor conversation. ADK automatically exposes
these sub-agents as delegation calls; the root has no retrieval or booking tools.
Admin lead analysis uses a separate, tool-free agent outside the visitor team.

Chat and lead analysis use `Workflow`. A small `initialize_turn` Python node writes
trusted backend state before the agent runs. This avoids relying on the
`Runner.run_async(state_delta=...)` argument that ADK 2.1.0's node path does not
forward. The initialization node adds no model calls. Knowledge and appointment
requests incur coordinator calls as well as specialist calls; usage and cost
include both.

Booking operations live together under `agent/sub_agents/appointment/tools/`:

- `booking.py`: slot calculation, bounded date search, saved-booking verification,
  and the appointment transcript query.
- `availability.py`: the appointment specialist's ADK tool factory.
- `__init__.py`: exports the factory.

```python
from agent.client import AgentClient

client = AgentClient(chatbot, chat_session)
response = await client.chat("What services do you offer?")
# Synchronous Django callers: client.chat_sync(message="...")
```

All entry points return a JSON-serializable dictionary:

```json
{
  "result": {
    "content": "We offer …",
    "source_ids": ["knowledge-base-uuid"],
    "appointment": null,
    "lead_summary": null
  },
  "token": {
    "input_tokens": 200,
    "output_tokens": 50,
    "thinking_tokens": 10,
    "cached_input_tokens": 20,
    "total_tokens": 250
  },
  "cost": 0.000185
}
```

`source_ids` identifies `KnowledgeBase` rows actually selected by the model from
this turn's retrieved sources. Duplicate, invented, and stale IDs are removed.
Fetch original source details through a chatbot-scoped backend query. Content is
validated structured output; invalid JSON raises an error instead of leaking raw
model output to the UI.

Token usage sums ADK model events across all calls in the invocation, including
tool-selection and final-response calls. `output_tokens` includes thinking;
`thinking_tokens` and `cached_input_tokens` are subsets, not additional totals.
`cost` is an estimated USD amount using `GEMINI_INPUT_COST_PER_MILLION` and
`GEMINI_OUTPUT_COST_PER_MILLION`. It excludes retrieval embeddings and other
infrastructure charges and does not apply cache discounts or pricing tiers.
Configure rates for your selected model. Missing provider usage contributes zero;
this estimate is not a billing receipt.

## Appointment UI flow

1. Chat asks for the preferred date, resolving it in the chatbot timezone.
2. `find_appointment_availability` searches the preferred date plus at most six
   following dates and stops at the first available day. Only one search window
   can run per invocation, enforced through ADK `temp:` state. No slots are
   returned in the chat response.
3. On success, `result.appointment` contains `status: "available"`,
   `available: true`, `requested_date`, `date`, `searched_through`, and `timezone`.
   For June 16 with first availability on June 18, `date` is `2026-06-18`.
   Fetch current slots for that day in your backend and render the UI. The existing
   `agent.sub_agents.appointment.tools.booking.booking_schedule(chatbot.id, date)` helper returns slots.
4. If June 16–22 is unavailable, `available` is false, `date` is null,
   `searched_through` is June 22, and `next_search_date` is June 23. Chat asks whether
   to try next week and waits for another visitor turn. Searches stop at the
   configured booking horizon; disabled/invalid requests have distinct statuses.
5. Your booking endpoint validates the selected slot and fields, rechecks
   availability, and saves the appointment. **Set
   `Appointment.metadata["chat_session_id"] = str(chat_session.id)` in trusted
   backend code**, after verifying conversation ownership. Do not accept that
   association from untrusted UI metadata. All booking writers should lock the
   booking configuration during availability checking and saving.
6. After the save commits, call the backend-only confirmation method:

```python
response = await client.confirm_booking(appointment_id=str(appointment.id))
# Or client.confirm_booking_sync(appointment_id=...)
```

The method reads the saved appointment scoped to the chatbot, conversation, and
pending/confirmed status. It creates no appointment. ADK records the backend event
in conversation history, and the initialization node commits `booking_confirmations`
state before model execution.
The response contains `status: "booking_recorded"`, appointment ID, actual status,
and start/end timestamps. A pending request is described as awaiting approval.
`available` applies to availability offers only and is false for recorded bookings.
Repeated confirmation calls reuse the state record keyed by appointment ID but
produce another acknowledgment turn; callers should deduplicate delivery retries.
A failure during acknowledgment does not undo the saved booking or event.

## Admin lead analysis

```python
response = await client.generate_lead_summary()
score = response["result"]["lead_summary"]["score"]  # integer, 0–100
summary = response["result"]["lead_summary"]["summary"]
# Or client.generate_lead_summary_sync()
```

Authorize admin access in the caller. Analysis reads **all** Django messages in
chronological order, plus saved appointments associated with that conversation.
It does not depend on a visitor ADK user ID and does not add an admin request to
visitor history. Each analysis uses a fresh in-memory session; it writes no lead
score or summary. The caller decides how to persist the returned values.
At least one visitor message is required; there is no arbitrary five-message
threshold. Very large transcripts may exceed the model context limit; they are
not silently truncated. Message attachments are not downloaded or analyzed.

The scoring rubric gives up to 30 points each for need/fit and purchase intent,
and up to 20 each for timeline and engagement/next steps. Missing evidence earns
no points; the short summary explains the score. This is a model assessment, not
a calibrated prediction of conversion.

## Configuration and integration boundary

Initialize Django before importing the client. The existing settings are used:
`GEMINI_CHAT_MODEL`, `GEMINI_CHAT_MAX_OUTPUT_TOKENS`, `GOOGLE_CLOUD_PROJECT_ID`,
`GOOGLE_CLOUD_LOCATION`, ADC credentials, and the two model cost settings.
`ADK_DB_URL` is required unless a session service is explicitly injected (for
example `InMemorySessionService` in tests). Use a compatible async SQLAlchemy URL
for your database, e.g. `postgresql+asyncpg://...`.

Conversation IDs use `ChatSession.id`; default ADK user identity is scoped to the
chatbot and conversation. An optional `user_id` must remain stable across chat and
confirmation calls. Reuse the client/session service on one async event loop.
For synchronous requests backed by asyncpg, create the client in a single async
service boundary rather than carrying pooled connections between `async_to_sync`
event loops. Callers must serialize turns per conversation across workers.

This module does not register HTTP endpoints, switch `chat/services/ai.py` to ADK,
or save assistant replies into Django. Existing chat endpoints still use
`GeminiChatService`. Persist returned replies and metadata in your caller so admin
analysis has the full conversation. Admin and confirmation methods must not be
exposed as visitor-callable model tools.

The existing `google-adk==2.1.0` dependency is retained. ADK supports structured
output with tools via native model support or its `set_model_response` fallback;
Python validates the final response again. The fallback remains dependent on model
compliance, so malformed responses are surfaced as failures.

References checked September 10, 2026:
- [ADK graph workflows](https://adk.dev/graphs/)
- [ADK collaborative sub-agent modes](https://adk.dev/workflows/collaboration/)
- [ADK LLM agents and structured output](https://google.github.io/adk-docs/agents/llm-agents/)
- [Function tools and invocation-local state](https://google.github.io/adk-docs/tools-custom/function-tools/)
- [Async session services](https://google.github.io/adk-docs/sessions/session/)

Run deterministic tests (no model API calls or database needed):

```sh
env/bin/python manage.py test agent.tests
```

Tests exercise the real ADK runner with scripted model responses. ORM/retrieval
and their thread adapters are mocked; live Vertex calls, database persistence,
and concurrent booking writers require integration testing in your deployment.
