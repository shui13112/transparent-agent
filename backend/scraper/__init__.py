from .beautiful_soup.beautiful_soup import BeautifulSoupScraper
from .arxiv.arxiv import ArxivScraper
from .pymupdf.pymupdf import PyMuPDFScraper
from .browser.nodriver_scraper import NoDriverScraper
from .browser.cdp_scraper import CDPScraper
from .scraper import Scraper

__all__ = [
    "BeautifulSoupScraper",
    "ArxivScraper",
    "PyMuPDFScraper",
    "NoDriverScraper",
    "CDPScraper",
    "Scraper",
]
