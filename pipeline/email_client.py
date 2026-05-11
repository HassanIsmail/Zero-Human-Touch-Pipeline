"""
pipeline/email_client.py

Email delivery for the Zero Human Touch Pipeline.

Sends QA reports with screenshot attachments via SMTP / STARTTLS using Python's
built-in smtplib.  Failures are logged but never re-raised so they cannot abort
the pipeline.

Reads configuration from environment variables:
    EMAIL_FROM      — Sender address, e.g. pipeline@example.com
    EMAIL_TO        — Recipient address(es), comma-separated
    SMTP_HOST       — SMTP server hostname
    SMTP_PORT       — SMTP port (typically 587 for STARTTLS)
    SMTP_USER       — SMTP authentication username
    SMTP_PASSWORD   — SMTP authentication password
"""

import os
import smtplib
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

from pipeline.logger import get_logger

logger = get_logger(__name__)


def send_report(
    issue_key: str,
    bug_report_path: str,
    screenshots: List[str],
    overall_status: str,
) -> None:
    """Send a QA report email with screenshots attached.

    The function reads all required SMTP settings from environment variables.
    Any exception during sending is caught and logged — the pipeline will
    continue regardless.

    Args:
        issue_key:       Jira issue key, used in the email subject.
        bug_report_path: Absolute path to the ``bug-report.md`` file.
        screenshots:     List of absolute paths to PNG screenshots to attach.
        overall_status:  One of ``PASS``, ``PARTIAL``, or ``FAIL`` — used in
                         the email subject line.
    """
    try:
        _send_report(issue_key, bug_report_path, screenshots, overall_status)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "Failed to send QA report email for %s: %s",
            issue_key,
            exc,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Private implementation
# ---------------------------------------------------------------------------


def _send_report(
    issue_key: str,
    bug_report_path: str,
    screenshots: List[str],
    overall_status: str,
) -> None:
    """Internal implementation that may raise on failure."""
    # Read env vars.
    email_from = os.environ["EMAIL_FROM"]
    email_to_raw = os.environ["EMAIL_TO"]
    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_password = os.environ["SMTP_PASSWORD"]

    # Support multiple recipients separated by commas.
    recipients = [addr.strip() for addr in email_to_raw.split(",") if addr.strip()]

    # Read the bug report.
    try:
        with open(bug_report_path, "r", encoding="utf-8") as fh:
            report_text = fh.read()
    except OSError as exc:
        logger.error("Could not read bug report at %s: %s", bug_report_path, exc)
        report_text = f"Bug report unavailable ({exc})."

    # Build the MIME message.
    msg = MIMEMultipart()
    msg["From"] = email_from
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = f"QA Report — {issue_key} — {overall_status}"

    # Plain-text body (the full Markdown report).
    body_part = MIMEText(report_text, "plain", "utf-8")
    msg.attach(body_part)
    logger.debug("Attached plain-text body (%d chars)", len(report_text))

    # Screenshot attachments.
    attached_count = 0
    for screenshot_path in screenshots:
        if not os.path.isfile(screenshot_path):
            logger.warning("Screenshot not found, skipping attachment: %s", screenshot_path)
            continue
        try:
            with open(screenshot_path, "rb") as img_fh:
                img_data = img_fh.read()
            img_part = MIMEImage(img_data, name=os.path.basename(screenshot_path))
            img_part.add_header(
                "Content-Disposition",
                "attachment",
                filename=os.path.basename(screenshot_path),
            )
            msg.attach(img_part)
            attached_count += 1
            logger.debug("Attached screenshot: %s", screenshot_path)
        except OSError as exc:
            logger.warning("Could not attach screenshot %s: %s", screenshot_path, exc)

    logger.info(
        "Sending QA report email to %s — issue=%s status=%s screenshots=%d",
        recipients,
        issue_key,
        overall_status,
        attached_count,
    )

    # Connect and send.
    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(smtp_user, smtp_password)
        smtp.sendmail(email_from, recipients, msg.as_string())

    logger.info(
        "QA report email sent successfully to %s for issue %s",
        recipients,
        issue_key,
    )
