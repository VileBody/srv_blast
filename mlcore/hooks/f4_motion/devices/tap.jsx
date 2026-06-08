/**********************************************************************
 * F4 motion-hook overlay — device "tap"  ("тапай над экраном")
 * Injectable form of rebuild_tap.jsx. Builds the tap overlay on the passed
 * comp from comp-time 0 (clip window pre-reframed so cover-end == hook).
 * Tokens: __F4_BPM__ (numeric), __F4_DEVICE__ (id string).
 * Layer length is FIXED (timeScale=1.0); sag1 pulse follows bpm via expression.
 * S_Glow is Sapphire (skipped gracefully if absent).
 **********************************************************************/
(function (comp) {
  if (!comp) { try { $.writeln("[F4][__F4_DEVICE__] no comp"); } catch(_){} return; }

  var CONFIG = {
    textHold:    "ТАПАЙ \rНАД ЭКРАНОМ\rВ ТАКТ",
    textRelease: "ОТПУСКАЙ!",
    bpm:         __F4_BPM__,
    dropdownIdx: 1,
    sagPulseHi:  70,
    sagPulseLo:  50,
    sagSmooth:   1.0,
    timeScale:   1.0
  };

  var TS = CONFIG.timeScale;
  function t(x){ return x * TS; }

  function setKeys(prop, keys, ease){
    if (ease === undefined) ease = true;
    var i;
    for (i = 0; i < keys.length; i++) prop.setValueAtTime(t(keys[i].time), keys[i].val);
    if (!prop.numKeys) return;
    var dim = 1;
    var v = keys[0].val;
    if (v instanceof Array) dim = v.length;
    for (i = 1; i <= prop.numKeys; i++){
      try { prop.setInterpolationTypeAtKey(i,
        KeyframeInterpolationType.BEZIER, KeyframeInterpolationType.BEZIER); } catch(e){}
      if (ease){
        try {
          var inE = [], outE = [];
          for (var d = 0; d < dim; d++){ inE.push(new KeyframeEase(0, 33.333)); outE.push(new KeyframeEase(0, 33.333)); }
          prop.setTemporalEaseAtKey(i, inE, outE);
        } catch(e){}
      }
    }
  }
  function setConst(prop, val){ try { prop.setValue(val); } catch(e){} }
  function setExpr(prop, expr){ try { prop.expression = expr; } catch(e){} }
  function setEffParam(eff, mn, val){ try { eff.property(mn).setValue(val); } catch(e){} }
  function setEffKeys(eff, mn, keys, ease){ try { setKeys(eff.property(mn), keys, ease); } catch(e){} }

  function styleText(td, txt){
    td.text = txt;
    try { td.font = "TimesNewRomanPS-ItalicMT"; } catch(e){}
    td.fontSize = 72;
    td.applyFill = true;
    td.fillColor = [0.92157,0.92157,0.92157];
    td.applyStroke = false;
    try { td.justification = ParagraphJustification.CENTER_JUSTIFY; } catch(e){}
    try { td.tracking = -60; } catch(e){}
    try { td.autoLeading = false; td.leading = 131; } catch(e){}
    return td;
  }

  function buildBlackSolid(comp){
    var L = comp.layers.addSolid([0,0,0], "Сплошная заливка Черный 1",
                                 comp.width, comp.height, 1);
    L.startTime = 0; L.inPoint = 0; L.outPoint = t(4.3043043043043);
    var tr = L.property("ADBE Transform Group");
    // Cover must span the WHOLE comp ("Comp 1" is 1080x1960, not 1920 — the
    // hardcoded [540,960] left a ~20px gap at the bottom). Center on the actual
    // comp dims + 10% over-scale so reframe/rounding never exposes footage.
    var __cx = comp.width/2, __cy = comp.height/2;
    setConst(tr.property("ADBE Anchor Point"), [__cx,__cy,0]);
    setConst(tr.property("ADBE Position"),     [__cx,__cy,0]);
    setConst(tr.property("ADBE Scale"),        [110,110,100]);
    setConst(tr.property("ADBE Opacity"),      96);
    return L;
  }

  function buildBpmControl(comp){
    var L = comp.layers.addNull(comp.duration);
    L.name = "bpm control";
    L.startTime = 0; L.inPoint = 0; L.outPoint = t(5.30530530530531);
    var tr = L.property("ADBE Transform Group");
    setConst(tr.property("ADBE Anchor Point"), [0,0,0]);
    setConst(tr.property("ADBE Position"),     [213.33332824707,1460,0]);
    setConst(tr.property("ADBE Opacity"),      0);
    var fx = L.property("ADBE Effect Parade");
    try {
      var dd = fx.addProperty("ADBE Dropdown Control");
      try { dd.property(1).setValue(CONFIG.dropdownIdx); } catch(e){}
    } catch(e){}
    try {
      var sl = fx.addProperty("ADBE Slider Control");
      sl.name = "ползунок";
      sl.property("ADBE Slider Control-0001").setValue(CONFIG.bpm);
    } catch(e){}
    return L;
  }

  function buildSag1(comp){
    var L = comp.layers.addShape();
    L.name = "sag1 белый";
    L.startTime = 0; L.inPoint = 0; L.outPoint = t(5.30530530530531);

    var root = L.property("ADBE Root Vectors Group");
    var grp  = root.addProperty("ADBE Vector Group"); grp.name = "Эллипс 1";
    var cont = grp.property("ADBE Vectors Group");
    var ell  = cont.addProperty("ADBE Vector Shape - Ellipse");
    setConst(ell.property("ADBE Vector Ellipse Size"),     [400,400]);
    setConst(ell.property("ADBE Vector Ellipse Position"), [0,0]);
    var st = cont.addProperty("ADBE Vector Graphic - Stroke");
    setConst(st.property("ADBE Vector Stroke Color"),   [1,0,0,1]);
    setConst(st.property("ADBE Vector Stroke Width"),   20);
    setConst(st.property("ADBE Vector Stroke Opacity"), 100);

    var tr = L.property("ADBE Transform Group");
    setConst(tr.property("ADBE Anchor Point"), [0,0,0]);
    setConst(tr.property("ADBE Position"),     [540,960,0]);

    var expr =
      'bpm = thisComp.layer("bpm control").effect("ползунок")("Ползунок");\r' +
      'beatDur = 60 / bpm;\r' +
      't = time % beatDur;\r' +
      'smooth = ' + CONFIG.sagSmooth + ';\r' +
      's = ease(t, 0, beatDur * smooth, ' + CONFIG.sagPulseHi + ', ' + CONFIG.sagPulseLo + ');\r' +
      '[s, s]';
    setExpr(tr.property("ADBE Scale"), expr);

    setKeys(tr.property("ADBE Opacity"), [
      {time:3.97898,  val:100},
      {time:4.24424,  val:0}
    ]);
    return L;
  }

  function buildFlah(comp){
    var L = comp.layers.addSolid([0,0,0], "flah", comp.width, comp.height, 1);
    L.startTime = 0; L.inPoint = 0; L.outPoint = t(5.30530530530531);

    setKeys(L.property("ADBE Transform Group").property("ADBE Opacity"), [
      {time:0,        val:0},
      {time:3.80212,  val:0},
      {time:3.97896,  val:100},
      {time:4.46529,  val:0}
    ]);

    var fx = L.property("ADBE Effect Parade");
    var e = fx.addProperty("ADBE ELLIPSE");
    setEffParam(e, "ADBE ELLIPSE-0001", [540,960]);
    setEffKeys (e, "ADBE ELLIPSE-0002", [
      {time:3.97896, val:50}, {time:4.24423, val:2000}]);
    setEffKeys (e, "ADBE ELLIPSE-0003", [
      {time:3.97896, val:50}, {time:4.24423, val:2000}]);
    setEffParam(e, "ADBE ELLIPSE-0004", 20);
    setEffParam(e, "ADBE ELLIPSE-0005", 1);
    setEffParam(e, "ADBE ELLIPSE-0006", [1,1,1,1]);
    setEffParam(e, "ADBE ELLIPSE-0007", [1,1,1,1]);
    setEffParam(e, "ADBE ELLIPSE-0008", 0);
    return L;
  }

  function buildAdjustment(comp){
    var L = comp.layers.addSolid([1,1,1], "Корректирующий слой 1",
                                 comp.width, comp.height, 1);
    L.adjustmentLayer = true;
    L.startTime = 0; L.inPoint = 0; L.outPoint = t(4.3043043043043);

    var fx = L.property("ADBE Effect Parade");
    try {
      var g = fx.addProperty("S_Glow");
      setEffParam(g, "S_Glow-0050", 20);
      setEffParam(g, "S_Glow-0052", 0.1);
      setEffParam(g, "S_Glow-0054", 179);
      setEffParam(g, "S_Glow-0055", 1.12);
      setEffParam(g, "S_Glow-0056", 0.85);
      setEffParam(g, "S_Glow-0057", 1.0);
      setEffParam(g, "S_Glow-0058", 1.2);
      setEffParam(g, "S_Glow-0059", 1.4);
      setEffParam(g, "S_Glow-0061", 1);
      setEffParam(g, "S_Glow-0065", 1);
    } catch(e){}
    return L;
  }

  function buildHoldText(comp){
    var L = comp.layers.addText(CONFIG.textHold);
    L.name = "ТАПАЙ  НАД ЭКРАНОМ В ТАКТ";
    L.startTime = 0; L.inPoint = 0; L.outPoint = t(5.30530530530531);

    var srcProp = L.property("ADBE Text Properties").property("ADBE Text Document");
    var td = srcProp.value; styleText(td, CONFIG.textHold); srcProp.setValue(td);

    var tr = L.property("ADBE Transform Group");
    setConst(tr.property("ADBE Position"), [540,325.000002235174,0]);
    setKeys(tr.property("ADBE Opacity"), [
      {time:3.30328, val:100},
      {time:3.56855, val:0}
    ]);

    var animers = L.property("ADBE Text Properties").property("ADBE Text Animators");
    var anim = animers.addProperty("ADBE Text Animator");
    try { anim.property("ADBE Text Animator Properties")
              .addProperty("ADBE Text Opacity").setValue(0); } catch(e){}
    var sel = anim.property("ADBE Text Selectors").addProperty("ADBE Text Selector");
    try {
      sel.property("ADBE Text Range Advanced")
         .property("ADBE Text Range Type2").setValue(3);
    } catch(e){
      try { sel.property("ADBE Text Range Type2").setValue(3); } catch(e2){}
    }
    setKeys(sel.property("ADBE Text Percent Start"), [
      {time:0,       val:0},
      {time:0.86754, val:100}
    ]);

    var fx = L.property("ADBE Effect Parade");
    var mm = fx.addProperty("ADBE Minimax");
    setEffParam(mm, "ADBE Minimax-0001", 2);
    setEffParam(mm, "ADBE Minimax-0003", 2);
    setEffKeys (mm, "ADBE Minimax-0002", [
      {time:0,       val:15},
      {time:0.17684, val:0}
    ]);
    return L;
  }

  function buildReleaseText(comp){
    var L = comp.layers.addText(CONFIG.textRelease);
    L.name = "ОТПУСКАЙ!";
    L.startTime = 0; L.inPoint = t(3.53687020353687); L.outPoint = t(5.30530530530531);

    var srcProp = L.property("ADBE Text Properties").property("ADBE Text Document");
    var td = srcProp.value; styleText(td, CONFIG.textRelease); srcProp.setValue(td);

    var tr = L.property("ADBE Transform Group");
    setConst(tr.property("ADBE Position"), [540,325.000002235174,0]);
    setKeys(tr.property("ADBE Opacity"), [
      {time:3.97896, val:100},
      {time:4.24423, val:0}
    ]);

    var fx = L.property("ADBE Effect Parade");
    var mm = fx.addProperty("ADBE Minimax");
    setEffParam(mm, "ADBE Minimax-0001", 2);
    setEffParam(mm, "ADBE Minimax-0003", 2);
    setEffParam(mm, "ADBE Minimax-0004", 1);
    setEffKeys (mm, "ADBE Minimax-0002", [
      {time:0,       val:15},
      {time:0.17684, val:0},
      {time:3.53685, val:15},
      {time:3.60358, val:0}
    ]);
    return L;
  }

  app.beginUndoGroup("F4 tap overlay");
  try {
    buildBlackSolid(comp);
    buildBpmControl(comp);   // before sag1 so its expression resolves the slider
    buildSag1(comp);
    buildFlah(comp);
    buildAdjustment(comp);
    buildHoldText(comp);
    buildReleaseText(comp);
    try { $.writeln("[F4][__F4_DEVICE__] overlay built bpm=" + CONFIG.bpm); } catch(_){}
  } catch(err){
    try { $.writeln("[F4][__F4_DEVICE__] ERROR " + err.toString()); } catch(_){}
  } finally {
    app.endUndoGroup();
  }
})(MAIN_COMP);
