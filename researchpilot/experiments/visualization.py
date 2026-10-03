"""Validate generated SVG before it becomes a browser-visible artifact."""
from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree


ALLOWED_TAGS = {"svg", "g", "rect", "line", "polyline", "polygon", "path", "circle",
                "ellipse", "text", "tspan", "title", "desc", "defs", "clipPath",
                "linearGradient", "radialGradient", "stop", "pattern", "marker"}
ALLOWED_ATTRIBUTES = {"xmlns", "version", "width", "height", "viewBox", "x", "y", "x1", "y1",
                      "x2", "y2", "cx", "cy", "r", "rx", "ry", "points", "d", "fill",
                      "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "opacity",
                      "font-size", "font-family", "font-weight", "text-anchor", "transform",
                      "dominant-baseline", "id", "aria-label", "class", "role",
                      "preserveAspectRatio", "fill-opacity", "fill-rule", "stroke-opacity",
                      "stroke-dasharray", "stroke-dashoffset", "stroke-miterlimit",
                      "vector-effect", "paint-order", "font-style", "dx", "dy",
                      "letter-spacing", "word-spacing", "textLength", "lengthAdjust",
                      "clip-path", "clip-rule", "clipPathUnits", "gradientUnits",
                      "gradientTransform", "spreadMethod", "fx", "fy", "fr", "offset",
                      "stop-color", "stop-opacity", "patternUnits", "patternContentUnits",
                      "patternTransform", "marker-start", "marker-mid", "marker-end",
                      "markerWidth", "markerHeight", "markerUnits", "refX", "refY", "orient"}
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
MAX_SVG_BYTES = 1_000_000
LOCAL_REFERENCE_TARGETS = {
    "fill": {"linearGradient", "radialGradient", "pattern"},
    "stroke": {"linearGradient", "radialGradient", "pattern"},
    "clip-path": {"clipPath"},
    "marker-start": {"marker"}, "marker-mid": {"marker"}, "marker-end": {"marker"},
}
LOCAL_REFERENCE = re.compile(r"url\(#([A-Za-z_][A-Za-z0-9_.-]*)\)")


def visualization_svg_contract() -> dict:
    """JSON-serializable generator context derived from the validator's rules."""
    return {
        "allowed_elements": sorted(ALLOWED_TAGS),
        "allowed_attributes": sorted(ALLOWED_ATTRIBUTES),
        "namespace": SVG_NAMESPACE,
        "max_bytes": MAX_SVG_BYTES,
        "local_reference_targets": {key: sorted(value) for key, value in LOCAL_REFERENCE_TARGETS.items()},
        "rules": [
            "Use presentation attributes directly; class is a label and does not supply styling.",
            "Only url(#id) references to existing elements of the listed target types are allowed; no fallbacks.",
            "Referenced IDs must match [A-Za-z_][A-Za-z0-9_.-]* and all IDs must be unique.",
            "No scripts, event handlers, CSS style elements or attributes, animations, images, hrefs, external resources, or data URIs.",
            "Only unnamespaced attributes and SVG elements are allowed; no DOCTYPE or ENTITY declarations.",
        ],
    }


def validate_visualization_svg(path: str | Path) -> None:
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_SVG_BYTES:
        raise ValueError("visualization.svg is missing or exceeds the size limit")
    source = path.read_bytes()
    if b"<!DOCTYPE" in source.upper() or b"<!ENTITY" in source.upper():
        raise ValueError("visualization.svg contains an unsupported XML declaration")
    try:
        root = ElementTree.fromstring(source)
    except ElementTree.ParseError as exc:
        raise ValueError("visualization.svg is not valid XML") from exc
    if root.tag not in ("svg", f"{{{SVG_NAMESPACE}}}svg"):
        raise ValueError("visualization output must be an SVG image")
    ids = {}
    for node in root.iter():
        identifier = node.get("id")
        if identifier is not None:
            if identifier in ids:
                raise ValueError(f"visualization.svg contains duplicate id {identifier}")
            ids[identifier] = node.tag.rsplit("}", 1)[-1]
    for node in root.iter():
        if node.tag.startswith("{") and not node.tag.startswith(f"{{{SVG_NAMESPACE}}}"):
            raise ValueError("visualization.svg contains an unsupported namespace")
        tag = node.tag.rsplit("}", 1)[-1]
        if tag not in ALLOWED_TAGS:
            raise ValueError(f"visualization.svg contains unsupported element {tag}")
        for name, value in node.attrib.items():
            if name.startswith("{"):
                raise ValueError("visualization.svg contains an unsafe attribute namespace")
            attribute = name.rsplit("}", 1)[-1]
            if attribute not in ALLOWED_ATTRIBUTES:
                raise ValueError(f"visualization.svg contains unsupported attribute {attribute} on {tag}")
            if any(marker in value.lower() for marker in ("javascript:", "data:")):
                raise ValueError(f"visualization.svg contains an unsafe value for {attribute}")
            if "url" in value.lower() or attribute in {"clip-path", "marker-start", "marker-mid", "marker-end"}:
                if value == "none":
                    continue
                reference = LOCAL_REFERENCE.fullmatch(value)
                if (reference is None or attribute not in LOCAL_REFERENCE_TARGETS or
                        ids.get(reference.group(1)) not in LOCAL_REFERENCE_TARGETS[attribute]):
                    raise ValueError(f"visualization.svg contains an invalid local reference in {attribute}: {value}")
