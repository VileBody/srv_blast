// render_templates/template_engine.jsx
// v2 engine:
//  - applies keyTemplates via templateRef on keyframes
//  - supports matchName-based property trees (transformTree / textAnimTree)
(function () {
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

    function initLog() {
        if (!APP_DIR) return;
        try {
            LOG_FILE = new File(APP_DIR + "/ae_job_log");
            LOG_FILE.encoding = "UTF-8";
            if (LOG_FILE.exists) {
                LOG_FILE.remove();
            }
            LOG_FILE.open("w", "TEXT", "????");
            LOG_FILE.lineFeed = "Unix";
        } catch (e) {
            LOG_FILE = null;
        }
    }

    function logLine(msg) {
        var prefix = "";
        try {
            prefix = (new Date()).toUTCString() + " ";
        } catch (e) {}
        var line = prefix + msg;

        if (LOG_FILE) {
            try {
                LOG_FILE.writeln(line);
                LOG_FILE.flush();
            } catch (e1) {}
        }
        try {
            $.writeln(line);
        } catch (e2) {}
    }

    function closeLog() {
        try {
            if (LOG_FILE && LOG_FILE.opened) {
                LOG_FILE.close();
            }
        } catch (e) {}
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

    var KEY_TEMPLATES = (PROJECT_DATA.libraries && PROJECT_DATA.libraries.keyTemplates)
        ? PROJECT_DATA.libraries.keyTemplates
        : {};

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

    // -----------------------------
    // Keyframe template helpers
    // -----------------------------
    function _cloneArray(arr) {
        var out = [];
        for (var i = 0; i < arr.length; i++) out.push(arr[i]);
        return out;
    }

    function _normalizeEaseArray(eases, dims) {
        if (!eases || eases.length === 0) return null;
        var out = [];
        for (var i = 0; i < dims; i++) {
            var src = eases[Math.min(i, eases.length - 1)];
            out.push(new KeyframeEase(src.speed, src.influence));
        }
        return out;
    }

    function _findKeyIndexAtTime(aeProp, t) {
        var eps = 1e-4;
        try {
            for (var i = 1; i <= aeProp.numKeys; i++) {
                if (Math.abs(aeProp.keyTime(i) - t) < eps) return i;
            }
            return aeProp.nearestKeyIndex(t);
        } catch (e) {
            return -1;
        }
    }

    function _pickKeyAttr(k, tpl, fieldName) {
        if (k && k[fieldName] !== undefined) return k[fieldName];
        if (tpl && tpl[fieldName] !== undefined) return tpl[fieldName];
        return undefined;
    }

    function _applyKeyAttributes(aeProp, keyIndex, k) {
        if (!aeProp || keyIndex < 1) return;

        var tpl = null;
        if (KEY_TEMPLATES && k && k.templateRef && KEY_TEMPLATES[k.templateRef]) {
            tpl = KEY_TEMPLATES[k.templateRef];
        }

        // Interpolation
        var inType = _pickKeyAttr(k, tpl, "inInterpolationType");
        var outType = _pickKeyAttr(k, tpl, "outInterpolationType");
        if (inType !== undefined && outType !== undefined) {
            try { aeProp.setInterpolationTypeAtKey(keyIndex, inType, outType); } catch (e0) {}
        }

        // Temporal ease (needs per-dimension arrays)
        var inEaseSpec = _pickKeyAttr(k, tpl, "inTemporalEase");
        var outEaseSpec = _pickKeyAttr(k, tpl, "outTemporalEase");
        if (inEaseSpec || outEaseSpec) {
            var dims = 1;
            try {
                var v = aeProp.value;
                if (v instanceof Array) dims = v.length;
            } catch (e1) { dims = 1; }

            var inEaseArr = _normalizeEaseArray(inEaseSpec || outEaseSpec, dims);
            var outEaseArr = _normalizeEaseArray(outEaseSpec || inEaseSpec, dims);
            if (inEaseArr && outEaseArr) {
                try { aeProp.setTemporalEaseAtKey(keyIndex, inEaseArr, outEaseArr); } catch (e2) {}
            }
        }

        // Flags
        var autoB = _pickKeyAttr(k, tpl, "temporalAutoBezier");
        if (autoB !== undefined) {
            try { aeProp.setTemporalAutoBezierAtKey(keyIndex, autoB); } catch (e3) {}
        }
        var cont = _pickKeyAttr(k, tpl, "temporalContinuous");
        if (cont !== undefined) {
            try { aeProp.setTemporalContinuousAtKey(keyIndex, cont); } catch (e4) {}
        }
    }

    function setPropValue(aeProp, valueData) {
        try {
            if (!aeProp) return;

            // Expression (optional)
            if (valueData && typeof valueData === "object" && !(valueData instanceof Array) && valueData.expression !== undefined) {
                try {
                    aeProp.expression = valueData.expression;
                    aeProp.expressionEnabled = true;
                } catch (eExpr) {}
                return;
            } else {
                // If we are setting actual values/keys, disable expressions to avoid surprises.
                try {
                    aeProp.expression = "";
                    aeProp.expressionEnabled = false;
                } catch (eNoExpr) {}
            }

            // Keyframes: {"keys":[{time,value,templateRef?...}, ...]}
            if (valueData && typeof valueData === "object" && !(valueData instanceof Array) && valueData.keys && valueData.keys.length) {
                var keys = _cloneArray(valueData.keys);
                keys.sort(function (a, b) { return a.time - b.time; });

                for (var i = 0; i < keys.length; i++) {
                    var k = keys[i];
                    if (k.time === undefined) continue;

                    aeProp.setValueAtTime(k.time, k.value);

                    var idx = _findKeyIndexAtTime(aeProp, k.time);
                    _applyKeyAttributes(aeProp, idx, k);
                }
                return;
            }

            // Scalar / array / {"value": ...}
            if (valueData && typeof valueData === "object" && !(valueData instanceof Array) && valueData.value !== undefined) {
                aeProp.setValue(valueData.value);
                return;
            }

            aeProp.setValue(valueData);
        } catch (e) {
            logLine("Error setting property " + (aeProp ? aeProp.matchName : "<null>") + ": " + e.toString());
        }
    }

    // -----------------------------
    // Generic matchName property tree applier
    // -----------------------------
    function _getTextAnimatorsRoot(layer) {
        try {
            var tg = null;
            try { tg = layer.property("Text"); } catch (e1) {}
            if (!tg) {
                try { tg = layer.property("ADBE Text Properties"); } catch (e2) {}
            }
            if (!tg) return null;
            return tg.property("ADBE Text Animators");
        } catch (e) {
            return null;
        }
    }

    function _getTreeRootOnLayer(layer, treeMatchName) {
        try {
            if (treeMatchName === "ADBE Text Animators") {
                return _getTextAnimatorsRoot(layer);
            }
            return layer.property(treeMatchName);
        } catch (e) {
            return null;
        }
    }

    function _applyPropertyTreeNode(aeGroup, node) {
        if (!aeGroup || !node) return;

        // Apply properties
        if (node.properties) {
            for (var propName in node.properties) {
                if (!node.properties.hasOwnProperty(propName)) continue;
                var v = node.properties[propName];

                var aeProp = null;
                try {
                    if (aeGroup.canAddProperty && aeGroup.canAddProperty(propName)) {
                        aeProp = aeGroup.addProperty(propName);
                    } else {
                        aeProp = aeGroup.property(propName);
                    }
                } catch (e0) { aeProp = null; }

                if (aeProp) {
                    setPropValue(aeProp, v);
                } else {
                    logLine("applyPropertyTree: missing prop '" + propName + "' under '" + aeGroup.matchName + "'");
                }
            }
        }

        // Recurse into children
        if (node.children) {
            for (var i = 0; i < node.children.length; i++) {
                var ch = node.children[i];
                if (!ch || !ch.matchName) continue;

                var aeChild = null;
                try {
                    if (aeGroup.canAddProperty && aeGroup.canAddProperty(ch.matchName)) {
                        aeChild = aeGroup.addProperty(ch.matchName);
                    } else {
                        aeChild = aeGroup.property(ch.matchName);
                    }
                } catch (e1) { aeChild = null; }

                if (aeChild) {
                    _applyPropertyTreeNode(aeChild, ch);
                } else {
                    logLine("applyPropertyTree: missing child '" + ch.matchName + "' under '" + aeGroup.matchName + "'");
                }
            }
        }
    }

    function applyPropertyTreeOnLayer(layer, tree) {
        if (!layer || !tree || !tree.matchName) return;

        var root = _getTreeRootOnLayer(layer, tree.matchName);
        if (!root) {
            logLine("applyPropertyTree: root not found: " + tree.matchName);
            return;
        }

        _applyPropertyTreeNode(root, tree);
    }

    // -----------------------------
    // Text document applier
    // -----------------------------
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
        } catch (eFont) {}
        if (!fontSet) {
            try { textDocument.font = "Calibri"; } catch (eFb) {}
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

    function setupGeneralLayer(layer, config) {
        if (config.name) layer.name = config.name;
        else if (config.textDocument && config.textDocument.text) {
            layer.name = config.textDocument.text.replace(/\r/g, " ").substring(0, 15);
        }

        if (config.startTime !== undefined && config.startTime !== null) layer.startTime = config.startTime;
        if (config.inPoint   !== undefined && config.inPoint !== null)   layer.inPoint   = config.inPoint;
        if (config.outPoint  !== undefined && config.outPoint !== null)  layer.outPoint  = config.outPoint;

        if (config.enabled      !== undefined && config.enabled !== null) layer.enabled      = config.enabled;
        if (config.audioEnabled !== undefined && config.audioEnabled !== null && layer.hasAudio) layer.audioEnabled = config.audioEnabled;

        if (config.type === "adjustment") layer.adjustmentLayer = true;

        // Apply transform (priority: full-fidelity transformTree > legacy transform dict)
        if (config.transformTree) {
            applyPropertyTreeOnLayer(layer, config.transformTree);
        } else if (config.transform) {
            var tr = config.transform;

            // Если применяем fitPolicy (cover/contain), scale не трогаем —
            // auto-fit в applyFitPolicy уже выставит нужный размер.
            if (tr.scale && !config.fitPolicy) {
                setPropValue(layer.transform.scale, tr.scale);
            }
            if (tr.position) setPropValue(layer.transform.position, tr.position);
            if (tr.rotation) setPropValue(layer.transform.rotation, tr.rotation);
            if (tr.opacity !== undefined) setPropValue(layer.transform.opacity, tr.opacity);
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

    function importFootage(config) {
        try {
            var file = new File(config.path);
            if (!file.exists) throw new Error("File missing: " + config.path);

            var io = new ImportOptions(file);
            var footage = app.project.importFile(io);
            footage.name = config.name || file.name;
            footage.parentFolder = config.isRef ? fRef : fFootage;
            return footage;
        } catch (e) {
            logLine("Import footage error: " + e.toString());
            return null;
        }
    }

    function createComp(config) {
        var comp = app.project.items.addComp(
            config.name || config.id,
            config.width || 1080,
            config.height || 1920,
            config.pixelAspect || 1.0,
            config.duration || 10,
            config.fps || 24
        );
        comp.parentFolder = fComps;
        return comp;
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

            for (var j = 0; j < compConf.layers.length; j++) {
                var lConf = compConf.layers[j];
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
                    // text animators (full fidelity)
                    if (lConf.textAnimTree) {
                        applyPropertyTreeOnLayer(layer, lConf.textAnimTree);
                    }
                    // ensure a predictable base position (can be overridden by transformTree/transform)
                    layer.position.setValue([comp.width / 2, comp.height / 2]);
                } else if (lConf.type === "adjustment") {
                    layer = comp.layers.addSolid([1, 1, 1], lConf.name || "Adj Layer", comp.width, comp.height, 1);
                    layer.source.parentFolder = fSolids;
                }

                if (layer) {
                    setupGeneralLayer(layer, lConf);
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
