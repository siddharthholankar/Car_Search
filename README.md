# Car Search: a used-car deal finder

Pulls used-car listings from several sources into one SQLite database, merges duplicates by VIN,
scores each car against a market-price model, and sends an email or Telegram alert when a good
deal shows up. A Streamlit dashboard shows the listings with filters, a map, price history and a
payment calculator.

```
sources (MarketCheck / dealer feeds / sample)
   → dedupe by VIN → NHTSA VIN decode + recalls → SQLite (listings, price_history)
   → market-price model → deal score → alerts (email / Telegram) → dashboard
```

## Quick start

```bash
pip install -e ".[dashboard,dev]"
car-search init                # writes config.toml from config.example.toml
car-search run                 # collect, score, alert
car-search top -n 15           # best current deals
car-search dashboard           # opens the Streamlit app
```

Out of the box only the `sample` source is enabled. It generates **fake** listings (VINs start
with `DEMO`) around Alexandria, LA, so you can try everything without an API key. Turn it off
once a real source is set up.

## Configuration (`config.toml`)

| Section | What it controls |
|---|---|
| `[search]` | zip, radius, min year, max price, max miles, and the make/model list |
| `[sources]` | `enabled = ["marketcheck", "dealer_feed"]`, API key, dealer feed URLs |
| `[enrich]` | NHTSA VIN decoding and recall lookups (both free, no key) |
| `[scoring]` | training minimum, "negotiable" day count, penalty and bonus weights |
| `[alerts]` | threshold (default 0.08 = 8% below market), SMTP and Telegram settings |

Secrets can come from environment variables instead of the file: `MARKETCHECK_API_KEY`,
`SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
`config.toml` is git-ignored.

## Data sources

The program uses official APIs and published feeds rather than scraping. Cars.com, Carfax,
Edmunds and Facebook Marketplace prohibit scraping in their terms.

- **MarketCheck** (`car_search/sources/marketcheck.py`): aggregated dealer listings, searched by
  make, model, year, price, miles, zip and radius. It's a paid API with a free trial. Each vehicle
  is fetched in pages of 50, up to `max_pages`. It also supplies the Carfax clean-title and
  one-owner flags when the listing has them.
- **Dealer feeds** (`car_search/sources/dealer_feed.py`): CSV or JSON inventory files that
  dealers publish. Common column names (`vin`, `mileage`, `internet_price` and so on) are mapped
  automatically. Check each dealer's terms first.
- **NHTSA vPIC** (`car_search/nhtsa.py`): batch VIN decoding that fills in a missing trim,
  drivetrain, engine or body type. Results are cached in the database.
- **NHTSA recalls**: the public API looks recalls up by make, model and year, not by VIN, so the
  dashboard shows every campaign for that model year. To see whether a specific car's recalls
  were repaired, check its VIN at nhtsa.gov/recalls.
- **Adding a source** (for example Auto.dev or CarsXE): subclass `Source` in
  `car_search/sources/base.py`, return `Listing` objects, and register the class in
  `sources/__init__.py`.

## The deal score

```
value_score = (predicted_market_price − listing_price) / predicted_market_price
deal_score  = value_score + bonuses − penalties
```

- **Market price model**: a ridge regression on log(price). Its inputs are age, age², miles,
  miles per year, days listed, make/model, make/model/trim, drivetrain and state. It trains on
  every car seen in the last 90 days. Each car's own prediction is made **out of fold** (5-fold
  CV), so a car's asking price never feeds the estimate it's compared against. Salvage and
  rebuilt cars are left out of the training data. With fewer than `min_training_rows` cars, it
  falls back to the median price of the same model within ±1 model year.
- **Penalties**: accident −5%, salvage/rebuilt −40%, repossession −5%, rental −3%, more than
  3 owners −3%.
- **Bonuses**: one owner +2%, service records +1%, certified pre-owned +2%, listed more than
  45 days +1% (the car is also flagged as negotiable).
- All weights can be changed in `[scoring]`. On the sample data the model's median error is
  about 6–7%.

History flags are only as good as the source. MarketCheck supplies the title and one-owner
flags. Accident, rental and owner-count details usually need a paid history report.

## Alerts

After each run, active cars whose `deal_score` is at or above the threshold trigger an alert.
A car alerts once, and again only if its price drops below the price it alerted at.

- **Email**: SMTP with STARTTLS. For Gmail, use an app password.
- **Telegram**: create a bot with @BotFather, then put its token and your chat id in the config.

Run `car-search test-alert` to send the current best deal through the channels you've enabled.
With no channel enabled, alerts are only logged, and cars aren't marked as alerted.

## Scheduling (every 6 hours)

cron:

```cron
0 */6 * * * cd /path/to/Car_Search && /usr/bin/env car-search run >> car_search.log 2>&1
```

Or keep a process running: `car-search run --watch 6`.

A run marks cars it no longer sees as inactive, but only when every source answered. That way a
single API outage doesn't empty the board.

**Databricks:** the pipeline is plain Python on pandas DataFrames. To use Delta tables, swap
`Database` for writes to Delta tables (`listings`, `price_history`) and schedule `car-search run`
as a Job every 6 hours.

## Commands

| Command | What it does |
|---|---|
| `car-search run [--no-alerts] [--watch HOURS]` | collect, dedupe, enrich, store, score, alert |
| `car-search top [-n 20] [--all] [--rescore]` | table of the best deals |
| `car-search history VIN` | price changes for one car |
| `car-search payment PRICE --down --apr --term --tax --fees --trade-in` | loan payment, interest, resale estimate |
| `car-search test-alert` | send a test alert |
| `car-search dashboard` | Streamlit app (`streamlit run dashboard/app.py`) |

The payment calculator's default sales tax is 9.45%, roughly the Louisiana state rate plus the
Rapides Parish rate. Change it to match where you'll register the car.

## Layout

```
car_search/
  config.py        TOML config + env-var secrets
  models.py        Listing record shared by all sources
  sources/         marketcheck.py, dealer_feed.py, sample.py, base.py
  dedupe.py        VIN merge (cheapest copy wins, flags OR-ed, all URLs kept)
  nhtsa.py         vPIC decode + recalls
  db.py            SQLite schema and queries
  scoring.py       market model + adjustments
  alerts.py        email / Telegram
  finance.py       payment + resale estimate
  pipeline.py      one end-to-end run
  cli.py           command line
dashboard/app.py   Streamlit dashboard
tests/             pytest suite (no network needed)
```

Run the tests with `pytest`.
