# Discord Bot Setup

## Overview

Bob The Agent includes a Discord bot powered by the Hermes Agent framework for managing tasks through Discord slash commands and natural language interaction.

## Prerequisites

- A Discord account
- A Discord server where you have admin permissions
- The bot token and client ID configured in `.env`

## Step 1: Create Discord Application

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application"
3. Name it (e.g., "Bob The Agent")
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

1. Restart the main agent container:
   ```bash
   docker compose restart agent-main
   ```

2. Check logs:
   ```bash
   docker compose logs agent-main | grep -i discord
   ```

3. Test in Discord by sending a message or using commands

## Usage

Hermes Agent's Discord integration supports natural language interaction. The bot responds to messages and can use all configured skills and tools.

### Key Features

- **Natural language interaction** — Talk to the bot normally in channels
- **Threaded conversations** — The bot can auto-create threads for longer discussions
- **Reactions** — The bot uses emoji reactions to acknowledge messages
- **Slash commands** — Hermes provides built-in commands depending on configuration

### Discord Configuration

Discord bot behavior is configured in `hermes.template.yaml` under the `discord` section:

```yaml
discord:
  require_mention: true          # Only respond when mentioned
  auto_thread: true              # Auto-create threads for conversations
  reactions: true                 # Use emoji reactions
```

## Troubleshooting

### Bot Not Responding

1. Check if bot is online in Discord
2. Verify token and client ID are correct in `.env`
3. Check agent-main logs for Discord errors:
   ```bash
   docker compose logs agent-main | grep -i discord
   ```

### Commands Not Appearing

1. Restart the agent container: `docker compose restart agent-main`
2. Wait up to 1 hour for Discord to refresh command cache
3. Verify `hermes-cli` and `hermes-discord` toolsets are enabled in config

### Permission Errors

1. Ensure bot has required permissions in Discord
2. Re-invite bot with correct permissions
3. Check server role settings

## Security

### Token Security

- Never share your bot token
- Regenerate if compromised
- Use environment variables (never commit to git)

### Server Permissions

- Only invite to trusted servers
- Use minimal required permissions
- Regularly audit bot access