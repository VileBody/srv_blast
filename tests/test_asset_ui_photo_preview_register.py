from services.orchestrator.asset_routes import _photo_preview_targets


def test_photo_preview_registration_accepts_exact_active_catalog() -> None:
    """Registration must accept exactly the buckets the bot can offer.

    Pinned against the catalog rather than a hardcoded list: the active set is
    reviewed and re-cut regularly (buckets get merged, retired, or split per
    setting), and a frozen copy here only ever goes stale — it would reject a
    freshly approved vibe or accept a retired one.
    """
    from mlcore.photo_bucket_catalog import load_photo_catalog

    active = {bucket.bucket_id for bucket in load_photo_catalog()}
    targets = _photo_preview_targets()

    assert {bucket.bucket_id for bucket in targets.values()} == active
    assert len(targets) == len(active)
    # keyed by the reel file name the register step writes
    for file_name, bucket in targets.items():
        assert file_name == f"{bucket.bucket_id.replace(':', '__')}.mp4"
