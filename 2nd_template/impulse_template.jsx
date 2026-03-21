// blast_subtitles.jsx — универсальный скрипт субтитров для Blast
//
// Использование:
//   1. Подготовь JSON файл с субтитрами (см. формат ниже)
//   2. Запусти скрипт: Файл → Сценарии → Выполнить файл сценария
//   3. Выбери JSON файл в диалоге
//
// Формат JSON:
//   [
//     { "text": "большой чек", "in": 0.000, "out": 1.043 },
//     { "text": "босс",        "in": 1.293, "out": 1.960 },
//     ...
//   ]
//
// Логика автоматическая:
//   - dur < 1с  → КОРОТКИЙ: bounce scale (75→peak→0), expression spring animator
//   - dur >= 1с → ДЛИННЫЙ:  статичное появление, схлопывание в конце
//   - exit_t вычисляется автоматически: если внутри длинного слоя
//     начинается короткий — длинный начинает уход в этот момент

// ============================================================
// НАСТРОЙКИ ШРИФТА — меняй здесь
// ============================================================
var FONT_NAME    = "Point-Light";
var FONT_SIZE    = 100;
var FILL_COLOR   = [1, 1, 1];
var STROKE_COLOR = [1, 1, 1];
var STROKE_WIDTH = 3;
var TRACKING     = -25;
var LEADING      = 250;
var FPS          = 23.976;
var REVEAL_FACTOR = 0.25;  // компенсация раскрытия (0=выкл, 1=полная, 0.3=рекомендуется)

// ============================================================
// Expression Selector — пружинный отскок с задержкой между буквами
// ============================================================
// Expression генерируется динамически с нужным delay (см. buildExpr)
// delay масштабируется под доступное время строки: min(0.05, available / chars)
function buildExpr(delay) {
    var d = delay.toFixed(4);
    return "delay = " + d + ";\n" +
           "myDelay = delay*textIndex;\n" +
           "t = (time - inPoint) - myDelay;\n" +
           "if (t >= 0){\n" +
           "  freq = 2; amplitude = 100; decay = 8.0;\n" +
           "  s = amplitude*Math.cos(freq*t*2*Math.PI)/Math.exp(decay*t);\n" +
           "  [s,s]\n" +
           "} else { value }";
}

// ============================================================
// Утилиты
// ============================================================
function f2t(inT, frame) { return inT + frame / FPS; }

function parseJSON(str) {
    // Простой парсер — eval безопасен для локального файла
    try { return eval("(" + str + ")"); } catch(e) { return null; }
}

function readFile(path) {
    var f = new File(path);
    if (!f.exists) return null;
    f.encoding = "UTF-8";
    f.open("r");
    var content = f.read();
    f.close();
    return content;
}

// Вычисление exit_t для long слоёв:
// exit_t = max(первый short внутри, inT + revealTime + 0.15с)
// revealTime = 0.05с × кол-во символов (из Expression Selector)
function calcExits(subs) {
    var results = [];
    for (var i = 0; i < subs.length; i++) {
        var s   = subs[i];
        var inT = s.in;
        var outT = s.out;

        var exitT = null;
        if (s.type === "long") {
            // Ищем первый short, который начинается внутри этого слоя
            var childIn = null;
            for (var j = 0; j < subs.length; j++) {
                if (i === j) continue;
                var s2 = subs[j];
                if (s2.type === "short" && s2.in > inT && s2.in < outT) {
                    if (childIn === null || s2.in < childIn) childIn = s2.in;
                }
            }

            if (childIn !== null) {
                // Ждём пока все буквы раскроются через Expression (0.05с × символов)
                var revealTime = 0.05 * (s.text.length - 1);
                var minExitT   = inT + revealTime + 0.15;
                exitT = Math.max(childIn, minExitT);
                exitT = Math.min(exitT, outT - 7 / FPS); // не позже чем за 7 кадров до конца
            }
        }
        results.push(exitT);
    }
    return results;
}


