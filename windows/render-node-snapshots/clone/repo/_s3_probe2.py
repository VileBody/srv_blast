import os
import time
import traceback
from pathlib import Path
from s3_utils import download_file_from_s3

start = time.time()
print("S3_ENV", bool(os.getenv("S3_ACCESS_KEY_ID")), bool(os.getenv("S3_SECRET_ACCESS_KEY")), bool(os.getenv("S3_ENDPOINT_URL")))
try:
    dest = Path(r"C:\\ae_dev\\repo\\_probe_render_full.jsx")
    download_file_from_s3(bucket="f7cef916-job-artifacts", key="jobs/086caca6c8d1445294fbb1bad25eb864/render_full.jsx", dest=dest)
    print("OK", dest.exists(), dest.stat().st_size, "elapsed", round(time.time() - start, 2))
except Exception as e:
    print("ERR", repr(e), "elapsed", round(time.time() - start, 2))
    traceback.print_exc()
