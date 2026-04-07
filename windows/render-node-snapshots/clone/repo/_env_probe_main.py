import os
print("BEFORE", bool(os.environ.get("S3_ACCESS_KEY_ID")), bool(os.environ.get("S3_SECRET_ACCESS_KEY")), bool(os.environ.get("S3_ENDPOINT_URL")))
print("HASKEY_BEFORE", "S3_ACCESS_KEY_ID" in os.environ, "S3_SECRET_ACCESS_KEY" in os.environ, "S3_ENDPOINT_URL" in os.environ)
import main
print("AFTER", bool(os.environ.get("S3_ACCESS_KEY_ID")), bool(os.environ.get("S3_SECRET_ACCESS_KEY")), bool(os.environ.get("S3_ENDPOINT_URL")))
print("HASKEY_AFTER", "S3_ACCESS_KEY_ID" in os.environ, "S3_SECRET_ACCESS_KEY" in os.environ, "S3_ENDPOINT_URL" in os.environ)
print("VAL_PREFIX", (os.environ.get("S3_ACCESS_KEY_ID") or "")[:4], (os.environ.get("S3_ENDPOINT_URL") or "")[:18])
