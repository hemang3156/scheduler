import argparse
import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from src.agent.scorer import score_tasks_from_json, extract_and_score_notes
from src.agent.scheduler_agent import AutonomousScheduler, format_schedule_diff
from src.tools.calender_client import CalendarClient
from src.core.exceptions import CalendarAuthError, CalendarError

TIMEZONE = "Asia/Kolkata"


@dataclass(frozen=True)
class ScheduleConfig:
    start_window: dt.datetime
    end_window: dt.datetime
    initial_date: dt.date
    final_date: dt.date
    daily_start: dt.time | None = None
    daily_end: dt.time | None = None
    continuous_window: bool = False
    task_input: str | None = None
    task_file: Path | None = None
    task_words: list[str] | None = None
    sync: bool = False
    dry_run: bool = False
    auto_confirm: bool = False
    buffer_minutes: int = 5
    now: dt.datetime | None = None
    is_interactive: bool = False


def _banner():
    print("Autonomous Calendar Scheduler\n")


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(0)
    return answer if answer else default


def _ask_choice(prompt: str, options: list[str], default: int = 1) -> str:
    print(f"\n{prompt}")
    for i, opt in enumerate(options, 1):
        marker = "*" if i == default else " "
        print(f"  {marker} {i}. {opt}")
    while True:
        try:
            raw = input(f"  Enter choice [default {default}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(0)
        if not raw:
            return options[default - 1]
        try:
            idx = int(raw)
            if 1 <= idx <= len(options):
                return options[idx - 1]
        except ValueError:
            pass
        print(f"  Please enter a number between 1 and {len(options)}.")


def _ask_yes_no(prompt: str, default_yes: bool = True) -> bool:
    hint = "Y/n" if default_yes else "y/N"
    try:
        raw = input(f"{prompt} [{hint}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(0)
    if not raw:
        return default_yes
    return raw in ("y", "yes")


def _ask_multiline(prompt: str) -> str:
    print(f"\n{prompt}")
    print("  (Type each task on a new line. Press Enter on an empty line when done.)\n")
    lines = []
    while True:
        try:
            line = input("  > ")
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(0)
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines)


def _error_exit(message: str, hint: str = "", detail: str = ""):
    print(f"\nError: {message}")
    if hint:
        print(f"  Hint: {hint}")
    if detail:
        print(f"  Detail: {detail}")
    sys.exit(1)


def resolve_window(
    initial_date: dt.date,
    final_date: dt.date,
    start_hour: int | None,
    end_hour: int | None,
    now: dt.datetime,
    tz: ZoneInfo,
    is_default_range: bool = False,
) -> tuple[dt.datetime, dt.datetime, dt.date, dt.date, dt.time | None, dt.time | None, bool]:
    if final_date < now.date():
        _error_exit(
            f"Target date ({final_date}) is in the past.",
            "Scheduling is only permitted for the current time onward.",
        )

    if initial_date < now.date():
        initial_date = now.date()

    if is_default_range and start_hour is None and end_hour is None:
        return now, now + dt.timedelta(hours=24), initial_date, final_date, None, None, True

    sh = 9 if start_hour is None else start_hour
    eh = 21 if end_hour is None else end_hour
    daily_start = dt.time(sh, 0)
    daily_end = dt.time(eh, 0)

    if is_default_range:
        return now, now + dt.timedelta(hours=24), initial_date, final_date, daily_start, daily_end, False

    start_window = dt.datetime.combine(initial_date, daily_start, tzinfo=tz)
    end_window = dt.datetime.combine(final_date, daily_end, tzinfo=tz)

    if initial_date == now.date() and start_window < now:
        start_window = now

    if initial_date == final_date and start_window >= end_window:
        _error_exit(
            f"Start window ({start_window.strftime('%Y-%m-%d %I:%M %p')}) is at or after end window ({end_window.strftime('%I:%M %p')}).",
            "Today's scheduling window has ended. Choose tomorrow or a future date range.",
        )
    elif initial_date < final_date and now >= dt.datetime.combine(initial_date, daily_end, tzinfo=tz):
        initial_date = initial_date + dt.timedelta(days=1)
        start_window = dt.datetime.combine(initial_date, daily_start, tzinfo=tz)

    return start_window, end_window, initial_date, final_date, daily_start, daily_end, False


def interactive_mode(now: dt.datetime | None = None) -> ScheduleConfig:
    """Guided wizard configuration provider returning ScheduleConfig."""
    _banner()
    tz = ZoneInfo(TIMEZONE)
    if now is None:
        now = dt.datetime.now(tz).replace(microsecond=0)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)

    date_mode = _ask_choice(
        "Select scheduling date or date range:",
        [
            "Date range (choose initial start date and final end date)",
            f"Today only ({now.strftime('%A, %b %d')})",
            f"Tomorrow only ({(now + dt.timedelta(days=1)).strftime('%A, %b %d')})",
            "Specific single date",
        ],
        default=1,
    )

    if date_mode.startswith("Date range"):
        while True:
            start_str = _ask("Initial (start) date (YYYY-MM-DD)", default=now.date().isoformat())
            try:
                initial_date = dt.date.fromisoformat(start_str)
                break
            except ValueError:
                print(f"  Invalid date format. Expected YYYY-MM-DD (e.g. {now.date().isoformat()}).")

        while True:
            end_str = _ask("Final (end) date (YYYY-MM-DD)", default=initial_date.isoformat())
            try:
                final_date = dt.date.fromisoformat(end_str)
                if final_date < initial_date:
                    print(f"  Final date ({final_date}) cannot be earlier than initial date ({initial_date}).")
                    continue
                break
            except ValueError:
                print(f"  Invalid date format. Expected YYYY-MM-DD (e.g. {initial_date.isoformat()}).")

    elif date_mode.startswith("Today only"):
        initial_date = now.date()
        final_date = now.date()

    elif date_mode.startswith("Tomorrow only"):
        initial_date = (now + dt.timedelta(days=1)).date()
        final_date = (now + dt.timedelta(days=1)).date()

    else:
        while True:
            date_str = _ask("Enter date (YYYY-MM-DD)", default=now.date().isoformat())
            try:
                initial_date = dt.date.fromisoformat(date_str)
                final_date = initial_date
                break
            except ValueError:
                print(f"  Invalid date format. Expected YYYY-MM-DD (e.g. {now.date().isoformat()}).")

    use_defaults = _ask_yes_no("Use default daily time window (09:00 - 21:00)?", default_yes=True)
    if use_defaults:
        start_hour, end_hour = 9, 21
    else:
        start_str = _ask("Daily start hour (0-23, e.g. 9 for 9 AM)", "9")
        end_str = _ask("Daily end hour (0-23, e.g. 21 for 9 PM)", "21")
        try:
            start_hour, end_hour = int(start_str), int(end_str)
            if not (0 <= start_hour < 24 and 0 <= end_hour <= 24 and start_hour < end_hour):
                raise ValueError()
        except ValueError:
            print("Invalid hours. Using defaults (09:00 - 21:00).")
            start_hour, end_hour = 9, 21

    notes_text = _ask_multiline("What do you want to schedule? Write your tasks in plain English")
    if not notes_text.strip():
        print("\nNo tasks entered. Nothing to schedule.")
        sys.exit(0)

    mode_choice = _ask_choice(
        "What would you like to do?",
        [
            "Preview only (don't touch my calendar)",
            "Preview and add to Google Calendar",
        ],
        default=1,
    )
    wants_sync = mode_choice.startswith("Preview and add")

    start_window, end_window, initial_date, final_date, daily_start, daily_end, continuous_window = resolve_window(
        initial_date=initial_date,
        final_date=final_date,
        start_hour=start_hour,
        end_hour=end_hour,
        now=now,
        tz=tz,
        is_default_range=False,
    )

    return ScheduleConfig(
        start_window=start_window,
        end_window=end_window,
        initial_date=initial_date,
        final_date=final_date,
        daily_start=daily_start,
        daily_end=daily_end,
        continuous_window=continuous_window,
        task_input=notes_text,
        sync=wants_sync,
        dry_run=not wants_sync,
        auto_confirm=False,
        buffer_minutes=5,
        now=now,
        is_interactive=True,
    )


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Autonomous Calendar Scheduling Agent — Ingests informal notes, optimizes placement, and syncs to Google Calendar."
    )
    parser.add_argument(
        "note_words",
        nargs="*",
        default=None,
        help="Direct note input as command-line arguments (e.g. scheduler Guitar practice 8pm).",
    )
    parser.add_argument(
        "-n",
        "--notes",
        type=str,
        default=None,
        help="Path to markdown/text file of informal notes or response.json (defaults to data/daily_notes.md if present).",
    )
    parser.add_argument(
        "--raw-text",
        type=str,
        default=None,
        help="Raw text string of unstructured notes directly from the command line.",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Target date for scheduling (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--start-date",
        "--from",
        dest="start_date",
        type=str,
        default=None,
        help="Initial date for scheduling range (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--end-date",
        "--to",
        dest="end_date",
        type=str,
        default=None,
        help="Final date for scheduling range (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--tomorrow",
        action="store_true",
        help="Schedule tasks for tomorrow instead of today.",
    )
    parser.add_argument(
        "--start-hour",
        type=int,
        default=None,
        help="Daily starting hour in 24-hour format (default: 9 for 09:00).",
    )
    parser.add_argument(
        "--end-hour",
        type=int,
        default=None,
        help="Daily ending hour in 24-hour format (default: 21 for 21:00).",
    )
    parser.add_argument(
        "--buffer",
        type=int,
        default=5,
        help="Buffer minutes between scheduled tasks (default: 5).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without connecting to Google Calendar; outputs schedule diff preview only.",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Prompt to commit and write placed tasks into Google Calendar.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Automatically confirm and bypass [y/N] prompt when --sync is active.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Launch the guided interactive wizard (no flags needed).",
    )
    return parser.parse_args(argv)


def _should_use_interactive(args) -> bool:
    if args.interactive:
        return True
    if args.note_words and len(args.note_words) > 0:
        return False
    if args.notes or args.raw_text:
        return False
    if args.dry_run or args.sync or args.tomorrow or args.date or args.start_date or args.end_date:
        return False
    repo_root = Path(__file__).resolve().parent.parent.parent
    daily_notes_path = repo_root / "data" / "daily_notes.md"
    if daily_notes_path.exists():
        content = daily_notes_path.read_text(encoding="utf-8").strip()
        if content:
            return False
    return True


def parse_cli_config(args: argparse.Namespace, now: dt.datetime | None = None) -> ScheduleConfig:
    """CLI argument parser config provider returning ScheduleConfig."""
    tz = ZoneInfo(TIMEZONE)
    if now is None:
        now = dt.datetime.now(tz).replace(microsecond=0)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)

    is_default_range = False
    if args.tomorrow:
        initial_date = (now + dt.timedelta(days=1)).date()
        final_date = initial_date
    elif args.start_date or args.end_date:
        try:
            initial_date = dt.date.fromisoformat(args.start_date or args.date or now.date().isoformat())
            final_date = dt.date.fromisoformat(args.end_date or args.start_date or args.date or now.date().isoformat())
            if initial_date > final_date:
                _error_exit(
                    f"Initial date ({initial_date}) must be on or before final date ({final_date}).",
                    "Check your --start-date and --end-date values.",
                )
        except ValueError as ve:
            _error_exit(
                "Invalid date format in date range.",
                "Expected format: YYYY-MM-DD (e.g., 2026-12-25).",
                detail=str(ve),
            )
    elif args.date:
        try:
            initial_date = dt.date.fromisoformat(args.date)
            final_date = initial_date
        except ValueError:
            _error_exit(
                f"Invalid date format '{args.date}'.",
                "Expected format: YYYY-MM-DD (e.g., 2026-12-25).",
            )
    else:
        is_default_range = True
        initial_date = now.date()
        final_date = (now + dt.timedelta(hours=24)).date()

    start_window, end_window, initial_date, final_date, daily_start, daily_end, continuous_window = resolve_window(
        initial_date=initial_date,
        final_date=final_date,
        start_hour=args.start_hour,
        end_hour=args.end_hour,
        now=now,
        tz=tz,
        is_default_range=is_default_range,
    )

    return ScheduleConfig(
        start_window=start_window,
        end_window=end_window,
        initial_date=initial_date,
        final_date=final_date,
        daily_start=daily_start,
        daily_end=daily_end,
        continuous_window=continuous_window,
        task_input=args.raw_text,
        task_words=args.note_words if args.note_words and len(args.note_words) > 0 else None,
        task_file=Path(args.notes) if args.notes else None,
        sync=args.sync,
        dry_run=args.dry_run,
        auto_confirm=args.yes,
        buffer_minutes=args.buffer,
        now=now,
        is_interactive=False,
    )


