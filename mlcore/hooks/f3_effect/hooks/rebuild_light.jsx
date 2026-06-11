/**********************************************************************
 * rebuild_light.jsx  ("light1" — вспышка/молнии/шейк)
 * --------------------------------------------------------------------
 * Один скрипт на весь проект. Из дампа total_dump__light1__...
 * Комп 1080x1920, 29.97.
 *
 * СОБИРАЕТ (сверху вниз): моргачка, вспышка, левая молния, правая молния,
 *   масштаб, шейк.
 * ПРОПУСКАЕТ: "глоу", "сатурация" (по просьбе) и медиа (.mp4).
 *
 * ВРУЧНУЮ / ОГРАНИЧЕНИЯ AE:
 *  - Цвета градиентных обводок (вспышка, молнии) = NO_VALUE -> взял БЕЛЫЙ.
 *    Если нужен другой цвет/градиент — поправь CONFIG.strokeColor.
 *  - Mocha-маски в S_HueSatBright/S_Glow и Mocha-трекинг на молниях скриптом
 *    не воссоздаются (CUSTOM_VALUE) — эффект применяется глобально к слою.
 *  - Режимы наложения светящихся слоёв выставлены ADD (в дампе 5220/5233) —
 *    если не совпадёт, поменяй BLEND_LIGHT.
 *  - Адъюст-слои (шейк/масштаб/моргачка) = солид 1080x1080 + scale 177.78
 *    (как в оригинале; важно для координат эффектов Warp/Magnify).
 *  - Sapphire (S_WarpRepeat / S_Glow / S_HueSatBright) — нужен установленный.
 **********************************************************************/

var CONFIG = {
    strokeColor: [1,1,1,1],   // цвет обводок вспышки/молний (градиент нечитаем)
    compW: 1080, compH: 1920, compFps: 29.9700012207031, compDur: 30.03003003003
};
var BLEND_LIGHT = BlendingMode.ADD; // режим наложения светящихся слоёв

// ---- авто-каркас: приём параметров от оркестратора, SILENT, синхрон на дроп ----
var SILENT = true;
var __B = (typeof $!=="undefined" && $.global && $.global.__BLAST) ? $.global.__BLAST : {};
var TARGET_COMP = (__B.targetCompName!=null) ? __B.targetCompName : null;
var DROP_TIME   = (__B.dropTime!=null) ? __B.dropTime : null;
var HOOK_ANCHOR = 4.5045045045045; // якорь хука (вспышка/шейк) -> на этот момент попадёт DROP_TIME
function logL(m){ if(SILENT){try{$.writeln(m);}catch(e){}}else alert(m); }

// ====================== ХЕЛПЕРЫ ======================
function clamp0(x){ return (x < 0) ? 0 : x; }
function setConst(p, v){ try { p.setValue(v); } catch(e){} }
function setExpr(p, e){ try { p.expression = e; } catch(err){} }
function setEffParam(eff, mn, v){ try { eff.property(mn).setValue(v); } catch(e){} }
function setKeys(prop, keys){
    var i;
    for (i = 0; i < keys.length; i++) prop.setValueAtTime(keys[i].time, keys[i].val);
    for (i = 1; i <= prop.numKeys; i++){
        try { prop.setInterpolationTypeAtKey(i, KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER); } catch(e){}
    }
}
function setEffKeys(eff, mn, keys){ try { setKeys(eff.property(mn), keys); } catch(e){} }
function makeShape(verts, inT, outT, closed){
    var s = new Shape(); s.vertices = verts; s.inTangents = inT; s.outTangents = outT; s.closed = closed; return s;
}

