from __future__ import annotations

from pathlib import Path


_APP_PATH = Path(__file__).resolve().parents[1] / "services" / "tg_bot_public" / "app.py"
_TEAM_APP_PATH = Path(__file__).resolve().parents[1] / "services" / "tg_bot_botapi" / "app.py"
_PUBLIC_SS_PATH = Path(__file__).resolve().parents[1] / "services" / "tg_bot_public" / "state_store.py"
_TEAM_SS_PATH = Path(__file__).resolve().parents[1] / "services" / "tg_bot_botapi" / "state_store.py"


def _app_source() -> str:
    return _APP_PATH.read_text(encoding="utf-8")


def _team_app_source() -> str:
    return _TEAM_APP_PATH.read_text(encoding="utf-8")


# ── existing reuse-input tests ────────────────────────────────────────────────

def test_reuse_input_button_wired_in_wait_audio_source() -> None:
    src = _app_source()
    assert 'BTN_REUSE_INPUT = "Сделать под тот же трек"' in src
    assert "if text == BTN_REUSE_INPUT:" in src
    assert "await self._ask_footage_genre(message, st)" in src


def test_reset_processing_state_does_not_drop_fragment_or_timing_source() -> None:
    src = _app_source()
    start = src.index("def _reset_processing_state(")
    tail = src[start:]
    end = tail.index("async def _send_long_html_message(")
    reset_body = tail[:end]

    assert "st.target_fragment = \"\"" not in reset_body
    assert "st.user_clip_start_sec = 0.0" not in reset_body
    assert "st.user_clip_end_sec = 0.0" not in reset_body


def test_can_reuse_input_checks_file_id_and_prepared_path_source() -> None:
    src = _app_source()
    assert "def _can_reuse_input(st: ChatState) -> bool:" in src
    assert "if str(st.pending_audio_file_id or \"\").strip():" in src
    assert "Path(prepared_raw).expanduser().resolve().exists()" in src


# ── bigtest parity: public bot must carry the flag + stub ────────────────────

def test_bigtest_disabled_in_public_bot_source() -> None:
    src = _app_source()
    assert "BIGTEST_ENABLED: bool = False" in src, (
        "Public bot must declare BIGTEST_ENABLED = False for parity"
    )


def test_bigtest_stub_handler_registered_in_public_bot_source() -> None:
    src = _app_source()
    assert 'Command("bigtest")' in src, (
        "/bigtest handler must be registered in public bot (parity)"
    )
    assert "Эта команда недоступна" in src, (
        "Public bot /bigtest stub must reply with rejection message"
    )


def test_bigtest_state_fields_in_public_state_store() -> None:
    src = _PUBLIC_SS_PATH.read_text(encoding="utf-8")
    for field in ("bigtest_mode", "bigtest_index", "bigtest_total",
                  "bigtest_current_label", "bigtest_master_job_id"):
        assert field in src, f"Public ChatState missing bigtest field: {field}"


# ── team bot: reuse-input wired in _handle_wait_next ─────────────────────────

def test_team_bot_reuse_input_in_wait_next_source() -> None:
    src = _team_app_source()
    assert "BTN_REUSE_INPUT" in src
    assert "await self._ask_bg_mode(message, st)" in src, (
        "Team bot reuse flow must go to _ask_bg_mode (not footage_genre)"
    )


def test_team_bot_batch_completion_restores_audio_s3_url_source() -> None:
    src = _team_app_source()
    assert "_saved_audio_s3 = str(st.batch_audio_s3_url or \"\")" in src, (
        "batch completion must save batch_audio_s3_url before _reset_processing_state"
    )
    assert "st.batch_audio_s3_url = _saved_audio_s3" in src, (
        "batch completion must restore batch_audio_s3_url after reset for /bigtest reuse"
    )


def test_team_bot_bigtest_cases_count_source() -> None:
    src = _team_app_source()
    assert "_BIGTEST_CASES" in src
    # 28 cases: count dict entries by label keys
    label_count = src.count('"label":')
    assert label_count >= 28, f"Expected at least 28 bigtest cases, found {label_count}"


def test_team_bot_bigtest_state_fields_in_state_store() -> None:
    src = _TEAM_SS_PATH.read_text(encoding="utf-8")
    for field in ("bigtest_mode", "bigtest_index", "bigtest_total",
                  "bigtest_current_label", "bigtest_master_job_id"):
        assert field in src, f"Team ChatState missing bigtest field: {field}"


