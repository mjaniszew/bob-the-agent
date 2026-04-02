# Skills

This file describes which skills should be used by agent to perform various tasks and gives some context how they should be implemented or included in agent if already available.

## Current Implementation Status

The skills in `src/skills/` are TypeScript implementations for the agent's capability layer.

**Implemented Skills (TypeScript):**
| Skill | Status | Description |
|-------|--------|-------------|
| web-search | ✅ Implemented | Web search with multiple sources |
| data-extraction | ✅ Implemented | Extract data from files and URLs |
| math-operations | ✅ Implemented | Mathematical calculations |
| document-creation | ✅ Implemented | PDF and DOCX generation |
| notifications | ✅ Implemented | Discord, email notifications |
| scheduling | ✅ Implemented | Cron-based task scheduling |
| grok-search | ✅ Implemented | X.AI Grok search for web and X.com |
| aws-s3 | ✅ Implemented | S3 upload and URL generation |

**System Tool Skills (SKILL.md only):**
| Skill | Status | Description |
|-------|--------|-------------|
| playwright | ✅ Available | Browser automation via playwright-cli |

**Environment Variables Required:**
- `XAI_API_KEY` - For Grok Search
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_BUCKET`, `AWS_S3_REGION` - For AWS S3
- `PLAYWRIGHT_CLI` - Installed in PATH for Playwright skill

---

## Web Search
1. Agent should be able to freely use web search to find information about any topic defined by user.
2. Search should be performed via multiple popular sources eg. Google, Microsoft, to make sure different angles are covered
3. Search should work in parallel, so that results from different sources are loaded independly into agent context
4. Search should be performed as one or many subagents by main agent. How many agents to use should be determined by main agent based on complexity of task and number of results to be found
5. Search should be possible to be done deeply, meanint that if task requires it, not only top results should be used, but also deeper search for longer period of time, even for hours if necessary and required by task.
6. If complexity of task and quantity of serch results requires it, there should be possibility to store search results in some kind of database, or as files, so that they can be used later by agent to perform other tasks

## Data Extraction and Deep Analysis
1. Agent should be able to extract data from any source, including web search results, pdf and other documents
2. Agent should be able to analyze fairly large quantity of data, and extract information from it
3. Agent should be able to select specific data models that fits the purpose of particular tasks eg. economic data analysis, technical data analysis, etc.
4. Agent should be able to cross-check that data with other data sources already stored in files or database

## Math operations
1. Agent should be able to use proper and accurate math operations to perform calculations and complex data analysis
2. Agent should be able to choose separate math models depending on task to perform better calculations

## Documents creation
1. Agent should be able to create documents in various formats, including pdf, docx
2. Documents should be possible to be created in various languages, including English, Polish
3. Documents should be possible to be created in varius structures eg. serving purposes of reports, articles, economic analysis, etc.

## Notifications
1. Agent should have possibility to send notifications to user when task is being performed or in other cases defined in task by user
2. Notifications can be: push notifications, Discord bot notifications, email notifications via amazon ses etc. Type of notification should be also be decided based on task definition provided by user

## Scheduling
1. Agent should be able to schedulre running particular actions based on task defined by user
2. Agent should be able to provide possibility to manage scheduled tasks: add, remove, list etc. Managment can be done via CLI
3. Schedule managemnt should be possible via CLI eg. after connecting to docker container via ssh, or via Discord bot

## Grok Search
1. Agent should be able to search web and X.com (Twitter) using x.AI's Grok API
2. Should support parallel searching of both web and social media
3. Should support filtering by domains, X handles, and date ranges
4. Should support image and video understanding for X.com results
5. Requires XAI_API_KEY environment variable

## AWS S3
1. Agent should be able to upload files to AWS S3 buckets
2. Should generate presigned URLs for time-limited access to private objects
3. Should generate public URLs for objects in public buckets (no credentials required)
4. Should support both direct content upload and file path upload
5. Requires AWS credentials and bucket configuration via environment variables

## Playwright (Browser Automation)
1. Agent should be able to open and visit complex websites using playwright-cli
2. Should interact with graphical interfaces of websites, take screenshots
3. Should perform web automations, execute scripts in browser
4. This skill uses playwright-cli system tool, not TypeScript implementation
5. Requires playwright-cli to be installed and available in PATH