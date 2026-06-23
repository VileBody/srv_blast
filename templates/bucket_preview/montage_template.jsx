// templates/bucket_preview/montage_template.jsx
//
// Standalone "example montage" builder for footage BUCKET PREVIEWS (precision
// flow, phase 4). NOT part of the main render template — it builds a tiny
// 1080x1920 sequence of N representative clips (~1.5s each) with an optional
// label caption, saves an .aep and writes ae_status.txt in the exact contract
// the render node (ae_sdk.py) expects (OK / aep=... / compName=...).
//
// The python side (scripts/build_bucket_previews.py) replaces the
// /*__MONTAGE_DATA__*/ marker with:  var MONTAGE = { ... };
// Clips are downloaded by the node into APP_DIR/media/video/<file_name>, so the
// JSX resolves absolute paths from APP_DIR + relpath itself (no PROJECT_DATA
// path-patching needed).
(function () {
    // ---- env + helpers (mirrors render_templates/job_template.jsx contract) ----
    function getEnvSafe(name, defValue) {
        try {
            var v = $.getenv(name);
            if (v === null || v === undefined || v === "") return defValue;
            return v;
        } catch (e) {
            return defValue;
        }
    }

    var APP_DIR   = getEnvSafe("APP_DIR", "");
    var JOB_ID    = getEnvSafe("JOB_ID", "");
    var COMP_NAME = getEnvSafe("COMP_NAME", "");

    if (!APP_DIR) {
        try {
            var jsxFile = new File($.fileName);
            if (jsxFile && jsxFile.parent) APP_DIR = jsxFile.parent.fsName;
        } catch (e1) {}
    }
    if (!JOB_ID && APP_DIR) {
        try {
            var parts = APP_DIR.replace(/\\/g, "/").split("/");
            var last = parts[parts.length - 1];
            JOB_ID = (last && last.toLowerCase() === "app" && parts.length >= 2)
                ? parts[parts.length - 2] : last;
        } catch (e2) {}
    }

    var STATUS_PATH = APP_DIR ? (APP_DIR + "/ae_status.txt") : "";
    var LOG_PATH    = APP_DIR ? (APP_DIR + "/ae_job_log") : "";

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

    // ---- injected montage spec ----
    /*__MONTAGE_DATA__*/

    function fail(msg) {
        logLine("MONTAGE ERROR: " + msg);
        writeStatus("ERROR", String(msg));
    }

    if (typeof MONTAGE === "undefined" || !MONTAGE) {
        fail("MONTAGE spec is not defined");
        return;
    }

    try {
        var clips = MONTAGE.clips || [];
        if (!clips.length) throw new Error("MONTAGE.clips is empty");

        var W   = MONTAGE.width  || 1080;
        var H   = MONTAGE.height || 1920;
        var FPS = MONTAGE.fps    || 23.976;
        var SPC = MONTAGE.seconds_per_clip || 1.5;
        var compName = MONTAGE.comp_name || COMP_NAME || "Bucket Preview";
        var duration = SPC * clips.length;

        app.beginSuppressDialogs();

        var comp = app.project.items.addComp(compName, W, H, 1.0, duration, FPS);

        for (var i = 0; i < clips.length; i++) {
            var clip = clips[i];
            var rel = clip.relpath || ("media/video/" + clip.file_name);
            var f = new File(APP_DIR + "/" + rel);
            var item = null;
            if (f.exists) {
                try {
                    var io = new ImportOptions(f);
                    if (io.canImportAs(ImportAsType.FOOTAGE)) {
                        item = app.project.importFile(io);
                    }
                } catch (eImp) {
                    logLine("import failed for " + rel + ": " + eImp.toString());
                }
            } else {
                logLine("clip missing on disk: " + f.fsName);
            }
            if (!item) {
                item = app.project.importPlaceholder(clip.file_name || ("clip" + i), W, H, FPS, SPC);
            }

            var layer = comp.layers.add(item);
            // place clip i into slot [i*SPC, (i+1)*SPC], showing its first SPC seconds
            layer.startTime = i * SPC;
            try { layer.inPoint  = i * SPC; } catch (eIn) {}
            try { layer.outPoint = (i + 1) * SPC; } catch (eOut) {}
            if (layer.hasAudio) { try { layer.audioEnabled = false; } catch (eA) {} }

            // cover-fit to fill the vertical frame
            var sw = 0, sh = 0;
            try { sw = item.width; sh = item.height; } catch (eDim) {}
            if (sw > 0 && sh > 0) {
                var sx = W / sw * 100.0;
                var sy = H / sh * 100.0;
                var s = (sx > sy) ? sx : sy;
                try { layer.property("Scale").setValue([s, s]); } catch (eSc) {}
            }
            try { layer.property("Position").setValue([W / 2, H / 2]); } catch (ePos) {}
        }

        // optional label caption — created LAST so it stays on top
        var label = String(MONTAGE.label || "");
        if (label) {
            var txt = comp.layers.addText(label);
            try {
                var tp = txt.property("Source Text");
                var td = tp.value;
                td.text = label;
                try { td.font = MONTAGE.label_font || "Calibri-Bold"; } catch (eF) { try { td.font = "Calibri"; } catch (eF2) {} }
                td.fontSize = MONTAGE.label_size || 64;
                td.fillColor = [1, 1, 1];
                td.applyFill = true;
                td.applyStroke = true;
                td.strokeColor = [0, 0, 0];
                td.strokeWidth = 6;
                td.justification = ParagraphJustification.CENTER_JUSTIFY;
                tp.setValue(td);
            } catch (eTxt) {
                logLine("label styling failed: " + eTxt.toString());
            }
            try { txt.property("Position").setValue([W / 2, H - 220]); } catch (ePos2) {}
        }

        if (!APP_DIR) throw new Error("APP_DIR is empty, cannot save AEP");
        var projFile = new File(APP_DIR + "/debug_" + (JOB_ID || "bucket_preview") + ".aep");
        if (projFile.parent && !projFile.parent.exists) projFile.parent.create();
        app.project.save(projFile);

        app.endSuppressDialogs(false);

        var msg = "aep=" + projFile.fsName + "\ncompName=" + comp.name;
        logLine("MONTAGE OK: " + msg);
        writeStatus("OK", msg);
    } catch (err) {
        try { app.endSuppressDialogs(false); } catch (e3) {}
        fail(err && err.toString ? err.toString() : String(err));
    } finally {
        try {
            if (app.project && typeof CloseOptions !== "undefined") {
                app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);
            }
        } catch (eClose) {}
    }
})();
