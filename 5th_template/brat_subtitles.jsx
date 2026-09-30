/**********************************************************************
 * brat_subtitles.jsx — Brat-субтитры (charli xcx) + jakson-аниматор + моргачка
 * --------------------------------------------------------------------
 * Word-level JSON -> блоки (≤MAX_LINES строк, по WORDS_PER_LINE слов) ->
 * box-text слой на блок: full-justify, пословное раскрытие (Range Selector
 * по словам + PercentStart, word-точная математика), Minimax + Gaussian Blur.
 *
 * НОВОЕ:
 *  - всё строится в ОТДЕЛЬНОМ компе «СУБТИТРЫ» (иначе adj-моргачка цепляет
 *    футаж); комп вкладывается в активный поверх видео.
 *  - «моргачка» = adjustment-слой с CC Image Wipe, кейфреймы генерятся по BPM
 *    (один блинк на бит, пик в центре бита), растянут на всю длину текста.
 *  - центровка динамическая от размера компа (не хардкод 1080x1920).
 *
 * HEADLESS-инъекция (пайплайн blast): $.global.__BLAST_SUBS_JSON — данные без
 * файл-диалога; $.global.__BLAST_TARGET_COMP — целевой комп; $.global.__BLAST_BPM
 * — BPM трека для моргачки. Иначе — INTERACTIVE/DEBUG (ручное тестирование в AE).
 **********************************************************************/

// ============================ CONFIG ============================
var CONFIG = {
    INTERACTIVE:     true,
    DEBUG:           true,

    font:            "ArialNarrow",
    fontFallback:    "Arial-BoldMT",
    fontSize:        130,
    minFontSize:     56,                   // нижняя граница глобального fit
    fitMargin:       0.97,                 // доля BOX_W, в которую должна влезть самая широкая строка
    leading:         130,
    tracking:        -20,
    fillColor:       [1, 1, 1],

    WORDS_PER_LINE:  2,
    MAX_LINES:       4,

    boxWFactor:      0.80,
    boxHFactor:      0.50,
    scale:           [80, 80, 100],
    // Центровка: меряем реальные границы текста (sourceRect) и ставим его центр
    // в центр кадра -> работает на ЛЮБОМ размере/пропорции компа (1:1, 9:16...).
    // yNudge — вертикальный сдвиг: 0 = ровно по центру; >0 ниже (lower-third).
    yNudge:          0,

    blurRadius:      10,
    minimaxRadius:   15,
    minimaxChannel:  2,
    fps:             30,
    tailFrames:      0,
    revealLeadFrames: 0,

    // ---- отдельный комп под текст ----
    separateComp:    true,
    textCompName:    "СУБТИТРЫ",
    emptyTemplateCompName: "\u0422\u0435\u043a\u0441\u0442",
    nestIntoActive:  true,                 // вложить комп субтитров в активный (поверх футажа)

    // ---- моргачка (CC Image Wipe, по BPM) ----
    // 2026-06-22: вернули ON, но мягче — дело было в ИНТЕНСИВНОСТИ, не скорости.
    // peak (глубина wipe на пике) снижен 0.8→0.4, край мягче (softness 0.03→0.08),
    // скорость возвращена к исходной (subdiv 4→2 = 1/8).
    blinker:         true,
    bpm:             120,                  // BPM трека (дамп был на 120)
    beatOffset:      0,                    // время первого бита, с (фаза)
    blinkSubdiv:     2,                    // блинков на долю: 2 = 1/8, 4 = 1/16
    blinkPeak:      0.4,                   // пик Completion (CC Image Wipe), мягче
    blinkBorderSoftness: 0.08,
    blinkInfluence:  33.333333,

    // ---- локальное появление каждой визуальной строки ----
    transitionBlur:          true,
    transitionBlurFrames:    6,
    transitionBlurDirection: 90,
    transitionBlurLength:    50,

    // Тень под КАЖДЫМ словом (смотр 2026-09-30: плашка под строкой — грязное
    // пятно). ADBE Drop Shadow на тексте внутри прекомпа слова — появляется вместе
    // со словом. Opacity 0..255. Поле прекомпа (transitionBlurLength+12) вмещает
    // distance+softness.
    wordShadow:             true,
    wordShadowOpacity:      150,
    wordShadowDirection:    180,           // вниз
    wordShadowDistance:     6,
    wordShadowSoftness:     22,

    // Фокус-слово курсивом (null = как остальные). focusFauxItalic — наклон тем же шрифтом.
    focusFont:              null,
    focusFauxItalic:        false,

    // ---- настройки текста из движка (app/subtitle_font_layout.brat_layout) ----
    strictFont:             false,         // true: ArialNarrow обязан быть в AE без подмены
    centerXFrac:            0.5,           // центр бокса по X (доля ширины компа)
    centerYFrac:            0.5,           // центр блока по Y (доля высоты компа)
    focusFillColor:         null,          // [r,g,b] — фокус-слова акцентным цветом

    // Прежняя плашка под строкой (выключена; оставлена для сравнения в лабе).
    contrastPlate:          false,
    contrastCompName:       "BRAT CONTRAST",
    contrastOpacity:        20,
    contrastPadX:           28,
    contrastPadY:           8,
    contrastRoundness:      28,
    contrastRadialAmount:   40,
    contrastRadialType:     1,             // Spin
    contrastRadialAA:       2,             // High
    contrastRadialSeed:     0,             // AE 2025 exposes seed as NO_VALUE; keep effect default
    contrastHeightFactor:   0.78
};
// ================================================================

