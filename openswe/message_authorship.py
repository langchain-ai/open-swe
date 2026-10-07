"""Separate delegated message authorship from the authenticated principal."""

from openswe.input_messages import InputMessageContext, PersonIdentity, SystemIdentity
from openswe.prompts import prompt
from openswe.users import User


async def concierge_author(login: str, email: str | None) -> SystemIdentity:
    person: PersonIdentity = {"id": f"github:{login}", "github_login": login}
    if email:
        person["email"] = email
    user = await User.for_person(person)
    person = await User.canonical_person(person)
    name = (user.display_name if user else None) or login
    return {
        "id": f"system:concierge-{person['id']}",
        "display_name": f"Concierge on behalf of {name}",
        "platform": "open-swe",
        "sender_type": "bot",
        "content": prompt("runs/concierge-authorship", principal=person["id"]),
    }


def message_context(sender_id: str, author: SystemIdentity | None) -> InputMessageContext:
    if author is None:
        return {"sender_id": sender_id, "surface": "web", "kind": "human"}
    return {
        "sender_id": author["id"],
        "surface": "automation",
        "kind": "system",
        "data": {"on_behalf_of": sender_id, "author_name": author["display_name"]},
    }
