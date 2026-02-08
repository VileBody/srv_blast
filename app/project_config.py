# -*- coding: utf-8 -*-
from __future__ import annotations

AE_PROJECT = {
    # MAIN COMP (верхний)
    "main_comp": {
        "name": "Comp 1",
        "w": 1080,
        "h": 1960,
        "fps": 23.9759979248047,
        "dur": 60.0600600600601,
        "pixelAspect": 1.0,
        "workAreaStart": 0.0,
        "workAreaDuration": 18.4351017684351,
        "displayStartTime": 0.0,
        "bgColor": [0, 0, 0],
        "parentFolderPath": "ROOT",
    },

    # TEXT COMP (куда кладутся все блоки)
    "text_comp": {
        "name": "Текст",
        "w": 1080,
        "h": 1920,
        "fps": 23.9759979248047,
        "dur": 18.4351017684351,
        "pixelAspect": 1.0,
        "workAreaStart": 0.0,
        "workAreaDuration": 18.4351017684351,
        "displayStartTime": 0.0,
        "bgColor": [0, 0, 0],
        "parentFolderPath": "ROOT",
    },

    # INNER PRECOMP (Mine)
    "mine_comp": {
        "name": 'Текст "Mine"',
        "w": 1080,
        "h": 1920,
        "fps": 23.9759979248047,
        "dur": 2.54421087754421,
        "pixelAspect": 1.0,
        "workAreaStart": 0.0,
        "workAreaDuration": 2.54421087754421,
        "displayStartTime": 0.0,
        "bgColor": [0, 0, 0],
        "parentFolderPath": "ROOT",
    },

    # Маппинг “старых AE-id из блюпринтов” -> “имя компа”.
    # ВАЖНО: в whole-скрипте мы опираемся на comp_name, а не id,
    # потому что AE выдаёт новые id при создании.
    "precomp_id_to_name": {
        133: "Текст",
        88: 'Текст "Mine"',
    },

    # z-index для слоя-сабкомпа в main (больше = ниже в стеке)
    "root_precomp_z_index": 9999,

    # Placement сабкомпа "Текст" внутри "Comp 1"
    # (под vertical 1080x1960 и вертикальный сабкомп 1080x1920)
    "root_precomp_placement": {
        "anchor": [540, 960, 0],
        "position": [540, 980, 0],
        "scale": [100, 100, 100],
        "rotationZ": 0,
        "opacity": 100,
    },
}
