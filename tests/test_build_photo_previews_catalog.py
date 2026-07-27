from scripts.build_bucket_previews import _catalog_for_media


def test_photo_preview_builder_uses_standalone_photo_catalog():
    """Photo previews must be driven by the PHOTO catalog, never the video one —
    the two planes are deliberately separate. Sized against the catalog itself so
    a reviewed re-cut of the bucket set does not break the test."""
    from mlcore.photo_bucket_catalog import load_photo_catalog

    catalog = _catalog_for_media("photo")

    assert [b.bucket_id for b in catalog] == [b.bucket_id for b in load_photo_catalog()]
    assert all(bucket.bucket_id.startswith("photo:") for bucket in catalog)
