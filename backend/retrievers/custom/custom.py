from typing import Any, Dict, List, Optional
import logging
import requests
import os

logger = logging.getLogger(__name__)


class CustomRetriever:
    """
    Custom API Retriever
    """

    def __init__(self, query: str, query_domains=None):
        self.endpoint = os.getenv('RETRIEVER_ENDPOINT')
        if not self.endpoint:
            raise ValueError("RETRIEVER_ENDPOINT environment variable not set")

        self.params = self._populate_params()
        self.query = query

    def _populate_params(self) -> Dict[str, Any]:
        """
        Populates parameters from environment variables prefixed with 'RETRIEVER_ARG_'
        """
        return {
            key[len('RETRIEVER_ARG_'):].lower(): value
            for key, value in os.environ.items()
            if key.startswith('RETRIEVER_ARG_')
        }

    def search(self, max_results: int = 5) -> Optional[List[Dict[str, Any]]]:
        """
        Performs the search using the custom retriever endpoint.

        :param max_results: Maximum number of results to return (not currently used)
        :return: JSON response in the format:
            [
              {
                "href": "http://example.com/page1",
                "body": "Content of page 1"
              },
              {
                "href": "http://example.com/page2",
                "body": "Content of page 2"
              }
            ]
        """
        try:
            response = requests.get(self.endpoint, params={**self.params, 'query': self.query}, timeout=30)
            response.raise_for_status()
            results = response.json()
            # 统一字段名：url→href, raw_content→body
            normalized = []
            for r in results:
                normalized.append({
                    "href": r.get("url", r.get("href", "")),
                    "body": r.get("raw_content", r.get("body", ""))
                })
            return normalized
        except requests.RequestException as e:
            logger.error(f"Failed to retrieve search results: {e}")
            return None