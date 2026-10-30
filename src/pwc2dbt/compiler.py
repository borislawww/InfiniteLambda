"""Compile mapping targets into dbt models: one CTE per transformation instance, built on demand.

For each target, lineage is walked backwards from its connected columns. Each
column depends on the ports that compute it plus the row-shaping ports of
every transformation it passes through (filter and router conditions, join
keys, group-by ports). A column whose lineage reaches something that cannot be
translated is left out and the model is INCOMPLETE; nothing is replaced by a
placeholder.
"""

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from . import expressions as ex
from .ir import SAFE_IDENTIFIER, Diagnostic, Folder, Instance, Mapping, Port, Transformation, validate_mapping

COMPLETE = "COMPLETE"
INCOMPLETE = "INCOMPLETE"

# The two type boundaries. Intermediate ports are never cast (session high precision is off,
# so PowerCenter computes decimals as doubles, which matches DuckDB division).
_SQ_TYPES = {
    "string": "varchar",
    "nstring": "varchar",
    "text": "varchar",
    "ntext": "varchar",
    "integer": "integer",
    "small integer": "integer",
    "bigint": "bigint",
    "double": "double",
    "real": "double",
    "decimal": "double",
    "date/time": "timestamp",
}
_JOIN_TYPES = {
    "Normal Join": ("detail", "inner join", "master"),
    "Master Outer Join": ("detail", "left join", "master"),
    "Detail Outer Join": ("master", "left join", "detail"),
    "Full Outer Join": ("detail", "full outer join", "master"),
}
_PARAMETER_DEFAULT_FORMAT = "%m/%d/%Y %H:%M:%S"

Item = tuple[str, str]  # (instance name, port name upper)
Node = tuple[str, str]  # (instance name, router group upper or "")
Note = tuple[str, str, str]  # (transformation instance, port, message)


@dataclass(frozen=True)
class Cte:
    name: str
    body: str


@dataclass(frozen=True)
class ModelResult:
    name: str
    mapping: str
    target_instance: str
    status: str
    ctes: tuple[Cte, ...]
    final: str
    refs: tuple[str, ...]
    sources: tuple[tuple[str, str], ...]  # (owner, table), lower case
    columns: tuple[tuple[str, str], ...]  # (column, DuckDB type) of every target column
    diagnostics: tuple[Diagnostic, ...]


@dataclass
class _Spec:
    """How one output port is computed inside its transformation's CTE."""

    sql: tuple[str, ...] = ()  # one expression per Union input group, otherwise exactly one
    deps: tuple[Item, ...] = ()
    problem: Note | None = None
    warnings: tuple[Note, ...] = ()


@dataclass
class _NodeBuild:
    ports: dict[str, _Spec]
    render: Callable[[list[str]], str]
    row_deps: tuple[Item, ...] = ()
    problem: Note | None = None
    warnings: tuple[Note, ...] = ()
    relation: tuple[str, str, str] | None = None  # Source Qualifier: (kind, owner, name), kind = ref | source


class _Graph:
    def __init__(self, mapping: Mapping, folder: Folder):
        self.mapping = mapping
        self.folder = folder
        self._into = {
            (c.to_instance, c.to_field.upper()): (c.from_instance, c.from_field.upper()) for c in mapping.connectors
        }
        self._builds: dict[Node, _NodeBuild] = {}
        self.model_targets = {
            i.definition.upper()
            for m in folder.mappings
            for i in m.instances.values()
            if i.type == "TARGET"
        }

    def transformation(self, instance: str) -> Transformation | None:
        inst = self.mapping.instances.get(instance)
        return self.mapping.transformations.get(inst.definition) if inst and inst.type == "TRANSFORMATION" else None

    def upstream_of(self, instance: str, port: str) -> Item | None:
        """The output port feeding ``instance.port``, seen through Sorters. The only reader of connectors."""
        found = self._into.get((instance, port.upper()))
        while found:
            sorter = self.transformation(found[0])
            if not sorter or sorter.type != "Sorter" or sorter.attribute("Distinct") == "YES":
                return found
            found = self._into.get(found)
        return None

    def node_of(self, item: Item) -> Node:
        t = self.transformation(item[0])
        if t and t.type == "Router":
            port = t.port(item[1])
            return (item[0], port.group.upper() if port else "")
        return (item[0], "")

    def build(self, node: Node) -> _NodeBuild:
        if node not in self._builds:
            self._builds[node] = _build_node(self, node)
        return self._builds[node]


