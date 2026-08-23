#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_dir=$(CDPATH= cd -- "$script_dir/../.." && pwd)

cd "$repo_dir"

docker compose \
  --env-file "$script_dir/.env" \
  -f "$script_dir/compose.yaml" \
  config --quiet

docker compose \
  --env-file "$script_dir/.env" \
  -f "$script_dir/compose.yaml" \
  build --pull

docker compose \
  --env-file "$script_dir/.env" \
  -f "$script_dir/compose.yaml" \
  up -d --remove-orphans

web_container=$(docker compose \
  --env-file "$script_dir/.env" \
  -f "$script_dir/compose.yaml" \
  ps -q web)

attempt=0
while [ "$attempt" -lt 30 ]; do
  health=$(docker inspect --format '{{.State.Health.Status}}' "$web_container")
  if [ "$health" = "healthy" ]; then
    docker compose \
      --env-file "$script_dir/.env" \
      -f "$script_dir/compose.yaml" \
      ps
    exit 0
  fi
  if [ "$health" = "unhealthy" ]; then
    break
  fi
  attempt=$((attempt + 1))
  sleep 3
done

docker compose \
  --env-file "$script_dir/.env" \
  -f "$script_dir/compose.yaml" \
  logs --tail 100 web
exit 1