# ── bigtest LLM-reuse roll-forward (fix: bigtest_master_job_id updated per case)

def test_team_bot_bigtest_promotes_master_job_after_each_case_source() -> None:
    """After each completed bigtest case, bigtest_master_job_id must be promoted
    to the just-finished job's id so LLM stages run at most once per bigtest run."""
    src = _team_app_source()
    # The roll-forward assignment must exist inside the bigtest batch-completion path
    assert "st.bigtest_master_job_id = _saved_master_job_id" in src, (
        "Team bot must promote bigtest_master_job_id to the last completed job id "
        "so subsequent cases reuse its resume_state instead of re-running LLM."
    )


def test_team_bot_bigtest_guard_distinguishes_reuse_source_source() -> None:
    """The /bigtest startup message must distinguish between 'has master_job_id
    (no LLM)' and 'no master_job_id (first case will run LLM)'."""
    src = _team_app_source()
    assert "Кейс 1 переиспользует resume_state" in src, (
        "When master_job_id is set, /bigtest must tell the operator LLM won't run."
    )
    assert "Кейс 1 прогонит ASR" in src, (
        "When master_job_id is absent, /bigtest must warn that first case runs LLM."
    )


# ── bigtest full-reuse fix (footage + subtitles_mode) ────────────────────────

def test_bigtest_footage_seed_in_team_state_store() -> None:
    src = _TEAM_SS_PATH.read_text(encoding="utf-8")
    assert "bigtest_footage_seed" in src, (
        "Team ChatState must have bigtest_footage_seed field for STAGE2_SELECTION_SEED pinning"
    )


def test_bigtest_footage_seed_in_public_state_store() -> None:
    src = _PUBLIC_SS_PATH.read_text(encoding="utf-8")
    assert "bigtest_footage_seed" in src, (
        "Public ChatState must have bigtest_footage_seed field for schema parity"
    )


def test_schemas_has_reuse_stage2_footage_field() -> None:
    schemas_path = (
        Path(__file__).resolve().parents[1]
        / "services" / "orchestrator" / "schemas.py"
    )
    src = schemas_path.read_text(encoding="utf-8")
    assert "reuse_stage2_footage" in src, (
        "SendAudioS3Request must have reuse_stage2_footage field"
    )
    assert "stage2_selection_seed_override" in src, (
        "SendAudioS3Request must have stage2_selection_seed_override field"
    )


def test_team_bot_bigtest_preserves_subtitles_mode_source() -> None:
    """subtitles_mode must be saved before _reset_processing_state and restored
    after, so every bigtest case uses the same mode as case-0 instead of the
    LEGACY_BLOCKS default that _reset_processing_state writes."""
    src = _team_app_source()
    assert '_saved_subtitles_mode = str(st.subtitles_mode or "")' in src, (
        "Bigtest batch completion must save subtitles_mode before _reset_processing_state"
    )
    assert "st.subtitles_mode = _saved_subtitles_mode" in src, (
        "Bigtest batch completion must restore subtitles_mode after _reset_processing_state"
    )


def test_team_bot_bigtest_sets_footage_seed_after_case0_source() -> None:
    """After case-0 enqueues, st.bigtest_footage_seed must be populated so
    cases 1-27 can reuse the same STAGE2_SELECTION_SEED."""
    src = _team_app_source()
    assert 'st.bigtest_footage_seed = f"{new_batch_id}:v1"' in src, (
        "Team bot must store bigtest_footage_seed = f'{new_batch_id}:v1' after case-0 enqueues"
    )


def test_team_bot_bigtest_cases_pass_reuse_stage2_footage_source() -> None:
    """Cases 1-27 must pass reuse_stage2_footage=True so stage2_style /
    stage2_style_rotation are copied alongside the text resume state."""
    src = _team_app_source()
    assert "reuse_stage2_footage=(idx > 0)" in src, (
        "Team bot must pass reuse_stage2_footage=(idx > 0) for bigtest cases"
    )


def test_team_bot_bigtest_cases_pass_selection_seed_override_source() -> None:
    """Cases 1-27 must forward st.bigtest_footage_seed as
    stage2_selection_seed_override so footage_picker uses identical clips."""
    src = _team_app_source()
    assert "st.bigtest_footage_seed" in src, (
        "Team bot must reference st.bigtest_footage_seed when building bigtest enqueue calls"
    )
    assert "stage2_selection_seed_override" in src, (
        "Team bot must pass stage2_selection_seed_override to _enqueue_batch_version"
    )
