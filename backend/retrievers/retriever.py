"""Retriever factory and utilities for GPT Researcher.

This module provides functions to instantiate and manage various
search retriever implementations.
"""


def get_retriever(retriever: str):
    """Get a retriever class by name.

    Args:
        retriever: The name of the retriever to get (e.g., 'google', 'tavily', 'duckduckgo').

    Returns:
        The retriever class if found, None otherwise.

    Supported retrievers:
        - searx: SearX search engine
        - duckduckgo: DuckDuckGo search
        - arxiv: arXiv academic search
        - tavily: Tavily search API
        - custom: Custom user-defined retriever
    """
    match retriever:
        case "searx":
            from .searx.searx import SearxSearch

            return SearxSearch
        case "duckduckgo":
            from .duckduckgo.duckduckgo import Duckduckgo

            return Duckduckgo
        case "arxiv":
            from .arxiv.arxiv import ArxivSearch

            return ArxivSearch
        case "tavily":
            from .tavily.tavily_search import TavilySearch

            return TavilySearch
        case "custom":
            from .custom.custom import CustomRetriever

            return CustomRetriever
        case _:
            return None


def get_retrievers(retrievers: list[str]):
    """
    根据retrievers,默认配置等决定检索器

    Args:
        retrievers (list[str]): The list of retriever names
        setting: The setting object

    Returns:
        list: 用于搜索的检索器类列表
    """
    retriever_classes = [get_retriever(r) or get_default_retriever() for r in retrievers]
    
    return retriever_classes


def get_default_retriever():
    """Get the default retriever class.

    Returns:
        The TavilySearch retriever class as the default search provider.
    """
    from .tavily.tavily_search import TavilySearch

    return TavilySearch