# Per agent config generation

Goal of this task is to `src/scripts/generate-config.sh` bash script and add capability to merge general hermes config template with particular agent configuration partial, both in yaml format, and generate final config file which will be used by hermes agent.

## Definition of Done
1. Script `src/scripts/generate-config.sh` is able to take parameter variables:
- `template_file`, which is path to hermes template yaml file
- `agents_dir`, which is base directory where script will be searching for agent config partials
- `agent_name` which is agent name used to concatenate path to agent config partial
- `output_file` which is path where final config file will be saved
2. Script `src/scripts/generate-config.sh` reads `template_file` file in yaml format, as well as `${agents_dir}/${agent_name}/hermes.partial.yml` in yaml format, and merges both files together, with agent partial overriding template values.
3. Script `src/scripts/generate-config.sh` saves final merged config in path specified by `output_file` parameter.

## Development rules and context
- use subagents where feasible
- always create implementation plan first according to Definition of Done and all the context from this file, then ask for review before implementing
- generate code for bash script
