# Architecture

```
AffinityAnswers/
├── q1_python_scraper/      Q1  mdcomputers.in product scraper
│   ├── scraper.py              Fetcher (tiers) → parse_search_page → formatters → CLI
│   ├── sample_output.jsonl     live run, "external hard drive"
│   └── DESIGN_DECISIONS.md     JSONL choice, Cloudflare strategy
├── q2_sql_rfam/            Q2  Rfam public MySQL
│   ├── queries.sql             single source of truth (named blocks)
│   ├── run_queries.py          parses queries.sql, runs live, renders RESULTS.md
│   └── RESULTS.md              exact live outputs of the full run
├── q3_unix_shell/          Q3  S&P 500 CSV → TSV sorted by founding year
│   ├── parse_companies.sh      bash + POSIX awk RFC 4180 state machine
│   └── sample_output.tsv
├── tests/                  pytest; fixtures/ holds a real mdcomputers.in page
├── scripts/run_demo.sh     end-to-end demo (~30 s)
└── docs/Architecture.md
```

## Q1 data flow

```
search term ──► build_search_url ──► Fetcher.fetch(url)
                                       ├─ tier 1 curl_cffi  (impersonate="chrome124")
                                       ├─ tier 2 httpx      (browser headers)
                                       └─ tier 3 headless Chrome/Edge --dump-dom
                                     first non-challenge HTML wins
             --from-file PATH ─────────────────┐
                                               ▼
                                   parse_search_page (BeautifulSoup + lxml)
                                               ▼
                         list[Product]  (numeric prices + raw text + lineage)
                                               ▼
                         to_jsonl | to_json | to_csv | to_table
```

Fetching and parsing are decoupled: tests and offline demos parse a captured page through the
same code path as live runs. Details: [`q1_python_scraper/DESIGN_DECISIONS.md`](../q1_python_scraper/DESIGN_DECISIONS.md).

Observed site behaviour:

- Cloudflare answers `httpx`/`requests` with `403` + `<title>Just a moment...</title>` whatever the
  headers — a TLS/HTTP2 fingerprint block. `curl_cffi` impersonating Chrome 124 gets `200`.
- Headless Chrome (`--headless=new --dump-dom`) is challenged too, so it is a last resort only.
- Every normal page contains the passive `/cdn-cgi/challenge-platform/...` beacon and
  `__cfRLUnblockHandlers` in `onclick` attributes; neither means the request was challenged.
- Search URL: `/?route=product%2Fsearch&search=term+words[&page=N][&limit=N]`, 20 cards per page.

## Q2

`queries.sql` holds `-- name:` / `-- title:` blocks; `run_queries.py` parses them, so the SQL a
reviewer reads is byte-for-byte the SQL that is executed. Connection: `pymysql` to
`mysql-rfam-public.ebi.ac.uk:4497`, user `rfamro`, no password, database `Rfam`.

Two modes:

- **default (fast, ~10 s):** Q2.1 and Q2.2 live; for Q2.3/Q2.4 prints the SQL, the page-9 rows
  recorded by the last full run, and a live spot-check (each listed family has a hit on the
  836,514,780 bp sequence `LT934116.1`, an indexed lookup).
- **`--full` (~5 min):** every query live, rewrites `RESULTS.md`.

Database facts that shaped the SQL:

- Collation is case-insensitive: `LIKE '%Acacia%'` also matches `acaciae` (insects, yeast) and
  *Arracacia*, hence three reported readings for Q2.1.
- `rfamseq` ≈ 81M rows (no index on `length`), `full_region` ≈ 10M, `family` ≈ 4k.
- The literal page-9 join order (`family ⋈ full_region ⋈ rfamseq`) did not finish in 10+ minutes
  (~10M primary-key lookups). Reading `rfamseq` first with `WHERE length > 1000000` (one ~100 s scan
  → 160,660 sequences) is result-identical and takes ~2.5 min per variant.
- Many families tie at 836,514,780 bp, so `ORDER BY` adds `rfam_acc` for a deterministic page 9.

## Q3

`curl -fsSL` → awk (quote-aware CSV split, header-name column lookup, emits `year_key<TAB>row`) →
`LC_ALL=C sort -k1,1n -k2,2` → `cut -f2-`. Pure POSIX awk, so gawk/mawk/BSD awk behave identically.

## Cross-cutting decisions

| Decision | Reason |
|---|---|
| Network tests opt-in (`LIVE=1`) | `make test` must be deterministic and must not hammer third-party sites |
| Committed outputs (`sample_output.*`, `RESULTS.md`) | Reviewers can see results without credentials or network |
| `.gitattributes eol=lf` | Developed on Windows (Git Bash, GNU awk 5.4, Python 3.14); scripts must still run on Linux/macOS/WSL |
