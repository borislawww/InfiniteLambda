"""One isolated fixture per transformation type; every assertion is on rows DuckDB produced."""

from decimal import Decimal

import pytest

from conftest import MappingBuilder, compile_xml, port, run_model
from pwc2dbt.sql import render_model

STR = "varchar2:100:0"


def _incomplete_messages(result) -> list[str]:
    assert result.status == "INCOMPLETE"
    return [str(d) for d in result.diagnostics if d.severity == "INCOMPLETE"]


# ---------------------------------------------------------------- Source Qualifier


def test_source_qualifier_casts_to_port_types(tmp_path, con):
    b = MappingBuilder().source("RAW_A", {"K": "varchar2", "AMT": "number"})
    b.target("T", {"K": STR, "AMT": "number"}).connect("SQ_RAW_A", "T", ["K", "AMT"])
    models, _ = compile_xml(tmp_path, b.xml())
    con.execute("create schema raw; create table raw.raw_a (k varchar, amt integer);"
                "insert into raw.raw_a values ('x', 7)")
    rows = run_model(con, models["t"])
    assert rows == [{"k": "x", "amt": 7.0}]
    assert "source('raw', 'raw_a')" in render_model(models["t"])


def test_select_distinct_uses_all_connected_ports_even_when_pruned(tmp_path, con):
    b = MappingBuilder().source("RAW_A", {"K": "varchar2", "V": "number"}, sq_attrs={"Select Distinct": "YES"})
    b.target("T", {"K": STR}).connect("SQ_RAW_A", "T", ["K"])
    models, _ = compile_xml(tmp_path, b.xml())
    con.execute("create schema raw; create table raw.raw_a (k varchar, v integer);"
                "insert into raw.raw_a values ('x', 1), ('x', 1), ('x', 2)")
    assert run_model(con, models["t"]) == [{"k": "x"}, {"k": "x"}]


def test_select_distinct_with_an_untranslatable_connected_port_is_incomplete(tmp_path):
    b = MappingBuilder().source("RAW_A", {"K": "varchar2", "V": "number"}, sq_attrs={"Select Distinct": "YES"})
    b.target("T", {"K": STR}).connect("SQ_RAW_A", "T", ["K"])
    xml = b.xml().replace('NAME="V" PORTTYPE="INPUT/OUTPUT" DATATYPE="double"', 'NAME="V" PORTTYPE="INPUT/OUTPUT" DATATYPE="binary"')
    models, _ = compile_xml(tmp_path, xml)
    # Dropping V from the distinct would silently return fewer rows.
    assert any("binary" in m for m in _incomplete_messages(models["t"]))


def test_source_filter_makes_the_model_incomplete(tmp_path):
    b = MappingBuilder().source("RAW_A", {"K": "varchar2"}, sq_attrs={"Source Filter": "RAW_A.K > 'a'"})
    b.target("T", {"K": STR}).connect("SQ_RAW_A", "T", ["K"])
    models, _ = compile_xml(tmp_path, b.xml())
    assert any("Source Filter" in m for m in _incomplete_messages(models["t"]))
    assert models["t"].ctes == ()


# ---------------------------------------------------------------- Expression


def _stateful_mapping() -> MappingBuilder:
    b = MappingBuilder().source("RAW_A", {"ID": "varchar2", "X": "number"})
    b.transformation("EXP_SEQ", "Expression", [
        port("ID", "INPUT/OUTPUT"),
        port("X", "INPUT", "double"),
        port("v_PREV_X", "LOCAL VARIABLE", "double", "X"),
        port("v_SEQ", "LOCAL VARIABLE", "double", "IIF(v_PREV_X = X, v_SEQ + 1, 1)"),
        port("v_DOUBLE", "LOCAL VARIABLE", "double", "X * 2"),
        port("v_PLUS", "LOCAL VARIABLE", "double", "v_DOUBLE + 1"),
        port("OUT_SEQ", "OUTPUT", "double", "v_SEQ"),
        port("OUT_CALC", "OUTPUT", "double", "v_PLUS"),
    ])
    b.connect("SQ_RAW_A", "EXP_SEQ", ["ID", "X"])
    b.target("T_DEAD", {"ID": STR, "CALC": "number"}).connect("EXP_SEQ", "T_DEAD", {"ID": "ID", "OUT_CALC": "CALC"})
    b.target("T_LIVE", {"ID": STR, "SEQ": "number"}).connect("EXP_SEQ", "T_LIVE", {"ID": "ID", "OUT_SEQ": "SEQ"})
    return b


