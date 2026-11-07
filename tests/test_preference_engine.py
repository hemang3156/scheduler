import json
from pathlib import Path
import pytest

from src.core.models import PreferenceProfile
from src.core.preference_engine import (
    normalize_profile_name,
    sanitize_weights,
    get_global_baseline,
    load_profile,
    save_profile,
    recompute_global_baseline,
    CATEGORIES_DIR,
    GLOBAL_BASELINE_PATH,
)


def test_normalize_profile_name():
    assert normalize_profile_name("Guitar practice 8pm") == "guitar_practice_8pm"
    assert normalize_profile_name("ML pipeline debug - YOLO!") == "ml_pipeline_debug_yolo"
    assert normalize_profile_name("   Flute @ Practice   ") == "flute_practice"
    assert normalize_profile_name("---") == "unnamed_task"


def test_sanitize_weights():
    # Exactly 24 weights with out-of-bounds numbers
    raw = [1.5] + [-0.2] + [0.333333] + [0.0] * 21
    sanitized = sanitize_weights(raw)
    assert len(sanitized) == 24
    assert sanitized[0] == 1.0
    assert sanitized[1] == 0.0
    assert sanitized[2] == 0.3333

    # Invalid length raises ValueError
    with pytest.raises(ValueError, match="must have exactly 24 elements"):
        sanitize_weights([0.5] * 23)

    with pytest.raises(ValueError, match="must have exactly 24 elements"):
        sanitize_weights([0.5] * 25)


def test_load_and_save_profile(tmp_path, monkeypatch):
    test_categories_dir = tmp_path / "categories"
    monkeypatch.setattr("src.core.preference_engine.CATEGORIES_DIR", test_categories_dir)

    # Initial load for nonexistent task should return cold start profile (updated_count=0)
    profile = load_profile("New Piano Lesson")
    assert profile.profile_name == "new_piano_lesson"
    assert profile.updated_count == 0
    assert len(profile.hourly_weights) == 24

    # Save modified profile
    profile.hourly_weights[18] = 0.85
    profile.updated_count = 1
    saved_path = save_profile(profile)
    assert saved_path.exists()

    # Re-load from disk
    reloaded = load_profile("New Piano Lesson")
    assert reloaded.updated_count == 1
    assert reloaded.hourly_weights[18] == 0.85


def test_recompute_global_baseline(tmp_path, monkeypatch):
    test_profiles_dir = tmp_path / "preference_profiles"
    test_categories_dir = test_profiles_dir / "categories"
    test_baseline_path = test_profiles_dir / "global_baseline.json"

    monkeypatch.setattr("src.core.preference_engine.PROFILES_DIR", test_profiles_dir)
    monkeypatch.setattr("src.core.preference_engine.CATEGORIES_DIR", test_categories_dir)
    monkeypatch.setattr("src.core.preference_engine.GLOBAL_BASELINE_PATH", test_baseline_path)

    test_categories_dir.mkdir(parents=True)

    # Profile A: count 1, weight 1.0 at hour 10
    prof_a = PreferenceProfile(
        profile_name="task_a",
        updated_count=1,
        hourly_weights=[0.0] * 10 + [1.0] + [0.0] * 13,
    )
    save_profile(prof_a)

    # Profile B: count 3, weight 0.5 at hour 10
    prof_b = PreferenceProfile(
        profile_name="task_b",
        updated_count=3,
        hourly_weights=[0.0] * 10 + [0.5] + [0.0] * 13,
    )
    save_profile(prof_b)

    # Weighted average at hour 10:
    # (1 * 1.0 + 3 * 0.5) / 4 = (1.0 + 1.5) / 4 = 2.5 / 4 = 0.625
    new_baseline = recompute_global_baseline()
    assert new_baseline.updated_count == 4
    assert new_baseline.hourly_weights[10] == 0.625
    assert test_baseline_path.exists()