// ============================================================
// Создание одного субтитра
// ============================================================
function createSubtitle(comp, sub, isLong, exitT, prevOutPoint) {
    var trackIn  = sub.in;   // оригинальный тайминг трека
    var outT     = sub.out;
    var isShort  = !isLong;

    // Динамический delay: масштабируем под доступное время
    var chars     = sub.text.length;
    var exprDelay;
    if (isLong) {
        var roughDur  = outT - trackIn;
        var exitTime  = 7 / FPS;
        var minHold   = 0.10;
        var available = roughDur - exitTime - minHold;
        exprDelay = (chars > 1) ? Math.min(0.05, available / (chars - 1)) : 0.05;
        exprDelay = Math.max(0.005, exprDelay);
    } else {
        exprDelay = 0.05;
    }

    // Компенсация времени раскрытия: стартуем анимацию РАНЬШЕ track_in
    // чтобы текст был полностью виден в момент звучания слова
    var revealTime = isLong ? exprDelay * (chars - 1) : 0;
    var inT  = trackIn - revealTime * REVEAL_FACTOR;  // реальный inPoint слоя
    inT      = Math.max(0, inT);           // не уходим в минус
    if (prevOutPoint !== undefined) {
        inT = Math.max(inT, prevOutPoint);  // не залезаем на предыдущий слой
    }
    var dur  = outT - inT;
    var totalF = Math.round(dur * FPS);

    // Peak scale: комбинируем длительность И длину текста
    // avgCharW ~62px при шрифте 100px, безопасная ширина = 85% от ширины компа
    var peakByDur  = Math.round(190 + (1.0 - dur) * 180);
    var avgCharW   = 62;
    var safeWidth  = comp.width * 0.85;
    var textWidth  = sub.text.length * avgCharW;
    var peakByChar = Math.round((safeWidth / textWidth) * 100);
    var peakVal    = Math.min(peakByDur, peakByChar);
    peakVal        = Math.max(peakVal, 150); // минимум 150%

    // ---- Текстовый слой ----
    // Очистка текста: строчные буквы + убираем пунктуацию
    // Очистка текста: строчные буквы + убираем пунктуацию
    // Оставляем apostrophe и дефис ВНУТРИ слов (i'm, как-то)
    var rawText = sub.text.toLowerCase();
    var cleanText = "";
    for (var ci = 0; ci < rawText.length; ci++) {
        var ch = rawText[ci];
        if (ch === "'" || ch === "-") {
            // оставляем только если между буквами
            var prev = ci > 0 ? rawText[ci-1] : " ";
            var next = ci < rawText.length-1 ? rawText[ci+1] : " ";
            var prevLetter = /[a-zа-яёa-z]/i.test(prev);
            var nextLetter = /[a-zа-яёa-z]/i.test(next);
            if (prevLetter && nextLetter) cleanText += ch;
        } else if (/[.,!?;:"«»()\\/—–]/.test(ch)) {
            // убираем
        } else {
            cleanText += ch;
        }
    }
    cleanText = cleanText.replace(/\s+/g, " ").replace(/^\s+|\s+$/g, "");

    var tl = comp.layers.addText(cleanText);
    tl.name       = cleanText;
    tl.inPoint    = inT;
    tl.outPoint   = outT;
    tl.motionBlur = true;

    // Текст
    var tdProp = tl.property("ADBE Text Properties").property("ADBE Text Document");
    var td = tdProp.value;
    td.resetCharStyle();
    // текст уже задан через addText(cleanText)
    td.font          = FONT_NAME;
    td.fontSize      = FONT_SIZE;
    td.fillColor     = FILL_COLOR;
    td.applyFill     = true;
    td.strokeColor   = STROKE_COLOR;
    td.applyStroke   = true;
    td.strokeWidth   = STROKE_WIDTH;
    td.tracking      = TRACKING;
    td.leading       = LEADING;
    td.justification = ParagraphJustification.CENTER_JUSTIFY;
    tdProp.setValue(td);

    var transform = tl.property("ADBE Transform Group");
    transform.property("ADBE Position").setValue([540, 960, 0]);
    transform.property("ADBE Anchor Point").setValue([0.564, -23.213, 0]);

    // ---- Text Animator ----
    var animators = tl.property("ADBE Text Properties").property("ADBE Text Animators");
    var anim = animators.addProperty("ADBE Text Animator");
    anim.name = "Animator 1";

    var ap = anim.property("ADBE Text Animator Properties");
    ap.addProperty("ADBE Text Position 3D").setValue([0, 25, 0]);
    ap.addProperty("ADBE Text Scale 3D").setValue([50, 50, 100]);
    ap.addProperty("ADBE Text Rotation").setValue(15);
    ap.addProperty("ADBE Text Opacity").setValue(0);
    ap.addProperty("ADBE Text Blur").setValue([15, 15]);

    var sels = anim.property("ADBE Text Selectors");
    var ranSel = sels.addProperty("ADBE Text Selector");
    ranSel.property("ADBE Text Percent Start").setValue(0);
    ranSel.property("ADBE Text Percent End").setValue(100);
    var adv = ranSel.property("ADBE Text Range Advanced");
    adv.property("ADBE Text Range Units").setValue(1);
    adv.property("ADBE Text Range Type2").setValue(1);
    adv.property("ADBE Text Selector Mode").setValue(1);
    adv.property("ADBE Text Selector Max Amount").setValue(100);
    adv.property("ADBE Text Range Shape").setValue(1);
    adv.property("ADBE Text Selector Smoothness").setValue(100);
    adv.property("ADBE Text Levels Max Ease").setValue(0);
    adv.property("ADBE Text Levels Min Ease").setValue(0);

    var exprSel = sels.addProperty("ADBE Text Expressible Selector");
    exprSel.property("ADBE Text Range Type2").setValue(1);
    exprSel.property("ADBE Text Expressible Amount").expression = buildExpr(exprDelay);

    // ---- Drop Shadow (3 слоя) ----
    var fx = tl.property("ADBE Effect Parade");
    var ds1 = fx.addProperty("ADBE Drop Shadow");
    ds1.property("ADBE Drop Shadow-0001").setValue([0,0,0,1]);
    ds1.property("ADBE Drop Shadow-0002").setValue(255);
    ds1.property("ADBE Drop Shadow-0003").setValue(180);
    ds1.property("ADBE Drop Shadow-0004").setValue(3);
    ds1.property("ADBE Drop Shadow-0005").setValue(0);
    var ds2 = fx.addProperty("ADBE Drop Shadow");
    ds2.property("ADBE Drop Shadow-0001").setValue([0,0,0,1]);
    ds2.property("ADBE Drop Shadow-0002").setValue(255);
    ds2.property("ADBE Drop Shadow-0003").setValue(180);
    ds2.property("ADBE Drop Shadow-0004").setValue(0);
    ds2.property("ADBE Drop Shadow-0005").setValue(25);
    var ds3 = fx.addProperty("ADBE Drop Shadow");
    ds3.property("ADBE Drop Shadow-0001").setValue([0,0,0,1]);
    ds3.property("ADBE Drop Shadow-0002").setValue(127.5);
    ds3.property("ADBE Drop Shadow-0003").setValue(180);
    ds3.property("ADBE Drop Shadow-0004").setValue(0);
    ds3.property("ADBE Drop Shadow-0005").setValue(50);

    // ---- Transform: Scale + Opacity ----
    var scale   = transform.property("ADBE Scale");
    var opacity = transform.property("ADBE Opacity");

    if (isLong) {
        // ДЛИННЫЙ: уход начинается в exitT (если есть наслоение) или за 7 кадров до конца
        var scaleExitT, opacityExitT;
        if (exitT !== null) {
            scaleExitT   = exitT;
            opacityExitT = exitT + 3 / FPS;
        } else {
            scaleExitT   = f2t(inT, totalF - 7);
            opacityExitT = f2t(inT, totalF - 4);
        }
        scale.setValueAtTime(scaleExitT,       [75, 75, 100]);
        scale.setValueAtTime(f2t(inT, totalF), [0,  0,  100]);
        opacity.setValueAtTime(opacityExitT,       100);
        opacity.setValueAtTime(f2t(inT, totalF),     0);
    } else {
        // КОРОТКИЙ: bounce — 75% → peak → 0%
        var peakF = Math.round(totalF * 0.5);
        scale.setValueAtTime(f2t(inT, 0),      [75,      75,      100]);
        scale.setValueAtTime(f2t(inT, peakF),  [peakVal, peakVal, 100]);
        scale.setValueAtTime(f2t(inT, totalF), [0,       0,       100]);
        opacity.setValueAtTime(f2t(inT, totalF - 3), 100);
        opacity.setValueAtTime(f2t(inT, totalF),       0);
    }
}

// ============================================================
// Главная функция
// ============================================================
(function () {
    // Выбор JSON файла
    var jsonFile = File.openDialog("Выбери JSON файл с субтитрами", "JSON:*.json");
    if (!jsonFile) return;

    var content = readFile(jsonFile.fsName);
    if (!content) { alert("Не удалось прочитать файл!"); return; }

    var subs = parseJSON(content);
    if (!subs || !subs.length) {
        alert("Ошибка парсинга JSON!\n\nФормат:\n[\n  {\"text\": \"слово\", \"in\": 0.0, \"out\": 1.5},\n  ...\n]");
        return;
    }

    // Ищем композицию "Текст"
    var comp = null;
    for (var i = 1; i <= app.project.numItems; i++) {
        var item = app.project.items[i];
        if (item instanceof CompItem && item.name === "\u0422\u0435\u043a\u0441\u0442") {
            comp = item; break;
        }
    }
    if (!comp) { alert("Композиция 'Текст' не найдена в проекте!"); return; }

    app.beginUndoGroup("Blast Subtitles");

    // Удаляем старые слои кроме аудио
    var toDelete = [];
    for (var i = 1; i <= comp.numLayers; i++) {
        var L = comp.layers[i];
        if (L.hasAudio && !L.hasVideo) continue;
        toDelete.push(L);
    }
    for (var i = 0; i < toDelete.length; i++) toDelete[i].remove();

    // Вычисляем exit_t
    var exitTimes = calcExits(subs);

    // Создаём субтитры
    var prevOutPoint = undefined;
    for (var s = 0; s < subs.length; s++) {
        var layer = createSubtitle(comp, subs[s], subs[s].type === "long", exitTimes[s], prevOutPoint);
        if (layer) prevOutPoint = layer.outPoint;
    }

    app.endUndoGroup();
    alert("Готово! Создано " + subs.length + " субтитров из файла:\n" + jsonFile.name);
})();
