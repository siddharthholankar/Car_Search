# Car Search: a used-car deal finder that runs on your own browser

There are no APIs, keys or paid services. You browse used-car listings the way you normally do,
on Cars.com, CarGurus, Autotrader, Carfax, Facebook or dealer sites. A small browser extension
saves the cars on each page you open to a program running on your own computer. That program
then:

- **merges duplicates by VIN**, since the same car often shows up on several sites;
- **scores each deal** against a market-price model trained on the cars you've collected;
- **pops up a browser notification** when a car is at least 8% below market (email is optional);
- **tracks price history** each time you see a car again;
- shows everything in a **dashboard**: filters, a map, price history, and a payment calculator.

```
pages you open ─▶ extension ─▶ car-search serve (127.0.0.1:8765, your computer only)
                                  │ extract → dedupe by VIN → filter to your search
                                  │ → SQLite → market model → deal score
                  notification ◀──┘                         └─▶ dashboard (Streamlit)
```

The extension reads only pages you choose to open. It never crawls sites on its own or loads
pages in the background, so it doesn't trip the sites' anti-bot blocks or their rules against
automated scraping.

## Setup (about 5 minutes)

1. **Install the program.** You need [Python 3.11+](https://www.python.org/downloads/); on
   Windows, tick "Add python.exe to PATH" in the installer. Then download the code:
   ```bash
   git clone -b claude/awesome-davinci-vda2r7 https://github.com/siddharthholankar/Car_Search.git
   ```
   (Or, on GitHub, switch to that branch and use **Code → Download ZIP**.)

   - **Windows:** double-click `install.bat` once, then `start.bat` whenever you want to use it.
   - **Mac/Linux:** run `./install.sh` once, then `./start.sh`.

   The install scripts set everything up inside the folder (in `.venv`) and create
   `config.toml`. Edit that file with your zip, models, max price and miles. The start scripts
   run the capture server and open the dashboard.
2. **Install the extension** in Chrome, Edge or Brave:
   - Open `chrome://extensions` (or `edge://extensions`).
   - Turn on **Developer mode**.
   - Click **Load unpacked** and choose this project's `extension/` folder.
   - Pin the 🚗 icon to the toolbar.
3. **Browse.** Search for your cars on any listing site. Results pages and single-car pages both
   work. The icon's badge shows how many cars on the page match your search, and turns into a
   green **$** when the page has a deal. Click the icon to see each car's score.
4. **See everything** in the dashboard that `start.bat`/`start.sh` opens, or click **Open dashboard**
   in the popup.

Want to look around before setting anything up? Run `car-search demo`. It loads about 90 made-up
cars (their VINs start with `DEMO`).

## How pages are read

- **Structured data first.** Most listing and dealer sites embed schema.org `Car`/`Vehicle`
  data in the page for search engines. That gives the VIN, price, miles, year, make, model,
  trim, drivetrain, the dealer and its location. One reader handles every site that uses it,
  and it doesn't break when a site redesigns.
- **Plain-text fallback.** For a single car's page without that data, it looks for exactly one
  VIN, plus the title, price and mileage in the text. Pages showing several VINs but no
  structured data are skipped, because it can't reliably tell which price goes with which car.
- **Search filter.** Cars outside your search (model, year, price, miles, distance) are ignored.
  Model names match loosely: `CR-V` also matches `CRV` and `CR-V Hybrid`.
- **Sold cars.** You won't revisit every listing, so a car you haven't seen in `stale_days`
  (default 14) is treated as sold and drops out of the active list.
- **No extension?** You can save a page with the browser's **Save page as… → Webpage, HTML
  only** and run `car-search import page.html`. Inventory CSV or JSON files that some dealers
  offer for download import the same way.

**Site coverage varies.** Sites that embed vehicle data work best. Some sites build their pages
in ways that hide the VIN. For those, open the car's own page (one car per page), and the text
fallback usually catches it. If a site you use isn't being picked up, a page saved from it
with "Save page as" is all that's needed to add support for it in `extract.py`.

## The deal score

```
value_score = (predicted_market_price − listing_price) / predicted_market_price
deal_score  = value_score + bonuses − penalties
```

- **Market price model:** a ridge regression on log(price). Its inputs are age, age², miles,
  miles per year, days listed, make/model, make/model/trim, drivetrain and state.
  - It trains on every car seen in the last 90 days.
  - Each car's price is predicted **out of fold**, so a car's own asking price never feeds the
    estimate it's compared against.
  - Salvage and rebuilt cars are left out of the training data.
  - Until about 30 cars are collected, it uses the median price of the same model within ±1
    model year instead. Browsing a few results pages is enough to get past 30.
- **Penalties:** accident −5%, salvage/rebuilt −40%, repossession −5%, rental −3%, more than
  3 owners −3%.
- **Bonuses:** one owner +2%, service records +1%, certified pre-owned +2%, listed more than
  45 days +1% (the car is also flagged as negotiable).
- **Your own weights:** all of these can be changed under `[scoring]` in `config.toml`.
- **History flags** come from whatever the page shows. A free Carfax link on a listing isn't
  read automatically.

## Alerts

- A **browser notification** fires the first time a car scores at or above `threshold`.
  Clicking it opens the listing.
- A car alerts again only if its price falls at least `realert_drop` (default 3%) below the
  price it last alerted at.
- **Email** is optional: set `email_enabled = true` and your SMTP details. For Gmail, use an app
  password. `car-search test-alert` sends a test.

## Commands

| Command | What it does |
|---|---|
| `car-search init` | create `config.toml` |
| `car-search serve` | receive pages from the extension (keep it running while you browse) |
| `car-search dashboard` | open the Streamlit dashboard |
| `car-search top [-n 20] [--all]` | best current deals in the terminal |
| `car-search history VIN` | price changes for one car |
| `car-search import FILE...` | import saved `.html` pages or `.csv`/`.json` inventory files |
| `car-search payment PRICE --down --apr --term --tax --fees --trade-in` | loan payment, interest, resale estimate |
| `car-search demo` | load made-up sample cars |
| `car-search test-alert` | email the current best deal |

- **Recalls:** the dashboard has a link that opens the car's VIN on nhtsa.gov in your browser,
  where you can see open recalls.
- **Sales tax:** the payment calculator defaults to 9.45%, roughly the Louisiana state rate plus
  the Rapides Parish rate. Change it to match where you'll register the car.

## Privacy and safety

- **Content script:** the extension runs one on every page, but it sends nothing unless the page
  contains vehicle data or a VIN. Turn off "Save listings automatically" in the popup to capture
  only when you click **Capture this page**.
- **Where data goes:** pages go only to `127.0.0.1:8765` on your own computer, and everything is
  stored in `car_search.db` next to your config.
- **No fake listings from websites:** the local server accepts posts only from browser
  extensions, never from ordinary web pages.

## Layout

```
extension/         browser extension (Manifest V3): content.js reads pages, background.js
                   talks to car-search and shows notifications, popup.* is the toolbar popup
car_search/
  extract.py       schema.org + text-fallback extraction, saved-HTML parsing
  server.py        local capture server
  pipeline.py      ingest: dedupe → filter → store → score → alert
  dedupe.py        VIN merge (cheapest copy wins, flags OR-ed, all URLs kept)
  scoring.py       market model + adjustments
  db.py            SQLite schema and queries
  alerts.py        alert text + optional email
  importers.py     saved pages and CSV/JSON inventory files
  finance.py       payment + resale estimate
  config.py        TOML config
  sources/sample.py  made-up demo listings
  cli.py           command line
dashboard/app.py   Streamlit dashboard
tests/             pytest suite (runs offline)
```

Run the tests with `pip install -e ".[dev]"` and then `pytest`.
