import csv
from html import escape
from io import BytesIO, StringIO

from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from chat.models import ChatMessage
from chat.utils.choices import ChatMessageSenderType


TRANSCRIPT_MESSAGE_LIMIT = 350


def _latest_messages(chat_session):
    messages = list(
        ChatMessage.objects.filter(chat_session=chat_session)
        .select_related("sender__user")
        .prefetch_related("attachments")
        .order_by("-created_at", "-id")[:TRANSCRIPT_MESSAGE_LIMIT]
    )
    messages.reverse()
    return messages


def _sender_name(message, chatbot_name):
    if message.sender_type == ChatMessageSenderType.AGENT:
        if message.sender_id:
            return message.sender.user.name or message.sender.user.email or "Agent"
        return "Former agent"
    if message.sender_type == ChatMessageSenderType.AI:
        return chatbot_name
    return message.get_sender_type_display()


def _attachment_text(message):
    return "; ".join(
        attachment.file_name or attachment.file_url
        for attachment in message.attachments.all()
    )


def _timestamp(message):
    return timezone.localtime(message.created_at).isoformat()


def _safe_csv_cell(value):
    value = value or ""
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{value}"
    return value


def _build_csv(chat_session, messages):
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("timestamp", "sender", "message", "attachments"))
    for message in messages:
        writer.writerow(
            (
                _timestamp(message),
                _safe_csv_cell(_sender_name(message, chat_session.chatbot.chatbot_name)),
                _safe_csv_cell(message.content),
                _safe_csv_cell(_attachment_text(message)),
            )
        )
    return output.getvalue().encode("utf-8-sig")


def _paragraph_text(value):
    return escape(value or "").replace("\n", "<br/>")


def _build_pdf(chat_session, messages):
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Chat session {chat_session.id} transcript",
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph("Chat transcript", styles["Title"]),
        Paragraph(f"Session: {chat_session.id}", styles["Normal"]),
        Paragraph(
            f"Messages: {len(messages)} (latest {TRANSCRIPT_MESSAGE_LIMIT} maximum)",
            styles["Normal"],
        ),
        Spacer(1, 8 * mm),
    ]
    for message in messages:
        content = message.content
        attachments = _attachment_text(message)
        if attachments:
            content = f"{content}\nAttachments: {attachments}".strip()
        timestamp = _paragraph_text(_timestamp(message))
        sender = _paragraph_text(
            _sender_name(message, chat_session.chatbot.chatbot_name)
        )
        story.extend(
            (
                Paragraph(f"{timestamp} — <b>{sender}</b>", styles["Heading4"]),
                Paragraph(_paragraph_text(content), styles["BodyText"]),
                Spacer(1, 4 * mm),
            )
        )
    document.build(story)
    return output.getvalue()


def build_transcript(chat_session, transcript_format):
    messages = _latest_messages(chat_session)
    if transcript_format == "csv":
        return _build_csv(chat_session, messages)
    if transcript_format == "pdf":
        return _build_pdf(chat_session, messages)
    raise ValueError(f"Unsupported transcript format: {transcript_format}")
