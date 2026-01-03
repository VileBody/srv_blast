// render_templates/job_template.jsx
// AUTO-GENERATED (source of truth: render_templates/jsx_src/parts/*.jsxinc)
// Rebuild: python tools/build_job_template.py
(function () {
    // ==========================================
    // 0.1. LEGACY JS HELPERS (ExtendScript-safe)
    // ==========================================
    function _isArray(v) {
        try {
            return (v instanceof Array);
        } catch (e) {
            return false;
        }
    }

    // ==========================================
    // 0. ENV + HELPERS
    // ==========================================
    function getEnvSafe(name, defValue) {
        try {
            var v = $.getenv(name);
            if (v === null || v === undefined || v === "") return defValue;
            return v;
        } catch (e) {
            return defValue;
        }
    }

    var APP_DIR    = getEnvSafe("APP_DIR", "");
    var OUTPUT_REL = getEnvSafe("OUTPUT_REL", "work/output.mp4"); // сейчас не используем, но пусть живёт
    var JOB_ID     = getEnvSafe("JOB_ID", "");
    var COMP_NAME  = getEnvSafe("COMP_NAME", "");

    // Фолбэк: если env не доехал (GUI AE уже открыт) — берём папку, где лежит скрипт.
    if (!APP_DIR) {
        try {
            var jsxFile = new File($.fileName);
            if (jsxFile && jsxFile.parent) {
                APP_DIR = jsxFile.parent.fsName;
            }
        } catch (e1) {
            // APP_DIR остаётся пустым — это авария, но пайплайн залогирует
        }
    }

    // Фолбэк для JOB_ID: из имени папки перед "app"
    if (!JOB_ID && APP_DIR) {
        try {
            var normPath = APP_DIR.replace(/\\/g, "/");
            var parts = normPath.split("/");
            if (parts.length >= 2) {
                var last = parts[parts.length - 1];
                if (last.toLowerCase() === "app" && parts.length >= 2) {
                    JOB_ID = parts[parts.length - 2];
                } else {
                    JOB_ID = last;
                }
            }
        } catch (e2) {
            // останется пустым, будет debug_project.aep
        }
    }

    var LOG_FILE    = null;
    var STATUS_PATH = APP_DIR ? (APP_DIR + "/ae_status.txt") : "";
    var LOG_PATH    = APP_DIR ? (APP_DIR + "/ae_job_log") : "";

    function initLog() {
        if (!APP_DIR) return;
        try {
            // Reset log file, but do NOT keep it opened (aerender/AfterFX.com can be picky)
            var f = new File(LOG_PATH);
            f.encoding = "UTF-8";
            if (f.exists) f.remove();
            f.open("w");
            f.lineFeed = "Unix";
            f.writeln("=== AE JOB LOG START ===");
            f.close();
            LOG_FILE = true; // marker "logging enabled"
        } catch (e) {
            LOG_FILE = null;
        }
    }

    function _appendLog(line) {
        if (!LOG_PATH) return;
        try {
            var f = new File(LOG_PATH);
            f.encoding = "UTF-8";
            f.open("a");
            f.lineFeed = "Unix";
            f.writeln(line);
            f.close();
        } catch (e) {
            // last resort: console only
        }
    }

    function logLine(msg) {
        var prefix = "";
        try {
            prefix = (new Date()).toUTCString() + " ";
        } catch (e) {}
        var line = prefix + msg;

        // Always try to append; avoids "empty file" due to handle issues
        _appendLog(line);

        try {
            $.writeln(line);
        } catch (e2) {}
    }

    function closeLog() {
        // no-op: we don't keep the file handle open anymore
    }

    function writeStatus(status, message) {
        if (!STATUS_PATH) return;
        try {
            var f = new File(STATUS_PATH);
            f.encoding = "UTF-8";
            if (f.exists) {
                f.remove();
            }
            f.open("w", "TEXT", "????");
            f.lineFeed = "Unix";
            f.writeln(status);
            if (message) {
                // message может быть многострочным: aep=... \n compName=...
                var lines = message.split("\n");
                for (var i = 0; i < lines.length; i++) {
                    f.writeln(lines[i]);
                }
            }
            f.close();
        } catch (e) {
            // статус-файл nice-to-have, не ломаем скрипт
        }
    }

    initLog();
    logLine("JOB START; APP_DIR=" + APP_DIR + "; OUTPUT_REL=" + OUTPUT_REL + "; JOB_ID=" + JOB_ID);

    // ==========================================
    // 1. DATA INJECTION ZONE
    // ==========================================
    // Python заменит эту строку на: var PROJECT_DATA = { ... };
    /*__PYTHON_DATA_INJECT__*/

    if (typeof PROJECT_DATA === "undefined" || !PROJECT_DATA) {
        var errPd = "PROJECT_DATA is not defined";
        logLine("ERROR: " + errPd);
        writeStatus("ERROR", errPd);
        closeLog();
        return;
    }

    // ==========================================
    // 2. ENGINE CORE
    // ==========================================
    var itemRegistry = {};

    function getFolder(name) {
        for (var i = 1; i <= app.project.numItems; i++) {
            if (app.project.item(i) instanceof FolderItem && app.project.item(i).name === name) {
                return app.project.item(i);
            }
        }
        return app.project.items.addFolder(name);
    }

    var fComps   = getFolder("01_COMPS");
    var fFootage = getFolder("02_SOURCES");
    var fRef     = getFolder("99_REF");
    var fSolids  = getFolder("00_SOLIDS");

    function _resolveKeyTime(k, layerCtx) {
        if (!k) return null;
        if (k.time !== undefined && k.time !== null) return k.time;
        if (k.t !== undefined && k.t !== null && layerCtx) {
            var dur = (layerCtx.outPoint - layerCtx.inPoint);
            return layerCtx.inPoint + (k.t * dur);
        }
        return null;
    }

    // Resolve AE property by either:
    //  - string matchName/key
    //  - array path: ["Group", "Subgroup", "Prop"] or [1,2,3]
    function resolvePropByPath(root, path) {
        if (!root || path === undefined || path === null) return null;

        if (typeof path === "string") {
            try { return root.property(path); } catch (e1) { return null; }
        }

        if (_isArray(path)) {
            var cur = root;
            for (var i = 0; i < path.length; i++) {
                var seg = path[i];
                if (seg === undefined || seg === null) return null;
                try { cur = cur.property(seg); }
                catch (e2) {
                    try { cur = cur.property(parseInt(seg, 10)); } catch (e3) { return null; }
                }
                if (!cur) return null;
            }
            return cur;
        }
        return null;
    }

    function setPropValue(aeProp, valueData, layerCtx) {
        if (!aeProp) return;
        if (valueData === undefined || valueData === null) return;

        var isObj = (typeof valueData === "object") && !_isArray(valueData);

        // expression first (if any)
        if (isObj && valueData.expression !== undefined && aeProp.canSetExpression) {
            try {
                aeProp.expression = valueData.expression || "";
            } catch (eExpr) {
                logLine("WARN: expression set failed: " + eExpr.toString());
            }
        }

        // keyframes
        if (isObj && valueData.keys && valueData.keys.length > 0) {
            for (var i = 0; i < valueData.keys.length; i++) {
                var k = valueData.keys[i];
                if (!k) continue;
                var t = _resolveKeyTime(k, layerCtx);
                if (t === null || t === undefined) continue;
                if (k.value === undefined) continue;
                try {
                    aeProp.setValueAtTime(t, k.value);
                } catch (eKey) {
                    logLine("WARN: setValueAtTime failed: " + eKey.toString());
                }
            }
            return;
        }

        // wrapped value
        if (isObj && valueData.value !== undefined) {
            try {
                aeProp.setValue(valueData.value);
            } catch (eVal) {
                logLine("WARN: setValue failed: " + eVal.toString());
            }
            return;
        }

        // raw scalar/array/object
        try {
            aeProp.setValue(valueData);
        } catch (eSet) {
            logLine("WARN: setValue failed: " + eSet.toString());
        }
    }

    function applyEffects(layer, effectsConf) {
        if (!layer || !effectsConf || !effectsConf.length) return;
        var parade = layer.property("ADBE Effect Parade");
        if (!parade) {
            logLine("WARN: no Effect Parade on layer: " + layer.name);
            return;
        }

        for (var i = 0; i < effectsConf.length; i++) {
            var fxConf = effectsConf[i];
            if (!fxConf || !fxConf.matchName) continue;

            var fx = null;
            try {
                fx = parade.addProperty(fxConf.matchName);
            } catch (eAdd) {
                logLine("FX ADD FAIL: matchName=" + fxConf.matchName + " err=" + eAdd.toString());
                continue;
            }
            if (!fx) {
                logLine("FX ADD NULL: matchName=" + fxConf.matchName);
                continue;
            }

            var params = fxConf.params || {};
            if (_isArray(params)) {
                for (var pi = 0; pi < params.length; pi++) {
                    var entry = params[pi];
                    if (!entry) continue;
                    var path = entry.path !== undefined ? entry.path : entry.key;
                    var val = entry.value;
                    var p2 = resolvePropByPath(fx, path);
                    if (!p2) {
                        logLine("FX PARAM MISSING: fx=" + fxConf.matchName + " path=" + path);
                        continue;
                    }
                    setPropValue(p2, val, layer);
                }
            } else {
                for (var key in params) {
                    if (!params.hasOwnProperty(key)) continue;
                    var p = resolvePropByPath(fx, key);
                    if (!p) {
                        logLine("FX PARAM MISSING: fx=" + fxConf.matchName + " key=" + key);
                        continue;
                    }
                    setPropValue(p, params[key], layer);
                }
            }
        }
    }

    function importFootage(conf) {
        var file = new File(conf.path);
        var item = null;

        logLine("Import footage: id=" + (conf.id || "?") + " path=" + conf.path);

        if (!file.exists) {
            logLine("Footage missing, using placeholder: " + conf.path);
            item = app.project.importPlaceholder(conf.name || "missing_file", 1920, 1080, 24, 10);
        } else {
            try {
                var io = new ImportOptions(file);
                if (io.canImportAs(ImportAsType.FOOTAGE)) {
                    item = app.project.importFile(io);
                } else {
                    logLine("Cannot import as FOOTAGE: " + conf.path);
                    item = app.project.importPlaceholder(conf.name || "bad_footage", 1920, 1080, 24, 10);
                }
            } catch (e) {
                logLine("Import Error for " + conf.path + ": " + e.toString());
                item = app.project.importPlaceholder(conf.name || "error_footage", 1920, 1080, 24, 10);
            }
        }

        if (item) {
            if (conf.isRef) item.parentFolder = fRef;
            else item.parentFolder = fFootage;
        }
        return item;
    }

    function createComp(config) {
        var c = app.project.items.addComp(
            config.name,
            config.width,
            config.height,
            config.pixelAspect,
            config.duration,
            config.fps
        );
        c.parentFolder = fComps;
        logLine("Create comp: id=" + (config.id || "?") + " name=" + config.name);
        return c;
    }

    function applyTextSettings(textLayer, textDocConfig) {
        if (!textDocConfig) return;
        var textProp     = textLayer.property("Source Text");
        var textDocument = textProp.value;

        if (textDocConfig.text) textDocument.text = textDocConfig.text;

        var fontSet = false;
        try {
            if (textDocConfig.font) {
                textDocument.font = textDocConfig.font;
                fontSet = true;
            }
        } catch (eFont) {
            // AE может кинуть исключение, если шрифт отсутствует
            logLine("FONT SET FAIL: requested='" + textDocConfig.font + "' err=" + eFont.toString());
        }

        // Даже если исключения нет, AE может молча подменить шрифт.
        if (textDocConfig.font) {
            try {
                if (textDocument.font !== textDocConfig.font) {
                    logLine("FONT SUBSTITUTED: requested='" + textDocConfig.font + "' got='" + textDocument.font + "'");
                }
            } catch (eChk) {}
        }
        if (!fontSet) {
            try { textDocument.font = "Calibri"; } catch (eFb) {}
            if (textDocConfig.font) {
                logLine("FONT FALLBACK: requested='" + textDocConfig.font + "' -> Calibri");
            }
        }

        if (textDocConfig.fontSize)  textDocument.fontSize  = textDocConfig.fontSize;
        if (textDocConfig.tracking)  textDocument.tracking  = textDocConfig.tracking;
        if (textDocConfig.leading)   textDocument.leading   = textDocConfig.leading;

        if (textDocConfig.justification !== undefined) {
            var j = textDocConfig.justification;
            if (j === 1 || j === 7415 || j === "CENTER") textDocument.justification = ParagraphJustification.CENTER_JUSTIFY;
            else if (j === 0 || j === 7413 || j === "LEFT")  textDocument.justification = ParagraphJustification.LEFT_JUSTIFY;
            else if (j === 2 || j === 7414 || j === "RIGHT") textDocument.justification = ParagraphJustification.RIGHT_JUSTIFY;
        }

        if (textDocConfig.fillColor)   textDocument.fillColor   = textDocConfig.fillColor;
        if (textDocConfig.strokeColor) textDocument.strokeColor = textDocConfig.strokeColor;
        if (textDocConfig.strokeWidth) textDocument.strokeWidth = textDocConfig.strokeWidth;

        if (textDocConfig.applyFill === true)       textDocument.applyFill = true;
        else if (textDocConfig.applyFill === false) textDocument.applyFill = false;
        else if (textDocConfig.fillColor)           textDocument.applyFill = true;

        if (textDocConfig.applyStroke === true)       textDocument.applyStroke = true;
        else if (textDocConfig.applyStroke === false) textDocument.applyStroke = false;
        else if (textDocConfig.strokeColor || textDocConfig.strokeWidth) textDocument.applyStroke = true;

        textProp.setValue(textDocument);
    }

    // ==========================================
    // 1.X. TEXT ANIMATORS (BAKED APPLY)
    // ==========================================
    function clearTextAnimators(textLayer) {
        try {
            var tp = textLayer.property("ADBE Text Properties");
            if (!tp) return;
            var anims = tp.property("ADBE Text Animators");
            if (!anims) return;
            for (var i = anims.numProperties; i >= 1; i--) {
                try { anims.property(i).remove(); } catch (eRm) {}
            }
        } catch (e) {
            logLine("WARN: clearTextAnimators failed: " + e.toString());
        }
    }

    function applyTextAnimators(textLayer, animatorsConf) {
        if (!textLayer || !animatorsConf || !animatorsConf.length) return;

        var tp = textLayer.property("ADBE Text Properties");
        if (!tp) { logLine("WARN: no Text Properties on layer: " + textLayer.name); return; }

        var anims = tp.property("ADBE Text Animators");
        if (!anims) { logLine("WARN: no Text Animators group on layer: " + textLayer.name); return; }

        // Deterministic: do not accumulate animators across runs
        clearTextAnimators(textLayer);

        for (var i = 0; i < animatorsConf.length; i++) {
            var aConf = animatorsConf[i];
            if (!aConf) continue;

            var animator = null;
            try { animator = anims.addProperty("ADBE Text Animator"); } catch (eAddA) { animator = null; }
            if (!animator) { logLine("WARN: cannot add Text Animator on " + textLayer.name); continue; }
            if (aConf.name) animator.name = aConf.name;

            // Animator properties: dict {matchName: valueData}
            var aProps = animator.property("ADBE Text Animator Properties");
            var props = aConf.properties || {};
            for (var pKey in props) {
                if (!props.hasOwnProperty(pKey)) continue;
                var aeP = null;
                try { aeP = aProps.addProperty(pKey); } catch (eAddP) { aeP = null; }
                if (!aeP) { logLine("WARN: animator prop add failed: " + pKey); continue; }
                setPropValue(aeP, props[pKey], textLayer);
            }

            // Selectors
            var sels = animator.property("ADBE Text Selectors");
            var selList = aConf.selectors || [];
            for (var s = 0; s < selList.length; s++) {
                var sConf = selList[s];
                if (!sConf) continue;

                var sel = null;
                var selMatch = sConf.matchName || "ADBE Text Selector";
                try { sel = sels.addProperty(selMatch); } catch (eSel) { sel = null; }
                if (!sel) { logLine("WARN: selector add failed: " + selMatch); continue; }
                if (sConf.name) sel.name = sConf.name;

                // selector basic props: dict {matchName: valueData}
                var sp = sConf.properties || {};
                for (var spKey in sp) {
                    if (!sp.hasOwnProperty(spKey)) continue;
                    var pr = resolvePropByPath(sel, spKey);
                    if (!pr) { logLine("SEL PROP MISSING: " + spKey); continue; }
                    setPropValue(pr, sp[spKey], textLayer);
                }

                // advanced props: dict {matchName: valueData}
                var adv = sConf.advanced || {};
                if (adv && typeof adv === "object") {
                    var advGroup = null;
                    try { advGroup = sel.property("ADBE Text Selector Advanced"); } catch (eA1) { advGroup = null; }
                    if (!advGroup) {
                        try { advGroup = sel.property("ADBE Text Range Advanced"); } catch (eA2) { advGroup = null; }
                    }
                    if (!advGroup) advGroup = sel;

                    for (var aKey in adv) {
                        if (!adv.hasOwnProperty(aKey)) continue;
                        var ap = resolvePropByPath(advGroup, aKey);
                        if (!ap && advGroup !== sel) {
                            ap = resolvePropByPath(sel, aKey);
                        }
                        if (!ap) { logLine("ADV PROP MISSING: " + aKey); continue; }
                        setPropValue(ap, adv[aKey], textLayer);
                    }
                }
            }
        }
    }

    function setupGeneralLayer(layer, config) {
        if (config.name) layer.name = config.name;
        else if (config.textDocument && config.textDocument.text) {
            layer.name = config.textDocument.text.replace(/\r/g, " ").substring(0, 15);
        }

        if (config.startTime !== undefined) layer.startTime = config.startTime;
        if (config.inPoint   !== undefined) layer.inPoint   = config.inPoint;
        if (config.outPoint  !== undefined) layer.outPoint  = config.outPoint;

        if (config.enabled      !== undefined) layer.enabled      = config.enabled;
        if (config.audioEnabled !== undefined && layer.hasAudio) layer.audioEnabled = config.audioEnabled;

        if (config.type === "adjustment") layer.adjustmentLayer = true;

        if (config.threeDLayer === true || config.threeD === true) {
            try { layer.threeDLayer = true; } catch (e3d1) {}
        } else if (config.threeDLayer === false || config.threeD === false) {
            try { layer.threeDLayer = false; } catch (e3d2) {}
        }

        if (config.transform) {
            var tr = config.transform;

            // Если применяем fitPolicy (cover/contain), scale не трогаем —
            // auto-fit в applyFitPolicy уже выставит нужный размер.
            if (tr.scale && !config.fitPolicy) {
                setPropValue(layer.transform.scale, tr.scale, layer);
            }
            if (tr.position) setPropValue(layer.transform.position, tr.position, layer);
            if (tr.rotation) setPropValue(layer.transform.rotation, tr.rotation, layer);
            if (tr.opacity)  setPropValue(layer.transform.opacity,  tr.opacity, layer);
        }

        if (config.effects) {
            applyEffects(layer, config.effects);
        }

        // Text animators (baked by assembler from text_fx_combos.json)
        if (config.type === "text" && config.textAnimators && config.textAnimators.length) {
            applyTextAnimators(layer, config.textAnimators);
        }
    }

    function applyFitPolicy(layer, comp, src, conf) {
        if (!conf || !conf.fitPolicy) return;

        if (conf.fitPolicy === "cover" && src && src.width && src.height) {
            var sx = comp.width  / src.width  * 100;
            var sy = comp.height / src.height * 100;
            var s  = (sx > sy ? sx : sy); // cover: масштаб по минимальной стороне
            layer.property("Scale").setValue([s, s]);
        }
        // 'contain' можно добавить позже
    }

    function buildProject() {
        var itemsList = [];
        if (PROJECT_DATA.project && PROJECT_DATA.project.items) itemsList = PROJECT_DATA.project.items;
        else if (PROJECT_DATA.items) itemsList = PROJECT_DATA.items;

        logLine("PROJECT_DATA items count = " + itemsList.length);

        // STEP 1: create items
        for (var i = 0; i < itemsList.length; i++) {
            var conf = itemsList[i];
            if (conf.type === "footage") {
                var itemF = importFootage(conf);
                if (itemF) itemRegistry[conf.id] = itemF;
            } else if (conf.type === "comp") {
                var itemC = createComp(conf);
                if (itemC) itemRegistry[conf.id] = itemC;
            }
        }

        // STEP 2: create layers inside comps
        for (var ii = 0; ii < itemsList.length; ii++) {
            var compConf = itemsList[ii];
            if (compConf.type !== "comp" || !compConf.layers) continue;

            var comp = itemRegistry[compConf.id];
            if (!comp) continue;

            logLine("Populate comp: " + compConf.id + " with " + compConf.layers.length + " layers");

            function _sameWindow(a, b) {
                if (!a || !b) return false;
                if (a.inPoint === undefined || b.inPoint === undefined) return false;
                if (a.outPoint === undefined || b.outPoint === undefined) return false;
                return (Math.abs(a.inPoint - b.inPoint) < 1e-6) && (Math.abs(a.outPoint - b.outPoint) < 1e-6);
            }

            // Build "units": either [base, adjustment] or [single]
            function _normalizeLayerUnits(layers, compId) {
                var units = [];
                var i = 0;
                while (i < layers.length) {
                    var cur = layers[i];
                    var nxt = (i + 1 < layers.length) ? layers[i + 1] : null;

                    // Prefer pairing by equal window + one is adjustment
                    if (cur && nxt && (cur.type === "adjustment" || nxt.type === "adjustment") && _sameWindow(cur, nxt)) {
                        // ensure order base -> adjustment
                        if (cur.type === "adjustment" && nxt.type !== "adjustment") {
                            units.push([nxt, cur]);
                        } else if (cur.type !== "adjustment" && nxt.type === "adjustment") {
                            units.push([cur, nxt]);
                        } else {
                            // two adjustments or two bases; keep as singles
                            units.push([cur]);
                            i += 1;
                            continue;
                        }
                        i += 2;
                        continue;
                    }

                    units.push([cur]);
                    i += 1;
                }

                // Sort units by base inPoint (stable-ish)
                units.sort(function (ua, ub) {
                    var a = ua[0] || {};
                    var b = ub[0] || {};
                    var ta = (a.inPoint !== undefined) ? a.inPoint : 0.0;
                    var tb = (b.inPoint !== undefined) ? b.inPoint : 0.0;
                    return ta - tb;
                });

                // In comp_main: keep Audio Ref at very bottom, Text Overlay at very top
                if (compId === "comp_main") {
                    var audioUnits = [];
                    var overlayUnits = [];
                    var otherUnits = [];
                    for (var k = 0; k < units.length; k++) {
                        var u = units[k];
                        var base = u[0] || {};
                        if (base.type === "ref" && (base.refId === "audio_main" || base.audioEnabled === true)) {
                            audioUnits.push(u);
                        } else if (base.type === "ref" && base.refId === "comp_text") {
                            overlayUnits.push(u);
                        } else {
                            otherUnits.push(u);
                        }
                    }
                    units = audioUnits.concat(otherUnits).concat(overlayUnits);
                }

                return units;
            }

            function _createLayerFromConf(lConf) {
                var layer = null;
                if (lConf.type === "ref") {
                    var src = itemRegistry[lConf.refId];
                    if (src) {
                        layer = comp.layers.add(src);
                        applyFitPolicy(layer, comp, src, lConf);
                    } else {
                        layer = comp.layers.addNull();
                        layer.name = "MISSING: " + lConf.refId;
                    }
                } else if (lConf.type === "text") {
                    var txtContent = (lConf.textDocument && lConf.textDocument.text)
                        ? lConf.textDocument.text
                        : "Text";
                    layer = comp.layers.addText(txtContent);
                    if (lConf.textDocument) {
                        applyTextSettings(layer, lConf.textDocument);
                    }
                    layer.position.setValue([comp.width / 2, comp.height / 2]);
                } else if (lConf.type === "adjustment") {
                    layer = comp.layers.addSolid([1, 1, 1], lConf.name || "Adj Layer", comp.width, comp.height, 1);
                    try { layer.source.parentFolder = fSolids; } catch (eSol) {}
                }
                return layer;
            }

            // Normalize pairs/order FIRST, then create in forward order.
            // Forward order works well with AE "add to top": last created ends up on top.
            var units = _normalizeLayerUnits(compConf.layers, compConf.id);
            for (var ui = 0; ui < units.length; ui++) {
                var unit = units[ui];
                for (var li = 0; li < unit.length; li++) {
                    var conf = unit[li];
                    var layer = _createLayerFromConf(conf);
                    if (layer) setupGeneralLayer(layer, conf);
                }
            }
        }

        // STEP 3: entry comp
        var entryComp = null;
        if (PROJECT_DATA.entryPoint) {
            entryComp = itemRegistry[PROJECT_DATA.entryPoint];
        } else if (COMP_NAME) {
            entryComp = itemRegistry[COMP_NAME];
        } else {
            entryComp = itemRegistry["comp_main"];
        }

        if (!entryComp) {
            var entryName = PROJECT_DATA.entryPoint || COMP_NAME || "comp_main";
            throw new Error("Entry comp not found: " + entryName);
        }

        logLine("Entry comp: " + entryComp.name);
        // openInViewer можно включить, если тебе удобно:
        // entryComp.openInViewer();

        return entryComp;
    }

    function saveProject(entryComp) {
        if (!APP_DIR) {
            throw new Error("APP_DIR is empty, cannot save AEP");
        }

        var projName = "debug_" + (JOB_ID || "project") + ".aep";
        var projFile = new File(APP_DIR + "/" + projName);

        if (projFile.parent && !projFile.parent.exists) {
            projFile.parent.create();
        }

        app.project.save(projFile);
        logLine("Project saved: " + projFile.fsName);
        return projFile;
    }

    // ==========================================
    // 3. MAIN
    // ==========================================
    var projFile = null;
    var entryComp = null;

    try {
        entryComp = buildProject();
        projFile = saveProject(entryComp);

        var msgLines = [];
        if (projFile) msgLines.push("aep=" + projFile.fsName);
        if (entryComp) msgLines.push("compName=" + entryComp.name);
        var msg = msgLines.join("\n");

        logLine("JOB END (success)");
        writeStatus("OK", msg);
    } catch (err) {
        var errMsg = (err && err.toString ? err.toString() : String(err));
        logLine("JOB ERROR: " + errMsg);
        writeStatus("ERROR", errMsg);
    } finally {
        // закрываем ПРОЕКТ, но не приложение After Effects
        try {
            if (app.project && typeof CloseOptions !== "undefined") {
                app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);
            } else if (app.project) {
                app.project.close();
            }
        } catch (eClose) {
            logLine("Project close error: " + eClose.toString());
        }
        closeLog();
    }
})();
