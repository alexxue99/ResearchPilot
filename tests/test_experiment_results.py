import unittest

from researchpilot.experiment_results import discover_measurements, normalize_measurements


class ExperimentResultParsingTests(unittest.TestCase):
    def test_flat_and_grouped_results(self):
        flat = {"control": [1, 2, 3], "treatment": [2, 4, 6], "higher_supports": True}
        result = normalize_measurements(flat, discover_measurements(flat), 20)
        self.assertEqual(result[0]["relative_effect"], 1.0)

        grouped = {"control_iterations": {"32": [100, 110, 120], "64": [200, 210, 220]},
                   "treatment_iterations": {"32": [800, 900, 1000], "64": [1200, 1300, 1400]},
                   "higher_supports": {"32": True, "64": True},
                   "control_censored": {"32": [False]*3, "64": [False]*3},
                   "treatment_censored": {"32": [False]*3, "64": [False]*3}}
        result = normalize_measurements(grouped, discover_measurements(grouped), 20)
        self.assertEqual([item["label"] for item in result], ["32", "64"])
        self.assertTrue(all(item["relative_effect"] > 0 for item in result))
        grouped["control"] = grouped.pop("control_iterations")
        grouped["treatment"] = grouped.pop("treatment_iterations")
        self.assertEqual(len(normalize_measurements(grouped, discover_measurements(grouped), 20)), 2)

    def test_model_mapping_selects_existing_values_only(self):
        raw = {"runs": {"control": [3, 4, 5], "changed": [6, 7, 8]}}
        mapping = [{"label": "run", "control_path": "/runs/control",
                    "treatment_path": "/runs/changed", "higher_supports": True}]
        self.assertIsNone(discover_measurements(raw))
        self.assertEqual(normalize_measurements(raw, mapping, 20)[0]["treatment"], [6, 7, 8])
        mapping[0]["treatment_path"] = "/runs/invented"
        with self.assertRaises(ValueError):
            normalize_measurements(raw, mapping, 20)

    def test_censored_or_nonfinite_results_are_rejected(self):
        raw = {"control_iterations": {"32": [1, 2, 3]},
               "treatment_iterations": {"32": [4, 5, 6]},
               "higher_supports": {"32": True},
               "treatment_censored": {"32": [False, True, False]}}
        with self.assertRaisesRegex(ValueError, "censored"):
            normalize_measurements(raw, discover_measurements(raw), 20)
        raw = {"control": [1, 2, float("nan")], "treatment": [4, 5, 6], "higher_supports": True}
        with self.assertRaisesRegex(ValueError, "invalid"):
            normalize_measurements(raw, discover_measurements(raw), 20)


if __name__ == "__main__":
    unittest.main()
