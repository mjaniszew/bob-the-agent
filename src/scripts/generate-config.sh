#!/bin/bash
set -e

template=$(cat /app/config/hermes.template.yaml)

# Substitute ${VAR} placeholders with env values
output="$template"
while IFS= read -r var; do
  val="${!var}"
  if [[ -z "$val" ]]; then
    echo "Missing env var: $var" >&2
    exit 1
  fi
  output="${output//\$\{$var\}/$val}"
done < <(grep -oP '\$\{\K\w+(?=\})' /app/config/hermes.template.yaml | sort -u)

echo "$output" > /opt/data/config.yaml
echo "First run: config.yaml generated"