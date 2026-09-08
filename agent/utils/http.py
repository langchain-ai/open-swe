import httpx2 as httpx

DEFAULT_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
