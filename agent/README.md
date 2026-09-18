# Chatbot agents (Google ADK 2.1.0)

`AgentClient` runs one root `LlmAgent` as a thin coordinator over dynamically
composed specialists, currently `knowledge_agent` and `appointment_agent`.

## Architecture

- **Root coordinator** (`agent/root_agent.py`): greets, delegates, and relays.
  It owns no business tools and no `output_schema`, so its final answer is
  plain text that can be streamed token-by-token. Its instruction is built
  from the specialist roster that the chatbot's features actually enable.
- **Specialists** (`agent/sub_agents/<name>/agent.py`): `mode="single_turn"`
  agents exposed to the coordinator as delegate tools. Each keeps a structured
  output schema (`content` + `source_ids`), owns its domain tools, and shares
  the conversation tools (`record_lead_score`, `request_human_escalation`).
- **Registry** (`agent/sub_agents/__init__.py`): an ordered tuple of
  `build(chatbot, session) -> LlmAgent | None` factories. A factory returns
  `None` when the chatbot lacks that feature, and the coordinator is assembled
  from whatever remains. Adding a later specialist (service recommendation,
  quotation generation, product recommendation) means adding a package with a
  `build` factory and appending it to `SUB_AGENT_FACTORIES`; coordinator
  instructions, delegate tools, and streaming adapt automatically.
- **Feature gates**: the knowledge specialist requires
  `Chatbot.knowledge_base_enabled`; the appointment specialist requires
  `Chatbot.appointment_booking_enabled`; the escalation tool requires
  `Chatbot.human_handoff_enabled`. With no specialist enabled, the root
  becomes the whole chatbot and carries the conversation tools itself.

Specialists use ADK's collaborative `single_turn` delegation: the coordinator
sends a self-contained request, the specialist returns its structured answer,
and the coordinator relays it faithfully (dates, slot offers, booking
statuses, citations) to the visitor.

The runner is created from an ADK `App`, with event compaction every three
events, one event of overlap, and context caching for contexts of at least
2,048 tokens. One shared Vertex client (HTTP pool) backs every agent model
(`agent/helpers/model.py`). Trusted backend booking state is appended to the
ADK session before each App run; this avoids relying on
`Runner.run_async(state_delta=...)`, which ADK 2.1.0's App node path does not
expose to agent context.

Booking operations live together under `agent/sub_agents/appointment/tools/`:

- `booking.py`: slot calculation, bounded date search, saved-booking
  verification, and the appointment transcript query.
- `availability.py`: the appointment specialist's ADK tool factory.
- `__init__.py`: exports the factory.

## Usage

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
    "lead_score": null,
    "escalation": null
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

`source_ids` comes from the knowledge specialist's structured answer and is
validated against every source retrieved in this conversation: the search tool
records retrieved IDs in session state (`knowledge_source_ids`), so a
follow-up answered from prior conversation context may keep citing earlier
sources without re-searching. Duplicate, invented, and unknown IDs are
removed. Invalid specialist structured output fails the turn (ADK rejects it
before a reply is produced) instead of leaking raw output to the UI.

Token usage sums ADK model events across all calls in the invocation,
including tool-selection and final-response calls; a streamed answer's chunks
count as one call. `output_tokens` includes thinking; `thinking_tokens` and
`cached_input_tokens` are subsets, not additional totals. `cost` is an
estimated USD amount using `GEMINI_INPUT_COST_PER_MILLION` and
`GEMINI_OUTPUT_COST_PER_MILLION`. It excludes retrieval embeddings and other
infrastructure charges and does not apply cache discounts or pricing tiers.
Configure rates for your selected model. Missing provider usage contributes
zero; this estimate is not a billing receipt.

## Streaming (socket-style chatbots)

`chat_stream` runs the same turn through ADK's SSE streaming mode and is a
separate entry point designed for WebSocket consumers:

```python
async for item in client.chat_stream(message, user_id=visitor_id):
    match item["type"]:
        case "delta":
            await socket.send(json.dumps(item))  # {"type": "delta", "content": "..."}
        case "done":
            await socket.send(json.dumps(item))  # {"type": "done", "response": {...envelope...}}
```

- `delta` frames carry coordinator text chunks as they are produced (thought
  parts excluded). If the model or deployment does not emit partials, only
  the `done` frame arrives; consumers should render from `done` alone.
- The final `done` frame carries `response`, the exact envelope `chat`
  returns, including `appointment` slots for the booking UI, `lead_score`,
  `escalation`, token usage, and cost. Persist this frame, not the deltas.
- If the generator raises mid-stream (after deltas were already delivered),
  consumers should emit their own error frame; no `done` frame is produced.

## Appointment UI flow

1. Chat asks for the preferred date, resolving it in the chatbot timezone.
2. `find_appointment_availability` searches the preferred date plus at most
   six following dates and stops at the first available day. Only one search
   window can run per invocation, enforced through ADK `temp:` state.
   Available slots are returned in `result.appointment.slots` and saved in
   the AI message metadata for the widget.
