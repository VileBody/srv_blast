"""Пересобирает src/data/figma-icon-box.json из реальных viewBox ассетов Figma.

Экспорт Figma ставит preserveAspectRatio="none": SVG молча растягивается в любой
заданный бокс. Поэтому размеры нельзя задавать на глаз — FigIcon берёт пропорцию отсюда.
Запуск: npm run icons:box (после добавления/обновления ассетов).
"""
import re, glob, os, json

out = {}
for f in sorted(glob.glob("public/assets/figma/*.svg")):
    head = open(f, encoding="utf-8").read(600)
    m = re.search(r'viewBox="([\d.eE\- ]+)"', head)
    if not m:
        continue
    p = [float(x) for x in m.group(1).split()]
    if len(p) < 4 or p[2] <= 0 or p[3] <= 0:
        continue
    out[os.path.basename(f)] = [round(p[2], 3), round(p[3], 3)]

json.dump({
    "$schema": "blast.figma_icon_box/1",
    "_comment": "АВТОГЕН: npm run icons:box. Реальные viewBox всех figma-ассетов.",
    "box": out,
}, open("src/data/figma-icon-box.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"figma-icon-box.json: {len(out)} ассетов")
