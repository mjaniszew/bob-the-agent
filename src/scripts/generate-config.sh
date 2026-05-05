#!/bin/bash
set -e

# generate-config.sh - Merge hermes template with agent partial config
#
# Usage: generate-config.sh <template_file> <agents_dir> <agent_name> <output_file>
#
# Arguments:
#   template_file  Path to the hermes template YAML file
#   agents_dir     Base directory containing agent partial YAMLs
#   agent_name     Name of the agent (subdirectory under agents_dir)
#   output_file    Path where the merged config will be written
#
# The script looks for a partial override at:
#   ${agents_dir}/${agent_name}/hermes.partial.yml
#
# If the partial file does not exist, the template is used as-is.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -lt 4 ]]; then
  echo "Usage: $0 <template_file> <agents_dir> <agent_name> <output_file>" >&2
  echo "" >&2
  echo "Generate hermes config by merging template with agent partial." >&2
  exit 1
fi

TEMPLATE_FILE="$1"
AGENTS_DIR="$2"
AGENT_NAME="$3"
OUTPUT_FILE="$4"

# Validate template file exists
if [[ ! -f "$TEMPLATE_FILE" ]]; then
  echo "Error: Template file not found: $TEMPLATE_FILE" >&2
  exit 1
fi

# Determine partial path
PARTIAL_FILE="${AGENTS_DIR}/${AGENT_NAME}/hermes.partial.yml"

MERGE_SCRIPT="${SCRIPT_DIR}/merge-yaml.mjs"

if [[ -f "$PARTIAL_FILE" && -r "$PARTIAL_FILE" ]]; then
  echo "Merging template with partial for agent: ${AGENT_NAME}"
  node "$MERGE_SCRIPT" "$TEMPLATE_FILE" "$PARTIAL_FILE" "$OUTPUT_FILE"
else
  echo "No partial for agent '${AGENT_NAME}'; using template as-is"
  mkdir -p "$(dirname "$OUTPUT_FILE")"
  cp "$TEMPLATE_FILE" "$OUTPUT_FILE"
  echo "Config written to: ${OUTPUT_FILE}"
fi