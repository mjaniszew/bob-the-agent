# General

This file describes general features and requirements that agent should have.

## Autonomy
1. Agent should have full autonomy. It's main requirement is that it should never ask any questions to run particular commands, how to execue task etc. It should just take wll defined task and execute it, or stop and return failure if task is impossible to be execured.
2. If available it should have yolo mode or similar configured to run tasks without any user interaction.

## Mounting external volumes / shares
1. Container should have possibility to mount external shares / voluems defined in variables eg. in `docker-compose.yaml` or in other way that is currently golder indrustry standard for containers.
2. That external volume / share should serve as place to store any result files, database, or place where user can put specific files to be read by agent.

## CLI
1. Agent should have CLI possible to be accessed via ssh to Docker compose
2. CLI can be something that comes with particular agent solution if fits the requirement eg. Claude Code CLI, or OpenClaw CLI
3. CLI should allow to add, run or delete tasks defined by user
4. CLI should allow to manage scheduling of tasks: add, list, remove etc.
5. CLI should allow to manage agent's skills, capabilities etc.
6. CLI should allow to select/change models providers

## Discord Bot
1. Agent should have connection with Discord bot that can be used to communicate
2. Discord bot should allow to add, run or delete tasks defined by user
3. Discord bot should allow to display tasks results either directly, or allow to download files with results

## Web Interface
1. Agent should have simple web interface served from same Docker Container over configured port, deault being 81234
2. Interface should be simple with basic authorization with username and password
3. Interface should list all the tasks that were run by agent and are being processed or scheduled, with possibility to see their results
4. Interface should display basic information about agent, like version, number of tasks, some basic diagnostic informations eg. about cpu and memory usage
5. Interface should be build using React and Node.js, served with nginx in container