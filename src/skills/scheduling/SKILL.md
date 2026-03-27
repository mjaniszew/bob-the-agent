---
name: scheduling
description: Schedule tasks for execution at specified times or intervals
version: 1.0.0
trigger: "schedule|cron|timer|delayed task|periodic task|run later"
tools: [shell]
---

# Scheduling Skill

Use this skill to create and manage scheduled tasks. Supports cron expressions, intervals, and one-time execution.

## When to Use

Use this skill when:
- You need to schedule a task to run at specific times
- You need to create recurring tasks
- You need to delay task execution
- You need to manage existing schedules

## Usage

Call the skill-runner with:

```bash
node /app/scripts/skill-runner.mjs --skill scheduling --params '{"mode":"cron","schedule":{"expression":"0 9 * * *"},"task":{"skill":"web-search","parameters":{"query":"daily news"}}}'
```

## Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| mode | string | Yes | Schedule mode: "cron", "interval", or "once" |
| schedule | object | Yes | Schedule configuration |
| task | object | Yes | Task to execute |

### Schedule Modes

| Mode | Required Field | Format |
|------|----------------|--------|
| cron | expression | Standard cron: "minute hour day month weekday" |
| interval | interval | Seconds (minimum 60) |
| once | datetime | ISO datetime string |

### Task Structure

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| name | string | Yes | Task name |
| description | string | No | Task description |
| skill | string | Yes | Skill to execute |
| parameters | object | Yes | Skill parameters |

## Examples

### Cron Schedule (Daily at 9 AM)
```bash
node /app/scripts/skill-runner.mjs --skill scheduling --params '{
  "mode": "cron",
  "schedule": {"expression": "0 9 * * *"},
  "task": {
    "name": "Daily News Search",
    "skill": "web-search",
    "parameters": {"query": "daily news"}
  }
}'
```

### Interval Schedule (Every Hour)
```bash
node /app/scripts/skill-runner.mjs --skill scheduling --params '{
  "mode": "interval",
  "schedule": {"interval": 3600},
  "task": {
    "name": "Hourly Check",
    "skill": "data-extraction",
    "parameters": {"source": {"type": "url", "value": "https://example.com/status"}}
  }
}'
```

### One-time Schedule
```bash
node /app/scripts/skill-runner.mjs --skill scheduling --params '{
  "mode": "once",
  "schedule": {"datetime": "2026-03-25T10:00:00Z"},
  "task": {
    "name": "Future Task",
    "skill": "notifications",
    "parameters": {"channels": [{"type": "discord", "webhook_url": "..."}], "message": {"title": "Reminder", "body": "Scheduled reminder"}}
  }
}'
```

## Cron Expression Format

```
┌───────────── minute (0 - 59)
│ ┌───────────── hour (0 - 23)
│ │ ┌───────────── day of month (1 - 31)
│ │ │ ┌───────────── month (1 - 12)
│ │ │ │ ┌───────────── day of week (0 - 6)
│ │ │ │ │
* * * * *
```

Examples:
- `0 9 * * *` - Every day at 9:00 AM
- `*/15 * * * *` - Every 15 minutes
- `0 0 * * 0` - Every Sunday at midnight
- `0 9 1 * *` - First day of every month at 9:00 AM

## Output

Returns JSON with:
- `success`: Boolean indicating success
- `scheduleId`: Unique identifier for the schedule
- `nextExecution`: ISO datetime of next execution
- `message`: Status message

## Notes

- Minimum interval is 60 seconds
- Schedules are persisted in SQLite database
- One-time schedules are deleted after execution
- Requires database path configuration