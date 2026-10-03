"""Human-readable references without changing provenance identifiers."""
from __future__ import annotations

import re

from .models import ResearchState
from .source_links import source_url
from .literature import has_article_title


def experiment_labels(state: ResearchState) -> dict[str, str]:
    labels = {design.id: f"Experiment {index}: {design.name.strip() or 'Untitled experiment'}"
              for index, design in enumerate(state.experiments_planned, 1)}
    for index, result in enumerate(state.experiments_completed, 1):
        labels[result.id] = labels.get(result.design_id, f"Experiment run {index}")
    return labels


def reference_context(state: ResearchState) -> dict:
    return {
        "source_references": [{"source_id": source.id, "title": source.title,
                               "url": source_url(source)} for source in state.sources],
        "experiment_labels": experiment_labels(state),
    }


def readable_references(text: str, state: ResearchState) -> str:
    """Resolve known IDs in prose, leaving code and Markdown link targets intact."""
    labels = experiment_labels(state)
    source_titles = {}
    for source in state.sources:
        if not has_article_title(source):
            continue
        for alias in (source.id, source.url, source_url(source),
                      f"doi:{source.doi}" if source.doi else None,
                      f"arxiv:{source.arxiv_id}" if source.arxiv_id else None):
            if alias:
                labels[alias] = source.title.strip()
                source_titles[alias] = source.title.strip()
    if not labels:
        return text
    identifiers = re.compile(r"(?<![\w])(?:" + "|".join(
        re.escape(key) for key in sorted(labels, key=len, reverse=True)) + r")(?![\w])")
    def escape_title(title):
        return re.sub(r"([\\\[\]])", r"\\\1", title)

    def replace_reference(found):
        identifier = found[0]
        title = labels[identifier]
        if identifier.startswith(("http://", "https://")):
            return f"[{escape_title(title)}]({identifier})"
        return title

    protected = re.compile(r"(`{3,})[\s\S]*?\1|`[^`\n]*`|\[[^\]\n]*\]\(([^\n)]*)\)")
    parts, start = [], 0
    for match in protected.finditer(text):
        link_target = match.group(2)
        link = (f"[{escape_title(source_titles[link_target])}]({link_target})"
                if link_target in source_titles else match[0])
        parts.extend((identifiers.sub(replace_reference, text[start:match.start()]), link))
        start = match.end()
    parts.append(identifiers.sub(replace_reference, text[start:]))
    return "".join(parts)
