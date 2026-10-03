from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class SymbolicResult:
    operation: str
    input_expression: str
    result: str
    verified: bool
    assumptions: dict[str, str]


class SymbolicMath:
    """Restricted SymPy wrapper for auditable algebraic checks; no arbitrary eval."""

    def __init__(self, symbols: dict[str, str] | None = None) -> None:
        try:
            import sympy
        except ImportError as exc:
            raise RuntimeError("Install ResearchPilot with the 'math' extra for symbolic verification") from exc
        self.sympy = sympy
        specs = symbols or {name: "real" for name in ("x", "y", "z", "a", "b", "c", "n")}
        self.assumptions = specs
        self.locals = {name: sympy.Symbol(name, **({kind: True} if kind else {})) for name, kind in specs.items()}

    def parse(self, expression: str):
        if len(expression) > 4000: raise ValueError("symbolic expression is too long")
        from sympy.parsing.sympy_parser import parse_expr, standard_transformations, implicit_multiplication_application
        return parse_expr(expression, local_dict=self.locals, global_dict={**self.sympy.__dict__, "__builtins__": {}},
                          transformations=standard_transformations + (implicit_multiplication_application,), evaluate=True)

    def simplify(self, expression: str) -> SymbolicResult:
        parsed = self.parse(expression); result = self.sympy.simplify(parsed)
        return SymbolicResult("simplify", expression, str(result), self.sympy.simplify(parsed - result) == 0, self.assumptions)

    def differentiate(self, expression: str, variable: str) -> SymbolicResult:
        if variable not in self.locals: raise ValueError(f"undeclared symbol: {variable}")
        result = self.sympy.diff(self.parse(expression), self.locals[variable])
        return SymbolicResult("differentiate", expression, str(result), True, self.assumptions)

    def equivalent(self, left: str, right: str) -> SymbolicResult:
        difference = self.sympy.simplify(self.parse(left) - self.parse(right))
        return SymbolicResult("equivalence", f"{left} == {right}", str(difference), difference == 0, self.assumptions)

