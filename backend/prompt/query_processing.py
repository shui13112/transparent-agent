import json
import os
from time import timezone
import json_repair
import datetime
from typing import Any, List, Dict
import logging
from openai import OpenAI

from retrievers.retriever import get_retrievers
from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
async def get_search_results(query: str, retriever: Any, query_domains: List[str] = None, max_results: int = None) -> List[Dict[str, Any]]:
    """
    得到给定问题的初步网页查询结果

    Args:
        query: 查询的问题
        retriever: 检索器类
        query_domains: 可选的搜索域名列表
        max_results: 每个检索器返回的最大URL数量，默认使用 settings.max_retrieved_urls


    Returns:
        查询结果的列表，每个结果是一个包含"title", "href", "body"的字典
    """
    if max_results is None:
        max_results = settings.max_retrieved_urls

    search_retriever = retriever(query, query_domains=query_domains)

    return search_retriever.search(max_results=max_results)


async def generate_search_queries_prompt(
    question: str,
    context: List[str] = [],
):
    """为给出的问题生成子搜索的提示词
    Args:
        question (str): 需要生成更好搜索查询的原问题
        context (str): 用于更好地理解任务并获取实时网络信息的背景信息
    Returns: str: The search queries prompt for the given question
    """
    task = f"{question}"
    max_iterations = settings.max_iterations

    context_prompt = f"""

上下文: {context}
利用此上下文来指导并优化您的问题。该上下文提供了实时的网络信息，能够帮助您生成更具体、更相关的问题。请考虑上下文中提及的任何当前事件、最新进展或特定细节，这些都可能有助于完善您的问题。
""" if context else ""

    dynamic_example = ", ".join([f'"query {i+1}"' for i in range(max_iterations)])

    return f"""你是一名经验丰富的研究助理，负责生成针对性的问题以获取与以下原始查询相关的相关信息，让用户可以全面了解与原始查询相关的知识。
原始查询: "{task}".针对这个任务编写最多 {max_iterations} 个问题，以便在网络上进行搜索。编写的问题一定要和用户的原始查询密切相关，
并且要具体、清晰，以便在网络上搜索时能够得到有用的结果。当问题涉及到的概念偏向学术性时，重构的问题以英文给出，其它情况下以中文给出。所有问题都应该是对原始查询的一个具体方面的深入挖掘，
或者是一个相关但更具体的子问题。请确保生成的问题涵盖了原始查询的不同方面，以便在网络上搜索时能够获得全面的信息。
{context_prompt}
您必须以以下格式的字符串列表回答：[{dynamic_example}]。
回复内容仅需包含该列表。再次注意，当问题涉及到的概念偏向学术性时，重构的问题以英文给出，其它情况下以中文给出。
"""

async def generate_sub_queries(
    query: str,
) -> List[str]:
    """
    用轻量模型生成子问题

    Args:
        query: 原始查询
        max_iterations: 研究迭代的最大次数
        

    Returns:
        A list of sub-queries
    """
    retriever_classes = get_retrievers(settings.retrievers)
    retriever = retriever_classes[0]  # 仅使用第一个
    search_results = await get_search_results(query, retriever)
    context = [r["body"] for r in search_results if r.get("body")]
    gen_queries_prompt = await generate_search_queries_prompt(
        query,
        context=context
    )
    
    try:
        client = OpenAI(
            api_key=settings.fast_llm_api_key,
            base_url=settings.fast_llm_base_url,
        )
        request_payload = {
            "model": settings.fast_llm_model,
            "messages": [
                {"role": "user", "content": gen_queries_prompt[:500] + "…[已截断]"}
            ],
            "stream": False,
            "thinking": {"type": "enabled"},
        }
        

        response = client.chat.completions.create(
            model=settings.fast_llm_model,
            messages=[{"role": "user", "content": gen_queries_prompt}],
            stream=False,
            extra_body={"thinking": {"type": "enabled"}},
        )
        client.close()
        return json_repair.loads(response.choices[0].message.content)
    except Exception as e:
        logger.warning(f"Error with FAST LLM: {e}. ")
        return [query]


async def generate_sub_queries_bare(
    query: str,
) -> list[str]:
    """用轻量模型生成子问题（无初步搜索，适合 RAG 场景）。

    与 generate_sub_queries 不同，此函数不做初步网页搜索，
    直接让大模型根据自身知识对问题进行拆解重构。

    Args:
        query: 原始查询

    Returns:
        A list of sub-queries
    """
    gen_queries_prompt = await generate_search_queries_prompt(
        query,
        context=[],
    )

    try:
        client = OpenAI(
            api_key=settings.fast_llm_api_key,
            base_url=settings.fast_llm_base_url,
        )
        response = client.chat.completions.create(
            model=settings.fast_llm_model,
            messages=[{"role": "user", "content": gen_queries_prompt}],
            stream=False,
        )
        client.close()
        return json_repair.loads(response.choices[0].message.content)
    except Exception as e:
        logger.warning(f"Error with FAST LLM (bare): {e}. ")
        return [query]


