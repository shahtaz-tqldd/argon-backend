# Google ADK agents

`AgentClient` runs a delegating root with knowledge and appointment specialists.
Tools bind chatbot identity in Python. Knowledge uses `KnowledgeVectorService`;
booking reads the existing configuration and saves an `Appointment` with pending
status after checking availability and validating configured fields.

```python
from agent.client import AgentClient

client = AgentClient()  # reuse across turns
reply = await client.call(
    chatbot=chatbot, user_id=str(visitor_id), session_id=str(chat_session.id),
    message="I'd like to book an appointment",
)
# Synchronous caller: client.call_sync(...) with the same keyword arguments.
```

Initialize Django first. Uses existing `GEMINI_CHAT_MODEL`,
`GOOGLE_CLOUD_PROJECT_ID`, `GOOGLE_CLOUD_LOCATION` and ADC credentials.
`google-adk==2.1.0` is already in requirements. Set `ADK_DB_URL` for persistent
ADK sessions; otherwise each client uses its own in-memory session store.
Callers authorize chatbot/session ownership and serialize turns for each session.
ADK history is separate from Django messages: use the same client/session IDs for
all turns. This module does not change the current chat backend or save replies.
Booking writes serialize against the booking config; other booking writers should
use the same lock to prevent overlapping bookings across entry points.

The optional analysis function is deliberately not invoked anywhere:

```python
from agent.summary import generate_conversation_summary

analysis = await generate_conversation_summary(chat_session.id, min_user_messages=5)
# None unless closed with >= N visitor messages; otherwise ConversationAnalysis.
# Caller decides whether/how to persist summary, lead_score, reason and next_steps.
```

Analysis sends the full text transcript to the model; callers should choose their
own context limits before enabling it for very long sessions.

ADK references: https://google.github.io/adk-docs/agents/multi-agents/
and https://google.github.io/adk-docs/runtime/

Run isolated tests: `env/bin/python -m unittest discover -s agent/tests`.