def cte_name(node: Node) -> str:
    return node[0].lower() + (f"__{node[1].lower()}" if node[1] else "")


def column_ref(graph: _Graph, item: Item, qualifier: str | None = None) -> str:
    return f"{qualifier or cte_name(graph.node_of(item))}.{item[1].lower()}"


# ---------------------------------------------------------------- per-type builders


def _build_node(graph: _Graph, node: Node) -> _NodeBuild:
    instance, _ = node
    t = graph.transformation(instance)
    if t is None:
        return _unsupported(instance, f"instance {instance} is not a transformation the converter can read from")
    builders = {
        "Source Qualifier": _source_qualifier,
        "Expression": _expression,
        "Filter": _filter,
        "Router": _router,
        "Joiner": _joiner,
        "Aggregator": _aggregator,
        "Custom Transformation": _union,
    }
    if t.type == "Sorter":
        return _unsupported(instance, "Sorter with Distinct=YES removes duplicate rows; not supported")
    if t.type not in builders:
        return _unsupported(instance, f"transformation type {t.type!r} is not supported")
    return builders[t.type](graph, node, t)


def _unsupported(instance: str, message: str) -> _NodeBuild:
    return _NodeBuild(ports={}, render=lambda needed: "", problem=(instance, "", message))


def _select(columns: list[str], tail: str, distinct: bool = False) -> str:
    head = "select distinct" if distinct else "select"
    return head + "\n" + ",\n".join(f"        {c}" for c in columns) + "\n    " + tail


class _Inputs:
    """Resolves input ports of one instance to upstream columns and checks their default values."""

    def __init__(self, graph: _Graph, instance: str, t: Transformation):
        self.graph, self.instance, self.t = graph, instance, t

    def upstream_node(self, ports: list[Port]) -> tuple[Node | None, str]:
        nodes = {self.graph.node_of(up) for p in ports if (up := self.graph.upstream_of(self.instance, p.name))}
        if len(nodes) != 1:
            what = "no connected input" if not nodes else f"inputs from {len(nodes)} upstream transformations"
            return None, f"{what}; one upstream pipeline is required"
        return nodes.pop(), ""

    def resolve(self, port: Port, qualifier: str | None, deps: list[Item], warnings: list[Note]) -> tuple[str, str]:
        up = self.graph.upstream_of(self.instance, port.name)
        if up is None:
            raise ex.Unsupported(f"input port {port.name} is not connected")
        default = port.default_value.strip()
        if default.upper().startswith("ERROR("):
            warnings.append(
                (self.instance, port.name, f"default value {default}: PowerCenter may reject rows where "
                 f"{port.name} is NULL; the generated SQL keeps them")
            )
        elif default.upper() not in ("", "NULL"):
            raise ex.Unsupported(f"input port {port.name} has default value {default!r}, which replaces NULL input")
        deps.append(up)
        return column_ref(self.graph, up, qualifier), ex.kind_of(port.datatype)


def _translate_port(
    graph: _Graph,
    instance: str,
    port_name: str,
    compute: Callable[[list[Item], list[Note]], str],
) -> _Spec:
    deps: list[Item] = []
    warnings: list[Note] = []
    try:
        sql = compute(deps, warnings)
    except ex.Unsupported as exc:
        return _Spec(problem=(instance, port_name, str(exc)))
    return _Spec(sql=(sql,), deps=tuple(deps), warnings=tuple(warnings))


def _context(graph: _Graph, resolve: Callable[[str], tuple[str, str]], warnings: list[Note], instance: str,
             port: str, allow_aggregates: bool = False) -> ex.Context:
    return ex.Context(
        resolve=resolve,
        param=lambda name: _parameter(graph, name),
        allow_aggregates=allow_aggregates,
        warn=lambda message: warnings.append((instance, port, message)),
    )


