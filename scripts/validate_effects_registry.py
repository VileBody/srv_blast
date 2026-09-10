"""Validate the website registry against executable AE assets and preview records."""
from __future__ import annotations
import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT/'web_app/frontend/src/data/effects-registry.json'
CATEGORIES = {'hook': 'effect_hook', 'glue': 'effect_transition', 'style': 'effect_extra'}


def contract_ids(root: Path = ROOT) -> dict[str, set[str]]:
    tree = ast.parse((root/'services/orchestrator/schemas.py').read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'SendAudioS3Request')
    return {n.target.id: {v.value for v in ast.walk(n.annotation) if isinstance(v, ast.Constant) and isinstance(v.value, str)}
            for n in cls.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}


def validate(manifest: Path, *, registry: Path = REGISTRY, preview_store: Path | None = None, root: Path = ROOT) -> list[str]:
    data = json.loads(registry.read_text(encoding='utf-8'))
    effects = {e['id']: e for e in json.loads(manifest.read_text(encoding='utf-8'))['effects']}
    allowed = contract_ids(root)
    previews = json.loads(preview_store.read_text(encoding='utf-8')).get('previews', {}) if preview_store else None
    errors = []
    labels = set(); ids = set()
    for group, category in CATEGORIES.items():
        for entry in data[group]:
            effect_id = entry['manifestId']; label = entry['label']
            if label in labels or effect_id in ids: errors.append(f'{effect_id}: duplicate label or id')
            labels.add(label); ids.add(effect_id)
            effect = effects.get(effect_id)
            if not effect: errors.append(f'{effect_id}: missing manifest entry'); continue
            if effect.get('deprecated') or 'unuse/' in str(effect.get('script', '')).replace('\\', '/'):
                errors.append(f'{effect_id}: deprecated or disabled')
            script = (manifest.parent / str(effect.get('script', ''))).resolve()
            if not script.is_relative_to(manifest.parent.resolve()) or script.suffix.lower() != '.jsx' or not script.is_file():
                errors.append(f'{effect_id}: JSX does not exist inside manifest root: {script}')
            icon = (root/'web_app/frontend/public/assets/figma'/entry['icon']).resolve()
            if not icon.is_relative_to((root/'web_app/frontend/public/assets/figma').resolve()) or not icon.is_file():
                errors.append(f'{effect_id}: icon missing or outside icon directory')
            if effect_id not in allowed.get(category, set()): errors.append(f'{effect_id}: not accepted by SendAudioS3Request.{category}')
            if previews is not None:
                preview = previews.get(f'{category}:{effect_id}') or {}
                if not any(preview.get(k) for k in ('s3_url', 'local_path', 'file_id', 'preview_file_id')):
                    errors.append(f'{effect_id}: missing preview record {category}:{effect_id}')
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--registry', type=Path, default=REGISTRY)
    parser.add_argument('--preview-store', type=Path)
    args = parser.parse_args()
    errors = validate(args.manifest, registry=args.registry, preview_store=args.preview_store)
    for error in errors: print(error)
    print(f'effects registry: {len(errors)} errors')
    return int(bool(errors))


if __name__ == '__main__': raise SystemExit(main())
