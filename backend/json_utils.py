"""JSON extraction helpers for parsing (occasionally noisy) model responses."""

import json
from typing import Any, Dict


def extract_json_or_raise(text: str) -> Dict[str, Any]:
    """Extract a JSON object from a model response.

    Assumes the response is pure JSON, but falls back to slicing the first
    ``{`` … last ``}`` block if the model wrapped extra prose around it.
    Raises ``ValueError`` if no parseable JSON object can be found.
    """
    try:
        return json.loads(text)
    except Exception as e:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = text[start : end + 1]
            return json.loads(candidate)
        raise ValueError(f"Failed to parse JSON: {e}\nResponse was:\n{text}")
