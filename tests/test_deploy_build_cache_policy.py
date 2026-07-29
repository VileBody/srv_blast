from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "infra" / "runners" / "deploy_branch.sh"


def test_infra_deploy_keeps_bounded_builder_cache() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'DEPLOY_BUILD_CACHE_MAX_USED_SPACE="${DEPLOY_BUILD_CACHE_MAX_USED_SPACE:-12gb}"' in source
    assert 'if [[ "$DEPLOY_STACK" == "infra-ops" ]]; then' in source
    assert '--max-used-space "$DEPLOY_BUILD_CACHE_MAX_USED_SPACE"' in source
    assert "docker builder prune -af >/dev/null 2>&1 || true" in source
