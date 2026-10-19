import csv
import io
import json

import pytest
from conftest import FIXTURES

from q1_python_scraper import scraper
from q1_python_scraper.scraper import (
    FetchError,
    Fetcher,
    Product,
    build_search_url,
    is_challenge_page,
    parse_discount,
    parse_pagination_last_page,
    parse_price,
    parse_search_page,
)

SEARCH_PAGE = FIXTURES / "mdcomputers_search_external_harddrive.html"


@pytest.fixture(scope="module")
def html():
    return SEARCH_PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def products(html):
    return parse_search_page(html, source_url="https://mdcomputers.in")


def test_parses_every_product_card(products):
    assert len(products) == 20
    assert all(p.product_name and p.product_url.startswith("https://mdcomputers.in/") for p in products)


def test_first_card_fields(products):
    p = products[0]
    assert p.product_name == "EK-Loop Connect - External USB Cable (1M)"
    assert p.price_original == 1299.0
    assert p.price_discounted == 550.0
    assert p.discount_percent == 58
    assert p.stock_status == "In Stock"
    assert p.product_id == "15127"


def test_prices_are_numeric_and_consistent(products):
    for p in products:
        assert isinstance(p.price_discounted, float)
        assert p.price_original >= p.price_discounted


def test_pagination(html):
    assert parse_pagination_last_page(html) == 3


@pytest.mark.parametrize(
    "text, expected",
    [("₹4,995", 4995.0), ("Rs. 1,299.00", 1299.0), ("INR 10", 10.0), ("", None), ("call us", None)],
)
def test_parse_price(text, expected):
    assert parse_price(text) == expected


def test_parse_discount():
    assert parse_discount("-58%") == 58
    assert parse_discount("New") is None


def test_build_search_url_matches_site_format():
    assert build_search_url("external harddrive") == (
        "https://mdcomputers.in/?route=product%2Fsearch&search=external+harddrive"
    )
    assert build_search_url("ssd", page=2).endswith("&page=2")


def test_challenge_detection(html):
    assert not is_challenge_page(html)  # real page includes the passive CF beacon
    assert is_challenge_page("<html><title>Just a moment...</title></html>")


def test_fetcher_falls_back_to_next_tier(monkeypatch):
    calls = []

    def fake_http(self, tier, url):
        calls.append(tier)
        if tier == "curl_cffi":
            raise FetchError("curl_cffi: HTTP 403 (Cloudflare challenge)")
        return "<html>ok</html>"

    monkeypatch.setattr(Fetcher, "_fetch_http", fake_http)
    with Fetcher(tiers=("curl_cffi", "httpx")) as f:
        assert f.fetch("https://example.test") == "<html>ok</html>"
        assert f.last_tier == "httpx"
    assert calls == ["curl_cffi", "httpx"]


def test_fetcher_raises_when_all_tiers_fail(monkeypatch):
    def fail(self, tier, url):
        raise FetchError(f"{tier}: down")

    monkeypatch.setattr(Fetcher, "_fetch_http", fail)
    with Fetcher(tiers=("httpx",)) as f, pytest.raises(FetchError, match="all fetch tiers failed"):
        f.fetch("https://example.test")


def test_fetcher_rejects_unknown_tier():
    with pytest.raises(ValueError):
        Fetcher(tiers=("selenium",))


def test_cli_from_file_jsonl(tmp_path):
    out = tmp_path / "out.jsonl"
    rc = scraper.main(["--from-file", str(SEARCH_PAGE), "--output", str(out), "--quiet"])
    assert rc == 0
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 20
    assert rows[0]["price_discounted"] == 550.0


def test_csv_escapes_commas_and_quotes():
    p = Product("Drive, 2TB \"Red\"", "https://x", 1.0, 2.0, "₹1", 50, "In Stock")
    row = next(csv.DictReader(io.StringIO(scraper.to_csv([p]))))
    assert row["product_name"] == 'Drive, 2TB "Red"'


def test_table_format_renders(products):
    assert "mdcomputers.in products (20 rows)" in scraper.to_table(products)


@pytest.mark.live
def test_live_curl_cffi_gets_past_cloudflare():
    products = scraper.scrape("external hard drive", tiers=("curl_cffi",), retries=1)
    assert products, "curl_cffi tier returned no products"
