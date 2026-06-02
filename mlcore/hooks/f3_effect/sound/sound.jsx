/**********************************************************************
 * sound.jsx — прокидывание звука под эффект, синхрон на ДРОП
 * --------------------------------------------------------------------
 * Кладёт аудио-слой из конкретного файла или из пула (папки),
 * синхронизирует импульс звука на дроп, тримит до лимита, делает fade.
 * Авто-каркас: без кликов (комп по place), без блок-окон (SILENT). Headless ок.
 *
 * Синхрон: если у звука импульс на impactAt сек внутри файла —
 *   startTime = drop - impactAt  (импульс ложится ровно на дроп).
 *   Иначе звук стартует на дропе.
 **********************************************************************/

// ======================= КАСТОМИЗАЦИЯ =======================
var CONFIG = {
    targetCompName: null,
    placeRef:       "Текст",
    dropTime:       3.0,           // момент дропа (якорь)

    soundFile:      null,          // конкретный файл (приоритетнее пула)
    soundPool:      "C:/Users/Пользователь/Desktop/АЕ/Звуки/Camera Flash", // папка-пул
    pick:           "random",      // "random" | "first"

    impactAt:       null,          // сек до импульса внутри файла (null = старт на дропе)
    maxDuration:    3.0,           // трим, сек
    fadeOut:        0.1,           // фейд в конце, сек
    levelDb:        0              // базовая громкость, dB
};
var SILENT = true;
// ============================================================

// крючок оркестратора: внешние параметры перекрывают CONFIG
if (typeof $ !== "undefined" && $.global && $.global.__BLAST){
    var __p = $.global.__BLAST; for (var __k in __p){ if (__p[__k] !== null && __p[__k] !== undefined) CONFIG[__k] = __p[__k]; }
}

function log(m){ if (SILENT){ try { $.writeln(m); } catch(e){} } else alert(m); }
function findLayer(c, n){ for (var i=1;i<=c.numLayers;i++) if (c.layer(i).name===n) return c.layer(i); return null; }
function findTargetComp(){
    var i, it;
    if (CONFIG.targetCompName){ for (i=1;i<=app.project.numItems;i++){ it=app.project.item(i);
        if (it instanceof CompItem && it.name===CONFIG.targetCompName) return it; } }
    if (CONFIG.placeRef){ for (i=1;i<=app.project.numItems;i++){ it=app.project.item(i);
        if (it instanceof CompItem && findLayer(it, CONFIG.placeRef)) return it; } }
    var a=app.project.activeItem; if (a && a instanceof CompItem) return a;
    var best=null; for (i=1;i<=app.project.numItems;i++){ it=app.project.item(i);
        if (it instanceof CompItem && (!best||it.numLayers>best.numLayers)) best=it; }
    return best;
}
function pickFile(){
    if (CONFIG.soundFile){ var f=new File(CONFIG.soundFile); return f.exists?f:null; }
    if (CONFIG.soundPool){
        var fld=new Folder(CONFIG.soundPool); if (!fld.exists) return null;
        var files=fld.getFiles(function(x){ return (x instanceof File) && /\.(wav|mp3|aif|aiff|m4a)$/i.test(x.name); });
        if (!files || !files.length) return null;
        var idx=(CONFIG.pick==="random") ? Math.floor(Math.random()*files.length) : 0;
        return files[idx];
    }
    return null;
}
function importAudio(file){
    for (var i=1;i<=app.project.numItems;i++){ var it=app.project.item(i);
        if (it instanceof FootageItem && it.name===file.name) return it; }
    try { return app.project.importFile(new ImportOptions(file)); } catch(e){ return null; }
}

function main(){
    if (!app.project){ log("Нет проекта."); return; }
    var comp = findTargetComp(); if (!comp){ log("Не найдена комп."); return; }
    var file = pickFile(); if (!file){ log("Нет звука (file/pool)."); return; }
    var src  = importAudio(file); if (!src){ log("Не импортировался: " + file.name); return; }

    app.beginUndoGroup("Sound");
    try {
        var L = comp.layers.add(src);
        L.name = "SFX: " + file.name;

        var d = CONFIG.dropTime;
        var impact = (CONFIG.impactAt !== null) ? CONFIG.impactAt : 0;
        var start = d - impact;                       // импульс на дропе
        L.startTime = start;
        L.inPoint   = start;
        var span = Math.min(CONFIG.maxDuration, src.duration);
        L.outPoint  = start + span;

        // громкость + fade в конце
        try {
            var lv = L.property("ADBE Audio Group").property("ADBE Audio Levels");
            var base = CONFIG.levelDb, end = L.outPoint, fo = CONFIG.fadeOut;
            if (fo > 0 && fo < span){
                lv.setValueAtTime(end - fo, [base, base]);
                lv.setValueAtTime(end,      [-96, -96]);
            } else {
                lv.setValue([base, base]);
            }
        } catch(e){}

        log("SFX «" + file.name + "» в «" + comp.name + "»: start=" + start.toFixed(3) +
            "с, импульс на дропе " + d + "с, длина " + span.toFixed(2) + "с.");
    } catch(err){ log("Ошибка: " + err.toString() + " (стр " + (err.line||"?") + ")"); }
    finally { app.endUndoGroup(); }
}
main();
