from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Iterable


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str


def _normalize_recipients(to_addrs: str | Iterable[str]) -> list[str]:
    if isinstance(to_addrs, str):
        parts = [p.strip() for p in to_addrs.replace(";", ",").split(",")]
        return [p for p in parts if p]
    return [x.strip() for x in to_addrs if x and x.strip()]


def send_email(
    *,
    smtp: SmtpConfig,
    to_addrs: str | Iterable[str],
    subject: str,
    body_text: str,
    from_addr: str | None = None,
) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr or smtp.user
    recipients = _normalize_recipients(to_addrs)
    msg["To"] = ", ".join(recipients)
    msg.set_content(body_text)

    with smtplib.SMTP(smtp.host, smtp.port, timeout=30) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        s.login(smtp.user, smtp.password)
        s.send_message(msg, from_addr=msg["From"], to_addrs=recipients)

