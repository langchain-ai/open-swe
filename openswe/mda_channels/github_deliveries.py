"""Every GitHub delivery, which MDA's GitHub channel forwards here as GitHub sent it.

The channel verifies GitHub's signature, then forwards each delivery (``forward_to``
in ``mda/channels/github.py``): the body and GitHub's headers unchanged, signed with
``MDA_SERVICE_SECRET``. MDA answers GitHub only once this has, within a few seconds,
so this only logs the delivery: a delivery that fails here fails on GitHub, where it
can be redelivered from the App's settings (GitHub doesn't send it again on its own).
The backend picks and runs its own work after answering
(``openswe.github.channel.take_forwarded_delivery``): reviews, walkthroughs, human
review, the merge queue, CI and the pull request state the dashboard shows. A
delivery is logged and its work run once, however often it arrives. Agent runs are
the channel's.
"""

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import ValidationError
from runtime_api.github import GitHubDelivery

from openswe.github.channel import take_forwarded_delivery
from openswe.mda_channels.api import ChannelCall
from openswe.mda_channels.auth import require_mda_channel

router = APIRouter(prefix="/mda", dependencies=[Depends(require_mda_channel)])


@router.post("/github/deliveries", status_code=202)
async def github_delivery(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    """Take a delivery MDA forwarded; the backend's work runs after the answer."""
    try:
        delivery = GitHubDelivery(
            event=request.headers.get("x-github-event", ""),
            delivery_id=request.headers.get("x-github-delivery", ""),
            payload=json.loads(await request.body()),
        )
    except (ValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Not a GitHub delivery") from exc
    await take_forwarded_delivery(ChannelCall(request, background_tasks), delivery)
    return {"status": "accepted"}
