"""提供给检索器的实用函数。

该模块提供各种检索器实现所需要使用的辅助函数和常量。
"""

import importlib.util
import logging
import os
import sys

logger = logging.getLogger(__name__)


def check_pkg(pkg: str) -> None:
    """
    检查一个包是否已经安装，如果没有安装则抛出 ImportError
    
    Args:
        pkg (str): 包名
    
    Raises:
        ImportError: 如果包未安装
    """
    if not importlib.util.find_spec(pkg):
        pkg_kebab = pkg.replace("_", "-")
        raise ImportError(
            f"Unable to import {pkg_kebab}. Please install with "
            f"`pip install -U {pkg_kebab}`"
        )

# Valid retrievers for fallback (auto-detected from directory)
VALID_RETRIEVERS = [
    "arxiv",
    "custom",
    "duckduckgo",
    "searx",
    "tavily",
    "mock",
]

def get_all_retriever_names():
    """
    得到所有可用检索器的名称
    :return: 所有可用检索器名称的列表
    :rtype: list
    """
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Get all items in the current directory
        all_items = os.listdir(current_dir)
        
        # Filter out only the directories, excluding __pycache__
        retrievers = [
            item for item in all_items 
            if os.path.isdir(os.path.join(current_dir, item)) and not item.startswith('__')
        ]
        
        return retrievers
    except Exception as e:
        logger.error(f"Error getting retrievers: {e}")
        return VALID_RETRIEVERS
