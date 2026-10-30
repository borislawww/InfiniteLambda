"""Graph validation and topology: pipelines, branching, shared upstreams, cycles and bad references."""

from conftest import MappingBuilder, compile_xml, port, run_model
from pwc2dbt.sql import render_model

STR = "varchar2:100:0"


def _errors(diagnostics) -> list[str]:
    return [str(d) for d in diagnostics if d.severity == "ERROR"]


def test_linear_pipeline(tmp_path, con):
    b = MappingBuilder().source("RAW_A", {"K": "varchar2"})
    b.transformation("EXP", "Expression", [port("K", "INPUT"), port("K_UP", "OUTPUT", expr="UPPER(K)")])
    b.connect("SQ_RAW_A", "EXP", ["K"]).target("T", {"K": STR}).connect("EXP", "T", {"K_UP": "K"})
    models, diagnostics = compile_xml(tmp_path, b.xml())
    assert diagnostics == []
    con.execute("create schema raw; create table raw.raw_a (k varchar); insert into raw.raw_a values ('x')")
    assert run_model(con, models["t"]) == [{"k": "X"}]
    assert [c.name for c in models["t"].ctes] == ["sq_raw_a", "exp"]


def test_shared_upstream_is_one_cte_and_feeds_both_sides_of_a_self_join(tmp_path, con):
    b = MappingBuilder().source("RAW_A", {"K": "varchar2", "V": "varchar2"})
    b.transformation("JNR", "Joiner", [port("K"), port("V"), port("K1", "INPUT/MASTER"),
                                       port("V1", "INPUT/OUTPUT/MASTER")],
                     {"Join Type": "Normal Join", "Join Condition": "K = K1"})
    b.connect("SQ_RAW_A", "JNR", ["K", "V"]).connect("SQ_RAW_A", "JNR", {"K": "K1", "V": "V1"})
    b.target("T", {"K": STR, "V": STR, "V1": STR}).connect("JNR", "T", ["K", "V", "V1"])
    models, _ = compile_xml(tmp_path, b.xml())
    sql = render_model(models["t"])
    assert sql.count("sq_raw_a as (") == 1
    con.execute("create schema raw; create table raw.raw_a (k varchar, v varchar);"
                "insert into raw.raw_a values ('1', 'a'), ('2', 'b')")
    assert run_model(con, models["t"], order_by="k") == [{"k": "1", "v": "a", "v1": "a"}, {"k": "2", "v": "b", "v1": "b"}]


def test_chained_joiners_and_two_instances_of_one_source(tmp_path, con):
    b = MappingBuilder().source("RAW_A", {"K": "varchar2", "V": "varchar2"})
    b.source("RAW_A", {"K": "varchar2", "V": "varchar2"}, instance="RAW_A1")
    b.source("RAW_B", {"K": "varchar2", "W": "varchar2"})
    b.transformation("JNR1", "Joiner", [port("K"), port("V"), port("K1", "INPUT/MASTER"), port("W", "INPUT/OUTPUT/MASTER")],
                     {"Join Type": "Normal Join", "Join Condition": "K = K1"})
    b.connect("SQ_RAW_A", "JNR1", ["K", "V"]).connect("SQ_RAW_B", "JNR1", {"K": "K1", "W": "W"})
    b.transformation("JNR2", "Joiner", [port("K"), port("V"), port("W"), port("K1", "INPUT/MASTER"),
                                        port("V1", "INPUT/OUTPUT/MASTER")],
                     {"Join Type": "Master Outer Join", "Join Condition": "K1 = K"})
    b.connect("JNR1", "JNR2", ["K", "V", "W"]).connect("SQ_RAW_A1", "JNR2", {"K": "K1", "V": "V1"})
    b.target("T", {"K": STR, "W": STR, "V1": STR}).connect("JNR2", "T", ["K", "W", "V1"])
    models, _ = compile_xml(tmp_path, b.xml())
    sql = render_model(models["t"])
    assert "{{ source('raw', 'raw_a') }} as raw_a1" in sql and "{{ source('raw', 'raw_a') }} as raw_a\n" in sql
    con.execute("create schema raw; create table raw.raw_a (k varchar, v varchar); insert into raw.raw_a values ('1', 'a');"
                "create table raw.raw_b (k varchar, w varchar); insert into raw.raw_b values ('1', 'w')")
    assert run_model(con, models["t"]) == [{"k": "1", "w": "w", "v1": "a"}]


def test_multiple_targets_and_cross_mapping_refs(tmp_path, con):
    staging = MappingBuilder("m_STAGE").source("RAW_A", {"K": "varchar2"})
    staging.target("STG_A", {"K": STR}).connect("SQ_RAW_A", "STG_A", ["K"])
    staging.target("STG_A_COPY", {"K": STR}).connect("SQ_RAW_A", "STG_A_COPY", ["K"])
    mart = MappingBuilder("m_MART").source("STG_A", {"K": "varchar2"}, owner="STG")
    mart.target("MART_A", {"K": STR}, ).connect("SQ_STG_A", "MART_A", ["K"])
    models, _ = compile_xml(tmp_path, staging.xml(others=[mart]))
    assert set(models) == {"stg_a", "stg_a_copy", "mart_a"}
    assert models["mart_a"].refs == ("stg_a",) and models["mart_a"].sources == ()
    assert models["stg_a"].sources == (("raw", "raw_a"),)
    con.execute("create schema raw; create table raw.raw_a (k varchar); insert into raw.raw_a values ('x')")
    run_model(con, models["stg_a"])
    assert run_model(con, models["mart_a"]) == [{"k": "x"}]


