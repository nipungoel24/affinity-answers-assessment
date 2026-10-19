import re
import shutil
import subprocess

import pytest
from conftest import ROOT

SCRIPT = ROOT / "q3_unix_shell" / "parse_companies.sh"
SAMPLE = ROOT / "q3_unix_shell" / "sample_output.tsv"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

EDGE_CASES = (
    "Symbol,Security,GICS Sector,GICS Sub-Industry,Headquarters Location,Date added,CIK,Founded\r\n"
    'MMM,3M,Industrials,Conglomerates,"Saint Paul, Minnesota",1957-03-04,66740,1902\r\n'
    'BRK,"Berkshire Hathaway, Inc.",Financials,Insurance,"Omaha, Nebraska",2010-02-16,1067983,1839\r\n'
    'Q,"The ""Quoted"" Co",Tech,Software,"Austin,\nTexas",2020-01-01,1,2013 (1888)\r\n'
    "NOY,No Year Corp,Tech,Software,Nowhere,2020-01-01,2,\r\n"
    'OLD,Old Co,Utilities,Water,"Boston, Massachusetts",2000-01-01,3,1784\r\n'
)


def run_script(*args):
    proc = subprocess.run(
        [BASH, SCRIPT.as_posix(), *args], capture_output=True, text=True, encoding="utf-8", check=True
    )
    return [line.split("\t") for line in proc.stdout.splitlines()]


@pytest.fixture
def edge_rows(tmp_path):
    csv_path = tmp_path / "companies.csv"
    csv_path.write_bytes(EDGE_CASES.encode("utf-8"))
    return run_script(csv_path.as_posix())


def test_header(edge_rows):
    assert edge_rows[0] == ["Company", "Location", "Founded"]


def test_quoted_commas_do_not_shift_columns(edge_rows):
    assert all(len(r) == 3 for r in edge_rows)
    assert ["3M", "Saint Paul, Minnesota", "1902"] in edge_rows
    assert ["Berkshire Hathaway, Inc.", "Omaha, Nebraska", "1839"] in edge_rows


def test_escaped_quotes_and_multiline_field(edge_rows):
    assert ['The "Quoted" Co', "Austin, Texas", "2013 (1888)"] in edge_rows


def test_sorted_by_leading_year_unknown_last(edge_rows):
    assert [r[0] for r in edge_rows[1:]] == [
        "Old Co",
        "Berkshire Hathaway, Inc.",
        "3M",
        "The \"Quoted\" Co",
        "No Year Corp",
    ]


def test_writes_output_file(tmp_path):
    csv_path = tmp_path / "c.csv"
    csv_path.write_bytes(EDGE_CASES.encode("utf-8"))
    out = tmp_path / "out.tsv"
    run_script(csv_path.as_posix(), out.as_posix())
    assert out.read_text(encoding="utf-8").startswith("Company\tLocation\tFounded\n")


def test_committed_sample_output_is_sorted():
    lines = SAMPLE.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "Company\tLocation\tFounded"
    rows = [line.split("\t") for line in lines[1:]]
    assert len(rows) > 490 and all(len(r) == 3 for r in rows)
    years = [int(m[0]) if (m := re.search(r"\d{4}", r[2])) else 9999 for r in rows]
    assert years == sorted(years)


@pytest.mark.live
def test_live_url_with_tracking_param():
    url = (
        "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/refs/heads/main/"
        "data/constituents.csv?utm_source=newsletter"
    )
    rows = run_script(url)
    assert len(rows) > 490 and ["3M", "Saint Paul, Minnesota", "1902"] in rows
