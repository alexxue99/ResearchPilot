"""Short assessment descriptions grounded in recorded evidence."""
from __future__ import annotations

from .models import ResearchState


def assessment_evidence_summary(state: ResearchState) -> str:
    literature = list(state.structured_evidence)
    experiments = list(state.experimental_evidence)
    if state.assessment:
        for group in (state.assessment.literature_support,
                      state.assessment.literature_contradictions,
                      state.assessment.literature_qualifications,
                      state.assessment.literature_related):
            literature.extend(item for item in group if item not in literature)
        for group in (state.assessment.experimental_support,
                      state.assessment.experimental_contradictions):
            experiments.extend(item for item in group if item not in experiments)

    relations = {item.relation_to_conjecture for item in literature}
    if 'supports' in relations and 'contradicts' in relations:
        literature_text = 'Literature contains both supporting and contradicting results for the conjecture.'
    elif 'contradicts' in relations:
        literature_text = 'Literature contains results contradicting the conjecture.'
    elif 'supports' in relations:
        literature_text = 'Literature contains results supporting the conjecture'
        literature_text += ' with qualifications or additional assumptions.' if 'qualifies' in relations else '.'
    elif 'qualifies' in relations:
        literature_text = 'Related literature identifies qualifications or additional assumptions for the conjecture.'
    elif literature:
        literature_text = 'Related literature results were found, but they do not directly support or contradict the conjecture.'
    else:
        literature_text = 'No relevant literature results found in this investigation.'

    relations = {item.relation_to_conjecture for item in experiments}
    if 'supports' in relations and 'contradicts' in relations:
        experiment_text = 'Experiments give mixed results, supporting the conjecture in some tested cases and contradicting it in others.'
    elif 'contradicts' in relations:
        experiment_text = 'Experiments suggest the conjecture is false in the tested cases.'
    elif 'supports' in relations:
        experiment_text = 'Experiments support the conjecture in the tested cases.'
    elif experiments:
        experiment_text = 'Experimental results are inconclusive about the conjecture.'
    elif any(result.status == 'completed' for result in state.experiments_completed):
        experiment_text = 'Experiments completed, but their implications for the conjecture have not been assessed.'
    else:
        experiment_text = 'No completed experimental results are available.'
    if relations & {'supports', 'contradicts'} and 'inconclusive' in relations:
        experiment_text += ' Other experimental results are inconclusive.'
    return f'{literature_text} {experiment_text}'
