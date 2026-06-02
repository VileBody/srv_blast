/**********************************************************************
 * run_job.jsx — ОРКЕСТРАТОР (вариант Б)
 * --------------------------------------------------------------------
 * Один проход на ролик. Читает job.json (что выбрал юзер) + manifest.json
 * (как это применять/откуда звук), сам собирает точки склейки из клипов,
 * и применяет: HOOK + TRANSITIONS + EXTRA + лого + звук.
 *
 * Запуск headless: afterfx.exe -r run_job.jsx  (проект уже открыт).
 * job.json берётся из env BLAST_JOB или из __job.json рядом со скриптом.
 * Корень ассетов (Звуки/Лого) — из env BLAST_ASSET_ROOT (см. ниже).
 *
 * job.json пример:
 *   { "dropTime": 4.2, "hook": "flash_slow_shutter",
 *     "transition": "snap_wipe", "extra": "warm_map",
 *     "hookExtend": "to_end" }          // опц.: "to_end" | "after_drop:3"
 *
 * dropTime — COMP-relative секунды в "Comp 1" (= user_drop_t минус начало
 * рендер-сегмента; конвертация как в f5_cognition abs->relative).
 *
 * СИНХРОН ЗВУКА (важно):
 *   - На ДРОП звук вешает только сам ХУК (молния у hook light / вспышка
 *     камеры у shutter/slow shutter). Сабдроп НЕ применяется.
 *   - Звук переходов/грейдов (glitch) вешается на склейки СТРОГО ДО дропа,
 *     после дропа — тишина. Один звук на склейку (дедуп переход+грейд).
 *
 * Параметры в дочерние скрипты — через глобал $.global.__BLAST, каждый
 * скрипт merge-ит его в свой CONFIG на старте.
 **********************************************************************/

var BASE = (function(){ return new File($.fileName).parent.fsName; })();
function log(m){ try { $.writeln("[run_job] " + m); } catch(e){} }

// корень репо-пайплайна (на 2 уровня выше: f3_effect -> hooks -> mlcore) — дефолт.
var BASE_ROOT = (function(){ return new File(BASE).parent.parent.fsName; })();
// корень АССЕТОВ (Звуки/* и Лого). На рендер-ноде задаётся env BLAST_ASSET_ROOT,
// т.к. в репо самих звуков/лого нет. Без env — падаем на BASE_ROOT (dev).
var ASSET_ROOT = ($.getenv && $.getenv("BLAST_ASSET_ROOT")) ? $.getenv("BLAST_ASSET_ROOT") : BASE_ROOT;

// ---- JSON (ExtendScript: объектный литерал = валидный JS) ----
function readJSON(path){
    var f = new File(path); if (!f.exists) return null;
    f.open("r"); var txt = f.read(); f.close();
    try { return eval("(" + txt + ")"); } catch(e){ log("JSON err " + path + ": " + e); return null; }
}

// ---- поиск компа по слою-ориентиру ("Текст" -> это Comp 1, где футаж) ----
function findLayer(c, n){ for (var i=1;i<=c.numLayers;i++) if (c.layer(i).name===n) return c.layer(i); return null; }
function findComp(placeRef){
    var a=app.project.activeItem; if (a && a instanceof CompItem && findLayer(a,placeRef)) return a;
    for (var i=1;i<=app.project.numItems;i++){ var it=app.project.item(i);
        if (it instanceof CompItem && findLayer(it,placeRef)) return it; }
    if (a && a instanceof CompItem) return a;
    return null;
}
// точки склейки = inPoint'ы видео-слоёв (клипов), отсортированные, дедуп в 1 кадр
function detectCuts(comp){
    var cuts=[];
    for (var i=1;i<=comp.numLayers;i++){
        var L=comp.layer(i);
        var isFootage = (L.source && (L.source instanceof FootageItem) && L.hasVideo && !L.adjustmentLayer);
        if (isFootage) cuts.push(L.inPoint);
    }
    cuts.sort(function(a,b){return a-b;});
    var out=[], fr=comp.frameDuration;
    for (var k=0;k<cuts.length;k++){ if (!out.length || Math.abs(cuts[k]-out[out.length-1])>fr) out.push(cuts[k]); }
    return out;
}

// ---- манифест: резолвинг ----
function effById(man, id){ for (var i=0;i<man.effects.length;i++) if (man.effects[i].id===id) return man.effects[i]; return null; }
function poolDir(man, pool){ return (man.sounds && man.sounds.pools && man.sounds.pools[pool]) ? (ASSET_ROOT + "/" + man.sounds.pools[pool]) : null; }

// ---- вызвать дочерний скрипт с параметрами ----
function runScript(relPath, params){
    var f = new File(BASE + "/" + relPath);
    if (!f.exists){ log("нет скрипта: " + f.fsName); return; }
    $.global.__BLAST = params || {};
    try { $.evalFile(f); } catch(e){ log("evalFile " + relPath + ": " + e); }
    $.global.__BLAST = null;
}