3. On success, `result.appointment` contains `status: "available"`,
   `available: true`, `requested_date`, `date`, `searched_through`, and
   `timezone`. For June 16 with first availability on June 18, `date` is
   `2026-06-18`. Each slot contains offset-aware `starts_at` and `ends_at`
   values. Render these choices in the widget, but treat them as offers
   rather than reservations.
4. If June 16–22 is unavailable, `available` is false, `date` is null,
   `searched_through` is June 22, and `next_search_date` is June 23. Chat
   asks whether to try next week and waits for another visitor turn.
   Searches stop at the configured booking horizon; disabled/invalid
   requests have distinct statuses.
5. POST the selected `starts_at` and customer `collected_fields` to
   `/api/v1/chatbots/{public_key}/conversations/{session_id}/appointments/`
   using the conversation bearer token. The endpoint rechecks availability
   while locking the booking configuration, derives `ends_at`, and stores
   the trusted chat-session association itself.
6. After the save commits, call the backend-only confirmation method:

```python
response = await client.confirm_booking(appointment_id=str(appointment.id))
# Or client.confirm_booking_sync(appointment_id=...)
```

The method reads the saved appointment scoped to the chatbot, conversation,
and pending/confirmed status. It creates no appointment. ADK records the
backend event in conversation history, and a trusted state event commits
`booking_confirmations` before model execution. The response contains
`status: "booking_recorded"`, appointment ID, actual status, and start/end
timestamps. A pending request is described as awaiting approval.
`available` applies to availability offers only and is false for recorded
bookings. Repeated confirmation calls reuse the state record keyed by
appointment ID but produce another acknowledgment turn; callers should
deduplicate delivery retries. A failure during acknowledgment does not undo
the saved booking or event.

## In-conversation lead scoring

Specialists call `record_lead_score` only after a meaningful qualification
signal, such as a concrete need, timeline, budget, booking intent, or
committed next step. The tool validates a 0–100 integer, updates
`Lead.lead_score`, and saves its concise rationale in
`ChatSession.metadata["lead_score_summary"]`. If the session has not yet
been associated with a lead, the tool returns `recorded: false` and writes
nothing. The result is also returned as `result.lead_score` for message
metadata.

## Human escalation

Specialists (and the root, when it is the whole chatbot) call
`request_human_escalation` for explicit human requests, configured
escalation rules, questions they cannot safely or confidently answer, or
required tool failures; the tool is only exposed when
`human_handoff_enabled` is on. The tool atomically sets
`requires_attention`, stores its concise escalation explanation directly in
the `attention_reason` text field, and updates `attention_requested_at` on
`ChatSession`. `chat.tasks` creates an `AI_NOTIFICATION` for the chatbot
dashboard, with the chat-session ID in its metadata, after the AI reply is
saved. The reply and notification are in the same database transaction, so
a failed task does not leave a notification without its corresponding reply.
Existing resolution/takeover services remain responsible for clearing the
attention fields.

## Configuration and integration boundary

Initialize Django before importing the client. The existing settings are
used: `GEMINI_CHAT_MODEL`, `GEMINI_CHAT_MAX_OUTPUT_TOKENS`,
`GOOGLE_CLOUD_PROJECT_ID`, `GOOGLE_CLOUD_LOCATION`, ADC credentials, and the
two model cost settings. `ADK_DB_URL` is required unless a session service is
explicitly injected (for example `InMemorySessionService` in tests). Use a
compatible async SQLAlchemy URL for your database, e.g.
`postgresql+asyncpg://...`.

Conversation IDs use `ChatSession.id`; default ADK user identity is scoped to
the chatbot and conversation. An optional `user_id` must remain stable across
chat, streaming, and confirmation calls. Reuse the client/session service on
one async event loop. For synchronous requests backed by asyncpg, create the
client in a single async service boundary rather than carrying pooled
connections between `async_to_sync` event loops. Callers must serialize turns
per conversation across workers.

This module does not register HTTP endpoints or save assistant replies into
Django. `chat.tasks` persists returned replies, public message metadata,
usage, and escalation notifications. Internal lead-score and escalation tool
payloads are not placed in public message metadata. The confirmation method
remains backend-only and is not exposed as a visitor-callable model tool.

The existing `google-adk==2.1.0` dependency is retained. Specialists use
structured output with tools via native model support or ADK's
`set_model_response` fallback; Python validates the final response again,
and malformed specialist output is surfaced as a failed turn.

References checked September 17, 2026:
- [ADK collaborative sub-agent modes](https://adk.dev/workflows/collaboration/)
- [ADK LLM agents and structured output](https://google.github.io/adk-docs/agents/llm-agents/)
- [ADK run config streaming modes](https://google.github.io/adk-docs/runtime/run-config/)
- [Function tools and invocation-local state](https://google.github.io/adk-docs/tools-custom/function-tools/)
- [Async session services](https://google.github.io/adk-docs/sessions/session/)

Run deterministic tests (no model API calls or database needed):

```sh
env/bin/python manage.py test agent.tests.test_workflows
```

Tests exercise the real ADK runner with scripted model responses, including
streaming partials, dynamic specialist composition, and context-based
citations. `agent.tests.test_conversation_tools` additionally needs a
database. ORM/retrieval and their thread adapters are mocked; live Vertex
calls, database persistence, and concurrent booking writers require
integration testing in your deployment.