var BZ = KeyframeInterpolationType.BEZIER;

function log(m){ try { $.writeln("[brat] " + m); } catch(e){} }
// No alert(): this script is bundled into the render-node project, where a
// modal blocks AE until someone clicks it and nobody is watching.
function say(m){ log(m); }

function injectedData(){
    try { if (typeof $.global.__BLAST_SUBS_JSON !== "undefined" && $.global.__BLAST_SUBS_JSON) return $.global.__BLAST_SUBS_JSON; } catch(e){}
    return null;
}
function injectedFill(){
    try { if (typeof $.global.__BLAST_FILL !== "undefined" && $.global.__BLAST_FILL && $.global.__BLAST_FILL.length >= 3) return $.global.__BLAST_FILL; } catch(e){}
    return null;
}
// Настройки из движка: неизвестный ключ — ошибка, а не молчаливый игнор (No Fallback).
function applyInjectedStyle(){
    var st = null;
    try { if (typeof $.global.__BLAST_STYLE !== "undefined" && $.global.__BLAST_STYLE) st = $.global.__BLAST_STYLE; } catch(e){}
    if (!st) return false;
    for (var k in st){
        if (!st.hasOwnProperty(k)) continue;
        if (!CONFIG.hasOwnProperty(k)) throw new Error("brat: unknown style key " + k);
        CONFIG[k] = st[k];
    }
    return true;
}
function requireRealFont(ps){
    var found = app.fonts.getFontsByPostScriptName(ps);
    if (!found || found.length === 0) throw new Error("brat: font not resolvable in AE: " + ps);
    for (var k = 0; k < found.length; k++) if (!found[k].isSubstitute) return;
    throw new Error("brat: font is substituted in AE: " + ps);
}
// голос хука (F5/F1) помечен focus целиком — стилем фокус-слова его не красим
function wFocus(w){ return !!(w && w.focus && !w.voice); }
// Шрифт слова: фокус-слово — курсивом (CONFIG.focusFont / focusFauxItalic).
function wordFont(isFocus){ return (isFocus && CONFIG.focusFont) ? CONFIG.focusFont : CONFIG.font; }
function wordFaux(isFocus){ return !!(isFocus && CONFIG.focusFauxItalic); }
function addWordShadow(fx){
    var ds = fx.addProperty("ADBE Drop Shadow");
    if (!ds) throw new Error("word shadow: ADBE Drop Shadow is unavailable");
    ds.property("ADBE Drop Shadow-0001").setValue([0, 0, 0, 1]);
    ds.property("ADBE Drop Shadow-0002").setValue(CONFIG.wordShadowOpacity);
    ds.property("ADBE Drop Shadow-0003").setValue(CONFIG.wordShadowDirection);
    ds.property("ADBE Drop Shadow-0004").setValue(CONFIG.wordShadowDistance);
    ds.property("ADBE Drop Shadow-0005").setValue(CONFIG.wordShadowSoftness);
    return ds;
}
function injectedBpm(){
    try { if (typeof $.global.__BLAST_BPM !== "undefined" && $.global.__BLAST_BPM){ var b = Number($.global.__BLAST_BPM); if (b > 0) return b; } } catch(e){}
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
function wVoice(w){ return !!(w && w.voice); }  // hook voice phrase (F5/F1) — own container

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

function isGeneratedBratCompName(name){
    var n = String(name || "");
    return n === CONFIG.textCompName || n === CONFIG.contrastCompName ||
           n.indexOf(CONFIG.textCompName + " / BRAT WORD ") === 0;
}

// Make repeated/manual execution deterministic. Remove only comps produced by
// this BRAT generator and only the known empty template Text layer. A populated
// Text precomp is user content and is preserved.
function cleanupPreviousBrat(srcComp){
    var removedLayers = 0, removedComps = 0, removedEmptyText = 0;
    var li, layer, source;

    for (li = srcComp.numLayers; li >= 1; li--){
        layer = srcComp.layer(li);
        source = null;
        try { source = layer.source; } catch (eSource) {}
        if (source && source instanceof CompItem && isGeneratedBratCompName(source.name)){
            layer.remove();
            removedLayers++;
            continue;
        }
        if (source && source instanceof CompItem && source.numLayers === 0 &&
            (String(layer.name) === CONFIG.emptyTemplateCompName ||
             String(source.name) === CONFIG.emptyTemplateCompName)){
            layer.remove();
            removedEmptyText++;
        }
    }

    for (var pi = app.project.numItems; pi >= 1; pi--){
        var item = app.project.item(pi);
        if (item === srcComp) continue;
        if (item instanceof CompItem && isGeneratedBratCompName(item.name)){
            item.remove();
            removedComps++;
        }
    }
    log("cleanup: layers=" + removedLayers +
        " comps=" + removedComps + " emptyText=" + removedEmptyText);
    return { layers: removedLayers, comps: removedComps, emptyText: removedEmptyText };
}

function _packRun(run, wpl, maxLines, isVoice){
    // 2 words per line; an odd tail word is MERGED into the previous line (→ a
    // 3-word last line) so there is never a lone 1-word line — every line has
    // ≥2 words. A genuine 1-word block (whole run = 1 word) is the only case
    // left with <2 words, handled by LEFT-justify downstream.
    var perBlock = wpl * maxLines, blocks = [], i, j;
    for (i = 0; i < run.length; i += perBlock){
        var slice = run.slice(i, Math.min(i + perBlock, run.length));
        var lines = [];
        for (j = 0; j < slice.length; j += wpl) lines.push(slice.slice(j, Math.min(j + wpl, slice.length)));
        if (lines.length > 1 && lines[lines.length - 1].length < wpl){
            var tail = lines.pop(); var prev = lines[lines.length - 1];
            for (j = 0; j < tail.length; j++) prev.push(tail[j]);
        }
        blocks.push({ words: slice, lines: lines, voice: !!isVoice });
    }
    return blocks;
}
function packBlocks(words, wpl, maxLines){
    // Split into runs of same voice-flag FIRST so a hook voice phrase (F5/F1)
    // never shares a text container with track subtitles, then pack each run.
    var blocks = [], i, run = [], runVoice = null;
    for (i = 0; i < words.length; i++){
        var v = wVoice(words[i]);
        if (runVoice === null) runVoice = v;
        if (v !== runVoice){
            blocks = blocks.concat(_packRun(run, wpl, maxLines, runVoice));
            run = []; runVoice = v;
        }
        run.push(words[i]);
    }
    if (run.length) blocks = blocks.concat(_packRun(run, wpl, maxLines, runVoice));
    return blocks;
}
function blockText(block){
    var rows = [], i, j;
    for (i = 0; i < block.lines.length; i++){
        var ws = []; for (j = 0; j < block.lines[i].length; j++) ws.push(wWord(block.lines[i][j]));
        rows.push(ws.join(" "));
    }
    return rows.join("\r").toLowerCase();
}

function addRevealAnimator(L, slice, t0){
    var tp = L.property("ADBE Text Properties");
    var anim = tp.property("ADBE Text Animators").addProperty("ADBE Text Animator");
    anim.name = "Аниматор 1";
    anim.property("ADBE Text Animator Properties").addProperty("ADBE Text Opacity").setValue(0);
    var sel = anim.property("ADBE Text Selectors").addProperty("ADBE Text Selector");
    var adv = sel.property("ADBE Text Range Advanced");
    try { adv.property("ADBE Text Range Type2").setValue(3); } catch (e1) {}
    try { adv.property("ADBE Text Selector Smoothness").setValue(0); } catch (e2) {}
    var ps = sel.property("ADBE Text Percent Start");
    var n = slice.length, fr = 1.0 / CONFIG.fps, prevT = -1e9, i;
    function pct(k){ return (k / n) * 100.0; }
    for (i = 0; i < n; i++){
        var ws = wStart(slice[i]); if (isNaN(ws)) ws = t0;
        var holdT = ws + CONFIG.revealLeadFrames * fr;
        if (holdT <= prevT) holdT = prevT + fr * 0.5;
        var jumpT = holdT + fr;
        ps.setValueAtTime(holdT, pct(i));
        ps.setValueAtTime(jumpT, pct(i + 1));
        prevT = jumpT;
    }
}

// After the tail-merge the only block left with a <2-word line is a GENUINE
// 1-word block (the whole run is one word, e.g. a short voice phrase). It can't
// be paired, so LEFT-justify it (natural width, no letter-stretch) instead of
// full-justify. Everything else has ≥2 words per line → full-justify.
function blockNeedsLeft(block){
    return block.words.length < 2;
}
function styleText(L, justify, fontSize, fillColor, font, fauxItalic){
    var want = font || CONFIG.font;
    var stProp = L.property("ADBE Text Properties").property("ADBE Text Document");
    var td = stProp.value;
    td.resetCharStyle();
    td.font          = want;
    td.fontSize      = fontSize || CONFIG.fontSize;
    td.applyFill     = true;
    td.fillColor     = fillColor || CONFIG.fillColor;
    td.applyStroke   = false;
    td.tracking      = CONFIG.tracking;
    try { td.autoLeading = false; } catch (eA) {}
    try { td.leading     = (fontSize || CONFIG.fontSize) * (CONFIG.leading / CONFIG.fontSize); } catch (eL) {}
    td.justification = justify || ParagraphJustification.FULL_JUSTIFY_LASTLINE_FULL;
    if (fauxItalic) td.fauxItalic = true;
    stProp.setValue(td);
    if (!CONFIG.strictFont) try { var chk = stProp.value; if (String(chk.font) !== want){ chk.font = CONFIG.fontFallback; stProp.setValue(chk); } } catch (eF) {}
}

// GLOBAL fit: measure the widest line across ALL blocks and return one fontSize
// for the WHOLE video so the widest line fits the box → no auto-wrap anywhere →
// every visual line keeps its 2–3 words, and the subtitle size is uniform across
// the clip (no per-block jumping). Line width = sum of word widths (each in its
// own font: the focus word may be italic) + spaces.
function computeFitFontSize(tcomp, blocks, boxW){
    var probe = tcomp.layers.addText("x");
    var sp = probe.property("ADBE Text Properties").property("ADBE Text Document");
    function width(text, font, faux){
        var td = sp.value;
        td.resetCharStyle();
        td.text = text;
        td.font = font;
        td.fontSize = CONFIG.fontSize;
        td.tracking = CONFIG.tracking;
        td.applyStroke = false;
        if (faux) td.fauxItalic = true;
        sp.setValue(td);
        return probe.sourceRectAtTime(0, false).width;
    }
    var space = width("\u043d \u043d", CONFIG.font, false) - width("\u043d\u043d", CONFIG.font, false);
    var maxW = 1, b, i, j;
    for (b = 0; b < blocks.length; b++){
        for (i = 0; i < blocks[b].lines.length; i++){
            var line = blocks[b].lines[i], w = 0;
            for (j = 0; j < line.length; j++){
                var t = wWord(line[j]).toLowerCase(); if (!t.length) continue;
                var f = wFocus(line[j]);
                w += width(t, wordFont(f), wordFaux(f)) + (j ? space : 0);
            }
            if (w > maxW) maxW = w;
        }
    }
    try { probe.remove(); } catch (e) {}
    var avail = boxW * (CONFIG.fitMargin || 0.97);
    if (maxW > avail) return Math.max(CONFIG.minFontSize || 40, CONFIG.fontSize * (avail / maxW));
    return CONFIG.fontSize;
}

// Six-frame adjustment-layer transition between subtitle "pages". The cut is
// the first word of the incoming block: the outgoing full 4-line block stays
// visible up to the cut, blur grows 0 -> 50 over the first 3 frames, then the
// incoming block resolves 50 -> 0 over the next 3. Because subtitle pages do
// not overlap, the adjustment layer affects only the page visible at that time.
// Each visual row lives in its own precomp. Its adjustment layer therefore
// affects only that row, never the already-visible rows above or below it.
// The row enters with Blur Length 50 and resolves to 0 over exactly 6 frames.
function addLineContrastPlate(contrastComp, lineIn, lineOut, rowX, rowY, lineStep, boxW, index){
    if (lineOut <= lineIn) throw new Error("contrast plate: invalid span for line " + index);
    var plateW = boxW * (CONFIG.scale[0] / 100.0) + CONFIG.contrastPadX * 2;
    var plateH = lineStep * CONFIG.contrastHeightFactor + CONFIG.contrastPadY * 2;

    var L = contrastComp.layers.addShape();
    L.name = "BRAT contrast line " + index;
    L.motionBlur = false;
    L.inPoint = lineIn;
    L.outPoint = lineOut;

    var root = L.property("ADBE Root Vectors Group");
    var group = root.addProperty("ADBE Vector Group");
    group.name = "contrast plate";
    var vectors = group.property("ADBE Vectors Group");
    var rect = vectors.addProperty("ADBE Vector Shape - Rect");
    rect.property("ADBE Vector Rect Size").setValue([plateW, plateH]);
    rect.property("ADBE Vector Rect Roundness").setValue(CONFIG.contrastRoundness);
    var fill = vectors.addProperty("ADBE Vector Graphic - Fill");
    fill.property("ADBE Vector Fill Color").setValue([0, 0, 0]);
    fill.property("ADBE Vector Fill Opacity").setValue(100);

    var tg = L.property("ADBE Transform Group");
    tg.property("ADBE Position").setValue([rowX, rowY, 0]);
    tg.property("ADBE Opacity").setValue(CONFIG.contrastOpacity);

    var fx = L.property("ADBE Effect Parade");
    var radial = fx.addProperty("ADBE Radial Blur");
    if (!radial) throw new Error("contrast plate: Radial Blur is unavailable");
    var amount = radial.property("ADBE Radial Blur-0001");
    var center = radial.property("ADBE Radial Blur-0002");
    var type = radial.property("ADBE Radial Blur-0003");
    var antialias = radial.property("ADBE Radial Blur-0004");
    if (!amount || !center || !type || !antialias){
        throw new Error("contrast plate: Radial Blur properties are unavailable");
    }
    amount.setValue(CONFIG.contrastRadialAmount);
    center.setValue([contrastComp.width / 2, contrastComp.height / 2]);
    type.setValue(CONFIG.contrastRadialType);
    antialias.setValue(CONFIG.contrastRadialAA);
    // Random Seed stays at the native default 0. ADBE Radial Blur-0005 is
    // PropertyValueType.NO_VALUE in AE 2025 and cannot be read or written.
    return L;
}

function addWordDirectionalBlur(wordComp, visibleDuration, frameDuration, index){
    var frames = Math.max(1, Math.round(CONFIG.transitionBlurFrames));
    var tOut = Math.min(wordComp.duration, visibleDuration, frames * frameDuration);
    if (tOut <= 0) throw new Error("directional blur: word " + index + " has no visible span");

    var L = wordComp.layers.addSolid(
        [1, 1, 1],
        "BRAT word blur " + index,
        wordComp.width,
        wordComp.height,
        wordComp.pixelAspect
    );
    L.adjustmentLayer = true;
    L.startTime = 0;
    L.inPoint = 0;
    L.outPoint = tOut;
    L.moveToBeginning();

    var fx = L.property("ADBE Effect Parade");
    var db = fx.addProperty("ADBE Motion Blur");
    if (!db) throw new Error("directional blur: ADBE Motion Blur is unavailable");
    var direction = db.property("ADBE Motion Blur-0001");
    var length = db.property("ADBE Motion Blur-0002");
    if (!direction || !length) throw new Error("directional blur: effect properties are unavailable");

    direction.setValue(CONFIG.transitionBlurDirection);
    length.setValueAtTime(0, CONFIG.transitionBlurLength);
    length.setValueAtTime(tOut, 0);
    for (var ki = 1; ki <= length.numKeys; ki++){
        length.setInterpolationTypeAtKey(ki, BZ, BZ);
        length.setTemporalEaseAtKey(
            ki,
            [new KeyframeEase(0, 33.333333)],
            [new KeyframeEase(0, 33.333333)]
        );
    }
    return L;
}

function addBlinker(tcomp, spanIn, spanOut){
    var L = tcomp.layers.addSolid([1, 1, 1], "моргачка", tcomp.width, tcomp.height, tcomp.pixelAspect);
    L.adjustmentLayer = true; L.startTime = 0;
    L.inPoint = spanIn; L.outPoint = spanOut;
    L.moveToBeginning();                                  // adj сверху -> цепляет весь текст ниже
    var fx = L.property("ADBE Effect Parade");
    var w = fx.addProperty("CC Image Wipe");
    try { w.property("CC Image Wipe-0002").setValue(CONFIG.blinkBorderSoftness); } catch (e) {} // Border Softness
    var cmp = w.property("CC Image Wipe-0001");           // Completion
    var beat = 60.0 / Math.max(1, CONFIG.bpm);            // длина доли
    var subdiv = Math.max(1, CONFIG.blinkSubdiv || 1);    // блинков на долю
    var period = beat / subdiv;                           // период блинка (1/8 при subdiv=2)
    // Медленный BPM → реже моргает → делаем каждый блинк ИНТЕНСИВНЕЕ (глубже
    // wipe). Пик масштабируется обратно BPM относительно 120, клампится в
    // [blinkPeak, 1.0]. Быстрый трек — базовый пик; медленный — почти полный.
    var refBpm = 120.0;
    var peak = Math.max(CONFIG.blinkPeak,
                        Math.min(1.0, CONFIG.blinkPeak * (refBpm / Math.max(1, CONFIG.bpm))));
    // фаза: первая граница блинка <= spanIn
    var k0 = Math.floor((spanIn - CONFIG.beatOffset) / period);
    var t  = CONFIG.beatOffset + k0 * period;
    cmp.setValueAtTime(Math.max(spanIn, t), 0);
    var guard = 0;
    while (t < spanOut && guard < 100000){
        var bStart = t, bMid = t + period * 0.5;
        if (bStart > spanIn && bStart < spanOut) cmp.setValueAtTime(bStart, 0);   // граница -> видно
        if (bMid   > spanIn && bMid   < spanOut) cmp.setValueAtTime(bMid, peak);  // центр -> блинк
        t += period; guard++;
    }
    cmp.setValueAtTime(spanOut, 0);
    for (var ki = 1; ki <= cmp.numKeys; ki++){
        try {
            cmp.setInterpolationTypeAtKey(ki, BZ, BZ);
            cmp.setTemporalEaseAtKey(ki, [new KeyframeEase(0, CONFIG.blinkInfluence)], [new KeyframeEase(0, CONFIG.blinkInfluence)]);
        } catch (eK) {}
    }
    return L;
}

// ============================ MAIN ============================
(function(){
    if (!app.project){ say("нет открытого проекта"); return; }
    var __bpm = injectedBpm(); if (__bpm) CONFIG.bpm = __bpm;
    var __fill = injectedFill(); if (__fill) CONFIG.fillColor = __fill;  // blast: custom subtitle color
    applyInjectedStyle();  // blast: настройки текста визарда (движок) — поверх прод-дефолтов
    if (CONFIG.strictFont){
        requireRealFont(CONFIG.font);
        if (CONFIG.focusFont) requireRealFont(CONFIG.focusFont);
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

    var srcComp = findComp(); if (!srcComp){ say("нет активной композиции — открой комп и запусти снова"); return; }
    if (isGeneratedBratCompName(srcComp.name)){
        say("active comp is a generated BRAT comp; open the main comp and run again");
        return;
    }
    var cleanup = cleanupPreviousBrat(srcComp);

    // Reveal keyframes must land on the comp's own frame grid. CONFIG.fps is a
    // fallback for standalone runs; inside the pipeline the comp is 23.976, and
    // quantising to a hardcoded 30 put every keyframe between frames. Across the
    // per-word precomps BRAT builds, that mixed-rate timing is what AE reports as
    // "internal structure inconsistency (seq) (25 :: 8)".
    if (srcComp.frameRate > 0) { CONFIG.fps = srcComp.frameRate; }

    var CW = srcComp.width, CH = srcComp.height;
    var BOX_W = Math.round(CW * CONFIG.boxWFactor);
    var BOX_H = Math.round(CH * CONFIG.boxHFactor);
    var fr = 1.0 / CONFIG.fps;

    var blocks = packBlocks(words, CONFIG.WORDS_PER_LINE, CONFIG.MAX_LINES);

    app.beginUndoGroup("Brat Subtitles");
    var made = 0, firstErr = "";
    var spanIn = 1e9, spanOut = -1e9;
    try {
        // целевой комп: отдельный «СУБТИТРЫ» (чтобы моргачка не цепляла футаж) или активный
        var tcomp = srcComp;
        if (CONFIG.separateComp){
            tcomp = app.project.items.addComp(CONFIG.textCompName, CW, CH, srcComp.pixelAspect, srcComp.duration, srcComp.frameRate);
        }
        if (CONFIG.contrastPlate && !CONFIG.separateComp){
            throw new Error("contrast plate requires separateComp=true");
        }
        var contrastComp = null;
        if (CONFIG.contrastPlate){
            contrastComp = app.project.items.addComp(
                CONFIG.contrastCompName, CW, CH, srcComp.pixelAspect, srcComp.duration, srcComp.frameRate
            );
        }

        // ГЛОБАЛЬНЫЙ fit: один fontSize на весь ролик, чтобы самая широкая строка
        // влезала в бокс (без авто-переноса → 2–3 слова держатся в строке) и
        // размер был одинаков во всех блоках (не скакал).
        var fitFontSize = computeFitFontSize(tcomp, blocks, BOX_W);

        // Measure the actual rendered width of every word once, then place
        // independent word precomps across the same BOX_W full-justify span.
        // Each word owns its adjustment layer, so it cannot blur its neighbours.
        var probe = tcomp.layers.addText("x");
        probe.name = "BRAT word measure probe";
        styleText(probe, ParagraphJustification.LEFT_JUSTIFY, fitFontSize);
        var probeSource = probe.property("ADBE Text Properties").property("ADBE Text Document");
        function measureWord(wordText, isFocus){
            var td = probeSource.value;
            td.text = wordText;
            td.font = wordFont(isFocus);
            td.fauxItalic = wordFaux(isFocus);
            probeSource.setValue(td);
            var mr = probe.sourceRectAtTime(0, false);
            return {
                width: Math.max(1, mr.width),
                height: Math.max(1, mr.height),
                top: mr.top,
                bottom: mr.top + mr.height
            };
        }

        // Одна высота прекомпа слова на ВЕСЬ ролик (чётная): раньше она считалась
        // по выносным каждого слова и округлялась вверх — центр прекомпа (он же
        // базовая линия) у разных слов попадал то в целый пиксель, то в полпикселя,
        // и после блюров/масштаба 80% слова в строке «ехали» по высоте.
        var glyphHalfH = 1;
        for (var gbi = 0; gbi < blocks.length; gbi++){
            for (var gw = 0; gw < blocks[gbi].words.length; gw++){
                var gm = measureWord(wWord(blocks[gbi].words[gw]).toLowerCase(), wFocus(blocks[gbi].words[gw]));
                glyphHalfH = Math.max(glyphHalfH, -gm.top, gm.bottom);
            }
        }
        var wordPad = CONFIG.transitionBlurLength + 12;
        var wordCompH = Math.max(4, 2 * Math.ceil(glyphHalfH + wordPad));
        var rowX = CW * CONFIG.centerXFrac;
        // Строб Ч/Б (Difference, авто-инверсия) — тень там не нужна
        var __blS = ($.global && $.global.__BLAST_SUBS_BLEND) ? String($.global.__BLAST_SUBS_BLEND).toLowerCase() : "";
        var wordShadowOn = CONFIG.wordShadow && __blS !== "difference";
        if (CONFIG.wordShadow && !wordShadowOn) log("word shadow skipped: subtitles blend is difference (auto-invert)");

        var wordBlurCount = 0, plateCount = 0;
        for (var b = 0; b < blocks.length; b++){
            var block = blocks[b]; if (!block.words.length) continue;
            var slice = block.words;
            var t0 = wStart(slice[0]); var t1 = wEnd(slice[slice.length - 1]);
            if (isNaN(t0)) t0 = 0;
            if (isNaN(t1) || t1 <= t0) t1 = t0 + fr;
            t1 += CONFIG.tailFrames * fr;

            var blockOut = Math.min(tcomp.duration, t1);
            if (block.lines.length === CONFIG.MAX_LINES && b + 1 < blocks.length &&
                !!block.voice === !!blocks[b + 1].voice){
                var nextStart = wStart(blocks[b + 1].words[0]);
                if (!isNaN(nextStart) && nextStart > t0) blockOut = Math.min(tcomp.duration, nextStart);
            }

            var lineStep = fitFontSize * (CONFIG.leading / CONFIG.fontSize) * (CONFIG.scale[1] / 100.0);
            for (var row = 0; row < block.lines.length; row++){
                var line = block.lines[row]; if (!line.length) continue;
                var metrics = [], naturalWidth = 0;
                for (var mi = 0; mi < line.length; mi++){
                    var metric = measureWord(wWord(line[mi]).toLowerCase(), wFocus(line[mi]));
                    metrics.push(metric);
                    naturalWidth += metric.width;
                }
                var gap = line.length > 1 ? (BOX_W - naturalWidth) / (line.length - 1) : 0;
                if (gap < 0) throw new Error("word layout exceeds BOX_W in block " + (b + 1) + ", row " + (row + 1));

                var cursorX = -BOX_W / 2.0;
                var rowY = CH * CONFIG.centerYFrac + CONFIG.yNudge +
                    (row - (block.lines.length - 1) / 2.0) * lineStep;
                var plateIn = wStart(line[0]); if (isNaN(plateIn)) plateIn = t0;
                var plateOut = Math.max(plateIn + fr, blockOut);
                if (contrastComp){
                    addLineContrastPlate(contrastComp, plateIn, plateOut, rowX, rowY, lineStep, BOX_W,
                                         (b + 1) + "." + (row + 1));
                    plateCount++;
                }
                for (var wi = 0; wi < line.length; wi++){
                    var word = line[wi];
                    var wordText = wWord(word).toLowerCase();
                    var wordIn = wStart(word); if (isNaN(wordIn)) wordIn = t0;
                    var visibleDuration = Math.max(fr, blockOut - wordIn);
                    var blurDuration = CONFIG.transitionBlurFrames * srcComp.frameDuration;
                    var wordCompDuration = Math.max(visibleDuration, blurDuration);
                    var wordCompW = Math.max(4, 2 * Math.ceil(metrics[wi].width / 2.0 + wordPad));
                    // высота — общая wordCompH (см. выше): базовая линия каждого
                    // слова ровно в центре прекомпа, центр у всех одинаковый.
                    var wc = app.project.items.addComp(
                        CONFIG.textCompName + " / BRAT WORD " + (b + 1) + "." + (row + 1) + "." + (wi + 1),
                        wordCompW, wordCompH, srcComp.pixelAspect,
                        wordCompDuration, srcComp.frameRate
                    );

                    try {
                        var L = wc.layers.addText(wordText);
                        L.name = "brat word " + (b + 1) + "." + (row + 1) + "." + (wi + 1);
                        L.motionBlur = false;
                        L.inPoint = 0;
                        L.outPoint = wc.duration;
                        var isFocus = wFocus(word);
                        styleText(L, ParagraphJustification.LEFT_JUSTIFY, fitFontSize,
                                  (isFocus && CONFIG.focusFillColor) ? CONFIG.focusFillColor : null,
                                  wordFont(isFocus), wordFaux(isFocus));
                        var wr = L.sourceRectAtTime(0, false);
                        var wtg = L.property("ADBE Transform Group");
                        // Point-text layer space has y=0 on the baseline.  Keep
                        // that origin at the vertical centre of every word
                        // precomp.  Centering by wr.top + wr.height/2 made words
                        // with descenders float relative to neighbours.
                        wtg.property("ADBE Anchor Point").setValue([wr.left + wr.width / 2, 0, 0]);
                        wtg.property("ADBE Position").setValue([wordCompW / 2, wordCompH / 2, 0]);

                        var fx = L.property("ADBE Effect Parade");
                        var mm = fx.addProperty("ADBE Minimax");
                        try { mm.property("ADBE Minimax-0001").setValue(2); } catch (eO) {}
                        var rad = mm.property("ADBE Minimax-0002");
                        rad.setValueAtTime(0, CONFIG.minimaxRadius);
                        rad.setValueAtTime(fr, 0);
                        try { mm.property("ADBE Minimax-0003").setValue(CONFIG.minimaxChannel); } catch (eC) {}
                        var gb = fx.addProperty("ADBE Gaussian Blur 2");
                        gb.property("ADBE Gaussian Blur 2-0001").setValue(CONFIG.blurRadius);
                        if (wordShadowOn) addWordShadow(fx);

                        addWordDirectionalBlur(wc, wordCompDuration, wc.frameDuration,
                                               (b + 1) + "." + (row + 1) + "." + (wi + 1));

                        var nestedWord = tcomp.layers.add(wc);
                        nestedWord.name = "BRAT WORD " + (b + 1) + "." + (row + 1) + "." + (wi + 1);
                        nestedWord.startTime = wordIn;
                        nestedWord.inPoint = wordIn;
                        nestedWord.outPoint = Math.min(tcomp.duration, blockOut);
                        var centerX = cursorX + metrics[wi].width / 2.0;
                        var ntg = nestedWord.property("ADBE Transform Group");
                        ntg.property("ADBE Position").setValue([
                            rowX + centerX * (CONFIG.scale[0] / 100.0), rowY, 0
                        ]);
                        ntg.property("ADBE Scale").setValue(CONFIG.scale);

                        wordBlurCount++;
                        made++;
                        if (wordIn < spanIn) spanIn = wordIn;
                        if (blockOut > spanOut) spanOut = blockOut;
                    } catch (eWord){
                        if (!firstErr) firstErr = String(eWord) + " (line " + (eWord.line || "?") + ")";
                    }
                    cursorX += metrics[wi].width + gap;
                }
            }
        }
        try { probe.remove(); } catch (eProbe) {}

        var blinked = false;
        if (CONFIG.blinker && made && spanOut > spanIn){
            try { addBlinker(tcomp, spanIn, spanOut); blinked = true; }
            catch (eB){ if (!firstErr) firstErr = "blinker: " + eB; }
        }

        // Normalize after all subtitle/effect construction. AE can retain an
        // interactive value on early shape effects while a project is open;
        // the generated result must always end with the explicit config values.
        if (contrastComp){
            for (var cpi = 1; cpi <= contrastComp.numLayers; cpi++){
                var cpLayer = contrastComp.layer(cpi);
                if (cpLayer.name.indexOf("BRAT contrast line ") !== 0) continue;
                cpLayer.property("ADBE Transform Group").property("ADBE Opacity").setValue(CONFIG.contrastOpacity);
                var cpRadial = cpLayer.property("ADBE Effect Parade").property("ADBE Radial Blur");
                if (!cpRadial) throw new Error("contrast plate normalization: Radial Blur is missing");
                cpRadial.property("ADBE Radial Blur-0001").setValue(CONFIG.contrastRadialAmount);
                cpRadial.property("ADBE Radial Blur-0002").setValue([contrastComp.width / 2, contrastComp.height / 2]);
                cpRadial.property("ADBE Radial Blur-0003").setValue(CONFIG.contrastRadialType);
                cpRadial.property("ADBE Radial Blur-0004").setValue(CONFIG.contrastRadialAA);
                // Do not touch ADBE Radial Blur-0005: it is NO_VALUE in AE 2025.
            }
        }

        // вложить комп субтитров в активный (поверх футажа)
        if (CONFIG.separateComp && CONFIG.nestIntoActive && tcomp !== srcComp){
            try {
                if (contrastComp){
                    var contrastLayer = srcComp.layers.add(contrastComp);
                    contrastLayer.name = CONFIG.contrastCompName;
                    contrastLayer.startTime = 0;
                    contrastLayer.inPoint = 0;
                    contrastLayer.outPoint = srcComp.duration;
                    contrastLayer.moveToBeginning();
                }
                var nl = srcComp.layers.add(tcomp); nl.moveToBeginning();
                // Strobe Ч/Б: Difference на вложенном компе → белый текст авто-
                // инвертируется под мигающим Ч/Б фоном (читаем на любом сегменте).
                try {
                    var __bl = ($.global && $.global.__BLAST_SUBS_BLEND) ? String($.global.__BLAST_SUBS_BLEND).toLowerCase() : "";
                    if (__bl === "difference") nl.blendingMode = BlendingMode.DIFFERENCE;
                } catch (eBl){}
            } catch (eN){ if (!firstErr) firstErr = "nest: " + eN; }
        }
    } catch (err){ firstErr = String(err); }
    finally { app.endUndoGroup(); }

    var msg = "готово: слоёв " + made + " / блоков " + blocks.length + " (слов " + words.length + ")" +
              "\nкомп: " + (CONFIG.separateComp ? ("«" + CONFIG.textCompName + "» (отдельный)") : srcComp.name) +
              " " + CW + "x" + CH + "  box " + BOX_W + "x" + BOX_H +
              "\ncleanup: old layers " + cleanup.layers + ", old comps " + cleanup.comps + ", empty Text " + cleanup.emptyText +
              "\ndirectional blur: " + (CONFIG.transitionBlur ? (wordBlurCount + " words, " + CONFIG.transitionBlurFrames + " frames each") : "off") +
              "\nморгачка: " + (CONFIG.blinker ? ("да, BPM=" + CONFIG.bpm + ", span " + spanIn.toFixed(2) + "–" + spanOut.toFixed(2) + "с") : "нет");
    if (firstErr) msg += "\n⚠ первая ошибка: " + firstErr;
    say(msg);
    // итог прогона — для лабов/диагностики (alert на ноде нельзя)
    try { $.global.__BLAST_SUBS_RESULT = msg; } catch (eR) {}
})();
