from minx_mcp.contracts import LLMError
from minx_mcp.core.llm import LLMProviderError


def test_llm_provider_error_is_an_llm_error() -> None:
    assert issubclass(LLMProviderError, LLMError)
