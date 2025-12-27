# JSX Template Sources

`render_templates/job_template.jsx` is a **single-file** ExtendScript template that the Python side
injects `PROJECT_DATA` into.

To keep the runtime template single-file (AE node receives one script), we keep a modular **source**
layout under `render_templates/jsx_src/parts/` and generate the dist file.

- Source of truth: `render_templates/jsx_src/parts/*.jsxinc`
- Build script: `python tools/build_job_template.py`
