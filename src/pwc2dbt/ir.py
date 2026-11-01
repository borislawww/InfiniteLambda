"""Intermediate representation of a PowerCenter folder export, plus graph validation.

The IR is plain frozen data. It knows nothing about SQL. Transformations are
keyed per mapping, never globally: two mappings may define different
transformations with the same name.
"""

import re
from collections.abc import Mapping as MappingType
from dataclasses import dataclass, field

SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

SUPPORTED_TYPES = frozenset(
    {
        "Source Qualifier",
        "Expression",
        "Filter",
        "Router",
        "Sorter",
        "Joiner",
        "Aggregator",
        "Custom Transformation",  # only the Union module; checked by the compiler
    }
)


@dataclass(frozen=True)
class Field:
    name: str
    datatype: str
    precision: int
    scale: int


@dataclass(frozen=True)
class SourceDef:
    name: str
    owner: str
    fields: tuple[Field, ...]

    def field(self, name: str) -> Field | None:
        return next((f for f in self.fields if f.name.upper() == name.upper()), None)


@dataclass(frozen=True)
class TargetDef:
    name: str
    fields: tuple[Field, ...]

    def field(self, name: str) -> Field | None:
        return next((f for f in self.fields if f.name.upper() == name.upper()), None)


@dataclass(frozen=True)
class Port:
    name: str
    porttype: str
    datatype: str
    precision: int
    scale: int
    expression: str
    group: str
    ref_field: str
    expression_type: str
    default_value: str

    @property
    def is_input(self) -> bool:
        return "INPUT" in self.porttype.split("/")

    @property
    def is_output(self) -> bool:
        return "OUTPUT" in self.porttype.split("/")

    @property
    def is_master(self) -> bool:
        return "MASTER" in self.porttype.split("/")

    @property
    def is_variable(self) -> bool:
        return self.porttype == "LOCAL VARIABLE"


@dataclass(frozen=True)
class Group:
    name: str
    type: str
    order: int
    expression: str


@dataclass(frozen=True)
class Transformation:
    name: str
    type: str
    ports: tuple[Port, ...]
    attributes: MappingType[str, str]
    groups: tuple[Group, ...] = ()
    field_dependencies: tuple[tuple[str, str], ...] = ()  # (input port, output port)

    def port(self, name: str) -> Port | None:
        return next((p for p in self.ports if p.name.upper() == name.upper()), None)

    def attribute(self, name: str) -> str:
        return self.attributes.get(name, "")


@dataclass(frozen=True)
class Instance:
    name: str
    type: str  # SOURCE | TARGET | TRANSFORMATION
    definition: str
    transformation_type: str
    associated_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class Connector:
    from_instance: str
    from_field: str
    to_instance: str
    to_field: str


@dataclass(frozen=True)
class Parameter:
    name: str
    datatype: str
    default: str


@dataclass(frozen=True)
class Mapping:
    name: str
    transformations: MappingType[str, Transformation]
    instances: MappingType[str, Instance]
    connectors: tuple[Connector, ...]
    parameters: MappingType[str, Parameter] = field(default_factory=dict)


@dataclass(frozen=True)
class Folder:
    name: str
    sources: MappingType[str, SourceDef]
    targets: MappingType[str, TargetDef]
    mappings: tuple[Mapping, ...]


@dataclass(frozen=True)
class Diagnostic:
    severity: str  # ERROR | INCOMPLETE | WARNING
    mapping: str
    transformation: str
    port: str
    message: str

    def __str__(self) -> str:
        where = self.mapping
        if self.transformation:
            where += f"/{self.transformation}"
        if self.port:
            where += f".{self.port}"
        return f"{self.severity} {where}: {self.message}"


def instance_fields(instance: Instance, mapping: Mapping, folder: Folder) -> dict[str, tuple[bool, bool]] | None:
    """Port name (upper) -> (can receive a connector, can send a connector), or None if unresolved."""
    if instance.type == "SOURCE":
        source = folder.sources.get(instance.definition)
        return None if source is None else {f.name.upper(): (False, True) for f in source.fields}
    if instance.type == "TARGET":
        target = folder.targets.get(instance.definition)
        return None if target is None else {f.name.upper(): (True, False) for f in target.fields}
    transformation = mapping.transformations.get(instance.definition)
    if transformation is None:
        return None
    return {p.name.upper(): (p.is_input, p.is_output) for p in transformation.ports}


