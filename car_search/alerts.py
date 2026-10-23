"""Alerts for high-scoring cars: browser notifications (sent by the extension) and optional email."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import pandas as pd

from .config import AlertConfig

log = logging.getLogger(__name__)


def select_alerts(df: pd.DataFrame, threshold: float, realert_drop: float = 0.03) -> pd.DataFrame:
    """Active cars scoring at or above `threshold` that are new to us, or whose price has fallen
    at least `realert_drop` (a fraction) below the price we last alerted at."""
    if df.empty:
        return df
    score = pd.to_numeric(df["deal_score"], errors="coerce")
    hits = df[(df["active"] == 1) & (score >= threshold)]
    alerted = pd.to_numeric(hits["alerted_price"], errors="coerce")
    fresh = alerted.isna() | (hits["price"] <= alerted * (1 - realert_drop))
    return hits[fresh].sort_values("deal_score", ascending=False)


def text_or_blank(value) -> str:
    """Database blanks come back as None or NaN; both read as empty."""
    return "" if value is None or (not isinstance(value, str) and pd.isna(value)) else str(value)


def format_car(row) -> str:
    trim = f" {text_or_blank(row.get('trim'))}" if text_or_blank(row.get("trim")) else ""
    where = ", ".join(x for x in (text_or_blank(row.get("city")), text_or_blank(row.get("state"))) if x)
    dist = f" ({row['distance_miles']:.0f} mi)" if pd.notna(row.get("distance_miles")) else ""
    seller = text_or_blank(row.get("dealer_name")) or "Seller"
    lines = [
        f"{row['year']} {row['make']} {row['model']}{trim} - ${row['price']:,.0f}",
        f"{row['deal_score'] * 100:.1f}% deal score, est. market ${row['market_price']:,.0f}, "
        f"{row['miles']:,} mi",
        f"{seller} - {where}{dist}" if where or dist else seller,
    ]
    if pd.notna(row.get("alerted_price")):
        lines.append(f"Price dropped from ${row['alerted_price']:,.0f}")
    if text_or_blank(row.get("url")):
        lines.append(row["url"])
    return "\n".join(lines)


def build_message(cars: pd.DataFrame) -> str:
    header = f"{len(cars)} car deal{'s' if len(cars) != 1 else ''} found:\n"
    return header + "\n\n".join(format_car(r) for _, r in cars.iterrows())


def send_email(cfg: AlertConfig, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.smtp_user
    msg["To"] = cfg.email_to or cfg.smtp_user
    msg.set_content(body)
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(cfg.smtp_user, cfg.smtp_password)
        smtp.send_message(msg)


def dispatch(cfg: AlertConfig, cars: pd.DataFrame) -> list[str]:
    """Email `cars` if email is enabled. Returns the channels that succeeded.

    Browser notifications don't go through here: the capture server hands the
    deals back to the extension, which shows them.
    """
    if cars.empty:
        return []
    body = build_message(cars)
    sent = []
    if cfg.email_enabled:
        try:
            send_email(cfg, f"Car deals: {len(cars)} new", body)
            sent.append("email")
        except Exception as exc:
            log.error("email alert failed: %s", exc)
    else:
        log.info("email alerts off; deals:\n%s", body)
    return sent
