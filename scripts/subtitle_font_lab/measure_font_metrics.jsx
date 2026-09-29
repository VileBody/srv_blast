// Измеряет в AE типографские метрики шрифтов субтитров (чернильные, по sourceRectAtTime).
// Все числа — в пикселях на 100 pt, при трекинге/капсе как у jakson.
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

    var SIZE = 100;
    var TRACKING = -50;
    var SAMPLES = {
        cap: "Н",
        accent: "ЙЁ",
        desc: "ДЩЦ",
        width: "СЪЕШЬ ЖЕ ЕЩЁ ЭТИХ МЯГКИХ ФРАНЦУЗСКИХ БУЛОК"
    };

    // Только в чистом пустом проекте; в конце закрываем без сохранения —
    // иначе AE оставит «грязный» проект и всплывёт «Сохранить как».
    if (!app.project || app.project.file || app.project.numItems !== 0 || app.project.renderQueue.numItems !== 0) {
        throw new Error("font metrics: AE must have a clean empty project");
    }
    var fonts = readJson(inFile);
    var comp = app.project.items.addComp("_font_metrics_probe", 1080, 1920, 1, 1, 23.976);
    var rows = [];

    function measure(ps, text) {
        var layer = comp.layers.addText(text);
        var src = layer.property("Source Text");
        var doc = src.value;
        doc.text = text;
        doc.font = ps;
        doc.fontSize = SIZE;
        doc.tracking = TRACKING;
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
            var cap = measure(ps, SAMPLES.cap);
            var acc = measure(ps, SAMPLES.accent);
            var dsc = measure(ps, SAMPLES.desc);
            var wid = measure(ps, SAMPLES.width);
            var capH = -cap.top;                        // базовая линия = y 0
            var nChars = SAMPLES.width.length;
            rows.push("{" + [
                q("ps") + ":" + q(ps),
                q("ok") + ":true",
                q("cap_h") + ":" + capH.toFixed(3),
                q("cap_ink_h") + ":" + cap.height.toFixed(3),
                q("accent_top") + ":" + (-acc.top).toFixed(3),
                q("desc_bottom") + ":" + (dsc.top + dsc.height).toFixed(3),
                q("width_sample_px") + ":" + wid.width.toFixed(3),
                q("width_sample_chars") + ":" + nChars,
                q("advance_per_char") + ":" + (wid.width / nChars).toFixed(3)
            ].join(",") + "}");
        } catch (e) {
            rows.push("{" + q("ps") + ":" + q(ps) + "," + q("ok") + ":false," + q("error") + ":" + q(e.toString()) + "}");
        }
    }
    comp.remove();

    outFile.encoding = "UTF-8";
    if (!outFile.open("w")) throw new Error("cannot write " + outFile.fsName);
    outFile.write("{\"size_pt\":" + SIZE + ",\"tracking\":" + TRACKING + ",\"fonts\":[\n" + rows.join(",\n") + "\n]}\n");
    outFile.close();
    app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);
})();
