import pytest
from fastapi import HTTPException

from openswe.slack.staged_files import SlackFileEntry, StagedSlackFile
from openswe.threads import runs
from openswe.threads.runs import _dashboard_images_from_content


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
        runs._image_blocks(images, model_id="fireworks:accounts/fireworks/models/kimi-k3")
    assert failure.value.status_code == 422


@pytest.mark.parametrize(
    "url", ["https://example.com/image.png", "data:image/png,hello", "not an image"]
)
def test_native_image_blocks_reject_unsupported_sources(url: str) -> None:
    with pytest.raises(HTTPException) as failure:
        _dashboard_images_from_content([{"type": "image_url", "image_url": {"url": url}}])
    assert failure.value.status_code == 422


@pytest.mark.parametrize(
    "image",
    [
        {"type": "image", "base64": "aGk=", "mime_type": "image/png", "file_name": "a.png"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGk="}},
    ],
)
async def test_mixed_attachments_stage_the_same_bytes_stored_in_the_transcript(
    monkeypatch: pytest.MonkeyPatch, image: dict[str, object]
) -> None:
    queued: list[SlackFileEntry] = []

    async def queue(
        thread_id: str, entries: list[SlackFileEntry], *, directory: str
    ) -> list[StagedSlackFile]:
        queued.extend(entries)
        return [StagedSlackFile(e.name, f"{directory}/{e.name}") for e in entries]

    monkeypatch.setattr(runs, "queue_slack_files", queue)
    pdf = {"type": "file", "base64": "cGRm", "mime_type": "application/pdf", "file_name": "a.pdf"}
    content = [image, pdf]
    files = [
        *_dashboard_images_from_content(content),
        *runs.DashboardImageBody.files_in(content),
    ]

    paths = await runs.DashboardImageBody.stage_files("t1", files, message_id="m1")
    _, pending = runs._transcript_attachments(files, "m1")

    assert len(queued) == len(pending) == 2
    assert [entry.attachment_id for entry in queued] == [file.attachment_id for file in pending]
    assert [file.data for file in pending] == [b"hi", b"pdf"]
    assert paths == [f"/workspace/.open-swe/attachments/{entry.name}" for entry in queued]
    assert queued[0].name.endswith(".png")
    assert queued[1].name == "a.pdf"
    if image["type"] == "image":
        assert queued[0].name == "a.png"


def test_unsupported_file_types_are_stored_as_opaque_bytes() -> None:
    files = runs.DashboardImageBody.files_in(
        [{"type": "file", "base64": "aGk=", "mime_type": "text/html", "file_name": "a.html"}]
    )
    _, pending = runs._transcript_attachments(files, "m1")

    assert pending[0].mime_type == "application/octet-stream"
    assert pending[0].data == b"hi"


async def test_retried_run_start_stages_files_under_the_same_attachment_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def queue(
        thread_id: str, entries: list[SlackFileEntry], *, directory: str
    ) -> list[StagedSlackFile]:
        return [StagedSlackFile(e.name, f"{directory}/{e.name}") for e in entries]

    monkeypatch.setattr(runs, "queue_slack_files", queue)

    async def stage() -> tuple[list[runs.DashboardImageBody], list[str]]:
        pdf = {
            "type": "file",
            "base64": "aGk=",
            "mime_type": "application/pdf",
            "file_name": "a.pdf",
        }
        files = [
            *_dashboard_images_from_content(
                [{"type": "image_url", "image_url": "data:image/png;base64,aGk="}]
            ),
            *runs.DashboardImageBody.files_in([pdf]),
        ]
        paths = await runs.DashboardImageBody.stage_files("t1", files, message_id="m1")
        return files, paths

    (first, first_paths), (retry, retry_paths) = await stage(), await stage()
    assert [file.attachment_id for file in first] == [file.attachment_id for file in retry]
    assert first_paths == retry_paths
