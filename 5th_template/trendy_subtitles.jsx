/**********************************************************************
 * trendy_subtitles.jsx — «trendy» субтитры: ОДНО слово по центру кадра.
 * --------------------------------------------------------------------
 * По дампу trendy/СУБТИТР: Montserrat-Bold, высокий (verticalScale 4),
 * tracking -55, белая заливка + чёрная обводка; аниматор Tracking 7->-1
 * на всю длину леера; Sapphire S_Gradient + S_DropShadow.
 *
 * Правила:
 *   - 1 слово = 1 слой, центр кадра (центровка по sourceRect — точный центр глифов)
 *   - если слово шире кадра -> уменьшаем fontSize, пока влезет
 *   - кейфреймы аниматора пересчитываются под длину каждого слоя
 *   - БЕЗ пауз: outPoint слова = старт следующего слова (слой за слоем)
 *
 * HEADLESS-инъекция (пайплайн blast): если задан $.global.__BLAST_SUBS_JSON —
 * берём данные оттуда (без файл-диалога), $.global.__BLAST_TARGET_COMP — имя
 * целевого компа. Иначе — INTERACTIVE/DEBUG-режим (ручное тестирование в AE).
 **********************************************************************/

// ============================ CONFIG ============================
var CONFIG = {
    INTERACTIVE:     true,
    DEBUG:           true,

    font:            "Montserrat-Bold",
    fontFallback:    "Arial-BoldMT",
    fontSize:        130,                  // базовый; уменьшается под ширину
    minFontSize:     40,                   // не мельче этого
    fitWidthFactor:  0.90,                 // макс. ширина = comp.width * этот фактор
    fitHeightFactor: 0.92,                 // макс. высота = comp.height * этот фактор

    fillColor:       [1, 1, 1],
    applyStroke:     true,
    strokeColor:     [0, 0, 0],
    strokeWidth:     5,
    strokeOverFill:  false,                // обводка ПОД заливкой
    tracking:        -55,
    leading:         130,
    verticalScale:   4,                    // 400% — высокие буквы (trendy)
    horizontalScale: 1,
    uppercase:       true,
    // Y-центровка по КАПУ, не по bbox глифов (иначе ц/й/щ/выносные «плывут»).
    // position.y = comp.height/2 + fontSize*verticalScale*yOffsetRatio.
    // 0.346 даёт для 130*4 ≈ +180px (как ты выставлял вручную). Динамично от компа.
    yOffsetRatio:    0.346,

    // аниматор Tracking Amount: start -> end за длину слоя
    trackStart:      7,
    trackEnd:        -1,
    trackInfluence:  33.333333,

    lastWordTail:    0.30,                 // на сколько держать последнее слово (с)

    // Sapphire-эффекты (скаляры; дропдауны Combine/Mask = NO_VALUE, не ставятся)
    applyEffects:    true,
    gradStartXY:     [540, 772],
    gradEndXY:       [565.33332824707, 1569.66667175293],
    gradStartColor:  [1, 1, 1, 1],
    gradEndColor:    [0.00392156885937, 0.00392156885937, 0.00392156885937, 1],
    gradBrightness:  1.26,
    shadowColor:     [0, 0, 0, 1],
    shadowOpacity:   1.14,
    shadowBlur:      100,

    // ---- настройки текста из движка (app/subtitle_font_layout.trendy_layout) ----
    // Дефолты = прод. Движок присылает $.global.__BLAST_STYLE — ключи строго из CONFIG.
    strictFont:      false,                // true: шрифт обязан быть в AE без подмены (иначе ошибка)
    applyShadow:     true,
    alignX:          "center",             // center | left | right
    marginX:         0.05,                 // поле кадра по X для left/right (доля ширины)
    centerYFrac:     0.5,                  // центр слова по Y (доля высоты компа)
    focusFillColor:  null,                 // [r,g,b] — фокус-слова акцентным цветом
    accent:          null                  // {font,fontSize,verticalScale,tracking,yOffsetRatio} — фокус-слово шрифтом пары
};
// ================================================================

function log(m){ try { $.writeln("[trendy] " + m); } catch(e){} }
// No alert(): bundled into the render-node project, where a modal blocks AE
// until someone clicks it and nobody is watching.
function say(m){ log(m); }
function setP(e, mn, v){ try { e.property(mn).setValue(v); return true; } catch(x){ return false; } }

