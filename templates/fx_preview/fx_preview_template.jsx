// templates/fx_preview/fx_preview_template.jsx
//
// Превью одного F3-эффекта (склейка или стилизация) на сырых клипах одной группы вайба.
// Собирается scripts/build_fx_previews.py и рендерится нодой через /render (inline JSX + media[]),
// в контракте afterfx_queue: билдер сохраняет .aep и пишет aep=/compName=, рендер — обёртка ноды.
//
// Эффект ставится НЕ своим кодом, а тем же F3-блоком, что в проде: питон подставляет на место
// маркер F3_OVERLAY результат mlcore.hooks.f3_effect.overlay.build_overlay_jsx(...)
// (переход — на склейку монтажного стола, стилизация — окном), поэтому превью = рендер ролика.
//
// маркер PREVIEW_DATA → var PREVIEW = { width, height, fps, duration, comp_name,
//   clips: [{ relpath, source_start, at, dur }] }   — клип показывает [source_start, +dur)
//   своего файла на отрезке [at, at+dur) композиции.
(function () {
    function getEnvSafe(name, defValue) {
        try {
            var v = $.getenv(name);
            if (v === null || v === undefined || v === "") return defValue;
            return v;
        } catch (e) {
            return defValue;
        }
    }

    var APP_DIR = getEnvSafe("APP_DIR", "");
    var JOB_ID = getEnvSafe("JOB_ID", "");
    if (!APP_DIR) {
        try { APP_DIR = new File($.fileName).parent.fsName; } catch (e1) {}
    }
    var STATUS_PATH = APP_DIR ? (APP_DIR + "/ae_status.txt") : "";
    var LOG_PATH = APP_DIR ? (APP_DIR + "/ae_job_log") : "";

    function logLine(msg) {
        if (LOG_PATH) {
            try {
                var f = new File(LOG_PATH);
                f.encoding = "UTF-8";
                f.open("a");
                f.lineFeed = "Unix";
                f.writeln(msg);
                f.close();
            } catch (e) {}
        }
        try { $.writeln(msg); } catch (e2) {}
    }

    function writeStatus(status, message) {
        if (!STATUS_PATH) return;
        try {
            var f = new File(STATUS_PATH);
            f.encoding = "UTF-8";
            if (f.exists) f.remove();
            f.open("w", "TEXT", "????");
            f.lineFeed = "Unix";
            f.writeln(status);
            if (message) {
                var lines = message.split("\n");
                for (var i = 0; i < lines.length; i++) f.writeln(lines[i]);
            }
            f.close();
        } catch (e) {}
    }

    /*__PREVIEW_DATA__*/

    function fail(msg) {
        logLine("FX PREVIEW ERROR: " + msg);
        writeStatus("ERROR", String(msg));
    }
    if (typeof PREVIEW === "undefined" || !PREVIEW) { fail("PREVIEW spec is not defined"); return; }

    // F3-блок ищет эти глобалы (как в шаблоне рендера ролика)
    var MAIN_COMP = null;
    var __APP_DIR = APP_DIR;

    try {
        var W = PREVIEW.width || 1080, H = PREVIEW.height || 1920, FPS = PREVIEW.fps || 23.976;
        app.beginSuppressDialogs();
        var comp = app.project.items.addComp(PREVIEW.comp_name || "FX Preview", W, H, 1.0, PREVIEW.duration, FPS);

        for (var i = 0; i < PREVIEW.clips.length; i++) {
            var c = PREVIEW.clips[i];
            var f = new File(APP_DIR + "/" + c.relpath);
            if (!f.exists) throw new Error("clip missing on disk: " + f.fsName);
            var item = app.project.importFile(new ImportOptions(f));
            var layer = comp.layers.add(item);
            layer.startTime = c.at - c.source_start;          // кадр source_start приходится на момент at
            layer.inPoint = c.at;
            layer.outPoint = c.at + c.dur;
            if (layer.hasAudio) { try { layer.audioEnabled = false; } catch (eA) {} }
            var s = Math.max(W / item.width, H / item.height) * 100;   // cover под вертикаль
            layer.property("Scale").setValue([s, s]);
            layer.property("Position").setValue([W / 2, H / 2]);
        }
        // F3 кладёт свои adjustment-слои «below:Текст» — как в ролике, где сверху лежат субтитры
        var ref = comp.layers.addNull();
        ref.name = "Текст";

        MAIN_COMP = comp;
        /*__F3_OVERLAY__*/

        // контракт afterfx_queue ноды: билдер сохраняет .aep и оставляет его ОТКРЫТЫМ,
        // в статусе aep= и compName= — рендерит уже обёртка ноды (свой render здесь не нужен)
        var aep = new File(APP_DIR + "/fx_preview_" + (JOB_ID || "job") + ".aep");
        app.project.save(aep);
        app.endSuppressDialogs(false);
        writeStatus("OK", "aep=" + aep.fsName + "\ncompName=" + comp.name);
    } catch (err) {
        try { app.endSuppressDialogs(false); } catch (e3) {}
        fail(err && err.toString ? err.toString() + (err.line ? " (line " + err.line + ")" : "") : String(err));
    }
})();
