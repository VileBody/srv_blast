from pathlib import Path

from .assembler_core import build_project_payload_from_composition, load_json

# === CONFIG ===
FILE_STYLES = Path("config/styles/text_styles.json")
FILE_PRESETS = Path("config/styles/footage_presets.json")
FILE_SCENARIO = Path("render_v1/composition.json")
FILE_TEMPLATE = Path("render_v1/engine_template.jsx")
FILE_OUTPUT = Path("render_v1/render.jsx")


def main():
    print("--- AE Strict Assembler ---")

    scenario_data = load_json(FILE_SCENARIO)
    if not scenario_data:
        return

    _, json_str = build_project_payload_from_composition(
        styles_path=FILE_STYLES,
        presets_path=FILE_PRESETS,
        composition=scenario_data,
        entry_point="comp_main",
    )

    print("[OK] Validation successful.")

    js_variable = f"var PROJECT_DATA = {json_str};\n"

    try:
        template_code = FILE_TEMPLATE.read_text(encoding="utf-8")
        final_jsx = template_code.replace("/*__PYTHON_DATA_INJECT__*/", js_variable)
        FILE_OUTPUT.write_text(final_jsx, encoding="utf-8")
        print(f"[SUCCESS] Generated '{FILE_OUTPUT}'")
    except Exception as e:  # noqa: BLE001
        print(f"Error writing file: {e}")


if __name__ == "__main__":
    main()
