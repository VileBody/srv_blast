/**********************************************************************
 * brand_logo.jsx  — лого-штамп Бласта (брендинг)
 * --------------------------------------------------------------------
 * Кладёт лого-PNG поверх и анимирует «штамп-вспышку» на ДРОПЕ
 * (паттерн из shutter: Minimax-блум + Яркость +150 + opacity-поп).
 * stamp_flash = ТРИ лого со сдвигом в 3 кадра (стробо), первый ровно на дропе.
 * Авто-каркас: без кликов (комп по place), без блок-окон (SILENT).
 *
 * Вешается на хуки с branding:true (напр. slow shutter).
 * hook light НЕ брендируем (оверфит), shutter — лого уже встроено.
 **********************************************************************/

// ======================= КАСТОМИЗАЦИЯ =======================
var CONFIG = {
    targetCompName: null,
    placeRef:       "Текст",
    dropTime:       3.0,           // момент дропа, сек (якорь; первый штамп тут)
    logoPath:       "C:/Users/Пользователь/Desktop/АЕ/Хуки/Лого и шейпы/Лого Бласта/Group 1245.png",
    style:          "stamp_flash", // "stamp_flash" | "clean_fade" | "corner_watermark"
    scale:          23,
    position:       "center",      // "center" | "tl" | "tr" | "bl" | "br"
    margin:         90,
    duration:       0.15,          // длина одного штампа, сек
    stampCount:     3,             // сколько лого подряд (стробо)
    staggerFrames:  3              // сдвиг между ними, кадров
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
function setKeys(prop, arr){ var i; for (i=0;i<arr.length;i++) prop.setValueAtTime(arr[i][0], arr[i][1]);
    for (i=1;i<=prop.numKeys;i++){ try { prop.setInterpolationTypeAtKey(i,
        KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER); } catch(e){} } }
function importLogo(){
    var f = new File(CONFIG.logoPath); if (!f.exists) return null;
    for (var i=1;i<=app.project.numItems;i++){ var it=app.project.item(i);
        if (it instanceof FootageItem && it.name===f.name) return it; }
    try { return app.project.importFile(new ImportOptions(f)); } catch(e){ return null; }
}
function logoPos(comp, src){
    var sc=CONFIG.scale, m=CONFIG.margin, W=comp.width, H=comp.height;
    var halfW=(src.width*sc/100)/2, halfH=(src.height*sc/100)/2;
    switch (CONFIG.position){
        case "tl": return [halfW+m,     halfH+m,     0];
        case "tr": return [W-halfW-m,   halfH+m,     0];
        case "bl": return [halfW+m,     H-halfH-m,   0];
        case "br": return [W-halfW-m,   H-halfH-m,   0];
        default:   return [W/2,         H/2,         0];
    }
}
// один лого-штамп, стартующий в startT
function buildStamp(comp, src, startT, name){
    var L = comp.layers.add(src);
    L.name = name; L.moveToBeginning();
    var tr = L.property("ADBE Transform Group");
    tr.property("ADBE Scale").setValue([CONFIG.scale, CONFIG.scale, 100]);
    tr.property("ADBE Position").setValue(logoPos(comp, src));
    var dur = CONFIG.duration;
    L.inPoint = startT; L.outPoint = startT + dur;
    var fx = L.property("ADBE Effect Parade");
    var mm = fx.addProperty("ADBE Minimax");                 // блум-вспышка
    setKeys(mm.property("ADBE Minimax-0002"),
        [[startT,326],[startT+dur*0.667,0],[startT+dur,300]]);
    var bc = fx.addProperty("ADBE Brightness & Contrast 2"); // белая вспышка
    bc.property("ADBE Brightness & Contrast 2-0001").setValue(150);
    setKeys(tr.property("ADBE Opacity"), [[startT,100],[startT+dur,0]]); // поп и гаснет
    return L;
}

function main(){
    if (!app.project){ log("Нет проекта."); return; }
    var comp = findTargetComp(); if (!comp){ log("Не найдена комп."); return; }
    var src = importLogo(); if (!src){ log("Не найден лого: " + CONFIG.logoPath); return; }

    app.beginUndoGroup("Brand logo");
    try {
        var d = CONFIG.dropTime;
        if (CONFIG.style === "corner_watermark"){
            var L = comp.layers.add(src); L.name = "Лого Бласта"; L.moveToBeginning();
            var tr = L.property("ADBE Transform Group");
            tr.property("ADBE Scale").setValue([CONFIG.scale, CONFIG.scale, 100]);
            tr.property("ADBE Position").setValue(logoPos(comp, src));
            L.inPoint = 0; L.outPoint = comp.duration;
            tr.property("ADBE Opacity").setValue(60);
        } else if (CONFIG.style === "clean_fade"){
            var L2 = comp.layers.add(src); L2.name = "Лого Бласта"; L2.moveToBeginning();
            var t2 = L2.property("ADBE Transform Group");
            t2.property("ADBE Scale").setValue([CONFIG.scale, CONFIG.scale, 100]);
            t2.property("ADBE Position").setValue(logoPos(comp, src));
            var du = CONFIG.duration;
            L2.inPoint = d; L2.outPoint = d + du + 0.2;
            setKeys(t2.property("ADBE Opacity"), [[d,0],[d+0.06,100],[d+du+0.14,100],[d+du+0.2,0]]);
        } else { // stamp_flash: 3 лого со сдвигом в 3 кадра, первый ровно на дропе
            var fr = comp.frameDuration, step = CONFIG.staggerFrames * fr;
            for (var k = 0; k < CONFIG.stampCount; k++){
                buildStamp(comp, src, d + k * step, "Лого Бласта " + (k + 1));
            }
        }
        log("Лого: style=" + CONFIG.style + ", drop=" + d + "с, штампов=" +
            (CONFIG.style==="stamp_flash"?CONFIG.stampCount:1) + " в «" + comp.name + "».");
    } catch(err){ log("Ошибка: " + err.toString() + " (стр " + (err.line||"?") + ")"); }
    finally { app.endUndoGroup(); }
}
main();
