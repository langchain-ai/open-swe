"""Tool: ``dismiss_review_finding``. Dismiss a reviewer finding for the person the run acts for."""

from typing import Any

from openswe.audit_logs.tools import audit_tool
from openswe.review.dismissals import FindingDismisser, FindingNotFound, FindingSettled
from openswe.tools.mcp_exposure import expose_mcp
from openswe.tools.review_finding_target import ReviewFindingTarget
from openswe.users.records import user_id_for_login


@expose_mcp()
@audit_tool()
async def dismiss_review_finding(
    finding_id: str, reason: str = "", pr_url: str = ""
) -> dict[str, Any]:
    """Implement the `dismiss_review_finding` tool."""
    try:
        target = await ReviewFindingTarget.resolve(pr_url)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    dismisser = FindingDismisser(
        owner=target.owner,
        repo=target.repo,
        number=target.number,
        finding_id=finding_id,
        login=target.login,
        user_id=user_id_for_login(target.login),
        reason=reason.strip()[:3000] or None,
    )
    try:
        finding = await dismisser.dismiss()
    except FindingNotFound:
        return {"success": False, "error": f"No finding {finding_id} on this pull request"}
    except FindingSettled as exc:
        return {"success": False, "error": f"The finding is already {exc}"}
    return {
        "success": True,
        "finding_id": finding["id"],
        "status": finding["status"],
        "resolution_note": finding.get("resolution_note"),
    }
