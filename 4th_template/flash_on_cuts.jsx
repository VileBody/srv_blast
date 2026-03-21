// flash_on_cuts.jsx
//
// Выдели нужные слои в композиции → запусти скрипт.
// Вспышка появится в момент начала (inPoint) каждого выделенного слоя.

// ============================================================
// НАСТРОЙКИ ВСПЫШКИ (из оригинального JSON)
// ============================================================
var FLASH_DURATION = 0.63333333;
var FLASH_OP_START = 25;           // opacity в начале (%)
var FLASH_OP_END   = 0;            // opacity в конце (%)
var FLASH_OP_TIME  = 0.6;          // время финального кейфрейма (внутри слоя)
var FLASH_BLEND    = BlendingMode.ADD;
var FLASH_NAME     = "Вспышка";

// ============================================================
// Утилиты
// ============================================================
function getWhiteSolid(comp) {
    for (var i = 1; i <= app.project.numItems; i++) {
        var item = app.project.items[i];
        if (item instanceof FootageItem && item.name === "White Solid 1") {
            return item;
        }
    }
    // Создаём если нет
    var temp = comp.layers.addSolid([1, 1, 1], "White Solid 1", comp.width, comp.height, comp.pixelAspect);
    var src  = temp.source;
    temp.remove();
    return src;
}

function addFlash(comp, solidSource, t) {
    var inT  = t;
    var outT = t + FLASH_DURATION;
    if (outT > comp.duration) outT = comp.duration;

    var layer          = comp.layers.add(solidSource, FLASH_DURATION);
    layer.name         = FLASH_NAME;
    layer.inPoint      = inT;
    layer.outPoint     = outT;
    layer.blendingMode = FLASH_BLEND;
    layer.motionBlur   = false;
    layer.moveToBeginning();

    var opacity  = layer.property("ADBE Transform Group").property("ADBE Opacity");
    var fadeTime = inT + FLASH_OP_TIME;
    if (fadeTime > outT) fadeTime = outT;

    opacity.setValueAtTime(inT, FLASH_OP_START);
    var k1 = opacity.nearestKeyIndex(inT);
    opacity.setTemporalEaseAtKey(k1,
        [new KeyframeEase(0,                   16.666666667)],
        [new KeyframeEase(-41.6666666666667,    16.666666667)]
    );

    opacity.setValueAtTime(fadeTime, FLASH_OP_END);
    var k2 = opacity.nearestKeyIndex(fadeTime);
    opacity.setTemporalEaseAtKey(k2,
        [new KeyframeEase(-41.6666666666667,    16.666666667)],
        [new KeyframeEase(0,                    16.666666667)]
    );
}

// ============================================================
// MAIN
// ============================================================
(function () {
    var comp = app.project.activeItem;
    if (!comp || !(comp instanceof CompItem)) {
        alert("Открой и активируй нужную композицию.");
        return;
    }

    var selected = comp.selectedLayers;
    if (!selected || !selected.length) {
        alert("Выдели слои, на стыке которых нужна вспышка.");
        return;
    }

    var solid = getWhiteSolid(comp);

    app.beginUndoGroup("Flash on cuts");

    for (var i = 0; i < selected.length; i++) {
        addFlash(comp, solid, selected[i].inPoint);
    }

    app.endUndoGroup();

    alert("Готово! Добавлено " + selected.length + " вспышек.");
})();
