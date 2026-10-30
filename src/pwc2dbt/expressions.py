"""PowerCenter transformation-language expressions: lexer, parser and DuckDB translation.

Only the constructs listed in ``FUNCTIONS`` and the operators below are
translated. Anything else raises ``Unsupported``; there is never a fallback.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass


class Unsupported(Exception):
    """The expression cannot be translated with the same semantics."""


@dataclass(frozen=True)
class Lit:
    value: str  # SQL text of the literal
    kind: str  # string | number | null


@dataclass(frozen=True)
class Ref:
    name: str


@dataclass(frozen=True)
class Param:
    name: str


@dataclass(frozen=True)
class Call:
    name: str  # upper case
    args: tuple
    star: bool = False  # COUNT(*)


@dataclass(frozen=True)
class Unary:
    op: str  # NOT | -
    operand: object


@dataclass(frozen=True)
class Binary:
    op: str  # OR AND = <> < <= > >= || + - * /
    left: object
    right: object


_TOKEN = re.compile(
    r"(?P<ws>\s+)"
    r"|(?P<string>'[^']*')"
    r"|(?P<number>\d+(?:\.\d*)?|\.\d+)"
    r"|(?P<param>\$\$[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<ident>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<op><>|!=|>=|<=|\|\||[=<>+\-*/(),])"
)
_KEYWORDS = {"AND", "OR", "NOT", "NULL"}
_COMPARISONS = {"=", "<>", "!=", "<", "<=", ">", ">="}


def tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match:
            raise Unsupported(f"cannot read expression at {text[pos:pos + 20]!r}")
        kind, value = match.lastgroup, match.group()
        if kind == "op" and text.startswith(("--", "//"), match.start()):
            raise Unsupported("comments inside expressions are not supported")
        if kind == "ident" and value.upper() in _KEYWORDS:
            kind, value = "keyword", value.upper()
        if kind != "ws":
            tokens.append((kind, value))
        pos = match.end()
    return tokens


def parse(text: str) -> object:
    parser = _Parser(tokenize(text))
    node = parser.expression()
    if parser.peek() is not None:
        raise Unsupported(f"unexpected {parser.peek()[1]!r} in expression")
    return node


class _Parser:
    """Recursive descent. Precedence, lowest first: OR, AND, NOT, comparison, ||, + -, * /, unary minus."""

    def __init__(self, tokens: list[tuple[str, str]]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        token = self.peek()
        if token is None:
            raise Unsupported("unexpected end of expression")
        self.pos += 1
        return token

    def accept(self, *values: str) -> str | None:
        token = self.peek()
        if token and token[0] in ("op", "keyword") and token[1] in values:
            self.pos += 1
            return token[1]
        return None

    def expect(self, value: str) -> None:
        if not self.accept(value):
            found = self.peek()[1] if self.peek() else "end of expression"
            raise Unsupported(f"expected {value!r}, found {found!r}")

    def expression(self) -> object:
        node = self.conjunction()
        while self.accept("OR"):
            node = Binary("OR", node, self.conjunction())
        return node

    def conjunction(self) -> object:
        node = self.negation()
        while self.accept("AND"):
            node = Binary("AND", node, self.negation())
        return node

    def negation(self) -> object:
        if self.accept("NOT"):
            return Unary("NOT", self.negation())
        return self.comparison()

    def comparison(self) -> object:
        node = self.concatenation()
        op = self.accept(*_COMPARISONS)
        if op:
            node = Binary("<>" if op == "!=" else op, node, self.concatenation())
            if self.peek() and self.peek()[1] in _COMPARISONS:
                raise Unsupported("chained comparisons are not supported")
        return node

    def concatenation(self) -> object:
        node = self.additive()
        while self.accept("||"):
            node = Binary("||", node, self.additive())
        return node

    def additive(self) -> object:
        node = self.term()
        while op := self.accept("+", "-"):
            node = Binary(op, node, self.term())
        return node

    def term(self) -> object:
        node = self.unary()
        while op := self.accept("*", "/"):
            node = Binary(op, node, self.unary())
        return node

    def unary(self) -> object:
        if self.accept("-"):
            return Unary("-", self.unary())
        if self.accept("+"):
            return self.unary()
        return self.primary()

    def primary(self) -> object:
        kind, value = self.take()
        if kind == "string":
            return Lit(value, "string")
        if kind == "number":
            return Lit(value, "number")
        if kind == "param":
            return Param(value)
        if kind == "keyword" and value == "NULL":
            return Lit("null", "null")
        if kind == "op" and value == "(":
            node = self.expression()
            self.expect(")")
            return node
        if kind == "ident":
            if not self.accept("("):
                return Ref(value)
            if self.peek() == ("op", "*"):
                self.take()
                self.expect(")")
                return Call(value.upper(), (), star=True)
            args = []
            if not self.accept(")"):
                args.append(self.expression())
                while self.accept(","):
                    args.append(self.expression())
                self.expect(")")
            return Call(value.upper(), tuple(args))
        raise Unsupported(f"unexpected {value!r} in expression")


def references(node: object, outside_aggregates: bool = False) -> list[str]:
    """Port names referenced by ``node`` in order; optionally only those not inside an aggregate call."""
    if isinstance(node, Ref):
        return [node.name]
    if isinstance(node, Call):
        if outside_aggregates and node.name in AGGREGATES:
            return []
        return [r for arg in node.args for r in references(arg, outside_aggregates)]
    if isinstance(node, Unary):
        return references(node.operand, outside_aggregates)
    if isinstance(node, Binary):
        return references(node.left, outside_aggregates) + references(node.right, outside_aggregates)
    return []


_KINDS = {
    "string": "string",
    "nstring": "string",
    "text": "string",
    "ntext": "string",
    "date/time": "date",
    "decimal": "number",
    "double": "number",
    "real": "number",
    "integer": "number",
    "small integer": "number",
    "bigint": "number",
}


def kind_of(datatype: str) -> str:
    """Expression kind of a PowerCenter transformation datatype."""
    return _KINDS.get(datatype.lower(), "unknown")


@dataclass
class Context:
    resolve: Callable[[str], tuple[str, str]]  # port name -> (SQL, kind); raises Unsupported
    param: Callable[[str], tuple[str, str]]  # $$NAME -> (SQL, kind); raises Unsupported
    allow_aggregates: bool = False
    warn: Callable[[str], None] = lambda message: None


AGGREGATES = {"SUM", "MIN", "MAX", "AVG", "COUNT"}
FUNCTIONS = {"IIF", "ISNULL", "UPPER", "LTRIM", "RTRIM", "TRUNC", "TO_CHAR", "TO_DATE", "DATE_DIFF"} | AGGREGATES

_LEVEL = {"OR": 1, "AND": 2, "NOT": 3, "=": 4, "<>": 4, "<": 4, "<=": 4, ">": 4, ">=": 4,
          "||": 5, "+": 6, "-": 6, "*": 7, "/": 7, "NEG": 8}
_ATOM = 99
_DAY_FORMATS = {"D", "DD", "DDD"}
_FORMAT_TOKENS = (("YYYY", "%Y"), ("HH24", "%H"), ("YY", "%y"), ("MM", "%m"), ("DD", "%d"), ("MI", "%M"), ("SS", "%S"))
_FORMAT_SEPARATORS = set("-/:., ")


def translate(node: object, ctx: Context) -> str:
    return _translate(node, ctx, in_aggregate=False)[0]


def translate_condition(node: object, ctx: Context) -> str:
    """Translate an expression used as a row condition; it must be boolean."""
    sql, kind, _ = _translate(node, ctx, in_aggregate=False)
    if kind not in ("boolean", "null"):
        raise Unsupported("condition is not a boolean expression")
    return sql


def _wrap(part: tuple[str, str, int], level: int, strict: bool = False) -> str:
    sql, _, part_level = part
    return f"({sql})" if part_level < level or (strict and part_level == level) else sql


def _expect(kind: str, allowed: tuple[str, ...], what: str) -> None:
    if kind not in allowed + ("null",):
        raise Unsupported(f"{what} needs {' or '.join(allowed)}, got {kind}")


def _translate(node: object, ctx: Context, in_aggregate: bool) -> tuple[str, str, int]:
    if isinstance(node, Lit):
        if node.kind == "string" and any(m in node.value for m in ("{{", "{%", "{#")):
            raise Unsupported("string literal contains Jinja delimiters")
        return node.value, node.kind, _ATOM
    if isinstance(node, Ref):
        sql, kind = ctx.resolve(node.name)
        return sql, kind, _ATOM
    if isinstance(node, Param):
        sql, kind = ctx.param(node.name)
        return sql, kind, _ATOM
    if isinstance(node, Unary):
        operand = _translate(node.operand, ctx, in_aggregate)
        if node.op == "NOT":
            _expect(operand[1], ("boolean",), "NOT")
            return f"not {_wrap(operand, _LEVEL['NOT'])}", "boolean", _LEVEL["NOT"]
        _expect(operand[1], ("number",), "unary minus")
        return f"-{_wrap(operand, _LEVEL['NEG'], strict=True)}", "number", _LEVEL["NEG"]
    if isinstance(node, Binary):
        return _binary(node, ctx, in_aggregate)
    if isinstance(node, Call):
        return _call(node, ctx, in_aggregate)
    raise Unsupported(f"unknown expression node {node!r}")


def _binary(node: Binary, ctx: Context, in_aggregate: bool) -> tuple[str, str, int]:
    left = _translate(node.left, ctx, in_aggregate)
    right = _translate(node.right, ctx, in_aggregate)
    level = _LEVEL[node.op]
    if node.op in ("AND", "OR"):
        _expect(left[1], ("boolean",), node.op)
        _expect(right[1], ("boolean",), node.op)
        kind = "boolean"
    elif level == _LEVEL["="]:
        if "null" not in (left[1], right[1]) and left[1] != right[1]:
            raise Unsupported(f"comparison between {left[1]} and {right[1]} needs an implicit conversion")
        if left[1] == "unknown":
            raise Unsupported("comparison of an operand with an unsupported datatype")
        kind = "boolean"
    elif node.op == "||":
        _expect(left[1], ("string",), "||")
        _expect(right[1], ("string",), "||")
        kind = "string"
    else:
        _expect(left[1], ("number",), f"operator {node.op}")
        _expect(right[1], ("number",), f"operator {node.op}")
        kind = "number"
    sql = f"{_wrap(left, level)} {node.op.lower()} {_wrap(right, level, strict=True)}"
    return sql, kind, level


def _call(node: Call, ctx: Context, in_aggregate: bool) -> tuple[str, str, int]:
    name = node.name
    if name not in FUNCTIONS:
        raise Unsupported(f"function {name} is not supported")
    if name in AGGREGATES:
        if not ctx.allow_aggregates:
            raise Unsupported(f"aggregate function {name} outside an Aggregator")
        if in_aggregate:
            raise Unsupported(f"nested aggregate function {name}")
        if node.star:
            if name != "COUNT":
                raise Unsupported(f"{name}(*) is not valid")
            return "count(*)", "number", _ATOM
        if len(node.args) != 1:
            raise Unsupported(f"{name} with {len(node.args)} arguments (filter conditions) is not supported")
        arg = _translate(node.args[0], ctx, in_aggregate=True)
        kind = arg[1] if name in ("MIN", "MAX") else "number"
        if name in ("SUM", "AVG"):
            _expect(arg[1], ("number",), name)
        return f"{name.lower()}({arg[0]})", kind, _ATOM

    args = [_translate(a, ctx, in_aggregate) for a in node.args]
    kinds = [a[1] for a in args]
    if name == "IIF":
        if len(args) != 3:
            raise Unsupported(f"IIF with {len(args)} arguments is not supported")
        _expect(kinds[0], ("boolean",), "IIF condition")
        if "null" not in kinds[1:] and kinds[1] != kinds[2]:
            raise Unsupported(f"IIF branches have different types ({kinds[1]}, {kinds[2]})")
        kind = kinds[1] if kinds[1] != "null" else kinds[2]
        return f"case when {args[0][0]} then {args[1][0]} else {args[2][0]} end", kind, _ATOM
    if name == "ISNULL":
        _arity(name, args, 1)
        return f"({args[0][0]} is null)", "boolean", _ATOM
    if name in ("UPPER", "LTRIM", "RTRIM"):
        if len(args) != 1:
            raise Unsupported(f"{name} with {len(args)} arguments is not supported")
        _expect(kinds[0], ("string",), name)
        return f"{name.lower()}({args[0][0]})", "string", _ATOM
    if name == "TRUNC":
        if len(args) != 1:
            raise Unsupported("TRUNC with a format argument is not supported")
        _expect(kinds[0], ("date",), "TRUNC")
        return f"date_trunc('day', {args[0][0]})", "date", _ATOM
    if name in ("TO_CHAR", "TO_DATE"):
        if len(args) != 2:
            raise Unsupported(f"{name} without an explicit format is not supported")
        _expect(kinds[0], ("date",) if name == "TO_CHAR" else ("string",), name)
        fmt = _date_format(node.args[1])
        if name == "TO_CHAR":
            return f"strftime({args[0][0]}, '{fmt}')", "string", _ATOM
        if "%y" in fmt:
            ctx.warn(
                "TO_DATE with a 2-digit year: DuckDB maps 69-99 to 19xx, PowerCenter uses the current century"
            )
        return f"strptime({args[0][0]}, '{fmt}')", "date", _ATOM
    if name == "DATE_DIFF":
        _arity(name, args, 3)
        _expect(kinds[0], ("date",), "DATE_DIFF")
        _expect(kinds[1], ("date",), "DATE_DIFF")
        unit = node.args[2]
        if not (isinstance(unit, Lit) and unit.kind == "string" and unit.value.strip("'").upper() in _DAY_FORMATS):
            raise Unsupported("DATE_DIFF is only supported with a day format ('D', 'DD' or 'DDD')")
        return f"(date_diff('second', {args[1][0]}, {args[0][0]}) / 86400.0)", "number", _ATOM
    raise Unsupported(f"function {name} is not supported")


def _arity(name: str, args: list, count: int) -> None:
    if len(args) != count:
        raise Unsupported(f"{name} expects {count} arguments, got {len(args)}")


def _date_format(node: object) -> str:
    if not (isinstance(node, Lit) and node.kind == "string"):
        raise Unsupported("date format must be a string literal")
    text, out = node.value[1:-1].upper(), []
    while text:
        for token, directive in _FORMAT_TOKENS:
            if text.startswith(token):
                out.append(directive)
                text = text[len(token):]
                break
        else:
            if text[0] not in _FORMAT_SEPARATORS:
                raise Unsupported(f"date format element at {text!r} is not supported")
            out.append(text[0])
            text = text[1:]
    return "".join(out)