def _parameter(graph: _Graph, name: str) -> tuple[str, str]:
    param = graph.mapping.parameters.get(name.upper())
    if param is None:
        raise ex.Unsupported(f"{name} is not a mapping parameter (mapping variables are not supported)")
    if param.datatype != "date/time":
        raise ex.Unsupported(f"mapping parameter {name} has type {param.datatype}; only date/time is supported")
    var = name[2:].lower()
    try:
        default = datetime.strptime(param.default, _PARAMETER_DEFAULT_FORMAT).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise ex.Unsupported(f"default {param.default!r} of {name} is not MM/DD/YYYY HH24:MI:SS") from exc
    if not SAFE_IDENTIFIER.match(var):
        raise ex.Unsupported(f"mapping parameter name {name} is not a safe dbt variable name")
    return f"cast('{{{{ var(\"{var}\", \"{default}\") }}}}' as timestamp)", "date"


def _source_qualifier(graph: _Graph, node: Node, t: Transformation) -> _NodeBuild:
    instance = node[0]
    for attribute in ("Sql Query", "User Defined Join", "Source Filter"):
        if t.attribute(attribute).strip():
            return _unsupported(instance, f"{attribute} is set; native database SQL cannot be translated safely")
    sq = graph.mapping.instances[instance]
    source_instance = graph.mapping.instances.get(sq.associated_sources[0]) if len(sq.associated_sources) == 1 else None
    source = graph.folder.sources.get(source_instance.definition) if source_instance else None
    if source is None:
        return _unsupported(instance, "exactly one associated source definition is required")

    alias = source_instance.name.lower()
    relation = ("ref" if source.name.upper() in graph.model_targets else "source", source.owner.lower(), source.name.lower())
    ports: dict[str, _Spec] = {}
    connected: list[Item] = []
    for p in t.ports:
        up = graph.upstream_of(instance, p.name)
        duck = _SQ_TYPES.get(p.datatype.lower())
        if up is not None:
            connected.append((instance, p.name.upper()))
        if up is None or up[0] != source_instance.name:
            ports[p.name.upper()] = _Spec(problem=(instance, p.name, "not connected to the associated source"))
        elif duck is None:
            ports[p.name.upper()] = _Spec(problem=(instance, p.name, f"datatype {p.datatype!r} has no DuckDB mapping"))
        else:
            ports[p.name.upper()] = _Spec(sql=(f"cast({alias}.{up[1].lower()} as {duck})",))

    distinct = t.attribute("Select Distinct") == "YES"
    if relation[0] == "ref":
        from_sql = f"{{{{ ref('{relation[2]}') }}}}"
    else:
        from_sql = f"{{{{ source('{relation[1]}', '{relation[2]}') }}}}"

    def render(needed: list[str]) -> str:
        return _select([f"{ports[n].sql[0]} as {n.lower()}" for n in needed], f"from {from_sql} as {alias}", distinct)

    # Select Distinct works on every connected column, so all of them shape the rows (and any problem
    # with one of them makes every column incomplete).
    return _NodeBuild(ports=ports, render=render, row_deps=tuple(connected) if distinct else (), relation=relation)


def _expression(graph: _Graph, node: Node, t: Transformation) -> _NodeBuild:
    instance = node[0]
    inputs = _Inputs(graph, instance, t)
    upstream, problem = inputs.upstream_node([p for p in t.ports if p.is_input])
    if upstream is None:
        return _unsupported(instance, problem)
    variables = [p for p in t.ports if p.is_variable]
    position = {v.name.upper(): i for i, v in enumerate(variables)}
    stateful: set[str] = set()
    for i, v in enumerate(variables):
        try:
            refs = ex.references(ex.parse(v.expression))
        except ex.Unsupported:
            continue  # reported when the variable is translated
        if any(position.get(r.upper(), -1) >= i for r in refs):
            stateful.add(v.name.upper())

    def compute_for(port: Port) -> Callable[[list[Item], list[Note]], str]:
        def compute(deps: list[Item], warnings: list[Note]) -> str:
            def resolve(name: str) -> tuple[str, str]:
                target = t.port(name)
                if target is None:
                    raise ex.Unsupported(f"unknown port {name}")
                if target.is_variable:
                    if target.name.upper() in stateful:
                        raise ex.Unsupported(
                            f"variable port {target.name} is stateful: it reads itself or a later variable, so it "
                            "carries a value from the previous row, which set-based SQL cannot express"
                        )
                    ctx = _context(graph, resolve, warnings, instance, port.name)
                    return f"({ex.translate(ex.parse(target.expression), ctx)})", ex.kind_of(target.datatype)
                if target.is_input:
                    return inputs.resolve(target, None, deps, warnings)
                raise ex.Unsupported(f"expression references output port {target.name}")

            text = port.expression.strip()
            if port.is_input:
                if text and text.upper() != port.name.upper():
                    raise ex.Unsupported("input/output port with an expression")
                return resolve(port.name)[0]
            return ex.translate(ex.parse(text), _context(graph, resolve, warnings, instance, port.name))

        return compute

    ports = {
        p.name.upper(): _translate_port(graph, instance, p.name, compute_for(p))
        for p in t.ports
        if p.is_output
    }
    up_name = cte_name(upstream)

    def render(needed: list[str]) -> str:
        return _select([f"{ports[n].sql[0]} as {n.lower()}" for n in needed], f"from {up_name}")

    return _NodeBuild(ports=ports, render=render)


