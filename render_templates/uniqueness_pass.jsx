/*
 * uniqueness_pass.jsx
 *
 * Per-clip geometric drift + optional mirror + color jitter to defeat
 * source-level fingerprinting on social platforms (TikTok/Meta/YT).
 *
 * INPUTS (globals injected by Python builder at build time):
 *   UNIQUENESS_SEED                    : integer (deterministic per JOB_ID)
 *   UNIQUENESS_ALLOW_MIRROR            : boolean (resolved from subgroup._people)
 *   UNIQUENESS_ENABLED                 : boolean (master kill switch)
 *   UNIQUENESS_GEOMETRY_ENABLED        : boolean (axis A — scale+offset)
 *   UNIQUENESS_MIRROR_ENABLED          : boolean (axis B — mirror)
 *   UNIQUENESS_COLOR_JITTER_ENABLED    : boolean (axis C — hue/sat/exp/gamma)
 *
 * ENV overrides (read at runtime inside AE, for hot-disable without rebuild):
 *   UNIQUENESS_ENABLED=0/1
 *   UNIQUENESS_GEOMETRY_ENABLED=0/1
 *   UNIQUENESS_MIRROR_ENABLED=0/1
 *   UNIQUENESS_COLOR_JITTER_ENABLED=0/1
 *
 * GUARANTEES:
 *   - No black borders: scale is always > 100% and offset is clamped by a
 *     runtime headroom check (actual scaled layer size vs comp size).
 *   - TEXT_COMP is never touched: only FootageItem layers are modified
 *     (CompItem layers are skipped).
 *   - No rotation.
 *   - Every applied transform is logged per-clip for easy manual rollback.
 *
 * RANGES:
 *   - Scale multiplier: ×1.05..×1.08 (multiplicative over cover-fit)
 *   - Offset: ±(scale_mult-1)/2 * 0.75 of comp size, clamped by actual headroom
 *   - Hue shift: ±3 degrees
 *   - Saturation delta: ±5 (on ADBE HUE SATURATION [-100,100] scale)
 *   - Exposure: ±0.05 stops
 *   - Gamma: 0.97..1.03
 */
