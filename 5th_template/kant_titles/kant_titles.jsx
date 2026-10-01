// Kant AddText titles → Blast. Тайтлы txtfx (Two Frames, Gum, Matrix, Edit, VHS, Lani Style) как стили строк субтитров.
//
//   $.evalFile(".../blast/kant_titles.jsx");
//   var layer = KantTitles.place(comp, "gum", "текст строки", 1.20, 2.85, { y: 1500, maxWidth: 940 });
//   KantTitles.checkFonts("matrix") → { ok: bool, missing: [...] }
//
// Как устроено: шаблон (.aep из txtfx/aep) импортируется в проект один раз, на каждую строку дублируется
// его композиция KANT_TEXT_V1__<id>, во все текстовые слои пишется текст, прекомп кладётся в целевую
// композицию с startTime = start (анимация входа тайтла отсчитывается от inPoint), outPoint = end.
// Экспрешены Animation Composer внутри шаблонов «запечены» (функция в самом экспрешене, псевдоэффекты в .aep) —
// плагин AC не нужен. Нужны шрифты и эффекты из TITLES ниже.
(function () {
    // Папка шаблонов: $.global.KANT_TITLES_AEP_DIR (задание ноды кладёт .aep в media/) или txtfx/aep рядом с модулем.
    var AEP_DIR = $.global.KANT_TITLES_AEP_DIR ||
        (new File($.fileName).parent.parent.fsName + "/txtfx/aep");

    // intro — длина анимации входа в шаблоне, с (из дампа ключей); cyr — замены шрифтов без кириллицы
    // (Staatliches, SixCaps, Nirmala Text, Adobe Arabic её не содержат — AE иначе рисует русский текст запасным шрифтом).
    // Замены — шрифты из комплекта Kant (engines/txtfx/fonts): Oswald, Inter, Cormorant Garamond.
    var TITLES = {
        "two-frames": { title: "Two Frames", fonts: ["Staatliches-Regular"], effects: [], intro: 0.1,
                        cyr: { "Staatliches-Regular": "Oswald-Regular" } },
        "gum":        { title: "Gum", fonts: ["Arial-BoldMT"], effects: ["PEDG2", "CC Light Sweep"], intro: 1.43, cyr: {} },
        "matrix":     { title: "Matrix", fonts: ["CoolveticaRg-Regular"], effects: ["S_Gradient", "S_Flicker"], intro: 2.0, cyr: {} },
        "edit":       { title: "Edit", fonts: ["NirmalaText-Semilight", "TagType", "AdobeArabic-BoldItalic"], effects: [], intro: 0.6,
                        cyr: { "NirmalaText-Semilight": "Inter-Regular", "AdobeArabic-BoldItalic": "CormorantGaramond-LightItalic" } },
        "vhs":        { title: "VHS", fonts: ["SixCaps"], effects: ["S_WarpFishEye", "S_Shake"], intro: 2.85,
                        // Oswald шире сверхузкого SixCaps (+ fisheye раздувает центр) — при замене поджимаем по X
                        cyr: { "SixCaps": "Oswald-Regular" }, cyrSqueeze: 0.75 },
        "lani-style": { title: "Motion", fonts: ["TagType"], effects: [], intro: 1.0, cyr: {} }
    };
    // Разбиение строк на экраны (placeLine): вес слова — 0.5 для коротких (≤3 букв), 1 для остальных;
    // на экран не больше MAX_WEIGHT («2 длинных или 3 коротких»). Кусок, который влезает по ширине только при
    // кегле < MIN_FIT от шаблонного, делится дальше; одиночное слово всегда ужимается под ширину.
    var MAX_WEIGHT = 2;
    var MIN_FIT = 0.8;
    var MIN_SCREEN = 0.35;    // с — короче экран не держим: соседние куски склеиваются
    var INTRO_SHARE = 0.45;   // вход тайтла занимает не больше этой доли строки — иначе ускоряем (stretch)
    var MIN_STRETCH = 30;     // %, быстрее 3.3x не разгоняем
    var CYRILLIC = /[Ѐ-ӿ]/;

    function findComp(name, folder) {
        for (var i = 1; i <= app.project.numItems; i++) {
            var it = app.project.item(i);
            if (it instanceof CompItem && it.name === name && (!folder || it.parentFolder === folder)) return it;
        }
        return null;
    }
    function templatesFolder() {
        for (var i = 1; i <= app.project.numItems; i++) {
            var it = app.project.item(i);
            if (it instanceof FolderItem && it.name === "KANT_TITLES") return it;
        }
        return app.project.items.addFolder("KANT_TITLES");
    }

    // шаблон импортируется один раз; повторные вызовы берут уже импортированную композицию
    function template(id) {
        if (!TITLES[id]) throw new Error("KantTitles: неизвестный тайтл " + id);
        var folder = templatesFolder(), name = "KANT_TEXT_V1__" + id;
        var comp = findComp(name, null);
        if (comp) return comp;
        var file = new File(AEP_DIR + "/" + id + ".aep");
        if (!file.exists) throw new Error("KantTitles: нет файла " + file.fsName);
        var before = {};
        for (var b = 1; b <= app.project.numItems; b++) before[app.project.item(b).id] = true;
        var imported = app.project.importFile(new ImportOptions(file));
        try { imported.parentFolder = folder; imported.name = "tpl_" + id; } catch (e) {}
        comp = findComp(name, null);
        if (!comp) throw new Error("KantTitles: в " + file.name + " нет композиции " + name);
        return comp;
    }

    // текст во все текстовые слои (вкл. ключи Source Text и прекомпы); fontMap — замены шрифтов по PostScript-имени
    function setTextEverywhere(comp, text, fontMap, visited) {
        visited = visited || {};
        if (visited[comp.id]) return 0;
        visited[comp.id] = true;
        var changed = 0;
        function edit(doc) {
            doc.text = text;
            if (fontMap && fontMap.hasOwnProperty(doc.font)) doc.font = fontMap[doc.font];
            return doc;
        }
        for (var i = 1; i <= comp.numLayers; i++) {
            var l = comp.layer(i), g = l.property("ADBE Text Properties");
            if (g) {
                var p = g.property("ADBE Text Document");
                if (p.numKeys) for (var k = 1; k <= p.numKeys; k++) p.setValueAtKey(k, edit(p.keyValue(k)));
                else p.setValue(edit(p.value));
                changed++;
            }
            if (l.source instanceof CompItem) changed += setTextEverywhere(l.source, text, fontMap, visited);
        }
        return changed;
    }

    // ширина самого широкого текстового слоя (в пикселях композиции тайтла) в момент t
    function textWidth(comp, t) {
        var w = 0;
        for (var i = 1; i <= comp.numLayers; i++) {
            var l = comp.layer(i);
            if (!(l instanceof TextLayer) || t < l.inPoint || t >= l.outPoint) continue;
            try {
                var r = l.sourceRectAtTime(t, false), s = l.property("ADBE Transform Group").property("ADBE Scale").valueAtTime(t, false);
                w = Math.max(w, r.width * Math.abs(s[0]) / 100);
            } catch (e) {}
        }
        return w;
    }
    function scaleFonts(comp, factor) {
        for (var i = 1; i <= comp.numLayers; i++) {
            var g = comp.layer(i).property("ADBE Text Properties");
            if (!g) continue;
            var p = g.property("ADBE Text Document");
            if (p.numKeys) for (var k = 1; k <= p.numKeys; k++) { var kd = p.keyValue(k); kd.fontSize = Math.max(1, kd.fontSize * factor); p.setValueAtKey(k, kd); }
            else { var d = p.value; d.fontSize = Math.max(1, d.fontSize * factor); p.setValue(d); }
        }
    }

    // сжатие текстовых слоёв по X (масштаб слоя; ключи масштаба тоже) — для замен шрифта шире оригинала
    function squeezeX(comp, factor) {
        for (var i = 1; i <= comp.numLayers; i++) {
            var l = comp.layer(i);
            if (!(l instanceof TextLayer)) continue;
            var sc = l.property("ADBE Transform Group").property("ADBE Scale");
            if (sc.numKeys) for (var k = 1; k <= sc.numKeys; k++) { var kv = sc.keyValue(k); kv[0] *= factor; sc.setValueAtKey(k, kv); }
            else { var v = sc.value; v[0] *= factor; sc.setValue(v); }
        }
    }
    function styleFor(comp, info, text, extraFonts) {
        var changed = setTextEverywhere(comp, String(text), fontMapFor(info, text, extraFonts));
        if (info.cyrSqueeze && CYRILLIC.test(String(text))) squeezeX(comp, info.cyrSqueeze);
        return changed;
    }

    function fontMapFor(info, text, extra) {
        var map = {}, k;
        if (CYRILLIC.test(String(text))) for (k in info.cyr) if (info.cyr.hasOwnProperty(k)) map[k] = info.cyr[k];
        if (extra) for (k in extra) if (extra.hasOwnProperty(k)) map[k] = extra[k];
        return map;
    }

    // ширина текста в тайтле при шаблонном кегле (px композиции тайтла) — через временный дубль шаблона
    var widthCache = {};
    function naturalWidth(id, text, extraFonts) {
        var key = id + "\u0001" + text;
        if (widthCache.hasOwnProperty(key)) return widthCache[key];
        var info = TITLES[id], dup = template(id).duplicate();
        try {
            styleFor(dup, info, text, extraFonts);
            widthCache[key] = textWidth(dup, Math.min(dup.duration - dup.frameDuration, info.intro + 0.3));
        } finally { dup.remove(); }
        return widthCache[key];
    }

    function wordWeight(w) { return w.replace(/[^A-Za-z0-9Ѐ-ӿ]/g, "").length <= 3 ? 0.5 : 1; }

    // слова строки → куски для экранов. words: [{text, start, end}] (тайминги слов) или null
    function splitLine(id, text, start, end, opts) {
        var raw = String(text).replace(/^\s+|\s+$/g, "").split(/\s+/), words = [], i;
        var timed = opts.words && opts.words.length === raw.length;
        for (i = 0; i < raw.length; i++) words.push({ text: raw[i], start: timed ? opts.words[i].start : null, end: timed ? opts.words[i].end : null });
        var maxWeight = opts.maxWords !== undefined ? opts.maxWords : MAX_WEIGHT;
        var maxW = opts.maxWidth !== undefined ? opts.maxWidth : 940;
        var scale = (opts.scale !== undefined ? opts.scale : 100) / 100;

        // единицы: предлог/частица (≤2 букв) прилипает к следующему слову
        var units = [], carry = [];
        for (i = 0; i < words.length; i++) {
            carry.push(words[i]);
            var bare = words[i].text.replace(/[^A-Za-z0-9Ѐ-ӿ]/g, "");
            if (bare.length > 2 || i === words.length - 1) { units.push(carry); carry = []; }
        }

        function fits(ws) {
            var t = [], wt = 0;
            for (var j = 0; j < ws.length; j++) { t.push(ws[j].text); wt += wordWeight(ws[j].text); }
            if (ws.length > 1 && wt > maxWeight) return false;
            var w = naturalWidth(id, t.join(" "), opts.fontMap) * scale;
            return ws.length === 1 || w <= 0 || maxW / w >= MIN_FIT; // одно слово влезает всегда — place() ужмёт кегль
        }
        var chunks = [], cur = [];
        for (i = 0; i < units.length; i++) {
            var next = cur.concat(units[i]);
            if (cur.length && !fits(next)) { chunks.push(cur); cur = units[i].slice(0); }
            else cur = next;
            // единица из нескольких слов сама может не влезть — режем её по словам
            while (cur.length > 1 && !fits(cur)) {
                var cut = cur.length - 1;
                while (cut > 1 && !fits(cur.slice(0, cut))) cut--;
                chunks.push(cur.slice(0, cut)); cur = cur.slice(cut);
            }
        }
        if (cur.length) chunks.push(cur);

        // тайминги: по словам, иначе пропорционально длине куска
        var total = 0, c, j;
        for (c = 0; c < chunks.length; c++) for (j = 0; j < chunks[c].length; j++) total += chunks[c][j].text.length + 1;
        var out = [], t0 = start;
        for (c = 0; c < chunks.length; c++) {
            var len = 0, txt = [];
            for (j = 0; j < chunks[c].length; j++) { len += chunks[c][j].text.length + 1; txt.push(chunks[c][j].text); }
            var s = timed ? chunks[c][0].start : t0;
            var e = timed ? (c + 1 < chunks.length ? chunks[c + 1][0].start : end) : (c === chunks.length - 1 ? end : t0 + (end - start) * len / total);
            out.push({ text: txt.join(" "), start: s, end: e });
            t0 = e;
        }
        // слишком короткие экраны склеиваем с соседним
        for (c = out.length - 1; c > 0; c--) {
            if (out[c].end - out[c].start < MIN_SCREEN || out[c - 1].end - out[c - 1].start < MIN_SCREEN) {
                out[c - 1] = { text: out[c - 1].text + " " + out[c].text, start: out[c - 1].start, end: out[c].end };
                out.splice(c, 1);
            }
        }
        return out;
    }

    // приглушённый хвост: тайтлы Kant без анимации выхода — короткий фейд, чтобы строки не «щёлкали»
    function fadeOut(layer, frames) {
        var op = layer.property("ADBE Transform Group").property("ADBE Opacity"), fd = layer.containingComp.frameDuration;
        var t1 = layer.outPoint, t0 = Math.max(layer.inPoint, t1 - frames * fd);
        if (t1 - t0 < fd) return;
        op.setValueAtTime(t0, 100);
        op.setValueAtTime(t1, 0);
    }

    $.global.KantTitles = {
        ids: function () { var r = []; for (var k in TITLES) if (TITLES.hasOwnProperty(k)) r.push(k); return r; },
        info: function (id) { return TITLES[id]; },

        // text — чтобы проверить именно те шрифты, которые пойдут в рендер (с кириллицей — замены из cyr)
        checkFonts: function (id, text) {
            var t = TITLES[id], missing = [];
            if (!t) return { ok: false, missing: ["<unknown title>"] };
            var cyr = text !== undefined && CYRILLIC.test(String(text));
            for (var i = 0; i < t.fonts.length; i++) {
                var font = cyr && t.cyr.hasOwnProperty(t.fonts[i]) ? t.cyr[t.fonts[i]] : t.fonts[i], ok = false;
                try {
                    var m = app.fonts.getFontsByPostScriptName(font);
                    for (var n = 0; m && n < m.length; n++) if (!m[n].isSubstitute) ok = true;
                } catch (e) { ok = true; } // старый AE без app.fonts — не можем проверить
                if (!ok) missing.push(font);
            }
            return { ok: !missing.length, missing: missing };
        },

        // opts: { x, y (центр строки в целевой комп.), maxWidth (px целевой комп.), scale (%), fadeFrames, name,
        //         fontMap ({PostScript: PostScript} — свои замены шрифтов, перекрывают кириллические) }
        place: function (target, id, text, start, end, opts) {
            opts = opts || {};
            var info = TITLES[id], tpl = template(id);
            var comp = tpl.duplicate();
            comp.name = (opts.name || ("title " + id)) + " · " + String(text).substring(0, 24);
            comp.parentFolder = templatesFolder();
            if (!styleFor(comp, info, text, opts.fontMap)) throw new Error("KantTitles: в шаблоне " + id + " нет текстовых слоёв");

            var scale = opts.scale !== undefined ? opts.scale : 100;
            var maxW = opts.maxWidth !== undefined ? opts.maxWidth : target.width * 0.87;
            var probe = Math.min(comp.duration - comp.frameDuration, info.intro + 0.3); // после входа — текст в финальном размере
            var w = textWidth(comp, probe) * scale / 100;
            if (w > maxW && w > 0) scaleFonts(comp, maxW / w);

            var layer = target.layers.add(comp);
            layer.name = comp.name;
            // длинный вход (VHS 2.85с, Matrix 2с) на короткой строке не успевает встать — ускоряем весь тайтл
            var lineDur = Math.max(target.frameDuration, end - start);
            var stretch = 100;
            if (info.intro > INTRO_SHARE * lineDur) stretch = Math.max(MIN_STRETCH, 100 * INTRO_SHARE * lineDur / info.intro);
            layer.stretch = stretch;
            layer.startTime = start;
            layer.inPoint = start;
            layer.outPoint = Math.min(end, start + comp.duration * stretch / 100);
            var tg = layer.property("ADBE Transform Group");
            tg.property("ADBE Scale").setValue([scale, scale]);
            tg.property("ADBE Position").setValue([opts.x !== undefined ? opts.x : target.width / 2, opts.y !== undefined ? opts.y : target.height / 2]);
            if (opts.fadeFrames !== 0) fadeOut(layer, opts.fadeFrames || 3);
            return layer;
        },

        // Строка субтитров → несколько экранов тайтла (основной вход для пайпа).
        // opts: как у place + maxWords (вес на экран, по умолчанию 2; 1 — по одному слову),
        //       words: [{start, end}] — тайминги слов строки (тогда экраны режутся по ним)
        // Возвращает [{text, start, end, layer}].
        placeLine: function (target, id, text, start, end, opts) {
            opts = opts || {};
            var screens = splitLine(id, text, start, end, opts), out = [];
            for (var i = 0; i < screens.length; i++) {
                var s = screens[i];
                out.push({ text: s.text, start: s.start, end: s.end, layer: this.place(target, id, s.text, s.start, s.end, opts) });
            }
            return out;
        },

        // только разбиение, без слоёв (для превью/проверки тайминга)
        split: function (id, text, start, end, opts) { return splitLine(id, text, start, end, opts || {}); }
    };
})();
