from __future__ import annotations

from pathlib import Path

import pytest

from marketsearch.config import EmailConfig, SmsConfig
from marketsearch.notify.delivery import (
    DeliveryError,
    Dispatcher,
    EmailSender,
    SmsSender,
    build_mime,
    resolve_secret,
)
from marketsearch.notify.render import ChangeCard, MatchCard, RenderedEmail
from marketsearch.models import RawListing
from marketsearch.store import ExtractionRow, ListingRow, Store


def email_config() -> EmailConfig:
    return EmailConfig.model_validate({
        "to": "me@example.com", "from": "bot@example.com",
        "smtp_host": "smtp.example.com", "smtp_port": 587,
        "username": "bot@example.com", "password_env": "TEST_SMTP_PASSWORD",
    })


def sms_config() -> SmsConfig:
    return SmsConfig(to="+15555550100", twilio_from="+15555550101",
                     account_sid_env="TEST_SID", auth_token_env="TEST_TOKEN")


def rendered(images=None) -> RenderedEmail:
    return RenderedEmail(subject="MarketSearch: 1 new match",
                         html="<html><body>hi <img src='cid:photo-1'></body></html>",
                         images=images if images is not None else [("photo-1", b"\x89PNG")])


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: float = 30.0):
        self.host, self.port = host, port
        self.started_tls = False
        self.logged_in_as: str | None = None
        self.sent: list[object] = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username: str, password: str):
        self.logged_in_as = username

    def send_message(self, message):
        self.sent.append(message)


class FakeTwilio:
    def __init__(self, sid: str, token: str):
        self.sid = sid
        self.sent: list[dict] = []
        self.messages = self

    def create(self, body: str, from_: str, to: str):
        self.sent.append({"body": body, "from_": from_, "to": to})


@pytest.fixture(autouse=True)
def _reset_smtp():
    FakeSMTP.instances.clear()


def test_resolve_secret_reads_the_environment(monkeypatch):
    monkeypatch.setenv("SOME_SECRET", "abc123")
    assert resolve_secret("SOME_SECRET") == "abc123"


def test_resolve_secret_raises_a_clear_error_when_missing(monkeypatch):
    monkeypatch.delenv("MISSING_SECRET", raising=False)
    with pytest.raises(DeliveryError, match="MISSING_SECRET"):
        resolve_secret("MISSING_SECRET")


def test_build_mime_sets_headers_and_html():
    message = build_mime(rendered(), "bot@example.com", "me@example.com")
    assert message["Subject"] == "MarketSearch: 1 new match"
    assert message["From"] == "bot@example.com"
    assert message["To"] == "me@example.com"


def test_build_mime_attaches_images_with_matching_cids():
    message = build_mime(rendered(), "bot@example.com", "me@example.com")
    cids = [part["Content-ID"] for part in message.walk() if part.get("Content-ID")]
    assert "<photo-1>" in cids


def test_build_mime_without_images_is_still_valid():
    message = build_mime(rendered(images=[]), "bot@example.com", "me@example.com")
    assert message["Subject"]


def test_email_sender_uses_starttls_and_logs_in():
    sender = EmailSender(email_config(), password="secret", smtp_factory=FakeSMTP)
    sender.send(rendered())
    smtp = FakeSMTP.instances[0]
    assert smtp.started_tls is True
    assert smtp.logged_in_as == "bot@example.com"
    assert len(smtp.sent) == 1


def test_email_sender_wraps_failures_in_delivery_error():
    class Boom(FakeSMTP):
        def send_message(self, message):
            raise OSError("connection reset")

    sender = EmailSender(email_config(), password="secret", smtp_factory=Boom)
    with pytest.raises(DeliveryError, match="email send failed"):
        sender.send(rendered())


def test_sms_sender_sends_body_from_and_to():
    fake = FakeTwilio("sid", "token")
    sender = SmsSender(sms_config(), "sid", "token", client_factory=lambda s, t: fake)
    sender.send("MarketSearch: 1 new match — check email.")
    assert fake.sent[0]["to"] == "+15555550100"
    assert fake.sent[0]["from_"] == "+15555550101"
    assert "1 new match" in fake.sent[0]["body"]


def listing_row(listing_id="1") -> ListingRow:
    return ListingRow(
        listing_id=listing_id, title="2019 Bobcat T770",
        price_cents=3_800_000, location="Olathe, KS",
        url=f"https://www.facebook.com/marketplace/item/{listing_id}/",
        thumbnail_url=None, seller_name="Dale S", fingerprint="fp", stage="matched",
        reject_reason=None, watched=False, first_seen_at="2026-07-26T10:00:00+00:00",
        last_seen_at="2026-07-26T10:00:00+00:00", last_change_check_at=None,
        extraction_attempts=0,
    )


def extraction_row(listing_id="1") -> ExtractionRow:
    return ExtractionRow(
        listing_id=listing_id, attributes={"core": {"engine_hours": 2400}},
        verdict="match", confidence=0.9, reasoning="under the limit",
        unknowns=[], model="claude-opus-5", created_at="2026-07-26T10:00:00+00:00",
    )


class RecordingEmail:
    def __init__(self):
        self.sent: list[RenderedEmail] = []

    def send(self, email: RenderedEmail) -> None:
        self.sent.append(email)


class RecordingSms:
    def __init__(self):
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "d.db")
    s.initialize()
    for listing_id in ("1", "2"):
        s.upsert_listing(
            RawListing(listing_id=listing_id, title="T770", price_cents=3_800_000,
                       location=None, url=f"https://example.com/{listing_id}",
                       thumbnail_url=None, seller_name=None),
            "bobcat-t770", "fp",
        )
    yield s
    s.close()


