"""Command line: car-search serve | import | demo | top | history | payment | test-alert | dashboard."""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

from .alerts import build_message, dispatch
from .config import load_config
from .db import Database
from .finance import monthly_payment, resale_estimate
from .importers import import_file
from .pipeline import ingest, rescore
from .sources.sample import SampleSource

log = logging.getLogger("car_search")


def cmd_init(args) -> int:
    target = Path("config.toml")
    if target.exists():
        print("config.toml already exists")
        return 1
    shutil.copy(Path(__file__).resolve().parent.parent / "config.example.toml", target)
    print("wrote config.toml - edit your search, then run: car-search serve")
    return 0


def cmd_serve(args) -> int:
    from .server import serve

    config = load_config(args.config)
    serve(config, args.port)
    return 0


def _report(r, config) -> None:
    info = r.model_info
    err = info.get("median_abs_pct_error")
    print(f"{r.received} listings -> {r.unique} unique cars, {len(r.saved_vins)} match your search | "
          f"{r.new} new, {r.price_drops} price drops, {r.went_inactive} retired")
    if info:
        print(f"market model: {info.get('method')} on {info.get('training_rows')} cars"
              + (f", median error {err:.1%}" if err is not None else ""))
    if len(r.alerts):
        print(f"\n{len(r.alerts)} deals at or above {config.alerts.threshold:.0%}:\n")
        print(build_message(r.alerts.head(10)))


def cmd_import(args) -> int:
    config = load_config(args.config)
    listings = []
    for path in args.files:
        found = import_file(path)
        print(f"{path}: {len(found)} cars")
        listings.extend(found)
    db = Database(config.database)
    try:
        _report(ingest(config, db, listings), config)
    finally:
        db.close()
    return 0


def cmd_demo(args) -> int:
    """Fill the database with fake sample listings to try the scoring and dashboard."""
    config = load_config(args.config)
    db = Database(config.database)
    try:
        r = ingest(config, db, SampleSource().fetch(config.search), complete_snapshot=True)
        _report(r, config)
        print("\n(These are made-up cars with VINs starting DEMO. Delete the database file to start fresh.)")
    finally:
        db.close()
    return 0


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
        print("no listings yet - run car-search serve and browse some listings")
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
        print("no scored listings yet")
        return 1
    sample = df.sort_values("deal_score", ascending=False).head(1)
    print(build_message(sample))
    sent = dispatch(config.alerts, sample)
    print(f"sent via: {', '.join(sent) or 'nothing (set email_enabled and SMTP settings in config.toml)'}")
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

    p = sub.add_parser("serve", help="receive listings from the browser extension")
    p.add_argument("--port", type=int, help="default 8765 (the extension expects 8765)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("import", help="import saved pages (.html) or inventory files (.csv/.json)")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_import)

    sub.add_parser("demo", help="load fake sample listings to try things out").set_defaults(func=cmd_demo)

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

    sub.add_parser("test-alert", help="email the current best deal (if email is set up)") \
        .set_defaults(func=cmd_test_alert)

    sub.add_parser("dashboard", help="open the Streamlit dashboard").set_defaults(func=cmd_dashboard)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
