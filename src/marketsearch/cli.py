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
    Dispatcher,
    EmailSender,
    SmsSender,
    resolve_secret,
)
from marketsearch.notify.render import RenderedEmail
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


def build_dispatcher(config: Config, store: Store) -> Dispatcher:
    email = EmailSender(
        config.notifications.email,
        resolve_secret(config.notifications.email.password_env),
    )
    sms = SmsSender(
        config.notifications.sms,
        resolve_secret(config.notifications.sms.account_sid_env),
        resolve_secret(config.notifications.sms.auth_token_env),
    )
    return Dispatcher(store, email, sms, enabled=config.notifications.enabled)


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
                    cfg.account.profile_dir, headless=True, debug_dir=DEFAULT_DEBUG
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
        f"{report.counters.matched} matched, {report.changes} change(s), "
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
        cfg.account.profile_dir, headless=False, debug_dir=DEFAULT_DEBUG
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
