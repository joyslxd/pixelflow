"""Data providers for the interactive setup wizard."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LLMProvider:
    name: str
    display_name: str
    use: str
    api_key_field: str
    env_var: str | None
    models: list[str]
    default_model: str


@dataclass(frozen=True)
class SearchProvider:
    name: str
    display_name: str
    use: str
    env_var: str | None
    tool_name: str = "web_search"


LLM_PROVIDERS: list[LLMProvider] = [
    LLMProvider(
        name="openai",
        display_name="OpenAI",
        use="langchain_openai:ChatOpenAI",
        api_key_field="api_key",
        env_var="OPENAI_API_KEY",
        models=["gpt-5", "gpt-4o"],
        default_model="gpt-5",
    ),
    LLMProvider(
        name="deepseek",
        display_name="DeepSeek",
        use="volcengine:ChatDeepSeek",
        api_key_field="api_key",
        env_var="DEEPSEEK_API_KEY",
        models=["deepseek-chat", "deepseek-reasoner"],
        default_model="deepseek-chat",
    ),
    LLMProvider(
        name="claude",
        display_name="Claude",
        use="deerflow.models.claude_provider:ClaudeChatModel",
        api_key_field="api_key",
        env_var="CLAUDE_CODE_OAUTH_TOKEN",
        models=["claude-sonnet-4-6"],
        default_model="claude-sonnet-4-6",
    ),
    LLMProvider(
        name="gemini",
        display_name="Gemini",
        use="langchain_google_genai:ChatGoogleGenerativeAI",
        api_key_field="gemini_api_key",
        env_var="GEMINI_API_KEY",
        models=["gemini-2.5-flash", "gemini-2.0-flash"],
        default_model="gemini-2.5-flash",
    ),
    LLMProvider(
        name="qwen",
        display_name="Qwen",
        use="dashscope:ChatTongyi",
        api_key_field="api_key",
        env_var="QWEN_API_KEY",
        models=["qwen-plus", "qwen-max"],
        default_model="qwen-plus",
    ),
    LLMProvider(
        name="moonshot",
        display_name="Moonshot AI",
        use="langchain_openai:ChatOpenAI",
        api_key_field="api_key",
        env_var="MOONSHOT_API_KEY",
        models=["kimi-k2", "kimi-k2-preview"],
        default_model="kimi-k2",
    ),
    LLMProvider(
        name="openrouter",
        display_name="OpenRouter",
        use="langchain_openai:ChatOpenAI",
        api_key_field="api_key",
        env_var="OPENROUTER_API_KEY",
        models=["google/gemini-2.5-flash", "anthropic/claude-4-sonnet"],
        default_model="google/gemini-2.5-flash",
    ),
    LLMProvider(
        name="codex",
        display_name="Codex CLI",
        use="deerflow.models.openai_codex_provider:CodexChatModel",
        api_key_field="api_key",
        env_var=None,
        models=["gpt-5.4"],
        default_model="gpt-5.4",
    ),
]


SEARCH_PROVIDERS: list[SearchProvider] = [
    SearchProvider(
        name="ddg",
        display_name="DuckDuckGo",
        use="deerflow.community.ddg_search.tools:web_search_tool",
        env_var=None,
    ),
    SearchProvider(
        name="tavily",
        display_name="Tavily",
        use="deerflow.community.tavily.tools:web_search_tool",
        env_var="TAVILY_API_KEY",
    ),
    SearchProvider(
        name="serpapi",
        display_name="SerpAPI",
        use="deerflow.community.serpapi.tools:web_search_tool",
        env_var="SERPAPI_API_KEY",
    ),
    SearchProvider(
        name="exa",
        display_name="Exa",
        use="deerflow.community.exa.tools:web_search_tool",
        env_var="EXA_API_KEY",
    ),
    SearchProvider(
        name="firecrawl",
        display_name="Firecrawl",
        use="deerflow.community.firecrawl.tools:web_search_tool",
        env_var="FIRECRAWL_API_KEY",
    ),
]


WEB_FETCH_PROVIDERS: list[SearchProvider] = [
    SearchProvider(
        name="firecrawl",
        display_name="Firecrawl",
        use="deerflow.community.firecrawl.tools:web_fetch_tool",
        env_var="FIRECRAWL_API_KEY",
        tool_name="web_fetch",
    ),
    SearchProvider(
        name="exa",
        display_name="Exa",
        use="deerflow.community.exa.tools:web_fetch_tool",
        env_var="EXA_API_KEY",
        tool_name="web_fetch",
    ),
    SearchProvider(
        name="jina",
        display_name="Jina AI",
        use="deerflow.community.jina_ai.tools:web_fetch_tool",
        env_var=None,
        tool_name="web_fetch",
    ),
]
