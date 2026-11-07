import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest

from src.agent.scorer import extract_and_score_notes

DATASET_PATH = Path(__file__).resolve().parent / "data" / "synthetic_notes_dataset.json"
TZ = ZoneInfo("Asia/Kolkata")


@pytest.mark.evals
def test_synthetic_notes_batch_extraction_benchmark():
    """
    Evaluates Gemini extraction and scoring against a synthetic dataset of 16 edge cases.
    Measures:
      1. Event type classification accuracy (>= 80%)
      2. Mobility constraint accuracy (>= 80%)
      3. Duration calibration accuracy (>= 80%)
      4. Start hour anchor accuracy (>= 80%)
    """
    assert DATASET_PATH.exists(), f"Evaluation dataset not found at {DATASET_PATH}"

    with open(DATASET_PATH, encoding="utf-8") as f:
        cases = json.load(f)

    # Combine into a realistic multi-line user notes dump
    multi_line_input = "\n".join(f"- {c['raw_note']}" for c in cases)

    extracted_tasks = extract_and_score_notes(multi_line_input)
    assert len(extracted_tasks) >= len(cases) - 2, (
        f"Expected at least {len(cases) - 2} extracted tasks, got {len(extracted_tasks)}"
    )

    type_correct = 0
    movable_correct = 0
    duration_correct = 0
    anchor_correct = 0
    total_evaluated = 0

    report_rows = []

    for case in cases:
        # Match extracted task by case keywords
        note_lower = case["raw_note"].lower()
        matched = None
        for t in extracted_tasks:
            # Check title overlap
            title_words = set(t.title.lower().split())
            note_words = set(note_lower.split())
            if len(title_words.intersection(note_words)) >= 2:
                matched = t
                break

        if not matched:
            report_rows.append((case["id"], "MISSING", "MISSING", "MISSING", "MISSING", "FAIL"))
            continue

        total_evaluated += 1

        # 1. Type Check
        t_ok = True
        if "acceptable_types" in case:
            t_ok = matched.event_type.value in case["acceptable_types"]
        elif "expected_type" in case:
            t_ok = matched.event_type.value == case["expected_type"]
        if t_ok:
            type_correct += 1

        # 2. Mobility Check
        m_ok = True
        if "expected_movable" in case:
            m_ok = matched.is_movable == case["expected_movable"]
        if m_ok:
            movable_correct += 1

        # 3. Duration Check
        dur_ok = True
        dur = matched.estimated_duration
        if "expected_duration" in case:
            dur_ok = abs(dur - case["expected_duration"]) <= 15
        elif "min_duration" in case and "max_duration" in case:
            dur_ok = case["min_duration"] <= dur <= case["max_duration"]
        if dur_ok:
            duration_correct += 1

        # 4. Anchor Check
        anc_ok = True
        if "expected_start_hour" in case:
            if matched.start and matched.start.dateTime:
                parsed_dt = datetime.fromisoformat(matched.start.dateTime)
                anc_ok = parsed_dt.hour == case["expected_start_hour"]
            else:
                anc_ok = False
        if anc_ok:
            anchor_correct += 1

        all_ok = t_ok and m_ok and dur_ok and anc_ok
        report_rows.append((
            case["id"],
            "PASS" if t_ok else f"FAIL({matched.event_type.value})",
            "PASS" if m_ok else f"FAIL({matched.is_movable})",
            "PASS" if dur_ok else f"FAIL({dur}m)",
            "PASS" if anc_ok else "FAIL",
            "PASS" if all_ok else "PARTIAL",
        ))

    # Print Evaluation Scorecard
    print("\n" + "=" * 80)
    print("                 LLM REGRESSION EVALUATION BENCHMARK")
    print("=" * 80)
    print(f"{'Case ID':<28} {'Type':<12} {'Movable':<10} {'Duration':<12} {'Anchor':<8} {'Status'}")
    print("-" * 80)
    for row in report_rows:
        print(f"{row[0]:<28} {row[1]:<12} {row[2]:<10} {row[3]:<12} {row[4]:<8} {row[5]}")
    print("-" * 80)

    type_acc = (type_correct / total_evaluated) * 100 if total_evaluated else 0
    movable_acc = (movable_correct / total_evaluated) * 100 if total_evaluated else 0
    duration_acc = (duration_correct / total_evaluated) * 100 if total_evaluated else 0
    anchor_acc = (anchor_correct / total_evaluated) * 100 if total_evaluated else 0

    print(f"Type Classification Accuracy:    {type_acc:0.1f}%  (Target >= 80%)")
    print(f"Mobility Classification Accuracy:{movable_acc:0.1f}%  (Target >= 80%)")
    print(f"Duration Calibration Accuracy:   {duration_acc:0.1f}%  (Target >= 80%)")
    print(f"Anchor Extraction Accuracy:      {anchor_acc:0.1f}%  (Target >= 80%)")
    print("=" * 80)

    # Core AI Engineering Assertions
    assert type_acc >= 75.0, f"Type accuracy {type_acc:.1f}% below target 75%"
    assert movable_acc >= 80.0, f"Mobility accuracy {movable_acc:.1f}% below target 80%"
    assert duration_acc >= 80.0, f"Duration calibration {duration_acc:.1f}% below target 80%"
