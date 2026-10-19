#!/usr/bin/env bash
# parse_companies.sh - list S&P 500 companies (name, HQ location, founding year)
# sorted chronologically by founding year.
#
# Usage: parse_companies.sh [CSV_URL_OR_FILE] [OUTPUT_TSV]
#   CSV_URL_OR_FILE  defaults to the datasets/s-and-p-500-companies constituents.csv;
#                    a trailing ?utm_... tracking query is stripped; a local path is read directly.
#   OUTPUT_TSV       optional; defaults to stdout.
#
# Output: TSV with header "Company<TAB>Location<TAB>Founded".
#
# The CSV is RFC 4180: "Headquarters Location" ("Saint Paul, Minnesota") and some
# "Security" names contain commas inside double quotes, so naive `cut -d,` / `awk -F,`
# shifts columns. The awk below is a quote-aware state machine using only POSIX awk
# (no gawk FPAT), so it behaves the same under gawk, mawk and BSD awk.

set -euo pipefail

readonly DEFAULT_URL="https://raw.githubusercontent.com/datasets/s-and-p-500-companies/refs/heads/main/data/constituents.csv"

src="${1:-$DEFAULT_URL}"
out="${2:-}"
src="${src%%\?utm_*}"   # drop tracking parameters such as ?utm_source=newsletter

fetch() {
    if [[ -f "$src" ]]; then
        cat -- "$src"
    else
        curl -fsSL --retry 3 --retry-delay 2 --max-time 60 -- "$src"
    fi
}

parse() {
    awk '
    # Split one complete CSV record into F[1..n]; returns n.
    function split_csv(rec,    i, c, n, field, inq, len) {
        n = 0; field = ""; inq = 0; len = length(rec)
        for (i = 1; i <= len; i++) {
            c = substr(rec, i, 1)
            if (inq) {
                if (c == "\"") {
                    if (substr(rec, i + 1, 1) == "\"") { field = field "\""; i++ }  # escaped ""
                    else inq = 0
                } else field = field c
            } else if (c == "\"") inq = 1
            else if (c == ",") { F[++n] = field; field = "" }
            else field = field c
        }
        F[++n] = field
        return n
    }
    function clean(s) { gsub(/[\t\n]+/, " ", s); return s }

    {
        sub(/\r$/, "")
        buf = (pending ? buf "\n" $0 : $0)
        tmp = buf
        if (gsub(/"/, "", tmp) % 2) { pending = 1; next }   # quoted field spans lines
        pending = 0
        n = split_csv(buf)

        if (!header_done) {                     # locate columns by name, not position
            name_col = 2; loc_col = 5; year_col = 8
            for (i = 1; i <= n; i++) {
                if (F[i] == "Security") name_col = i
                else if (F[i] == "Headquarters Location") loc_col = i
                else if (F[i] == "Founded") year_col = i
            }
            header_done = 1
            next
        }

        founded = F[year_col]
        # Sort key = first 4-digit year, so "2013 (1888)" sorts as 2013. Unknown -> last.
        key = match(founded, /[0-9][0-9][0-9][0-9]/) ? substr(founded, RSTART, 4) : 9999
        printf "%s\t%s\t%s\t%s\n", key, clean(F[name_col]), clean(F[loc_col]), clean(founded)
    }'
}

main() {
    local result
    result="$(
        printf 'Company\tLocation\tFounded\n'
        fetch | parse | LC_ALL=C sort -t "$(printf '\t')" -k1,1n -k2,2 | cut -f2-
    )"
    if [[ -n "$out" ]]; then
        printf '%s\n' "$result" > "$out"
        echo "Wrote $(($(wc -l < "$out") - 1)) companies to $out" >&2
    else
        printf '%s\n' "$result"
    fi
}

main
