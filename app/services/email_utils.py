import logging
import smtplib
from email.message import EmailMessage
from typing import Iterable, Sequence

from ..config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()


def send_email(
    subject: str,
    body: str,
    *,
    to_list: Sequence[str] | None = None,
    bcc_list: Sequence[str] | None = None,
    attachments: Iterable[dict] | None = None,
) -> bool:
    """Send an email using the configured SMTP relay."""

    if not (_settings.smtp_host and _settings.smtp_from_email):
        logger.warning("SMTP settings incomplete; skipping email send for subject '%s'", subject)
        return False

    to = [addr for addr in (to_list or []) if addr]
    bcc = [addr for addr in (bcc_list or []) if addr]
    if not to and not bcc:
        logger.warning("No recipients provided for subject '%s'", subject)
        return False

    message = EmailMessage()
    message["From"] = _settings.smtp_from_email
    if to:
        message["To"] = ", ".join(to)
    if bcc:
        message["Bcc"] = ", ".join(bcc)
    message["Subject"] = subject
    message.set_content(body)

    for attachment in attachments or []:
        content = attachment.get("content")
        if content is None:
            continue
        filename = attachment.get("filename", "attachment.dat")
        maintype = attachment.get("maintype", "application")
        subtype = attachment.get("subtype", "octet-stream")
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

    try:
        with smtplib.SMTP(_settings.smtp_host, _settings.smtp_port, timeout=15) as client:
            if _settings.smtp_use_tls:
                client.starttls()
            if _settings.smtp_username and _settings.smtp_password:
                client.login(
                    _settings.smtp_username,
                    _settings.smtp_password.get_secret_value() if _settings.smtp_password else None,
                )
            client.send_message(message)
        logger.info("Sent email subject='%s' to=%s bcc=%s", subject, to, bcc)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to send email subject='%s': %s", subject, exc)
        return False
