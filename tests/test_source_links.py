import unittest

from researchpilot.conjecture_pipeline import deduplicate_sources
from researchpilot.models import Source
from researchpilot.source_links import arxiv_id_from_link, arxiv_url, source_url


class SourceLinkTests(unittest.TestCase):
    def test_web_sources_link_to_retrieved_pages_and_reject_executable_urls(self):
        self.assertEqual(source_url(Source("web:theorem", "Theorem", url="https://example.org/theorem")),
                         "https://example.org/theorem")
        for url in ("javascript:alert(1)", "data:text/html,<script>", "https://user:secret@example.org", "https://["):
            with self.subTest(url=url):
                self.assertIsNone(source_url(Source("web:bad", "Invalid reference", url=url)))

    def test_arxiv_link_accepts_abstract_and_pdf_urls(self):
        self.assertEqual(arxiv_id_from_link("https://arxiv.org/abs/2401.12345"), "2401.12345")
        self.assertEqual(arxiv_id_from_link("https://arxiv.org/pdf/math/0702226.pdf"), "math/0702226")

    def test_arxiv_link_rejects_other_hosts_and_extra_url_parts(self):
        for url in ("https://example.org/abs/2401.12345", "http://arxiv.org/abs/2401.12345",
                    "https://arxiv.org/abs/2401.12345?download=1", "https://arxiv.org/abs/not-an-id"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                arxiv_id_from_link(url)

    def test_arxiv_identifier_takes_precedence_over_publisher_url(self):
        source = Source("doi:10.1/example", "Paper", doi="10.1/example",
                        arxiv_id="2401.12345", url="https://doi.org/10.1/example")
        self.assertEqual(arxiv_url(source), "https://arxiv.org/abs/2401.12345")

    def test_arxiv_pdf_and_legacy_ids_link_to_abstract_page(self):
        source = Source("arxiv:math/0702226", "Paper", url="https://arxiv.org/pdf/math/0702226")
        self.assertEqual(arxiv_url(source), "https://arxiv.org/abs/math/0702226")

    def test_sources_without_arxiv_version_have_no_link(self):
        self.assertIsNone(arxiv_url(Source("doi:10.1/example", "Paper",
                                            url="https://doi.org/10.1/example")))

    def test_arxiv_doi_links_to_arxiv(self):
        source = Source("doi:10.48550/arXiv.2401.12345", "Paper",
                        doi="10.48550/arXiv.2401.12345")
        self.assertEqual(arxiv_url(source), "https://arxiv.org/abs/2401.12345")

    def test_duplicate_papers_keep_arxiv_version_and_doi(self):
        doi = Source("doi:10.1/example", "A reproducible numerical study", doi="10.1/example",
                     url="https://doi.org/10.1/example", verified=True)
        preprint = Source("arxiv:2401.12345", doi.title, abstract="A useful abstract",
                          arxiv_id="2401.12345", url="https://arxiv.org/pdf/2401.12345", verified=True)
        selected = deduplicate_sources([doi, preprint])
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].id, preprint.id)
        self.assertEqual(selected[0].doi, doi.doi)

    def test_attached_duplicate_keeps_chunk_source_id(self):
        attached = Source("sha256:uploaded", "A reproducible numerical study")
        discovered = Source("arxiv:2401.12345", attached.title, abstract="A useful abstract",
                            arxiv_id="2401.12345", verified=True)
        selected = deduplicate_sources([attached, discovered], [attached.id])
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].id, attached.id)
        self.assertEqual(selected[0].abstract, discovered.abstract)


if __name__ == "__main__":
    unittest.main()
