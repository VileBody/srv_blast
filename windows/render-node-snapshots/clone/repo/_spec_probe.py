import time
import traceback
from ae_sdk import make_job_spec_from_payload
import main  # loads .env via load_dotenv

payload = {
  "job_id": "specprobe_hist_086caca6",
  "render_jsx_s3_uri": "s3://f7cef916-job-artifacts/jobs/086caca6c8d1445294fbb1bad25eb864/render_full.jsx",
  "render_payload_s3_uri": "s3://f7cef916-job-artifacts/jobs/086caca6c8d1445294fbb1bad25eb864/final_render_instructions_full.json",
  "audio_url": "s3://f7cef916-raw-audio/010ab7f2e5254b14b81bceb6c5ee57b5.m4a",
  "entry_comp": "Comp 1",
  "output_relpath": "work/output.mp4",
  "output_s3_bucket": "f7cef916-output-video",
  "output_s3_key": "renders/specprobe_hist_086caca6/output.mp4",
}

start = time.time()
print("START")
try:
    spec = make_job_spec_from_payload(payload)
    print("OK elapsed", round(time.time()-start,2))
    print("spec_job", spec.job_id)
    print("spec_media_count", len(spec.media_files))
    if spec.media_files:
        print("media0", spec.media_files[0].relpath, spec.media_files[0].url[:120])
except Exception as e:
    print("ERR", repr(e), "elapsed", round(time.time()-start,2))
    traceback.print_exc()
