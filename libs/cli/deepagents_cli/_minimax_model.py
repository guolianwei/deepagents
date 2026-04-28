"""Custom MiniMax model that wraps ChatAnthropic.

This allows using MiniMax models (which are API-compatible with Anthropic)
through the Anthropic provider with the correct authentication header.
"""

import os
from typing import Any

import anthropic
from langchain_anthropic import ChatAnthropic


class MiniMaxChatModel(ChatAnthropic):
    """ChatAnthropic wrapper for MiniMax API compatibility.

    MiniMax's API is compatible with Anthropic's API but uses x-api-key
    header instead of Authorization: Bearer.
    """

    def __init__(self, **kwargs: Any) -> None:
        # Extract parameters before passing to parent
        api_key = kwargs.pop("api_key", None)
        base_url = kwargs.pop("base_url", None) or kwargs.pop("anthropic_api_url", None)

        # Initialize parent first
        super().__init__(**kwargs)

        # Override client with correct auth headers for MiniMax
        if api_key is not None:
            api_key_str = api_key.get_secret_value() if hasattr(api_key, "get_secret_value") else str(api_key)
        else:
            api_key_str = self.anthropic_api_key.get_secret_value() if hasattr(self.anthropic_api_key, "get_secret_value") else str(self.anthropic_api_key)

        # Temporarily unset ANTHROPIC_AUTH_TOKEN to prevent the SDK from sending
        # Authorization: Bearer header (MiniMax only accepts x-api-key header)
        auth_token_env = os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
        try:
            self._client = anthropic.Anthropic(
                api_key=api_key_str,
                base_url=base_url or self.anthropic_api_url,
            )
        finally:
            # Restore the env var if it was set
            if auth_token_env is not None:
                os.environ["ANTHROPIC_AUTH_TOKEN"] = auth_token_env