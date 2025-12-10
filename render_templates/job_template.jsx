// render_templates/job_template.jsx
(function () {
    // ==========================================
    // 1. DATA INJECTION ZONE
    // ==========================================
    // Python заменит эту строку на var PROJECT_DATA = { ... };
    /*__PYTHON_DATA_INJECT__*/

    var APP_DIR    = $.getenv("APP_DIR")    || "";
    var OUTPUT_REL = $.getenv("OUTPUT_REL") || "work/output.mp4";
    var JOB_ID     = $.getenv("JOB_ID")     || "";

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
        if (typeof valueData === "object" && valueData.keys && valueData.keys.length > 0) {
            if (aeProp.canSetExpression) aeProp.expression = "";
            for (var i = 0; i < valueData.keys.length; i++) {
                var k = valueData.keys[i];
                aeProp.setValueAtTime(k.time, k.value);
            }
        } else {
            aeProp.setValue(valueData);
        }
    }

    function importFootage(conf) {
        var file = new File(conf.path);
        var item = null;

        if (!file.exists) {
            item = app.project.importPlaceholder(conf.name || "missing_file", 1920, 1080, 24, 10);
        } else {
            try {
                var io = new ImportOptions(file);
                if (io.canImportAs(ImportAsType.FOOTAGE)) {
                    item = app.project.importFile(io);
                }
            } catch (e) {
                alert("Import Error: " + conf.path + "\n" + e.toString());
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
            // auto-fit в applyFitPolicy уже выставил нужный размер.
            if (tr.scale && !config.fitPolicy) {
                setPropValue(layer.transform.scale, tr.scale);
            }
            if (tr.position) setPropValue(layer.transform.position, tr.position);
            if (tr.rotation) setPropValue(layer.transform.rotation, tr.rotation);
            if (tr.opacity)  setPropValue(layer.transform.opacity,  tr.opacity);
        }
    }

    function applyFitPolicy(layer, comp, src, conf) {
        if (!conf || !conf.fitPolicy) return;

        if (conf.fitPolicy === "cover" && src && src.width && src.height) {
            var sx = comp.width  / src.width  * 100;
            var sy = comp.height / src.height * 100;
            var s  = (sx > sy ? sx : sy);
            layer.property("Scale").setValue([s, s]);
        }
        // 'contain' можно добавить позже
    }

    // ==========================================
    // 3. PIPELINE EXECUTION
    // ==========================================
    var itemsList = [];
    if (PROJECT_DATA.project && PROJECT_DATA.project.items) itemsList = PROJECT_DATA.project.items;
    else if (PROJECT_DATA.items) itemsList = PROJECT_DATA.items;

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

    // STEP 3: open entry comp, prepare Render Queue and save .aep
    var entryComp = null;
    if (PROJECT_DATA.entryPoint) {
        entryComp = itemRegistry[PROJECT_DATA.entryPoint];
    } else {
        entryComp = itemRegistry["comp_main"];
    }
    if (entryComp) {
        entryComp.openInViewer();
    }

    if (entryComp) {
        var outPath = OUTPUT_REL || "work/output.mp4";
        var outFile = null;
        if (APP_DIR) {
            var sep = (APP_DIR.slice(-1) === "/" || APP_DIR.slice(-1) === "\\") ? "" : "/";
            outFile = new File(APP_DIR + sep + outPath);
        } else {
            outFile = new File(outPath);
        }

        if (outFile && outFile.parent && !outFile.parent.exists) {
            outFile.parent.create();
        }

        var rqItem = app.project.renderQueue.items.add(entryComp);
        try { rqItem.applyTemplate("Best Settings"); } catch (eBest) {}
        var om = rqItem.outputModule(1);
        try { om.applyTemplate("H.264"); } catch (eOM) {}
        om.file = outFile;

        // save .aep for debug / последующего aerender
        if (!APP_DIR) {
            alert("APP_DIR is empty, cannot save AEP.");
            throw new Error("APP_DIR is empty, cannot save AEP.");
        }

        var projFile = new File(APP_DIR + "/debug_" + (JOB_ID || "project") + ".aep");
        if (!projFile.parent.exists) {
            projFile.parent.create();
        }

        try {
            app.project.save(projFile);
        } catch (eSave) {
            alert("Failed to save project:\n" + eSave.toString());
            throw eSave; // важно: пусть процесс упадёт, а ae_sdk увидит ошибку
        }

        try {
            if (app.project && typeof CloseOptions !== "undefined") {
                app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);
            } else if (app.project) {
                app.project.close();
            }
        } catch (eClose) {}
    }
})();