var PLACE_REF = "Текст"; // слой-ориентир: хук ляжет ПОД него (как у эффект-скриптов)
function findLayerByName(comp, name){ for (var i=1;i<=comp.numLayers;i++) if (comp.layer(i).name===name) return comp.layer(i); return null; }
function getTargetComp(){
    var a = app.project.activeItem, i, it;
    // 0) по имени из параметров оркестратора
    if (TARGET_COMP){ for (i=1;i<=app.project.numItems;i++){ it=app.project.item(i);
        if (it instanceof CompItem && it.name===TARGET_COMP) return it; } }
    // 1) активная комп со слоем "Текст"
    if (a && a instanceof CompItem && findLayerByName(a, PLACE_REF)) return a;
    // 2) любая комп со слоем "Текст"
    for (var i=1;i<=app.project.numItems;i++){ var it=app.project.item(i);
        if (it instanceof CompItem && findLayerByName(it, PLACE_REF)) return it; }
    // 3) активная комп (запас)
    if (a && a instanceof CompItem) return a;
    // 4) запас: новая комп
    var comp = app.project.items.addComp("LIGHT_REBUILD", CONFIG.compW, CONFIG.compH, 1, CONFIG.compDur, CONFIG.compFps);
    comp.bgColor = [0,0,0];
    return comp;
}
// адъюст-слой = солид 1080x1080 + adjustment + scale 177.78 (как в дампе)
function addAdjust(comp, name, inP, outP){
    var L = comp.layers.addSolid([1,1,1], name, 1080, 1080, 1);
    L.adjustmentLayer = true;
    L.startTime = 0; L.inPoint = clamp0(inP); L.outPoint = outP;
    setConst(L.property("ADBE Transform Group").property("ADBE Scale"), [177.777777777778,177.777777777778,100]);
    return L;
}

// ====================== ГЕОМЕТРИЯ МОЛНИЙ ======================
var BOLT_L = makeShape(
    [[534.542419433594,-591.632446289062],[136.017517089844,-371.176361083984],[-274.987945556641,33.6951293945312],[-106.744049072266,296.696350097656],[447.84130859375,534.233276367188]],
    [[0,0],[156.320861816406,144.155822753906],[-64.3250427246094,-86.097900390625],[-5.93589782714844,-286.197692871094],[0,0]],
    [[0,0],[-156.320556640625,-144.156158447266],[117.604949951172,157.412414550781],[5.85025024414062,282.069396972656],[0,0]], false);
var BOLT_R = makeShape(
    [[515.656127929688,-536.037658691406],[208.413635253906,-409.39794921875],[61.8117065429688,-77.4943542480469],[-141.368255615234,237.626831054688],[-511.725891113281,522.025512695312]],
    [[0,0],[156.320434570312,144.15576171875],[106.706787109375,-12.8076477050781],[32.6275024414062,-284.393920898438],[-0.00003051757812,0]],
    [[0,0],[-156.320556640625,-144.156127929688],[-88.9872741699219,10.6808776855469],[-32.62744140625,284.397338867188],[-0.00006103515625,0]], false);

// ====================== БИЛДЕРЫ ======================

// моргачка — мерцание яркости (wiggle)
function buildMorgachka(comp){
    var L = addAdjust(comp, "моргачка", -0.9009009009009, 4.2042042042042);
    var fx = L.property("ADBE Effect Parade");
    try {
        var bc = fx.addProperty("ADBE Brightness & Contrast 2");
        var br = bc.property("ADBE Brightness & Contrast 2-0001");
        setConst(br, 0.18561451998139);
        setExpr(br, 'wiggle(6, 30)'); // мерцание (само-референс из дампа опущен как no-op)
    } catch(e){}
    return L;
}

