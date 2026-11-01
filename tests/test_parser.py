"""Parser tests against the real export and small hostile inputs."""

import pytest

from conftest import FIXTURE_XML
from pwc2dbt.parser import ParseError, parse_file


@pytest.fixture(scope="module")
def folder():
    return parse_file(FIXTURE_XML)


def _mapping(folder, name):
    return next(m for m in folder.mappings if m.name == name)


def test_counts(folder):
    assert folder.name == "FLOWLINE_DEMO_JAFFLESHOP"
    assert len(folder.mappings) == 3
    assert len(folder.sources) == 12
    assert len(folder.targets) == 16
    assert sum(len(m.connectors) for m in folder.mappings) == 1066
    assert folder.sources["RAW_ORDERS"].owner == "RAW"
    assert [f.name for f in folder.targets["STG_CUSTOMERS"].fields] == ["CUSTOMER_ID", "CUSTOMER_NAME"]


def test_ports_groups_and_attributes(folder):
    staging = _mapping(folder, "m_FL_JS_STAGING_FULL")
    products = staging.transformations["EXP_PRODUCTS"]
    food = products.port("is_food_item")  # lookups are case-insensitive
    assert food.porttype == "OUTPUT" and food.datatype == "integer"
    assert food.expression == "IIF( UPPER(LTRIM(RTRIM(TYPE))) = 'JAFFLE', 1, 0 )"
    joiner = staging.transformations["JNR_SUPPLIES_PRODUCTS"]
    assert joiner.port("SKU1").is_master and not joiner.port("SKU").is_master
    assert joiner.attribute("Join Condition") == "SKU1 = SKU"
    router = staging.transformations["RTR_REGION"]
    assert router.port("LOCATION_ID3").group == "G_AMER" and router.port("LOCATION_ID3").ref_field == "LOCATION_ID"
    assert {g.name: g.type for g in router.groups}["DEFAULT1"] == "OUTPUT/DEFAULT"
    assert "\r\n" in next(g.expression for g in router.groups if g.name == "G_APAC")
    assert len(staging.transformations["UN_REGIONS"].field_dependencies) == 15
    assert staging.transformations["EXP_LOCATIONS"].port("TAX_RATE").default_value == "ERROR('Transformation Error')"


def test_instances_resolve_to_definitions(folder):
    staging = _mapping(folder, "m_FL_JS_STAGING_FULL")
    assert staging.instances["RAW_PRODUCTS1"].definition == "RAW_PRODUCTS"
    assert staging.instances["RAW_PRODUCTS1"].type == "SOURCE"
    assert staging.instances["SQ_RAW_PRODUCTS1"].associated_sources == ("RAW_PRODUCTS1",)
    enriched = _mapping(folder, "m_FL_JS_MARTS_ENRICHED")
    assert enriched.instances["ORDER_FINANCIALS1"].definition == "ORDER_FINANCIALS"


def test_transformations_are_scoped_per_mapping(folder):
    core = _mapping(folder, "m_FL_JS_MARTS_CORE").transformations["JNR_ADD_PRODUCTS"]
    enriched = _mapping(folder, "m_FL_JS_MARTS_ENRICHED").transformations["JNR_ADD_PRODUCTS"]
    assert core.attribute("Join Condition") == "PRODUCT_ID1 = PRODUCT_ID"
    assert enriched.attribute("Join Condition") == "PRODUCT_ID = PRODUCT_ID1"


def test_only_mapping_parameters_are_read(folder):
    assert set(_mapping(folder, "m_FL_JS_MARTS_ENRICHED").parameters) == {"$$RUN_DATE"}
    assert _mapping(folder, "m_FL_JS_MARTS_CORE").parameters == {}


def test_windows_1252_is_decoded(tmp_path):
    path = tmp_path / "x.xml"
    path.write_bytes(
        '<?xml version="1.0" encoding="Windows-1252"?><POWERMART><REPOSITORY><FOLDER NAME="F">'
        '<MAPPING NAME="m"><TRANSFORMATION NAME="E" TYPE="Expression">'
        '<TRANSFORMFIELD NAME="P" PORTTYPE="OUTPUT" EXPRESSION="\'Caf\xe9\'"/>'
        "</TRANSFORMATION></MAPPING></FOLDER></REPOSITORY></POWERMART>".encode("cp1252")
    )
    assert parse_file(path).mappings[0].transformations["E"].port("P").expression == "'Café'"


def test_external_entities_are_never_resolved(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET")
    path = tmp_path / "xxe.xml"
    path.write_text(
        f'<?xml version="1.0"?><!DOCTYPE POWERMART [<!ENTITY x SYSTEM "file://{secret}">]>'
        '<POWERMART><REPOSITORY><FOLDER NAME="F"><SOURCE NAME="S" OWNERNAME="O">'
        "<SOURCEFIELD NAME=\"A\" DESCRIPTION=\"\"/>&x;</SOURCE></FOLDER></REPOSITORY></POWERMART>"
    )
    try:
        folder = parse_file(path)
    except ParseError:
        return  # refusing the document is also safe
    assert "TOP-SECRET" not in repr(folder)


@pytest.mark.parametrize("content", [
    "<POWERMART><REPOSITORY>",
    "<POWERMART><REPOSITORY/></POWERMART>",
    "<POWERMART><REPOSITORY><FOLDER NAME='A'/><FOLDER NAME='B'/></REPOSITORY></POWERMART>",
])
def test_unreadable_or_ambiguous_exports_are_rejected(tmp_path, content):
    path = tmp_path / "bad.xml"
    path.write_text(content)
    with pytest.raises(ParseError):
        parse_file(path)


def test_missing_file_is_a_parse_error(tmp_path):
    with pytest.raises(ParseError, match="cannot read"):
        parse_file(tmp_path / "missing.xml")
