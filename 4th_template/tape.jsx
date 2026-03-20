// subtitle_from_json_16.jsx
//
// v16: красный цвет focus-слов через индексный Range Selector (Units=Index)
//      + allCaps = true для всего текста

// ============================================================
// НАСТРОЙКИ
// ============================================================
var FONT_NAME   = "Montserrat-BoldItalic";
var FONT_SIZE   = 60;
var FILL_COLOR  = [1, 1, 1];
var FOCUS_COLOR = [1.0, 0.451, 0.451]; // #FF7373 // #E51515
var TRACKING    = -25;
var LEADING     = 80;
var FPS         = 23.976;

// Сдвиг всех субтитров в секундах (+0.3 если опережают, -0.2 если отстают)
var TIME_OFFSET = 0.0;

var COMP_NAME = "Субтитры";
var COMP_W    = 1080;
var COMP_H    = 1920;

var BOX_W = 900;
var BOX_H = 160; // fontSize(60) + leading(80) + 20px padding = ровно 2 строки

// ============================================================
// Утилиты
// ============================================================
function parseJSON(str) {
    try { return eval("(" + str + ")"); } catch(e) { return null; }
}

function readFile(path) {
    var f = new File(path);
    if (!f.exists) return null;
    f.encoding = "UTF-8";
    f.open("r");
    var c = f.read();
    f.close();
    return c;
}

function buildWordList(wt) {
    var list = [];
    if (!wt) return list;
    for (var i = 0; i < wt.length; i++) {
        if (wt[i] && wt[i].word) {
            list.push({
                word:  wt[i].word,
                start: wt[i].start || 0,
                end:   wt[i].end   || 0,
                focus: wt[i].focus === true
            });
        }
    }
    return list;
}

function getSubWords(text, wordList, inT, outT) {
    var textWords = text.split(" ");
    var result    = [];
    var listIdx   = 0;
    for (var i = 0; i < textWords.length; i++) {
        var tw = textWords[i].replace(/[^\w\u0400-\u04FF]/gi, "").toLowerCase();
        for (var j = listIdx; j < wordList.length; j++) {
            var wl = wordList[j];
            if (wl.start >= inT - 0.15 && wl.end <= outT + 0.15) {
                var wlClean = wl.word.replace(/[^\w\u0400-\u04FF]/gi, "").toLowerCase();
                if (wlClean === tw) {
                    result.push({ textIdx: i, wt: wl });
                    listIdx = j + 1;
                    break;
                }
            }
        }
    }
    return result;
}

// Возвращает символьный индекс начала слова wordIdx в строке text
function getCharStart(text, wordIdx) {
    var words = text.split(" ");
    var pos   = 0;
    for (var i = 0; i < wordIdx; i++) {
        pos += words[i].length + 1; // +1 пробел
    }
    return pos;
}

