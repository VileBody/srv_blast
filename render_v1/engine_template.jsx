(function () {
    // ==========================================
    // 1. DATA INJECTION ZONE
    // ==========================================
    // Python заменит эту строку на переменную var PROJECT_DATA = { ... };
    /*__PYTHON_DATA_INJECT__*/

    // Значения из окружения, которые пробрасывает внешняя нода:
    //   APP_DIR    — корень app/ для конкретной джобы
    //   OUTPUT_REL — относительный путь итогового файла внутри app/
    //   JOB_ID     — идентификатор джобы (опционально)
    var APP_DIR    = $.getenv("APP_DIR")    || "";
    var OUTPUT_REL = $.getenv("OUTPUT_REL") || "work/output.mp4";
    var JOB_ID     = $.getenv("JOB_ID")     || "";

    // ==========================================
    // 2. ENGINE CORE
    // ==========================================
    var itemRegistry = {}; 
    
    // --- ПАПКИ (Для организации проекта) ---
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

    // --- УНИВЕРСАЛЬНЫЙ СЕТТЕР СВОЙСТВ (ЗНАЧЕНИЯ И КЛЮЧИ) ---
    function setPropValue(aeProp, valueData) {
        if (valueData === undefined || valueData === null) return;

        // Если это объект с ключами (Keyframes)
        if (typeof valueData === "object" && valueData.keys && valueData.keys.length > 0) {
            if (aeProp.canSetExpression) aeProp.expression = ""; // Сброс экспрешна если был
            for (var i = 0; i < valueData.keys.length; i++) {
                var k = valueData.keys[i];
                aeProp.setValueAtTime(k.time, k.value);
            }
        } 
        // Если это просто значение (Число или Массив [x,y])
        else {
            aeProp.setValue(valueData);
        }
    }

    // --- ИМПОРТ ФАЙЛОВ ---
    function importFootage(conf) {
        var file = new File(conf.path);
        var item = null;
        
        // Если файла нет — создаем цветную заглушку
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

        // Раскладываем по папкам
        if (item) {
            if (conf.isRef) item.parentFolder = fRef;
            else item.parentFolder = fFootage;
        }
        return item;
    }

    // --- СОЗДАНИЕ КОМПОЗИЦИИ ---
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

    // --- НАСТРОЙКА ТЕКСТА (STYLE APPLIER) ---
    function applyTextSettings(textLayer, textDocConfig) {
        if (!textDocConfig) return;
        var textProp     = textLayer.property("Source Text");
        var textDocument = textProp.value;

        // 1. Контент
        if (textDocConfig.text) textDocument.text = textDocConfig.text;
        
        // 2. Шрифт и Размер
        var fontSet = false;
        try {
            if (textDocConfig.font) {
                textDocument.font = textDocConfig.font;
                fontSet = true;
            }
        } catch (eFont) {
            // шрифт не найден — попробуем Calibri
        }
        if (!fontSet) {
            try {
                textDocument.font = "Calibri";
            } catch (eFallback) {
                // если даже Calibri нет — оставляем дефолт AE
            }
        }
        
        if (textDocConfig.fontSize)  textDocument.fontSize  = textDocConfig.fontSize;
        if (textDocConfig.tracking)  textDocument.tracking  = textDocConfig.tracking;
        if (textDocConfig.leading)   textDocument.leading   = textDocConfig.leading;

        // 3. Выравнивание (Justification)
        if (textDocConfig.justification !== undefined) {
            var j = textDocConfig.justification;
            if (j === 1 || j === 7415 || j === "CENTER") textDocument.justification = ParagraphJustification.CENTER_JUSTIFY;
            else if (j === 0 || j === 7413 || j === "LEFT")  textDocument.justification = ParagraphJustification.LEFT_JUSTIFY;
            else if (j === 2 || j === 7414 || j === "RIGHT") textDocument.justification = ParagraphJustification.RIGHT_JUSTIFY;
        }

        // 4. Цвета и Обводка
        if (textDocConfig.fillColor)   textDocument.fillColor   = textDocConfig.fillColor;
        if (textDocConfig.strokeColor) textDocument.strokeColor = textDocConfig.strokeColor;
        if (textDocConfig.strokeWidth) textDocument.strokeWidth = textDocConfig.strokeWidth;

        // 5. Флаги
        if (textDocConfig.applyFill === true)       textDocument.applyFill = true;
        else if (textDocConfig.applyFill === false) textDocument.applyFill = false;
        else if (textDocConfig.fillColor)           textDocument.applyFill = true;

        if (textDocConfig.applyStroke === true)       textDocument.applyStroke = true;
        else if (textDocConfig.applyStroke === false) textDocument.applyStroke = false;
        else if (textDocConfig.strokeColor || textDocConfig.strokeWidth) textDocument.applyStroke = true;

        textProp.setValue(textDocument);
    }

    // --- ОБЩАЯ НАСТРОЙКА СЛОЯ ---
    function setupGeneralLayer(layer, config) {
        // Имя слоя
        if (config.name) layer.name = config.name;
        else if (config.textDocument && config.textDocument.text) {
            layer.name = config.textDocument.text.replace(/\r/g, " ").substring(0, 15);
        }
        
        // Тайминг
        if (config.startTime !== undefined) layer.startTime = config.startTime;
        if (config.inPoint   !== undefined) layer.inPoint   = config.inPoint;
        if (config.outPoint  !== undefined) layer.outPoint  = config.outPoint;
        
        // Видимость и Звук
        if (config.enabled      !== undefined) layer.enabled      = config.enabled;
        if (config.audioEnabled !== undefined && layer.hasAudio) layer.audioEnabled = config.audioEnabled;
        
        // Тип слоя
        if (config.type === "adjustment") layer.adjustmentLayer = true;

        // Трансформации
        if (config.transform) {
            var tr = config.transform;
            if (tr.scale)    setPropValue(layer.transform.scale,    tr.scale);
            if (tr.position) setPropValue(layer.transform.position, tr.position);
            if (tr.rotation) setPropValue(layer.transform.rotation, tr.rotation);
            if (tr.opacity)  setPropValue(layer.transform.opacity,  tr.opacity);
        }
    }

    // ==========================================
    // 3. PIPELINE EXECUTION
    // ==========================================

    var itemsList = [];
    if (PROJECT_DATA.project && PROJECT_DATA.project.items) itemsList = PROJECT_DATA.project.items;
    else if (PROJECT_DATA.items) itemsList = PROJECT_DATA.items;

    // ШАГ 1: создаём items (файлы и композиции)
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

    // ШАГ 2: создаём слои внутри композиций
    for (var ii = 0; ii < itemsList.length; ii++) {
        var compConf = itemsList[ii];
        
        if (compConf.type === "comp" && compConf.layers) {
            var comp = itemRegistry[compConf.id];
            if (!comp) continue;

            for (var j = 0; j < compConf.layers.length; j++) {
                var lConf = compConf.layers[j];
                var layer = null;

                if (lConf.type === "ref") {
                    var src = itemRegistry[lConf.refId];
                    if (src) {
                        layer = comp.layers.add(src);
                    } else {
                        layer = comp.layers.addNull();
                        layer.name = "MISSING: " + lConf.refId;
                    }
                } 
                else if (lConf.type === "text") {
                    var txtContent = (lConf.textDocument && lConf.textDocument.text) ? lConf.textDocument.text : "Text";
                    layer = comp.layers.addText(txtContent);
                    if (lConf.textDocument) {
                        applyTextSettings(layer, lConf.textDocument);
                    }
                    layer.position.setValue([comp.width / 2, comp.height / 2]);
                } 
                else if (lConf.type === "adjustment") {
                    layer = comp.layers.addSolid([1,1,1], lConf.name || "Adj Layer", comp.width, comp.height, 1);
                    layer.source.parentFolder = fSolids;
                }

                if (layer) {
                    setupGeneralLayer(layer, lConf);
                }
            }
        }
    }

    // ШАГ 3: Открываем entry-комп
    var entryComp = null;
    if (PROJECT_DATA.entryPoint) {
        entryComp = itemRegistry[PROJECT_DATA.entryPoint];
        if (entryComp) {
            entryComp.openInViewer();
            entryComp.openInViewer();
        }
    } else {
        entryComp = itemRegistry["comp_main"];
        if (entryComp) entryComp.openInViewer();
    }

    // ШАГ 4: Сохраняем проект и рендерим
    if (entryComp) {
        // 4.1. Сохраняем проект для дебага
        if (APP_DIR) {
            try {
                var baseName = JOB_ID || "project";
                var projFile = new File(APP_DIR + "/debug_" + baseName + ".aep");
                app.project.save(projFile);
            } catch (eSave) {
                // сохранять не обязательно для пайплайна, так что не падаем
            }
        }

        // 4.2. Готовим выходной файл
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

        // 4.3. Render Queue
        var rqItem = app.project.renderQueue.items.add(entryComp);

        try {
            rqItem.applyTemplate("Best Settings");
        } catch (eBest) {}

        var om = rqItem.outputModule(1);
        try {
            om.applyTemplate("H.264");
        } catch (eOM) {}

        om.file = outFile;

        app.project.renderQueue.render();

        // 4.4. Закрываем проект, но не AE GUI
        try {
            if (app.project) {
                if (typeof CloseOptions !== "undefined") {
                    app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);
                } else {
                    app.project.close();
                }
            }
        } catch (eClose) {}
    }
})();
