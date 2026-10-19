"""MD Computers product scraper (Q1 - Affinity Answers Data Engineer assessment).

Searches https://mdcomputers.in for a term and extracts structured product records.

Modes:
  * live        - tiered fetcher (see ``Fetcher``), each tier with retry/backoff:
                    1. curl_cffi  - Chrome TLS (JA3/JA4) + HTTP/2 fingerprint impersonation
                    2. httpx      - plain HTTP client with browser headers
                    3. browser    - headless Chrome/Edge ``--dump-dom`` (real browser engine)
  * --from-file - parse a saved HTML page (offline / deterministic testing)

Output formats: jsonl (default), json, csv, table (rich).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

try:  # Tier 1 is optional at import time so --from-file works without it.
    from curl_cffi import requests as cffi_requests
    from curl_cffi.requests.exceptions import RequestException as CffiRequestError
except ImportError:  # pragma: no cover - exercised only when curl_cffi is absent
    cffi_requests = None
    CffiRequestError = None

BASE_URL = "https://mdcomputers.in"
IMPERSONATE = "chrome124"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

PRICE_RE = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)
DISCOUNT_RE = re.compile(r"-\s*(\d+)\s*%")
YEAR_RE = re.compile(r"\d{4}")


@dataclass
class Product:
    product_name: str
    product_url: str
    price_discounted: Optional[float]
    price_original: Optional[float]
    price_raw: str
    discount_percent: Optional[int]
    stock_status: str
    product_id: Optional[str] = None
    model: Optional[str] = None
    sku: Optional[str] = None
    brand: Optional[str] = None
    page: int = 1
    scraped_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    source_url: str = ""


def parse_price(text: str) -> Optional[float]:
    """Convert '₹4,995' / 'Rs. 1,299.00' -> 4995.0 / 1299.0."""
    if not text:
        return None
    m = PRICE_RE.search(text.replace("\xa0", " "))
    if not m:
        digits = re.sub(r"[^\d.]", "", text)
        return float(digits) if digits else None
    return float(m.group(1).replace(",", ""))


def parse_discount(label: str) -> Optional[int]:
    m = DISCOUNT_RE.search(label or "")
    return int(m.group(1)) if m else None


def build_search_url(term: str, page: int = 1, limit: int = 40, base_url: str = BASE_URL) -> str:
    query = {"route": "product/search", "search": term}
    if page > 1:
        query["page"] = str(page)
    if limit and limit != 40:
        query["limit"] = str(limit)
    return f"{base_url}/?{urlencode(query)}"


def _detect_stock(card) -> str:
    btn = card.select_one("button.add-to-cart-loop, button.ajax_add_to_cart, .add-to-cart-loop")
    if btn is not None:
        disabled = btn.get("disabled") is not None
        cls = " ".join(btn.get("class", []))
        if disabled or "disabled" in cls:
            return "Out of Stock"
        return "In Stock"
    text = card.get_text(" ", strip=True).lower()
    if "out of stock" in text or "unavailable" in text:
        return "Out of Stock"
    if "pre-order" in text or "preorder" in text:
        return "Pre-Order"
    return "Unknown"


def _extract_product_id(card) -> Optional[str]:
    for btn in card.select("button[onclick*='cart.add'], button[onclick*='wishlist.add']"):
        m = re.search(r"cart\.add\(['\"]?(\d+)", btn.get("onclick", ""))
        if m:
            return m.group(1)
        m = re.search(r"add\(['\"]?(\d+)", btn.get("onclick", ""))
        if m:
            return m.group(1)
    return None


def parse_search_page(html: str, page: int = 1, source_url: str = "") -> list[Product]:
    """Parse one mdcomputers.in search-results page (RetrinaPro/OpenCart theme).

    DOM (verified against live site HTML, 2025-2026 snapshots):
      cards      : div.product-grid-item
      name/url   : h3.product-entities-title > a
      price      : span.price > span.del > span.amount  (MRP, if discounted)
                       span.price > span.ins > span.amount  (selling price)
      discount   : span.product-label.onsale  ("-51%")
      stock      : button.add-to-cart-loop presence
      product id : onclick="cart.add('21177')"
      pagination : ul.pagination > li > a.page-link[href*='page=']
    """
    soup = BeautifulSoup(html, "lxml")
    products: list[Product] = []

    for card in soup.select("div.product-grid-item"):
        title_a = card.select_one("h3.product-entities-title a") or card.select_one(
            "h3 a.product-name, .product-title a"
        )
        if title_a is None:
            continue
        name = title_a.get_text(" ", strip=True)
        href = title_a.get("href") or ""
        url = urljoin(source_url or BASE_URL, href)

        price_el = card.select_one("span.price")
        price_raw = price_el.get_text(" ", strip=True) if price_el else ""
        original = discounted = None
        if price_el is not None:
            del_el = price_el.select_one("span.del span.amount, span.del .amount")
            ins_el = price_el.select_one("span.ins span.amount, span.ins .amount")
            if del_el is not None and ins_el is not None:
                original = parse_price(del_el.get_text(" ", strip=True))
                discounted = parse_price(ins_el.get_text(" ", strip=True))
            elif ins_el is not None:
                discounted = parse_price(ins_el.get_text(" ", strip=True))
            else:
                amounts = price_el.select("span.amount")
                if amounts:
                    discounted = parse_price(amounts[-1].get_text(" ", strip=True))
                else:
                    discounted = parse_price(price_raw)

        label_el = card.select_one("span.product-label.onsale, .product-label")
        discount = parse_discount(label_el.get_text(" ", strip=True)) if label_el else None

        if original is None and discounted is not None:
            original = discounted

        products.append(
            Product(
                product_name=name,
                product_url=url,
                price_discounted=discounted,
                price_original=original,
                price_raw=price_raw,
                discount_percent=discount,
                stock_status=_detect_stock(card),
                product_id=_extract_product_id(card),
                page=page,
                source_url=source_url,
            )
        )
    return products


def parse_pagination_last_page(html: str) -> Optional[int]:
    """Max page number advertised in ul.pagination, or None."""
    soup = BeautifulSoup(html, "lxml")
    pages = []
    for a in soup.select("ul.pagination a.page-link[href]"):
        for key, val in parse_qsl(urlsplit(a["href"]).query):
            if key == "page" and val.isdigit():
                pages.append(int(val))
    return max(pages) if pages else None


def enrich_product(fetcher: "Fetcher", product: Product) -> Product:
    """Fetch the product detail page to fill model / sku / brand / stock.

    Detail DOM: ul.product-status -> "Brand: X Product Code: MODEL Availability: In Stock"
    plus application/ld+json with model, sku, offers.
    """
    try:
        html = fetcher.fetch(product.product_url)
    except FetchError:
        return product

    soup = BeautifulSoup(html, "lxml")
    status = soup.select_one(".product-status")
    if status:
        text = status.get_text(" ", strip=True)
        m = re.search(r"Brand:\s*(.+?)\s+Product Code:", text)
        if m:
            product.brand = m.group(1)
        m = re.search(r"Product Code:\s*(.+?)\s+Availability:", text)
        if m:
            product.model = m.group(1)
        m = re.search(r"Availability:\s*(.+)$", text)
        if m:
            product.stock_status = m.group(1).strip()

    for script in soup.select("script[type='application/ld+json']"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("@type") == "Product":
            product.model = product.model or data.get("model")
            product.sku = product.sku or data.get("sku")
            if not product.brand and isinstance(data.get("brand"), dict):
                product.brand = data["brand"].get("name")
            break
    return product


class FetchError(RuntimeError):
    pass


# Interstitial pages served instead of content. Note: every mdcomputers.in page embeds
# the passive "/cdn-cgi/challenge-platform" beacon, so that string is NOT a marker.
CHALLENGE_MARKERS = (
    "<title>Just a moment...</title>",
    "<title>Attention Required! | Cloudflare</title>",
    "cf-turnstile-response",
    "window._cf_chl_opt",
)
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
FETCH_TIERS = ("curl_cffi", "httpx", "browser")
NETWORK_ERRORS: tuple[type[BaseException], ...] = (httpx.HTTPError,) + (
    (CffiRequestError,) if CffiRequestError is not None else ()
)

BROWSER_CANDIDATES = (
    "chrome",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "msedge",
    "microsoft-edge",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
    "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
)


def is_challenge_page(html: str) -> bool:
    return any(marker in html for marker in CHALLENGE_MARKERS)


def find_browser() -> Optional[str]:
    for candidate in BROWSER_CANDIDATES:
        path = shutil.which(candidate) or (candidate if os.path.isfile(candidate) else None)
        if path:
            return path
    return None


class Fetcher:
    """Tiered HTML fetcher: the first tier that returns real (non-challenge) HTML wins.

    Cloudflare fingerprints the TLS ClientHello (JA3/JA4) and HTTP/2 SETTINGS frames, so
    python-requests/httpx/curl are flagged before any header is read. curl_cffi links
    curl-impersonate and replays Chrome's exact handshake, which is why it is tier 1.
    httpx stays as a cheap fallback for mirrors/staging hosts without bot management,
    and a headless real browser is the last resort for a JavaScript (Turnstile) challenge.
    """

    def __init__(
        self,
        tiers: Sequence[str] = FETCH_TIERS,
        timeout: float = 30.0,
        retries: int = 3,
        backoff_base: float = 1.5,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        unknown = set(tiers) - set(FETCH_TIERS)
        if unknown:
            raise ValueError(f"unknown fetch tier(s): {sorted(unknown)}")
        self.tiers = [t for t in tiers if t != "curl_cffi" or cffi_requests is not None]
        self.timeout = timeout
        self.retries = retries
        self.backoff_base = backoff_base
        self.log = log or (lambda _msg: None)
        self.last_tier: Optional[str] = None
        self._sessions: dict = {}

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        for session in self._sessions.values():
            session.close()
        self._sessions.clear()

    def fetch(self, url: str) -> str:
        errors = []
        for tier in self.tiers:
            try:
                if tier == "browser":
                    html = self._fetch_browser(url)
                else:
                    html = self._fetch_http(tier, url)
            except FetchError as exc:
                errors.append(str(exc))
                self.log(f"[!] {exc} -> falling back")
                continue
            self.last_tier = tier
            return html
        raise FetchError(
            "all fetch tiers failed: " + " | ".join(errors) if errors else "no fetch tiers enabled"
        )

    def _session(self, tier: str):
        if tier not in self._sessions:
            if tier == "curl_cffi":
                self._sessions[tier] = cffi_requests.Session(
                    impersonate=IMPERSONATE, timeout=self.timeout
                )
            else:
                self._sessions[tier] = httpx.Client(
                    headers=BROWSER_HEADERS, timeout=self.timeout, follow_redirects=True
                )
        return self._sessions[tier]

    def _fetch_http(self, tier: str, url: str) -> str:
        last_error = "no attempt made"
        for attempt in range(self.retries + 1):
            try:
                resp = self._session(tier).get(url)
                challenged = is_challenge_page(resp.text)
                if resp.status_code == 200 and not challenged:
                    return resp.text
                last_error = f"HTTP {resp.status_code}" + (" (Cloudflare challenge)" if challenged else "")
                # A fingerprint block won't clear by retrying the same fingerprint.
                if challenged or resp.status_code not in RETRYABLE_STATUS:
                    break
            except NETWORK_ERRORS as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < self.retries:
                time.sleep(min(self.backoff_base ** attempt + random.uniform(0, 0.4), 20))
        raise FetchError(f"{tier}: {last_error} for {url}")

    def _fetch_browser(self, url: str) -> str:
        exe = find_browser()
        if exe is None:
            raise FetchError("browser: no Chrome/Edge/Chromium executable found")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as profile:
            cmd = [
                exe,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                f"--user-data-dir={profile}",
                "--virtual-time-budget=15000",
                "--dump-dom",
                url,
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout + 30,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise FetchError(f"browser: {type(exc).__name__}: {exc}") from exc
        html = proc.stdout
        if not html.strip():
            raise FetchError(f"browser: empty DOM (exit {proc.returncode}) for {url}")
        if is_challenge_page(html):
            raise FetchError(f"browser: Cloudflare challenge not cleared for {url}")
        return html


def scrape(
    search_term: str,
    max_pages: int = 1,
    limit: int = 40,
    base_url: str = BASE_URL,
    timeout: float = 30.0,
    retries: int = 3,
    delay: float = 1.0,
    enrich: bool = False,
    from_file: Optional[Path] = None,
    tiers: Sequence[str] = FETCH_TIERS,
    log: Optional[Callable[[str], None]] = None,
) -> list[Product]:
    log = log or (lambda _msg: None)
    with Fetcher(tiers=tiers, timeout=timeout, retries=retries, log=log) as fetcher:
        if from_file is not None:
            html = from_file.read_text(encoding="utf-8", errors="replace")
            products = parse_search_page(html, page=1, source_url=base_url)
            if enrich:
                products = [enrich_product(fetcher, p) for p in products]
            return products

        products: list[Product] = []
        last_page = max_pages
        for page in range(1, max_pages + 1):
            url = build_search_url(search_term, page=page, limit=limit, base_url=base_url)
            html = fetcher.fetch(url)
            log(f"[+] page {page}: fetched via {fetcher.last_tier}")
            batch = parse_search_page(html, page=page, source_url=url)
            if not batch:
                break
            if enrich:
                batch = [enrich_product(fetcher, p) for p in batch]
            products.extend(batch)
            if page == 1:
                last_page = min(max_pages, parse_pagination_last_page(html) or 1)
            if page >= last_page:
                break
            time.sleep(delay + random.uniform(0, 0.3))
    return products


def to_jsonl(products: Iterable[Product]) -> str:
    return "\n".join(json.dumps(asdict(p), ensure_ascii=False) for p in products) + "\n"


def to_json(products: list[Product]) -> str:
    return json.dumps([asdict(p) for p in products], ensure_ascii=False, indent=2) + "\n"


def to_csv(products: list[Product]) -> str:
    rows = [asdict(p) for p in products]
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def to_table(products: list[Product]) -> str:
    try:
        from rich.console import Console
        from rich.table import Table

        table = Table(title=f"mdcomputers.in products ({len(products)} rows)", show_lines=False)
        table.add_column("#", justify="right", style="dim")
        table.add_column("Product", max_width=52, overflow="ellipsis")
        table.add_column("Price (INR)", justify="right")
        table.add_column("MRP", justify="right")
        table.add_column("Disc %", justify="right")
        table.add_column("Stock")
        for i, p in enumerate(products, 1):
            disc = f"{p.discount_percent}%" if p.discount_percent is not None else "-"
            price = f"{p.price_discounted:,.0f}" if p.price_discounted is not None else "-"
            mrp = f"{p.price_original:,.0f}" if p.price_original is not None else "-"
            stock_style = "green" if p.stock_status == "In Stock" else "red"
            table.add_row(
                str(i), p.product_name, price, mrp, disc, f"[{stock_style}]{p.stock_status}[/{stock_style}]"
            )
        console = Console()
        with console.capture() as capture:
            console.print(table)
        return capture.get()
    except ImportError:
        header = f"{'#':>3}  {'PRODUCT':<52}  {'PRICE':>10}  {'MRP':>10}  {'DISC':>5}  STOCK"
        lines = [header, "-" * len(header)]
        for i, p in enumerate(products, 1):
            price = f"{p.price_discounted:,.0f}" if p.price_discounted is not None else "-"
            mrp = f"{p.price_original:,.0f}" if p.price_original is not None else "-"
            disc = f"{p.discount_percent}%" if p.discount_percent is not None else "-"
            lines.append(f"{i:>3}  {p.product_name[:52]:<52}  {price:>10}  {mrp:>10}  {disc:>5}  {p.stock_status}")
        return "\n".join(lines) + "\n"


FORMATTERS = {"jsonl": to_jsonl, "json": to_json, "csv": to_csv, "table": to_table}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scraper.py",
        description="Scrape product search results from mdcomputers.in",
    )
    parser.add_argument("term_positional", nargs="?", help="Search term (positional)")
    parser.add_argument("--search", dest="term_option", help="Search term (--search)")
    parser.add_argument(
        "--format",
        choices=sorted(FORMATTERS),
        default="jsonl",
        help="Output format (default: jsonl)",
    )
    parser.add_argument("--output", "-o", help="Write output to file instead of stdout")
    parser.add_argument("--max-pages", type=int, default=1, help="Max result pages (default 1)")
    parser.add_argument("--limit", type=int, default=40, help="Products per page (site default 40)")
    parser.add_argument("--base-url", default=BASE_URL, help="Override base URL (e.g. staging mirror)")
    parser.add_argument(
        "--fetcher",
        choices=("auto",) + FETCH_TIERS,
        default="auto",
        help="Fetch tier: auto = curl_cffi -> httpx -> headless browser (default auto)",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout seconds")
    parser.add_argument("--retries", type=int, default=3, help="Retry attempts per page")
    parser.add_argument("--delay", type=float, default=1.0, help="Polite delay between pages")
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Visit each product page to fill model/sku/brand/stock (slower)",
    )
    parser.add_argument(
        "--from-file",
        type=Path,
        help="Parse a saved HTML search-results page instead of live fetch",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress progress messages on stderr"
    )
    return parser


def resolve_search_term(args: argparse.Namespace) -> str:
    term = args.term_option or args.term_positional
    if term:
        return term
    if args.from_file:
        return ""
    if not sys.stdin.isatty():
        return ""
    try:
        entered = input("Enter mdcomputers.in search term (e.g. 'external hard drive'): ").strip()
        return entered
    except EOFError:
        return ""


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    term = resolve_search_term(args)
    if not term and not args.from_file:
        parser.error("a search term is required (positional, --search, or interactive input)")

    def log(msg: str) -> None:
        if not args.quiet:
            print(msg, file=sys.stderr)

    log(f"[*] Searching mdcomputers.in for: {term!r}")
    try:
        products = scrape(
            search_term=term or "external hard drive",
            max_pages=args.max_pages,
            limit=args.limit,
            base_url=args.base_url,
            timeout=args.timeout,
            retries=args.retries,
            delay=args.delay,
            enrich=args.enrich,
            from_file=args.from_file,
            tiers=FETCH_TIERS if args.fetcher == "auto" else (args.fetcher,),
            log=log,
        )
    except FetchError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        print(
            "[hint] mdcomputers.in sits behind Cloudflare; datacenter IPs are often blocked. "
            "Retry from a residential network, or use --from-file with a saved page "
            "(e.g. tests/fixtures/mdcomputers_search_external_harddrive.html).",
            file=sys.stderr,
        )
        return 2

    log(f"[+] Extracted {len(products)} products")
    output = FORMATTERS[args.format](products)

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        log(f"[+] Wrote {args.output}")
    else:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(
            sys.stdout, "reconfigure"
        ) else None
        sys.stdout.write(output)
    return 0 if products else 1


if __name__ == "__main__":
    raise SystemExit(main())