def make_dispatcher(store, enabled=True):
    email, sms = RecordingEmail(), RecordingSms()
    return Dispatcher(store, email, sms, enabled=enabled), email, sms


def test_dispatch_sends_one_email_and_one_sms(store: Store):
    dispatcher, email, sms = make_dispatcher(store)
    card = MatchCard(listing=listing_row("1"), extraction=extraction_row("1"), photos=[])
    assert dispatcher.dispatch([card], [], []) is True
    assert len(email.sent) == 1
    assert len(sms.sent) == 1


def test_dispatch_sends_nothing_when_disabled(store: Store):
    dispatcher, email, sms = make_dispatcher(store, enabled=False)
    card = MatchCard(listing=listing_row("1"), extraction=extraction_row("1"), photos=[])
    assert dispatcher.dispatch([card], [], []) is False
    assert email.sent == []
    assert sms.sent == []


def test_dispatch_sends_nothing_when_there_is_nothing_to_say(store: Store):
    dispatcher, email, sms = make_dispatcher(store)
    assert dispatcher.dispatch([], [], []) is False
    assert email.sent == []


def test_already_notified_listings_are_filtered_out(store: Store):
    dispatcher, email, _ = make_dispatcher(store)
    card = MatchCard(listing=listing_row("1"), extraction=extraction_row("1"), photos=[])
    dispatcher.dispatch([card], [], [])
    dispatcher2, email2, _ = make_dispatcher(store)
    assert dispatcher2.dispatch([card], [], []) is False
    assert email2.sent == []


def test_a_discarded_listing_is_never_alerted_on(store: Store):
    """The whole point of discarding a scam is that it stops reaching you."""
    store.dismiss("1", "scam")
    dispatcher, email, sms = make_dispatcher(store)
    card = MatchCard(listing=listing_row("1"), extraction=extraction_row("1"), photos=[])
    assert dispatcher.dispatch([card], [], []) is False
    assert email.sent == []
    assert sms.sent == []


def test_a_discarded_listing_is_silenced_for_price_changes_too(store: Store):
    """A price drop on a machine already judged a scam is not news — and a
    watched listing keeps generating change events indefinitely."""
    store.dismiss("1", "scam")
    dispatcher, email, _ = make_dispatcher(store)
    change = ChangeCard(
        listing=listing_row("1"), kind="price_change",
        old_price_cents=3_800_000, new_price_cents=3_200_000,
    )
    assert dispatcher.dispatch([], [], [change]) is False
    assert email.sent == []


def test_discarding_one_listing_does_not_silence_the_others(store: Store):
    store.dismiss("1", "scam")
    dispatcher, email, _ = make_dispatcher(store)
    cards = [
        MatchCard(listing=listing_row("1"), extraction=extraction_row("1"), photos=[]),
        MatchCard(listing=listing_row("2"), extraction=extraction_row("2"), photos=[]),
    ]
    assert dispatcher.dispatch(cards, [], []) is True
    assert len(email.sent) == 1
    assert store.already_notified("2", "email", "match") is True
    # Suppressed, not "sent" — restoring it later must let it alert again.
    assert store.already_notified("1", "email", "match") is False


def test_restoring_a_discarded_listing_lets_it_alert_again(store: Store):
    store.dismiss("1", "scam")
    card = MatchCard(listing=listing_row("1"), extraction=extraction_row("1"), photos=[])
    make_dispatcher(store)[0].dispatch([card], [], [])

    store.undismiss("1")
    dispatcher, email, _ = make_dispatcher(store)
    assert dispatcher.dispatch([card], [], []) is True
    assert len(email.sent) == 1


def test_new_listing_still_sends_when_another_was_already_notified(store: Store):
    dispatcher, email, _ = make_dispatcher(store)
    first = MatchCard(listing=listing_row("1"), extraction=extraction_row("1"), photos=[])
    dispatcher.dispatch([first], [], [])

    dispatcher2, email2, _ = make_dispatcher(store)
    second = MatchCard(listing=listing_row("2"), extraction=extraction_row("2"), photos=[])
    assert dispatcher2.dispatch([first, second], [], []) is True
    assert "2 new" not in email2.sent[0].subject  # only the unseen one is included


def test_notification_rows_are_written_only_after_a_successful_send(store: Store):
    class Failing:
        def send(self, email):
            raise DeliveryError("smtp down")

    dispatcher = Dispatcher(store, Failing(), RecordingSms(), enabled=True)
    card = MatchCard(listing=listing_row("1"), extraction=extraction_row("1"), photos=[])
    with pytest.raises(DeliveryError):
        dispatcher.dispatch([card], [], [])
    assert store.already_notified("1", "email", "match") is False


def test_sms_failure_does_not_undo_a_successful_email(store: Store):
    class FailingSms:
        def send(self, text):
            raise DeliveryError("twilio down")

    email = RecordingEmail()
    dispatcher = Dispatcher(store, email, FailingSms(), enabled=True)
    card = MatchCard(listing=listing_row("1"), extraction=extraction_row("1"), photos=[])
    assert dispatcher.dispatch([card], [], []) is True
    assert len(email.sent) == 1
    assert store.already_notified("1", "email", "match") is True
    assert store.already_notified("1", "sms", "match") is False


def test_price_change_and_match_are_tracked_separately(store: Store):
    dispatcher, _, _ = make_dispatcher(store)
    card = MatchCard(listing=listing_row("1"), extraction=extraction_row("1"), photos=[])
    dispatcher.dispatch([card], [], [])

    dispatcher2, email2, _ = make_dispatcher(store)
    change = ChangeCard(listing=listing_row("1"), kind="price_change",
                        old_price_cents=4_100_000, new_price_cents=3_800_000)
    assert dispatcher2.dispatch([], [], [change]) is True
    assert len(email2.sent) == 1
