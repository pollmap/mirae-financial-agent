#!/bin/sh
set -eu

read_secret() {
    variable_name="$1"
    secret_path="$2"
    if [ ! -r "$secret_path" ]; then
        echo "required secret file is not readable: $secret_path" >&2
        exit 78
    fi
    secret_value="$(cat "$secret_path")"
    if [ -z "$secret_value" ]; then
        echo "required secret file is empty: $secret_path" >&2
        exit 78
    fi
    export "$variable_name=$secret_value"
}

read_secret CLOVA_STUDIO_API_KEY /run/secrets/hcx_api_key
read_secret CLARIFICATION_SIGNING_KEY /run/secrets/engine_clarification_signing_key

exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8080 \
    --workers 1 \
    --no-access-log \
    --proxy-headers \
    --limit-concurrency 64
