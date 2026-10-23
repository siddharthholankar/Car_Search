"""Streamlit dashboard: streamlit run dashboard/app.py [-- --config config.toml]"""

from __future__ import annotations

import argparse
import json
import sys

import pandas as pd
import streamlit as st

from car_search.config import load_config
from car_search.db import Database
from car_search.finance import monthly_payment, resale_estimate

ACCENT = "#2a78d6"

parser = argparse.ArgumentParser()
parser.add_argument("--config")
args, _ = parser.parse_known_args(sys.argv[1:])
config = load_config(args.config)

st.set_page_config(page_title="Car deal finder", layout="wide")


def get_db() -> Database:
    return Database(config.database)


@st.cache_data(ttl=60)
def load_listings() -> pd.DataFrame:
    db = get_db()
    try:
        return db.listings_frame()
    finally:
        db.close()


def load_history(vin: str) -> pd.DataFrame:
    db = get_db()
    try:
        return db.price_history(vin)
    finally:
        db.close()


st.title("Used-car deal finder")
s = config.search
st.caption(f"{', '.join(f'{v.make} {v.model}' for v in s.vehicles)} · {s.min_year}+ · under \\${s.max_price:,} · "
           f"under {s.max_miles:,} mi · within {s.radius_miles} mi of {s.zip}")

df = load_listings()

with st.sidebar:
    st.header("Filters")
    if st.button("Refresh", width="stretch"):
        load_listings.clear()
        df = load_listings()

if df.empty:
    st.info(
        "**No cars yet.** Listings come from the pages you browse:\n\n"
        "1. In a terminal, run `car-search serve`\n"
        "2. Install the extension: open `chrome://extensions`, turn on **Developer mode**, click "
        "**Load unpacked** and pick this project's `extension` folder\n"
        "3. Browse used-car listings on Cars.com, CarGurus, Autotrader or dealer sites. "
        "Matching cars are saved as you go.\n\n"
        "To try it with made-up cars first, run `car-search demo`."
    )
    st.stop()

with st.sidebar:

    show_inactive = st.checkbox("Include cars no longer listed", value=False)
    models = sorted((df["make"] + " " + df["model"]).unique())
    chosen = st.multiselect("Vehicles", models, default=models)
    max_price = st.slider("Max price", 0, int(max(df["price"].max(), 1000)), int(df["price"].max()), step=500)
    max_miles = st.slider("Max miles", 0, int(max(df["miles"].max(), 1000)), int(df["miles"].max()), step=5000)
    lo, hi = int(df["year"].min()), int(df["year"].max())
    years = st.slider("Model years", lo, hi, (lo, hi)) if lo < hi else (lo, hi)
    max_dist = st.slider("Max distance (mi)", 0, max(config.search.radius_miles, 1), config.search.radius_miles)
    min_score = st.slider("Min deal score (%)", -50, 40, -50, help="Leftmost shows everything")
    clean_only = st.checkbox("Hide accident / salvage / rebuilt", value=False)

view = df.copy()
if not show_inactive:
    view = view[view["active"] == 1]
view = view[(view["make"] + " " + view["model"]).isin(chosen)]
view = view[(view["price"] <= max_price) & (view["miles"] <= max_miles)
            & view["year"].between(*years)
            & (view["distance_miles"].isna() | (view["distance_miles"] <= max_dist))
            & ((min_score <= -50) | (view["deal_score"] * 100 >= min_score))]
if clean_only:
    view = view[(view["accident_reported"].fillna(0) == 0) & (view["salvage_or_rebuilt"].fillna(0) == 0)]
view = view.sort_values("deal_score", ascending=False)

# --- headline numbers -------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("Cars shown", f"{len(view):,}")
c2.metric(f"Deals ≥ {config.alerts.threshold:.0%} below market",
          int((view["deal_score"] >= config.alerts.threshold).sum()))
c3.metric("Median price", f"${view['price'].median():,.0f}" if len(view) else "-")
seen = pd.to_datetime(df["last_seen"], utc=True).max()
c4.metric("Last capture (UTC)", seen.strftime("%b %d %H:%M") if pd.notna(seen) else "never")

# --- table ------------------------------------------------------------------
table = pd.DataFrame({
    "Score": view["deal_score"] * 100,
    "Car": view["year"].astype(str) + " " + view["make"] + " " + view["model"] + " " + view["trim"].fillna(""),
    "Price": view["price"],
    "Market": view["market_price"],
    "Miles": view["miles"],
    "Distance": view["distance_miles"],
    "Days listed": view["days_on_market"],
    "Dealer": view["dealer_name"].fillna("") + " · " + view["city"].fillna(""),
    "Notes": view["score_notes"].map(lambda n: ", ".join(json.loads(n)) if n else ""),
    "Listing": view["url"],
    "VIN": view["vin"],
})
st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    column_config={
        "Score": st.column_config.NumberColumn("Deal score", format="%+.1f%%",
                                               help="% below predicted market price, after history adjustments"),
        "Price": st.column_config.NumberColumn(format="$%,.0f"),
        "Market": st.column_config.NumberColumn("Est. market", format="$%,.0f"),
        "Miles": st.column_config.NumberColumn(format="%,d"),
        "Distance": st.column_config.NumberColumn(format="%.0f mi"),
        "Listing": st.column_config.LinkColumn(display_text="open"),
    },
)

