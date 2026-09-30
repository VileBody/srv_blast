// Находит в AE (app.fonts, AE 24+) PostScript-имена шрифтов по подстрокам семейства.
// Проект не трогает. Вход: <Folder.temp>/blast_font_lab/find.json ["katherine", "simphony", ...]
// Выход: <Folder.temp>/blast_font_lab/found_fonts.json
(function () {
    var dir = Folder.temp.fsName + "/blast_font_lab";
    var inFile = new File(dir + "/find.json");
    inFile.encoding = "UTF-8";
    if (!inFile.open("r")) throw new Error("cannot read " + inFile.fsName);
    var needles = eval("(" + inFile.read() + ")");
    inFile.close();
    function q(s) { return '"' + String(s).replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"'; }

    var rows = [];
    var groups = app.fonts.allFonts;
    for (var g = 0; g < groups.length; g++) {
        var fam = groups[g];
        for (var k = 0; k < fam.length; k++) {
            var f = fam[k];
            var hay = (String(f.familyName) + " " + String(f.postScriptName)).toLowerCase();
            for (var n = 0; n < needles.length; n++) {
                if (hay.indexOf(String(needles[n]).toLowerCase()) >= 0) {
                    rows.push("{" + q("needle") + ":" + q(needles[n]) + "," + q("ps") + ":" + q(f.postScriptName) + "," +
                        q("family") + ":" + q(f.familyName) + "," + q("style") + ":" + q(f.styleName) + "}");
                    break;
                }
            }
        }
    }
    var out = new File(dir + "/found_fonts.json");
    out.encoding = "UTF-8";
    if (!out.open("w")) throw new Error("cannot write " + out.fsName);
    out.write("[\n" + rows.join(",\n") + "\n]\n");
    out.close();
})();
