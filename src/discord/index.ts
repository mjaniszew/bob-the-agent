/**
 * Discord Bot Implementation
 * Provides Discord slash commands for task management
 */

import {
  Client,
  GatewayIntentBits,
  REST,
  Routes,
  SlashCommandBuilder,
  CommandInteraction,
  EmbedBuilder,
  ButtonBuilder,
  ButtonStyle,
  ActionRowBuilder,
  ComponentType
} from 'discord.js';

// Slash commands definition
const commands = [
  new SlashCommandBuilder()
    .setName('task')
    .setDescription('Manage tasks')
    .addSubcommand(subcommand =>
      subcommand
        .setName('add')
        .setDescription('Add a new task')
        .addStringOption(option =>
          option.setName('name').setDescription('Task name').setRequired(true)
        )
        .addStringOption(option =>
          option.setName('description').setDescription('Task description')
        )
        .addStringOption(option =>
          option.setName('skill').setDescription('Skill to use')
            .addChoices(
              { name: 'Web Search', value: 'web-search' },
              { name: 'Data Extraction', value: 'data-extraction' },
              { name: 'Math Operations', value: 'math-operations' },
              { name: 'Document Creation', value: 'document-creation' },
              { name: 'Notifications', value: 'notifications' }
            )
        )
    )
    .addSubcommand(subcommand =>
      subcommand
        .setName('list')
        .setDescription('List all tasks')
        .addStringOption(option =>
          option.setName('status').setDescription('Filter by status')
            .addChoices(
              { name: 'Pending', value: 'pending' },
              { name: 'Running', value: 'running' },
              { name: 'Completed', value: 'completed' },
              { name: 'Failed', value: 'failed' }
            )
        )
    )
    .addSubcommand(subcommand =>
      subcommand
        .setName('status')
        .setDescription('Get task status')
        .addStringOption(option =>
          option.setName('id').setDescription('Task ID').setRequired(true)
        )
    )
    .addSubcommand(subcommand =>
      subcommand
        .setName('delete')
        .setDescription('Delete a task')
        .addStringOption(option =>
          option.setName('id').setDescription('Task ID').setRequired(true)
        )
    )
    .addSubcommand(subcommand =>
      subcommand
        .setName('run')
        .setDescription('Run a task immediately')
        .addStringOption(option =>
          option.setName('id').setDescription('Task ID').setRequired(true)
        )
    ),

  new SlashCommandBuilder()
    .setName('schedule')
    .setDescription('Manage schedules')
    .addSubcommand(subcommand =>
      subcommand
        .setName('add')
        .setDescription('Add a new schedule')
        .addStringOption(option =>
          option.setName('name').setDescription('Schedule name').setRequired(true)
        )
        .addStringOption(option =>
          option.setName('cron').setDescription('Cron expression').setRequired(true)
        )
        .addStringOption(option =>
          option.setName('task').setDescription('Task template (JSON)').setRequired(true)
        )
    )
    .addSubcommand(subcommand =>
      subcommand
        .setName('list')
        .setDescription('List all schedules')
    )
    .addSubcommand(subcommand =>
      subcommand
        .setName('remove')
        .setDescription('Remove a schedule')
        .addStringOption(option =>
          option.setName('id').setDescription('Schedule ID').setRequired(true)
        )
    ),

  new SlashCommandBuilder()
    .setName('result')
    .setDescription('Get task result')
    .addStringOption(option =>
      option.setName('id').setDescription('Task ID').setRequired(true)
    ),

  new SlashCommandBuilder()
    .setName('status')
    .setDescription('Get agent status'),

  new SlashCommandBuilder()
    .setName('help')
    .setDescription('Show help information')
];

// API client for communicating with agent
class AgentAPI {
  private baseUrl: string;

  constructor(baseUrl: string = process.env.AGENT_API_URL || 'http://localhost:3001') {
    this.baseUrl = baseUrl;
  }

  async getTasks(status?: string): Promise<any> {
    const url = status
      ? `${this.baseUrl}/api/tasks?status=${status}`
      : `${this.baseUrl}/api/tasks`;
    const response = await fetch(url);
    return response.json();
  }

  async getTask(id: string): Promise<any> {
    const response = await fetch(`${this.baseUrl}/api/tasks/${id}`);
    return response.json();
  }

