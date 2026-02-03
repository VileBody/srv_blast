# Makefile (no-tabs-safe)
# Usage examples:
#   make start-server
#   make worker-build
#   make worker-render
#   make gemini  AUDIO=./audio/track.mp3
#   make builder AUDIO=./audio/track.mp3
#   make dispatch WINDOWS=http://217.199.253.173:8000

SHELL := bash
.SHELLFLAGS := -euo pipefail -c

# Make normally requires TABs for recipes.
# We avoid TAB issues by changing recipe prefix to '>'
.RECIPEPREFIX := >

# -----------------------------
# Config
# -----------------------------
PY ?= python3
ORCH_HOST ?= 0.0.0.0
ORCH_PORT ?= 8000

# Your repo defaults
DATA_DIR ?= ./data
OUT_DIR  ?= ./out

# AUDIO can be overridden: make gemini AUDIO=./audio/foo.mp3
AUDIO ?=

# Windows render node base URL (override per call)
WINDOWS ?= $(WINDOWS_RENDER_URL)

# Celery queues (should match env CELERY_QUEUE_BUILD / CELERY_QUEUE_RENDER)
QUEUE_BUILD ?= build
QUEUE_RENDER ?= render

# Concurrency defaults
BUILD_CONCURRENCY ?= 4
RENDER_CONCURRENCY ?= 1

# Timeouts
WINDOWS_TIMEOUT_S ?= 300

# -----------------------------
# Helpers
# -----------------------------
.PHONY: help
help:
> @echo "Targets:"
> @echo "  make start-server              # run FastAPI orchestrator locally"
> @echo "  make worker-build              # celery worker for build queue"
> @echo "  make worker-render             # celery worker for render queue"
> @echo "  make gemini  AUDIO=...         # run ONLY Gemini step (configs) into $(DATA_DIR) + $(OUT_DIR)"
> @echo "  make builder AUDIO=...         # run ONLY AE builder step (no Gemini), uses existing configs"
> @echo "  make dispatch [WINDOWS=...]    # send existing out/render_full.jsx + final_render_instructions_full.json to Windows node"
> @echo ""
> @echo "Notes:"
> @echo "  - .env is sourced automatically (if exists)."
> @echo "  - AUDIO is optional for builder if your configs already reference the correct audio; recommended to pass it."

.PHONY: _env
_env:
> if [[ -f ./.env ]]; then set -a; source ./.env; set +a; echo "[env] loaded ./.env"; else echo "[env] ./.env not found (ok)"; fi

.PHONY: _ensure_dirs
_ensure_dirs:
> mkdir -p "$(DATA_DIR)" "$(OUT_DIR)"

# -----------------------------
# Run orchestrator locally (no docker)
# -----------------------------
.PHONY: start-server
start-server: _env
> ORCH_HOST="$(ORCH_HOST)" ORCH_PORT="$(ORCH_PORT)" $(PY) -m services.orchestrator.run_uvicorn

.PHONY: worker-build
worker-build: _env
> celery -A services.orchestrator.celery_app:celery_app worker -l INFO --concurrency="$(BUILD_CONCURRENCY)" -Q "$(QUEUE_BUILD)"

.PHONY: worker-render
worker-render: _env
> celery -A services.orchestrator.celery_app:celery_app worker -l INFO --concurrency="$(RENDER_CONCURRENCY)" -Q "$(QUEUE_RENDER)"

# -----------------------------
# Local pipeline (bypass queues)
# -----------------------------
# 1) Gemini-only: generates configs into DATA_DIR + mirrors into OUT_DIR
.PHONY: gemini
gemini: _env _ensure_dirs
> if [[ -z "$(AUDIO)" ]]; then echo "[ERR] AUDIO is required: make gemini AUDIO=./audio/file.mp3"; exit 2; fi
> AUDIO_ABS="$$(python - <<'PY'\nfrom pathlib import Path\nimport os\np=Path(os.environ['AUDIO']).expanduser().resolve()\nprint(str(p))\nPY\n)"; \
> echo "[ok] AUDIO_FILE_PATH=$${AUDIO_ABS}"; \
> DATA_DIR="$(DATA_DIR)" OUT_DIR="$(OUT_DIR)" AUDIO_FILE_PATH="$${AUDIO_ABS}" AUDIO_DIR="$$(dirname "$${AUDIO_ABS}")" \
> $(PY) run.py --skip-ae --full-edit "$(DATA_DIR)/full_edit_config.json" --footage "$(DATA_DIR)/footage_config.json" --out-dir "$(OUT_DIR)"

