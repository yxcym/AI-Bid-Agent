"""业务异常。抛它 = 让 API 返回 4xx 和统一响应体，而不是 500 堆栈。"""

from __future__ import annotations


class BizException(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
