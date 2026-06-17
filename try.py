import asyncio

from backend.prompt.query_processing import generate_sub_queries
from backend.retrievers.arxiv.arxiv import ArxivSearch
# ab=ArxivSearch()
# a=ab.search('what is mixed-mode oscillation')

# print(a)
b=asyncio.run(generate_sub_queries('什么是混合模式振荡')) 
print(b)