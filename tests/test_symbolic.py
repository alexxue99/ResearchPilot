import importlib.util
import unittest

from researchpilot.symbolic import SymbolicMath


@unittest.skipUnless(importlib.util.find_spec("sympy"), "sympy optional dependency not installed")
class SymbolicMathTests(unittest.TestCase):
    def test_simplify_and_equivalence(self):
        tool = SymbolicMath({"x": "real"})
        self.assertEqual(tool.simplify("(x + 1)**2 - x**2 - 2*x").result, "1")
        self.assertTrue(tool.equivalent("(x+1)**2", "x**2+2*x+1").verified)

    def test_differentiation_preserves_assumptions(self):
        result = SymbolicMath({"x": "real"}).differentiate("x**3", "x")
        self.assertEqual(result.result, "3*x**2")
        self.assertEqual(result.assumptions, {"x": "real"})
