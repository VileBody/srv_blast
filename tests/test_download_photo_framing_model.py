from pathlib import Path

from scripts.download_photo_framing_model import MODEL_SHA256, _sha256


def test_sha256_helper(tmp_path: Path) -> None:
    sample = tmp_path / "sample.bin"
    sample.write_bytes(b"blast-photo-framing")
    assert _sha256(sample) == "f9884511a7aa8cf15ffe49572a7dac4ba9599fbe72d0b1b2b7e544a25b44faa3"
    assert len(MODEL_SHA256) == 64
