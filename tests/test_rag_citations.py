import unittest

from researchpilot.citations import audit_grounding, verify_passage_support
from researchpilot.models import Evidence, EvidenceKind, ResearchState
from researchpilot.rag import LocalRetriever, semantic_chunks


class RagTests(unittest.TestCase):
    def test_section_chunking_and_retrieval(self):
        chunks = semantic_chunks("p1", "Paper", "# Introduction\nRandomized methods.\n\n# Results\nKaczmarz converges exponentially.")
        retriever = LocalRetriever(); retriever.add(chunks)
        result = retriever.search("Kaczmarz exponential", 1)
        self.assertIn("converges", result[0][0].text)

    def test_orphan_literature_claim_is_rejected(self):
        state = ResearchState("q", "o")
        state.evidence.append(Evidence("e", EvidenceKind.LITERATURE, "claim", source_id="missing"))
        self.assertTrue(audit_grounding(state))

    def test_passage_verification_identifies_supporting_chunk(self):
        from researchpilot.models import Source
        state = ResearchState("q", "o"); state.sources.append(Source("s", "Paper", doi="x", verified=True))
        state.evidence.append(Evidence("e", EvidenceKind.LITERATURE,
            "Expected error converges exponentially under a scaled condition bound.", source_id="s"))
        chunk = semantic_chunks("s", "Paper", "# Theorem\nExpected error converges exponentially under a scaled condition bound.")[0]
        verification = verify_passage_support(state.evidence[0], [chunk])
        self.assertTrue(verification['supported'])
        self.assertEqual(verification['chunk_id'], chunk.id)
        self.assertEqual(verification['score'], 1.0)

    def test_passage_verification_rejects_irrelevant_chunk(self):
        from researchpilot.models import Source
        state = ResearchState("q", "o"); state.sources.append(Source("s", "Paper", doi="x", verified=True))
        state.evidence.append(Evidence("e", EvidenceKind.LITERATURE, "Exponential convergence theorem.", source_id="s"))
        chunk = semantic_chunks("s", "Paper", "# Results\nThe dataset contains images of birds.")[0]
        verification = verify_passage_support(state.evidence[0], [chunk])
        self.assertFalse(verification['supported'])
        self.assertLess(verification['score'], verification['threshold'])
