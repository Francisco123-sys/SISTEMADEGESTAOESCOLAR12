import os
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlparse


def _required_mail_settings():
    host = os.getenv('SMTP_HOST', '').strip()
    sender = os.getenv('SMTP_FROM', '').strip()
    base_url = os.getenv('APP_BASE_URL', '').strip().rstrip('/')
    if not host or not sender or not base_url:
        raise ValueError('SMTP_HOST, SMTP_FROM e APP_BASE_URL são necessários.')
    parsed_url = urlparse(base_url)
    if (
        parsed_url.scheme not in {'http', 'https'}
        or not parsed_url.netloc
        or parsed_url.query
        or parsed_url.fragment
        or (
            parsed_url.scheme != 'https'
            and parsed_url.hostname not in {'localhost', '127.0.0.1', '::1'}
        )
    ):
        raise ValueError('APP_BASE_URL deve ser HTTPS, excepto em localhost.')
    return host, sender, base_url


def send_password_reset_email(recipient, reset_url):
    host, sender, _base_url = _required_mail_settings()
    username = os.getenv('SMTP_USERNAME', '').strip()
    password = os.getenv('SMTP_PASSWORD', '')
    if bool(username) != bool(password):
        raise ValueError('SMTP_USERNAME e SMTP_PASSWORD devem ser definidos em conjunto.')

    message = EmailMessage()
    message['Subject'] = 'Redefinição da palavra-passe do Portal do Encarregado'
    message['From'] = sender
    message['To'] = recipient
    message.set_content(
        'Recebemos um pedido para redefinir a palavra-passe do Portal do Encarregado.\n\n'
        f'Use esta ligação, válida por 30 minutos e apenas uma vez:\n{reset_url}\n\n'
        'Se não pediu esta alteração, ignore esta mensagem.'
    )

    port = int(os.getenv('SMTP_PORT', '587'))
    use_ssl = os.getenv('SMTP_USE_SSL', '').strip().lower() in {'1', 'true', 'yes'}
    use_starttls = os.getenv('SMTP_STARTTLS', 'true').strip().lower() in {
        '1', 'true', 'yes',
    }
    if use_ssl and use_starttls:
        raise ValueError('Configure apenas SMTP_USE_SSL ou SMTP_STARTTLS.')

    if use_ssl:
        with smtplib.SMTP_SSL(
            host, port, timeout=10, context=ssl.create_default_context()
        ) as server:
            if username:
                server.login(username, password)
            server.send_message(message)
        return

    with smtplib.SMTP(host, port, timeout=10) as server:
        if use_starttls:
            server.starttls(context=ssl.create_default_context())
        if username:
            server.login(username, password)
        server.send_message(message)


def build_password_reset_url(token):
    _host, _sender, base_url = _required_mail_settings()
    return f'{base_url}/portal-encarregado/redefinir/{token}'
