/*
 * Read-only font inventory for the Windows render node.
 * Run from After Effects 24.0+ with File > Scripts > Run Script File.
 * Writes exact PostScript names and available script metadata to the desktop.
 */
(function () {
    function safe(value) {
        try {
            return value == null ? "" : String(value);
        } catch (e) {
            return "";
        }
    }

    function csv(value) {
        return '"' + safe(value).replace(/"/g, '""') + '"';
    }

    function scriptNames(font) {
        var names = [];
        try {
            var scripts = font.writingScripts || [];
            for (var i = 0; i < scripts.length; i++) {
                names.push(String(scripts[i]));
            }
        } catch (e) {}
        return names.join("|");
    }

    if (!app.fonts || !app.fonts.allFonts) {
        alert("app.fonts недоступен. Нужен After Effects 24.0 или новее.");
        return;
    }

    var groups = app.fonts.allFonts;
    var rows = [];
    var count = 0;

    for (var g = 0; g < groups.length; g++) {
        var group = groups[g];
        if (!(group instanceof Array)) group = [group];

        for (var f = 0; f < group.length; f++) {
            var font = group[f];
            rows.push([
                safe(font.familyName),
                safe(font.styleName),
                safe(font.postScriptName),
                safe(font.fullName),
                safe(font.nativeFullName),
                scriptNames(font),
                safe(font.technology),
                safe(font.type),
                safe(font.version)
            ]);
            count++;
        }
    }

    rows.sort(function (a, b) {
        var left = (a[0] + "\t" + a[1] + "\t" + a[2]).toLowerCase();
        var right = (b[0] + "\t" + b[1] + "\t" + b[2]).toLowerCase();
        return left < right ? -1 : (left > right ? 1 : 0);
    });

    var header = ["familyName", "styleName", "postScriptName", "fullName", "nativeFullName", "writingScripts", "technology", "type", "version"];
    var headerCells = [];
    for (var h = 0; h < header.length; h++) headerCells.push(csv(header[h]));
    var lines = [headerCells.join(",")];
    for (var r = 0; r < rows.length; r++) {
        var cells = [];
        for (var c = 0; c < rows[r].length; c++) cells.push(csv(rows[r][c]));
        lines.push(cells.join(","));
    }

    var output = new File(Folder.desktop.fsName + "/ae_font_inventory.csv");
    output.encoding = "UTF-8";
    if (!output.open("w")) {
        alert("Не удалось создать файл: " + output.fsName);
        return;
    }
    output.write(lines.join("\r\n"));
    output.close();

    alert("Шрифтов: " + count + "\nAE: " + app.version + "\nФайл: " + output.fsName);
})();
