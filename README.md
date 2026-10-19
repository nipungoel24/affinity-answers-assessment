# Affinity Answers — Data Engineer Assessment

Nipun Goel — three solutions, each with committed output, tests and a design note.
Architecture notes: [`docs/Architecture.md`](docs/Architecture.md).

| | Question | Solution | Output |
|---|---|---|---|
| Q1 | Scrape product info from mdcomputers.in for a search term | [`q1_python_scraper/scraper.py`](q1_python_scraper/scraper.py) | [`sample_output.jsonl`](q1_python_scraper/sample_output.jsonl) |
| Q2 | Rfam public MySQL queries | [`q2_sql_rfam/queries.sql`](q2_sql_rfam/queries.sql) | [`RESULTS.md`](q2_sql_rfam/RESULTS.md) |
| Q3 | S&P 500 CSV → company, location, founding year, sorted by year | [`q3_unix_shell/parse_companies.sh`](q3_unix_shell/parse_companies.sh) | [`sample_output.tsv`](q3_unix_shell/sample_output.tsv) |

## Quick start

```bash
python -m pip install -r requirements.txt
bash scripts/run_demo.sh   # ~30 s: Q1, Q2, Q3 live, then pytest -v
python -m pytest -q        # offline tests (deterministic)
LIVE=1 python -m pytest -q # adds tests that hit mdcomputers.in, Rfam and GitHub
```

The [`Makefile`](Makefile) wraps the same commands (`make demo`, `make test`, `make q2`, ...).

## Q1 — mdcomputers.in scraper

```bash
python q1_python_scraper/scraper.py --search "external hard drive" --format table
python q1_python_scraper/scraper.py "external hard drive" --max-pages 3 -o products.jsonl
python q1_python_scraper/scraper.py --from-file tests/fixtures/mdcomputers_search_external_harddrive.html
```

Options: `--format jsonl|json|csv|table`, `--output`, `--max-pages`, `--enrich` (brand/model/SKU from
product pages), `--fetcher auto|curl_cffi|httpx|browser`, `--from-file`.

Each record: `product_name, product_url, price_discounted, price_original, price_raw,
discount_percent, stock_status, product_id, model, sku, brand, page, scraped_at, source_url`.

**Cloudflare.** The site blocks Python HTTP clients by TLS (JA3/JA4) fingerprint, not by headers.
The fetcher tries `curl_cffi` impersonating Chrome 124 first (works), then `httpx`, then headless
Chrome/Edge, and detects challenge pages by content. Verified per tier — see
[`DESIGN_DECISIONS.md`](q1_python_scraper/DESIGN_DECISIONS.md), which also explains why JSON Lines is
the primary output.

## Q2 — Rfam

```bash
python q2_sql_rfam/run_queries.py           # ~10 s: Q2.1/Q2.2 live, page 9 recorded + live spot-check
python q2_sql_rfam/run_queries.py --full    # ~5 min: every query live, rewrites RESULTS.md
```

Answers (live, see [`RESULTS.md`](q2_sql_rfam/RESULTS.md) for full tables):

| | Answer |
|---|---|
| Q2.1 Acacia types | **326** species in genus *Acacia* (`species LIKE 'Acacia %'`). Alternative readings: 359 plants whose name/lineage mention Acacia; 389 for a bare `'%Acacia%'` wildcard, which also catches insects/yeasts named *acaciae* and the genus *Arracacia*. |
| Q2.2 Longest wheat sequence | ***Triticum durum* (durum wheat)**: ncbi_id 4567, `LT934116.1`, 836,514,780 bp |
| Q2.3/Q2.4 Page 9 (15/page, `OFFSET 120`) | significant hits: `RF01284 snoR8a` … `RF04335 Plastid-rpl20`; all regions: `RF01219 snoR100` … `RF03674 MIR5387`. All at 836,514,780 bp, so `rfam_acc` breaks ties. |

The literal join order did not finish in 10+ minutes on the public server. The committed SQL reads
`rfamseq` first with `WHERE length > 1000000`, which gives the same result (reasoning in
`queries.sql`) and finishes in about 2.5 minutes.

## Q3 — S&P 500 companies by founding year

```bash
bash q3_unix_shell/parse_companies.sh                       # default URL, TSV to stdout
bash q3_unix_shell/parse_companies.sh "<csv url or file>" out.tsv
```

`Headquarters Location` (`"Saint Paul, Minnesota"`) and some `Security` names contain quoted commas,
so `cut -d,` / `awk -F,` are wrong. The script uses a POSIX-awk quote-aware state machine (also
handles `""` escapes, multi-line fields, CRLF), finds columns by header name, and sorts by the first
4-digit year so `2013 (1888)` sorts as 2013. Rows without a year sort last.

## Tests

`tests/` — 33 offline tests plus 3 live tests (`LIVE=1`): parser against a real captured
mdcomputers.in page, tier fallback, CLI, SQL structure (page-9 offset, HAVING, joins), Markdown
rendering, and the shell parser against an edge-case CSV.

