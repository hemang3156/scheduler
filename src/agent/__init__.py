"""Agent orchestration and LLM task evaluation modules."""
from src.agent.scorer import score_tasks_from_json, extract_and_score_notes
from src.agent.scheduler_agent import AutonomousScheduler, format_schedule_diff
from src.agent.curve_updater import evolve_curve_with_llm
from src.agent.prompt_templates import (
    build_notes_extraction_prompt,
    build_scoring_prompt,
    build_curve_evolution_prompt,
    NOTES_EXTRACTION_PROMPT_TEMPLATE,
    SCORING_PROMPT_TEMPLATE,
    CURVE_EVOLUTION_PROMPT_TEMPLATE,
)

__all__ = [
    "score_tasks_from_json",
    "extract_and_score_notes",
    "AutonomousScheduler",
    "format_schedule_diff",
    "evolve_curve_with_llm",
    "build_notes_extraction_prompt",
    "build_scoring_prompt",
    "build_curve_evolution_prompt",
    "NOTES_EXTRACTION_PROMPT_TEMPLATE",
    "SCORING_PROMPT_TEMPLATE",
    "CURVE_EVOLUTION_PROMPT_TEMPLATE",
]
