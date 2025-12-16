from __future__ import annotations

from typing import Any


def strip_additional_properties(schema: Any) -> Any:
    """Recursively remove `additionalProperties` from a JSON Schema dict.

    Why: the Gemini API / google-genai SDK rejects schemas that contain the
    `additionalProperties` keyword anywhere in the tree.

    NOTE:
    - We only strip the keyword from the schema we send to the model.
    - We still validate the actual response with Pydantic models on our side.
    """
    if isinstance(schema, dict):
        # Drop the keyword at *any* depth
        return {
            k: strip_additional_properties(v)
            for k, v in schema.items()
            if k != "additionalProperties"
        }
    if isinstance(schema, list):
        return [strip_additional_properties(v) for v in schema]
    return schema
