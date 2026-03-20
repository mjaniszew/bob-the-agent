# Scheduling Skill

Manages scheduled task execution with various scheduling modes.

## Features
- Cron-based scheduling
- Interval-based scheduling
- One-time execution
- Persistent schedule storage
- Timezone support

## Usage
```yaml
skill: scheduling
parameters:
  mode: "cron" | "interval" | "once"
  schedule:
    # For cron mode
    expression: "0 9 * * *"  # 9 AM daily

    # For interval mode
    interval: 3600  # Every hour in seconds

    # For once mode
    datetime: "2024-12-25T10:00:00Z"

  task:
    skill: "web-search"
    parameters:
      query: "daily news"
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

## Output
Returns:
- Schedule ID
- Next execution time
- Status (enabled/disabled)