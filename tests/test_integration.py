"""End-to-end tests against the real PowerCenter export."""

import re
from pathlib import Path

from conftest import FIXTURE_XML, materialize_models, query
from pwc2dbt.cli import main
from pwc2dbt.parser import parse_file

REPO_ROOT = Path(__file__).resolve().parents[1]

RAW_DATA = """
create schema raw;
create table raw.raw_customers (id varchar, name varchar);
insert into raw.raw_customers values ('c1', 'Alice'), ('c2', 'Bob');

create table raw.raw_stores (id varchar, name varchar, opened_at timestamp, tax_rate decimal(6, 4));
insert into raw.raw_stores values
    ('s1', 'Philadelphia', '2024-01-01 10:30:00', 0.06),
    ('s2', 'Berlin', '2024-02-01 09:00:00', 0.20),
    ('s3', 'Tokyo', '2024-03-01 08:00:00', 0.10);

create table raw.raw_products (sku varchar, name varchar, type varchar, price bigint, description varchar);
insert into raw.raw_products values
    ('p1', 'Nutella jaffle', 'jaffle', 700, 'sweet'),
    ('p2', 'Cola', 'beverage', 300, 'fizzy');

create table raw.raw_supplies (id varchar, name varchar, cost bigint, perishable varchar, sku varchar);
insert into raw.raw_supplies values
    ('su1', 'bread', 50, 'False', 'p1'),
    ('su2', 'cup', 10, 'True', 'p2'),
    ('su3', 'orphan supply', 5, 'False', 'p_missing');

create table raw.raw_orders (
    id varchar, customer varchar, ordered_at timestamp, store_id varchar,
    subtotal bigint, tax_paid bigint, order_total bigint
);
insert into raw.raw_orders values
    ('o1', 'c1', '2024-09-01 12:34:00', 's1', 700, 42, 742),
    ('o2', 'c1', '2024-09-02 08:00:00', 's2', 300, 60, 360),
    ('o3', 'c2', '2024-09-03 18:00:00', 's1', 1000, 60, 1060);

create table raw.raw_items (id varchar, order_id varchar, sku varchar);
insert into raw.raw_items values ('i1', 'o1', 'p1'), ('i2', 'o2', 'p2'), ('i3', 'o3', 'p1'), ('i4', 'o3', 'p2');
"""

TARGET_NAMES = {
    "stg_customers", "stg_locations", "stg_orders", "stg_order_items", "stg_products", "stg_supplies",
    "customers", "orders", "order_items", "products", "supplies",
    "customer_360", "order_financials", "order_item_enriched", "product_daily_performance", "store_daily_kpis",
}


def _convert(out: Path, *extra: str) -> int:
    return main(["--input", str(FIXTURE_XML), "--output", str(out), *extra])


