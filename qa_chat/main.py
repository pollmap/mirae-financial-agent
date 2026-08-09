"""ASGI entrypoint for ``uvicorn qa_chat.main:app``."""

from qa_chat.app import create_app

app = create_app()
