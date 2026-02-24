from __future__ import annotations

from agent.utils.multimodal import extract_image_urls


def test_extract_image_urls_empty() -> None:
    assert extract_image_urls("") == []


def test_extract_image_urls_markdown_and_direct_dedupes() -> None:
    text = (
        "Here is an image ![alt](https://example.com/a.png) and another "
        "![https://example.com/b.JPG?size=large plus a repeat https://example.com/a.png"
    )

    assert extract_image_urls(text) == [
        "https://example.com/a.png",
        "https://example.com/b.JPG?size=large",
    ]


def test_extract_image_urls_ignores_non_images() -> None:
    text = "Not images: https://example.com/file.pdf and https://example.com/noext"

    assert extract_image_urls(text) == []


def test_extract_image_urls_markdown_syntax() -> None:
    text = "Check out this screenshot: ![Screenshot](https://example.com/screenshot.png)"

    assert extract_image_urls(text) == ["https://example.com/screenshot.png"]


def test_extract_image_urls_direct_links() -> None:
    text = "Direct link: https://example.com/photo.jpg and another https://example.com/image.gif"

    assert extract_image_urls(text) == [
        "https://example.com/photo.jpg",
        "https://example.com/image.gif",
    ]


def test_extract_image_urls_various_formats() -> None:
    text = (
        "Multiple formats: "
        "https://example.com/image.png "
        "https://example.com/photo.jpeg "
        "https://example.com/pic.gif "
        "https://example.com/img.webp "
        "https://example.com/bitmap.bmp "
        "https://example.com/scan.tiff"
    )

    assert extract_image_urls(text) == [
        "https://example.com/image.png",
        "https://example.com/photo.jpeg",
        "https://example.com/pic.gif",
        "https://example.com/img.webp",
        "https://example.com/bitmap.bmp",
        "https://example.com/scan.tiff",
    ]


def test_extract_image_urls_with_query_params() -> None:
    text = "Image with params: https://cdn.example.com/image.png?width=800&height=600"

    assert extract_image_urls(text) == ["https://cdn.example.com/image.png?width=800&height=600"]


def test_extract_image_urls_case_insensitive() -> None:
    text = "Mixed case: https://example.com/Image.PNG and https://example.com/photo.JpEg"

    assert extract_image_urls(text) == [
        "https://example.com/Image.PNG",
        "https://example.com/photo.JpEg",
    ]


def test_extract_image_urls_deduplication() -> None:
    text = "Same URL twice: https://example.com/image.png and again https://example.com/image.png"

    assert extract_image_urls(text) == ["https://example.com/image.png"]


def test_extract_image_urls_mixed_markdown_and_direct() -> None:
    text = (
        "Markdown: ![alt text](https://example.com/markdown.png) "
        "and direct: https://example.com/direct.jpg "
        "and another markdown ![](https://example.com/another.gif)"
    )

    result = extract_image_urls(text)
    assert set(result) == {
        "https://example.com/markdown.png",
        "https://example.com/direct.jpg",
        "https://example.com/another.gif",
    }
    assert len(result) == 3
