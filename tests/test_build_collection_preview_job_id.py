from types import SimpleNamespace

from mlcore.footage_collection_catalog import CollectionBucket
from scripts import build_bucket_previews as build


def _rendered_job_id(monkeypatch, seed: str) -> str:
    bucket = CollectionBucket(
        slug="films__бойцовский клуб",
        label="Бойцовский клуб",
        kind="films",
        folder="бойцовский клуб",
    )
    clips = [{"file_name": f"clip-{index}.mp4"} for index in range(5)]
    captured = {}

    monkeypatch.setattr(build.bp, "select_bucket_clips", lambda *a, **k: clips)
    monkeypatch.setattr(build, "_filter_clips_in_s3", lambda *a, **k: clips)
    monkeypatch.setattr(build.bp, "build_collection_montage_spec", lambda *a, **k: {})
    monkeypatch.setattr(build.bp, "render_montage_jsx", lambda *a, **k: "jsx")
    monkeypatch.setattr(build.bp, "montage_media_payload", lambda *a, **k: [])
    monkeypatch.setattr(build, "_montage_template_text", lambda *a, **k: "template")
    monkeypatch.setattr(build, "_output_s3_target", lambda *a, **k: ("bucket", "prefix"))

    def render(**kwargs):
        captured["job_id"] = kwargs["job_id"]
        return "https://example.invalid/preview.mp4"

    monkeypatch.setattr(build, "_render_via_node", render)
    args = SimpleNamespace(
        seed=seed,
        top_n=5,
        min_clips=3,
        dry_run=False,
        local_footage_dir=None,
        media="collection",
        render_preset="wide",
        no_s3_check=True,
        node_url="http://render.invalid",
        render_timeout_s=60,
        poll_s=1,
        no_telegram=True,
    )

    build.build_one_bucket(bucket, mapped_assets=[], url_by_fn={}, args=args)
    return captured["job_id"]


def test_collection_preview_job_id_is_ascii_and_retry_distinct(monkeypatch) -> None:
    first = _rendered_job_id(monkeypatch, "preview-v1")
    retry = _rendered_job_id(monkeypatch, "preview-v1-retry")

    assert first.startswith("bucketprev_")
    assert first.isascii()
    assert retry != first
