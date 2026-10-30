"""Shared helpers: execute generated dbt models in DuckDB without dbt.

The generated SQL uses only three Jinja forms: ``source()``, ``ref()`` and
``var()`` with a default. Tests substitute them with plain relation names and
the default value so the SQL itself is executed and its rows asserted.
"""

import re
from pathlib import Path
from xml.sax.saxutils import quoteattr

import duckdb
import pytest

from pwc2dbt.compiler import compile_folder
from pwc2dbt.parser import parse_file
from pwc2dbt.sql import render_model

FIXTURE_XML = Path(__file__).parent / "fixtures" / "FLOWLINE_DEMO_JAFFLESHOP.xml"

_SOURCE = re.compile(r"\{\{\s*source\('(\w+)',\s*'(\w+)'\)\s*\}\}")
_REF = re.compile(r"\{\{\s*ref\('(\w+)'\)\s*\}\}")
_VAR = re.compile(r"""\{\{\s*var\(["']\w+["'],\s*["']([^"']*)["']\)\s*\}\}""")


def render_jinja(sql: str) -> str:
    sql = _SOURCE.sub(lambda m: f"{m.group(1)}.{m.group(2)}", sql)
    sql = _REF.sub(lambda m: f"main.{m.group(1)}", sql)
    sql = _VAR.sub(lambda m: m.group(1), sql)
    assert "{{" not in sql and "{%" not in sql, sql
    return sql


def materialize_models(con: duckdb.DuckDBPyConnection, models_dir: Path) -> list[str]:
    """Create one table per model under ``models_dir``, in ref order."""
    sqls = {p.stem: p.read_text() for p in sorted(models_dir.rglob("*.sql"))}
    built: list[str] = []

    def build(name: str) -> None:
        if name in built:
            return
        for dep in _REF.findall(sqls[name]):
            build(dep)
        con.execute(f"create table main.{name} as {render_jinja(sqls[name])}")
        built.append(name)

    for name in sqls:
        build(name)
    return built


def query(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict]:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


@pytest.fixture
def con():
    connection = duckdb.connect()
    yield connection
    connection.close()


# ---------------------------------------------------------------- tiny PowerCenter XML builder

_SQ_TYPE = {"varchar2": "string", "number": "double", "number(p,s)": "decimal", "timestamp": "date/time", "date": "date/time"}


def attr(value: str) -> str:
    return quoteattr(value)


def port(name: str, porttype: str = "INPUT/OUTPUT", datatype: str = "string", expr: str = "", **extra: str) -> str:
    """One TRANSFORMFIELD. ``extra`` keys are XML attribute names, e.g. GROUP, REF_FIELD, DEFAULTVALUE."""
    attrs = {"NAME": name, "PORTTYPE": porttype, "DATATYPE": datatype, "PRECISION": "15", "SCALE": "2",
             "EXPRESSION": expr, **extra}
    return "<TRANSFORMFIELD " + " ".join(f"{k}={attr(v)}" for k, v in attrs.items()) + "/>"


