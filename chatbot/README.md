# Chatbot domain

The current base schema contains:

- `Chatbot`: belongs to one workspace.
- `ChatbotUser`: assigns an active workspace member to a chatbot with an
  `admin` or `member` role.

Use `create_chatbot` to create a chatbot and assign its creator as chatbot
admin. Use `assign_user_to_chatbot` to assign another user. The service checks
that both the assigning user and assigned user are active members of the
chatbot's workspace.