def _passthrough_ports(graph: _Graph, inputs: _Inputs, instance: str, ports: list[Port],
                       source_of: Callable[[Port], Port | None]) -> dict[str, _Spec]:
    def spec(p: Port) -> _Spec:
        def compute(deps: list[Item], warnings: list[Note]) -> str:
            source = source_of(p)
            if source is None:
                raise ex.Unsupported(f"output port {p.name} has no input port")
            return inputs.resolve(source, None, deps, warnings)[0]

        return _translate_port(graph, instance, p.name, compute)

    return {p.name.upper(): spec(p) for p in ports}


def _condition(inputs: _Inputs, graph: _Graph, text: str, instance: str) -> tuple[str, tuple[Item, ...], tuple[Note, ...]]:
    deps: list[Item] = []
    warnings: list[Note] = []

    def resolve(name: str) -> tuple[str, str]:
        port = inputs.t.port(name)
        if port is None or not port.is_input:
            raise ex.Unsupported(f"condition references {name}, which is not an input port")
        return inputs.resolve(port, None, deps, warnings)

    sql = ex.translate_condition(ex.parse(text), _context(graph, resolve, warnings, instance, ""))
    return sql, tuple(deps), tuple(warnings)


def _filter(graph: _Graph, node: Node, t: Transformation) -> _NodeBuild:
    instance = node[0]
    inputs = _Inputs(graph, instance, t)
    upstream, problem = inputs.upstream_node([p for p in t.ports if p.is_input])
    if upstream is None:
        return _unsupported(instance, problem)
    text = t.attribute("Filter Condition").strip()
    try:
        where, row_deps, warnings = _condition(inputs, graph, text, instance) if text else ("", (), ())
    except ex.Unsupported as exc:
        return _unsupported(instance, f"filter condition: {exc}")
    ports = _passthrough_ports(graph, inputs, instance, [p for p in t.ports if p.is_output], lambda p: p)
    up_name = cte_name(upstream)

    def render(needed: list[str]) -> str:
        tail = f"from {up_name}" + (f"\n    where {where}" if where else "")
        return _select([f"{ports[n].sql[0]} as {n.lower()}" for n in needed], tail)

    return _NodeBuild(ports=ports, render=render, row_deps=row_deps, warnings=warnings)


