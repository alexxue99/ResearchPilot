import unittest

from researchpilot.latex_report import latex_source, markdown_to_latex


class LatexReportTests(unittest.TestCase):
    def test_sections_math_links_and_code_are_converted(self):
        source = latex_source(
            '# Assessment\n\n**Supported** for $x^2$.\n\n'
            '$$\\frac{a}{b} \\leq 1$$\n\n'
            '- [Paper](https://arxiv.org/abs/2401.12345)\n\n'
            '```python\nprint("a & b")\n```',
            'Does $x^2$ hold?')
        self.assertIn(r'\section{Assessment}', source)
        self.assertIn(r'\textbf{Supported}', source)
        self.assertIn('$x^2$', source)
        self.assertIn(r'\[\frac{a}{b} \leq 1\]', source)
        self.assertIn(r'\href{https://arxiv.org/abs/2401.12345}{Paper}', source)
        self.assertIn(r'print("a\ \&\ b")', source)
        self.assertIn('Does $x^2$ hold?', source)

    def test_unsafe_math_command_is_rendered_as_text(self):
        source = markdown_to_latex(r'Claim $\input{secret}$ remains visible.')
        self.assertNotIn(r'\input{secret}', source)
        self.assertIn('input', source)

    def test_currency_is_not_parsed_as_math(self):
        source = markdown_to_latex('Cost: $0.001 plus $0.002.')
        self.assertIn(r'\$0.001 plus \$0.002', source)

    def test_numeric_math_does_not_swallow_report_prose(self):
        text = ('All 12 paired runs reached relative error $0.5$ later for the harmonic '
                'spectrum than for the flat spectrum. The median paired delay was 339.5 '
                'iterations. At iteration 5000, median relative error was about $0.0164$ '
                'for harmonic and numerically zero for flat.')
        self.assertEqual(markdown_to_latex(text), text + '\n\n')

    def test_math_is_parsed_before_markdown_emphasis_and_escaping(self):
        text = r'For $x_i + y_j$ and $\|Ax\|_2$, **the prose** stays formatted. $\mathbb{E}[x\mid y]$'
        source = markdown_to_latex(text)
        self.assertIn('$x_i + y_j$', source)
        self.assertIn(r'$\|Ax\|_2$', source)
        self.assertIn(r'$\mathbb{E}[x\mid y]$', source)
        self.assertIn(r'\textbf{the prose}', source)
        self.assertNotIn(r'\emph', source)

    def test_alternative_delimiters_and_multiline_display_math(self):
        source = markdown_to_latex(r'Error \(0.5\) and \[x_i + y_j\].' + '\n\n$$\nx_i + y_j\n$$')
        self.assertIn('$0.5$', source)
        self.assertIn(r'\[x_i + y_j\]', source)
        self.assertNotIn('textbackslash', source)

    def test_literal_dollars_and_code_remain_literal(self):
        source = markdown_to_latex(r'Pay \$5 or $10. Code: `$x_i$`.' + '\n\n```\n$0.5$\n```')
        self.assertIn(r'Pay \$5 or \$10.', source)
        self.assertIn(r'\texttt{\$x\_i\$}', source)
        self.assertIn(r'\$0.5\$', source)


if __name__ == '__main__':
    unittest.main()
