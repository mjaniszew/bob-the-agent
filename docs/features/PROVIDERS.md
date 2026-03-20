# Providers

This file describes which providers should be used by agent to perform tasks and how.

## Ollama
1. Agent should be abble to use ollama as a provider for models, for both cloud and local models.
2. Ollama can be bundled along with agent in same container for initial phase, but it should be possible to use it as a separate container spinned up via Docker Compose along with Agent container eventualy.
3. Ollama API_KEYS should be used. User shoudl defined API_KEY as env variable eg. in `docker-compose.yaml` or in any other way that is considered as golden standard when using docker compose