def test_dead_stateful_port_is_not_incomplete(tmp_path, con):
    models, _ = compile_xml(tmp_path, _stateful_mapping().xml())
    con.execute("create schema raw; create table raw.raw_a (id varchar, x integer); insert into raw.raw_a values ('a', 3)")
    # Pure variables are inlined in order: v_PLUS = v_DOUBLE + 1 = X * 2 + 1.
    assert run_model(con, models["t_dead"]) == [{"id": "a", "calc": 7.0}]


def test_needed_stateful_port_is_incomplete(tmp_path):
    models, _ = compile_xml(tmp_path, _stateful_mapping().xml())
    messages = _incomplete_messages(models["t_live"])
    assert len(messages) == 1
    assert "EXP_SEQ.OUT_SEQ" in messages[0] and "v_SEQ is stateful" in messages[0] and "SEQ" in messages[0]
    # The column that could be translated is still rendered; no placeholder for the missing one.
    sql = render_model(models["t_live"])
    assert "as id" in sql and "as seq" not in sql


def test_reference_to_an_output_port_is_incomplete(tmp_path):
    b = MappingBuilder().source("RAW_A", {"X": "number"})
    b.transformation("EXP", "Expression", [
        port("X", "INPUT", "double"),
        port("A", "OUTPUT", "double", "X + 1"),
        port("B", "OUTPUT", "double", "A + 1"),
    ]).connect("SQ_RAW_A", "EXP", ["X"])
    b.target("T", {"B": "number"}).connect("EXP", "T", ["B"])
    models, _ = compile_xml(tmp_path, b.xml())
    assert any("output port A" in m for m in _incomplete_messages(models["t"]))


@pytest.mark.parametrize(("default", "severity"), [("ERROR('bad')", "WARNING"), ("0", "INCOMPLETE")])
def test_input_port_default_values(tmp_path, default, severity):
    b = MappingBuilder().source("RAW_A", {"X": "number"})
    b.transformation("EXP", "Expression", [
        port("X", "INPUT/OUTPUT", "double", DEFAULTVALUE=default),
    ]).connect("SQ_RAW_A", "EXP", ["X"])
    b.target("T", {"X": "number"}).connect("EXP", "T", ["X"])
    models, _ = compile_xml(tmp_path, b.xml())
    found = [d for d in models["t"].diagnostics if d.port == "X" and d.transformation == "EXP"]
    assert [d.severity for d in found] == [severity]


# ---------------------------------------------------------------- Filter and Sorter


def test_filter_drops_false_and_null_conditions(tmp_path, con):
    b = MappingBuilder().source("RAW_A", {"ID": "varchar2", "X": "number"})
    b.transformation("FIL", "Filter", [port("ID"), port("X", datatype="double")], {"Filter Condition": "X > 1"})
    b.connect("SQ_RAW_A", "FIL", ["ID", "X"])
    b.target("T", {"ID": STR}).connect("FIL", "T", ["ID"])
    models, _ = compile_xml(tmp_path, b.xml())
    con.execute("create schema raw; create table raw.raw_a (id varchar, x integer);"
                "insert into raw.raw_a values ('zero', 0), ('two', 2), ('null', null)")
    assert run_model(con, models["t"]) == [{"id": "two"}]


def test_sorter_is_transparent_and_adds_no_order_by(tmp_path, con):
    b = MappingBuilder().source("RAW_A", {"ID": "varchar2"})
    b.transformation("SRT", "Sorter", [port("ID", ISSORTKEY="YES", SORTDIRECTION="DESCENDING")], {"Distinct": "NO"})
    b.connect("SQ_RAW_A", "SRT", ["ID"])
    b.target("T", {"ID": STR}).connect("SRT", "T", ["ID"])
    models, _ = compile_xml(tmp_path, b.xml())
    con.execute("create schema raw; create table raw.raw_a (id varchar); insert into raw.raw_a values ('a'), ('b')")
    assert "order by" not in render_model(models["t"]).lower()
    assert sorted(r["id"] for r in run_model(con, models["t"])) == ["a", "b"]


