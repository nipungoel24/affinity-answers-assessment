# Q1 — Design Decisions

## 1. Why JSON Lines (`.jsonl`) is the primary output

| Concern | JSON Lines | CSV | Single JSON array |
|---|---|---|---|
| Memory to write/read | O(1) per record — stream line by line | O(1) | O(n) — whole array must be parsed |
| Parallel / splittable ingestion | Yes — any newline is a record boundary (Spark, BigQuery `NEWLINE_DELIMITED_JSON`, Snowflake `COPY INTO … FILE_FORMAT=(TYPE=JSON)`, `jq -c`) | Yes, until a quoted field contains a newline | No |
| Types | Strict: `9160.0` is a number, `null` is null, `discount_percent` is an int | Everything is a string; `₹9,160` vs `9160` ambiguity | Strict |
| Escaping | JSON escaping is unambiguous | Commas/quotes in product names (`"WD My Passport 2TB, Red"`) are a classic source of shifted columns | Unambiguous |
| Appending incremental scrapes | `>>` a new line | Header handling required | Must rewrite the file |

Each record keeps both the **normalised numeric prices** (`price_discounted`, `price_original`) and the
**raw text** (`price_raw`) so downstream jobs never have to re-parse currency strings, but an auditor can
still see exactly what the page showed. `scraped_at` (UTC ISO-8601) and `source_url` make every row
self-describing lineage.

`--format table` (rich) is for humans at the terminal; `--format csv` / `--format json` exist for
spreadsheet users and small API payloads. The formatters are pure functions over `list[Product]`, so
adding Parquet later is one function.

## 2. Getting past Cloudflare: the tiered fetcher

mdcomputers.in is behind Cloudflare Bot Management. Cloudflare decides *before reading any HTTP header*
by fingerprinting the TLS ClientHello (JA3/JA4: cipher order, extensions, GREASE, ALPN) and the HTTP/2
SETTINGS/priority frames. Python's `ssl`-based clients (`requests`, `httpx`, `urllib`) and stock `curl`
present OpenSSL fingerprints that no real browser produces, so spoofing `User-Agent` alone is useless.

`Fetcher` (in `scraper.py`) tries tiers in order and returns the first page that is real content:

| Tier | Implementation | Observed result (2026-10-18, residential IP) |
|---|---|---|
| 1. `curl_cffi` | `curl_cffi.requests.Session(impersonate="chrome124")` — curl-impersonate replays Chrome 124's exact TLS + HTTP/2 handshake | **HTTP 200, real product grid** |
| 2. `httpx` | Plain client with browser headers; kept for mirrors/staging hosts without bot management | HTTP 403, `Just a moment...` challenge |
| 3. `browser` | Headless Chrome/Edge `--dump-dom` (auto-discovered, temp profile) | Challenge not cleared — Cloudflare flags headless Chrome |

Design points:

- **Challenge detection is content-based, not status-based.** Cloudflare can return a challenge with
  200. `is_challenge_page()` looks for the interstitial title / `_cf_chl_opt` / Turnstile markers.
  The passive `/cdn-cgi/challenge-platform` beacon that every normal mdcomputers.in page includes is
  deliberately *not* a marker (a naive check produced false positives on good pages).
- **Retries only where they can help.** 429/5xx get exponential backoff with jitter. A fingerprint
  block (403 or a challenge) breaks out immediately and falls through to the next tier — retrying the
  same fingerprint just burns requests and raises the IP's bot score.
- **One pooled session per tier**, reused for every page and for `--enrich` detail-page fetches.
- **Politeness:** `--delay` between pages and pagination stops at the last advertised page.
- `--fetcher curl_cffi|httpx|browser` pins one tier, which is how the table above was produced.

## 3. Offline mode decouples parsing from fetching

`--from-file PATH` runs the identical `parse_search_page()` on a saved page. A real search page
captured by tier 1 lives at `tests/fixtures/mdcomputers_search_external_harddrive.html`, so the test
suite and the demo are deterministic and do not depend on the network or on Cloudflare's mood. If
mdcomputers.in changes its theme, the fix is a selector change verified against a freshly saved page.

## 4. Parsing

The site runs OpenCart with the RetrinaPro theme. Selectors (documented in `parse_search_page`):

- card: `div.product-grid-item`; name + URL: `h3.product-entities-title > a`
- prices: `span.price > span.del .amount` (MRP) and `span.ins .amount` (selling price); a card with a
  single price gets `price_original == price_discounted`
- discount: `span.product-label.onsale` (`-58%` → `58`)
- stock: presence/disabled state of `button.add-to-cart-loop`
- product id: `cart.add('16447')` in the button's `onclick`

`--enrich` visits each product page for brand / model / SKU (from `.product-status` and JSON-LD).
It is off by default because it multiplies request count by ~20× per page.

## Known limitations

- Tier 3 does not defeat an interactive Turnstile challenge; that requires a headed browser and a human,
  which is out of scope for an unattended scraper.
- Cloudflare policy differs by IP reputation: datacenter / CI IPs may be blocked even for tier 1.
  `--from-file` is the documented fallback.
