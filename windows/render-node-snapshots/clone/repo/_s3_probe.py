from pathlib import Path
import traceback
from s3_utils import download_file_from_s3
b='f7cef916-job-artifacts'
k='ae_jobs_artifacts/replay_b5e0fd06_1775326788/app/media/video/14777505023070773.mp4'
o=Path(r'C:\ae_jobs\_s3_probe.mp4')
o.parent.mkdir(parents=True, exist_ok=True)
try:
    download_file_from_s3(bucket=b,key=k,dest=o)
    print('S3_PROBE_OK', o.exists(), o.stat().st_size if o.exists() else -1)
except Exception as e:
    print('S3_PROBE_ERR', repr(e))
    traceback.print_exc()