// вспышка — рваный прямоугольник-контур + S_HueSatBright + S_Glow
function buildVspyshka(comp){
    var L = comp.layers.addShape();
    L.name = "вспышка";
    L.startTime = 0; L.inPoint = 4.5045045045045; L.outPoint = 4.83817150483817;
    try { L.blendingMode = BLEND_LIGHT; } catch(e){}
    try { L.collapseTransformation = true; } catch(e){}

    var root = L.property("ADBE Root Vectors Group");
    var grp = root.addProperty("ADBE Vector Group"); grp.name = "Прямоугольник 1";
    var cont = grp.property("ADBE Vectors Group");
    var rect = cont.addProperty("ADBE Vector Shape - Rect");
    setConst(rect.property("ADBE Vector Rect Size"), [1086.66666666667,1090]);
    var st = cont.addProperty("ADBE Vector Graphic - Stroke");
    setConst(st.property("ADBE Vector Stroke Color"), CONFIG.strokeColor);
    setConst(st.property("ADBE Vector Stroke Width"), 52);
    try { grp.property("ADBE Vector Transform Group").property("ADBE Vector Position").setValue([0,1.66666666666663]); } catch(e){}
    // Roughen (рваные края)
    var rgh = root.addProperty("ADBE Vector Filter - Roughen");
    setEffParam(rgh, "ADBE Vector Roughen Size", 53);
    setEffParam(rgh, "ADBE Vector Roughen Detail", 100);
    setEffParam(rgh, "ADBE Vector Temporal Freq", 51);

    var tr = L.property("ADBE Transform Group");
    setConst(tr.property("ADBE Anchor Point"), [0,0,0]);
    setConst(tr.property("ADBE Position"), [540,940,0]);          // точечная подгонка (скрин 4)
    setConst(tr.property("ADBE Scale"),    [150,215.2,100]);      // верт. растяжка вспышки
    setKeys(tr.property("ADBE Opacity"), [{time:4.5045045045045,val:34},{time:4.8048048048048,val:0}]);

    var fx = L.property("ADBE Effect Parade");
    try { var h = fx.addProperty("S_HueSatBright"); setEffParam(h, "S_HueSatBright-0050", 0.09); } catch(e){}
    try {
        var g = fx.addProperty("S_Glow");
        setEffParam(g, "S_Glow-0050", 16.19);  // Brightness
        setEffParam(g, "S_Glow-0052", 0.54);   // Threshold
        setEffParam(g, "S_Glow-0054", 199);    // Glow Width
        setEffParam(g, "S_Glow-0061", 0.86);   // Affect Alpha
    } catch(e){}
    return L;
}

// молния (общий билдер) — ломаный контур + обводка(85->0) + trim + Roughen + S_Glow
function buildBolt(comp, name, shape, layerPos, layerScale){
    var L = comp.layers.addShape();
    L.name = name;
    L.startTime = 0; L.inPoint = 4.2042042042042; L.outPoint = 4.83817150483817;
    try { L.blendingMode = BLEND_LIGHT; } catch(e){}
    try { L.collapseTransformation = true; } catch(e){}

    var root = L.property("ADBE Root Vectors Group");
    var grp = root.addProperty("ADBE Vector Group"); grp.name = "Фигура 1";
    var cont = grp.property("ADBE Vectors Group");
    var pg = cont.addProperty("ADBE Vector Shape - Group"); pg.name = "Контур 1";
    setConst(pg.property("ADBE Vector Shape"), shape);
    var st = cont.addProperty("ADBE Vector Graphic - Stroke");
    setConst(st.property("ADBE Vector Stroke Color"), CONFIG.strokeColor);
    setKeys(st.property("ADBE Vector Stroke Width"), [{time:4.27093760427094,val:85},{time:4.6046046046046,val:0}]);
    var trim = cont.addProperty("ADBE Vector Filter - Trim");
    setConst(trim.property("ADBE Vector Trim Start"), 0);
    // Конец (Trim End): с кадра 0 слоя = 0% -> через 7 кадров = 100% (прорисовка молнии).
    // Start и End НЕ коррелируют; анимируем именно КОНЕЦ.
    setKeys(trim.property("ADBE Vector Trim End"), [
        {time:4.2042042042042,        val:0},
        {time:4.43777110443777,       val:100}
    ]);
    try { grp.property("ADBE Vector Transform Group").property("ADBE Vector Scale").setValue([109.174005872275,98.898895085734]); } catch(e){}
    // Roughen (электрические рваные края), Size анимирован
    var rgh = root.addProperty("ADBE Vector Filter - Roughen");
    setEffKeys(rgh, "ADBE Vector Roughen Size", [{time:4.27093760427094,val:37},{time:4.53787120453787,val:76}]);

    var tr = L.property("ADBE Transform Group");
    setConst(tr.property("ADBE Position"), layerPos);
    setConst(tr.property("ADBE Scale"),    layerScale);

    var fx = L.property("ADBE Effect Parade");
    try { var g = fx.addProperty("S_Glow"); setEffParam(g, "S_Glow-0050", 8.41); setEffParam(g, "S_Glow-0054", 96); } catch(e){}
    return L;
}