def test_real_xml_end_to_end(tmp_path, con, capsys):
    out = tmp_path / "project"

    assert _convert(out, "--all-mappings") == 2

    printed = capsys.readouterr().out
    mappings = [m.name for m in parse_file(FIXTURE_XML).mappings]
    assert mappings == ["m_FL_JS_STAGING_FULL", "m_FL_JS_MARTS_ENRICHED", "m_FL_JS_MARTS_CORE"]
    for name in mappings:
        assert name in printed

    complete = {p.stem: p for p in (out / "models").rglob("*.sql")}
    incomplete = {p.stem: p for p in (out / "incomplete").glob("*.sql")}
    assert set(complete) | set(incomplete) == TARGET_NAMES
    assert set(incomplete) == {"orders", "order_financials"}
    assert len(complete) == 14
    for path in incomplete.values():
        header = path.read_text()
        for needle in ("EXP_CUSTOMER_ORDER_NUM", "v_ORDER_SEQUENCE", "CUSTOMER_ORDER_NUMBER"):
            assert needle in header, (path.name, needle)

    for name, path in complete.items():
        sql = path.read_text()
        if path.parent.name == "m_fl_js_staging_full":
            assert re.search(r"source\('raw', 'raw_\w+'\)", sql), name
        else:
            assert re.search(r"ref\('stg_\w+'\)", sql), name

    con.execute(RAW_DATA)
    built = materialize_models(con, out / "models")
    assert set(built) == set(complete)

    # Connector swap honoured: the target column SUBTOTAL_CENTS is fed by the dollars port.
    o1 = query(con, "select * from stg_orders where order_id = 'o1'")[0]
    assert o1["subtotal_cents"] == 7.0
    assert o1["subtotal"] == 700

    # Router plus Union: every store survives exactly once, region derived from the tax rate.
    locations = {r["location_id"]: r for r in query(con, "select * from stg_locations")}
    assert set(locations) == {"s1", "s2", "s3"}
    assert locations["s1"]["region"] == "AMER"
    assert locations["s2"]["region"] == "EMEA"
    assert locations["s3"]["region"] == "APAC"

    # Normal join drops the supply whose SKU is not a product; 'False' is not 'Y' or '1'.
    supplies = query(con, "select * from stg_supplies order by supply_id")
    assert [r["supply_id"] for r in supplies] == ["su1", "su2"]
    assert all(r["is_perishable"] == 0 for r in supplies)

    # CUSTOMER_360 follows the rotated wiring: LIFETIME_TAX_PAID is fed by the pretax sum.
    alice = query(con, "select * from customer_360 where customer_id = 'c1'")[0]
    assert alice["lifetime_tax_paid"] == 1000


def test_committed_output_matches_regeneration(tmp_path):
    out = tmp_path / "project"
    _convert(out, "--all-mappings")
    committed = REPO_ROOT / "output"
    generated = sorted(p.relative_to(out) for p in out.rglob("*") if p.is_file())
    assert generated == sorted(p.relative_to(committed) for p in committed.rglob("*") if p.is_file())
    for rel in generated:
        assert (out / rel).read_bytes() == (committed / rel).read_bytes(), rel


def test_cli_exit_codes(tmp_path, capsys):
    assert _convert(tmp_path / "staging", "--mapping", "m_FL_JS_STAGING_FULL") == 0
    assert _convert(tmp_path / "unknown", "--mapping", "m_DOES_NOT_EXIST") == 1
    assert "m_FL_JS_MARTS_CORE" in capsys.readouterr().err
    bad = tmp_path / "bad.xml"
    bad.write_text("<POWERMART><REPOSITORY>")
    assert main(["--input", str(bad), "--output", str(tmp_path / "bad"), "--all-mappings"]) == 1


def test_refuses_to_overwrite_without_flag(tmp_path):
    out = tmp_path / "project"
    out.mkdir()
    (out / "keep.txt").write_text("user file")
    assert _convert(out, "--all-mappings") == 1
    assert not (out / "models").exists()

    stale = out / "models" / "stale.sql"
    stale.parent.mkdir()
    stale.write_text("select 1")
    assert _convert(out, "--all-mappings", "--overwrite") == 2
    assert not stale.exists()
    assert (out / "keep.txt").read_text() == "user file"


def test_no_jaffle_names_hardcoded_in_converter():
    folder = parse_file(FIXTURE_XML)
    names = {"JAFFLE", folder.name}
    for mapping in folder.mappings:
        names.add(mapping.name)
        names.update(i.name for i in mapping.instances.values())
        for t in mapping.transformations.values():
            names.update(p.name for p in t.ports)
    names.update(folder.targets)
    names.update(folder.sources)
    # Single words such as NAME or TYPE are also PowerCenter XML attribute names.
    names = {n for n in names if "_" in n} | {"JAFFLE"}
    source = "\n".join(p.read_text() for p in (REPO_ROOT / "src" / "pwc2dbt").glob("*.py")).upper()
    leaked = sorted(n for n in names if re.search(rf"\b{re.escape(n.upper())}\b", source))
    assert leaked == []
