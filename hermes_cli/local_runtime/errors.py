"""Local model setup failures, independent of HTTP frameworks."""
class LocalModelError(RuntimeError):
    def __init__(self, detail: str, status_code: int | None = None):
        super().__init__(detail)
        self.detail = detail
