"""Compatibility shim for resume_state payloads that were round-tripped through
the Redis JobStore Lua merge.

Root cause: ``services/orchestrator/job_store.py`` runs a server-side Lua script
that does ``cjson.decode(raw) -> mutate -> cjson.encode(obj)`` on the whole
``JobState`` (which embeds ``result.resume_state``). lua-cjson has no separate
type for arrays vs objects, so an *empty* JSON array ``[]`` decodes to an empty
Lua table and re-encodes as ``{}``. As a result, every empty list inside a
persisted resume_state (e.g. ``stage1_asr.pause_spans`` / ``srt_items`` when the
clip has no pauses) becomes ``{}`` in Redis.

On text reuse (/bigtest, "same track"), the seeded resume_state is read back and
``model_validate``d. Pydantic then rejects ``{}`` where it expects a list
("Input should be a valid list"), the orchestrator discards the cached stage
(``llm_resume_bad``) and re-runs the LLM — defeating reuse entirely.

This shim restores the intended empty lists. It is **schema-guided**: only
fields declared as lists are coerced from ``{}`` to ``[]``; nested models are
recursed into using their own field annotations. Genuine free-form dict fields
(e.g. ``stage2_style``) and legitimately-empty objects are left untouched, so
the coercion cannot corrupt non-list data.
"""
from __future__ import annotations

import typing
from typing import Any

from pydantic import BaseModel


def _union_args(annotation: Any) -> list[Any]:
    """Return the meaningful members of an Optional/Union annotation, or the
    annotation itself wrapped in a list."""
    if typing.get_origin(annotation) is typing.Union:
        return [a for a in typing.get_args(annotation) if a is not type(None)]
    return [annotation]


def _list_item_type(annotation: Any) -> Any | None:
    """If ``annotation`` is (optionally) a list, return its item type
    (``Any`` if unparametrised); otherwise ``None``."""
    for member in _union_args(annotation):
        if typing.get_origin(member) in (list, tuple, set, frozenset):
            args = typing.get_args(member)
            return args[0] if args else Any
    return None


def _model_type(annotation: Any) -> type[BaseModel] | None:
    """If ``annotation`` is (optionally) a pydantic model, return that class."""
    for member in _union_args(annotation):
        if isinstance(member, type) and issubclass(member, BaseModel):
            return member
    return None


def restore_cjson_empty_lists(model_cls: type[BaseModel], data: Any) -> Any:
    """Recursively turn ``{}`` back into ``[]`` for every field of ``model_cls``
    (and nested models) that is declared as a list.

    Used as a ``model_validator(mode="before")`` body on resume-reuse payload
    models. A no-op for freshly-produced LLM payloads (those carry real ``[]``).
    """
    if not isinstance(data, dict):
        return data

    fields = getattr(model_cls, "model_fields", {}) or {}
    for name, field in fields.items():
        key = name
        if key not in data:
            alias = getattr(field, "alias", None)
            if alias and alias in data:
                key = alias
            else:
                continue

        value = data[key]
        annotation = getattr(field, "annotation", None)

        item_type = _list_item_type(annotation)
        if item_type is not None:
            # Declared list field.
            if value == {}:
                data[key] = []
            elif isinstance(value, list) and isinstance(item_type, type) and issubclass(item_type, BaseModel):
                data[key] = [restore_cjson_empty_lists(item_type, item) for item in value]
            continue

        nested = _model_type(annotation)
        if nested is not None and isinstance(value, dict):
            data[key] = restore_cjson_empty_lists(nested, value)

    return data