def _router(graph: _Graph, node: Node, t: Transformation) -> _NodeBuild:
    instance, group_name = node
    inputs = _Inputs(graph, instance, t)
    upstream, problem = inputs.upstream_node([p for p in t.ports if p.is_input])
    if upstream is None:
        return _unsupported(instance, problem)
    group = next((g for g in t.groups if g.name.upper() == group_name), None)
    if group is None:
        return _unsupported(instance, f"output group {group_name} not found")
    user_groups = sorted((g for g in t.groups if g.type == "OUTPUT"), key=lambda g: g.order)
    try:
        if group.type == "OUTPUT":
            where, row_deps, warnings = _condition(inputs, graph, group.expression, instance)
        elif group.type == "OUTPUT/DEFAULT":
            parts = [_condition(inputs, graph, g.expression, instance) for g in user_groups]
            # A NULL condition is not TRUE, so such a row belongs to the default group.
            where = "\n      and ".join(f"not coalesce({sql}, false)" for sql, _, _ in parts) or "true"
            row_deps = tuple(d for _, deps, _ in parts for d in deps)
            warnings = tuple(w for _, _, ws in parts for w in ws)
        else:
            return _unsupported(instance, f"group {group.name} of type {group.type} is not an output group")
    except ex.Unsupported as exc:
        return _unsupported(instance, f"group condition: {exc}")

    group_ports = [p for p in t.ports if p.is_output and p.group.upper() == group_name]
    ports = _passthrough_ports(graph, inputs, instance, group_ports,
                               lambda p: t.port(p.ref_field) if p.ref_field else None)
    up_name = cte_name(upstream)

    def render(needed: list[str]) -> str:
        return _select([f"{ports[n].sql[0]} as {n.lower()}" for n in needed], f"from {up_name}\n    where {where}")

    return _NodeBuild(ports=ports, render=render, row_deps=row_deps, warnings=warnings)


def _joiner(graph: _Graph, node: Node, t: Transformation) -> _NodeBuild:
    instance = node[0]
    inputs = _Inputs(graph, instance, t)
    join = _JOIN_TYPES.get(t.attribute("Join Type"))
    if join is None:
        return _unsupported(instance, f"join type {t.attribute('Join Type')!r} is not supported")
    if t.attribute("Case Sensitive String Comparison") == "NO":
        return _unsupported(instance, "case-insensitive string comparison is not supported")
    sides: dict[str, Node] = {}
    for side, is_master in (("master", True), ("detail", False)):
        upstream, problem = inputs.upstream_node([p for p in t.ports if p.is_input and p.is_master == is_master])
        if upstream is None:
            return _unsupported(instance, f"{side} side: {problem}")
        sides[side] = upstream

    def qualifier(port: Port) -> str:
        return "master" if port.is_master else "detail"

    deps: list[Item] = []
    warnings: list[Note] = []
    conditions: list[str] = []
    try:
        for term in _conjuncts(ex.parse(t.attribute("Join Condition"))):
            if not (isinstance(term, ex.Binary) and term.op == "=" and isinstance(term.left, ex.Ref)
                    and isinstance(term.right, ex.Ref)):
                raise ex.Unsupported("only conjunctions of port = port are supported")
            pair = [t.port(term.left.name), t.port(term.right.name)]
            if None in pair or not all(p.is_input for p in pair) or pair[0].is_master == pair[1].is_master:
                raise ex.Unsupported("each equality needs one master and one detail input port")
            master, detail = sorted(pair, key=lambda p: not p.is_master)
            m_sql = inputs.resolve(master, "master", deps, warnings)[0]
            d_sql = inputs.resolve(detail, "detail", deps, warnings)[0]
            conditions.append(f"{m_sql} = {d_sql}")
    except ex.Unsupported as exc:
        return _unsupported(instance, f"join condition: {exc}")

    ports: dict[str, _Spec] = {}
    for p in t.ports:
        if p.is_output:
            ports[p.name.upper()] = _translate_port(
                graph, instance, p.name, lambda d, w, p=p: inputs.resolve(p, qualifier(p), d, w)[0]
            )
    first, join_sql, second = join
    on = "\n        and ".join(conditions)

    def render(needed: list[str]) -> str:
        tail = (f"from {cte_name(sides[first])} as {first}\n    {join_sql} {cte_name(sides[second])} as {second}"
                f"\n        on {on}")
        return _select([f"{ports[n].sql[0]} as {n.lower()}" for n in needed], tail)

    return _NodeBuild(ports=ports, render=render, row_deps=tuple(deps), warnings=tuple(warnings))


def _conjuncts(node: object) -> list[object]:
    if isinstance(node, ex.Binary) and node.op == "AND":
        return _conjuncts(node.left) + _conjuncts(node.right)
    return [node]


