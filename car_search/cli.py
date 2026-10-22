"""Command line: car-search run | top | history | payment | test-alert | dashboard."""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

from .alerts import build_message, dispatch
from .config import load_config
from .db import Database
from .finance import monthly_payment, resale_estimate
from .pipeline import rescore, run

log = logging.getLogger("car_search")


def cmd_init(args) -> int:
    target = Path("config.toml")
    if target.exists():
        print("config.toml already exists")
        return 1
    shutil.copy(Path(__file__).resolve().parent.parent / "config.example.toml", target)
    print("wrote config.toml - edit your search, then run: car-search run")
    return 0


def cmd_run(args) -> int:
    config = load_config(args.config)
    while True:
        db = Database(config.database)
        try:
            r = run(config, db, send_alerts=not args.no_alerts)
        finally:
            db.close()
        info = r.model_info
        err = info.get("median_abs_pct_error")
        print(f"fetched {r.fetched} listings -> {r.unique} unique cars | {r.new} new, "
              f"{r.price_drops} price drops, {r.went_inactive} gone")
        print(f"market model: {info.get('method')} on {info.get('training_rows')} cars"
              + (f", median error {err:.1%}" if err is not None else ""))
        if len(r.alerts):
            channels = ", ".join(r.alert_channels) or "no channel enabled"
            print(f"{len(r.alerts)} deals at or above {config.alerts.threshold:.0%} ({channels})")
        for e in r.errors:
            print(f"warning: {e}", file=sys.stderr)
        if not args.watch:
            return 0
        print(f"next run in {args.watch} h")
        time.sleep(args.watch * 3600)


def cmd_top(args) -> int:
    config = load_config(args.config)
    db = Database(config.database)
    try:
        if args.rescore:
            rescore(config, db)
        df = db.listings_frame(active_only=not args.all)
    finally:
        db.close()
    if df.empty:
        print("no listings yet - run: car-search run")
        return 0
    df = df.dropna(subset=["deal_score"]).sort_values("deal_score", ascending=False).head(args.n)
    view = pd.DataFrame({
        "score": (df["deal_score"] * 100).map("{:+.1f}%".format),
        "car": df["year"].astype(str) + " " + df["make"] + " " + df["model"] + " " + df["trim"].fillna(""),
        "price": df["price"].map("${:,.0f}".format),
        "market": df["market_price"].map("${:,.0f}".format),
        "miles": df["miles"].map("{:,}".format),
        "dist": df["distance_miles"].map(lambda d: "" if pd.isna(d) else f"{d:.0f} mi"),
        "dom": df["days_on_market"].map(lambda d: "" if pd.isna(d) else int(d)),
        "vin": df["vin"],
    })
    print(view.to_string(index=False))
    return 0


def cmd_history(args) -> int:
    config = load_config(args.config)
    db = Database(config.database)
    try:
        hist = db.price_history(args.vin.upper())
    finally:
        db.close()
    if hist.empty:
        print("no history for that VIN")
        return 1
    print(hist.to_string(index=False))
    return 0


def cmd_payment(args) -> int:
    p = monthly_payment(args.price, args.down, args.apr, args.term, args.tax, args.fees, args.trade_in)
    print(f"amount financed  ${p.amount_financed:,.2f}")
    print(f"monthly payment  ${p.monthly_payment:,.2f}  ({args.term} months at {args.apr}% APR)")
    print(f"total interest   ${p.total_interest:,.2f}")
    print(f"total cost       ${p.total_cost:,.2f}")
    for years in (3, 5):
        print(f"est. value in {years}y ${resale_estimate(args.price, years):,.0f}")
    return 0


def cmd_test_alert(args) -> int:
    config = load_config(args.config)
    db = Database(config.database)
    try:
        df = db.listings_frame(active_only=True).dropna(subset=["deal_score"])
    finally:
        db.close()
    if df.empty:
        print("no scored listings yet - run: car-search run")
        return 1
    sample = df.sort_values("deal_score", ascending=False).head(1)
    print(build_message(sample))
    sent = dispatch(config.alerts, sample)
    print(f"sent via: {', '.join(sent) or 'nothing (enable email or telegram in config.toml)'}")
    return 0


def cmd_dashboard(args) -> int:
    app = Path(__file__).resolve().parent.parent / "dashboard" / "app.py"
    extra = ["--", "--config", args.config] if args.config else []
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app), *extra])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="car-search", description="Used-car deal finder")
    parser.add_argument("-c", "--config", help="path to config.toml (default: ./config.toml)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create config.toml from the example").set_defaults(func=cmd_init)

    p = sub.add_parser("run", help="collect, score and alert")
    p.add_argument("--no-alerts", action="store_true")
    p.add_argument("--watch", type=float, metavar="HOURS", help="keep running every HOURS (e.g. 6)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("top", help="show the best current deals")
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--all", action="store_true", help="include cars no longer listed")
    p.add_argument("--rescore", action="store_true", help="re-run the market model first")
    p.set_defaults(func=cmd_top)

    p = sub.add_parser("history", help="price history for a VIN")
    p.add_argument("vin")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("payment", help="loan payment calculator")
    p.add_argument("price", type=float)
    p.add_argument("--down", type=float, default=0.0)
    p.add_argument("--apr", type=float, default=7.0, help="percent, e.g. 6.9")
    p.add_argument("--term", type=int, default=60, help="months")
    p.add_argument("--tax", type=float, default=0.0945, help="sales tax as a fraction")
    p.add_argument("--fees", type=float, default=0.0, help="title, registration, dealer fees")
    p.add_argument("--trade-in", type=float, default=0.0)
    p.set_defaults(func=cmd_payment)

    sub.add_parser("test-alert", help="send the current best deal through your alert channels") \
        .set_defaults(func=cmd_test_alert)

    sub.add_parser("dashboard", help="open the Streamlit dashboard").set_defaults(func=cmd_dashboard)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