// конец КОНТЕНТА (не компа): workArea, т.к. Comp 1 длиннее видимого ролика.
function contentEnd(comp){
    var wa = comp.workAreaStart + comp.workAreaDuration;
    return (wa > 0 && wa <= comp.duration) ? wa : comp.duration;
}
// ---- длительность хука: дефолт = default_duration; extendable-хуки можно тянуть ----
function hookDuration(h, comp, drop, cuts, extend){
    var base = (h.default_duration!=null) ? h.default_duration : 0.5;
    if (!h.extendable || !extend) return base;
    var endT = contentEnd(comp);
    if (extend === "to_end") return Math.max(base, endT - drop);
    var m = String(extend).match(/^after_drop:(\d+)$/);
    if (m){
        var n = parseInt(m[1], 10), after = [], fr = comp.frameDuration, i;
        for (i=0;i<cuts.length;i++){ if (cuts[i] > drop + fr) after.push(cuts[i]); }
        if (after.length >= n) return Math.max(base, after[n-1] - drop); // до n-й склейки после дропа
        return Math.max(base, endT - drop);                              // склеек < n -> до конца контента
    }
    return base;
}

// ---- звук на склейки СТРОГО ДО дропа (один на склейку; used = уже озвученные) ----
function attachCutSounds(man, comp, poolName, cuts, drop, used){
    if (!poolName) return;
    var dir = poolDir(man, poolName); if (!dir) return;
    var fr = comp.frameDuration, i, u;
    for (i=0;i<cuts.length;i++){
        var ct = cuts[i];
        if (ct >= drop - fr) continue;                 // только до дропа (на дропе звучит хук)
        var dup=false; for (u=0;u<used.length;u++){ if (Math.abs(used[u]-ct)<=fr){ dup=true; break; } }
        if (dup) continue;                             // не дублировать на той же склейке
        runScript("sound/sound.jsx", { targetCompName: comp.name, dropTime: ct,
                                       soundPool: dir, impactAt: null, maxDuration: 1.5 });
        used.push(ct);
    }
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
    if (!comp){ log("не найдена рабочая комп (слой '" + placeRef + "')"); return; }
    var cuts = detectCuts(comp);
    var drop = (job.dropTime != null) ? job.dropTime : (cuts.length ? cuts[cuts.length-1] : 0);
    var usedSnd = []; // времена склеек, на которые уже повесили звук (дедуп переход+грейд)

    app.beginUndoGroup("Blast job");
    try {
        // ---------- HOOK (визуал на дропе + свой звук на дропе + лого) ----------
        if (job.hook){
            var h = effById(manifest, job.hook);
            if (h){
                var hookDur = hookDuration(h, comp, drop, cuts, job.hookExtend);
                runScript(h.script, { targetCompName: comp.name, dropTime: drop,
                                      duration: hookDur, place: "below:"+placeRef, cuts: cuts });
                // звук хука = единственный звук на дропе (молния / вспышка камеры)
                if (h.sound){
                    runScript("sound/sound.jsx", {
                        targetCompName: comp.name, dropTime: drop,
                        soundFile: h.sound.file ? (ASSET_ROOT + "/" + h.sound.file) : null,
                        soundPool: h.sound.pool ? poolDir(manifest, h.sound.pool) : null,
                        impactAt: (h.sound.impact_at != null) ? h.sound.impact_at : null
                    });
                }
                // лого-штамп (branding:true ИЛИ built_in)
                if (h.branding === true || h.branding === "built_in"){
                    runScript("branding/brand_logo.jsx", {
                        targetCompName: comp.name, dropTime: drop,
                        logoPath: ASSET_ROOT + "/" + manifest.branding.logo_default,
                        style: h.branding_style || manifest.branding.default_style
                    });
                }
            }
        }

        // ---------- TRANSITIONS (визуал на каждой склейке; звук — только до дропа) ----------
        if (job.transition){
            var t = effById(manifest, job.transition);
            if (t){
                runScript(t.script, { targetCompName: comp.name, dropTime: drop,
                                      duration: t.default_duration, place: "below:"+placeRef, cuts: cuts });
                if (t.sound && t.sound.pool) attachCutSounds(manifest, comp, t.sound.pool, cuts, drop, usedSnd);
            }
        }

        // ---------- EXTRA (грейд 0..дроп; звук — на склейки до дропа, дедуп с переходом) ----------
        if (job.extra){
            var e = effById(manifest, job.extra);
            if (e){
                runScript(e.script, { targetCompName: comp.name, dropTime: drop,
                                      startTime: (job.extraStart!=null?job.extraStart:0),
                                      duration: (job.extraDuration!=null?job.extraDuration:(drop>0?drop:null)),
                                      place: "below:"+placeRef, cuts: cuts });
                if (e.sound && e.sound.pool) attachCutSounds(manifest, comp, e.sound.pool, cuts, drop, usedSnd);
            }
        }

        log("готово: hook=" + job.hook + " ext=" + (job.hookExtend||"-") +
            " trans=" + job.transition + " extra=" + job.extra +
            " drop=" + drop + " cuts=" + cuts.length + " cutSnd=" + usedSnd.length);
    } catch(err){ log("Ошибка: " + err.toString() + " (стр " + (err.line||"?") + ")"); }
    finally { app.endUndoGroup(); }
})();
