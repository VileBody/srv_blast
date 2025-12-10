import json
import copy
import os
from models import Payload

# === CONFIG ===
FILE_STYLES   = 'config/styles/text_styles.json'
FILE_PRESETS  = 'config/styles/footage_presets.json'
FILE_SCENARIO = 'render_v1/composition.json'
FILE_TEMPLATE = 'render_v1/engine_template.jsx'
FILE_OUTPUT   = 'render_v1/render.jsx'

def load_json(filename):
    if not os.path.exists(filename):
        print(f"[WARN] File not found: {filename}")
        return {}
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Decoding {filename}: {e}")
        return {}

def process_layer(layer, styles_lib, presets_lib):
    if 'inPoint' in layer and 'startTime' not in layer:
        layer['startTime'] = layer['inPoint']

    if layer.get('type') == 'text' and 'styleId' in layer:
        sid = layer['styleId']
        if sid in styles_lib:
            style_props = copy.deepcopy(styles_lib[sid])
            if 'textDocument' not in layer: layer['textDocument'] = {}
            for k, v in style_props.items():
                if k not in layer['textDocument']: layer['textDocument'][k] = v
            
            content = layer.get('content') or layer.get('text')
            if content: layer['textDocument']['text'] = content
            if 'content' in layer: del layer['content']
            if 'text' in layer and not isinstance(layer['text'], dict): del layer['text']
        del layer['styleId']

    if 'presetId' in layer:
        pid = layer['presetId']
        if pid in presets_lib:
            preset_data = presets_lib[pid]
            if 'transform' in preset_data:
                preset_transform = copy.deepcopy(preset_data['transform'])
                if 'transform' not in layer: layer['transform'] = {}
                for k, v in preset_transform.items():
                    if k not in layer['transform']: layer['transform'][k] = v
        del layer['presetId']

    return layer

def apply_defaults(item, defaults):
    """
    Если в item (композиции) не хватает полей, берем из defaults.
    """
    if item.get('type') == 'comp':
        for key, val in defaults.items():
            if key not in item:
                item[key] = val
    return item

def main():
    print("--- AE Strict Assembler ---")
    
    styles_data = load_json(FILE_STYLES)
    presets_data = load_json(FILE_PRESETS)
    scenario_data = load_json(FILE_SCENARIO)
    
    if not scenario_data: return

    # 1. Получаем глобальные настройки
    project_settings = scenario_data.get('projectSettings', {})
    defaults = project_settings.get('defaults', {})
    
    print(f"Applying Defaults: {defaults}")

    final_items = []
    raw_items = scenario_data.get('items', [])
    
    for item in raw_items:
        # A. Применяем дефолты (width, fps, pixelAspect...)
        item = apply_defaults(item, defaults)

        # B. Разворачиваем слои
        if item.get('type') == 'comp' and 'layers' in item:
            processed_layers = []
            for layer in item['layers']:
                processed_layer = process_layer(layer, styles_data, presets_data)
                processed_layers.append(processed_layer)
            item['layers'] = processed_layers
        
        final_items.append(item)

    # 2. Формируем Payload
    raw_payload = {
        "project": {
            "projectName": project_settings.get('name', "Auto Build"),
            "items": final_items
        },
        "entryPoint": "comp_main"
    }

    # 3. ВАЛИДАЦИЯ (PYDANTIC)
    try:
        # Если pixelAspect не прилетел ни из item, ни из defaults -> ТУТ БУДЕТ ОШИБКА
        model = Payload(**raw_payload)
        print("[OK] Validation successful.")
        json_str = model.model_dump_json(indent=2, exclude_none=True)
        
    except Exception as e:
        print(f"\n[FATAL VALIDATION ERROR] Invalid JSON structure:\n{e}")
        # Скрипт остановится здесь и не создаст render.jsx
        return

    # 4. Генерация
    js_variable = f"var PROJECT_DATA = {json_str};\n"

    try:
        with open(FILE_TEMPLATE, 'r', encoding='utf-8') as f:
            template_code = f.read()
            
        final_jsx = template_code.replace("/*__PYTHON_DATA_INJECT__*/", js_variable)
        
        with open(FILE_OUTPUT, 'w', encoding='utf-8') as f:
            f.write(final_jsx)
        print(f"[SUCCESS] Generated '{FILE_OUTPUT}'")
        
    except Exception as e:
        print(f"Error writing file: {e}")

if __name__ == "__main__":
    main()