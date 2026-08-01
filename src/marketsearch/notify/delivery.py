"""Sending, and the rules that keep the inbox worth reading.

Two invariants:
  * nothing is sent unless notifications are explicitly enabled;
  * a listing is never alerted on twice for the same reason, because the
    notifications table is only written after a confirmed send.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Callable, Protocol

from marketsearch.config import EmailConfig, SmsConfig
from marketsearch.notify.render import (
    ChangeCard,
    MatchCard,
    RenderedEmail,
    render_email,
    render_sms,
)
from marketsearch.store import Store

log = logging.getLogger(__name__)

_SMTP_TIMEOUT_S = 30.0


class DeliveryError(Exception):
    """A notification could not be delivered."""


def resolve_secret(env_name: str) -> str:
    value = os.environ.get(env_name)
    if not value:
        raise DeliveryError(
            f"environment variable {env_name} is not set — add it to your .env file"
        )
    return value


def build_mime(email: RenderedEmail, from_addr: str, to_addr: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = email.subject
    message["From"] = from_addr
    message["To"] = to_addr
    message.set_content(
        "This message contains listing photos and formatting. "
        "View it in an HTML-capable mail client."
    )
    message.add_alternative(email.html, subtype="html")

    html_part = message.get_payload()[-1]
    for cid, data in email.images:
        html_part.add_related(data, maintype="image", subtype="jpeg", cid=f"<{cid}>")
    return message


class EmailSender:
    def __init__(
        self,
        config: EmailConfig,
        password: str,
        smtp_factory: Callable[..., smtplib.SMTP] | None = None,
    ) -> None:
        self._config = config
        self._password = password
        self._smtp_factory = smtp_factory or smtplib.SMTP

    def send(self, email: RenderedEmail) -> None:
        message = build_mime(email, self._config.from_, self._config.to)
        try:
            with self._smtp_factory(
                self._config.smtp_host, self._config.smtp_port, timeout=_SMTP_TIMEOUT_S
            ) as smtp:
                smtp.starttls()
                smtp.login(self._config.username, self._password)
                smtp.send_message(message)
        except Exception as exc:
            raise DeliveryError(f"email send failed: {exc}") from exc
        log.info("sent email: %s", email.subject)


class SmsSender:
    def __init__(
        self,
        config: SmsConfig,
        account_sid: str,
        auth_token: str,
        client_factory: Callable[[str, str], object] | None = None,
    ) -> None:
        self._config = config
        self._sid = account_sid
        self._token = auth_token
        self._client_factory = client_factory

    def _client(self):
        if self._client_factory is not None:
            return self._client_factory(self._sid, self._token)
        from twilio.rest import Client

        return Client(self._sid, self._token)

    def send(self, text: str) -> None:
        try:
            self._client().messages.create(
                body=text, from_=self._config.twilio_from, to=self._config.to
            )
        except Exception as exc:
            raise DeliveryError(f"sms send failed: {exc}") from exc
        log.info("sent sms: %s", text)


class _EmailChannel(Protocol):
    def send(self, email: RenderedEmail) -> None: ...


class _SmsChannel(Protocol):
    def send(self, text: str) -> None: ...


class Dispatcher:
    def __init__(
        self,
        store: Store,
        email_sender: _EmailChannel,
        sms_sender: _SmsChannel,
        enabled: bool,
    ) -> None:
        self._store = store
        self._email = email_sender
        self._sms = sms_sender
        self._enabled = enabled

    def dispatch(
        self,
        matches: list[MatchCard],
        unverified: list[MatchCard],
        changes: list[ChangeCard],
    ) -> bool:
        """Send at most one email and one SMS. Returns True if anything went out."""
        # Nothing to send means nothing to look up. Kept ahead of every store
        # call so a caller with an empty run never touches the database at all.
        if not (matches or unverified or changes):
            return False

        # A discarded listing is silenced for good, including for changes. A
        # price drop on a machine already judged a scam is not news, and
        # re-alerting on it is exactly what discarding is meant to stop.
        discarded = self._store.dismissed_listing_ids()
        if discarded:
            before = len(matches) + len(unverified) + len(changes)
            matches = [c for c in matches if c.listing.listing_id not in discarded]
            unverified = [c for c in unverified if c.listing.listing_id not in discarded]
            changes = [c for c in changes if c.listing.listing_id not in discarded]
            suppressed = before - (len(matches) + len(unverified) + len(changes))
            if suppressed:
                log.info("suppressed %d alert(s) for discarded listing(s)", suppressed)

        matches = [c for c in matches
                   if not self._store.already_notified(c.listing.listing_id, "email", "match")]
        unverified = [c for c in unverified
                      if not self._store.already_notified(
                          c.listing.listing_id, "email", "unverified")]
        changes = [c for c in changes
                   if not self._store.already_notified(
                       c.listing.listing_id, "email", c.kind)]

        if not (matches or unverified or changes):
            return False

        if not self._enabled:
            log.info(
                "notifications disabled — would have sent %d match(es), %d unverified, "
                "%d change(s)",
                len(matches), len(unverified), len(changes),
            )
            return False

        self._email.send(render_email(matches, unverified, changes))

        for card in matches:
            self._store.record_notification(card.listing.listing_id, "email", "match", "sent")
        for card in unverified:
            self._store.record_notification(
                card.listing.listing_id, "email", "unverified", "sent")
        for change in changes:
            self._store.record_notification(
                change.listing.listing_id, "email", change.kind, "sent")

        # The SMS is only a nudge. Losing it must not cost the email, whose
        # notification rows are already committed above.
        try:
            self._sms.send(render_sms(matches, unverified, changes))
        except DeliveryError as exc:
            log.warning("sms nudge failed (email was delivered): %s", exc)
        else:
            for card in matches:
                self._store.record_notification(
                    card.listing.listing_id, "sms", "match", "sent")

        return True