def test_sorter_distinct_is_incomplete(tmp_path):
    b = MappingBuilder().source("RAW_A", {"ID": "varchar2"})
    b.transformation("SRT", "Sorter", [port("ID", ISSORTKEY="YES")], {"Distinct": "YES"})
    b.connect("SQ_RAW_A", "SRT", ["ID"])
    b.target("T", {"ID": STR}).connect("SRT", "T", ["ID"])
    models, _ = compile_xml(tmp_path, b.xml())
    assert any("Distinct" in m for m in _incomplete_messages(models["t"]))


# ---------------------------------------------------------------- Router and Union


def _router_union(deps_order: list[tuple[str, str]] | None = None) -> MappingBuilder:
    b = MappingBuilder().source("RAW_N", {"ID": "varchar2", "X": "number"})
    groups = [("INPUT", "INPUT", ""), ("G_HI", "OUTPUT", "X >= 10"), ("G_MID", "OUTPUT", "X >= 5\r\n"),
              ("DEFAULT1", "OUTPUT/DEFAULT", "")]
    ports = [port("ID", "INPUT", GROUP="INPUT"), port("X", "INPUT", "double", GROUP="INPUT")]
    for suffix, group in (("1", "G_HI"), ("2", "G_MID"), ("3", "DEFAULT1")):
        ports += [port(f"ID{suffix}", "OUTPUT", GROUP=group, REF_FIELD="ID"),
                  port(f"X{suffix}", "OUTPUT", "double", GROUP=group, REF_FIELD="X")]
    b.transformation("RTR", "Router", ports, groups=groups).connect("SQ_RAW_N", "RTR", ["ID", "X"])
    union_ports = [port("ID", "OUTPUT", GROUP="OUTPUT"), port("X", "OUTPUT", "double", GROUP="OUTPUT")]
    deps = []
    for suffix, group in (("1", "HI"), ("2", "MID"), ("3", "DEF")):
        union_ports += [port(f"ID_{group}", "INPUT", GROUP=group), port(f"X_{group}", "INPUT", "double", GROUP=group)]
        deps += [(f"ID_{group}", "ID"), (f"X_{group}", "X")]
        b.connect("RTR", "UN", {f"ID{suffix}": f"ID_{group}", f"X{suffix}": f"X_{group}"})
    b.transformation("UN", "Custom Transformation", union_ports, {"Module Identifier": "pmuniontrans"},
                     groups=[("OUTPUT", "OUTPUT", ""), ("HI", "INPUT", ""), ("MID", "INPUT", ""), ("DEF", "INPUT", "")],
                     deps=deps_order or deps)
    b.target("T", {"ID": STR, "X": "number"}).connect("UN", "T", ["ID", "X"])
    return b


def test_router_row_in_two_groups_and_default(tmp_path, con):
    models, _ = compile_xml(tmp_path, _router_union().xml())
    con.execute("create schema raw; create table raw.raw_n (id varchar, x integer);"
                "insert into raw.raw_n values ('a', 12), ('b', 7), ('c', 1), ('d', null)")
    rows = run_model(con, models["t"], order_by="id")
    # 'a' matches both user groups; a NULL condition is not TRUE, so 'd' goes to DEFAULT.
    assert [r["id"] for r in rows] == ["a", "a", "b", "c", "d"]


