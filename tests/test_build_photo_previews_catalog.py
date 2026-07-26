from scripts.build_bucket_previews import _catalog_for_media


def test_photo_preview_builder_uses_standalone_photo_catalog():
    catalog = _catalog_for_media("photo")

    assert len(catalog) == 17
    assert all(bucket.bucket_id.startswith("photo:") for bucket in catalog)