def _aggregator(graph: _Graph, node: Node, t: Transformation) -> _NodeBuild:
    instance = node[0]
    inputs = _Inputs(graph, instance, t)
    upstream, problem = inputs.upstream_node([p for p in t.ports if p.is_input])
    if upstream is None:
        return _unsupported(instance, problem)
    if any(p.is_variable for p in t.ports):
        return _unsupported(instance, "variable ports in an Aggregator are not supported")
    group_by = [p for p in t.ports if p.expression_type == "GROUPBY"]
    if not group_by:
        return _unsupported(instance, "an Aggregator without group-by ports is not supported")
    group_names = {p.name.upper() for p in group_by}
    row_deps: list[Item] = []
    warnings: list[Note] = []
    try:
        group_sql = [inputs.resolve(p, None, row_deps, warnings)[0] for p in group_by]
    except ex.Unsupported as exc:
        return _unsupported(instance, f"group-by: {exc}")

    def compute_for(port: Port) -> Callable[[list[Item], list[Note]], str]:
        def compute(deps: list[Item], port_warnings: list[Note]) -> str:
            if port.is_input:
                if port.name.upper() not in group_names:
                    raise ex.Unsupported("an input/output port outside the group-by returns an arbitrary row's value")
                return inputs.resolve(port, None, deps, port_warnings)[0]
            ast = ex.parse(port.expression)
            outside = [r for r in ex.references(ast, outside_aggregates=True) if r.upper() not in group_names]
            if outside:
                raise ex.Unsupported(f"{outside[0]} is used outside an aggregate function and is not a group-by port")

            def resolve(name: str) -> tuple[str, str]:
                target = t.port(name)
                if target is None or not target.is_input:
                    raise ex.Unsupported(f"{name} is not an input port")
                return inputs.resolve(target, None, deps, port_warnings)

            ctx = _context(graph, resolve, port_warnings, instance, port.name, allow_aggregates=True)
            return ex.translate(ast, ctx)

        return compute

    ports = {p.name.upper(): _translate_port(graph, instance, p.name, compute_for(p)) for p in t.ports if p.is_output}
    up_name = cte_name(upstream)

    def render(needed: list[str]) -> str:
        tail = f"from {up_name}\n    group by " + ", ".join(group_sql)
        return _select([f"{ports[n].sql[0]} as {n.lower()}" for n in needed], tail)

    return _NodeBuild(ports=ports, render=render, row_deps=tuple(row_deps), warnings=tuple(warnings))


def _union(graph: _Graph, node: Node, t: Transformation) -> _NodeBuild:
    instance = node[0]
    if t.attribute("Module Identifier") != "pmuniontrans":
        return _unsupported(instance, f"Custom transformation {t.attribute('Module Identifier')!r} is not supported "
                                      "(only the Union module pmuniontrans is)")
    inputs = _Inputs(graph, instance, t)
    in_groups = sorted((g for g in t.groups if g.type == "INPUT"), key=lambda g: g.order)
    upstreams: list[Node] = []
    for g in in_groups:
        upstream, problem = inputs.upstream_node([p for p in t.ports if p.group.upper() == g.name.upper()])
        if upstream is None:
            return _unsupported(instance, f"input group {g.name}: {problem}")
        upstreams.append(upstream)

    def spec(out: Port) -> _Spec:
        deps: list[Item] = []
        warnings: list[Note] = []
        branches: list[str] = []
        try:
            for g in in_groups:
                sources = [t.port(i) for i, o in t.field_dependencies if o.upper() == out.name.upper()]
                sources = [p for p in sources if p and p.group.upper() == g.name.upper()]
                if len(sources) != 1:
                    raise ex.Unsupported(f"needs exactly one field dependency in input group {g.name}, found {len(sources)}")
                branches.append(inputs.resolve(sources[0], None, deps, warnings)[0])
        except ex.Unsupported as exc:
            return _Spec(problem=(instance, out.name, str(exc)))
        return _Spec(sql=tuple(branches), deps=tuple(deps), warnings=tuple(warnings))

    ports = {p.name.upper(): spec(p) for p in t.ports if p.is_output}

    def render(needed: list[str]) -> str:
        selects = [
            _select([f"{ports[n].sql[i]} as {n.lower()}" for n in needed], f"from {cte_name(up)}")
            for i, up in enumerate(upstreams)
        ]
        return "\n\n    union all\n\n    ".join(selects)

    return _NodeBuild(ports=ports, render=render)


