/*** layer_shake (transition, clip_transform) — панч-влёт на КАЖДЫЙ клип. Авто-каркас ***/
var CONFIG = { targetCompName:null, placeRef:"Текст" };
var SILENT = true;
if (typeof $!=="undefined" && $.global && $.global.__BLAST){ var __p=$.global.__BLAST; for (var __k in __p){ if (__p[__k]!=null) CONFIG[__k]=__p[__k]; } }
var CFG = { intro_dur:0.63, outro_dur:0.63, min_hold:0.10, rot_peak1:-2.31, rot_peak2:2.35, rot_damp:-0.68,
            scale_from:265, scale_to:100, pos_y_offset:242, blur_idle:0.5, blur_peak:0.8 };

function log(m){ if(SILENT){try{$.writeln(m);}catch(e){}}else alert(m); }
function findLayer(c,n){ for(var i=1;i<=c.numLayers;i++) if(c.layer(i).name===n) return c.layer(i); return null; }
function findComp(){ var a=app.project.activeItem,i,it;
  if(CONFIG.targetCompName){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&it.name===CONFIG.targetCompName)return it;}}
  if(CONFIG.placeRef){for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&findLayer(it,CONFIG.placeRef))return it;}}
  if(a&&a instanceof CompItem)return a; var b=null;for(i=1;i<=app.project.numItems;i++){it=app.project.item(i);if(it instanceof CompItem&&(!b||it.numLayers>b.numLayers))b=it;} return b; }
function addKey(p,t,v){ p.setValueAtTime(t,v); return p.nearestKeyIndex(t); }
function setEase(p,idx,iI,oI,iS,oS){ p.setTemporalEaseAtKey(idx,[new KeyframeEase(iS||0,iI||33.33)],[new KeyframeEase(oS||0,oI||33.33)]); }
function calcTiming(clip){ var i=CFG.intro_dur,o=CFG.outro_dur,h=CFG.min_hold;
  if(clip>=i+o+h)return{mode:"full",i_dur:i,o_dur:o}; if(clip>=i+h)return{mode:"intro_only",i_dur:i,o_dur:0};
  if(clip>=h*2)return{mode:"intro_only",i_dur:clip-h,o_dur:0}; return null; }

