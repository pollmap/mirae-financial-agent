#!/bin/sh
set -eu

for secret_path in "$QA_TRANSCRIPT_KEY_FILE" "$QA_AUTH_SECRET_FILE"; do
    if [ ! -r "$secret_path" ]; then
        echo "required QA secret file is not readable: $secret_path" >&2
        exit 78
    fi
    if [ ! -s "$secret_path" ]; then
        echo "required QA secret file is empty: $secret_path" >&2
        exit 78
    fi
done

exec uvicorn qa_chat.main:app \
    --host 0.0.0.0 \
    --port 8090 \
    --workers 1 \
    --no-access-log \
    --proxy-headers \
    --forwarded-allow-ips='*' \
    --limit-concurrency 32
