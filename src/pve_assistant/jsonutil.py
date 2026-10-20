"""Small helpers shared by the loop and the recipes."""
import json


def _find_json(text):
    """Return (prefix, parsed) for results that carry a banner before the JSON, else (text, None)."""
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                return text[:i].strip(), json.loads(text[i:])
            except ValueError:
                continue
    return text, None
