#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
EXAMPLE_FILE="$ROOT_DIR/server/.env.example"
ENV_FILE=${YIQIAO_ENV_FILE:-"$ROOT_DIR/server/.env"}
HISTORY_DIR="$ROOT_DIR/server/history"

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

random_secret() {
  if command -v openssl >/dev/null 2>&1; then
    value=$(openssl rand -base64 48)
  elif command -v python3 >/dev/null 2>&1; then
    value=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
  elif command -v python >/dev/null 2>&1; then
    value=$(python -c 'import secrets; print(secrets.token_urlsafe(48))')
  elif command -v base64 >/dev/null 2>&1 && [ -r /dev/urandom ]; then
    value=$(dd if=/dev/urandom bs=48 count=1 2>/dev/null | base64)
  else
    fail "openssl, Python, or /dev/urandom with base64 is required to generate secrets"
  fi

  value=$(printf '%s' "$value" | tr '/+' '_-' | tr -d '=\r\n')
  [ "${#value}" -ge 63 ] || fail "the system random-number generator returned an invalid secret"

  # neo4j-admin parses a password beginning with "-" as an option.
  printf 'y%.63s' "$value"
}

env_value() {
  awk -v key="$1" 'index($0, key "=") == 1 { print substr($0, length(key) + 2); exit }' "$ENV_FILE"
}

set_env_value() {
  key=$1
  value=$2
  temp_file=$(mktemp "${ENV_FILE}.tmp.XXXXXX") || fail "could not create a temporary environment file"
  chmod 600 "$temp_file" 2>/dev/null || true

  if ! awk -v key="$key" -v value="$value" '
    BEGIN { replaced = 0 }
    index($0, key "=") == 1 {
      if (!replaced) {
        print key "=" value
        replaced = 1
      }
      next
    }
    { print }
    END {
      if (!replaced) print key "=" value
    }
  ' "$ENV_FILE" >"$temp_file"; then
    rm -f "$temp_file"
    fail "could not update $ENV_FILE"
  fi

  mv "$temp_file" "$ENV_FILE"
}

ensure_secret() {
  key=$1
  current=$(env_value "$key")
  normalized=$(printf '%s' "$current" | tr -d '[:space:]')
  if [ -n "$normalized" ] && [ "$normalized" != '""' ] && [ "$normalized" != "''" ]; then
    return
  fi
  set_env_value "$key" "$(random_secret)"
  GENERATED_KEYS="${GENERATED_KEYS}${GENERATED_KEYS:+, }$key"
}

[ -f "$EXAMPLE_FILE" ] || fail "missing template: $EXAMPLE_FILE"
umask 077

if [ -e "$ENV_FILE" ] && [ ! -f "$ENV_FILE" ]; then
  fail "$ENV_FILE exists but is not a regular file"
fi

if [ ! -f "$ENV_FILE" ]; then
  cp "$EXAMPLE_FILE" "$ENV_FILE"
  CREATED_ENV=true
else
  CREATED_ENV=false
fi
chmod 600 "$ENV_FILE" 2>/dev/null || true

GENERATED_KEYS=""
ensure_secret POSTGRES_PASSWORD
ensure_secret NEO4J_PASSWORD
ensure_secret JWT_SECRET
ensure_secret OAUTH_USER_CODE_HMAC_SECRET
ensure_secret OAUTH_AUDIT_HMAC_SECRET

neo4j_password=$(printf '%s' "$(env_value NEO4J_PASSWORD)" | tr -d '[:space:]')
case "$neo4j_password" in
  -*|\"-*|\'-*)
    fail "NEO4J_PASSWORD must not start with '-'; Neo4j interprets it as a command option"
    ;;
esac
mkdir -p "$HISTORY_DIR"

command -v docker >/dev/null 2>&1 || fail "Docker is not installed or is not on PATH"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required (run: docker compose version)"

if ! (cd "$ROOT_DIR/server" && docker compose --env-file "$ENV_FILE" config --quiet); then
  fail "Docker Compose configuration validation failed"
fi

if [ "$CREATED_ENV" = true ]; then
  printf 'Created %s.\n' "$ENV_FILE"
else
  printf 'Kept existing %s.\n' "$ENV_FILE"
fi
if [ -n "$GENERATED_KEYS" ]; then
  printf 'Generated missing secrets: %s.\n' "$GENERATED_KEYS"
else
  printf 'Required secrets were already configured.\n'
fi
printf 'Docker Compose configuration is valid.\n\n'
printf 'Next:\n  cd "%s"\n  docker compose up -d\n' "$ROOT_DIR/server"
