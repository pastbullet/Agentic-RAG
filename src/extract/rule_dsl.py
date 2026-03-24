"""Restricted DSL parser for MessageIR presence/validation rules."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable


class RuleSyntaxError(ValueError):
    """Raised when a DSL expression is outside the supported v1 grammar."""


@dataclass(frozen=True)
class FieldRef:
    value: str


@dataclass(frozen=True)
class IntLiteral:
    value: int


@dataclass(frozen=True)
class SumExpr:
    terms: tuple[FieldRef | IntLiteral, ...]


@dataclass(frozen=True)
class ComparisonExpr:
    left: FieldRef | IntLiteral | SumExpr
    operator: str
    right: FieldRef | IntLiteral | SumExpr


@dataclass(frozen=True)
class InSetExpr:
    field: FieldRef
    values: tuple[int, ...]


@dataclass(frozen=True)
class AndExpr:
    items: tuple["Expr", ...]


@dataclass(frozen=True)
class ImplicationExpr:
    antecedent: "Expr"
    consequent: "Expr"


Expr = ComparisonExpr | InSetExpr | AndExpr | ImplicationExpr


@dataclass(frozen=True)
class RuleAnalysis:
    expression: str
    ast: Expr
    depends_on_fields: list[str]


_TOKEN_RE = re.compile(
    r"""
    (?P<SPACE>\s+)
    |(?P<ARROW>->)
    |(?P<LE><=)
    |(?P<GE>>=)
    |(?P<EQ>==)
    |(?P<NE>!=)
    |(?P<LT><)
    |(?P<GT>>)
    |(?P<LBRACE>\{)
    |(?P<RBRACE>\})
    |(?P<COMMA>,)
    |(?P<PLUS>\+)
    |(?P<NUMBER>\d+)
    |(?P<IDENT>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)
    |(?P<MISMATCH>.)
    """,
    re.VERBOSE,
)


_COMPARISON_TOKENS = {"EQ": "==", "NE": "!=", "LT": "<", "LE": "<=", "GT": ">", "GE": ">="}
_KEYWORDS = {"and", "in"}


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    position: int


def _tokenize(expression: str) -> list[_Token]:
    tokens: list[_Token] = []
    for match in _TOKEN_RE.finditer(expression):
        kind = match.lastgroup or "MISMATCH"
        value = match.group()
        if kind == "SPACE":
            continue
        if kind == "MISMATCH":
            raise RuleSyntaxError(f"Unsupported token at position {match.start()}: {value!r}")
        if kind == "IDENT" and value in _KEYWORDS:
            tokens.append(_Token(value.upper(), value, match.start()))
            continue
        tokens.append(_Token(kind, value, match.start()))
    return tokens


class _Parser:
    def __init__(self, expression: str):
        self.expression = expression
        self.tokens = _tokenize(expression)
        self.index = 0

    def parse(self) -> Expr:
        expr = self._parse_implication()
        if self._peek() is not None:
            token = self._peek()
            raise RuleSyntaxError(f"Unexpected token at position {token.position}: {token.value!r}")
        return expr

    def _peek(self) -> _Token | None:
        if self.index >= len(self.tokens):
            return None
        return self.tokens[self.index]

    def _advance(self) -> _Token:
        token = self._peek()
        if token is None:
            raise RuleSyntaxError("Unexpected end of expression")
        self.index += 1
        return token

    def _accept(self, kind: str) -> _Token | None:
        token = self._peek()
        if token is not None and token.kind == kind:
            self.index += 1
            return token
        return None

    def _expect(self, kind: str) -> _Token:
        token = self._advance()
        if token.kind != kind:
            raise RuleSyntaxError(f"Expected {kind} at position {token.position}, got {token.value!r}")
        return token

    def _parse_implication(self) -> Expr:
        left = self._parse_and()
        if self._accept("ARROW"):
            right = self._parse_and()
            if self._peek() is not None:
                token = self._peek()
                raise RuleSyntaxError(f"Only one implication is allowed (at position {token.position})")
            return ImplicationExpr(antecedent=left, consequent=right)
        return left

    def _parse_and(self) -> Expr:
        items = [self._parse_comparison()]
        while self._accept("AND"):
            items.append(self._parse_comparison())
        if len(items) == 1:
            return items[0]
        return AndExpr(items=tuple(items))

    def _parse_comparison(self) -> Expr:
        left = self._parse_sum()
        token = self._advance()
        if token.kind == "IN":
            if not isinstance(left, FieldRef):
                raise RuleSyntaxError("'in' requires a field reference on the left-hand side")
            values = self._parse_set_literal()
            return InSetExpr(field=left, values=values)
        operator = _COMPARISON_TOKENS.get(token.kind)
        if operator is None:
            raise RuleSyntaxError(f"Expected comparison operator at position {token.position}, got {token.value!r}")
        right = self._parse_sum()
        return ComparisonExpr(left=left, operator=operator, right=right)

    def _parse_sum(self) -> FieldRef | IntLiteral | SumExpr:
        terms = [self._parse_term()]
        while self._accept("PLUS"):
            terms.append(self._parse_term())
        if len(terms) == 1:
            return terms[0]
        return SumExpr(terms=tuple(terms))

    def _parse_term(self) -> FieldRef | IntLiteral:
        token = self._advance()
        if token.kind == "IDENT":
            return FieldRef(token.value)
        if token.kind == "NUMBER":
            return IntLiteral(int(token.value))
        raise RuleSyntaxError(f"Expected field or integer at position {token.position}, got {token.value!r}")

    def _parse_set_literal(self) -> tuple[int, ...]:
        self._expect("LBRACE")
        values = [self._expect("NUMBER").value]
        while self._accept("COMMA"):
            values.append(self._expect("NUMBER").value)
        self._expect("RBRACE")
        return tuple(int(value) for value in values)


def parse_rule_expression(expression: str) -> Expr:
    raw = (expression or "").strip()
    if not raw:
        raise RuleSyntaxError("Expression is empty")
    return _Parser(raw).parse()


def _collect_fields(expr: Expr | FieldRef | IntLiteral | SumExpr, ordered: list[str]) -> None:
    if isinstance(expr, FieldRef):
        if expr.value not in ordered:
            ordered.append(expr.value)
        return
    if isinstance(expr, IntLiteral):
        return
    if isinstance(expr, SumExpr):
        for term in expr.terms:
            _collect_fields(term, ordered)
        return
    if isinstance(expr, ComparisonExpr):
        _collect_fields(expr.left, ordered)
        _collect_fields(expr.right, ordered)
        return
    if isinstance(expr, InSetExpr):
        _collect_fields(expr.field, ordered)
        return
    if isinstance(expr, AndExpr):
        for item in expr.items:
            _collect_fields(item, ordered)
        return
    if isinstance(expr, ImplicationExpr):
        _collect_fields(expr.antecedent, ordered)
        _collect_fields(expr.consequent, ordered)
        return
    raise TypeError(f"Unsupported expression node: {type(expr)!r}")


def analyze_rule_expression(expression: str) -> RuleAnalysis:
    ast = parse_rule_expression(expression)
    depends_on_fields: list[str] = []
    _collect_fields(ast, depends_on_fields)
    return RuleAnalysis(expression=expression, ast=ast, depends_on_fields=depends_on_fields)


def extract_depends_on_fields(expression: str) -> list[str]:
    return analyze_rule_expression(expression).depends_on_fields


def _render_term(term: FieldRef | IntLiteral | SumExpr, field_ref_resolver: Callable[[str], str]) -> str:
    if isinstance(term, FieldRef):
        return field_ref_resolver(term.value)
    if isinstance(term, IntLiteral):
        return str(term.value)
    if isinstance(term, SumExpr):
        return " + ".join(_render_term(item, field_ref_resolver) for item in term.terms)
    raise TypeError(f"Unsupported term node: {type(term)!r}")


def render_rule_as_c(expr: Expr, field_ref_resolver: Callable[[str], str]) -> str:
    if isinstance(expr, ComparisonExpr):
        return (
            f"({_render_term(expr.left, field_ref_resolver)} {expr.operator} "
            f"{_render_term(expr.right, field_ref_resolver)})"
        )
    if isinstance(expr, InSetExpr):
        left = field_ref_resolver(expr.field.value)
        return "(" + " || ".join(f"({left} == {value})" for value in expr.values) + ")"
    if isinstance(expr, AndExpr):
        return "(" + " && ".join(render_rule_as_c(item, field_ref_resolver) for item in expr.items) + ")"
    if isinstance(expr, ImplicationExpr):
        left = render_rule_as_c(expr.antecedent, field_ref_resolver)
        right = render_rule_as_c(expr.consequent, field_ref_resolver)
        return f"((!{left}) || {right})"
    raise TypeError(f"Unsupported expression node: {type(expr)!r}")


def render_rule_expression_as_c(expression: str, field_ref_resolver: Callable[[str], str]) -> str:
    return render_rule_as_c(parse_rule_expression(expression), field_ref_resolver)
