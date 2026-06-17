import arxiv
import requests
import logging
from datetime import datetime, timezone
import math
from typing import List, Dict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ArxivSearch:
    def __init__(self, query: str, query_domains=None):
        self.query = query
        self.query_domains = query_domains
        self.s2_api_url = "https://api.semanticscholar.org/graph/v1/paper/"
        self.arxiv_client = arxiv.Client()

    def _get_s2_data(self, arxiv_id: str) -> Dict:
        """通过 Arxiv ID 获取 S2 的引用数据"""
        try:
            # S2 支持直接通过 ARXIV:ID 格式查询
            url = f"{self.s2_api_url}ARXIV:{arxiv_id}"
            params = {"fields": "citationCount,title,year"}
            resp = requests.get(url, params=params, timeout=5)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning(f"S2 lookup failed for {arxiv_id}: {e}")
        return {"citationCount": 0}

    def search(self, query: str = "", max_samples: int = 20, max_results: int = 5):
        query = query or self.query
        """
        混合搜索并排序
        :param query: 搜索词
        :param max_samples: 初始采样数量（从 Arxiv 取回多少条来参与重排）
        :param max_results: 最终返回多少条
        """
        # 1. 从 Arxiv 获取初始候选集（Arxiv 默认按相关性排序）
        search_obj = arxiv.Search(
            query=query,
            max_results=max_samples,
            sort_by=arxiv.SortCriterion.Relevance
        )
        
        raw_results = list(self.arxiv_client.results(search_obj))
        if not raw_results:
            return []

        scored_list = []
        now = datetime.now(timezone.utc)

        # 2. 收集数据并进行初步处理
        for rank, res in enumerate(raw_results):
            arxiv_id = res.entry_id.split('/')[-1]
            s2_info = self._get_s2_data(arxiv_id)
            
            # 原始数据提取
            citations = s2_info.get("citationCount", 0)
            # 计算天数差（新鲜度）
            days_old = (now - res.published).days
            # 语义相关性得分：使用 Arxiv 的原始排名作为代理 (rank 0 最相关)
            # 也可以使用相似度算法，这里简单处理：第一名1.0，最后一名0.0
            relevance_score = 1.0 - (rank / max_samples)

            scored_list.append({
                "arxiv_res": res,
                "raw_citations": citations,
                "days_old": days_old,
                "relevance_score": relevance_score
            })

        # 3. 归一化处理 (Normalization)
        # 引用数跨度极大（0 到 几万），推荐用 log 处理
        max_log_cite = math.log1p(max(item["raw_citations"] for item in scored_list) + 1)
        # 时间处理：越新分越高，设置一个 5 年（1825天）的窗口
        max_days = max(item["days_old"] for item in scored_list) if scored_list else 365

        for item in scored_list:
            # 引用得分 (0-1)
            item["cite_score"] = math.log1p(item["raw_citations"]) / max_log_cite if max_log_cite > 0 else 0
            # 新鲜度得分 (0-1)，越近(days小)分数越高
            item["fresh_score"] = 1.0 - (item["days_old"] / max_days) if max_days > 0 else 1.0

            # 4. 计算最终得分 (按用户提供的权重)
            # 分数 = (语义相关性 * 0.5) + (引用数得分 * 0.3) + (时间新鲜度 * 0.2)
            item["final_score"] = (
                (item["relevance_score"] * 0.5) +
                (item["cite_score"] * 0.3) +
                (item["fresh_score"] * 0.2)
            )

        # 5. 排序并取 Top K
        scored_list.sort(key=lambda x: x["final_score"], reverse=True)
        
        final_results = []
        for item in scored_list[:max_results]:
            res = item["arxiv_res"]
            final_results.append({
                "href": res.pdf_url,
                "body": res.summary
            })
            
        return final_results