// ============================================================
// Создание одного субтитра
// ============================================================
function createSubtitle(comp, sub, wordList) {
    var inT  = sub["in"]  + TIME_OFFSET;
    var outT = sub["out"] + TIME_OFFSET;
    var dur  = outT - inT;
    if (dur <= 0) return;

    var FADE_FRAMES = 4;
    var fadeDur     = FADE_FRAMES / FPS;

    var subWords = getSubWords(sub.text, wordList, inT, outT);
    var nWords   = sub.text.split(" ").length;

    var fadeStart = outT - fadeDur;
    if (fadeStart <= inT) fadeStart = inT + dur * 0.8;

    // ---- Слой ----
    var displayText = sub.text.toUpperCase();
    var tl = comp.layers.addBoxText([BOX_W, BOX_H], displayText);
    tl.name       = displayText;
    tl.inPoint    = inT;
    tl.outPoint   = outT;
    tl.motionBlur = false;

    // ---- Текст ----
    var tdProp = tl.property("ADBE Text Properties").property("ADBE Text Document");
    var td = tdProp.value;
    td.resetCharStyle();
    td.font          = FONT_NAME;
    td.fontSize      = FONT_SIZE;
    td.fillColor     = FILL_COLOR;
    td.applyFill     = true;
    td.applyStroke   = false;
    td.tracking      = TRACKING;
    td.leading       = LEADING;
    td.justification = ParagraphJustification.CENTER_JUSTIFY;
    tdProp.setValue(td);

    // ---- Transform ----
    var transform = tl.property("ADBE Transform Group");
    transform.property("ADBE Position").setValue([540, 960, 0]);

    // ---- Opacity fade ----
    var opacity = transform.property("ADBE Opacity");
    opacity.setValueAtTime(fadeStart, 100);
    var o1 = opacity.nearestKeyIndex(fadeStart);
    opacity.setTemporalEaseAtKey(o1,
        [new KeyframeEase(0,    16.666666667)],
        [new KeyframeEase(-600, 16.666666667)]
    );
    opacity.setValueAtTime(outT, 0);
    var o2 = opacity.nearestKeyIndex(outT);
    opacity.setTemporalEaseAtKey(o2,
        [new KeyframeEase(-600, 16.666666667)],
        [new KeyframeEase(0,    16.666666667)]
    );

    var animators = tl.property("ADBE Text Properties").property("ADBE Text Animators");

    // ---- Animator 1: раскрытие по словам ----
    var anim1 = animators.addProperty("ADBE Text Animator");
    anim1.name = "Animator 1";

    var ap1 = anim1.property("ADBE Text Animator Properties");
    ap1.addProperty("ADBE Text Opacity").setValue(0);

    var ranSel1 = anim1.property("ADBE Text Selectors").addProperty("ADBE Text Selector");
    ranSel1.name = "Range Selector 1";

    var adv1 = ranSel1.property("ADBE Text Range Advanced");
    adv1.property("ADBE Text Range Units").setValue(1);
    adv1.property("ADBE Text Range Type2").setValue(3);  // Words
    adv1.property("ADBE Text Selector Mode").setValue(1);
    adv1.property("ADBE Text Selector Max Amount").setValue(100);
    adv1.property("ADBE Text Range Shape").setValue(1);
    adv1.property("ADBE Text Selector Smoothness").setValue(100);
    adv1.property("ADBE Text Levels Max Ease").setValue(0);
    adv1.property("ADBE Text Levels Min Ease").setValue(0);
    adv1.property("ADBE Text Randomize Order").setValue(0);

    ranSel1.property("ADBE Text Percent End").setValue(100);

    var ps = ranSel1.property("ADBE Text Percent Start");

    if (subWords.length >= 2) {
        for (var k = 0; k < subWords.length; k++) {
            var pct  = (k / nWords) * 100;
            var time = subWords[k].wt.start;
            if (time < inT) time = inT;
            ps.setValueAtTime(time, pct);
        }
        var lastWt   = subWords[subWords.length - 1].wt;
        var lastTime = (lastWt.end < outT - fadeDur) ? lastWt.end : outT - fadeDur;
        if (lastTime <= inT) lastTime = inT + dur * 0.7;
        ps.setValueAtTime(lastTime, 100);
    } else {
        var revealEnd   = outT - fadeDur;
        if (revealEnd <= inT) revealEnd = inT + dur * 0.7;
        var revealSpeed = 100.0 / (revealEnd - inT);
        ps.setValueAtTime(inT, 0);
        var k1 = ps.nearestKeyIndex(inT);
        ps.setTemporalEaseAtKey(k1,
            [new KeyframeEase(0,           16.666666667)],
            [new KeyframeEase(revealSpeed, 16.666666667)]
        );
        ps.setValueAtTime(revealEnd, 100);
        var k2 = ps.nearestKeyIndex(revealEnd);
        ps.setTemporalEaseAtKey(k2,
            [new KeyframeEase(revealSpeed, 16.666666667)],
            [new KeyframeEase(0,           16.666666667)]
        );
    }

    // ---- Animator 2+: красный цвет + двойное раскрытие focus-слов ----
    var textWords = displayText.split(" ");

    for (var fi = 0; fi < subWords.length; fi++) {
        if (!subWords[fi].wt.focus) continue;

        var wordIdx  = subWords[fi].textIdx;
        var charFrom = getCharStart(displayText, wordIdx);
        var charTo   = charFrom + textWords[wordIdx].length;
        var wordLen  = textWords[wordIdx].length;
        var charMid  = charFrom + Math.ceil(wordLen / 2); // середина слова

        var wStart   = subWords[fi].wt.start + TIME_OFFSET;
        var wEnd     = subWords[fi].wt.end   + TIME_OFFSET;
        var wMid     = wStart + (wEnd - wStart) / 2;

        // Animator A: красный цвет на всё слово
        var animF = animators.addProperty("ADBE Text Animator");
        animF.name = "Focus color: " + textWords[wordIdx];

        var apF       = animF.property("ADBE Text Animator Properties");
        var colorProp = apF.addProperty("ADBE Text Fill Color");
        colorProp.setValue(FOCUS_COLOR);

        var ranSelF = animF.property("ADBE Text Selectors").addProperty("ADBE Text Selector");
        ranSelF.name = "Range";
        var advF = ranSelF.property("ADBE Text Range Advanced");
        advF.property("ADBE Text Range Units").setValue(2);      // Index
        advF.property("ADBE Text Selector Mode").setValue(1);
        advF.property("ADBE Text Selector Max Amount").setValue(100);
        advF.property("ADBE Text Selector Smoothness").setValue(100);
        ranSelF.property("ADBE Text Index Start").setValue(charFrom);
        ranSelF.property("ADBE Text Index End").setValue(charTo);

        // Animator B: скрываем вторую половину слова до wMid
        // Max Amount: 100 @ wStart → 0 @ wMid (вторая половина появляется)
        var animH = animators.addProperty("ADBE Text Animator");
        animH.name = "Focus split: " + textWords[wordIdx];

        var apH = animH.property("ADBE Text Animator Properties");
        apH.addProperty("ADBE Text Opacity").setValue(0);

        var ranSelH = animH.property("ADBE Text Selectors").addProperty("ADBE Text Selector");
        ranSelH.name = "Range";
        var advH = ranSelH.property("ADBE Text Range Advanced");
        advH.property("ADBE Text Range Units").setValue(2);      // Index
        advH.property("ADBE Text Selector Mode").setValue(1);
        advH.property("ADBE Text Selector Smoothness").setValue(100);
        ranSelH.property("ADBE Text Index Start").setValue(charMid);
        ranSelH.property("ADBE Text Index End").setValue(charTo);

        // Max Amount: 100 (скрыто) → 0 (открыто) за полдлительности слова
        var maxAmt = ranSelH.property("ADBE Text Range Advanced")
                            .property("ADBE Text Selector Max Amount");
        maxAmt.setValueAtTime(wStart, 100);
        maxAmt.setValueAtTime(wMid,   0);
    }

    // ---- Эффекты ----
    var fx = tl.property("ADBE Effect Parade");

    var glo1 = fx.addProperty("ADBE Glo2");
    glo1.name = "Свечение";
    glo1.property("ADBE Glo2-0001").setValue(2);
    glo1.property("ADBE Glo2-0002").setValue(153);
    glo1.property("ADBE Glo2-0003").setValue(35);
    glo1.property("ADBE Glo2-0004").setValue(0.75);
    glo1.property("ADBE Glo2-0005").setValue(2);
    glo1.property("ADBE Glo2-0006").setValue(3);
    glo1.property("ADBE Glo2-0007").setValue(1);
    glo1.property("ADBE Glo2-0008").setValue(3);
    glo1.property("ADBE Glo2-0009").setValue(1);
    glo1.property("ADBE Glo2-0010").setValue(0);
    glo1.property("ADBE Glo2-0011").setValue(0.5);
    try { glo1.property("ADBE Glo2-0012").setValue([1, 1, 1, 0]); } catch(e) {}
    try { glo1.property("ADBE Glo2-0013").setValue([0, 0, 0, 0]); } catch(e) {}
    glo1.property("ADBE Glo2-0014").setValue(1);

    var ds1 = fx.addProperty("ADBE Drop Shadow");
    ds1.name = "Тень";
    ds1.property("ADBE Drop Shadow-0001").setValue([0, 0, 0, 1]);
    ds1.property("ADBE Drop Shadow-0002").setValue(255);
    ds1.property("ADBE Drop Shadow-0003").setValue(135);
    ds1.property("ADBE Drop Shadow-0004").setValue(3);
    ds1.property("ADBE Drop Shadow-0005").setValue(5);
    ds1.property("ADBE Drop Shadow-0006").setValue(0);

    var ds2 = fx.addProperty("ADBE Drop Shadow");
    ds2.name = "Drop Shadow 2";
    ds2.property("ADBE Drop Shadow-0001").setValue([0, 0, 0, 1]);
    ds2.property("ADBE Drop Shadow-0002").setValue(255);
    ds2.property("ADBE Drop Shadow-0003").setValue(135);
    ds2.property("ADBE Drop Shadow-0004").setValue(3);
    ds2.property("ADBE Drop Shadow-0005").setValue(15);
    ds2.property("ADBE Drop Shadow-0006").setValue(0);

    var glo2 = fx.addProperty("ADBE Glo2");
    glo2.name = "Glow 2";
    glo2.property("ADBE Glo2-0003").setValue(25);
    glo2.property("ADBE Glo2-0004").setValue(0.52);
    glo2.enabled = false;
}