# ---------------------------------------------------------------- target compilation


def _target_type(datatype: str, precision: int, scale: int) -> str | None:
    kind = datatype.lower()
    if kind in ("varchar2", "varchar", "char", "nvarchar2", "nchar"):
        return "varchar"
    if kind == "number(p,s)":
        return f"decimal({precision},{scale})"
    if kind == "number":
        return "double"
    if kind in ("date", "timestamp"):
        return "timestamp"
    return None


@dataclass
class _Lineage:
    items: list[Item]
    edges: set[tuple[Node, Node]]
    problem: Note | None
    warnings: list[Note]


def _lineage(graph: _Graph, start: Item) -> _Lineage:
    lineage = _Lineage(items=[], edges=set(), problem=None, warnings=[])
    seen_items: set[Item] = set()
    seen_nodes: set[Node] = set()

    def visit(item: Item) -> None:
        if item in seen_items or lineage.problem:
            return
        seen_items.add(item)
        node = graph.node_of(item)
        build = graph.build(node)
        if node not in seen_nodes:
            seen_nodes.add(node)
            lineage.warnings.extend(build.warnings)
            if build.problem:
                lineage.problem = build.problem
                return
            for dep in build.row_deps:
                lineage.edges.add((node, graph.node_of(dep)))
                visit(dep)
        spec = build.ports.get(item[1])
        if spec is None:
            lineage.problem = (item[0], item[1], "port is not an output of this transformation")
            return
        if spec.problem:
            lineage.problem = spec.problem
            return
        lineage.items.append(item)
        lineage.warnings.extend(spec.warnings)
        for dep in spec.deps:
            lineage.edges.add((node, graph.node_of(dep)))
            visit(dep)

    visit(start)
    return lineage


def compile_target(mapping: Mapping, folder: Folder, target_instance: Instance) -> ModelResult:
    graph = _Graph(mapping, folder)
    target = folder.targets[target_instance.definition]
    model = target.name.lower()
    diagnostics: list[Diagnostic] = []
    warnings: list[Note] = []
    columns: list[tuple[str, str]] = []
    select: list[tuple[str, str]] = []  # (SQL, trailing comment)
    needed: dict[Node, set[str]] = {}
    edges: set[tuple[Node, Node]] = set()
    root: Node | None = None

    def incomplete(column: str, note: Note) -> None:
        diagnostics.append(Diagnostic(INCOMPLETE, mapping.name, note[0], note[1],
                                      f"target column {column} not generated: {note[2]}"))

    for f in target.fields:
        duck = _target_type(f.datatype, f.precision, f.scale)
        columns.append((f.name.lower(), duck or "unknown"))
        if duck is None:
            incomplete(f.name, (target_instance.name, f.name, f"target datatype {f.datatype!r} has no DuckDB mapping"))
            continue
        up = graph.upstream_of(target_instance.name, f.name)
        if up is None:
            warnings.append((target_instance.name, f.name, "target column is not connected; generated as NULL"))
            select.append((f"cast(null as {duck}) as {f.name.lower()}", "not connected in the mapping"))
            continue
        if up[1] != f.name.upper() and target.field(up[1]) is not None:
            warnings.append((target_instance.name, f.name, f"fed by port {up[0]}.{up[1]}, which has the name of "
                                                           f"another column of this target; wiring kept as exported"))
        lineage = _lineage(graph, up)
        node = graph.node_of(up)
        if lineage.problem is None and root not in (None, node):
            lineage.problem = (target_instance.name, f.name, f"fed from {cte_name(node)} while other columns come "
                                                             f"from {cte_name(root)}")
        if lineage.problem:
            incomplete(f.name, lineage.problem)
            continue
        root = node
        warnings.extend(lineage.warnings)
        edges |= lineage.edges
        for item in lineage.items:
            needed.setdefault(graph.node_of(item), set()).add(item[1])
        select.append((f"cast({column_ref(graph, up)} as {duck}) as {f.name.lower()}", ""))

    if root is None and not diagnostics:
        diagnostics.append(Diagnostic(INCOMPLETE, mapping.name, target_instance.name, "",
                                      "target has no connected columns"))

    ctes: list[Cte] = []
    refs: set[str] = set()
    sources: set[tuple[str, str]] = set()
    for node in _topological(root, edges) if root else []:
        build = graph.build(node)
        t = graph.transformation(node[0])
        port_order = [p.name.upper() for p in t.ports if p.name.upper() in needed.get(node, ())]
        ctes.append(Cte(cte_name(node), build.render(port_order)))
        if build.relation:
            kind, owner, name = build.relation
            if kind == "ref":
                refs.add(name)
            else:
                sources.add((owner, name))

    final = ""
    if root is not None:
        lines = []
        for i, (sql, comment) in enumerate(select):
            sep = "," if i < len(select) - 1 else ""
            lines.append(f"    {sql}{sep}" + (f"  -- {comment}" if comment else ""))
        final = "select\n" + "\n".join(lines) + f"\nfrom {cte_name(root)}"

    seen: set[Note] = set()
    unique_warnings = [w for w in warnings if not (w in seen or seen.add(w))]
    diagnostics.extend(Diagnostic("WARNING", mapping.name, *w) for w in unique_warnings)
    status = INCOMPLETE if any(d.severity == INCOMPLETE for d in diagnostics) else COMPLETE
    return ModelResult(
        name=model,
        mapping=mapping.name,
        target_instance=target_instance.name,
        status=status,
        ctes=tuple(ctes),
        final=final,
        refs=tuple(sorted(refs)),
        sources=tuple(sorted(sources)),
        columns=tuple(columns),
        diagnostics=tuple(diagnostics),
    )