def run_pipeline(config: ScheduleConfig):
    """Single linear execution path for scheduling and calendar operations."""
    print("\n" + "=" * 80)
    print("      AUTONOMOUS CALENDAR SCHEDULER - INITIALIZING ENGINE")
    print("=" * 80)
    print(f"Timezone:          {TIMEZONE}")
    if config.continuous_window:
        print(f"Target Window:     {config.start_window.strftime('%Y-%m-%d %I:%M %p')} -> {config.end_window.strftime('%Y-%m-%d %I:%M %p')} (Next 24 Hours)")
        if config.daily_start and config.daily_end:
            print(f"Daily Operating:   {config.daily_start.strftime('%I:%M %p')} -> {config.daily_end.strftime('%I:%M %p')}")
        else:
            print("Daily Operating:   Continuous 24-Hour Rolling Window")
    elif config.initial_date == config.final_date:
        print(f"Target Date:       {config.initial_date.isoformat()}")
        print(f"Scheduling Window: {config.start_window.strftime('%Y-%m-%d %I:%M %p')} -> {config.end_window.strftime('%I:%M %p')}")
    else:
        days_count = (config.final_date - config.initial_date).days + 1
        print(f"Target Date Range: {config.initial_date.isoformat()} -> {config.final_date.isoformat()} ({days_count} days)")
        print(f"Daily Window:      {config.start_window.strftime('%I:%M %p')} -> {config.end_window.strftime('%I:%M %p')} each day")
    print(f"Task Buffer:       {config.buffer_minutes} minutes")

    print("\n[Step 1/3] Extracting and prioritizing tasks via Gemini API...")
    try:
        if config.task_input:
            tasks = extract_and_score_notes(config.task_input)
        elif config.task_words:
            raw_input = " ".join(config.task_words).strip()
            tasks = extract_and_score_notes(raw_input)
        elif config.task_file:
            if not config.task_file.exists():
                _error_exit(f"File not found: {config.task_file}", "Check the file path and try again.")
            if config.task_file.suffix == ".json":
                tasks = score_tasks_from_json(response_json_path=config.task_file)
            else:
                tasks = extract_and_score_notes(config.task_file.read_text(encoding="utf-8"))
        else:
            repo_root = Path(__file__).resolve().parent.parent.parent
            daily_notes = repo_root / "data" / "daily_notes.md"
            if daily_notes.exists() and daily_notes.read_text(encoding="utf-8").strip():
                tasks = extract_and_score_notes(daily_notes.read_text(encoding="utf-8"))
            else:
                fallback = repo_root / "redundant" / "export" / "response.json"
                if fallback.exists():
                    tasks = score_tasks_from_json(response_json_path=fallback)
                else:
                    _error_exit(
                        "No task input provided.",
                        "Pass tasks as arguments, use --notes, or write to data/daily_notes.md.\n  Or run 'scheduler --interactive' for a guided walkthrough.",
                    )
        print(f" -> Successfully scored {len(tasks)} tasks.")
    except SystemExit:
        raise
    except Exception as e:
        _error_exit(
            "Task scoring failed." if not config.is_interactive else "Could not analyze your tasks.",
            "Make sure your Gemini API key is configured correctly.",
            detail=str(e),
        )

    print("\n[Step 2/3] Fetching commitments & optimizing schedule plan...")
    client = None
    existing_events = []

    if not config.dry_run:
        try:
            client = CalendarClient()
            existing_events = client.list_events(config.start_window, config.end_window)
            print(f" -> Retrieved {len(existing_events)} existing calendar commitments.")
        except (CalendarAuthError, CalendarError) as e:
            print(f" -> Calendar connection warning: {e}")
            print(" -> Falling back to offline dry-run mode (no external events).")
            client = None
    else:
        print(" -> Dry-run mode enabled: skipping live Google Calendar fetch.")

    scheduler = AutonomousScheduler(calendar_client=client)
    plan = scheduler.generate_plan(
        tasks=tasks,
        start_window=config.start_window,
        end_window=config.end_window,
        existing_events=existing_events,
        buffer_minutes=config.buffer_minutes,
        now=config.now,
        daily_start_time=config.daily_start,
        daily_end_time=config.daily_end,
        continuous_window=config.continuous_window,
    )

    print("\n[Step 3/3] Optimization complete. Reviewing proposed schedule diff:")
    diff_text = format_schedule_diff(plan)
    print("\n" + diff_text)

    if config.sync and client is not None and plan.placed_tasks:
        print("\n" + "-" * 80)
        if config.auto_confirm:
            confirm = "y"
        elif config.is_interactive:
            confirm = "y" if _ask_yes_no("Add these events to your Google Calendar?", default_yes=True) else "n"
        else:
            try:
                confirm = input("Would you like to sync these placed tasks to Google Calendar? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                confirm = "n"

        if confirm in ("y", "yes"):
            print("Syncing events to Google Calendar...")
            created = scheduler.apply_plan(plan, dry_run=False)
            print(f"Successfully synced {len(created)} events to your calendar!")
        else:
            print("Sync aborted by user. No calendar changes were made.")
    elif not plan.placed_tasks:
        print("\nNo tasks could be placed in the available time slots.")
    elif not config.sync:
        if config.is_interactive:
            print("\nPreview complete. Run again and choose 'Preview and add' to sync to your calendar.")
        else:
            print("\nSchedule generation complete. Run with --sync to write to Google Calendar.")

    return plan


def main(argv=None, now: dt.datetime | None = None):
    args = parse_args(argv)
    if _should_use_interactive(args):
        config = interactive_mode(now=now)
    else:
        config = parse_cli_config(args, now=now)
    return run_pipeline(config)


if __name__ == "__main__":
    main()
