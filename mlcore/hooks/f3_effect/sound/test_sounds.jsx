/**********************************************************************
 * test_sounds.jsx — быстрый аудит звуков
 * Кладёт в активную (или найденную) композицию от t=0:
 *   - звук молнии (Light Sound/myinstants.mp3)
 *   - все глитчи из папки Glitch Transform
 * Все слои на startTime=0, полная громкость. Двигай/солируй вручную,
 * слушай релевантность и уровень. Это ТОЛЬКО для теста.
 **********************************************************************/

var LIGHT = "C:/Users/Пользователь/Desktop/АЕ/Звуки/Light Sound/myinstants.mp3";
var GLITCH_DIR = "C:/Users/Пользователь/Desktop/АЕ/Звуки/Glitch Transform";

function findLayer(c, n){ for (var i=1;i<=c.numLayers;i++) if (c.layer(i).name===n) return c.layer(i); return null; }
function getComp(){
    var a = app.project.activeItem; if (a && a instanceof CompItem) return a;
    for (var i=1;i<=app.project.numItems;i++){ var it=app.project.item(i);
        if (it instanceof CompItem && findLayer(it,"Текст")) return it; }
    for (var j=1;j<=app.project.numItems;j++){ var c=app.project.item(j); if (c instanceof CompItem) return c; }
    return null;
}
function imp(file){
    for (var i=1;i<=app.project.numItems;i++){ var it=app.project.item(i);
        if (it instanceof FootageItem && it.name===file.name) return it; }
    try { return app.project.importFile(new ImportOptions(file)); } catch(e){ return null; }
}
function place(comp, src, label){
    var L = comp.layers.add(src);
    L.name = label + ": " + src.name;
    L.startTime = 0; L.inPoint = 0;
    L.outPoint = Math.min(comp.duration, src.duration);
    L.moveToBeginning();
    return L;
}

(function(){
    var comp = getComp();
    if (!comp){ alert("Не найдена композиция. Открой нужную."); return; }
    app.beginUndoGroup("Test sounds");
    var added = 0, names = [];

    // молния
    var lf = new File(LIGHT);
    if (lf.exists){ var ls = imp(lf); if (ls){ place(comp, ls, "TEST молния"); added++; names.push(ls.name); } }

    // все глитчи
    var fld = new Folder(GLITCH_DIR);
    if (fld.exists){
        var files = fld.getFiles(function(x){ return (x instanceof File) && /\.(wav|mp3|aif|aiff|m4a)$/i.test(x.name); });
        for (var i=0;i<files.length;i++){ var s = imp(files[i]); if (s){ place(comp, s, "TEST глитч"); added++; names.push(s.name); } }
    }

    app.endUndoGroup();
    alert("Добавлено звуков: " + added + " (от t=0) в «" + comp.name + "».\n\n" + names.join("\n") +
          "\n\nСолируй/двигай вручную для оценки.");
})();
