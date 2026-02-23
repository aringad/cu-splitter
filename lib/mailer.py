"""
Invio email SMTP con allegati PDF.

Supporta TLS/SSL, template HTML personalizzabili.
"""

import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from pathlib import Path


class SendStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"


@dataclass
class SMTPConfig:
    """Configurazione server SMTP."""
    host: str
    port: int
    user: str
    password: str
    from_address: str
    use_tls: bool = True


@dataclass
class SendLog:
    """Log di un singolo invio email."""
    to: str
    subject: str
    filename: str
    status: SendStatus
    timestamp: str
    error: str = ""


DEFAULT_TEMPLATE = Path(__file__).parent.parent / "templates" / "email_template.html"


def load_default_template() -> str:
    """Carica il template email HTML di default."""
    if DEFAULT_TEMPLATE.exists():
        return DEFAULT_TEMPLATE.read_text(encoding="utf-8")
    return (
        "<p>Gentile {nome} {cognome},</p>"
        "<p>in allegato trova la Sua Certificazione Unica {anno}.</p>"
        "<p>Cordiali saluti</p>"
    )


def render_template(template_html: str, nome: str, cognome: str, anno: str) -> str:
    """Renderizza il template sostituendo i placeholder."""
    return (
        template_html
        .replace("{nome}", nome.title())
        .replace("{cognome}", cognome.title())
        .replace("{anno}", anno)
    )


def test_smtp_connection(config: SMTPConfig) -> tuple[bool, str]:
    """
    Testa la connessione SMTP.

    Returns:
        (success, message)
    """
    try:
        if config.use_tls:
            server = smtplib.SMTP(config.host, config.port, timeout=10)
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        else:
            if config.port == 465:
                server = smtplib.SMTP_SSL(config.host, config.port, timeout=10,
                                          context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(config.host, config.port, timeout=10)
                server.ehlo()

        server.login(config.user, config.password)
        server.quit()
        return True, "Connessione SMTP riuscita!"
    except Exception as e:
        return False, f"Errore connessione: {e}"


def send_cu_email(
    config: SMTPConfig,
    to_address: str,
    subject: str,
    body_html: str,
    pdf_bytes: bytes,
    pdf_filename: str,
) -> SendLog:
    """
    Invia un'email con la CU allegata.

    Args:
        config: configurazione SMTP
        to_address: indirizzo destinatario
        subject: oggetto dell'email
        body_html: corpo HTML dell'email
        pdf_bytes: contenuto del PDF allegato
        pdf_filename: nome del file PDF allegato

    Returns:
        SendLog con esito dell'invio
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        msg = MIMEMultipart()
        msg["From"] = config.from_address
        msg["To"] = to_address
        msg["Subject"] = subject

        msg.attach(MIMEText(body_html, "html", "utf-8"))

        attachment = MIMEBase("application", "pdf")
        attachment.set_payload(pdf_bytes)
        encoders.encode_base64(attachment)
        attachment.add_header(
            "Content-Disposition",
            f'attachment; filename="{pdf_filename}"',
        )
        msg.attach(attachment)

        if config.use_tls:
            server = smtplib.SMTP(config.host, config.port, timeout=30)
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        else:
            if config.port == 465:
                server = smtplib.SMTP_SSL(config.host, config.port, timeout=30,
                                          context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(config.host, config.port, timeout=30)
                server.ehlo()

        server.login(config.user, config.password)
        server.sendmail(config.from_address, to_address, msg.as_string())
        server.quit()

        return SendLog(
            to=to_address,
            subject=subject,
            filename=pdf_filename,
            status=SendStatus.SUCCESS,
            timestamp=timestamp,
        )

    except Exception as e:
        return SendLog(
            to=to_address,
            subject=subject,
            filename=pdf_filename,
            status=SendStatus.ERROR,
            timestamp=timestamp,
            error=str(e),
        )