function injectedData(){
    try { if (typeof $.global.__BLAST_SUBS_JSON !== "undefined" && $.global.__BLAST_SUBS_JSON) return $.global.__BLAST_SUBS_JSON; } catch(e){}
    return null;
}
// Настройки из движка: неизвестный ключ — ошибка, а не молчаливый игнор (No Fallback).
function applyInjectedStyle(){
    var st = null;
    try { if (typeof $.global.__BLAST_STYLE !== "undefined" && $.global.__BLAST_STYLE) st = $.global.__BLAST_STYLE; } catch(e){}
    if (!st) return false;
    for (var k in st){
        if (!st.hasOwnProperty(k)) continue;
        if (!CONFIG.hasOwnProperty(k)) throw new Error("trendy: unknown style key " + k);
        CONFIG[k] = st[k];
    }
    return true;
}
// doc.font возвращает запрошенное имя даже при подмене — проверяем реестр шрифтов AE.
function requireRealFont(ps){
    var found = app.fonts.getFontsByPostScriptName(ps);
    if (!found || found.length === 0) throw new Error("trendy: font not resolvable in AE: " + ps);
    for (var k = 0; k < found.length; k++) if (!found[k].isSubstitute) return;
    throw new Error("trendy: font is substituted in AE: " + ps);
}
function wFocus(w){ return !!(w && w.focus); }
// стиль слова: основной или акцентный (фокус-слово шрифтом пары)
function wordStyle(isFocus){
    var a = (isFocus && CONFIG.accent) ? CONFIG.accent : null;
    return {
        font:          a ? a.font : CONFIG.font,
        fontSize:      a ? a.fontSize : CONFIG.fontSize,
        verticalScale: a ? a.verticalScale : CONFIG.verticalScale,
        tracking:      a ? a.tracking : CONFIG.tracking,
        yOffsetRatio:  a ? a.yOffsetRatio : CONFIG.yOffsetRatio,
        uppercase:     a ? false : CONFIG.uppercase,
        fillColor:     (isFocus && CONFIG.focusFillColor) ? CONFIG.focusFillColor : CONFIG.fillColor
    };
}
function justificationFor(align){
    if (align === "left") return ParagraphJustification.LEFT_JUSTIFY;
    if (align === "right") return ParagraphJustification.RIGHT_JUSTIFY;
    if (align === "center") return ParagraphJustification.CENTER_JUSTIFY;
    throw new Error("trendy: unknown alignX " + align);
}
function injectedFill(){
    try { if (typeof $.global.__BLAST_FILL !== "undefined" && $.global.__BLAST_FILL && $.global.__BLAST_FILL.length >= 3) return $.global.__BLAST_FILL; } catch(e){}
    return null;
}
function pickFile(){
    // No File.openDialog(): on the render node it would hang AE forever.
    try { var sf = new File($.fileName); return new File(sf.parent.fsName + "/subtitles.json"); } catch(e){ return null; }
}
function readJSON(jf){
    jf.encoding = "UTF-8"; if (!jf.open("r")) return null;
    var raw = jf.read(); jf.close();
    if (raw && raw.charCodeAt(0) === 0xFEFF) raw = raw.substring(1);
    try { return eval("(" + raw + ")"); } catch(e){ say("JSON parse error: " + e); return null; }
}
function extractWords(data){
    if (data instanceof Array) return data;
    if (data && typeof data === "object"){
        var keys = ["word_timings", "words", "subtitles", "segments", "tokens"];
        for (var i = 0; i < keys.length; i++) if (data[keys[i]] instanceof Array) return data[keys[i]];
    }
    return null;
}
function wWord(w){ var v = (w.word != null) ? w.word : (w.text != null ? w.text : w.w); return String(v == null ? "" : v); }
function wStart(w){ var v = (w.start != null) ? w.start : (w.t_start != null ? w.t_start : w.s); return Number(v); }
function wEnd(w){ var v = (w.end != null) ? w.end : (w.t_end != null ? w.t_end : w.e); return Number(v); }
function targetCompName(){
    try { if (typeof $.global.__BLAST_TARGET_COMP !== "undefined" && $.global.__BLAST_TARGET_COMP) return String($.global.__BLAST_TARGET_COMP); } catch(e){}
    return null;
}
function findComp(){
    var want = targetCompName();
    if (want){ for (var n = 1; n <= app.project.numItems; n++){ var c = app.project.item(n); if (c instanceof CompItem && c.name === want) return c; } }
    var a = app.project.activeItem; if (a && a instanceof CompItem) return a;
    for (var i = 1; i <= app.project.numItems; i++){ var it = app.project.item(i); if (it instanceof CompItem) return it; }
    return null;
}

