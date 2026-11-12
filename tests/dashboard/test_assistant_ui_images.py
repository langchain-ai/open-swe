import pytest
from fastapi import HTTPException

from openswe.slack.staged_files import SlackFileEntry, StagedSlackFile
from openswe.threads import runs
from openswe.threads.runs import _dashboard_images_from_content, _validate_command_images


@pytest.mark.parametrize(
    "url", ["data:image/png;base64,aGVsbG8=", {"url": "data:image/png;base64,aGVsbG8="}]
)
def test_native_image_blocks_are_validated_against_the_model(url: str | dict[str, str]) -> None:
    content = [{"type": "image_url", "image_url": url}]
    images = _dashboard_images_from_content(content)
    assert len(images) == 1
    assert images[0].mime_type == "image/png"
    assert images[0].base64 == "aGVsbG8="
    with pytest.raises(HTTPException) as failure:
        _validate_command_images(content, model_id="fireworks:accounts/fireworks/models/kimi-k3")
    assert failure.value.status_code == 422


@pytest.mark.parametrize(
    "url", ["https://example.com/image.png", "data:image/png,hello", "not an image"]
)
def test_native_image_blocks_reject_unsupported_sources(url: str) -> None:
    with pytest.raises(HTTPException) as failure:
        _dashboard_images_from_content([{"type": "image_url", "image_url": {"url": url}}])
    assert failure.value.status_code == 422


async def test_file_blocks_are_queued_for_the_sandbox_and_replaced_by_a_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued: list[SlackFileEntry] = []

    async def queue(
        thread_id: str, entries: list[SlackFileEntry], *, directory: str
    ) -> list[StagedSlackFile]:
        queued.extend(entries)
        return [StagedSlackFile(e.name, f"{directory}/{e.name}") for e in entries]

    monkeypatch.setattr(runs, "queue_slack_files", queue)
    pdf = {"type": "file", "base64": "aGk=", "mime_type": "application/pdf", "file_name": "a.pdf"}
    params = {"input": {"messages": [{"content": [pdf, {"type": "text", "text": "read it"}]}]}}

    files = await runs.DashboardImageBody.stage_files("t1", params)

    assert [f.attachment_id for f in files] == [e.attachment_id for e in queued]
    content = params["input"]["messages"][0]["content"]
    assert [block["type"] for block in content] == ["text", "text"]
    assert "/workspace/.open-swe/attachments/a.pdf" in content[1]["text"]
    pdf["mime_type"] = "application/zip"
    with pytest.raises(HTTPException):
        await runs.DashboardImageBody.stage_files(
            "t1", {"input": {"messages": [{"content": [pdf]}]}}
        )


async def test_retried_run_start_stages_files_under_the_same_attachment_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def queue(
        thread_id: str, entries: list[SlackFileEntry], *, directory: str
    ) -> list[StagedSlackFile]:
        return [StagedSlackFile(e.name, f"{directory}/{e.name}") for e in entries]

    monkeypatch.setattr(runs, "queue_slack_files", queue)

    async def stage() -> list[runs.DashboardImageBody]:
        pdf = {
            "type": "file",
            "base64": "aGk=",
            "mime_type": "application/pdf",
            "file_name": "a.pdf",
        }
        params = {"input": {"messages": [{"id": "m1", "content": [pdf]}]}}
        return await runs.DashboardImageBody.stage_files("t1", params)

    first, retry = await stage(), await stage()
    assert first[0].attachment_id == retry[0].attachment_id
