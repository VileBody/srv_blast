
import json
import time
import sys
from pathlib import Path
import requests

if len(sys.argv) < 2:
    raise SystemExit("usage: _local_hist_run_fixed.py <job_id>")
job_id = str(sys.argv[1]).strip()
if not job_id:
    raise SystemExit("empty job_id")

log_path = Path(r"C:\ae_dev\logs") / f"{job_id}.log"
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
log_path.parent.mkdir(parents=True, exist_ok=True)
with open(log_path, "w", encoding="utf-8") as f:
    f.write(f"START job_id={job_id}\n")
    f.write("PAYLOAD=" + json.dumps(payload, ensure_ascii=False) + "\n")
    f.flush()
    t0 = time.time()
    try:
        r = requests.post("http://127.0.0.1:8000/jobs", json=payload, timeout=3600)
        f.write(f"HTTP={r.status_code} elapsed={time.time()-t0:.2f}\n")
        f.write((r.text or "")[:20000] + "\n")
    except Exception as e:
        f.write(f"EXC={e!r} elapsed={time.time()-t0:.2f}\n")
print(f"JOB_ID={job_id}")
print(f"LOG={str(log_path)}")

