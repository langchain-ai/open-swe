import pytest
from fastapi import HTTPException

from agent.threads.runs import _dashboard_images_from_content, _validate_command_images


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
        _validate_command_images(
            content, model_id="fireworks:accounts/fireworks/models/deepseek-v4-pro"
        )
    assert failure.value.status_code == 422


@pytest.mark.parametrize(
    "url", ["https://example.com/image.png", "data:image/png,hello", "not an image"]
)
def test_native_image_blocks_reject_unsupported_sources(url: str) -> None:
    with pytest.raises(HTTPException) as failure:
        _dashboard_images_from_content([{"type": "image_url", "image_url": {"url": url}}])
    assert failure.value.status_code == 422
