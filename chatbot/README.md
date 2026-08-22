# Chatbot domain

The current base schema contains:

- `Chatbot`: belongs to one workspace.
- `Chatbot`: also stores its core conversation behavior and general feature
  flags.
- `ChatbotWidgetSettings`: stores the widget public key, enabled state, and
  presentation configuration independently from general chatbot settings.
- `ChatbotAllowedOrigin`: stores the web origins allowed to load the widget.
- `ChatbotUser`: assigns an active workspace member to a chatbot with an
  `admin` or `member` role.

Lead-management and appointment-management permissions remain unavailable
until their dedicated settings modules are added.

Use `create_chatbot` to create a chatbot and assign its creator as chatbot
admin. The service also creates the chatbot's default widget settings. Use
`assign_user_to_chatbot` to assign another user. The service checks
that both the assigning user and assigned user are active members of the
chatbot's workspace.
