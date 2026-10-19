import re

import pytest

from q2_sql_rfam import run_queries as rq
from q2_sql_rfam.run_queries import Query, Result


@pytest.fixture(scope="module")
def queries():
    return {q.name: q for q in rq.load_queries()}


def test_all_query_blocks_parsed(queries):
    assert list(queries) == [
        "q2_1a_acacia_genus",
        "q2_1b_acacia_plants",
        "q2_1c_acacia_wildcard",
        "q2_2_longest_wheat",
        "q2_3_page9_significant",
        "q2_3_page9_all_regions",
    ]
    assert all(q.sql.rstrip().endswith(";") and q.sql.count(";") == 1 for q in queries.values())


def test_wheat_query_joins_rfamseq_and_taxonomy(queries):
    sql = queries["q2_2_longest_wheat"].sql
    assert re.search(r"JOIN taxonomy tx ON rf\.ncbi_id = tx\.ncbi_id", sql)
    assert "ORDER BY rf.length DESC" in sql


@pytest.mark.parametrize("name", ["q2_3_page9_significant", "q2_3_page9_all_regions"])
def test_pagination_is_page_9_of_15(queries, name):
    sql = queries[name].sql
    assert "LIMIT 15 OFFSET 120" in sql  # (9 - 1) * 15
    assert "HAVING MAX(rf.length) > 1000000" in sql
    assert "ORDER BY max_sequence_length DESC" in sql
    for col in ("f.rfam_acc", "f.rfam_id", "MAX(rf.length) AS max_sequence_length"):
        assert col in sql


def test_significance_filter_only_on_significant_variant(queries):
    assert "is_significant = 1" in queries["q2_3_page9_significant"].sql
    assert "is_significant" not in queries["q2_3_page9_all_regions"].sql


def test_render_puts_answers_first(queries):
    results = {
        "q2_1a_acacia_genus": Result(queries["q2_1a_acacia_genus"], ["acacia_types"], [(326,)], 0.1),
        "q2_2_longest_wheat": Result(
            queries["q2_2_longest_wheat"],
            ["species", "ncbi_id", "rfamseq_acc", "length"],
            [("Triticum durum (durum wheat)", 4567, "LT934116.1", 836514780)],
            0.1,
        ),
    }
    md = rq.render(results)
    answers = md.split("## Answers", 1)[1].split("\n## ", 1)[0]
    assert "Q2.1 Acacia types: 326" in answers
    assert "Triticum durum (durum wheat)" in answers and "836,514,780 bp" in answers


def test_markdown_table_escapes_pipes():
    res = Result(Query("x", "t", "SELECT 1;"), ["a"], [("p|q",), ("r",)], 0.0)
    assert "p\\|q" in rq.to_markdown_table(res)


@pytest.mark.parametrize("name", ["q2_3_page9_significant", "q2_3_page9_all_regions"])
def test_recorded_page9_rows_load_from_results(name):
    stamp, header, rows = rq.load_recorded(name)
    assert stamp is not None
    assert header == ["#", "rfam_acc", "rfam_id", "description", "max_sequence_length"]
    assert len(rows) == 15 and all(r[1].startswith("RF") for r in rows)
    accs = [r[1] for r in rows]
    assert accs == sorted(accs)  # all tie on length, so rfam_acc order decides the page


@pytest.mark.live
def test_live_acacia_and_wheat(queries):
    with rq.connect() as conn:
        genus = rq.run(conn, queries["q2_1a_acacia_genus"]).rows[0][0]
        wildcard = rq.run(conn, queries["q2_1c_acacia_wildcard"]).rows[0][0]
        wheat = rq.run(conn, queries["q2_2_longest_wheat"]).rows
    assert 0 < genus <= wildcard
    assert wheat[0][0].startswith("Triticum")
    assert wheat[0][3] == max(r[3] for r in wheat)
