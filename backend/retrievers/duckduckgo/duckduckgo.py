from itertools import islice
import logging
from ..utils import check_pkg

logger = logging.getLogger(__name__)


class Duckduckgo:
    """
    Duckduckgo API 检索器
    """
    def __init__(self, query, query_domains=None):
        check_pkg('ddgs')
        from ddgs import DDGS
        self.ddg = DDGS()
        self.query = query
        self.query_domains = query_domains or None

    def search(self, max_results=5):
        """
        执行搜索
        :param max_results:
        :return:
        """
       
        try:
            search_response = self.ddg.text(self.query, region='wt-wt', max_results=max_results)
        except Exception as e:
            logger.error(f"Error: {e}. Failed fetching sources. Resulting in empty response.")
            search_response = []
        return search_response
