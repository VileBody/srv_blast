from __future__ import annotations

import copy
from typing import Any, Dict


def build_text_document(text_styles: Dict[str, Any], style_id: str, content: Any) -> Dict[str, Any]:
    """Build AE TextDocument dict from a styleId + content."""
    doc = copy.deepcopy(text_styles.get(style_id, {}))
    doc["text"] = "" if content is None else str(content)
    return doc

