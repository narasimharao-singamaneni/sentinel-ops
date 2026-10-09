import json

from django import template

register = template.Library()


@register.filter
def to_json(value):
    """Render a JSON-serializable value as pretty-printed JSON text."""
    try:
        return json.dumps(value, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value)


@register.filter
def mins2(value):
    """Minutes with exactly 2 decimals (int/float from JSON is normalized)."""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


@register.filter
def mins4(value):
    """Minutes with exactly 4 decimals (int/float from JSON is normalized)."""
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return str(value)


@register.filter
def pct(value):
    """Render a 0.0-1.0 confidence as a percentage, e.g. 0.92 -> '92.0'."""
    try:
        return f"{float(value) * 100:.1f}"
    except (TypeError, ValueError):
        return ""


@register.filter
def conf_class(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "confidence-low"
    if v >= 0.8:
        return "confidence-high"
    if v >= 0.5:
        return "confidence-medium"
    return "confidence-low"
