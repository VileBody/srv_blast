/**********************************************************************
 * rebuild_warm_map.jsx  (АВТОМАТИЗАЦИЯ: импорт .aep + copyToComp)
 * --------------------------------------------------------------------
 * Полный перенос эффекта без ручной донастройки и БЕЗ кликов.
 * Скрипт сам находит рабочую композицию (по слою-ориентиру, напр. "Текст"),
 * импортирует исходный .aep и копирует слой(и) с эффектом на нужное место.
 * Переносится всё на 100% (включая Colorama, который скриптом не задаётся).
 *
 * Запуск в пайплайне: достаточно, чтобы проект был ОТКРЫТ (через aerender /
 * BridgeTalk / app.open). Активная композиция/выделение НЕ требуются.
 **********************************************************************/

// ======================= КАСТОМИЗАЦИЯ =======================
var CONFIG = {
    targetCompName: null,         // имя рабочей композиции (null = найти авто по слою place)
    startTime: 3,                 // старт эффекта, сек (null = как в исходнике)
    duration:  2,                 // длительность, сек (null = как в исходнике)
    place:     "below:Текст",     // "above:<имя>" | "below:<имя>" | "top" | "bottom" | null
    opacity:   null,              // прозрачность слоя, % (null = не трогать)
    blend:     null               // null|"add"|"screen"|"multiply"|"overlay"|"normal"
};
var SILENT = true; // true = без блокирующих alert (для автоматизации); false = показывать окна
// ============================================================

var AEP_PATH = (function(){ var f = new File($.fileName); return f.parent.fsName + "/warm map.aep"; })();
var SRC_FOLDER_NAME = "[src] warm map";

function log(msg){ if (SILENT){ try { $.writeln(msg); } catch(e){} } else alert(msg); }

function refName(){
    if (CONFIG.place && CONFIG.place.indexOf(":") > -1) return CONFIG.place.split(":")[1];
    return null;
}
function findLayer(comp, name){
    for (var i = 1; i <= comp.numLayers; i++) if (comp.layer(i).name === name) return comp.layer(i);
    return null;
}
// найти рабочую композицию БЕЗ кликов
function findTargetComp(){
    var i, it;
    if (CONFIG.targetCompName){
        for (i = 1; i <= app.project.numItems; i++){ it = app.project.item(i);
            if (it instanceof CompItem && it.name === CONFIG.targetCompName) return it; }
    }
    var rn = refName();
    if (rn){
        for (i = 1; i <= app.project.numItems; i++){ it = app.project.item(i);
            if (it instanceof CompItem){ if (findLayer(it, rn)) return it; } }
    }
    var a = app.project.activeItem; if (a && a instanceof CompItem) return a; // запас, если вручную
    var best = null; // запас: комп с наибольшим числом слоёв
    for (i = 1; i <= app.project.numItems; i++){ it = app.project.item(i);
        if (it instanceof CompItem){ if (!best || it.numLayers > best.numLayers) best = it; } }
    return best;
}
function blendEnum(s){
    switch(String(s).toLowerCase()){
        case "add": return BlendingMode.ADD;       case "screen": return BlendingMode.SCREEN;
        case "multiply": return BlendingMode.MULTIPLY; case "overlay": return BlendingMode.OVERLAY;
        case "normal": return BlendingMode.NORMAL;
    }
    return null;
}
function customize(dst, layers){
    var i, L;
    if (CONFIG.place){
        var mode = CONFIG.place, ref = null;
        if (CONFIG.place.indexOf(":") > -1){ mode = CONFIG.place.split(":")[0]; ref = findLayer(dst, CONFIG.place.split(":")[1]); }
        for (i = 0; i < layers.length; i++){ L = layers[i];
            if (mode === "above" && ref) L.moveBefore(ref);
            else if (mode === "below" && ref) L.moveAfter(ref);
            else if (mode === "top") L.moveToBeginning();
            else if (mode === "bottom") L.moveToEnd();
        }
    }
    if (CONFIG.startTime !== null){
        var earliest = null;
        for (i = 0; i < layers.length; i++) earliest = (earliest===null) ? layers[i].inPoint : Math.min(earliest, layers[i].inPoint);
        var delta = CONFIG.startTime - earliest;
        // ВАЖНО: меняем ТОЛЬКО startTime — inPoint/outPoint в AE сдвигаются сами.
        for (i = 0; i < layers.length; i++) layers[i].startTime = layers[i].startTime + delta;
    }
    if (CONFIG.duration !== null){
        var base = (CONFIG.startTime !== null ? CONFIG.startTime : layers[0].inPoint);
        var winEnd = base + CONFIG.duration;
        for (i = 0; i < layers.length; i++){
            L = layers[i];
            if (L.inPoint  < base)   L.inPoint  = base;    // не начинать раньше окна
            if (L.outPoint > winEnd) L.outPoint = winEnd;  // и не заканчиваться позже
        }
    }
    for (i = 0; i < layers.length; i++){ L = layers[i];
        if (CONFIG.opacity !== null){ try { L.property("ADBE Transform Group").property("ADBE Opacity").setValue(CONFIG.opacity); } catch(e){} }
        var bm = blendEnum(CONFIG.blend); if (bm !== null){ try { L.blendingMode = bm; } catch(e){} }
    }
}

function main(){
    if (!app.project){ log("Нет открытого проекта."); return; }
    var dst = findTargetComp();
    if (!dst){ log("Не найдена рабочая композиция."); return; }
    var aep = new File(AEP_PATH);
    if (!aep.exists){ log("Не найден .aep:\n" + AEP_PATH); return; }

    app.beginUndoGroup("warm map (from aep)");
    try {
        var before = {}, i, it;
        for (i = 1; i <= app.project.numItems; i++) before[app.project.item(i).id] = true;

        app.project.importFile(new ImportOptions(aep));

        var newItems = [], srcComp = null;
        for (i = 1; i <= app.project.numItems; i++){ it = app.project.item(i);
            if (before[it.id]) continue;
            newItems.push(it);
            if (!srcComp && (it instanceof CompItem)){
                for (var li = 1; li <= it.numLayers; li++) if (it.layer(li).adjustmentLayer){ srcComp = it; break; }
            }
        }
        if (!srcComp){ log("В .aep не найден слой с эффектом."); app.endUndoGroup(); return; }

        var copiedLayers = [];
        for (var li = srcComp.numLayers; li >= 1; li--){
            var L = srcComp.layer(li);
            if (L.adjustmentLayer){ L.copyToComp(dst); copiedLayers.push(dst.layer(1)); }
        }
        if (copiedLayers.length > 0) customize(dst, copiedLayers);

        try { // прибрать импортированное в папку (не удаляем — чтобы не сломать копию)
            var fld = app.project.items.addFolder(SRC_FOLDER_NAME);
            for (var k = 0; k < newItems.length; k++) try { newItems[k].parentFolder = fld; } catch(e){}
        } catch(e){}

        log("warm map: скопировано " + copiedLayers.length + " слоёв в «" + dst.name + "».");
    } catch(err){ log("Ошибка: " + err.toString() + " (стр " + (err.line||"?") + ")"); }
    finally { app.endUndoGroup(); }
}
main();