function styleDoc(td, size, ws){
    td.resetCharStyle();
    td.font          = ws.font;
    td.fontSize      = size;
    td.applyFill     = true;
    td.fillColor     = ws.fillColor;
    td.applyStroke   = CONFIG.applyStroke;
    if (CONFIG.applyStroke){
        td.strokeColor = CONFIG.strokeColor;
        td.strokeWidth = CONFIG.strokeWidth;
        try { td.strokeOverFill = CONFIG.strokeOverFill; } catch (e0) {}
    }
    td.tracking      = ws.tracking;
    try { td.autoLeading = false; } catch (e1) {}
    try { td.leading = CONFIG.leading; } catch (e2) {}
    try { td.verticalScale   = ws.verticalScale;   } catch (e3) {}
    try { td.horizontalScale = CONFIG.horizontalScale; } catch (e4) {}
    td.justification = justificationFor(CONFIG.alignX);
    return td;
}

function addSapphire(L){
    if (!CONFIG.applyEffects) return;
    var fx = L.property("ADBE Effect Parade");
    try {
        var g = fx.addProperty("S_Gradient");
        setP(g, "S_Gradient-0050", CONFIG.gradStartXY);
        setP(g, "S_Gradient-0051", CONFIG.gradEndXY);
        setP(g, "S_Gradient-0052", CONFIG.gradStartColor);  // Start Color (если задаётся)
        setP(g, "S_Gradient-0053", CONFIG.gradEndColor);
        setP(g, "S_Gradient-0054", CONFIG.gradBrightness);
        setP(g, "S_Gradient-0057", 0);
        // Combine=Grad Only / Mask Use=Luma — NO_VALUE, JSX не ставит (дефолт)
    } catch (eG) {}
    if (CONFIG.applyShadow) try {
        var d = fx.addProperty("S_DropShadow");
        setP(d, "S_DropShadow-0050", CONFIG.shadowColor);   // Shadow Color (если задаётся)
        setP(d, "S_DropShadow-0051", CONFIG.shadowOpacity);
        setP(d, "S_DropShadow-0052", CONFIG.shadowBlur);
        setP(d, "S_DropShadow-0053", 0);
        setP(d, "S_DropShadow-0054", 0);
    } catch (eD) {}
}

