"""Command-line entry points."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import typer
from dotenv import load_dotenv

from marketsearch.config import Config, ConfigError, load_config
from marketsearch.extract import Extractor
from marketsearch.logging_setup import setup_logging
from marketsearch.notify.delivery import (
    DeliveryError,
    Dispatcher,
    EmailSender,
    SmsSender,
    resolve_secret,
)
from marketsearch.notify.render import MatchCard, RenderedEmail
from marketsearch.pipeline import run_once
from marketsearch.runstate import AlreadyRunning, OperationalAlerts, RunLock
from marketsearch.sources.facebook import FacebookSource, open_login_browser
from marketsearch.store import Store

log = logging.getLogger(__name__)

app = typer.Typer(add_completion=False, help="Watch Facebook Marketplace for equipment.")

DEFAULT_CONFIG = Path("config.yaml")
DEFAULT_DB = Path("marketsearch.db")
DEFAULT_LOG = Path("logs/marketsearch.log")
DEFAULT_LOCK = Path("marketsearch.lock")
DEFAULT_DEBUG = Path("debug")


def _load(config_path: Path) -> Config:
    try:
        return load_config(config_path)
    except ConfigError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc


def build_extractor(config: Config) -> Extractor:
    import anthropic

    return Extractor(
        anthropic.Anthropic(),
        model=config.extraction.model,
        effort=config.extraction.effort,
    )


class _Unconfigured:
    """Stands in for a sender while notifications are off. Dispatcher never calls
    it in that state; if it ever does, fail loudly rather than send with no
    credentials."""

    def send(self, _: object) -> None:
        raise DeliveryError("notifications are disabled — no sender is configured")


def build_dispatcher(config: Config, store: Store) -> Dispatcher:
    # Secrets are resolved only when notifications are on. The whole point of the
    # shakedown is to run for days with `enabled: false`, which must not require
    # an SMTP password or Twilio credentials for messages that are never sent.
    if not config.notifications.enabled:
        return Dispatcher(store, _Unconfigured(), _Unconfigured(), enabled=False)

    email = EmailSender(
        config.notifications.email,
        resolve_secret(config.notifications.email.password_env),
    )
    sms = SmsSender(
        config.notifications.sms,
        resolve_secret(config.notifications.sms.account_sid_env),
        resolve_secret(config.notifications.sms.auth_token_env),
    )
    return Dispatcher(store, email, sms, enabled=True)


def build_operational_notifier(config: Config) -> Callable[[str, str], None]:
    """Operational alerts go out even when listing notifications are disabled —
    a silent tool that has stopped working is worse than one that is noisy."""

    def notify(subject: str, body: str) -> None:
        try:
            sender = EmailSender(
                config.notifications.email,
                resolve_secret(config.notifications.email.password_env),
            )
            html = f"<html><body><p>{body.replace(chr(10), '<br>')}</p></body></html>"
            sender.send(RenderedEmail(subject=subject, html=html, images=[]))
        except Exception as exc:
            log.error("could not send operational alert %r: %s", subject, exc)

    return notify


@app.command()
def run(
    config: Path = typer.Option(DEFAULT_CONFIG, "--config"),
    db: Path = typer.Option(DEFAULT_DB, "--db"),
    lock: Path = typer.Option(DEFAULT_LOCK, "--lock"),
    log_file: Path = typer.Option(DEFAULT_LOG, "--log"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Scan but write and send nothing."),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Perform one sweep."""
    load_dotenv()
    setup_logging(log_file, verbose)
    cfg = _load(config)

    try:
        with RunLock(lock):
            with Store(db) as store:
                store.initialize()
                with FacebookSource(
                    cfg.account.profile_dir, headless=True, debug_dir=DEFAULT_DEBUG,
                    max_listings_per_search=cfg.extraction.max_listings_per_search,
                ) as source:
                    report = run_once(
                        config=cfg,
                        store=store,
                        source=source,
                        extractor=build_extractor(cfg),
                        dispatcher=build_dispatcher(cfg, store),
                        alerts=OperationalAlerts(store),
                        notify_operational=build_operational_notifier(cfg),
                        dry_run=dry_run,
                    )
    except AlreadyRunning as exc:
        typer.echo(f"Already running: {exc}")
        raise typer.Exit(code=1) from exc

    if report.blocked:
        typer.echo(
            f"Paused: Facebook needs attention ({report.blocked}). "
            f"Run `marketsearch login`."
        )
        raise typer.Exit(code=3)

    typer.echo(
        f"{report.counters.found} found, {report.counters.new} new, "
        f"{report.counters.matched} matched, {report.counters.alerted} alerted, "
        f"{report.changes} change(s), "
        f"{report.counters.errors} error(s)"
        + ("  [dry run — nothing written or sent]" if dry_run else "")
    )


@app.command()
def login(config: Path = typer.Option(DEFAULT_CONFIG, "--config")) -> None:
    """Open a browser to log into Facebook and set your Marketplace location."""
    cfg = _load(config)
    open_login_browser(cfg.account.profile_dir)

    with Store(DEFAULT_DB) as store:
        store.initialize()
        store.set_state("needs_login", "")
    typer.echo("Session saved. Runs will resume on the next scheduled sweep.")


