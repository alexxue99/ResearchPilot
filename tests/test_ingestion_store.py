import tempfile
import unittest
import importlib.util
from pathlib import Path

from researchpilot.citations import verify_passage_support
from researchpilot.ingestion import PaperIngestor
from researchpilot.models import Evidence, EvidenceKind, Source
from researchpilot.rag_store import PersistentChunkStore


class IngestionStoreTests(unittest.TestCase):
    def test_markdown_ingestion_persists_provenance_and_retrieves(self):
        with tempfile.TemporaryDirectory() as tmp:
            paper = Path(tmp) / "paper.md"
            paper.write_text("# Introduction\nRandomized projection methods solve linear systems.\n\n# Theorem 1\nThe expected error converges exponentially under a scaled condition bound.", encoding="utf-8")
            source = Source("doi:test", "Projection Paper", doi="test", url="https://doi.org/test", verified=True)
            parsed = PaperIngestor().ingest(paper, source)
            self.assertGreaterEqual(len(parsed.chunks), 2)
            store = PersistentChunkStore(Path(tmp) / "rag.sqlite")
            self.assertEqual(store.add(parsed.chunks), len(parsed.chunks))
            results = store.search("expected exponential convergence scaled condition", 1)
            self.assertEqual(results[0][0].source_id, source.id)
            self.assertIn("Theorem", results[0][0].section)

    def test_passage_verification_requires_matching_source_and_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            paper = Path(tmp) / "paper.txt"; paper.write_text("Expected error converges exponentially with the scaled condition number.", encoding="utf-8")
            source = Source("s", "Paper")
            chunks = PaperIngestor().ingest(paper, source).chunks
            evidence = Evidence("e", EvidenceKind.LITERATURE, "Expected error has exponential convergence governed by scaled condition.", source_id="s")
            self.assertTrue(verify_passage_support(evidence, chunks)["supported"])
            evidence.source_id = "other"
            self.assertFalse(verify_passage_support(evidence, chunks)["supported"])

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "pypdf optional dependency not installed")
    def test_pdf_ingestion_retains_page_count_and_extraction_warning(self):
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blank.pdf"; writer = PdfWriter(); writer.add_blank_page(200, 200)
            with path.open("wb") as handle: writer.write(handle)
            parsed = PaperIngestor().ingest(path, Source("pdf", "Blank"))
            self.assertEqual(parsed.page_count, 1)
            self.assertIn("page 1 contained no extractable text", parsed.warnings)