# 2) Builder-only: uses existing configs, produces render_full.jsx + final_render_instructions_full.json
.PHONY: builder
builder: _env _ensure_dirs
> if [[ -n "$(AUDIO)" ]]; then \
>   AUDIO_ABS="$$(python - <<'PY'\nfrom pathlib import Path\nimport os\np=Path(os.environ['AUDIO']).expanduser().resolve()\nprint(str(p))\nPY\n)"; \
>   echo "[ok] AUDIO_FILE_PATH=$${AUDIO_ABS}"; \
>   AUDIO_EXPORT="AUDIO_FILE_PATH=$${AUDIO_ABS} AUDIO_DIR=$$(dirname "$${AUDIO_ABS}")"; \
> else \
>   echo "[warn] AUDIO not provided; builder will use AUDIO_FILE_PATH/AUDIO_DIR from env or ./audio/ fallback"; \
>   AUDIO_EXPORT=""; \
> fi; \
> eval "$${AUDIO_EXPORT}" DATA_DIR="$(DATA_DIR)" OUT_DIR="$(OUT_DIR)" \
> $(PY) run.py --skip-llm --full-edit "$(DATA_DIR)/full_edit_config.json" --footage "$(DATA_DIR)/footage_config.json" --out-dir "$(OUT_DIR)"

# 3) Dispatch-only: sends already-built artifacts to Windows node (no Celery, no Orchestrator)
#    - Uses OUT_DIR/render_full.jsx and OUT_DIR/final_render_instructions_full.json
#    - Tries to auto-pick audio_url from DATA_DIR/footage_config.json (audio_only.file_path)
.PHONY: dispatch
dispatch: _env _ensure_dirs
> if [[ -z "$(WINDOWS)" ]]; then echo "[ERR] WINDOWS is required (or set WINDOWS_RENDER_URL in .env). Example: make dispatch WINDOWS=http://217.199.253.173:8000"; exit 2; fi
> $(PY) - <<'PY'\nimport json, os\nfrom pathlib import Path\n\nfrom services.orchestrator.render_manifest import build_windows_job_payload\nfrom services.orchestrator.windows_client import WindowsRenderClient\n\nrepo = Path('.').resolve()\ndata_dir = Path(os.environ.get('DATA_DIR','./data')).resolve()\nout_dir  = Path(os.environ.get('OUT_DIR','./out')).resolve()\nwindows  = (os.environ.get('WINDOWS') or os.environ.get('WINDOWS_RENDER_URL') or '').rstrip('/')\n\nrender_jsx = out_dir / 'render_full.jsx'\nrender_payload = out_dir / 'final_render_instructions_full.json'\n\nif not render_jsx.exists():\n    raise SystemExit(f'[ERR] missing {render_jsx}')\nif not render_payload.exists():\n    raise SystemExit(f'[ERR] missing {render_payload}')\n\n# Auto-pick audio_url from footage_config.json (audio_only layer)\nfootage_cfg = data_dir / 'footage_config.json'\nif not footage_cfg.exists():\n    raise SystemExit(f'[ERR] missing {footage_cfg} (needed to auto-pick audio_url)')\n\nd = json.loads(footage_cfg.read_text(encoding='utf-8'))\naudio_url = None\nfor layer in d.get('layers', []) or []:\n    if isinstance(layer, dict) and str(layer.get('type')) == 'audio_only':\n        audio_url = (layer.get('file_path') or '').strip()\n        break\nif not audio_url:\n    raise SystemExit('[ERR] could not find audio_only.file_path in footage_config.json')\n\njob_id = os.environ.get('JOB_ID') or 'manual_dispatch'\n\npayload = build_windows_job_payload(\n    job_id=job_id,\n    render_jsx_path=render_jsx,\n    render_payload_path=render_payload,\n    audio_url=audio_url,\n    entry_comp='Main Render',\n    output_relpath='work/output.mp4',\n    output_s3_bucket=os.environ.get('S3_BUCKET_OUTPUT_VIDEO',''),\n    output_s3_key=f'renders/{job_id}/output.mp4',\n)\n\nprint(f'[dispatch] windows={windows}')\nprint(f'[dispatch] audio_url={audio_url[:120]}...')\nprint(f'[dispatch] jsx={render_jsx}')\nprint(f'[dispatch] payload={render_payload}')\n\nclient = WindowsRenderClient(windows, timeout_s=float(os.environ.get('WINDOWS_TIMEOUT_S','300')))\nres = client.dispatch_render(payload)\nprint('\\n=== WINDOWS RESPONSE ===')\nprint(json.dumps(res, ensure_ascii=False, indent=2))\nPY