(function () {
    // --- Helpers ---------------------------------------------------------
    function safeLogEarly(line) {
        try { if (typeof logLine === "function") logLine("INFO", "[uniqueness] " + String(line)); } catch (e) {}
        try { $.writeln("[uniqueness] " + String(line)); } catch (e2) {}
    }

    // Resolve target comp:
    //   1) Prefer MAIN_COMP from enclosing scope (injected by project_template.j2).
    //      Required on headless render nodes where openInViewer() does NOT
    //      promote a comp to app.project.activeItem.
    //   2) Fallback to app.project.activeItem (manual run inside AE GUI).
    var comp = null;
    try {
        if (typeof MAIN_COMP !== "undefined" && MAIN_COMP && (MAIN_COMP instanceof CompItem)) {
            comp = MAIN_COMP;
        }
    } catch (eMC) {}
    if (!comp) {
        try {
            if (app.project && app.project.activeItem && (app.project.activeItem instanceof CompItem)) {
                comp = app.project.activeItem;
            }
        } catch (eAI) {}
    }
    if (!comp) {
        safeLogEarly("no target comp (MAIN_COMP undefined, no active CompItem) — skipping uniqueness pass");
        return;
    }
    safeLogEarly("target comp resolved name=\"" + comp.name + "\" total_layers=" + comp.layers.length);

    function envFlag(name, fallbackGlobal) {
        try {
            var raw = $.getenv(name);
            if (raw !== null && raw !== undefined && raw !== "") {
                var v = String(raw).toLowerCase();
                if (v === "0" || v === "false" || v === "off" || v === "no") return false;
                if (v === "1" || v === "true" || v === "on" || v === "yes") return true;
            }
        } catch (e) {}
        return !!fallbackGlobal;
    }

    function safeLog(line) {
        try { if (typeof logLine === "function") logLine("INFO", line); } catch (e) {}
    }

    // Deterministic linear-congruential RNG — same seed → same sequence.
    function makeRNG(seed) {
        var s = (seed >>> 0) || 1;
        return function () {
            s = ((s * 1664525) + 1013904223) >>> 0;
            return s / 4294967296;
        };
    }

    // --- Feature flags (global value OR env override) --------------------
    var MASTER_ON = envFlag("UNIQUENESS_ENABLED",
        (typeof UNIQUENESS_ENABLED !== "undefined") ? !!UNIQUENESS_ENABLED : true);
    if (!MASTER_ON) {
        safeLog("[uniqueness] master kill switch UNIQUENESS_ENABLED=0 — skipping whole pass");
        return;
    }

    var GEO_ON = envFlag("UNIQUENESS_GEOMETRY_ENABLED",
        (typeof UNIQUENESS_GEOMETRY_ENABLED !== "undefined") ? !!UNIQUENESS_GEOMETRY_ENABLED : true);
    var MIRROR_ON = envFlag("UNIQUENESS_MIRROR_ENABLED",
        (typeof UNIQUENESS_MIRROR_ENABLED !== "undefined") ? !!UNIQUENESS_MIRROR_ENABLED : true);
    var COLOR_ON = envFlag("UNIQUENESS_COLOR_JITTER_ENABLED",
        (typeof UNIQUENESS_COLOR_JITTER_ENABLED !== "undefined") ? !!UNIQUENESS_COLOR_JITTER_ENABLED : true);

    var SEED = (typeof UNIQUENESS_SEED !== "undefined" && UNIQUENESS_SEED) ? UNIQUENESS_SEED : 42;
    var ALLOW_MIRROR_CFG = (typeof UNIQUENESS_ALLOW_MIRROR !== "undefined") ? !!UNIQUENESS_ALLOW_MIRROR : false;
    var APPLY_MIRROR = MIRROR_ON && ALLOW_MIRROR_CFG;

    safeLog("[uniqueness] begin: seed=" + SEED + " GEO=" + GEO_ON +
        " MIRROR=" + APPLY_MIRROR + " (allow=" + ALLOW_MIRROR_CFG + ",flag=" + MIRROR_ON + ")" +
        " COLOR=" + COLOR_ON);

    var globalRng = makeRNG(SEED);

    app.beginUndoGroup("Uniqueness Pass");
    try {
        // --- Collect footage layers -------------------------------------
        // Skip: adjustment, null, pre-comps (TEXT_COMP), non-FootageItem, audio-only.
        var footageLayers = [];
        for (var i = 1; i <= comp.layers.length; i++) {
            var L = comp.layers[i];
            if (L.adjustmentLayer) continue;
            if (L.nullLayer) continue;
            if (!L.source) continue;
            if (L.source instanceof CompItem) continue;
            if (!(L.source instanceof FootageItem)) continue;
            if (L.source.hasVideo !== true) continue;
            footageLayers.push(L);
        }
        safeLog("[uniqueness] footage layers found: " + footageLayers.length);

        // --- Axis A + B: per-clip geometry + mirror ---------------------
        if (GEO_ON || APPLY_MIRROR) {
            for (var k = 0; k < footageLayers.length; k++) {
                var L = footageLayers[k];
                var clipRng = makeRNG(((SEED >>> 0) + (k + 1) * 7919) >>> 0);

                var curScale = L.property("Scale").value;
                var origScaleX = curScale[0];
                var origScaleY = curScale[1];
                var newScaleX = origScaleX;
                var newScaleY = origScaleY;
                var scaleMult = 1.0;
                var offsetX = 0;
                var offsetY = 0;
                var headroomOk = false;

                if (GEO_ON) {
                    scaleMult = 1.05 + clipRng() * 0.03; // 1.05 - 1.08
                    newScaleX = origScaleX * scaleMult;
                    newScaleY = origScaleY * scaleMult;
                }
                if (APPLY_MIRROR) {
                    newScaleX = -newScaleX;
                }

                // Write Scale only if it changed
                if (GEO_ON || APPLY_MIRROR) {
                    L.property("Scale").setValue([newScaleX, newScaleY]);
                }

                // Runtime headroom check — offset only inside actual overshoot
                if (GEO_ON) {
                    var src = L.source;
                    var actualScaledW = src.width * Math.abs(newScaleX) / 100;
                    var actualScaledH = src.height * Math.abs(newScaleY) / 100;
                    var headroomX = (actualScaledW - comp.width) / 2;
                    var headroomY = (actualScaledH - comp.height) / 2;
                    headroomOk = (headroomX > 0 && headroomY > 0);

                    if (headroomOk) {
                        var maxOffsetX = headroomX * 0.75;
                        var maxOffsetY = headroomY * 0.75;
                        offsetX = (clipRng() * 2 - 1) * maxOffsetX;
                        offsetY = (clipRng() * 2 - 1) * maxOffsetY;
                        var curPos = L.property("Position").value;
                        L.property("Position").setValue([curPos[0] + offsetX, curPos[1] + offsetY]);
                    }
                    // else: edge case — source smaller than comp after cover-fit.
                    //       Skip offset. Scale alone is still applied.
                }

                safeLog("[uniqueness] clip[" + k + "] name=\"" + L.name + "\"" +
                    " scale_mult=" + scaleMult.toFixed(4) +
                    " mirror=" + (APPLY_MIRROR ? "yes" : "no") +
                    " offset=(" + offsetX.toFixed(1) + "," + offsetY.toFixed(1) + ")px" +
                    " headroom_ok=" + headroomOk +
                    " orig_scale=(" + origScaleX.toFixed(2) + "," + origScaleY.toFixed(2) + ")");
            }
        } else {
            safeLog("[uniqueness] axis A+B disabled — skipping geometry/mirror");
        }

        // --- Axis C: color jitter (per-footage-layer, TEXT_COMP untouched) ---
        if (COLOR_ON) {
            var hueDeg   = (globalRng() * 2 - 1) * 3.0;
            var satDelta = (globalRng() * 2 - 1) * 5.0;
            var expStops = (globalRng() * 2 - 1) * 0.05;
            var gamma    = 1.0 + (globalRng() * 2 - 1) * 0.03;

            safeLog("[uniqueness] color jitter:" +
                " hue=" + hueDeg.toFixed(2) + "deg" +
                " sat=" + satDelta.toFixed(2) +
                " exp=" + expStops.toFixed(3) + "stops" +
                " gamma=" + gamma.toFixed(4));

            for (var j = 0; j < footageLayers.length; j++) {
                var FL = footageLayers[j];
                try {
                    var hs = FL.Effects.addProperty("ADBE HUE SATURATION");
                    try { hs.property("ADBE HUE SATURATION-0004").setValue(hueDeg); } catch (eH) {}
                    try { hs.property("ADBE HUE SATURATION-0005").setValue(satDelta); } catch (eS) {}
                } catch (eHS) {}
                try {
                    var ex = FL.Effects.addProperty("ADBE Exposure2");
                    try { ex.property("ADBE Exposure2-0003").setValue(expStops); } catch (eE) {}
                } catch (eEX) {}
                try {
                    var lv = FL.Effects.addProperty("ADBE Easy Levels2");
                    try { lv.property("ADBE Easy Levels2-0007").setValue(gamma); } catch (eG) {}
                } catch (eLV) {}
            }
        } else {
            safeLog("[uniqueness] axis C disabled — skipping color jitter");
        }

        safeLog("[uniqueness] done");
    } catch (err) {
        try { if (typeof logLine === "function") logLine("ERR", "[uniqueness] failed: " + err.toString()); } catch (e) {}
    }
    app.endUndoGroup();
})();
