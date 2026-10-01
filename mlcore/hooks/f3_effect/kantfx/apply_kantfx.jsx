/*
 * apply_kantfx.jsx — общий скрипт эффектов из пресетов Kant Tools (.ffx) для f3_effect.
 *
 * Один скрипт на все записи манифеста с полем `preset`: у каждой свой .ffx в kantfx/.
 * Параметры приходят через $.global.__BLAST, как у остальных скриптов f3.
 *
 * Где взять пресет:
 *   presetBin — содержимое .ffx строкой «символ = байт» (overlay.js_binary_literal). Так его
 *               передаёт overlay.py: скрипт вставляется текстом в render JSX, а бинарник на
 *               ноду отдельно не доставляется. Пишется во временный файл как есть (без
 *               декодирования в ExtendScript — оно на больших пресетах не укладывалось в
 *               таймаут ноды) и удаляется после применения.
 *   preset    — путь к .ffx на диске (run_job.jsx: BASE + "/" + effect.preset).
 *
 * Режимы (mode):
 *   "window" — стилизация: adjustment-слой на [startTime, startTime + duration), пресет на весь слой.
 *              duration = null → до конца компа.
 *   "cuts"   — переход: на каждой склейке из cuts свой adjustment-слой длиной span;
 *              ключи пресета стартуют от времени склейки (applyPreset ставит их от comp.time).
 *
 * Пресет со сторонним плагином, которого нет на ноде, не добавляет эффектов — это ошибка
 * (throw), а не тихий пустой слой: рендер без эффекта, выбранного человеком, хуже падения.
 */
(function () {
    var CONFIG = {
        targetCompName: null,
        preset: null,
        presetBin: null,
        mode: "window",
        startTime: 0,
        duration: null,
        cuts: [],
        span: 0.5,
        place: "below:Текст",
        label: "Kant FX"
    };
    var P = $.global.__BLAST || {};
    for (var k in P) if (P.hasOwnProperty(k) && P[k] !== undefined) CONFIG[k] = P[k];

    function findComp(name) {
        for (var i = 1; i <= app.project.numItems; i++) {
            var it = app.project.item(i);
            if (it instanceof CompItem && it.name === name) return it;
        }
        return null;
    }

    function placeLayer(comp, layer) {
        var m = /^below:(.+)$/.exec(String(CONFIG.place || ""));
        if (!m) return;
        for (var i = 1; i <= comp.numLayers; i++) {
            var ref = comp.layer(i);
            if (ref !== layer && ref.name === m[1]) { layer.moveAfter(ref); return; }
        }
    }

    function adjustment(comp, name, t0, t1) {
        var l = comp.layers.addSolid([1, 1, 1], name, comp.width, comp.height, comp.pixelAspect, comp.duration);
        l.adjustmentLayer = true;
        l.startTime = 0;
        l.inPoint = Math.max(0, t0);
        l.outPoint = Math.min(comp.duration, Math.max(t1, t0 + comp.frameDuration));
        placeLayer(comp, l);
        return l;
    }

    function presetFile() {
        if (CONFIG.presetBin) {
            var f = new File(Folder.temp.fsName + "/blast_kantfx_" + (new Date().getTime()) + "_" + Math.floor(Math.random() * 1e6) + ".ffx");
            f.encoding = "BINARY";
            if (!f.open("w")) throw new Error("apply_kantfx: не открыть временный файл " + f.fsName);
            f.write(String(CONFIG.presetBin));
            f.close();
            return { file: f, temp: true };
        }
        var p = new File(String(CONFIG.preset || ""));
        if (!p.exists) throw new Error("apply_kantfx: нет пресета " + CONFIG.preset);
        return { file: p, temp: false };
    }

    function apply(comp, layer, file, at) {
        var keep = comp.time;
        comp.time = Math.max(0, Math.min(comp.duration - comp.frameDuration, at));
        try { layer.applyPreset(file); } finally { comp.time = keep; }
        if (!layer.property("ADBE Effect Parade").numProperties) {
            throw new Error("apply_kantfx: пресет " + CONFIG.label + " не добавил эффектов — нет плагина на ноде?");
        }
    }

    var comp = findComp(CONFIG.targetCompName);
    if (!comp) throw new Error("apply_kantfx: нет компа " + CONFIG.targetCompName);
    var src = presetFile();
    try {
        if (CONFIG.mode === "cuts") {
            var cuts = CONFIG.cuts || [], span = Math.max(comp.frameDuration * 2, Number(CONFIG.span) || 0.5);
            for (var c = 0; c < cuts.length; c++) {
                var t = Number(cuts[c]);
                if (!(t > 0) || t >= comp.duration) continue;
                apply(comp, adjustment(comp, CONFIG.label + " @" + t.toFixed(2), t, t + span), src.file, t);
            }
        } else {
            var t0 = Number(CONFIG.startTime) || 0;
            var t1 = CONFIG.duration !== null && CONFIG.duration !== undefined ? t0 + Number(CONFIG.duration) : comp.duration;
            apply(comp, adjustment(comp, CONFIG.label, t0, t1), src.file, t0);
        }
    } finally {
        if (src.temp) { try { src.file.remove(); } catch (e) {} }
    }
})();
