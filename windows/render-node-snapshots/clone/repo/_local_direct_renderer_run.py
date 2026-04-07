
import json
import time
import sys
import traceback
from pathlib import Path

if len(sys.argv) < 2:
    raise SystemExit("usage: _local_direct_renderer_run.py <job_id>")
job_id = str(sys.argv[1]).strip()
if not job_id:
    raise SystemExit("empty job_id")

log_path = Path(r"C:\ae_dev\logs") / f"{job_id}.direct.log"
log_path.parent.mkdir(parents=True, exist_ok=True)

def log(msg: str):
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

payload = {
  "job_id": job_id,
  "render_jsx_s3_uri": "s3://f7cef916-job-artifacts/jobs/086caca6c8d1445294fbb1bad25eb864/render_full.jsx",
  "render_payload_s3_uri": "s3://f7cef916-job-artifacts/jobs/086caca6c8d1445294fbb1bad25eb864/final_render_instructions_full.json",
  "audio_url": "s3://f7cef916-raw-audio/010ab7f2e5254b14b81bceb6c5ee57b5.m4a",
  "entry_comp": "Comp 1",
  "output_relpath": "work/output.mp4",
  "output_s3_bucket": "f7cef916-output-video",
  "output_s3_key": f"renders/{job_id}/output.mp4",
}

log(f"START job_id={job_id}")
log("PAYLOAD=" + json.dumps(payload, ensure_ascii=False))

try:
    t0 = time.time()
    import main  # load .env + renderer singleton
    from ae_sdk import make_job_spec_from_payload
    log(f"IMPORT_DONE elapsed={time.time()-t0:.2f}")

    t1 = time.time()
    spec = make_job_spec_from_payload(payload)
    log(f"SPEC_DONE elapsed={time.time()-t1:.2f} media_count={len(spec.media_files)}")

    t2 = time.time()
    res = main.renderer.run_job(spec)
    log(f"RUN_DONE elapsed={time.time()-t2:.2f} success={res.success}")
    log("RESULT=" + json.dumps({
        "job_id": res.job_id,
        "success": bool(res.success),
        "message": str(res.message),
        "app_dir": str(res.app_dir),
        "output_path": str(res.output_path) if res.output_path else None,
        "output_s3_url": str(res.output_s3_url) if res.output_s3_url else None,
        "artifacts_s3_uri": str(res.artifacts_s3_uri) if getattr(res, 'artifacts_s3_uri', None) else None,
    }, ensure_ascii=False))
except Exception as e:
    log(f"EXC={e!r}")
    log(traceback.format_exc())
    raise

print(f"JOB_ID={job_id}")
print(f"LOG={str(log_path)}")

