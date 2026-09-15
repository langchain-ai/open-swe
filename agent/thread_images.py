"""Where offloaded conversation images live and which threads may show them.

Shared by the agent middleware that stores them and the dashboard route that
serves them, so the webapp does not have to import the agent stack.
"""

IMAGE_STORE_NAMESPACE: tuple[str, ...] = ("thread_images",)


def image_owner_threads(thread_id: str, continued_from_thread_id: object) -> frozenset[str]:
    """Threads whose stored images ``thread_id`` may show.

    A private continuation copies the transcript of the collaborative thread it
    was made from, so its messages reference images stored under that thread.
    """
    owners = {thread_id} if thread_id else set[str]()
    if isinstance(continued_from_thread_id, str) and continued_from_thread_id:
        owners.add(continued_from_thread_id)
    return frozenset(owners)