def test_cycle_is_an_error(tmp_path):
    b = MappingBuilder().source("RAW_A", {"K": "varchar2"})
    b.transformation("EXP1", "Expression", [port("K"), port("J", "INPUT")])
    b.transformation("EXP2", "Expression", [port("K")])
    b.connect("SQ_RAW_A", "EXP1", ["K"]).connect("EXP1", "EXP2", ["K"]).connect("EXP2", "EXP1", {"K": "J"})
    b.target("T", {"K": STR}).connect("EXP2", "T", ["K"])
    models, diagnostics = compile_xml(tmp_path, b.xml())
    assert any("cycle" in e and "EXP1 -> EXP2 -> EXP1" in e for e in _errors(diagnostics))
    assert models == {}


def test_bad_references_fan_in_and_direction(tmp_path):
    b = MappingBuilder().source("RAW_A", {"K": "varchar2", "V": "varchar2"})
    b.transformation("EXP", "Expression", [port("K", "INPUT"), port("OUT", "OUTPUT", expr="K")])
    b.connect("SQ_RAW_A", "EXP", ["K"]).connect("SQ_RAW_A", "EXP", {"V": "K"})  # fan-in
    b.connect("SQ_RAW_A", "NOPE", ["K"])  # unknown instance
    b.connect("SQ_RAW_A", "EXP", {"MISSING": "K"})  # unknown port
    b.connect("EXP", "SQ_RAW_A", {"K": "K"})  # from an input-only port
    b.target("T", {"K": STR}).connect("EXP", "T", {"OUT": "K"})
    _, diagnostics = compile_xml(tmp_path, b.xml())
    errors = "\n".join(_errors(diagnostics))
    assert "m_TEST/EXP.K: input port is fed by more than one connector" in errors
    assert "m_TEST/NOPE.K: connector to unknown instance" in errors
    assert "m_TEST/SQ_RAW_A.MISSING: connector from unknown port" in errors
    assert "m_TEST/EXP.K: connector from a port that is not an output port" in errors


def test_unresolved_definition_and_unsafe_names(tmp_path):
    b = MappingBuilder().source("RAW_A", {"K": "varchar2"})
    b.target("T", {"K": STR}).connect("SQ_RAW_A", "T", ["K"])
    b.target("GHOST", {}, define=False)
    b.transformation("EXP 1", "Expression", [port("K")])
    _, diagnostics = compile_xml(tmp_path, b.xml())
    errors = "\n".join(_errors(diagnostics))
    assert "target definition 'GHOST' not found" in errors
    assert "instance name 'EXP 1' is not a safe SQL identifier" in errors


def test_unsupported_type_only_affects_dependent_targets(tmp_path):
    b = MappingBuilder().source("RAW_A", {"K": "varchar2"})
    b.transformation("LKP", "Lookup Procedure", [port("K")]).connect("SQ_RAW_A", "LKP", ["K"])
    b.target("T_LOOKUP", {"K": STR}).connect("LKP", "T_LOOKUP", ["K"])
    b.target("T_PLAIN", {"K": STR}).connect("SQ_RAW_A", "T_PLAIN", ["K"])
    models, diagnostics = compile_xml(tmp_path, b.xml())
    assert [d.severity for d in diagnostics] == ["WARNING"]
    assert models["t_plain"].status == "COMPLETE"
    assert models["t_lookup"].status == "INCOMPLETE"
    assert "Lookup Procedure" in str(models["t_lookup"].diagnostics[0])


def test_incompleteness_and_failed_mappings_propagate_to_dependants(tmp_path):
    broken = MappingBuilder("m_BROKEN").source("RAW_A", {"K": "varchar2"})
    broken.target("STG_BROKEN", {"K": STR}).connect("SQ_RAW_A", "STG_BROKEN", ["K"]).connect("SQ_RAW_A", "NOPE", ["K"])
    partial = MappingBuilder("m_PARTIAL").source("RAW_A", {"K": "varchar2"})
    partial.transformation("LKP", "Lookup Procedure", [port("K")]).connect("SQ_RAW_A", "LKP", ["K"])
    partial.target("STG_PARTIAL", {"K": STR}).connect("LKP", "STG_PARTIAL", ["K"])
    mart = MappingBuilder("m_MART").source("STG_BROKEN", {"K": "varchar2"}, owner="STG")
    mart.source("STG_PARTIAL", {"K": "varchar2"}, owner="STG")
    mart.target("FROM_BROKEN", {"K": STR}).connect("SQ_STG_BROKEN", "FROM_BROKEN", ["K"])
    mart.target("FROM_PARTIAL", {"K": STR}).connect("SQ_STG_PARTIAL", "FROM_PARTIAL", ["K"])
    models, diagnostics = compile_xml(tmp_path, broken.xml(others=[partial, mart]))
    assert _errors(diagnostics)
    assert "stg_broken" not in models
    for name, upstream in (("from_broken", "stg_broken"), ("from_partial", "stg_partial")):
        assert models[name].status == "INCOMPLETE"
        assert f"upstream model {upstream} is incomplete or failed" in str(models[name].diagnostics[-1])


def test_a_target_loaded_by_two_mappings_is_an_error_not_an_overwrite(tmp_path):
    first = MappingBuilder("m_FIRST").source("RAW_A", {"K": "varchar2"})
    first.target("T", {"K": STR}).connect("SQ_RAW_A", "T", ["K"])
    second = MappingBuilder("m_SECOND").source("RAW_A", {"K": "varchar2"})
    second.target("T", {"K": STR}, define=False).connect("SQ_RAW_A", "T", ["K"])
    models, diagnostics = compile_xml(tmp_path, first.xml(others=[second]))
    assert models["t"].mapping == "m_FIRST"
    assert any("also loaded by mapping m_FIRST" in e for e in _errors(diagnostics))
