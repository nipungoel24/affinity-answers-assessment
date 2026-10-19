#!/usr/bin/env bash
# run_demo.sh - end-to-end walkthrough of Q1, Q2, Q3 and the test suite (~30 s).
#
#   bash scripts/run_demo.sh
#   PYTHON=python3 bash scripts/run_demo.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-$(command -v python3 || command -v python)}"
FIXTURE="tests/fixtures/mdcomputers_search_external_harddrive.html"
START=$SECONDS

banner() { printf '\n\033[1;36m==== %s ====\033[0m\n' "$*"; }

banner "Q1  mdcomputers.in scraper (curl_cffi -> httpx -> headless browser)"
"$PYTHON" q1_python_scraper/scraper.py --search "external hard drive" --format table \
    || { echo "[demo] live fetch failed; parsing the saved page instead"; \
         "$PYTHON" q1_python_scraper/scraper.py --from-file "$FIXTURE" --format table; }

banner "Q2  Rfam public MySQL (mysql-rfam-public.ebi.ac.uk:4497)"
"$PYTHON" q2_sql_rfam/run_queries.py

banner "Q3  S&P 500 companies sorted by founding year (awk RFC 4180 parser)"
q3_out="$(mktemp)"
trap 'rm -f "$q3_out"' EXIT
bash q3_unix_shell/parse_companies.sh "" "$q3_out"
{ head -n 16 "$q3_out"; printf '...\t\t\n'; tail -n 5 "$q3_out"; } > "$q3_out.view"
column -t -s "$(printf '\t')" "$q3_out.view" 2>/dev/null || cat "$q3_out.view"
rm -f "$q3_out.view"

banner "Tests"
"$PYTHON" -m pytest -v -p no:cacheprovider

banner "Done in $((SECONDS - START))s"