def test_union_follows_field_dependencies_not_port_order(tmp_path, con):
    b = MappingBuilder().source("RAW_A", {"K": "varchar2", "V": "varchar2"})
    b.source("RAW_B", {"K": "varchar2", "V": "varchar2"})
    # Input ports of group G_A are declared V first; position would swap the columns.
    b.transformation("UN", "Custom Transformation", [
        port("K", "OUTPUT", GROUP="OUTPUT"), port("V", "OUTPUT", GROUP="OUTPUT"),
        port("V_A", "INPUT", GROUP="G_A"), port("K_A", "INPUT", GROUP="G_A"),
        port("K_B", "INPUT", GROUP="G_B"), port("V_B", "INPUT", GROUP="G_B"),
    ], {"Module Identifier": "pmuniontrans"},
        groups=[("OUTPUT", "OUTPUT", ""), ("G_A", "INPUT", ""), ("G_B", "INPUT", "")],
        deps=[("K_A", "K"), ("V_A", "V"), ("K_B", "K"), ("V_B", "V")])
    b.connect("SQ_RAW_A", "UN", {"K": "K_A", "V": "V_A"}).connect("SQ_RAW_B", "UN", {"K": "K_B", "V": "V_B"})
    b.target("T", {"K": STR, "V": STR}).connect("UN", "T", ["K", "V"])
    models, _ = compile_xml(tmp_path, b.xml())
    con.execute("create schema raw; create table raw.raw_a (k varchar, v varchar); insert into raw.raw_a values ('k1', 'v1');"
                "create table raw.raw_b (k varchar, v varchar); insert into raw.raw_b values ('k2', 'v2')")
    assert run_model(con, models["t"], order_by="k") == [{"k": "k1", "v": "v1"}, {"k": "k2", "v": "v2"}]


def test_custom_transformation_other_than_union_is_incomplete(tmp_path):
    b = MappingBuilder().source("RAW_A", {"K": "varchar2"})
    b.transformation("CT", "Custom Transformation", [port("K", "INPUT/OUTPUT")], {"Module Identifier": "mymodule"})
    b.connect("SQ_RAW_A", "CT", ["K"]).target("T", {"K": STR}).connect("CT", "T", ["K"])
    models, _ = compile_xml(tmp_path, b.xml())
    assert any("mymodule" in m for m in _incomplete_messages(models["t"]))


# ---------------------------------------------------------------- Joiner

JOIN_DATA = """
create schema raw;
create table raw.raw_d (k varchar, v varchar);
insert into raw.raw_d values ('1', 'd1'), ('2', 'd2'), (null, 'dn');
create table raw.raw_m (mk varchar, w varchar);
insert into raw.raw_m values ('1', 'm1'), ('3', 'm3'), (null, 'mn');
"""


def _joiner(join_type: str, condition: str) -> MappingBuilder:
    b = MappingBuilder().source("RAW_D", {"K": "varchar2", "V": "varchar2"})
    # The master key column has its own name so a swapped side cannot bind by accident.
    b.source("RAW_M", {"MK": "varchar2", "W": "varchar2"})
    b.transformation("JNR", "Joiner", [
        port("K", "INPUT/OUTPUT"), port("V", "INPUT/OUTPUT"),
        port("K1", "INPUT/MASTER"), port("W", "INPUT/OUTPUT/MASTER"),
    ], {"Join Type": join_type, "Join Condition": condition, "Case Sensitive String Comparison": "YES"})
    b.connect("SQ_RAW_D", "JNR", ["K", "V"]).connect("SQ_RAW_M", "JNR", {"MK": "K1", "W": "W"})
    b.target("T", {"K": STR, "V": STR, "W": STR}).connect("JNR", "T", ["K", "V", "W"])
    return b


@pytest.mark.parametrize(("join_type", "expected"), [
    ("Normal Join", [("1", "d1", "m1")]),
    ("Master Outer Join", [("1", "d1", "m1"), ("2", "d2", None), (None, "dn", None)]),
    ("Detail Outer Join", [("1", "d1", "m1"), (None, None, "m3"), (None, None, "mn")]),
    ("Full Outer Join", [("1", "d1", "m1"), ("2", "d2", None), (None, "dn", None), (None, None, "m3"),
                         (None, None, "mn")]),
])
def test_join_types_and_null_keys_never_match(tmp_path, con, join_type, expected):
    models, _ = compile_xml(tmp_path, _joiner(join_type, "K1 = K").xml())
    con.execute(JOIN_DATA)
    rows = run_model(con, models["t"])
    key = lambda r: tuple("" if x is None else x for x in r)  # noqa: E731
    assert sorted(((r["k"], r["v"], r["w"]) for r in rows), key=key) == sorted(expected, key=key)


