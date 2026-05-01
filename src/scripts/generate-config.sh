#!/bin/sh
set -e

output=$(cat /app/config/hermes.template.yaml)

# Extract unique ${VAR} placeholder names to replace with env variables
vars=$(grep -o '\${[^}]*}' /app/config/hermes.template.yaml | sed 's/\${//;s/}//' | sort -u)

for var in $vars; do
  val=$(eval echo "\$$var")
  if [ -z "$val" ]; then
    echo "Missing env var: $var" >&2
    exit 1
  fi
  output=$(echo "$output" | sed "s/\${$var}/$val/g")
done

echo "$output" > /opt/data/config.yaml
echo "First run: config.yaml generated"