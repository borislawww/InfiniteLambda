"""Read a PowerCenter repository export into the IR.

The DTD named in the DOCTYPE is never loaded and entities are never resolved,
so the parser cannot reach the file system or the network (XXE).
"""

from pathlib import Path

from lxml import etree

from .ir import (
    Connector,
    Field,
    Folder,
    Group,
    Instance,
    Mapping,
    Parameter,
    Port,
    SourceDef,
    TargetDef,
    Transformation,
)


class ParseError(Exception):
    """The file is not a PowerCenter folder export this converter can read."""


def parse_file(path: Path | str) -> Folder:
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    try:
        root = etree.fromstring(Path(path).read_bytes(), parser)
    except (OSError, etree.XMLSyntaxError) as exc:
        raise ParseError(f"cannot read {path}: {exc}") from exc
    folders = root.findall("./REPOSITORY/FOLDER")
    if len(folders) != 1:
        raise ParseError(f"expected exactly one REPOSITORY/FOLDER in {path}, found {len(folders)}")
    return _folder(folders[0])


def _folder(el: etree._Element) -> Folder:
    sources = {s.get("NAME"): _source(s) for s in el.findall("SOURCE")}
    targets = {t.get("NAME"): _target(t) for t in el.findall("TARGET")}
    mappings = tuple(_mapping(m) for m in el.findall("MAPPING"))
    return Folder(name=el.get("NAME"), sources=sources, targets=targets, mappings=mappings)


def _int(value: str | None) -> int:
    return int(value) if value and value.strip().lstrip("-").isdigit() else 0


def _field(el: etree._Element) -> Field:
    return Field(el.get("NAME"), el.get("DATATYPE", ""), _int(el.get("PRECISION")), _int(el.get("SCALE")))


def _source(el: etree._Element) -> SourceDef:
    return SourceDef(el.get("NAME"), el.get("OWNERNAME", ""), tuple(_field(f) for f in el.findall("SOURCEFIELD")))


def _target(el: etree._Element) -> TargetDef:
    return TargetDef(el.get("NAME"), tuple(_field(f) for f in el.findall("TARGETFIELD")))


def _port(el: etree._Element) -> Port:
    return Port(
        name=el.get("NAME"),
        porttype=el.get("PORTTYPE", ""),
        datatype=el.get("DATATYPE", ""),
        precision=_int(el.get("PRECISION")),
        scale=_int(el.get("SCALE")),
        expression=el.get("EXPRESSION", ""),
        group=el.get("GROUP", ""),
        ref_field=el.get("REF_FIELD", ""),
        expression_type=el.get("EXPRESSIONTYPE", ""),
        default_value=el.get("DEFAULTVALUE", ""),
    )


def _transformation(el: etree._Element) -> Transformation:
    return Transformation(
        name=el.get("NAME"),
        type=el.get("TYPE", ""),
        ports=tuple(_port(p) for p in el.findall("TRANSFORMFIELD")),
        attributes={a.get("NAME"): a.get("VALUE", "") for a in el.findall("TABLEATTRIBUTE")},
        groups=tuple(
            Group(g.get("NAME"), g.get("TYPE", ""), _int(g.get("ORDER")), g.get("EXPRESSION", ""))
            for g in el.findall("GROUP")
        ),
        field_dependencies=tuple(
            (d.get("INPUTFIELD"), d.get("OUTPUTFIELD")) for d in el.findall("FIELDDEPENDENCY")
        ),
    )


def _instance(el: etree._Element) -> Instance:
    return Instance(
        name=el.get("NAME"),
        type=el.get("TYPE", ""),
        definition=el.get("TRANSFORMATION_NAME", ""),
        transformation_type=el.get("TRANSFORMATION_TYPE", ""),
        associated_sources=tuple(a.get("NAME") for a in el.findall("ASSOCIATED_SOURCE_INSTANCE")),
    )


def _mapping(el: etree._Element) -> Mapping:
    return Mapping(
        name=el.get("NAME"),
        transformations={t.get("NAME"): _transformation(t) for t in el.findall("TRANSFORMATION")},
        instances={i.get("NAME"): _instance(i) for i in el.findall("INSTANCE")},
        connectors=tuple(
            Connector(c.get("FROMINSTANCE"), c.get("FROMFIELD"), c.get("TOINSTANCE"), c.get("TOFIELD"))
            for c in el.findall("CONNECTOR")
        ),
        # Mapping variables (ISPARAM=NO) are not parameters; a reference to one fails translation.
        parameters={
            v.get("NAME").upper(): Parameter(v.get("NAME"), v.get("DATATYPE", ""), v.get("DEFAULTVALUE", ""))
            for v in el.findall("MAPPINGVARIABLE")
            if v.get("ISPARAM") == "YES"
        },
    )
