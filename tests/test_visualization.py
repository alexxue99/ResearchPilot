import tempfile
import unittest
from pathlib import Path

from researchpilot.experiments.visualization import validate_visualization_svg


class VisualizationTests(unittest.TestCase):
    def test_accepts_rich_static_visualization(self):
        svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 200" role="img">
          <title>Heatmap, scatterplot, and directed graph</title>
          <defs>
            <linearGradient id="color"><stop offset="0%" stop-color="blue"/>
              <stop offset="100%" stop-color="red" stop-opacity="0.8"/></linearGradient>
            <radialGradient id="radial" cx="0.5" cy="0.5" r="0.5"/>
            <clipPath id="bounds" clipPathUnits="userSpaceOnUse"><rect width="200" height="100"/></clipPath>
            <pattern id="hatch" width="5" height="5" patternUnits="userSpaceOnUse">
              <path d="M0 0L5 5" stroke="gray"/></pattern>
            <marker id="arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
              <path d="M0 0L6 3L0 6Z" fill="black"/></marker>
          </defs>
          <g clip-path="url(#bounds)">
            <rect width="50" height="50" fill="url(#color)"/>
            <rect x="50" width="50" height="50" fill="url(#hatch)"/>
            <circle cx="20" cy="30" r="5" fill="url(#radial)" fill-opacity="0.5"/>
            <path d="M20 30L100 80" stroke="black" stroke-dasharray="5,4"
              stroke-dashoffset="2" marker-end="url(#arrow)" fill="none"/>
          </g>
          <text class="label" x="10" y="150" font-style="italic">Measured values</text>
        </svg>'''
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "visualization.svg"
            path.write_text(svg)
            validate_visualization_svg(path)

    def test_rejects_external_references_and_active_content(self):
        cases = [
            '<rect fill="url(https://example.org/a.svg#color)"/>',
            '<rect fill="url(data:image/svg+xml,bad)"/>',
            '<rect fill="url(#missing)"/>',
            '<rect id="wrong"/><rect fill="url(#wrong)"/>',
            '<rect id="duplicate"/><circle id="duplicate"/>',
            '<rect style="fill:red"/>',
            '<style>rect {fill:red}</style>',
            '<image href="https://example.org/a.png"/>',
            '<use href="#shape"/>',
            '<foreignObject/>',
            '<animate attributeName="fill"/>',
            '<rect onclick="alert(1)"/>',
            '<rect xmlns:x="http://www.w3.org/1999/xlink" x:href="#shape"/>',
            '<g xmlns="https://example.org/other"/>',
            '<rect clip-path="url(#missing) red"/>',
            '<rect fill="URL(#missing)"/>',
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "visualization.svg"
            for content in cases:
                with self.subTest(content=content):
                    path.write_text('<svg xmlns="http://www.w3.org/2000/svg">' + content + '</svg>')
                    with self.assertRaises(ValueError):
                        validate_visualization_svg(path)

    def test_rejection_identifies_attribute_for_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "visualization.svg"
            path.write_text('<svg><rect unknown="value"/></svg>')
            with self.assertRaisesRegex(ValueError, 'unsupported attribute unknown on rect'):
                validate_visualization_svg(path)

    def test_accepts_simple_chart_and_rejects_active_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "visualization.svg"
            path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" '
                            'height="5"/><text x="0" y="7">Result</text></svg>')
            validate_visualization_svg(path)
            path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
            with self.assertRaises(ValueError):
                validate_visualization_svg(path)
            path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect '
                            'onload="alert(1)"/></svg>')
            with self.assertRaises(ValueError):
                validate_visualization_svg(path)
