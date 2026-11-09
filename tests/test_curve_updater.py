from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo
import pytest

from src.core.models import (
    CalendarEvent,
    PreferenceProfile,
    UpdatedCurveResponse,
)
from src.agent.curve_updater import evolve_curve_with_llm
from src.agent.prompt_templates import build_curve_evolution_prompt

TZ = ZoneInfo("Asia/Kolkata")


def test_build_curve_evolution_prompt():
    prompt = build_curve_evolution_prompt(
        task_title="Guitar practice",
        event_type="routine",
        start_time="2026-11-07 20:00",
        end_time="2026-11-07 20:45",
        duration_minutes=45,
        current_weights_json="[0.0, 0.0, ...]",
        updated_count=0,
    )
    assert "Guitar practice" in prompt
    assert "2026-11-07 20:00" in prompt
    assert "routine" in prompt
    assert "45 minutes" in prompt
    assert "Do not greedily polarize weights" in prompt
    assert "slots too low in weights unless there is no choice" in prompt



def test_evolve_curve_with_mock_llm(tmp_path, monkeypatch):
    test_profiles_dir = tmp_path / "preference_profiles"
    test_categories_dir = test_profiles_dir / "categories"
    test_baseline_path = test_profiles_dir / "global_baseline.json"

    monkeypatch.setattr("src.core.preference_engine.PROFILES_DIR", test_profiles_dir)
    monkeypatch.setattr("src.core.preference_engine.CATEGORIES_DIR", test_categories_dir)
    monkeypatch.setattr("src.core.preference_engine.GLOBAL_BASELINE_PATH", test_baseline_path)

    # Mock Gemini client
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.parsed = UpdatedCurveResponse(
        hourly_weights=[0.0] * 20 + [0.95, 0.5, 0.0, 0.0],
        update_rationale="Reinforced evening hours 20 and 21 for guitar practice.",
    )
    mock_client.models.generate_content.return_value = mock_response

    monkeypatch.setattr("src.agent.curve_updater.genai.Client", lambda api_key: mock_client)

    profile = PreferenceProfile(
        profile_name="guitar_practice",
        updated_count=0,
        hourly_weights=[0.0] * 24,
    )

    event = CalendarEvent(
        id="guitar_1",
        summary="Guitar practice",
        start=datetime(2026, 11, 7, 20, 0, tzinfo=TZ),
        end=datetime(2026, 11, 7, 20, 45, tzinfo=TZ),
    )

    updated = evolve_curve_with_llm(
        profile=profile,
        event=event,
        api_key="mock_key",
    )

    assert updated.profile_name == "guitar_practice"
    assert updated.updated_count == 1
    assert updated.hourly_weights[20] == 0.95
    assert updated.hourly_weights[21] == 0.5
    assert updated.description == "Reinforced evening hours 20 and 21 for guitar practice."

    # File should exist on disk
    saved_file = test_categories_dir / "guitar_practice.json"
    assert saved_file.exists()
