from runtime_api.web_pages import PageText


async def test_deeply_nested_page_falls_back_to_its_text_without_scripts() -> None:
    depth = 6000
    html = (
        "<div>" * depth
        + "<p>Deep &amp; nested micro<span>service</span> <em>outage</em></p>"
        + "<script>alert('x')</script><style>.a{}</style><p>Second</p>"
        + "</div>" * depth
    )

    assert await PageText.markdown(html) == "Deep & nested microservice outage\n\nSecond"
