from abc import ABC, abstractmethod

from .types import IpInfoModel


class ProxyProvider(ABC):
    @abstractmethod
    async def get_proxy(self, num: int = 1) -> list[IpInfoModel]:
        """返回指定数量的代理 IP。"""
        ...