class MappingBuilder:
    """Builds a one-folder export. Sources get an instance and a Source Qualifier SQ_<name> automatically."""

    def __init__(self, name: str = "m_TEST"):
        self.name = name
        self.definitions: list[str] = []
        self.body: list[str] = []
        self.instances: list[str] = []
        self.connectors: list[str] = []
        self.sources: set[str] = set()

    def source(self, name: str, fields: dict[str, str], owner: str = "RAW", sq_attrs: dict | None = None,
               instance: str | None = None) -> "MappingBuilder":
        instance = instance or name
        if name not in self.sources:
            self.sources.add(name)
            self.definitions.append(
                f"<SOURCE NAME={attr(name)} OWNERNAME={attr(owner)} DBDNAME=\"DB\">"
                + "".join(f"<SOURCEFIELD NAME={attr(f)} DATATYPE={attr(t)} PRECISION=\"12\" SCALE=\"2\"/>"
                          for f, t in fields.items())
                + "</SOURCE>"
            )
        sq = f"SQ_{instance}"
        attrs = {"Sql Query": "", "User Defined Join": "", "Source Filter": "", "Select Distinct": "NO",
                 **(sq_attrs or {})}
        self.transformation(sq, "Source Qualifier", [port(f, datatype=_SQ_TYPE[t]) for f, t in fields.items()], attrs)
        self.instances.append(
            f"<INSTANCE NAME={attr(instance)} TRANSFORMATION_NAME={attr(name)} TRANSFORMATION_TYPE=\"Source Definition\" TYPE=\"SOURCE\"/>"
        )
        self.instances[-2] = self.instances[-2].replace(
            "/>", f"><ASSOCIATED_SOURCE_INSTANCE NAME={attr(instance)}/></INSTANCE>"
        )
        self.connect(instance, sq, list(fields))
        return self

    def transformation(self, name: str, type_: str, ports: list[str], attrs: dict | None = None,
                       groups: list[tuple[str, str, str]] = (), deps: list[tuple[str, str]] = ()) -> "MappingBuilder":
        self.body.append(
            f"<TRANSFORMATION NAME={attr(name)} TYPE={attr(type_)} REUSABLE=\"NO\">"
            + "".join(f"<GROUP NAME={attr(g)} TYPE={attr(t)} ORDER=\"{i + 1}\" EXPRESSION={attr(e)}/>"
                      for i, (g, t, e) in enumerate(groups))
            + "".join(ports)
            + "".join(f"<TABLEATTRIBUTE NAME={attr(k)} VALUE={attr(v)}/>" for k, v in (attrs or {}).items())
            + "".join(f"<FIELDDEPENDENCY INPUTFIELD={attr(i)} OUTPUTFIELD={attr(o)}/>" for i, o in deps)
            + "</TRANSFORMATION>"
        )
        self.instances.append(
            f"<INSTANCE NAME={attr(name)} TRANSFORMATION_NAME={attr(name)} TRANSFORMATION_TYPE={attr(type_)} TYPE=\"TRANSFORMATION\"/>"
        )
        return self

    def target(self, name: str, fields: dict[str, str], instance: str | None = None,
               define: bool = True) -> "MappingBuilder":
        if define:
            self.definitions.append(
                f"<TARGET NAME={attr(name)}>"
                + "".join(f"<TARGETFIELD NAME={attr(f)} DATATYPE={attr(t.split(':')[0])} "
                          f"PRECISION={attr(t.split(':')[1] if ':' in t else '12')} "
                          f"SCALE={attr(t.split(':')[2] if t.count(':') == 2 else '2')}/>"
                          for f, t in fields.items())
                + "</TARGET>"
            )
        self.instances.append(
            f"<INSTANCE NAME={attr(instance or name)} TRANSFORMATION_NAME={attr(name)} TRANSFORMATION_TYPE=\"Target Definition\" TYPE=\"TARGET\"/>"
        )
        return self

    def connect(self, from_instance: str, to_instance: str, fields: list[str] | dict[str, str]) -> "MappingBuilder":
        pairs = fields.items() if isinstance(fields, dict) else [(f, f) for f in fields]
        for a, b in pairs:
            self.connectors.append(
                f"<CONNECTOR FROMINSTANCE={attr(from_instance)} FROMFIELD={attr(a)} "
                f"TOINSTANCE={attr(to_instance)} TOFIELD={attr(b)}/>"
            )
        return self

    def mapping_xml(self, params: str = "") -> str:
        return (f"<MAPPING NAME={attr(self.name)} ISVALID=\"YES\">" + "".join(self.body) + "".join(self.instances)
                + "".join(self.connectors) + params + "</MAPPING>")

    def xml(self, params: str = "", others: list["MappingBuilder"] = ()) -> str:
        definitions = self.definitions + [d for o in others for d in o.definitions]
        mappings = [self.mapping_xml(params)] + [o.mapping_xml() for o in others]
        return ('<?xml version="1.0" encoding="Windows-1252"?>\n<!DOCTYPE POWERMART SYSTEM "powrmart.dtd">\n'
                '<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F_TEST">'
                + "".join(definitions) + "".join(mappings) + "</FOLDER></REPOSITORY></POWERMART>")


def compile_xml(tmp_path: Path, xml: str) -> tuple[dict, list]:
    """Parse and compile; returns ({model name: ModelResult}, mapping-level diagnostics)."""
    path = tmp_path / "export.xml"
    path.write_bytes(xml.encode("cp1252"))
    results, diagnostics = compile_folder(parse_file(path))
    return {r.name: r for r in results}, diagnostics


def run_model(con: duckdb.DuckDBPyConnection, result, order_by: str = "") -> list[dict]:
    """Execute one compiled model (sources in schema raw, refs in main) and return its rows."""
    assert result.status == "COMPLETE", [str(d) for d in result.diagnostics]
    sql = render_jinja(render_model(result))
    con.execute(f"create or replace table main.{result.name} as {sql}")
    return query(con, f"select * from main.{result.name}" + (f" order by {order_by}" if order_by else ""))
