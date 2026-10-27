from openswe.expedited_review.eligibility import ChangedFile
from openswe.github.pull_request_status import PullRequestClient

MIN_CHANGED_LINES = 100


async def needs_walkthrough(pull: PullRequestClient) -> bool:
    files = await ChangedFile.of_pull(pull, max_pages=30)
    return (
        files is None
        or len(files) >= 3000
        or sum(file.changed_lines for file in files if not file.is_test) >= MIN_CHANGED_LINES
    )
