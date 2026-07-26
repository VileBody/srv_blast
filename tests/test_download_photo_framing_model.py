from pathlib import Path

from scripts.download_photo_framing_model import MODEL_SHA256, _sha256


def test_sha256_helper(tmp_path: Path) -> None:
    sample = tmp_path / "sample.bin"
    sample.write_bytes(b"blast-photo-framing")
    assert _sha256(sample) == "f9884511a7aa8cf15ffe49572a7dac4ba9599fbe72d0b1b2b7e544a25b44faa3"
    assert len(MODEL_SHA256) == 64

def test_runtime_model_path_is_not_hidden_by_data_bind_mount() -> None:
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    assert "PHOTO_FRAMING_MODEL_PATH=/app/models/" in dockerfile
    assert "PHOTO_FRAMING_MODEL_PATH=/app/data/" not in dockerfile
    assert "RUN python scripts/download_photo_framing_model.py" in dockerfile
