# Dashboard API

Add new endpoint groups in focused `*_api.py` modules with their own routers, then include those routers from `routes.py`. Do not add more endpoints directly to the oversized `routes.py`; migrate existing endpoint groups incrementally as they are changed.
