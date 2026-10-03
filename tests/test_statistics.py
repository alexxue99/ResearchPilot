import unittest

from researchpilot.statistics import bootstrap_mean_ci, cohens_d, summarize


class StatisticsTests(unittest.TestCase):
    def test_summary(self):
        result = summarize([1, 2, 3])
        self.assertEqual(result["mean"], 2)
        self.assertEqual(result["median"], 2)
        self.assertEqual(result["variance"], 1)

    def test_empty_rejected(self):
        with self.assertRaises(ValueError): summarize([])

    def test_bootstrap_is_reproducible(self):
        self.assertEqual(bootstrap_mean_ci([1, 2, 3], 100, 4), bootstrap_mean_ci([1, 2, 3], 100, 4))

    def test_effect_size(self):
        self.assertLess(cohens_d([1, 2, 3], [4, 5, 6]), 0)

