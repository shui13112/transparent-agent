import time
from dataclasses import dataclass


@dataclass
class IpInfoModel:
    ip: str
    port: int
    user: str = ""
    password: str = ""
    protocol: str = "http"
    expired_time_ts: int | None = None

    def is_expired(self, buffer_seconds: int = 30) -> bool:
        if self.expired_time_ts is None:
            return False
        return time.time() >= (self.expired_time_ts - buffer_seconds)
