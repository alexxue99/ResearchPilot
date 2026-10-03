from __future__ import annotations


def select_tools(question: str) -> list[str]:
    text = question.lower()
    if text.startswith("plan ") and not any(term in text for term in ("experiment", "paper", "plot", "theorem")):
        return ["planning"]
    tools: list[str] = []
    literature_intent = any(term in text for term in ("paper", "literature", "known", "theorem", "research", "citation"))
    if literature_intent:
        tools += ["literature_search", "rag"]
    experiment_intent = (
        any(term in text for term in ("experiment", "empirical", "plot", "numerical", "reproduce", "training behavior", "study variance"))
        or ("convergence" in text and not literature_intent)
        or ("compare" in text and not literature_intent)
    )
    if experiment_intent:
        tools += ["experiment_design", "python", "statistics"]
    if any(term in text for term in ("plot", "curve", "visual")) or ("convergence" in text and not literature_intent):
        tools.append("plotting")
    if any(term in text for term in ("derive", "equation", "symbolic", "prove", "theorem")):
        tools.append("symbolic_math")
    return list(dict.fromkeys(tools or ["planning"]))


def material_ambiguities(question: str) -> list[str]:
    text = question.lower().strip()
    issues = []
    if len(text.split()) < 4:
        issues.append("The research objective is too short to determine a defensible scope.")
    if "compare" in text and not any(metric in text for metric in
                                      ("convergence", "accuracy", "runtime", "error", "cost", "variance", "coverage")):
        issues.append("The comparison metric is unspecified.")
    if "this empirical dataset" in text or ("this dataset" in text and not any(
            term in text for term in ("uploaded", "attached", "path"))):
        issues.append("The referenced dataset is unavailable; attach it or provide an accessible location.")
    if "these papers" in text and not any(term in text for term in ("uploaded", "attached", "doi", "arxiv")):
        issues.append("The referenced papers are unavailable; attach them or provide persistent identifiers.")
    if "impossible infinite" in text:
        issues.append("The requested unbounded experiment must be replaced by a finite budget and stopping rule.")
    if "one random" in text and "trial" in text:
        issues.append("A single stochastic trial is insufficient for a supported conclusion; specify or accept repeated seeds.")
    return issues