// ============================================================
// MAIN
// ============================================================
(function () {
    var jsonFile = File.openDialog("Выбери JSON файл с субтитрами", "JSON:*.json");
    if (!jsonFile) return;

    var content = readFile(jsonFile.fsName);
    if (!content) { alert("Не удалось прочитать файл!"); return; }

    var data = parseJSON(content);
    if (!data) { alert("Ошибка парсинга JSON!"); return; }

    var subs, wordTimings;
    if (data instanceof Array) {
        subs        = data;
        wordTimings = null;
    } else if (data.subtitles) {
        subs        = data.subtitles;
        wordTimings = data.word_timings || null;
    } else {
        alert("Не найден массив субтитров."); return;
    }
    if (!subs || !subs.length) { alert("Массив субтитров пуст."); return; }

    var wordList = buildWordList(wordTimings);

    var lastOut = 0;
    for (var i = 0; i < subs.length; i++) {
        if (subs[i]["out"] > lastOut) lastOut = subs[i]["out"];
    }

    var comp = null;
    for (var i = 1; i <= app.project.numItems; i++) {
        var item = app.project.items[i];
        if (item instanceof CompItem && item.name === COMP_NAME) {
            comp = item; break;
        }
    }
    if (!comp) {
        comp = app.project.items.addComp(COMP_NAME, COMP_W, COMP_H, 1, lastOut + 0.5, FPS);
    } else {
        if (comp.duration < lastOut + 0.5) comp.duration = lastOut + 0.5;
    }
    comp.openInViewer();

    app.beginUndoGroup("Subtitle from JSON v16");

    var created = 0;
    var skipped = 0;
    for (var s = 0; s < subs.length; s++) {
        var sub = subs[s];
        if (!sub || sub["out"] <= sub["in"]) { skipped++; continue; }
        createSubtitle(comp, sub, wordList);
        created++;
    }

    app.endUndoGroup();
    var msg = "Готово! Создано " + created + " субтитров.";
    if (skipped > 0) msg += "\nПропущено: " + skipped;
    alert(msg);
})();
