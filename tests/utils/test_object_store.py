from datetime import UTC, datetime

from openswe.utils.object_store import ListingPage, ObjectStore

_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_requests_are_signed_like_aws_documents_sigv4() -> None:
    """The GET and list examples from AWS's "Signature Calculations for S3" guide."""
    store = ObjectStore(
        url="https://s3.amazonaws.com",
        bucket="examplebucket",
        region="us-east-1",
        access_key_id="AKIAIOSFODNN7EXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    )
    now = datetime(2013, 5, 24, tzinfo=UTC)
    headers = {"x-amz-content-sha256": _EMPTY_SHA256, "x-amz-date": "20130524T000000Z"}
    host = "examplebucket.s3.amazonaws.com"

    get = store.authorization("GET", host, "/test.txt", {}, {"range": "bytes=0-9", **headers}, now)
    listing = store.authorization("GET", host, "/", {"max-keys": "2", "prefix": "J"}, headers, now)

    assert get.endswith(
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )
    assert listing.endswith(
        "Signature=34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"
    )


def test_listings_are_read_in_gcs_and_s3_namespaces() -> None:
    """GCS answers in its own namespace; a listing read as empty would sweep nothing."""
    page = """<ListBucketResult xmlns='{namespace}'>
      <IsTruncated>{truncated}</IsTruncated>
      <NextContinuationToken>next</NextContinuationToken>
      <Contents><Key>plugin-attachments/a</Key><LastModified>2026-02-17T22:11:12.487Z</LastModified></Contents>
    </ListBucketResult>"""
    gcs = ListingPage.parse(
        page.format(namespace="http://doc.s3.amazonaws.com/2006-03-01", truncated="true").encode()
    )
    s3 = ListingPage.parse(
        page.format(namespace="http://s3.amazonaws.com/doc/2006-03-01/", truncated="false").encode()
    )

    assert [o.key for o in gcs.objects] == [o.key for o in s3.objects] == ["plugin-attachments/a"]
    assert gcs.next_token == "next"
    assert s3.next_token is None
