// templates/bucket_preview/photo_montage_template.jsx
//
// Standalone "example montage" builder for PHOTO bucket previews. NOT part of the
// main render template — it builds a tiny 1920x1440 sequence of N representative
// STILLS (~1.5s each) with the founder's cover-fit + scale animation (base cover
// -> +grow over the clip -> +punch in the final frames), so a preview looks like
// the real photo render. Saves the mp4 and writes ae_status.txt in the exact
// contract the render node (ae_sdk.py) expects (OK / output=... / compName=...).
//
// The python side (scripts/build_bucket_previews.py) replaces the
// /*__MONTAGE_DATA__*/ marker with:  var MONTAGE = { ..., anim: {...} };
// Photos are downloaded by the node into APP_DIR/media/video/<file_name> (the
// render manifest routes non-audio source_footage there, same as the real photo
// render), so the JSX resolves absolute paths from APP_DIR + relpath itself.
(function () {
    // ---- env + helpers (mirrors the footage montage / job template contract) ----
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
        logLine("PHOTO MONTAGE ERROR: " + msg);
        writeStatus("ERROR", String(msg));
    }

    if (typeof MONTAGE === "undefined" || !MONTAGE) {
        fail("MONTAGE spec is not defined");
        return;
    }

    try {
        var clips = MONTAGE.clips || [];
        if (!clips.length) throw new Error("MONTAGE.clips is empty");

        var W   = MONTAGE.width  || 1920;
        var H   = MONTAGE.height || 1440;
        var FPS = MONTAGE.fps    || 23.976023976023978;
        var SPC = MONTAGE.seconds_per_clip || 1.5;
        var compName = MONTAGE.comp_name || COMP_NAME || "Photo Bucket Preview";

        // Founder's scale-animation constants (mirror app/photo_comp.PHOTO_ANIM /
        // templates/photo_template.j2). Injected by python; defaults match the
        // real render so a preview is faithful even if the spec omits them.
        var A = MONTAGE.anim || {};
        var GROW    = (A.grow != null) ? A.grow : 10;
        var PUNCH   = (A.punch != null) ? A.punch : 20;
        var PUNCH_F = (A.punch_frames != null) ? A.punch_frames : 4;
        var OVERSCAN= (A.overscan != null) ? A.overscan : 1.002;
        var EASE    = (A.ease != null) ? A.ease : 33.33;

        var FD = 1.0 / FPS;
        function snap(t) { return Math.round(t / FD) * FD; }
        function coverScale(sw, sh) {
            // larger of the two ratios fills the frame with no black bars
            return Math.max(W / sw, H / sh) * 100.0 * OVERSCAN;
        }
        function easeAllKeys(prop, infl) {
            var e = new KeyframeEase(0, infl);
            var dim = (prop.value instanceof Array) ? prop.value.length : 1;
            for (var k = 1; k <= prop.numKeys; k++) {
                prop.setInterpolationTypeAtKey(k, KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER);
                var arr = []; for (var d = 0; d < dim; d++) { arr.push(e); }
                prop.setTemporalEaseAtKey(k, arr);
            }
        }
        function scaleVec(prop, s) { return (prop.value.length === 3) ? [s, s, 100] : [s, s]; }

        app.beginSuppressDialogs();

        // Import stills first; keep only the ones AE can actually read. Unreadable
        // / missing files are SKIPPED (no ugly placeholder) — the montage is built
        // from the good stills only, so it's just slightly shorter.
        var items = [];
        for (var i = 0; i < clips.length; i++) {
            var clip = clips[i];
            var rel = clip.relpath || ("media/video/" + clip.file_name);
            var f = new File(APP_DIR + "/" + rel);
            if (!f.exists) { logLine("photo missing on disk: " + f.fsName); continue; }
            var imp = null;
            try {
                var io = new ImportOptions(f);
                if (io.canImportAs(ImportAsType.FOOTAGE)) imp = app.project.importFile(io);
            } catch (eImp) {
                logLine("import skipped (unreadable) " + clip.file_name + ": " + eImp.toString());
            }
            if (imp) items.push(imp);
            else logLine("photo skipped: " + clip.file_name);
        }
        if (!items.length) throw new Error("no importable stills for montage");

        var duration = SPC * items.length;
        var comp = app.project.items.addComp(compName, W, H, 1.0, duration, FPS);

        for (var k = 0; k < items.length; k++) {
            var it = items[k];
            var layer = comp.layers.add(it);
            var tIn  = snap(k * SPC);
            var tOut = snap((k + 1) * SPC);
            layer.startTime = tIn;
            try { layer.inPoint  = tIn; } catch (eIn) {}
            try { layer.outPoint = tOut; } catch (eOut) {}
            if (layer.hasAudio) { try { layer.audioEnabled = false; } catch (eA) {} }

            var sw = 0, sh = 0;
            try { sw = it.width; sh = it.height; } catch (eDim) {}
            var scale = layer.property("Scale");
            if (sw > 0 && sh > 0) {
                var s0 = coverScale(sw, sh);
                var tPunch = snap(tOut - PUNCH_F * FD);
                if (tPunch <= tIn) tPunch = snap(tIn + FD);  // guard tiny clips
                // base cover -> +grow across the clip -> +punch shootout at the end
                scale.setValueAtTime(tIn,    scaleVec(scale, s0));
                scale.setValueAtTime(tPunch, scaleVec(scale, s0 + GROW));
                scale.setValueAtTime(tOut,   scaleVec(scale, s0 + PUNCH));
                easeAllKeys(scale, EASE);
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
                try { td.font = MONTAGE.label_font || "Point-Regular"; } catch (eF) { try { td.font = "Point-Regular"; } catch (eF2) {} }
                td.fontSize = MONTAGE.label_size || 64;
                td.fillColor = [1, 1, 1];
                td.applyFill = true;
                td.applyStroke = false;   // no outline (per design)
                td.justification = ParagraphJustification.CENTER_JUSTIFY;
                tp.setValue(td);
            } catch (eTxt) {
                logLine("label styling failed: " + eTxt.toString());
            }
            // horizontal frame: keep the caption clear of the bottom edge
            try { txt.property("Position").setValue([W / 2, H - 160]); } catch (ePos2) {}
        }

        if (!APP_DIR) throw new Error("APP_DIR is empty, cannot render");

        // ---- render the mp4 right here via the render queue (one visible step) ----
        var outRel = (typeof OUTPUT_REL !== "undefined" && OUTPUT_REL) ? OUTPUT_REL : "work/output.mp4";
        var outFile = new File(APP_DIR + "/" + outRel);
        if (outFile.parent && !outFile.parent.exists) outFile.parent.create();
        if (outFile.exists) { try { outFile.remove(); } catch (eRm) {} }

        try { app.project.save(new File(APP_DIR + "/debug_" + (JOB_ID || "photo_bucket_preview") + ".aep")); } catch (eSave) {}

        var rqItem = app.project.renderQueue.items.add(comp);
        var om = rqItem.outputModule(1);
        var picked = "";
        try {
            var tmpls = om.templates;
            for (var ti = 0; ti < tmpls.length; ti++) {
                if (/h\.?264|mp4|264/i.test(String(tmpls[ti]))) { picked = tmpls[ti]; break; }
            }
        } catch (eTpl) {}
        logLine("render templates picked=" + picked);
        if (picked) { try { om.applyTemplate(picked); } catch (eApply) { logLine("applyTemplate failed: " + eApply.toString()); } }
        try { om.file = outFile; } catch (eFile) { logLine("set om.file failed: " + eFile.toString()); }

        app.endSuppressDialogs(false);
        logLine("photo montage render start -> " + outFile.fsName);
        app.project.renderQueue.render();

        var ok = false;
        try { ok = (outFile.exists && outFile.length > 0); } catch (eChk) { ok = outFile.exists; }
        if (ok) {
            var msg = "output=" + outFile.fsName + "\ncompName=" + comp.name;
            logLine("PHOTO MONTAGE RENDERED: " + msg);
            writeStatus("OK", msg);
        } else {
            fail("render produced no output file: " + outFile.fsName + " (template=" + picked + ")");
        }
    } catch (err) {
        try { app.endSuppressDialogs(false); } catch (e3) {}
        fail(err && err.toString ? err.toString() : String(err));
    } finally {
        // clean up so the project doesn't accumulate junk across jobs.
        try {
            var rq = app.project.renderQueue;
            while (rq.numItems > 0) { rq.item(1).remove(); }
        } catch (eRq) {}
        try {
            var proj = app.project;
            for (var ci = proj.numItems; ci >= 1; ci--) {
                try { proj.item(ci).remove(); } catch (eItem) {}
            }
        } catch (eClean) {}
        try { app.purge(PurgeTarget.ALL_CACHES); } catch (ePurge) {}
    }
})();
