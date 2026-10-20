"""Bangumi 侧：读收藏表、调条目接口。"""

from .client import BangumiClient
from .sheet import read_collection

__all__ = ["BangumiClient", "read_collection"]
