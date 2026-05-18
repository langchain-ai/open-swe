"""Seed `["oauth_tokens"]` + `["email_to_login"]` from the legacy hardcoded map.

This is a one-shot migration. Once the dashboard's GitHub OAuth login is wired
up, every user who logs in will refresh their own record automatically — but
existing users would otherwise get the "no stored email" auth-required prompt
the first time they trigger the agent from Slack / Linear / GitHub. Run this
once at deploy time to backfill the email side of those records (tokens
remain empty until each user logs in to the dashboard).

Idempotent: running it twice leaves the same final state. Existing records
are merged, so a previously-saved encrypted token is preserved.

Usage::

    LANGGRAPH_URL=https://your-deployment uv run python -m scripts.seed_email_map
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

import httpx
from langgraph_sdk import get_client

logger = logging.getLogger(__name__)

# Snapshot of the legacy `GITHUB_USER_EMAIL_MAP` taken at migration time. The
# source-of-truth moves to the LangGraph Store after this script runs.
LEGACY_GITHUB_USER_EMAIL_MAP: dict[str, str] = {
    "aran-yogesh": "yogesh.mahendran@langchain.dev",
    "AaryanPotdar": "aaryan.potdar@langchain.dev",
    "agola11": "ankush@langchain.dev",
    "akira": "alex@langchain.dev",
    "amal-irgashev": "amal.irgashev@langchain.dev",
    "andrew-langchain-gh": "andrew.selden@langchain.dev",
    "andrewnguonly": "andrew@langchain.dev",
    "andrewrreed": "andrew@langchain.dev",
    "angus-langchain": "angus@langchain.dev",
    "ArthurLangChain": "arthur@langchain.dev",
    "asatish-langchain": "asatish@langchain.dev",
    "ashwinamardeep-ashwin": "ashwin.amardeep@langchain.dev",
    "asrira428": "siri.arun@langchain.dev",
    "ayoung19": "andy@langchain.dev",
    "baskaryan": "bagatur@langchain.dev",
    "bastiangerstner": "bastian.gerstner@langchain.dev",
    "bees": "arian@langchain.dev",
    "bentanny": "ben.tannyhill@langchain.dev",
    "bracesproul": "brace@langchain.dev",
    "brianto-langchain": "brian.to@langchain.dev",
    "bscott449": "brandon@langchain.dev",
    "bvs-langchain": "brian@langchain.dev",
    "bwhiting2356": "brendan.whiting@langchain.dev",
    "carolinedivittorio": "caroline.divittorio@langchain.dev",
    "casparb": "caspar@langchain.dev",
    "catherine-langchain": "catherine@langchain.dev",
    "ccurme": "chester@langchain.dev",
    "christian-bromann": "christian@langchain.dev",
    "christineastoria": "christine@langchain.dev",
    "colifran": "colin.francis@langchain.dev",
    "conradcorbett-crypto": "conrad.corbett@langchain.dev",
    "cstanlee": "carlos.stanley@langchain.dev",
    "cwaddingham": "chris.waddingham@langchain.dev",
    "cwlbraa": "cwlbraa@langchain.dev",
    "dahlke": "neil@langchain.dev",
    "DanielKneipp": "daniel@langchain.dev",
    "danielrlambert3": "daniel@langchain.dev",
    "DavoCoder": "davidc@langchain.dev",
    "ddzmitry": "dzmitry.dubarau@langchain.dev",
    "denis-at-langchain": "denis@langchain.dev",
    "dqbd": "david@langchain.dev",
    "elibrosen": "eli@langchain.dev",
    "emil-lc": "emil@langchain.dev",
    "emily-langchain": "emily@langchain.dev",
    "ericdong-langchain": "ericdong@langchain.dev",
    "ericjohanson-langchain": "eric.johanson@langchain.dev",
    "eyurtsev": "eugene@langchain.dev",
    "gethin-langchain": "gethin.dibben@langchain.dev",
    "gladwig2": "geoff@langchain.dev",
    "GowriH-1": "gowri@langchain.dev",
    "hanalodi": "hana@langchain.dev",
    "hari-dhanushkodi": "hari@langchain.dev",
    "hinthornw": "will@langchain.dev",
    "hntrl": "hunter@langchain.dev",
    "hwchase17": "harrison@langchain.dev",
    "iakshay": "akshay@langchain.dev",
    "sydney-runkle": "sydney@langchain.dev",
    "tanushree-sharma": "tanushree@langchain.dev",
    "victorm-lc": "victor@langchain.dev",
    "vishnu-ssuresh": "vishnu.suresh@langchain.dev",
    "vtrivedy": "vivek.trivedy@langchain.dev",
    "will-langchain": "will.anderson@langchain.dev",
    "xuro-langchain": "xuro@langchain.dev",
    "yumuzi234": "zhen@langchain.dev",
    "j-broekhuizen": "jb@langchain.dev",
    "jacobalbert3": "jacob.albert@langchain.dev",
    "jacoblee93": "jacob@langchain.dev",
    "jdrogers940 ": "josh@langchain.dev",
    "jeeyoonhyun": "jeeyoon@langchain.dev",
    "jessieibarra": "jessie.ibarra@langchain.dev",
    "jfglanc": "jan.glanc@langchain.dev",
    "jkennedyvz": "john@langchain.dev",
    "joaquin-borggio-lc": "joaquin@langchain.dev",
    "joel-at-langchain": "joel.johnson@langchain.dev",
    "johannes117": "johannes@langchain.dev",
    "joshuatagoe": "joshua.tagoe@langchain.dev",
    "katmayb": "kathryn@langchain.dev",
    "kenvora": "kvora@langchain.dev",
    "kevinbfrank": "kevin.frank@langchain.dev",
    "KiewanVillatel": "kiewan@langchain.dev",
    "l2and": "randall@langchain.dev",
    "langchain-infra": "mukil@langchain.dev",
    "langchain-karan": "karan@langchain.dev",
    "lc-arjun": "arjun@langchain.dev",
    "lc-chad": "chad@langchain.dev",
    "lcochran400": "logan.cochran@langchain.dev",
    "lnhsingh": "lauren@langchain.dev",
    "longquanzheng": "long@langchain.dev",
    "loralee90": "lora.lee@langchain.dev",
    "lunevalex": "alunev@langchain.dev",
    "maahir30": "maahir.sachdev@langchain.dev",
    "madams0013": "maddy@langchain.dev",
    "mdrxy": "mason@langchain.dev",
    "mhk197": "katz@langchain.dev",
    "mwalker5000": "mike.walker@langchain.dev",
    "mlo20030": "morgan.lo@langchain.dev",
    "natasha-langchain": "nwhitney@langchain.dev",
    "nhuang-lc": "nick@langchain.dev",
    "niilooy": "niloy@langchain.dev",
    "nitboss": "nithin@langchain.dev",
    "npentrel": "naomi@langchain.dev",
    "nrc": "nick.cameron@langchain.dev",
    "Palashio": "palash@langchain.dev",
    "PeriniM": "marco@langchain.dev",
    "pjrule": "parker@langchain.dev",
    "QuentinBrosse": "quentin@langchain.dev",
    "rahul-langchain": "rahul@langchain.dev",
    "ramonpetgrave64": "ramon@langchain.dev",
    "rx5ad": "rafid.saad@langchain.dev",
    "saad-supports-langchain": "saad@langchain.dev",
    "samecrowder": "scrowder@langchain.dev",
    "samnoyes": "sam@langchain.dev",
    "seanderoiste": "sean@langchain.dev",
    "simon-langchain": "simon@langchain.dev",
    "sriputhucode-ops": "sri.puthucode@langchain.dev",
    "stephen-chu": "stephen.chu@langchain.dev",
    "sthm": "steffen@langchain.dev",
    "steve-langchain": "steve@langchain.dev",
    "SumedhArani": "sumedh@langchain.dev",
    "suraj-langchain": "suraj@langchain.dev",
}


def _load_dotenv_if_available() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


async def seed_all() -> None:
    from agent.dashboard.profiles import upsert_email_record

    client = get_client()
    seeded = 0
    skipped = 0
    errors = 0

    for raw_login, email in LEGACY_GITHUB_USER_EMAIL_MAP.items():
        login = raw_login.strip()
        if not login or not email:
            skipped += 1
            continue
        try:
            await upsert_email_record(login, email)
            seeded += 1
        except httpx.HTTPError as exc:
            logger.error("Failed to seed %s → %s: %s", login, email, exc)
            errors += 1
            continue

    logger.info("Seeded %d records; skipped %d; errors %d", seeded, skipped, errors)
    # Touch the client so it doesn't sit unused (and to surface connection errors).
    _ = client


def _resolve_value(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    return value if isinstance(value, dict) else None


async def verify_seed() -> None:
    """Spot-check that records made it into the store."""
    client = get_client()
    sample_logins = list(LEGACY_GITHUB_USER_EMAIL_MAP.keys())[:5]
    for raw_login in sample_logins:
        login = raw_login.strip()
        try:
            item = await client.store.get_item(["oauth_tokens"], login)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning("Spot check: %s not found in oauth_tokens", login)
                continue
            raise
        record = _resolve_value(item)
        if record is None:
            logger.warning("Spot check: %s value missing/invalid", login)
            continue
        logger.info("Spot check: %s → %s", login, record.get("email"))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _load_dotenv_if_available()
    if not os.environ.get("LANGGRAPH_URL") and not os.environ.get("LANGGRAPH_URL_PROD"):
        logger.error("LANGGRAPH_URL (or LANGGRAPH_URL_PROD) must be set")
        sys.exit(1)
    asyncio.run(_run())


async def _run() -> None:
    await seed_all()
    await verify_seed()


if __name__ == "__main__":
    main()
