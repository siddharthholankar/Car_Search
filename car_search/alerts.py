"""Email and Telegram alerts for high-scoring cars."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

import pandas as pd
import requests

from .config import AlertConfig

log = logging.getLogger(__name__)


def select_alerts(df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """Active cars scoring at or above `threshold` that we haven't alerted at this price or lower."""
    if df.empty:
        return df
    score = pd.to_numeric(df["deal_score"], errors="coerce")
    hits = df[(df["active"] == 1) & (score >= threshold)]
    alerted = pd.to_numeric(hits["alerted_price"], errors="coerce")
    fresh = alerted.isna() | (hits["price"] < alerted)
    return hits[fresh].sort_values("deal_score", ascending=False)


def format_car(row) -> str:
    trim = f" {row['trim']}" if row.get("trim") else ""
    where = ", ".join(x for x in (row.get("city"), row.get("state")) if x)
    dist = f" ({row['distance_miles']:.0f} mi)" if pd.notna(row.get("distance_miles")) else ""
    lines = [
        f"{row['year']} {row['make']} {row['model']}{trim} - ${row['price']:,.0f}",
        f"{row['deal_score'] * 100:.1f}% deal score, est. market ${row['market_price']:,.0f}, "
        f"{row['miles']:,} mi",
        f"{row.get('dealer_name') or 'Seller'} - {where}{dist}",
    ]
    if row.get("alerted_price") and pd.notna(row["alerted_price"]):
        lines.append(f"Price dropped from ${row['alerted_price']:,.0f}")
    if row.get("url"):
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


def send_telegram(cfg: AlertConfig, text: str) -> None:
    url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
    # Telegram caps messages at 4096 characters.
    for start in range(0, len(text), 4000):
        resp = requests.post(url, json={"chat_id": cfg.telegram_chat_id, "text": text[start:start + 4000],
                                        "disable_web_page_preview": True}, timeout=30)
        resp.raise_for_status()


def dispatch(cfg: AlertConfig, cars: pd.DataFrame) -> list[str]:
    """Send `cars` through every enabled channel. Returns the channels that succeeded."""
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
    if cfg.telegram_enabled:
        try:
            send_telegram(cfg, body)
            sent.append("telegram")
        except Exception as exc:
            log.error("telegram alert failed: %s", exc)
    if not (cfg.email_enabled or cfg.telegram_enabled):
        log.info("no alert channel enabled; would have sent:\n%s", body)
    return sent
