// Измеряет в AE типографские метрики шрифтов субтитров (чернильные, по sourceRectAtTime).
// Все числа — в пикселях на 100 pt.
//   прописные (как в jakson: капс, трекинг −50): cap_h, accent_top (ЙЁ), desc_bottom (ДЩЦ), advance_per_char
//   строчные (как у акцентного слова: трекинг 0): x_h (о), lc_asc_top (бй), lc_desc_bottom (дру), lc_advance_per_char
// Вход:  <Folder.temp>/blast_font_lab/fonts.json   [{"ps": "Point-SemiBold"}, ...]
// Выход: <Folder.temp>/blast_font_lab/metrics.json
// Шрифт, который AE подменил, — ошибка в отчёте (не подстановка).
(function () {
    var dir = new Folder(Folder.temp.fsName + "/blast_font_lab");
    var inFile = new File(dir.fsName + "/fonts.json");
    var outFile = new File(dir.fsName + "/metrics.json");

    function readJson(f) {
        f.encoding = "UTF-8";
        if (!f.open("r")) throw new Error("cannot read " + f.fsName);
        var s = f.read();
        f.close();
        return eval("(" + s + ")");
    }
    function q(s) {
        return '"' + String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
    }
    function num(k, v) { return q(k) + ":" + v.toFixed(3); }

    var SIZE = 100;
    var CAPS_TRACKING = -50;
    var LC_TRACKING = 0;
    var CAPS = { cap: "Н", accent: "ЙЁ", desc: "ДЩЦ", width: "СЪЕШЬ ЖЕ ЕЩЁ ЭТИХ МЯГКИХ ФРАНЦУЗСКИХ БУЛОК" };
    // xh — одно «о» (у скриптов бывает крошечной/приподнятой). body — МЕДИАНА по
    // отдельным буквам высоты строчных: рамку целого слова раздувают соединительные
    // штрихи, одну букву — её причуды; медиана устойчива к обоим.
    var LC = { xh: "о", body: ["н", "а", "м", "е", "с", "о"], asc: "бй", desc: "дру", width: "съешь же ещё этих мягких французских булок" };
    function median(a) { a = a.slice().sort(function (x, y) { return x - y; }); var n = a.length; return n % 2 ? a[(n - 1) / 2] : (a[n / 2 - 1] + a[n / 2]) / 2; }

    // Только в чистом проекте: пустой несохранённый ИЛИ наш сохранённый лаб-проект
    // без изменений (его закрытие ничего не теряет). В конце закрываем без
    // сохранения — иначе AE оставит «грязный» проект и всплывёт «Сохранить как».
    var p = app.project;
    if (!p || p.renderQueue.numItems !== 0) throw new Error("font metrics: render queue is busy");
    if (p.file) {
        if (p.dirty || !/^(jakson|impulse|tape|trendy|brat)-/.test(String(p.file.name))) {
            throw new Error("font metrics: foreign or modified project is open: " + p.file.fsName);
        }
        p.close(CloseOptions.DO_NOT_SAVE_CHANGES);
        app.newProject();
    } else if (p.numItems !== 0) {
        throw new Error("font metrics: AE must have a clean empty project");
    }

    var fonts = readJson(inFile);
    var comp = app.project.items.addComp("_font_metrics_probe", 1080, 1920, 1, 1, 23.976);
    var rows = [];

    // doc.font возвращает запрошенное имя даже при подмене — этого мало.
    // Проверяем по реестру шрифтов AE (24+): шрифт должен быть найден и не быть заменой.
    function requireRealFont(ps) {
        var found = app.fonts.getFontsByPostScriptName(ps);
        if (!found || found.length === 0) throw new Error("font not resolvable in AE: " + ps);
        for (var k = 0; k < found.length; k++) {
            if (!found[k].isSubstitute) return;
        }
        throw new Error("font is a substitute in AE (unresolvable): " + ps);
    }

    function measure(ps, text, tracking) {
        var layer = comp.layers.addText(text);
        var src = layer.property("Source Text");
        var doc = src.value;
        doc.text = text;
        doc.font = ps;
        doc.fontSize = SIZE;
        doc.tracking = tracking;
        doc.applyFill = true;
        doc.applyStroke = false;
        doc.justification = ParagraphJustification.LEFT_JUSTIFY;
        src.setValue(doc);
        var got = src.value.font;
        var r = layer.sourceRectAtTime(0, false);
        layer.remove();
        if (got !== ps) throw new Error("font substituted: requested=" + ps + " got=" + got);
        return r;
    }

    for (var i = 0; i < fonts.length; i++) {
        var ps = fonts[i].ps;
        try {
            requireRealFont(ps);
            var cap = measure(ps, CAPS.cap, CAPS_TRACKING);
            var acc = measure(ps, CAPS.accent, CAPS_TRACKING);
            var dsc = measure(ps, CAPS.desc, CAPS_TRACKING);
            var wid = measure(ps, CAPS.width, CAPS_TRACKING);
            var xh = measure(ps, LC.xh, LC_TRACKING);
            var tops = [], bots = [];
            for (var b = 0; b < LC.body.length; b++) {
                var rb = measure(ps, LC.body[b], LC_TRACKING);
                tops.push(-rb.top);
                bots.push(rb.top + rb.height);
            }
            var lasc = measure(ps, LC.asc, LC_TRACKING);
            var ldsc = measure(ps, LC.desc, LC_TRACKING);
            var lwid = measure(ps, LC.width, LC_TRACKING);
            rows.push("{" + [
                q("ps") + ":" + q(ps),
                q("ok") + ":true",
                num("cap_h", -cap.top),                         // базовая линия = y 0
                num("accent_top", -acc.top),
                num("desc_bottom", dsc.top + dsc.height),
                num("advance_per_char", wid.width / CAPS.width.length),
                num("x_h", -xh.top),
                num("x_bottom", xh.top + xh.height),              // низ «о» (обычно ≈0, у части скриптов — нет)
                num("body_top", median(tops)),                    // медиана верха строчных н,а,м,е,с,о
                num("body_bottom", median(bots)),                 // медиана низа (+ = под базовой)
                num("lc_asc_top", -lasc.top),
                num("lc_desc_bottom", ldsc.top + ldsc.height),
                num("lc_advance_per_char", lwid.width / LC.width.length)
            ].join(",") + "}");
        } catch (e) {
            rows.push("{" + q("ps") + ":" + q(ps) + "," + q("ok") + ":false," + q("error") + ":" + q(e.toString()) + "}");
        }
    }
    comp.remove();

    outFile.encoding = "UTF-8";
    if (!outFile.open("w")) throw new Error("cannot write " + outFile.fsName);
    outFile.write("{\"size_pt\":" + SIZE + ",\"caps_tracking\":" + CAPS_TRACKING + ",\"lc_tracking\":" + LC_TRACKING +
        ",\"fonts\":[\n" + rows.join(",\n") + "\n]}\n");
    outFile.close();
    app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);
})();
