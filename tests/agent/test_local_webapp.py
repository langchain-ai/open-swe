import httpx

from agent import local_webapp


async def test_exports_desktop_thread_feedback(monkeypatch):
    exported = []

    async def create_feedback(thread_id, key, *, score, comment, source_info):
        exported.append((thread_id, key, score, comment, source_info))
        return True

    monkeypatch.setattr(local_webapp, "create_langsmith_thread_feedback", create_feedback)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=local_webapp.app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/open-swe/feedback",
            json={"thread_id": "local-1", "rating": "bad", "comment": "  More detail.  "},
        )

    assert response.json() == {"exported": True}
    assert exported == [
        (
            "local-1",
            "rating",
            0.0,
            "More detail.",
            {"source": "desktop_thread_feedback"},
        )
    ]