# --- map + market chart -----------------------------------------------------
left, right = st.columns(2)
with left:
    st.subheader("Where they are")
    pts = view.dropna(subset=["latitude", "longitude"])
    if len(pts):
        st.map(pts.rename(columns={"latitude": "lat", "longitude": "lon"})[["lat", "lon"]], color=ACCENT, size=800)
    else:
        st.caption("No coordinates in these listings.")
with right:
    st.subheader("Asking price vs. estimated market")
    st.caption("Points below the diagonal are priced under market.")
    chart = view.dropna(subset=["market_price"])[["market_price", "price"]].rename(
        columns={"market_price": "Est. market ($)", "price": "Asking ($)"})
    if len(chart):
        st.scatter_chart(chart, x="Est. market ($)", y="Asking ($)", color=ACCENT)

# --- detail -----------------------------------------------------------------
st.subheader("Car details")
if not len(view):
    st.stop()
labels = {r.vin: f"{r.deal_score * 100:+.1f}% · {r.year} {r.make} {r.model} {r.trim or ''} · ${r.price:,.0f} · {r.vin}"
          for r in view.itertuples() if pd.notna(r.deal_score)}
if not labels:
    st.stop()
vin = st.selectbox("Pick a car", list(labels), format_func=labels.get)
car = view[view["vin"] == vin].iloc[0]

d1, d2 = st.columns([1, 1])
with d1:
    st.markdown(f"**{car['year']} {car['make']} {car['model']} {car['trim'] or ''}** — \\${car['price']:,.0f} "
                f"(est. market \\${car['market_price']:,.0f})")
    facts = {
        "Miles": f"{car['miles']:,}",
        "Drivetrain": car["drivetrain"] or "?",
        "Engine": car["engine"] or "?",
        "Color": car["exterior_color"] or "?",
        "Dealer": f"{car['dealer_name'] or '?'}, {car['city'] or ''} {car['state'] or ''}",
        "Days listed": "?" if pd.isna(car["days_on_market"]) else int(car["days_on_market"]),
        "Owners": "?" if pd.isna(car["owners"]) else int(car["owners"]),
        "Seen on": car["sources"],
        "First seen": str(car["first_seen"])[:10],
    }
    st.table(pd.DataFrame([(k, str(v)) for k, v in facts.items()], columns=["", "value"]).set_index(""))
    for note in json.loads(car["score_notes"] or "[]"):
        st.markdown(f"- {note}")
    links = [car["url"], *json.loads(car["other_urls"] or "[]")]
    st.markdown(" · ".join(f"[listing {i + 1}]({u})" for i, u in enumerate(links) if u))
    if not vin.startswith("DEMO"):
        st.markdown(f"[Check open recalls for this VIN on nhtsa.gov](https://www.nhtsa.gov/recalls?vin={vin})")

    hist = load_history(vin)
    st.markdown("**Price history**")
    if len(hist) > 1:
        hist["seen_at"] = pd.to_datetime(hist["seen_at"])
        st.line_chart(hist.set_index("seen_at")["price"], color=ACCENT, y_label="Price ($)", x_label="")
    else:
        st.caption("No price changes seen yet.")

with d2:
    st.markdown("**Payment calculator**")
    price = st.number_input("Price", value=float(car["price"]), step=250.0)
    down = st.number_input("Down payment", value=round(float(car["price"]) * 0.1, -2), step=500.0)
    trade = st.number_input("Trade-in", value=0.0, step=500.0)
    apr = st.number_input("APR (%)", value=7.0, step=0.25)
    term = st.select_slider("Term (months)", options=[24, 36, 48, 60, 72, 84], value=60)
    tax = st.number_input("Sales tax (%)", value=9.45, step=0.05) / 100
    fees = st.number_input("Fees (title, registration, dealer)", value=500.0, step=50.0)
    p = monthly_payment(price, down, apr, term, tax, fees, trade)
    m1, m2 = st.columns(2)
    m1.metric("Monthly", f"${p.monthly_payment:,.0f}")
    m2.metric("Total interest", f"${p.total_interest:,.0f}")
    st.caption(f"Financed \\${p.amount_financed:,.0f} · total cost \\${p.total_cost:,.0f} · "
               f"est. resale in 3 yrs \\${resale_estimate(price, 3):,.0f}, "
               f"5 yrs \\${resale_estimate(price, 5):,.0f}")
