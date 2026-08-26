"""Provider construction in one place."""

from .config import AIConfig
from .openai_provider import OpenAIProvider
from .groq_provider import GroqProvider
from .provider import AIProvider, AIProviderError


def build_provider(config: AIConfig) -> AIProvider:
    if config.provider == "openai":
        return OpenAIProvider(config.api_key, config.timeout_seconds)
    if config.provider == "groq":
        return GroqProvider(config.api_key, config.timeout_seconds)
    raise AIProviderError("API_PROVIDER_ERROR", f"Unsupported AI provider: {config.provider}")
