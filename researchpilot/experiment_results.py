"""Ground executed measurements in the exact JSON written by an experiment."""
from __future__ import annotations

import math


def resolve_pointer(document, path: str):
    if path == "":
        return document
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("measurement path must be a JSON pointer")
    value = document
    for part in path[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            value = value[key]
        elif isinstance(value, list) and key.isdecimal():
            value = value[int(key)]
        else:
            raise ValueError(f"measurement path does not exist: {path}")
    return value


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def discover_measurements(payload: dict) -> list[dict] | None:
    """Recognize the published flat contract and dimension-keyed paired results."""
    if (isinstance(payload.get("control"), list) and isinstance(payload.get("treatment"), list)
            and "higher_supports" in payload):
        return [{"label": "overall", "control_path": "/control", "treatment_path": "/treatment",
                 "higher_supports_path": "/higher_supports",
                 "control_censored_path": "/control_censored" if "control_censored" in payload else "",
                 "treatment_censored_path": "/treatment_censored" if "treatment_censored" in payload else ""}]
    for control_name, treatment_name in (("control", "treatment"),
                                         ("control_iterations", "treatment_iterations")):
        control = payload.get(control_name)
        treatment = payload.get(treatment_name)
        direction = payload.get("higher_supports")
        if (isinstance(control, dict) and isinstance(treatment, dict) and
                isinstance(direction, dict) and control and control.keys() == treatment.keys() == direction.keys()):
            return [{"label": str(key), "control_path": f"/{control_name}/{_escape(str(key))}",
                     "treatment_path": f"/{treatment_name}/{_escape(str(key))}",
                     "higher_supports_path": f"/higher_supports/{_escape(str(key))}",
                     "control_censored_path": f"/control_censored/{_escape(str(key))}" if "control_censored" in payload else "",
                     "treatment_censored_path": f"/treatment_censored/{_escape(str(key))}" if "treatment_censored" in payload else ""}
                    for key in control]
    return None


def normalize_measurements(payload: dict, mappings: list[dict], max_repetitions: int) -> list[dict]:
    """Accept model-selected paths only when every measurement exists in raw output."""
    if not isinstance(payload, dict) or not isinstance(mappings, list) or not 1 <= len(mappings) <= 10:
        raise ValueError("experiment needs one to ten paired measurement groups")
    normalized = []
    labels = set()
    pairs = set()
    for mapping in mappings:
        if not isinstance(mapping, dict):
            raise ValueError("invalid measurement mapping")
        label = mapping.get("label")
        if not isinstance(label, str) or not label.strip() or len(label) > 100 or label in labels:
            raise ValueError("measurement groups need distinct labels")
        labels.add(label)
        pair = (mapping.get("control_path"), mapping.get("treatment_path"))
        if pair in pairs:
            raise ValueError("duplicate measurement group")
        pairs.add(pair)
        try:
            control = resolve_pointer(payload, mapping["control_path"])
            treatment = resolve_pointer(payload, mapping["treatment_path"])
            if mapping.get("higher_supports_path"):
                higher_supports = resolve_pointer(payload, mapping["higher_supports_path"])
            else:
                higher_supports = mapping["higher_supports"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("measurement mapping points to missing output") from exc
        if (not isinstance(control, list) or not isinstance(treatment, list) or
                not 1 <= len(control) == len(treatment) <= max_repetitions or
                not all(type(x) in (int, float) and math.isfinite(x) for x in control + treatment) or
                type(higher_supports) is not bool):
            raise ValueError("invalid or unbounded paired measurements")
        for side in ("control", "treatment"):
            path = mapping.get(f"{side}_censored_path")
            if f"{side}_censored" in payload and not path:
                raise ValueError("censoring flags must be mapped")
            if path:
                flags = resolve_pointer(payload, path)
                if not isinstance(flags, list) or len(flags) != len(control) or not all(type(x) is bool for x in flags):
                    raise ValueError("invalid censoring flags")
                if any(flags):
                    raise ValueError("censored observations cannot be treated as measured stopping times")
        baseline = sum(control) / len(control)
        changed = sum(treatment) / len(treatment)
        relative = ((changed - baseline) * (1 if higher_supports else -1)) / max(abs(baseline), 1e-9)
        normalized.append({"label": label, "control": control, "treatment": treatment,
                           "higher_supports": higher_supports, "relative_effect": relative})
    return normalized
