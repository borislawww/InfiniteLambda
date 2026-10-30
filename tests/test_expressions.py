"""Expression lexer, parser and translator; semantic cases are executed in DuckDB."""

import re

import pytest

from pwc2dbt import expressions as ex

COLUMNS = {"A": "number", "B": "number", "C": "number", "S": "string", "D1": "date", "D2": "date", "N": "number"}
ROW = "select 10 as a, 4 as b, 3 as c, '  jaffle ' as s, timestamp '2024-01-02 12:00:00' as d1, " \
      "timestamp '2024-01-01 00:00:00' as d2, cast(null as integer) as n"


def _ctx(warnings: list | None = None, aggregates: bool = False) -> ex.Context:
    def resolve(name: str) -> tuple[str, str]:
        if name.upper() not in COLUMNS:
            raise ex.Unsupported(f"unknown port {name}")
        return f"t.{name.lower()}", COLUMNS[name.upper()]

    def param(name: str) -> tuple[str, str]:
        raise ex.Unsupported(f"unknown parameter {name}")

    sink = warnings if warnings is not None else []
    return ex.Context(resolve, param, allow_aggregates=aggregates, warn=sink.append)


def evaluate(con, text: str):
    sql = ex.translate(ex.parse(text), _ctx())
    return con.execute(f"select {sql} from ({ROW}) as t").fetchone()[0]


@pytest.mark.parametrize(("text", "expected"), [
    ("A + B * C", 22),
    ("(A + B) * C", 42),
    ("A - (B - C)", 9),
    ("A - B - C", 3),
    ("-A + B", -6),
    ("A / B", 2.5),  # PowerCenter decimals are doubles with high precision off; DuckDB / is double
    ("IIF(A > B, 'big', 'small')", "big"),
    ("IIF(A < B, 1, IIF(B < C, 2, 3))", 3),
    ("IIF(NOT A = B AND B = 4 OR C = 0, 1, 0)", 1),
    ("IIF(NOT (A = 10 AND B = 5), 1, 0)", 1),
    ("UPPER(LTRIM(RTRIM(S)))", "JAFFLE"),
    ("IIF(\r\n  A >= 10,\r\n  1,\r\n  0)", 1),
])
def test_translated_expressions_evaluate_like_powercenter(con, text, expected):
    assert evaluate(con, text) == expected


def test_null_handling(con):
    assert evaluate(con, "IIF(N > 1, 'yes', 'no')") == "no"  # a NULL condition takes the else branch
    assert evaluate(con, "IIF(ISNULL(N), 0, N)") == 0
    assert evaluate(con, "N + 1") is None


def test_date_functions(con):
    assert evaluate(con, "DATE_DIFF(D1, D2, 'DD')") == 1.5  # fractional days, d1 - d2
    assert evaluate(con, "DATE_DIFF(D2, D1, 'DD')") == -1.5
    assert str(evaluate(con, "TRUNC(D1)")) == "2024-01-02 00:00:00"
    assert evaluate(con, "TO_CHAR(D1, 'YYYY/MM/DD HH24:MI:SS')") == "2024/01/02 12:00:00"
    warnings: list[str] = []
    sql = ex.translate(ex.parse("TO_DATE(TO_CHAR(D1, 'DD-MM-YY'), 'DD-MM-YY')"), _ctx(warnings))
    assert str(con.execute(f"select {sql} from ({ROW}) as t").fetchone()[0]) == "2024-01-02 00:00:00"
    assert len(warnings) == 1 and "2-digit year" in warnings[0]


@pytest.mark.parametrize(("text", "reason"), [
    ("DECODE(A, 1, 'one', 'other')", "function DECODE"),
    ("SUM(A)", "outside an Aggregator"),
    ("IIF(A > 1, 'x')", "IIF with 2 arguments"),
    ("IIF(A, 1, 0)", "IIF condition"),
    ("IIF(A > 1, 'x', 1)", "different types"),
    ("S = 1", "implicit conversion"),
    ("TRUNC(S)", "TRUNC needs date"),
    ("TO_CHAR(A, '999')", "TO_CHAR needs date"),
    ("TO_CHAR(D1, 'MON-DD')", "format element"),
    ("TO_DATE(S)", "explicit format"),
    ("DATE_DIFF(D1, D2, 'HH')", "day format"),
    ("LTRIM(S, 'x')", "LTRIM with 2 arguments"),
    ("'{{ var(\"x\") }}'", "Jinja"),
    ("A -- comment", "comments"),
    ("A = B = C", "chained comparisons"),
    ("UNKNOWN_PORT + 1", "unknown port"),
    ("$$NOT_A_PARAM", "unknown parameter"),
    ("(A + 1", "expected ')'"),
    ("A +", "unexpected end"),
])
def test_unsupported_constructs_are_rejected(text, reason):
    with pytest.raises(ex.Unsupported, match=re.escape(reason)):
        ex.translate(ex.parse(text), _ctx())


def test_aggregates_inside_aggregator_only_and_never_nested():
    assert ex.translate(ex.parse("COUNT(*)"), _ctx(aggregates=True)) == "count(*)"
    assert ex.translate(ex.parse("SUM(IIF(A > 1, A, 0))"), _ctx(aggregates=True)) == \
        "sum(case when t.a > 1 then t.a else 0 end)"
    with pytest.raises(ex.Unsupported, match="nested aggregate"):
        ex.translate(ex.parse("SUM(MAX(A))"), _ctx(aggregates=True))


def test_condition_must_be_boolean():
    assert ex.translate_condition(ex.parse("NOT ISNULL(A)"), _ctx()) == "not (t.a is null)"
    with pytest.raises(ex.Unsupported, match="boolean"):
        ex.translate_condition(ex.parse("A + 1"), _ctx())


def test_references_can_skip_aggregate_arguments():
    ast = ex.parse("G + SUM(V) + MAX(W)")
    assert ex.references(ast) == ["G", "V", "W"]
    assert ex.references(ast, outside_aggregates=True) == ["G"]