// масштаб — эффект "Увеличение" (Magnify) 110->100
function buildMasshtab(comp){
    var L = addAdjust(comp, "масштаб", 4.5045045045045, 6.17283950617284);
    var fx = L.property("ADBE Effect Parade");
    try {
        var m = fx.addProperty("ADBE Magnify");
        setEffParam(m, "ADBE Magnify-0010", 2);    // Фигура (Квадрат)
        setEffParam(m, "ADBE Magnify-0002", 1080); // Размер
        setEffKeys (m, "ADBE Magnify-0003", [{time:4.5045045045045,val:110},{time:6.17283950617284,val:100}]); // Увеличение
    } catch(e){}
    return L;
}

// шейк — S_WarpRepeat
function buildSheik(comp){
    var L = addAdjust(comp, "шейк", 4.5045045045045, 6.17283950617284);
    var fx = L.property("ADBE Effect Parade");
    try {
        var w = fx.addProperty("S_WarpRepeat");
        setEffParam(w, "S_WarpRepeat-0050", 100);                 // Steps
        setEffParam(w, "S_WarpRepeat-0051", [40,1854.81480577257]); // Center XY
        setEffKeys (w, "S_WarpRepeat-0052", [{time:4.5045045045045,val:1.2},{time:6.17283950617284,val:1}]); // From Z Dist
        setEffParam(w, "S_WarpRepeat-0053", -1);  // From Rotate
        setEffParam(w, "S_WarpRepeat-0054", 10);  // From Shift X
        setEffParam(w, "S_WarpRepeat-0055", 10);  // From Shift Y
    } catch(e){}
    return L;
}

// ====================== RUN ======================
function main(){
    var comp = getTargetComp();
    var before = comp.numLayers;
    app.beginUndoGroup("Rebuild light");
    var built = [];
    try {
        // снизу-вверх => последний наверху
        built.push(buildSheik(comp));
        built.push(buildMasshtab(comp));
        built.push(buildBolt(comp, "правая молния", BOLT_R, [545.9,977.2,0], [-96.6,163.4,100]));   // точечная подгонка (скрин 3)
        built.push(buildBolt(comp, "левая молния",  BOLT_L, [319.1,990.2,0], [131.9,168.5,100]));   // точечная подгонка (скрин 2)
        built.push(buildVspyshka(comp));
        built.push(buildMorgachka(comp)); // -> верх группы

        // синхрон на дроп: сдвинуть весь хук-стек, чтобы якорь (HOOK_ANCHOR) попал на DROP_TIME
        if (DROP_TIME != null){ var off = DROP_TIME - HOOK_ANCHOR;
            for (var m = 0; m < built.length; m++){ try { built[m].startTime = built[m].startTime + off; } catch(e){} } }
        // положить весь хук-стек ПОД слой "Текст" (сохраняя внутренний порядок)
        var txt = findLayerByName(comp, PLACE_REF);
        if (txt){ for (var k = 0; k < built.length; k++){ try { built[k].moveAfter(txt); } catch(e){} } }
    } catch(err){
        logL("hook light err: " + err.toString() + " (стр " + (err.line||"?") + ")");
    } finally {
        app.endUndoGroup();
    }
    logL("hook light: добавлено " + (comp.numLayers-before) + " -> " + comp.name + (DROP_TIME!=null ? (" @drop "+DROP_TIME) : ""));
}
main();