def _topological(root: Node, edges: set[tuple[Node, Node]]) -> list[Node]:
    """Upstream first; depth-first from the target with ties broken by name, so the order is deterministic."""
    children: dict[Node, list[Node]] = {}
    for parent, child in edges:
        children.setdefault(parent, []).append(child)
    order: list[Node] = []
    seen: set[Node] = set()

    def visit(node: Node) -> None:
        if node in seen:
            return
        seen.add(node)
        for child in sorted(children.get(node, ())):
            visit(child)
        order.append(node)

    visit(root)
    return order


def compile_folder(folder: Folder, mapping_names: list[str] | None = None) -> tuple[list[ModelResult], list[Diagnostic]]:
    """Compile the selected mappings (all when None). Returns the models and mapping-level diagnostics."""
    selected = [m for m in folder.mappings if mapping_names is None or m.name in mapping_names]
    results: list[ModelResult] = []
    diagnostics: list[Diagnostic] = []
    failed_models: set[str] = set()
    for mapping in selected:
        found = validate_mapping(mapping, folder)
        diagnostics.extend(found)
        targets = [i for i in mapping.instances.values() if i.type == "TARGET"]
        if any(d.severity == "ERROR" for d in found):
            failed_models |= {i.definition.lower() for i in targets}
            continue
        if not targets:
            diagnostics.append(Diagnostic("WARNING", mapping.name, "", "", "mapping has no targets"))
        for target in targets:
            loaded_by = next((r.mapping for r in results if r.name == target.definition.lower()), None)
            if loaded_by:
                diagnostics.append(Diagnostic("ERROR", mapping.name, target.name, "",
                                              f"target {target.definition} is also loaded by mapping {loaded_by}; "
                                              "one model per target cannot represent both"))
                continue
            results.append(compile_target(mapping, folder, target))

    generated = {r.name for r in results}
    for r in results:
        for ref in r.refs:
            if ref not in generated and ref not in failed_models:
                diagnostics.append(Diagnostic("WARNING", r.mapping, r.target_instance, "",
                                              f"refs model {ref}, which is not generated in this run"))

    changed = True
    while changed:
        changed = False
        unusable = failed_models | {r.name for r in results if r.status == INCOMPLETE}
        for i, r in enumerate(results):
            blocked = [ref for ref in r.refs if ref in unusable]
            if r.status == COMPLETE and blocked:
                note = Diagnostic(INCOMPLETE, r.mapping, r.target_instance, "",
                                  f"upstream model {blocked[0]} is incomplete or failed")
                results[i] = dataclasses.replace(r, status=INCOMPLETE, diagnostics=r.diagnostics + (note,))
                changed = True
    return results, diagnostics
