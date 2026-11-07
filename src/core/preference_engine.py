import json
import re
from pathlib import Path
from typing import List, Optional, Sequence

from src.core.models import PreferenceProfile

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROFILES_DIR = PROJECT_ROOT / "data" / "preference_profiles"
CATEGORIES_DIR = PROFILES_DIR / "categories"
GLOBAL_BASELINE_PATH = PROFILES_DIR / "global_baseline.json"


def normalize_profile_name(name: str) -> str:
    """Converts a title or category into a clean filesystem slug."""
    clean = re.sub(r"[^\w\s-]", "", name).strip().lower()
    slug = re.sub(r"[-\s]+", "_", clean).strip("_")
    return slug or "unnamed_task"


def sanitize_weights(raw_weights: Sequence[float]) -> List[float]:
    """Ensures exactly 24 floats clamped between 0.0 and 1.0."""
    if len(raw_weights) != 24:
        raise ValueError(f"Hourly weights must have exactly 24 elements, got {len(raw_weights)}")
    return [max(0.0, min(1.0, round(float(w), 4))) for w in raw_weights]


def get_global_baseline() -> PreferenceProfile:
    """Reads universal baseline profile from disk or returns default zeroed profile."""
    if GLOBAL_BASELINE_PATH.exists():
        with open(GLOBAL_BASELINE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return PreferenceProfile(**data)

    return PreferenceProfile(
        profile_name="global_baseline",
        updated_count=0,
        hourly_weights=[0.0] * 24,
        description="Universal baseline curve initialized to 0.0.",
    )


def load_profile(name_or_title: str) -> PreferenceProfile:
    """
    Loads task-specific profile if it exists.
    If not found, initializes a new profile inheriting from global_baseline.
    """
    slug = normalize_profile_name(name_or_title)
    CATEGORIES_DIR.mkdir(parents=True, exist_ok=True)
    target_file = CATEGORIES_DIR / f"{slug}.json"

    if target_file.exists():
        with open(target_file, encoding="utf-8") as f:
            data = json.load(f)
        return PreferenceProfile(**data)

    # Cold start: inherit baseline weights with updated_count = 0
    baseline = get_global_baseline()
    return PreferenceProfile(
        profile_name=slug,
        updated_count=0,
        hourly_weights=list(baseline.hourly_weights),
        description=f"Preference profile for '{name_or_title}' initialized from baseline.",
    )


def save_profile(profile: PreferenceProfile) -> Path:
    """Saves a profile to disk, ensuring weights are sanitized."""
    CATEGORIES_DIR.mkdir(parents=True, exist_ok=True)
    slug = normalize_profile_name(profile.profile_name)
    sanitized = sanitize_weights(profile.hourly_weights)

    to_save = PreferenceProfile(
        profile_name=slug,
        updated_count=profile.updated_count,
        hourly_weights=sanitized,
        description=profile.description,
    )

    target_file = CATEGORIES_DIR / f"{slug}.json"
    with open(target_file, "w", encoding="utf-8") as f:
        json.dump(to_save.model_dump(), f, indent=2)

    return target_file


def recompute_global_baseline() -> PreferenceProfile:
    """
    Recomputes global_baseline.json as the weighted average of all active category profiles.
    Only profiles with updated_count > 0 contribute to the aggregate.
    """
    CATEGORIES_DIR.mkdir(parents=True, exist_ok=True)
    all_files = list(CATEGORIES_DIR.glob("*.json"))

    active_profiles = []
    total_samples = 0

    for file_path in all_files:
        try:
            with open(file_path, encoding="utf-8") as f:
                p = PreferenceProfile(**json.load(f))
            if p.updated_count > 0:
                active_profiles.append(p)
                total_samples += p.updated_count
        except Exception:
            continue

    if not active_profiles or total_samples == 0:
        return get_global_baseline()

    # Weighted element-wise sum
    aggregated = [0.0] * 24
    for p in active_profiles:
        weight_factor = p.updated_count / total_samples
        for h in range(24):
            aggregated[h] += p.hourly_weights[h] * weight_factor

    new_baseline = PreferenceProfile(
        profile_name="global_baseline",
        updated_count=total_samples,
        hourly_weights=sanitize_weights(aggregated),
        description=f"Weighted aggregate across {len(active_profiles)} active profiles ({total_samples} total events).",
    )

    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    with open(GLOBAL_BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(new_baseline.model_dump(), f, indent=2)

    return new_baseline
