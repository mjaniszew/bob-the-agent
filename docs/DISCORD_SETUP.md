# Discord Bot Setup

## Overview

Mini Agent includes a Discord bot for managing tasks through Discord slash commands.

## Prerequisites

- A Discord account
- A Discord server where you have admin permissions
- The bot token and client ID from `.env`

## Step 1: Create Discord Application

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application"
3. Name it (e.g., "Mini Agent")
4. Copy the **Application ID** (this is your `DISCORD_CLIENT_ID`)

## Step 2: Create Bot User

1. Navigate to "Bot" in the left sidebar
2. Click "Add Bot"
3. Click "Reset Token" to generate a new token
4. Copy the token (this is your `DISCORD_BOT_TOKEN`)
5. Save changes

### Important Bot Settings

Enable these Privileged Gateway Intents:
- ✅ Message Content Intent
- ✅ Server Members Intent (optional)
- ✅ Presence Intent (optional)

## Step 3: Configure Environment

Add to your `.env` file:

```env
DISCORD_BOT_TOKEN=your-bot-token-here
DISCORD_CLIENT_ID=your-application-id-here
```

## Step 4: Invite Bot to Server

1. Go to "OAuth2" > "URL Generator" in Developer Portal
2. Select scopes:
   - ✅ `bot`
   - ✅ `applications.commands`
3. Select permissions:
   - ✅ Send Messages
   - ✅ Use Slash Commands
   - ✅ Embed Links
   - ✅ Read Message History
4. Copy the generated URL
5. Open URL in browser
6. Select your server and authorize

## Step 5: Verify Bot Works

1. Restart the agent container:
   ```bash
   docker compose restart agent
   ```

2. Check logs:
   ```bash
   docker compose logs agent | grep "Discord"
   ```

3. Test in Discord:
   ```
   /help
   ```

## Available Commands

### Task Management

| Command | Description |
|---------|-------------|
| `/task add` | Create a new task |
| `/task list` | List all tasks |
| `/task status` | Get task status |
| `/task run` | Run a task immediately |
| `/task delete` | Delete a task |

### Schedule Management

| Command | Description |
|---------|-------------|
| `/schedule add` | Create a scheduled task |
| `/schedule list` | List all schedules |
| `/schedule remove` | Remove a schedule |

### Other Commands

| Command | Description |
|---------|-------------|
| `/result` | Get task result |
| `/status` | Get agent status |
| `/help` | Show help |

## Usage Examples

### Create a Task

```
/task add name:"Search for AI news" description:"Find latest AI developments" skill:web-search
```

### List Tasks

```
/task list status:pending
```

### Run a Task

```
/task run id:abc123
```

### Create a Schedule

```
/schedule add name:"Daily Report" cron:"0 9 * * *" task:{"skill":"web-search","parameters":{"query":"daily news"}}
```

## Troubleshooting

### Bot Not Responding

1. Check if bot is online in Discord
2. Verify token and client ID are correct
3. Check agent logs for errors:
   ```bash
   docker compose logs agent
   ```

### Commands Not Appearing

1. Re-register commands:
   - Restart the agent container
   - Wait up to 1 hour for Discord to refresh

### Permission Errors

1. Ensure bot has required permissions
2. Re-invite bot with correct permissions
3. Check server role settings

## Security

### Token Security

- Never share your bot token
- Regenerate if compromised
- Use environment variables

### Server Permissions

- Only invite to trusted servers
- Use minimal required permissions
- Regularly audit bot access