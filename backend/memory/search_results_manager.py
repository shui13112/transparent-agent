from __future__ import annotations

import hashlib
import json

from pathlib import Path
from typing import List

from langchain_core.documents import Document



def save_and_to_documents(
    results: List[dict],
    base_dir: Path,
) -> List[Document]:
    """
    将抓取器返回的字典列表：
    1. 每个字典保存为一个 json 文件到 search_memory/ 文件夹，以 url 的 md5 为文件名
    2. 转为 LangChain Document 对象列表并返回
    """
    search_memory_dir = base_dir / "search_memory"
    search_memory_dir.mkdir(parents=True, exist_ok=True)

    documents: List[Document] = []

    for r in results:
        url = r.get("url", "")
        raw_content = r.get("raw_content", "")
        title = r.get("title", "")

        if not url:
            continue

        # 保存为 json 文件，以 url 的 md5 值命名
        filename = hashlib.md5(url.encode()).hexdigest() + ".json"
        filepath = search_memory_dir / filename
        filepath.write_text(
            json.dumps(r, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 转为 LangChain Document
        doc = Document(
            page_content=raw_content,
            metadata={
                "source": url,
                "title": title,
                "file_path": str(filepath),
            },
        )
        documents.append(doc)

    return documents