@app.command(name="test-search")
def test_search(
    query: str,
    config: Path = typer.Option(DEFAULT_CONFIG, "--config"),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Do one live search and print what was parsed. The smoke test after any
    Facebook breakage."""
    load_dotenv()
    setup_logging(DEFAULT_LOG, verbose)
    cfg = _load(config)

    with FacebookSource(
        cfg.account.profile_dir, headless=False, debug_dir=DEFAULT_DEBUG,
        max_listings_per_search=cfg.extraction.max_listings_per_search,
    ) as source:
        listings = source.search(
            query, cfg.location.anchor, cfg.location.radius_miles
        )

    typer.echo(f"{len(listings)} listing(s) parsed\n")
    for listing in listings:
        price = "—" if listing.price_cents is None else f"${listing.price_cents / 100:,.0f}"
        typer.echo(f"  {listing.listing_id}  {price:>10}  {listing.title}")


@app.command()
def history(
    db: Path = typer.Option(DEFAULT_DB, "--db"),
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """Show recent runs."""
    with Store(db) as store:
        store.initialize()
        rows = store.recent_runs(limit)

    if not rows:
        typer.echo("No runs recorded yet.")
        return

    typer.echo(f"{'started':<22}{'found':>7}{'new':>6}{'matched':>9}{'errors':>8}")
    for row in rows:
        typer.echo(
            f"{row['started_at'][:19]:<22}{row['found']:>7}{row['new']:>6}"
            f"{row['matched']:>9}{row['errors']:>8}"
        )


def _with_photos(card: MatchCard, store: Store) -> MatchCard:
    """Re-attach photos to a reconstructed card by re-downloading them.

    Facebook's image URLs may have expired since the run, so this is
    best-effort by design — a card whose photos fail still renders.
    """
    from dataclasses import replace

    from marketsearch.notify.render import download_photos

    detail = store.get_detail(card.listing.listing_id)
    if detail is None or not detail.photo_urls:
        return card
    return replace(card, photos=download_photos(detail.photo_urls))


@app.command()
def preview(
    config: Path = typer.Option(DEFAULT_CONFIG, "--config"),
    db: Path = typer.Option(DEFAULT_DB, "--db"),
    run_id: int | None = typer.Option(None, "--run", help="Defaults to the latest run."),
    out: Path = typer.Option(Path("preview.html"), "--out"),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Render the email a run would have sent, and open it in a browser."""
    import webbrowser

    from marketsearch.notify.render import render_email
    from marketsearch.shakedown import collect_run_cards

    cfg = _load(config)
    with Store(db) as store:
        store.initialize()
        target = run_id if run_id is not None else store.latest_run_id()
        if target is None:
            typer.echo("No runs recorded yet — run `marketsearch run` first.")
            raise typer.Exit(code=1)

        matches, unverified = collect_run_cards(store, cfg, target)
        if not (matches or unverified):
            typer.echo(f"Run {target} produced no matches or unverified listings.")
            raise typer.Exit(code=0)

        matches = [_with_photos(card, store) for card in matches]
        unverified = [_with_photos(card, store) for card in unverified]

    email = render_email(matches, unverified, [])
    out.write_text(email.html, encoding="utf-8")
    typer.echo(f"Run {target}: {email.subject}\nWrote {out}")
    if open_browser:
        webbrowser.open(out.resolve().as_uri())


@app.command()
def replay(
    search: str = typer.Option(..., "--search", help="Model name from config.yaml."),
    since: str = typer.Option("30d", "--since", help="e.g. 7d, 36h."),
    config: Path = typer.Option(DEFAULT_CONFIG, "--config"),
    db: Path = typer.Option(DEFAULT_DB, "--db"),
    save: bool = typer.Option(False, "--save", help="Persist the new verdicts."),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Re-judge stored listings against the criteria currently in config.yaml."""
    from marketsearch.shakedown import format_replay
    from marketsearch.shakedown import replay as run_replay

    load_dotenv()
    setup_logging(DEFAULT_LOG, verbose)
    cfg = _load(config)

    with Store(db) as store:
        store.initialize()
        rows = run_replay(
            store, cfg, build_extractor(cfg), model_name=search,
            since=since, save=save,
        )

    typer.echo(format_replay(rows, search))
    if not save and rows:
        typer.echo("\n(Not saved. Re-run with --save to persist these verdicts.)")


@app.command(name="requeue")
def requeue_command(
    config: Path = typer.Option(DEFAULT_CONFIG, "--config"),
    db: Path = typer.Option(DEFAULT_DB, "--db"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report without writing."),
    verbose: bool = typer.Option(False, "--verbose"),
) -> None:
    """Re-test rejected listings against the current catalog. No scraping."""
    from marketsearch.requeue import format_requeue
    from marketsearch.requeue import requeue as run_requeue

    setup_logging(DEFAULT_LOG, verbose)
    cfg = _load(config)

    with Store(db) as store:
        store.initialize()
        rows = run_requeue(store, cfg, dry_run=dry_run)

    typer.echo(format_requeue(rows, dry_run))
