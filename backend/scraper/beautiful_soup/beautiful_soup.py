import requests

from ..utils import parse_html


class RetryableHTTPError(Exception):
    """Raised for HTTP status codes (429/503/403) that should trigger a retry."""


class BeautifulSoupScraper:

    def __init__(self, link, session=None, user_agent=None, timeout=10, proxy_url=None, **_kwargs):
        self.link = link
        self.session = session
        self.user_agent = user_agent
        self.timeout = timeout
        self.proxy_url = proxy_url

    def scrape(self):
        """
        Scrape content from a webpage via GET request, parse HTML with BeautifulSoup,
        and return cleaned text content after stripping scripts and styles.

        Returns:
          tuple[str, str]: (content, title). On error, returns ("", "").
        """
        try:
            headers = {}
            if self.user_agent:
                headers["User-Agent"] = self.user_agent
            kwargs = {"timeout": self.timeout, "headers": headers}
            if self.proxy_url:
                kwargs["proxies"] = {"http": self.proxy_url, "https": self.proxy_url}
            session = self.session if self.session is not None else requests.Session()
            response = session.get(self.link, **kwargs)

            if response.status_code in (429, 503, 403):
                raise RetryableHTTPError(
                    f"HTTP {response.status_code} for {self.link}"
                )

            return parse_html(response.content)

        except RetryableHTTPError:
            raise
        except Exception as e:
            print("Error! : " + str(e))
            return "", ""
