from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives


@shared_task
def send_workspace_invitation_email(
    recipient_email,
    subject,
    message,
    html_message=None,
):
    email = EmailMultiAlternatives(
        subject=subject,
        body=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient_email],
    )
    if html_message:
        email.attach_alternative(html_message, "text/html")
    return email.send(fail_silently=False)
