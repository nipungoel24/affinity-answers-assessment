#!/usr/bin/env bash
# run_demo.sh - end-to-end walkthrough of Q1, Q2, Q3 and the test suite (~30 s).
#
#   bash scripts/run_demo.sh
#   PYTHON=python3 bash scripts/run_demo.sh   # force an interpreter
#   run_demo.bat                              # from Windows cmd

set -euo pipefail

# Git Bash / MSYS2 keep curl etc. outside /usr/bin; make sure Q3's tools resolve from a bare PATH.
for d in /ucrt64/bin /mingw64/bin /usr/bin; do
    [[ -d "$d" && ":$PATH:" != *":$d:"* ]] && PATH="$PATH:$d"
done
export PATH

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Find an interpreter that has the project's dependencies. Works from a minimal PATH
# (MSYS2/UCRT64, cmd -> run_demo.bat) by also probing the python.org install locations.
has_deps() { "$@" -c "import curl_cffi, pymysql, bs4, rich, pytest" >/dev/null 2>&1; }
find_python() {
    local c
    for c in python3 python; do
        command -v "$c" >/dev/null 2>&1 && has_deps "$c" && { command -v "$c"; return; }
    done
    while IFS= read -r c; do
        has_deps "$c" && { echo "$c"; return; }
    done < <(ls -d "$HOME"/AppData/Local/Programs/Python/Python3*/python.exe \
                   "/c/Program Files"/Python3*/python.exe 2>/dev/null | sort -rV)
    command -v py >/dev/null 2>&1 && has_deps py -3 && { echo "py -3"; return; }
    return 1
}
if [[ -z "${PYTHON:-}" ]]; then
    PYTHON="$(find_python)" || {
        echo "[demo] no Python with the dependencies found; run: python -m pip install -r requirements.txt" >&2
        exit 1
    }
fi
read -r -a PY <<< "$PYTHON"   # "py -3" -> (py -3)
export PYTHONIOENCODING=utf-8
echo "[demo] using $("${PY[@]}" -c 'import sys; print(sys.executable)')"
FIXTURE="tests/fixtures/mdcomputers_search_external_harddrive.html"
START=$SECONDS

banner() { printf '\n\033[1;36m==== %s ====\033[0m\n' "$*"; }

banner "Q1  mdcomputers.in scraper (curl_cffi -> httpx -> headless browser)"
"${PY[@]}" q1_python_scraper/scraper.py --search "external hard drive" --format table \
    || { echo "[demo] live fetch failed; parsing the saved page instead"; \
         "${PY[@]}" q1_python_scraper/scraper.py --from-file "$FIXTURE" --format table; }

banner "Q2  Rfam public MySQL (mysql-rfam-public.ebi.ac.uk:4497)"
"${PY[@]}" q2_sql_rfam/run_queries.py

banner "Q3  S&P 500 companies sorted by founding year (awk RFC 4180 parser)"
q3_out="$(mktemp)"
trap 'rm -f "$q3_out"' EXIT
bash q3_unix_shell/parse_companies.sh "" "$q3_out"
{ head -n 16 "$q3_out"; printf '...\t\t\n'; tail -n 5 "$q3_out"; } > "$q3_out.view"
column -t -s "$(printf '\t')" "$q3_out.view" 2>/dev/null || cat "$q3_out.view"
rm -f "$q3_out.view"

banner "Tests"
"${PY[@]}" -m pytest -v -p no:cacheprovider

banner "Done in $((SECONDS - START))s"
