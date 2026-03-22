---
name: notifications
description: Send notifications through Discord, email, or push channels
version: 1.0.0
trigger: "notify|send notification|alert|send message|discord|email"
tools: [shell]
---

# Notifications Skill

Use this skill to send notifications through various channels including Discord webhooks, email (via AWS SES), and push notifications.

## When to Use

Use this skill when:
- You need to send alerts or notifications
- You need to notify users of completed tasks
- You need to send reports via Discord or email
- You need to push real-time updates

## Usage

Call the skill-runner with:

```bash
node /app/scripts/skill-runner.js --skill notifications --params '{"channels":[{"type":"discord","webhook_url":"https://discord.com/api/webhooks/..."}],"message":{"title":"Alert","body":"Task completed"}}'
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| channels | array | Yes | - | Array of notification channels |
| message | object | Yes | - | Message content |
| retryCount | number | No | 3 | Number of retry attempts |
| retryDelay | number | No | 1000 | Delay between retries (ms) |

### Channel Configuration

| Channel Type | Required Fields |
|--------------|-----------------|
| discord | type, webhook_url |
| email | type, to, subject (optional) |
| push | type |

### Message Structure

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| title | string | Yes | Notification title |
| body | string | Yes | Notification body |
| priority | string | No | "low", "normal", or "high" |
| data | object | No | Additional data payload |

## Examples

### Discord Notification
```bash
node /app/scripts/skill-runner.js --skill notifications --params '{
  "channels": [{"type": "discord", "webhook_url": "https://discord.com/api/webhooks/..."}],
  "message": {"title": "Task Complete", "body": "Your task has finished", "priority": "normal"}
}'
```

### Email Notification
```bash
node /app/scripts/skill-runner.js --skill notifications --params '{
  "channels": [{"type": "email", "to": "user@example.com", "subject": "Report Ready"}],
  "message": {"title": "Report Generated", "body": "Your report is ready for download."}
}'
```

### Multi-channel Notification
```bash
node /app/scripts/skill-runner.js --skill notifications --params '{
  "channels": [
    {"type": "discord", "webhook_url": "https://discord.com/api/webhooks/..."},
    {"type": "email", "to": "user@example.com"}
  ],
  "message": {"title": "Critical Alert", "body": "System status changed", "priority": "high"}
}'
'
```

## Output

Returns JSON with:
- `totalSent`: Number of successful sends
- `totalFailed`: Number of failed sends
- `results`: Array of per-channel results

## Environment Variables

- `DISCORD_WEBHOOK_URL`: Default Discord webhook
- `AWS_ACCESS_KEY_ID`: For email via SES
- `AWS_SECRET_ACCESS_KEY`: For email via SES
- `AWS_REGION`: AWS region (default: us-east-1)

## Notes

- Discord notifications use webhook URLs
- Email requires AWS SES configuration
- Push notifications require additional setup (FCM/APNS)