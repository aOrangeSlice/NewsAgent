from __future__ import annotations

from email.message import EmailMessage
from typing import Any
import os
import re
import smtplib


class EmailDelivery:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
        recipients: list[str],
        use_tls: bool = True,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.sender = sender
        self.recipients = recipients
        self.use_tls = use_tls

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> "EmailDelivery":
        config = settings.get("delivery", {}).get("email", {})
        password = resolve_email_password(config)
        return cls(
            host=config.get("host", ""),
            port=int(config.get("port", 587)),
            username=config.get("username", ""),
            password=password,
            sender=config.get("sender") or config.get("username", ""),
            recipients=list(config.get("recipients", [])),
            use_tls=bool(config.get("use_tls", True)),
        )

    @staticmethod
    def is_configured(config: dict[str, Any]) -> bool:
        return bool(
            config.get("enabled")
            and config.get("host")
            and config.get("username")
            and config.get("sender", config.get("username"))
            and config.get("recipients")
            and resolve_email_password(config)
        )

    def send(self, subject: str, body: str) -> dict[str, Any]:
        missing = self._missing_fields()
        if missing:
            return {"ok": False, "error": f"Email delivery is not configured: {', '.join(missing)}"}

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        message.set_content(body, subtype="plain", charset="utf-8")

        with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
            if self.use_tls:
                smtp.starttls()
            smtp.login(self.username, self.password)
            smtp.send_message(message)
        return {"ok": True, "recipients": self.recipients}

    def _missing_fields(self) -> list[str]:
        missing = []
        for field, value in {
            "host": self.host,
            "username": self.username,
            "password": self.password,
            "sender": self.sender,
            "recipients": self.recipients,
        }.items():
            if not value:
                missing.append(field)
        return missing


def resolve_email_password(config: dict[str, Any]) -> str:
    password_value = str(config.get("password", "") or "")
    password_env = str(config.get("password_env", "") or "")
    if not password_env and looks_like_env_var(password_value):
        password_env = password_value
        password_value = ""
    password_env = password_env or "NEWSAGENT_SMTP_PASSWORD"
    return os.environ.get(password_env, password_value)


def looks_like_env_var(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Z0-9_]{4,}", value.strip()))
