// render_templates/job_template.jsx
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

    function setPropValue(aeProp, valueData) {
        if (valueData === undefined || valueData === null) return;

        var isObj = (typeof valueData === "object") && !Array.isArray(valueData);

        if (isObj && valueData.expression !== undefined && aeProp.canSetExpression) {
            try {
                aeProp.expression = valueData.expression || "";
            } catch (eExpr) {}
        }

        if (isObj && valueData.keys && valueData.keys.length > 0) {
            if (aeProp.canSetExpression) aeProp.expression = valueData.expression || "";
            for (var i = 0; i < valueData.keys.length; i++) {
                var k = valueData.keys[i];
                if (k.time === undefined || k.value === undefined) continue;
                aeProp.setValueAtTime(k.time, k.value);
            }
            return;
        }

        if (isObj && valueData.value !== undefined) {
            aeProp.setValue(valueData.value);
            return;
        }

        aeProp.setValue(valueData);
    }

    function applyEffects(layer, effectsConf) {
        if (!effectsConf || !effectsConf.length) return;
        var parade = layer.property("ADBE Effect Parade");
        if (!parade) return;

        for (var i = 0; i < effectsConf.length; i++) {
            var fxConf = effectsConf[i];
            if (!fxConf || !fxConf.matchName) continue;
            var fx = null;
            try {
                fx = parade.addProperty(fxConf.matchName);
            } catch (eAdd) {
                continue;
            }
            if (!fx || !fxConf.params) continue;
            for (var key in fxConf.params) {
                if (!fxConf.params.hasOwnProperty(key)) continue;
                var p = fx.property(key);
                if (p) setPropValue(p, fxConf.params[key]);
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

        if (config.startTime !== undefined) layer.startTime = config.startTime;
        if (config.inPoint   !== undefined) layer.inPoint   = config.inPoint;
        if (config.outPoint  !== undefined) layer.outPoint  = config.outPoint;

        if (config.enabled      !== undefined) layer.enabled      = config.enabled;
        if (config.audioEnabled !== undefined && layer.hasAudio) layer.audioEnabled = config.audioEnabled;

        if (config.type === "adjustment") layer.adjustmentLayer = true;

        if (config.transform) {
            var tr = config.transform;

            // Если применяем fitPolicy (cover/contain), scale не трогаем —
            // auto-fit в applyFitPolicy уже выставит нужный размер.
            if (tr.scale && !config.fitPolicy) {
                setPropValue(layer.transform.scale, tr.scale);
            }
            if (tr.position) setPropValue(layer.transform.position, tr.position);
            if (tr.rotation) setPropValue(layer.transform.rotation, tr.rotation);
            if (tr.opacity)  setPropValue(layer.transform.opacity,  tr.opacity);
        }

        if (config.effects) {
            applyEffects(layer, config.effects);
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