// ============================ MAIN ============================
(function(){
    if (!app.project){ say("нет открытого проекта"); return; }
    var __fill = injectedFill(); if (__fill) CONFIG.fillColor = __fill;  // blast: custom subtitle color
    applyInjectedStyle();  // blast: настройки текста визарда (движок) — поверх прод-дефолтов
    if (CONFIG.strictFont){
        requireRealFont(CONFIG.font);
        if (CONFIG.accent) requireRealFont(CONFIG.accent.font);
    }
    var data = injectedData();
    if (!data){
        var jf = pickFile(); if (!jf){ say("файл не выбран"); return; }
        if (!jf.exists){ say("файл не найден: " + jf.fsName); return; }
        data = readJSON(jf);
    }
    if (!data){ return; }
    var words = extractWords(data);
    if (!words || !words.length){ say("не нашёл массив слов ([{word,start,end}] или {\"word_timings\":[...]})"); return; }
    var comp = findComp(); if (!comp){ say("нет активной композиции — открой комп и запусти снова"); return; }

    var CW = comp.width, CH = comp.height;
    var maxW = CW * CONFIG.fitWidthFactor;
    var maxH = CH * CONFIG.fitHeightFactor;
    var cx = CW / 2, cy = CH * CONFIG.centerYFrac;
    if (CONFIG.alignX === "left") cx = CW * CONFIG.marginX;
    else if (CONFIG.alignX === "right") cx = CW * (1 - CONFIG.marginX);

    app.beginUndoGroup("Trendy Subtitles");
    var made = 0, fitDowns = 0, firstErr = "";
    try {
        for (var i = 0; i < words.length; i++){
            var raw = wWord(words[i]); if (!raw.length) continue;
            var ws = wordStyle(wFocus(words[i]));
            var phrase = ws.uppercase ? raw.toUpperCase() : raw.toLowerCase();

            // тайминг: без пауз — держим до старта следующего слова
            var t0 = wStart(words[i]); if (isNaN(t0)) t0 = (made ? 0 : 0);
            var t1;
            if (i + 1 < words.length){ t1 = wStart(words[i + 1]); }
            else { t1 = wEnd(words[i]); if (isNaN(t1)) t1 = t0 + CONFIG.lastWordTail; t1 += CONFIG.lastWordTail; }
            if (isNaN(t1) || t1 <= t0) t1 = t0 + 0.1;

            try {
                var L = comp.layers.addText(phrase);
                L.name = "trendy " + (i + 1) + " " + phrase;
                L.motionBlur = false;
                // Strobe Ч/Б: Difference → белый текст авто-инвертируется под фоном.
                try {
                    var __bl = ($.global && $.global.__BLAST_SUBS_BLEND) ? String($.global.__BLAST_SUBS_BLEND).toLowerCase() : "";
                    if (__bl === "difference") L.blendingMode = BlendingMode.DIFFERENCE;
                } catch (eBl){}

                var stProp = L.property("ADBE Text Properties").property("ADBE Text Document");

                // --- стиль + авто-уменьшение под ширину/высоту ---
                var size = ws.fontSize, rect = null, pass = 0;
                while (pass < 4){
                    stProp.setValue(styleDoc(stProp.value, size, ws));
                    rect = L.sourceRectAtTime(0, false);
                    var sw = maxW / rect.width, sh = maxH / rect.height;
                    var s = Math.min(sw, sh);
                    if (s >= 1.0 || size <= CONFIG.minFontSize) break;     // влезает
                    size = Math.max(CONFIG.minFontSize, Math.floor(size * s * 0.99));
                    pass++; fitDowns++;
                }
                // шрифт-fallback (прод); при strictFont шрифт проверен заранее — подмены нет
                if (!CONFIG.strictFont) try { var chk = stProp.value; if (String(chk.font) !== CONFIG.font){ chk.font = CONFIG.fontFallback; stProp.setValue(chk); rect = L.sourceRectAtTime(0, false); } } catch (eF) {}

                // --- центровка: X через center-justify (anchor.x=0), Y по БАЗОВОЙ
                //     ЛИНИИ (anchor.y=0) + смещение на полу-высоту капса. Базовая
                //     линия стабильна -> кор текста по центру независимо от выносных
                //     (ц/й/щ/descenders). Всё динамично от размера компа.
                var yOff = size * ws.verticalScale * ws.yOffsetRatio;
                // left/right: край чернил — ровно на поле (боковой отступ глифа не в счёт)
                var ax = 0;
                if (CONFIG.alignX === "left") ax = rect.left;
                else if (CONFIG.alignX === "right") ax = rect.left + rect.width;
                L.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([ax, 0]);
                L.property("ADBE Transform Group").property("ADBE Position").setValue([cx, cy + yOff]);

                // --- тайминг слоя (без пауз) ---
                L.inPoint  = t0;
                L.outPoint = Math.min(comp.duration, t1);

                // --- аниматор Tracking Amount: trackStart -> trackEnd за длину слоя ---
                var anim = L.property("ADBE Text Properties").property("ADBE Text Animators").addProperty("ADBE Text Animator");
                anim.name = "Аниматор 1";
                var trk = anim.property("ADBE Text Animator Properties").addProperty("ADBE Text Tracking Amount");
                trk.setValueAtTime(L.inPoint,  CONFIG.trackStart);
                trk.setValueAtTime(L.outPoint, CONFIG.trackEnd);
                for (var k = 1; k <= trk.numKeys; k++){
                    try {
                        trk.setInterpolationTypeAtKey(k, KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER);
                        trk.setTemporalEaseAtKey(k, [new KeyframeEase(0, CONFIG.trackInfluence)], [new KeyframeEase(0, CONFIG.trackInfluence)]);
                    } catch (eK) {}
                }

                addSapphire(L);
                made++;
            } catch (eLayer){ if (!firstErr) firstErr = String(eLayer) + " (стр " + (eLayer.line || "?") + ")"; }
        }
    } catch (err){ firstErr = String(err); }
    finally { app.endUndoGroup(); }

    var msg = "готово: слоёв " + made + " / слов " + words.length +
              "\nуменьшений под кадр: " + fitDowns + "\nкомп: " + comp.name + " " + CW + "x" + CH;
    if (firstErr) msg += "\n⚠ первая ошибка: " + firstErr;
    say(msg);
    // итог прогона — для лабов/диагностики (alert на ноде нельзя)
    try { $.global.__BLAST_SUBS_RESULT = msg; } catch (eR) {}
})();
