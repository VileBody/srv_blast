/**********************************************************************
 * run_job.jsx — ОРКЕСТРАТОР (вариант Б)
 * --------------------------------------------------------------------
 * Один проход на ролик. Читает job.json (что выбрал юзер) + manifest.json
 * (как это применять/откуда звук), сам собирает точки склейки из клипов,
 * и применяет: HOOK + TRANSITIONS + EXTRA + лого + звук.
 *
 * Запуск headless: afterfx.exe -r run_job.jsx  (проект уже открыт).
 * Путь к job.json берётся из переменной окружения BLAST_JOB или из
 * __job.json рядом со скриптом.
 *
 * job.json пример:
 *   { "dropTime": 4.2, "hook": "shutter_effect",
 *     "transition": "snap_wipe", "extra": "warm_map" }
 *
 * Параметры в дочерние скрипты прокидываются через глобал $.global.__BLAST:
 *   { comp, dropTime, place, startTime, duration, soundPool, soundFile,
 *     impactAt, logoPath, cuts: [...] }
 * Каждый дочерний скрипт в начале делает merge этого глобала в свой CONFIG
 * (хук-строка добавляется в unify-пасе). Сам run_job делает лого/звук напрямую.
 **********************************************************************/

var BASE = (function(){ return new File($.fileName).parent.fsName; })();
function log(m){ try { $.writeln("[run_job] " + m); } catch(e){} }

// ---- JSON (ExtendScript: объектный литерал = валидный JS) ----
function readJSON(path){
    var f = new File(path); if (!f.exists) return null;
    f.open("r"); var txt = f.read(); f.close();
    try { return eval("(" + txt + ")"); } catch(e){ log("JSON err " + path + ": " + e); return null; }
}

// ---- поиск компа по слою-ориентиру ----
function findLayer(c, n){ for (var i=1;i<=c.numLayers;i++) if (c.layer(i).name===n) return c.layer(i); return null; }
function findComp(placeRef){
    var a=app.project.activeItem; if (a && a instanceof CompItem && findLayer(a,placeRef)) return a;
    for (var i=1;i<=app.project.numItems;i++){ var it=app.project.item(i);
        if (it instanceof CompItem && findLayer(it,placeRef)) return it; }
    if (a && a instanceof CompItem) return a;
    return null;
}
// точки склейки = inPoint'ы видео-слоёв (клипов), отсортированные
function detectCuts(comp){
    var cuts=[];
    for (var i=1;i<=comp.numLayers;i++){
        var L=comp.layer(i);
        var isFootage = (L.source && (L.source instanceof FootageItem) && L.hasVideo && !L.adjustmentLayer);
        if (isFootage) cuts.push(L.inPoint);
    }
    cuts.sort(function(a,b){return a-b;});
    // убрать дубликаты (в пределах 1 кадра)
    var out=[], fr=comp.frameDuration;
    for (var k=0;k<cuts.length;k++){ if (!out.length || Math.abs(cuts[k]-out[out.length-1])>fr) out.push(cuts[k]); }
    return out;
}

// ---- манифест: резолвинг ----
function effById(man, id){ for (var i=0;i<man.effects.length;i++) if (man.effects[i].id===id) return man.effects[i]; return null; }
function poolDir(man, pool){ return (man.sounds && man.sounds.pools && man.sounds.pools[pool]) ? (BASE_ROOT + "/" + man.sounds.pools[pool]) : null; }

// корень "АЕ" (на 2 уровня выше Эффекты: Эффекты -> Хуки -> АЕ) —
// пути Звуки/* и Хуки/* в манифесте заданы относительно него.
var BASE_ROOT = (function(){ return new File(BASE).parent.parent.fsName; })();

// ---- вызвать дочерний скрипт с параметрами ----
function runScript(relPath, params){
    var f = new File(BASE + "/" + relPath);
    if (!f.exists){ log("нет скрипта: " + f.fsName); return; }
    $.global.__BLAST = params || {};
    try { $.evalFile(f); } catch(e){ log("evalFile " + relPath + ": " + e); }
    $.global.__BLAST = null;
}

// ============================ MAIN ============================
(function(){
    var manifest = readJSON(BASE + "/manifest.json");
    if (!manifest){ log("нет manifest.json"); return; }

    var jobPath = ($.getenv && $.getenv("BLAST_JOB")) ? $.getenv("BLAST_JOB") : (BASE + "/__job.json");
    var job = readJSON(jobPath);
    if (!job){ log("нет job.json (" + jobPath + ")"); return; }

    var placeRef = "Текст";
    var comp = findComp(placeRef);
    if (!comp){ log("не найдена рабочая комп"); return; }
    var cuts = detectCuts(comp);
    var drop = (job.dropTime != null) ? job.dropTime : (cuts.length ? cuts[cuts.length-1] : 0);

    app.beginUndoGroup("Blast job");
    try {
        // ---------- HOOK ----------
        if (job.hook){
            var h = effById(manifest, job.hook);
            if (h){
                runScript(h.script, { targetCompName: comp.name, dropTime: drop, place: "below:"+placeRef, cuts: cuts });
                // звук хука
                if (h.sound){
                    runScript("sound/sound.jsx", {
                        targetCompName: comp.name, dropTime: drop,
                        soundFile: h.sound.file ? (BASE_ROOT + "/" + h.sound.file) : null,
                        soundPool: h.sound.pool ? poolDir(manifest, h.sound.pool) : null,
                        impactAt: (h.sound.impact_at != null) ? h.sound.impact_at : null
                    });
                }
                // лого-штамп (branding:true ИЛИ built_in — shutter тоже получает лого)
                if (h.branding === true || h.branding === "built_in"){
                    runScript("branding/brand_logo.jsx", {
                        targetCompName: comp.name, dropTime: drop,
                        logoPath: BASE_ROOT + "/" + manifest.branding.logo_default,
                        style: h.branding_style || manifest.branding.default_style
                    });
                }
            }
        }

        // ---------- TRANSITIONS (на каждой склейке) ----------
        if (job.transition){
            var t = effById(manifest, job.transition);
            if (t){
                // один вызов: скрипт сам пройдёт по всем склейкам (cuts) или по клипам
                runScript(t.script, { targetCompName: comp.name, dropTime: drop,
                                      duration: t.default_duration, place: "below:"+placeRef, cuts: cuts });
                // одиночный сабдроп на дроп (правило drop_hit)
                if (manifest.sounds && manifest.sounds.rules && manifest.sounds.rules.drop_hit){
                    runScript("sound/sound.jsx", { targetCompName: comp.name, dropTime: drop,
                                                   soundPool: poolDir(manifest, manifest.sounds.rules.drop_hit.pool) });
                }
            }
        }

        // ---------- EXTRA (грейд на футажи) ----------
        if (job.extra){
            var e = effById(manifest, job.extra);
            if (e){
                runScript(e.script, { targetCompName: comp.name, dropTime: drop,
                                      startTime: (job.extraStart!=null?job.extraStart:0),
                                      // по умолчанию грейд тянется ДО дропа (футажи перед дропом)
                                      duration: (job.extraDuration!=null?job.extraDuration:(drop>0?drop:null)),
                                      place: "below:"+placeRef, cuts: cuts });
            }
        }
        log("готово: hook=" + job.hook + " trans=" + job.transition + " extra=" + job.extra + " drop=" + drop + " cuts=" + cuts.length);
    } catch(err){ log("Ошибка: " + err.toString() + " (стр " + (err.line||"?") + ")"); }
    finally { app.endUndoGroup(); }
})();
