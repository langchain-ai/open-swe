import httpx2

DEFAULT_HTTP_TIMEOUT = httpx2.Timeout(30.0, connect=10.0)