def test_master_outer_join_keeps_detail_rows_condition_either_side(tmp_path, con):
    con.execute(JOIN_DATA)
    results = []
    for condition in ("K1 = K", "K = K1"):
        models, _ = compile_xml(tmp_path, _joiner("Master Outer Join", condition).xml())
        results.append(sorted(str(r) for r in run_model(con, models["t"])))
    assert results[0] == results[1]
    assert len(results[0]) == 3  # every detail row, including the one with a NULL key


def test_join_condition_must_pair_master_and_detail(tmp_path):
    models, _ = compile_xml(tmp_path, _joiner("Normal Join", "K = V").xml())
    assert any("one master and one detail" in m for m in _incomplete_messages(models["t"]))


# ---------------------------------------------------------------- Aggregator


def _aggregator(extra: list[str] = ()) -> MappingBuilder:
    b = MappingBuilder().source("RAW_S", {"G": "varchar2", "V": "number"})
    b.transformation("AGG", "Aggregator", [
        port("G", "INPUT/OUTPUT", EXPRESSIONTYPE="GROUPBY"),
        port("V", "INPUT", "double"),
        port("N_ROWS", "OUTPUT", "double", "COUNT(*)"),
        port("N_V", "OUTPUT", "double", "COUNT(V)"),
        port("S", "OUTPUT", "double", "SUM(V)"),
        port("S_IIF", "OUTPUT", "double", "SUM(IIF(V > 1, 1, 0))"),
        *extra,
    ], {"Sorted Input": "NO"}).connect("SQ_RAW_S", "AGG", ["G", "V"])
    return b


def test_aggregator_count_star_count_port_and_null_sums(tmp_path, con):
    b = _aggregator()
    b.target("T", {"G": STR, "N_ROWS": "number", "N_V": "number", "S": "number", "S_IIF": "number"})
    b.connect("AGG", "T", ["G", "N_ROWS", "N_V", "S", "S_IIF"])
    models, _ = compile_xml(tmp_path, b.xml())
    con.execute("create schema raw; create table raw.raw_s (g varchar, v integer);"
                "insert into raw.raw_s values ('g1', 1), ('g1', 2), ('g1', null), ('g2', null)")
    assert run_model(con, models["t"], order_by="g") == [
        {"g": "g1", "n_rows": 3.0, "n_v": 2.0, "s": 3.0, "s_iif": 1.0},
        {"g": "g2", "n_rows": 1.0, "n_v": 0.0, "s": None, "s_iif": 0.0},
    ]


def test_aggregator_rejects_non_group_by_port_outside_an_aggregate(tmp_path):
    b = _aggregator([port("BAD", "OUTPUT", "double", "V + SUM(V)")])
    b.target("T", {"G": STR, "BAD": "number"}).connect("AGG", "T", ["G", "BAD"])
    models, _ = compile_xml(tmp_path, b.xml())
    assert any("V is used outside an aggregate" in m for m in _incomplete_messages(models["t"]))


# ---------------------------------------------------------------- Target


def test_target_casts_and_unconnected_column_is_null_with_warning(tmp_path, con):
    b = MappingBuilder().source("RAW_A", {"K": "varchar2", "AMT": "number"})
    b.target("T", {"K": STR, "AMT": "number(p,s):12:2", "EXTRA": STR}).connect("SQ_RAW_A", "T", ["K", "AMT"])
    models, _ = compile_xml(tmp_path, b.xml())
    con.execute("create schema raw; create table raw.raw_a (k varchar, amt double); insert into raw.raw_a values ('x', 1.239)")
    assert run_model(con, models["t"]) == [{"k": "x", "amt": Decimal("1.24"), "extra": None}]
    assert "-- not connected in the mapping" in render_model(models["t"])
    assert any(d.severity == "WARNING" and d.port == "EXTRA" for d in models["t"].diagnostics)
