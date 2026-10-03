import importlib.util
import tempfile
import unittest

from researchpilot.models import ResearchState
from researchpilot.observability import OpenTelemetryExporter, TraceExporter

try:
    HAS_OTEL_SDK = importlib.util.find_spec("opentelemetry.sdk") is not None
except ModuleNotFoundError:
    HAS_OTEL_SDK = False

@unittest.skipUnless(HAS_OTEL_SDK, "optional OpenTelemetry SDK")
class TelemetryTests(unittest.TestCase):
    def test_export_deduplicates_and_excludes_research_contents(self):
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        memory = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(memory))
        exporter = OpenTelemetryExporter(provider)
        state = ResearchState("private question", "private objective")
        state.record("execute_python", "failed", "private summary", inputs={"api_key": "secret", "paper": "private"}, latency_ms=10)
        exporter.export(state); exporter.export(state)
        state.record("report", "completed", "private report")
        exporter.export(state)
        spans = memory.get_finished_spans()
        self.assertEqual(len(spans), 2)
        self.assertEqual(spans[0].status.status_code.name, "ERROR")
        self.assertEqual(spans[0].end_time - spans[0].start_time, 10_000_000)
        self.assertNotIn("private", str(spans[0].attributes))
        self.assertNotIn("secret", str(spans[0].attributes))
        exporter.close()

    def test_local_snapshot_marks_unknown_pricing(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            state = ResearchState("q", "o", cost_estimate_complete=False)
            state.record("call", "completed", "recorded", inputs={"api_key": "secret"})
            path = TraceExporter(tmp).export(state)
            payload = json.loads(path.read_text())
            self.assertFalse(payload["cost_estimate_complete"])
            self.assertEqual(payload["trace"][0]["inputs"]["api_key"], "[REDACTED]")
