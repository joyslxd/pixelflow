"""Step helpers for setup wizard."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from wizard.providers import SEARCH_PROVIDERS, WEB_FETCH_PROVIDERS, SearchProvider


@dataclass
class SearchDecision:
    search_provider: SearchProvider | None
    search_api_key: str | None
    fetch_provider: SearchProvider | None
    fetch_api_key: str | None


def print_header(text: str) -> None:
    print(f"\n{text}\n")


def print_success(text: str) -> None:
    print(f"✓ {text}")


def print_info(text: str) -> None:
    print(f"• {text}")


def ask_choice(prompt: str, options: list[SearchProvider], default: int = 0) -> int:
    print_header(prompt)
    for i, option in enumerate(options):
        print(f"{i}: {option.display_name}")
    return default


def ask_secret(prompt: str) -> str:
    return input(f"{prompt}: ").strip()


def run_search_step() -> SearchDecision:
    print_header("Configure web search and web fetch providers")

    search_index = ask_choice("Select web search provider:", SEARCH_PROVIDERS, default=0)
    search_provider = SEARCH_PROVIDERS[search_index]
    print_success(f"selected web search: {search_provider.name}")

    fetch_index = ask_choice("Select web fetch provider:", WEB_FETCH_PROVIDERS, default=0)
    fetch_provider = WEB_FETCH_PROVIDERS[fetch_index]
    print_success(f"selected web fetch: {fetch_provider.name}")

    asked: dict[str, str] = {}

    def resolve_api_key(provider: SearchProvider | None) -> str | None:
        if provider is None or provider.env_var is None:
            return None
        if provider.env_var in asked:
            return asked[provider.env_var]
        api_key = ask_secret(f"{provider.env_var}")
        asked[provider.env_var] = api_key
        return api_key

    search_api_key = resolve_api_key(search_provider)
    fetch_api_key = resolve_api_key(fetch_provider)
    return SearchDecision(search_provider, search_api_key, fetch_provider, fetch_api_key)


search = sys.modules[__name__]
