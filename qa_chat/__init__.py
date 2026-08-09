"""Human QA gateway for the HCX-only financial-product agent.

The package is deliberately separate from :mod:`app`: it wraps the immutable
contest HTTP contract and never imports or invokes a language model itself.
"""

from qa_chat.app import create_app
from qa_chat.config import QASettings

__all__ = ["QASettings", "create_app"]
