#!/usr/bin/env python3
"""Sequentially render four 2-second style previews in an already-open AE.

Each style uses a different cached bucket clip. The effect is enabled for the
first second and disabled for the second. Every pass removes its render item,
composition and imported footage in a finally block; AE stays open.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AE = Path(r"C:\Program Files\Adobe\Adobe After Effects 2025\Support Files\AfterFX.com")
DEFAULT_OUTPUT = Path(r"C:\Users\Пользователь\Desktop\АЕ\Хуки\Эффекты\stylize\примеры стилей")
STYLES = {
    "blackwhite": ("rebuild_blackwhite.jsx", "blackwhite.mp4"),
    "crystal_glow": ("rebuild_crystal_glow.jsx", "crystalglow.mp4"),
    "night_vision": ("rebuild_night_vision.jsx", "nightvision.mp4"),
    "wave": ("rebuild_wave.jsx", "wave.mp4"),
}


def _js(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _bucket_sources() -> list[Path]:
    roots = [Path(r"C:\ae_jobs"), ROOT / "outputs"]
    found: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
                continue
            if "media" not in {x.lower() for x in p.parts} or "video" not in {x.lower() for x in p.parts}:
                continue
            key = p.name.lower()
            if key in seen or p.stat().st_size < 100_000:
                continue
            seen.add(key); found.append(p)
            if len(found) == len(STYLES):
                return found
    raise RuntimeError(f"need {len(STYLES)} different cached bucket clips; found {len(found)}")


def _jsx(style: str, source: Path, output: Path, status: Path) -> str:
    effect_file, _ = STYLES[style]
    effect = (ROOT / "mlcore" / "hooks" / "f3_effect" / "extra" / effect_file).read_text(encoding="utf-8")
    comp_name = f"style_preview_{style}"
    return f'''(function(){{
var comp=null,footage=null,rqItem=null;
function status(kind,msg){{var f=new File({_js(status.as_posix())});f.open("w");f.write(kind+"\\n"+msg);f.close();}}
function staleCleanup(){{
  var rq=app.project.renderQueue;
  for(var r=rq.numItems;r>=1;r--){{try{{var rc=rq.item(r).comp;if(rc&&String(rc.name).indexOf("style_preview_")===0)rq.item(r).remove();}}catch(e){{}}}}
  for(var i=app.project.numItems;i>=1;i--){{try{{var it=app.project.item(i);if(it&&String(it.name).indexOf("style_preview_")===0)it.remove();}}catch(e2){{}}}}
}}
try{{
  app.beginSuppressDialogs();
  if(!app.project)throw new Error("After Effects project is not open");
  staleCleanup();
  footage=app.project.importFile(new ImportOptions(new File({_js(source.as_posix())})));
  comp=app.project.items.addComp({_js(comp_name)},1080,1920,1,2,23.976);
  var src=comp.layers.add(footage);src.startTime=0;src.inPoint=0;src.outPoint=2;if(src.hasAudio)src.audioEnabled=false;
  var scale=Math.max(comp.width/footage.width,comp.height/footage.height)*100;src.property("Scale").setValue([scale,scale]);src.property("Position").setValue([540,960]);
  $.global.__BLAST={{targetCompName:{_js(comp_name)},placeRef:null,startTime:0,duration:1,place:"top"}};
  (function(){{
{effect}
  }})();
  $.global.__BLAST=null;
  rqItem=app.project.renderQueue.items.add(comp);
  var om=rqItem.outputModule(1),picked="",ts=om.templates;
  for(var t=0;t<ts.length;t++){{if(/h\\.?264|mp4|264/i.test(String(ts[t]))){{picked=ts[t];break;}}}}
  if(!picked)throw new Error("No H.264/MP4 output module template");
  om.applyTemplate(picked);om.file=new File({_js(output.as_posix())});
  app.endSuppressDialogs(false);
  app.project.renderQueue.render();
  if(!om.file.exists||om.file.length<=0)throw new Error("render produced no output");
  status("OK",{_js(style)});
}}catch(err){{
  try{{app.endSuppressDialogs(false);}}catch(e3){{}}
  status("ERROR",err.toString());
}}finally{{
  $.global.__BLAST=null;
  try{{if(rqItem)rqItem.remove();}}catch(e4){{}}
  try{{if(comp)comp.remove();}}catch(e5){{}}
  try{{if(footage)footage.remove();}}catch(e6){{}}
}}
}})();
'''


def _wait(status: Path, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if status.exists():
            lines = status.read_text(encoding="utf-8", errors="replace").splitlines()
            if lines and lines[0] == "OK":
                return
            if lines:
                raise RuntimeError("AE: " + " ".join(lines[1:]))
        time.sleep(1)
    raise TimeoutError(f"AE preview timed out after {timeout}s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--sources", nargs=4, type=Path, metavar=("BW", "CRYSTAL", "NIGHT", "WAVE"))
    ap.add_argument("--timeout", type=float, default=600)
    args = ap.parse_args()
    if not AE.is_file():
        raise SystemExit(f"AfterFX.com not found: {AE}")
    output_dir = args.output_dir.resolve(); output_dir.mkdir(parents=True, exist_ok=True)
    sources = [p.resolve() for p in args.sources] if args.sources else _bucket_sources()
    if len({str(p).lower() for p in sources}) != 4 or not all(p.is_file() for p in sources):
        raise SystemExit("four different existing source clips are required")
    work = ROOT / ".preview_work"; staged = work / "sources"; rendered = work / "rendered"
    staged.mkdir(parents=True, exist_ok=True); rendered.mkdir(parents=True, exist_ok=True)
    for index, ((style, (_, output_name)), source) in enumerate(zip(STYLES.items(), sources), 1):
        staged_source = staged / f"source_{index}{source.suffix.lower()}"
        shutil.copy2(source, staged_source)
        temp_output = rendered / output_name
        status = work / f"status_{style}.txt"; jsx = work / f"render_{style}.jsx"
        status.unlink(missing_ok=True); temp_output.unlink(missing_ok=True)
        jsx.write_text(_jsx(style, staged_source, temp_output, status), encoding="utf-8")
        subprocess.run([str(AE), "-r", str(jsx)], cwd=str(work), check=True)
        _wait(status, args.timeout)
        if not temp_output.is_file() or temp_output.stat().st_size <= 0:
            raise RuntimeError(f"missing preview: {temp_output}")
        shutil.copy2(temp_output, output_dir / output_name)
        print(f"{style}: {source.name} -> {output_dir / output_name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())