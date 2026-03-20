# Notifications Skill

Sends notifications through various channels.

## Features
- Discord bot notifications
- Email via Amazon SES
- Push notifications
- Retry mechanism with exponential backoff
- Priority queuing

## Usage
```yaml
skill: notifications
parameters:
  channels:
    - type: "discord"
      webhook_url: "${DISCORD_WEBHOOK_URL}"
    - type: "email"
      to: "user@example.com"
  message:
    title: "Task Completed"
    body: "Your task has finished processing."
    priority: "normal" | "high"
```

## Supported Channels
- **Discord**: Webhook-based notifications
- **Email**: Via Amazon SES (requires AWS credentials)
- **Push**: Browser push notifications

## Configuration
Environment variables:
- DISCORD_WEBHOOK_URL
- AWS_ACCESS_KEY_ID
- AWS_SECRET_ACCESS_KEY
- AWS_REGION
- NOTIFICATION_EMAIL

## Output
Returns delivery status for each channel.