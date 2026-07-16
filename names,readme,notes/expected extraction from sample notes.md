## extraction schemas 

*title, event_type (fixed_time | deadline_task), anchor_datetime, estimated_duration,lock_status (locked | movable)

<assuming date:16-07-26>

extraction format for:

[[Phase 2(sample notes)]]
[
  {
    "title": "SOP meet",
    "event_type": "fixed_time",
    "anchor_datetime": "2026-08-15T17:00",
    "estimated_duration": null,
    "lock_status": "locked"
  },
  {
    "title": "Sankalp meet",
    "event_type": "fixed_time",
    "anchor_datetime": "2026-08-10T20:00",
    "estimated_duration": null,
    "lock_status": "locked"
  },
  {
    "title": "Exam prep - DSA",
    "event_type": "fixed_time",
    "anchor_datetime": "2026-08-13T21:00",
    "estimated_duration": null,
    "lock_status": "movable"
  },
  {
    "title": "Weekly revise",
    "event_type": "fixed_time",
    "anchor_datetime": "2026-07-18T00:00",
    "estimated_duration": null,
    "lock_status": "movable"
  },
  {
    "title": "Call babaji",
    "event_type": "fixed_time",
    "anchor_datetime": null,
    "estimated_duration": null,
    "lock_status": "movable"
  },
  {
    "title": "Trip plan",
    "event_type": "deadline_task",
    "anchor_datetime": null,
    "estimated_duration": null,
    "lock_status": "movable"
  },
  {
    "title": "Complete report",
    "event_type": "deadline_task",
    "anchor_datetime": "2026-07-16T23:59",
    "estimated_duration": null,
    "lock_status": "locked"
  }
]