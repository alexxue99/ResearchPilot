import tempfile
import unittest
from pathlib import Path

from researchpilot.assessment_summary import assessment_evidence_summary
from researchpilot.models import (Conjecture, ConjectureAssessment, EvidenceItem,
                                 ExperimentalEvidence, ExperimentResult, ResearchState, Status)
from researchpilot.reporting import render_conjecture_report
from researchpilot.storage import ResearchRepository


class AssessmentSummaryTests(unittest.TestCase):
    def setUp(self):
        self.state = ResearchState(question='The conjecture', objective='Assess it',
                                   conjecture=Conjecture('The conjecture', 'The conjecture'),
                                   confidence=0.5)

    def literature(self, relation):
        return EvidenceItem('paper', 'Paper', 'A result', 'theorem', relation)

    def experiment(self, relation):
        return ExperimentalEvidence('run', 'A measured finding', relation)

    def test_absence_does_not_claim_experimental_direction(self):
        summary = assessment_evidence_summary(self.state)
        self.assertIn('No relevant literature results found', summary)
        self.assertIn('No completed experimental results', summary)

    def test_literature_directions_and_qualifications(self):
        cases = [('supports', 'supporting'), ('contradicts', 'contradicting'),
                 ('qualifies', 'qualifications'), ('neutral', 'do not directly support')]
        for relation, description in cases:
            with self.subTest(relation=relation):
                self.state.structured_evidence = [self.literature(relation)]
                self.assertIn(description, assessment_evidence_summary(self.state))
        self.state.structured_evidence = [self.literature('supports'), self.literature('contradicts')]
        self.assertIn('both supporting and contradicting', assessment_evidence_summary(self.state))
        self.state.structured_evidence = [self.literature('supports'), self.literature('qualifies')]
        self.assertIn('with qualifications', assessment_evidence_summary(self.state))

    def test_experimental_directions_are_independent_of_label(self):
        for relation, description in [('supports', 'support the conjecture in the tested cases'),
                                      ('contradicts', 'suggest the conjecture is false'),
                                      ('inconclusive', 'results are inconclusive')]:
            with self.subTest(relation=relation):
                self.state.experimental_evidence = [self.experiment(relation)]
                report = render_conjecture_report(self.state)
                self.assertIn('assessment: Qualitative judgment not yet generated** No relevant literature', report)
                self.assertIn(description, report)
                self.assertNotIn('Numerical experiments alone', report)
                self.assertNotIn('an unresolved assessment', report)
                self.assertEqual(self.state.to_dict()['assessment_evidence_summary'],
                                 assessment_evidence_summary(self.state))
        self.state.experimental_evidence = [self.experiment('supports'), self.experiment('contradicts')]
        self.assertIn('mixed results', assessment_evidence_summary(self.state))

    def test_completed_but_uninterpreted_is_not_absent(self):
        self.state.experiments_completed = [ExperimentResult('run', 'design', 'completed', {}, {})]
        self.assertIn('implications for the conjecture have not been assessed',
                      assessment_evidence_summary(self.state))

    def test_saved_assessment_only_evidence_and_old_report_are_updated(self):
        self.state.status = Status.COMPLETED
        self.state.assessment = ConjectureAssessment(
            literature_related=[self.literature('neutral')],
            experimental_contradictions=[self.experiment('contradicts')])
        self.state.confidence_rationale = 'No model assessment was available; the recorded evidence has not received a calibrated prediction.'
        self.state.report = '# Assessment\n\n**ResearchPilot assessment: uncertain.** This is an unresolved assessment based on the available evidence.\n\n## Related evidence'
        with tempfile.TemporaryDirectory() as temp:
            repository = ResearchRepository(Path(temp) / 'state.sqlite')
            repository.save(self.state)
            saved = repository.get(self.state.id)
        self.assertIn('Related literature results were found', saved.report)
        self.assertIn('Experiments suggest the conjecture is false', saved.report)
        self.assertNotIn('No model assessment was available', saved.report)
        self.assertIn('Experiments suggest the conjecture is false', saved.confidence_rationale)
