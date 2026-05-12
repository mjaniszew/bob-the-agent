# Update documentation after docker changes

Goal of this task is to update entire documentation inlcuding readme, architecture, installation etc. according to current state of project. Biggest change that has been made is that from now compose.yaml does not causes to build three separate ddocker images for each agent. Instead it expects image to be prebuild and uses it.

## Definition of Done
1. Documentation in `docs/*` is updated according to current state of project
2. Readme is updated with new information about how to build image and run docker compose
3. Installation instructions are updated to reflect changes in project

## Additional context
- from now on docker compose expect to have bob-the-agent image to be build when docker compose is being run either locally, or pulled from registry (not pushed at the moment)
- compose.yaml has been changed to use one image for all agents, and have now common configurations for all agents in order to reduce duplication
- there's new file `run-docker.sh` that can be used to build image and run docker compose at once locally. In most environment best practice is to pull image from registry instead, or build with separate `docker build -t bob-the-agent:latest -f dockerfiles/Dockerfile.hermes .` command, and only then run docker compose eg. using Portainer or Komodo