"""Add a renderer-supported effect, icon and preview in one reviewed command.

Example: python scripts/add_effect.py --group style --manifest-id blackwhite
  --label "Чёрно-белый" --icon icon.svg --preview-s3 s3://bucket/example.mp4
  --manifest /path/to/manifest.json --preview-store data/hook_previews.json

Use --dry-run for validation only. Rendering and deployment are separate explicit steps.
"""
from __future__ import annotations
import argparse
import json
import shutil
import subprocess
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_effects_registry import ROOT, REGISTRY, CATEGORIES, contract_ids, validate


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--group', required=True, choices=CATEGORIES)
    p.add_argument('--manifest-id', required=True)
    p.add_argument('--label', required=True)
    p.add_argument('--icon', type=Path, required=True)
    p.add_argument('--preview-s3', required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--preview-store', type=Path, required=True)
    p.add_argument('--inner', action='store_true')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    if not args.icon.is_file() or args.icon.suffix.lower() != '.svg': p.error('--icon must be an existing SVG')
    if not args.preview_s3.startswith('s3://') or '/' not in args.preview_s3[5:]: p.error('--preview-s3 must be an S3 object locator')
    effects = {e['id']: e for e in json.loads(args.manifest.read_text(encoding='utf-8'))['effects']}
    effect = effects.get(args.manifest_id)
    if not effect or effect.get('deprecated'): p.error('effect missing or deprecated in renderer manifest')
    script = (args.manifest.parent/effect['script']).resolve()
    if not script.is_relative_to(args.manifest.parent.resolve()) or not script.is_file(): p.error('manifest JSX missing')
    if args.manifest_id not in contract_ids()[CATEGORIES[args.group]]:
        p.error('Effect is not supported by the orchestrator yet. Register and validate its renderer contract first.')
    registry = json.loads(REGISTRY.read_text(encoding='utf-8'))
    if any(e['manifestId'] == args.manifest_id or e['label'] == args.label for group in CATEGORIES for e in registry[group]): p.error('effect id or label already registered')
    dest = ROOT/'web_app/frontend/public/assets/figma'/args.icon.name
    if dest.exists() and dest.read_bytes() != args.icon.read_bytes(): p.error('icon filename already exists with different contents')
    record = {'label': args.label, 'manifestId': args.manifest_id, 'icon': dest.name, 'inner': args.inner}
    registry[args.group].append(record)
    previews = json.loads(args.preview_store.read_text(encoding='utf-8'))
    previews.setdefault('previews', {})[f'{CATEGORIES[args.group]}:{args.manifest_id}'] = {'label': args.label, 's3_url': args.preview_s3}
    print(json.dumps({'effect': record, 'preview': args.preview_s3}, ensure_ascii=True))
    if args.dry_run: return 0
    backups = {path: path.read_bytes() if path.exists() else None for path in (REGISTRY, dest, args.preview_store, ROOT/'web_app/frontend/src/data/figma-icon-box.json')}
    try:
        dest.write_bytes(args.icon.read_bytes())
        REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        args.preview_store.write_text(json.dumps(previews, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        npm = shutil.which('npm.cmd' if sys.platform == 'win32' else 'npm')
        if not npm: raise RuntimeError('npm is required to regenerate icon dimensions')
        subprocess.run([npm, 'run', 'icons:box'], cwd=ROOT/'web_app/frontend', check=True)
        errors = validate(args.manifest, preview_store=args.preview_store)
        if errors: raise RuntimeError('\n'.join(errors))
    except BaseException:
        for path, content in backups.items():
            if content is None: path.unlink(missing_ok=True)
            else: path.write_bytes(content)
        raise
    print('Effect, icon and preview registered. Run build_web_preview_catalogs.py and deploy the resulting FX catalog.')
    return 0


if __name__ == '__main__': raise SystemExit(main())
