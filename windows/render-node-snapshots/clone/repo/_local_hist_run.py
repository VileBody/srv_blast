import json
import time
import requests

job_id = r\"hist_local_086caca6_20260405195454\"
log_path = r\"C:\\ae_dev\\logs\\hist_local_20260405195454.log\"
payload = {
  \"job_id\": job_id,
  \"render_jsx_s3_uri\": \"s3://f7cef916-job-artifacts/jobs/086caca6c8d1445294fbb1bad25eb864/render_full.jsx\",
  \"render_payload_s3_uri\": \"s3://f7cef916-job-artifacts/jobs/086caca6c8d1445294fbb1bad25eb864/final_render_instructions_full.json\",
  \"audio_url\": \"s3://f7cef916-raw-audio/010ab7f2e5254b14b81bceb6c5ee57b5.m4a\",
  \"entry_comp\": \"Comp 1\",
  \"output_relpath\": \"work/output.mp4\",
  \"output_s3_bucket\": \"f7cef916-output-video\",
  \"output_s3_key\": f\"renders/{job_id}/output.mp4\",
}

with open(log_path, \"w\", encoding=\"utf-8\") as f:
    f.write(f\"START job_id={job_id}\\n\")
    f.write(\"PAYLOAD=\" + json.dumps(payload, ensure_ascii=False) + \"\\n\")
    f.flush()
    t0 = time.time()
    try:
        r = requests.post(\"http://127.0.0.1:8000/jobs\", json=payload, timeout=3600)
        f.write(f\"HTTP={r.status_code} elapsed={time.time()-t0:.2f}\\n\")
        f.write((r.text or \"\")[:20000] + \"\\n\")
    except Exception as e:
        f.write(f\"EXC={e!r} elapsed={time.time()-t0:.2f}\\n\")
