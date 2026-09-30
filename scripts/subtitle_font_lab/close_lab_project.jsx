// Закрывает открытый лаб-проект шрифтов (jakson-*.aep) БЕЗ сохранения — только по
// явному согласию пользователя (правки смотра не нужны). Чужой проект не трогает.
(function () {
    var p = app.project;
    if (!p || !p.file) return;
    if (!/^(jakson|impulse|tape|trendy|brat)-/.test(String(p.file.name))) throw new Error("not a font lab project: " + p.file.fsName);
    if (p.renderQueue.numItems !== 0) throw new Error("render queue is not empty");
    p.close(CloseOptions.DO_NOT_SAVE_CHANGES);
    app.newProject();
})();