  async createTask(data: any): Promise<any> {
    const response = await fetch(`${this.baseUrl}/api/tasks`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    return response.json();
  }

  async runTask(id: string): Promise<any> {
    const response = await fetch(`${this.baseUrl}/api/tasks/${id}/run`, {
      method: 'POST'
    });
    return response.json();
  }

  async deleteTask(id: string): Promise<any> {
    const response = await fetch(`${this.baseUrl}/api/tasks/${id}`, {
      method: 'DELETE'
    });
    return response.json();
  }

  async getSchedules(): Promise<any> {
    const response = await fetch(`${this.baseUrl}/api/schedules`);
    return response.json();
  }

  async createSchedule(data: any): Promise<any> {
    const response = await fetch(`${this.baseUrl}/api/schedules`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    return response.json();
  }

  async deleteSchedule(id: string): Promise<any> {
    const response = await fetch(`${this.baseUrl}/api/schedules/${id}`, {
      method: 'DELETE'
    });
    return response.json();
  }

  async getStatus(): Promise<any> {
    const response = await fetch(`${this.baseUrl}/api/status`);
    return response.json();
  }
}

// Discord bot class
export class DiscordBot {
  private client: Client;
  private rest: REST;
  private api: AgentAPI;

  constructor() {
    this.client = new Client({
      intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildMessages,
        GatewayIntentBits.MessageContent
      ]
    });

    this.rest = new REST({ version: '10' }).setToken(process.env.DISCORD_BOT_TOKEN || '');
    this.api = new AgentAPI();
  }

  async start(): Promise<void> {
    // Register commands
    const clientId = process.env.DISCORD_CLIENT_ID;
    if (!clientId) {
      throw new Error('DISCORD_CLIENT_ID not set');
    }

    await this.rest.put(
      Routes.applicationCommands(clientId),
      { body: commands.map(cmd => cmd.toJSON()) }
    );

    console.log('Discord commands registered');

    // Setup event handlers
    this.client.on('ready', () => {
      console.log(`Discord bot logged in as ${this.client.user?.tag}`);
    });

    this.client.on('interactionCreate', async (interaction) => {
      if (!interaction.isCommand()) return;
      await this.handleCommand(interaction);
    });

    // Login
    await this.client.login(process.env.DISCORD_BOT_TOKEN);
  }

  private async handleCommand(interaction: CommandInteraction): Promise<void> {
    const { commandName } = interaction;

    try {
      switch (commandName) {
        case 'task':
          await this.handleTaskCommand(interaction);
          break;
        case 'schedule':
          await this.handleScheduleCommand(interaction);
          break;
        case 'result':
          await this.handleResultCommand(interaction);
          break;
        case 'status':
          await this.handleStatusCommand(interaction);
          break;
        case 'help':
          await this.handleHelpCommand(interaction);
          break;
        default:
          await interaction.reply('Unknown command');
      }
    } catch (error) {
      console.error('Command error:', error);
      await interaction.reply({
        content: `Error: ${error instanceof Error ? error.message : 'Unknown error'}`,
        ephemeral: true
      });
    }
  }

  private async handleTaskCommand(interaction: CommandInteraction): Promise<void> {
    const subcommand = interaction.options.getSubcommand();

    switch (subcommand) {
      case 'add': {
        const name = interaction.options.get('name')?.value as string;
        const description = interaction.options.get('description')?.value as string;
        const skill = interaction.options.get('skill')?.value as string;

        const task = await this.api.createTask({ name, description, skill });

        const embed = new EmbedBuilder()
          .setTitle('Task Created')
          .addFields(
            { name: 'ID', value: task.id, inline: true },
            { name: 'Name', value: name, inline: true },
            { name: 'Skill', value: skill || 'None', inline: true }
          )
          .setColor(0x00FF00)
          .setTimestamp();

        await interaction.reply({ embeds: [embed] });
        break;
      }

      case 'list': {
        const status = interaction.options.get('status')?.value as string;
        const result = await this.api.getTasks(status);

        if (!result.tasks || result.tasks.length === 0) {
          await interaction.reply('No tasks found');
          return;
        }

        const embed = new EmbedBuilder()
          .setTitle('Tasks')
          .setDescription(`Found ${result.tasks.length} tasks`)
          .addFields(
            result.tasks.slice(0, 10).map((task: any) => ({
              name: `${task.name} (${task.id.slice(0, 8)})`,
              value: `Status: ${task.status}`,
              inline: false
            }))
          )
          .setColor(0x5865F2)
          .setTimestamp();

        await interaction.reply({ embeds: [embed] });
        break;
      }

      case 'status': {
        const id = interaction.options.get('id')?.value as string;
        const task = await this.api.getTask(id);

        const embed = new EmbedBuilder()
          .setTitle(`Task: ${task.name}`)
          .addFields(
            { name: 'ID', value: task.id, inline: true },
            { name: 'Status', value: task.status, inline: true },
            { name: 'Created', value: new Date(task.created_at).toLocaleString(), inline: true }
          )
          .setColor(task.status === 'completed' ? 0x00FF00 :
                    task.status === 'failed' ? 0xFF0000 :
                    task.status === 'running' ? 0xFFFF00 : 0x5865F2)
          .setTimestamp();

        await interaction.reply({ embeds: [embed] });
        break;
      }

      case 'delete': {
        const id = interaction.options.get('id')?.value as string;
        await this.api.deleteTask(id);
        await interaction.reply(`Task ${id} deleted`);
        break;
      }

      case 'run': {
        const id = interaction.options.get('id')?.value as string;
        await this.api.runTask(id);
        await interaction.reply(`Task ${id} queued for execution`);
        break;
      }
    }
  }

  private async handleScheduleCommand(interaction: CommandInteraction): Promise<void> {
    const subcommand = interaction.options.getSubcommand();

    switch (subcommand) {
      case 'add': {
        const name = interaction.options.get('name')?.value as string;
        const cron = interaction.options.get('cron')?.value as string;
        const taskTemplate = interaction.options.get('task')?.value as string;

        const schedule = await this.api.createSchedule({
          name,
          cron_expression: cron,
          task_template: JSON.parse(taskTemplate)
        });

        await interaction.reply(`Schedule created with ID: ${schedule.id}`);
        break;
      }

      case 'list': {
        const result = await this.api.getSchedules();

        if (!result.schedules || result.schedules.length === 0) {
          await interaction.reply('No schedules found');
          return;
        }

        const embed = new EmbedBuilder()
          .setTitle('Schedules')
          .addFields(
            result.schedules.map((schedule: any) => ({
              name: schedule.name,
              value: `Cron: ${schedule.cron_expression}\nEnabled: ${schedule.enabled}`,
              inline: false
            }))
          )
          .setColor(0x5865F2);

        await interaction.reply({ embeds: [embed] });
        break;
      }

      case 'remove': {
        const id = interaction.options.get('id')?.value as string;
        await this.api.deleteSchedule(id);
        await interaction.reply(`Schedule ${id} removed`);
        break;
      }
    }
  }

  private async handleResultCommand(interaction: CommandInteraction): Promise<void> {
    const id = interaction.options.get('id')?.value as string;
    const task = await this.api.getTask(id);

    if (!task.result) {
      await interaction.reply('No result available for this task');
      return;
    }

    // Send result as file if too long
    if (task.result.length > 2000) {
      await interaction.reply({
        content: 'Result is too long for Discord. Download from web interface.',
        ephemeral: true
      });
    } else {
      await interaction.reply(`\`\`\`\n${task.result}\n\`\`\``);
    }
  }

  private async handleStatusCommand(interaction: CommandInteraction): Promise<void> {
    const status = await this.api.getStatus();

    const embed = new EmbedBuilder()
      .setTitle('Agent Status')
      .addFields(
        { name: 'Status', value: status.status, inline: true },
        { name: 'Version', value: status.version, inline: true },
        { name: 'Uptime', value: `${Math.floor(status.system.uptime / 3600)}h`, inline: true },
        { name: 'Tasks', value: `${status.tasks.total} total\n${status.tasks.pending} pending\n${status.tasks.running} running`, inline: true },
        { name: 'Memory', value: `${((status.system.memory.used / status.system.memory.total) * 100).toFixed(1)}% used`, inline: true }
      )
      .setColor(0x00FF00)
      .setTimestamp();

    await interaction.reply({ embeds: [embed] });
  }

  private async handleHelpCommand(interaction: CommandInteraction): Promise<void> {
    const embed = new EmbedBuilder()
      .setTitle('Mini Agent Help')
      .setDescription('Available commands for managing the AI agent')
      .addFields(
        { name: '/task add', value: 'Create a new task', inline: false },
        { name: '/task list', value: 'List all tasks', inline: false },
        { name: '/task status', value: 'Get task status', inline: false },
        { name: '/task run', value: 'Run a task immediately', inline: false },
        { name: '/task delete', value: 'Delete a task', inline: false },
        { name: '/schedule add', value: 'Create a scheduled task', inline: false },
        { name: '/schedule list', value: 'List all schedules', inline: false },
        { name: '/schedule remove', value: 'Remove a schedule', inline: false },
        { name: '/result', value: 'Get task result', inline: false },
        { name: '/status', value: 'Get agent status', inline: false }
      )
      .setColor(0x5865F2);

    await interaction.reply({ embeds: [embed] });
  }

  async stop(): Promise<void> {
    this.client.destroy();
  }
}

export default DiscordBot;