function applyShake(layer,comp){
  var fps=comp.frameRate,frame=1/fps,t0=layer.inPoint,t1=layer.outPoint,clip=t1-t0;
  var tm=calcTiming(clip); if(!tm)return false;
  var i_dur=tm.i_dur,o_dur=tm.o_dur,do_outro=(tm.mode==="full");
  var intro_end=t0+i_dur,outro_start=t1-o_dur;
  var s_in=t0+frame*2,s_in_end=t0+i_dur*0.95;
  var r_in1=t0+i_dur*0.13,r_in2=t0+i_dur*0.26,r_in3=t0+i_dur*0.46,r_in4=t0+i_dur*0.79;
  var b_in1=t0+frame,b_in2=t0+i_dur*0.40;
  var s_out_end=outro_start+o_dur*0.97,r_out1=outro_start+o_dur*0.21,r_out2=outro_start+o_dur*0.33,r_out3=outro_start+o_dur*0.53,r_out4=outro_start+o_dur*0.73,b_out1=outro_start+o_dur*0.35,b_out2=outro_start+o_dur*0.97;
  var cxC=comp.width/2,cyC=comp.height/2,cx,cy; try{cx=layer.source.width/2;cy=layer.source.height/2;}catch(e){cx=cxC;cy=cyC;}
  var bounce=["n=0;","if(numKeys>0){","  n=nearestKey(time).index;","  if(key(n).time>time)n--;","}","t=(n>0)?time-key(n).time:0;","if(n>0&&t<1){","  v=velocityAtTime(key(n).time-thisComp.frameDuration/10);","  value+v*0.02*Math.sin(2*t*2*Math.PI)/Math.exp(6*t);","}else{value;}"].join("\n");

  var curves=layer.Effects.addProperty("ADBE CurvesCustom"); curves.name="Cinematic Curves";
  var tr1=layer.Effects.addProperty("ADBE Geometry2"); tr1.name="Shake Rotation"; tr1.property("ADBE Geometry2-0001").setValue([cx,cy]);
  var rot=tr1.property("ADBE Geometry2-0007");
  if(i_dur>frame*5){
    addKey(rot,s_in,0); setEase(rot,rot.nearestKeyIndex(s_in),16.67,16.67);
    addKey(rot,r_in1,CFG.rot_peak1); setEase(rot,rot.nearestKeyIndex(r_in1),33.33,33.33);
    addKey(rot,r_in2,CFG.rot_peak2); setEase(rot,rot.nearestKeyIndex(r_in2),33.33,33.33);
    addKey(rot,r_in3,CFG.rot_damp); setEase(rot,rot.nearestKeyIndex(r_in3),71.84,33.33);
    addKey(rot,r_in4,0); setEase(rot,rot.nearestKeyIndex(r_in4),92.59,16.67,0.149,1.528);
  }
  if(do_outro&&o_dur>frame*5){
    addKey(rot,outro_start,0); setEase(rot,rot.nearestKeyIndex(outro_start),92.59,92.59,0.149,-0.149);
    addKey(rot,r_out1,CFG.rot_damp); setEase(rot,rot.nearestKeyIndex(r_out1),33.33,71.84,0,-0.156);
    addKey(rot,r_out2,CFG.rot_peak2); setEase(rot,rot.nearestKeyIndex(r_out2),33.33,33.33);
    addKey(rot,r_out3,CFG.rot_peak1); setEase(rot,rot.nearestKeyIndex(r_out3),33.33,33.33);
    addKey(rot,r_out4,0); setEase(rot,rot.nearestKeyIndex(r_out4),16.67,16.67);
  }
  var tr2=layer.Effects.addProperty("ADBE Geometry2"); tr2.name="Shake Scale+Position"; tr2.property("ADBE Geometry2-0001").setValue([cx,cy]);
  var scl=tr2.property("ADBE Geometry2-0003");
  addKey(scl,s_in,CFG.scale_from); setEase(scl,scl.nearestKeyIndex(s_in),33.33,0.68,0,-37333.57);
  addKey(scl,s_in_end,CFG.scale_to); setEase(scl,scl.nearestKeyIndex(s_in_end),100,16.67);
  if(do_outro){
    addKey(scl,intro_end,CFG.scale_to); setEase(scl,scl.nearestKeyIndex(intro_end),100,100);
    addKey(scl,outro_start,CFG.scale_to); setEase(scl,scl.nearestKeyIndex(outro_start),100,100);
    addKey(scl,s_out_end,CFG.scale_from); setEase(scl,scl.nearestKeyIndex(s_out_end),0.68,16.67,37333.57,0);
  }
  var pos=tr2.property("ADBE Geometry2-0002");
  pos.setValueAtTime(s_in,[cx,cy+CFG.pos_y_offset]); var k1=pos.nearestKeyIndex(s_in);
  pos.setSpatialTangentsAtKey(k1,[0,40.33],[0,-40.33]); pos.setTemporalEaseAtKey(k1,[new KeyframeEase(0,16.67)],[new KeyframeEase(5802.19,16.67)]);
  pos.setValueAtTime(s_in+frame,[cx,cy]); var k2=pos.nearestKeyIndex(s_in+frame);
  pos.setSpatialTangentsAtKey(k2,[0,40.33],[0,-40.33]); pos.setTemporalEaseAtKey(k2,[new KeyframeEase(5802.19,16.67)],[new KeyframeEase(0,16.67)]);
  pos.expression=bounce; pos.expressionEnabled=true;
  try{ var blur=layer.Effects.addProperty("S_BlurMotion"); blur.name="Zoom Blur"; blur.property("S_BlurMotion-0050").setValue([cxC,cyC]);
    var fz=blur.property("S_BlurMotion-0051");
    addKey(fz,b_in1,CFG.blur_idle); setEase(fz,fz.nearestKeyIndex(b_in1),16.67,7.49);
    addKey(fz,b_in2,CFG.blur_peak); setEase(fz,fz.nearestKeyIndex(b_in2),100,16.67);
    if(do_outro){ addKey(fz,intro_end,CFG.blur_peak); setEase(fz,fz.nearestKeyIndex(intro_end),100,100);
      addKey(fz,outro_start,CFG.blur_peak); setEase(fz,fz.nearestKeyIndex(outro_start),100,7.49);
      addKey(fz,b_out1,CFG.blur_peak); setEase(fz,fz.nearestKeyIndex(b_out1),100,7.49);
      addKey(fz,b_out2,CFG.blur_idle); setEase(fz,fz.nearestKeyIndex(b_out2),7.49,16.67); }
    blur.property("S_BlurMotion-0055").setValue(CFG.blur_peak); blur.property("S_BlurMotion-0068").setValue(2);
  }catch(e){}
  return true;
}

(function(){ if(!app.project){log("нет проекта");return;} var comp=findComp(); if(!comp){log("нет компа");return;}
  app.beginUndoGroup("layer shake"); var done=0;
  try{ for(var i=1;i<=comp.numLayers;i++){ var L=comp.layer(i);
        var isClip=(L.source&&(L.source instanceof FootageItem)&&L.hasVideo&&!L.adjustmentLayer);
        if(isClip){ try{ if(applyShake(L,comp))done++; }catch(e){ log("shake "+L.name+": "+e); } } }
    log("layer shake: клипов "+done+" -> "+comp.name);
  }catch(e){log("err "+e+" "+(e.line||""));} finally{app.endUndoGroup();} })();