def validate_mapping(mapping: Mapping, folder: Folder) -> list[Diagnostic]:
    """Structural checks that make a mapping impossible to compile. Unsupported types only warn."""
    diagnostics: list[Diagnostic] = []

    def error(transformation: str, port: str, message: str) -> None:
        diagnostics.append(Diagnostic("ERROR", mapping.name, transformation, port, message))

    def check_name(kind: str, name: str, transformation: str = "") -> None:
        if not SAFE_IDENTIFIER.match(name):
            error(transformation, "", f"{kind} name {name!r} is not a safe SQL identifier")

    check_name("mapping", mapping.name)
    fields_by_instance: dict[str, dict[str, tuple[bool, bool]]] = {}
    target_definitions: dict[str, str] = {}
    for instance in mapping.instances.values():
        check_name("instance", instance.name)
        fields = instance_fields(instance, mapping, folder)
        if fields is None:
            error(instance.name, "", f"{instance.type.lower()} definition {instance.definition!r} not found")
            continue
        fields_by_instance[instance.name] = fields
        for name in fields:
            check_name("port", name, instance.name)
        if instance.type == "TARGET":
            check_name("target", instance.definition, instance.name)
            if instance.definition in target_definitions:
                error(
                    instance.name,
                    "",
                    f"target {instance.definition} is also loaded by instance "
                    f"{target_definitions[instance.definition]}; one model per target cannot represent both",
                )
            target_definitions[instance.definition] = instance.name
        if instance.type == "SOURCE":
            source = folder.sources[instance.definition]
            check_name("source", source.name, instance.name)
            check_name("source owner", source.owner, instance.name)
        if instance.type != "TRANSFORMATION":
            continue
        transformation = mapping.transformations[instance.definition]
        seen: set[str] = set()
        for port in transformation.ports:
            if port.name.upper() in seen:
                error(instance.name, port.name, "duplicate port name")
            seen.add(port.name.upper())
        for group in transformation.groups:
            check_name("group", group.name, instance.name)
        if transformation.type not in SUPPORTED_TYPES:
            diagnostics.append(
                Diagnostic(
                    "WARNING",
                    mapping.name,
                    instance.name,
                    "",
                    f"transformation type {transformation.type!r} is not supported; dependent targets will be incomplete",
                )
            )

    edges: dict[str, set[str]] = {}
    fed: set[tuple[str, str]] = set()
    for c in mapping.connectors:
        ends = ((c.from_instance, c.from_field, 1, "from"), (c.to_instance, c.to_field, 0, "to"))
        valid = True
        for instance, port, direction, label in ends:
            fields = fields_by_instance.get(instance)
            if instance not in mapping.instances:
                error(instance, port, f"connector {label} unknown instance")
                valid = False
            elif fields is None:
                valid = False  # already reported as an unresolved definition
            elif port.upper() not in fields:
                error(instance, port, f"connector {label} unknown port")
                valid = False
            elif not fields[port.upper()][direction]:
                kind = "output" if direction else "input"
                error(instance, port, f"connector {label} a port that is not an {kind} port")
                valid = False
        if not valid:
            continue
        key = (c.to_instance.upper(), c.to_field.upper())
        if key in fed:
            error(c.to_instance, c.to_field, "input port is fed by more than one connector")
        fed.add(key)
        edges.setdefault(c.from_instance, set()).add(c.to_instance)

    cycle = _find_cycle(edges)
    if cycle:
        error(cycle[0], "", "cycle in the data flow: " + " -> ".join(cycle))
    return diagnostics


def _find_cycle(edges: dict[str, set[str]]) -> list[str]:
    state: dict[str, int] = {}  # 1 = on stack, 2 = done
    stack: list[str] = []

    def visit(node: str) -> list[str]:
        state[node] = 1
        stack.append(node)
        for nxt in sorted(edges.get(node, ())):
            if state.get(nxt) == 1:
                return stack[stack.index(nxt):] + [nxt]
            if nxt not in state:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return []

    for node in sorted(edges):
        if node not in state:
            found = visit(node)
            if found:
                return found
    return []
