# Project overview

This project purpose is to build containerized ai agent which can be run via Docker or Docker Compose, witch freedom to perform any tasks 24/7 without any user interactions, based on tasks defined by user beforehand.

## Features
- `docs/features` folder contains files describing specific functionalities that agent should have. Read it every time you're being asked to build somethign new, and use it as additional context to what you've been asked to build. Features might change over the time and it's critical to keep track of changes in this folder

## Development
- your job as AI Agent is to build another ai agent with any means possible, according to requiremets specified particular task pointed from `docs/tasks` folder at the time, as well as according to context of all features described in `docs/features`
- `docs/tasks` folder will contain specific tasks with detailed definition of what to build step by step. You will be asked to build something new every time, executing only one, specified by user task at the time, and ignoring rest of tasks from that folder

## Main assumptions and requirements
- agent should be able to run in container using Docker or Docker Compose
- it should be fully autonomic, able to run 24/7 according to tasks defined by user later on
- it should be based current most popular and free or cheap solutions. Agent itself should use somethign like Claude Code, ord OpenClaw which should be free, but for models and providers it can use paid solutions in cloud, but cheap ones with possibility to select various models depending on task to perform. Start with ollama, but make sure it will be possible to use something else in the future
- only select dependencies and solutions which are considered as fairly stable, and already have some tracktion annd are endorsed by community, do not use newly annoucnced and unproven solutions 
- agent should be fairly lightwight, possible to be launched on mediocre mimi pc in container, and use models from cloud
- final artifact of your work should be Dockerfile and docker-compose.yml file which will allow to build and deploy agent in container 