"""Cisco PSIRT openVuln API v2 vulnerability scanner."""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass

import requests
from html.parser import HTMLParser

logger = logging.getLogger(__name__)


class _HTMLStripper(HTMLParser):
    """Extract plain text from an HTML string, preserving link URLs inline."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href", "").strip()
        if tag in ("p", "li", "br"):
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            # append URL in brackets after link text
            self._parts.append(f" ({self._href})")
            self._href = None

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        import re as _re
        text = "".join(self._parts)
        # collapse multiple whitespace / newlines
        text = _re.sub(r"[ \t]+", " ", text)
        text = _re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _html_to_text(html: str) -> str:
    if not html or "<" not in html:
        return html
    stripper = _HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()

_PSIRT_TOKEN_URL  = "https://id.cisco.com/oauth2/default/v1/token"
_PSIRT_OSTYPE_URL = "https://apix.cisco.com/security/advisories/v2/OSType/{os_type}"

_TOKEN_LOCK = threading.Lock()
_TOKEN_CACHE: dict = {"token": None, "expires_at": 0.0}

SIR_RANK: dict[str, int] = {
    "Critical": 4,
    "High": 3,
    "Medium": 2,
    "Low": 1,
    "Informational": 0,
}


# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class Advisory:
    advisory_id: str
    title: str
    sir: str           # Critical / High / Medium / Low / Informational
    cvss_score: float
    cves: list[str]
    publication_url: str
    summary: str
    workaround: str
    first_fixed: list[str]
    first_published: str = ""   # ISO datetime string
    last_updated: str = ""      # ISO datetime string
    source: str = "PSIRT"


# ── Auth ─────────────────────────────────────────────────────────────────────

def _get_psirt_token() -> str:
    with _TOKEN_LOCK:
        if _TOKEN_CACHE["token"] and time.time() < _TOKEN_CACHE["expires_at"] - 60:
            return _TOKEN_CACHE["token"]

        client_id     = os.getenv("CISCO_PSIRT_CLIENT_ID", "").strip()
        client_secret = os.getenv("CISCO_PSIRT_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise RuntimeError("CISCO_PSIRT_CLIENT_ID / CISCO_PSIRT_CLIENT_SECRET not configured")

        resp = requests.post(
            _PSIRT_TOKEN_URL,
            data={
                "grant_type":    "client_credentials",
                "client_id":     client_id,
                "client_secret": client_secret,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        _TOKEN_CACHE["token"]      = data["access_token"]
        _TOKEN_CACHE["expires_at"] = time.time() + int(data.get("expires_in", 3600))
        return _TOKEN_CACHE["token"]


# ── Version / platform helpers ───────────────────────────────────────────────

def _normalize_ios_version(version: str) -> str:
    """Strip dot-sub-minor from inside parentheses so PSIRT can match it.

    15.2(4.0.55)E  → 15.2(4)E
    15.6(2)T       → 15.6(2)T   (unchanged)
    """
    return re.sub(r"\((\d+)\.\d[\d.]*\)", r"(\1)", version)


def _os_type_from_platform(os_platform: str, ios_version: str) -> str:
    """Map inventory os_platform + version to a Cisco PSIRT OSType string."""
    p = (os_platform or "").lower()
    v = (ios_version or "").lower()
    if "nxos" in p or "nx-os" in p:
        return "nxos"
    if "asa" in p:
        return "asa"
    if "iosxe" in p or "ios-xe" in p or "ios xe" in p:
        return "iosxe"
    # IOS XE uses 16.x / 17.x versioning
    if re.match(r"1[6-9]\.", v) or re.match(r"2\d\.", v):
        return "iosxe"
    return "ios"


# ── PSIRT query ──────────────────────────────────────────────────────────────

def _fetch_psirt(ios_version: str, os_platform: str = "cisco_ios") -> list[Advisory]:
    """Query Cisco PSIRT openVuln API v2 (apix.cisco.com).

    Raises RuntimeError on auth/permission errors.
    Returns empty list when no advisories are found (404).
    """
    token      = _get_psirt_token()
    os_type    = _os_type_from_platform(os_platform, ios_version)
    normalized = _normalize_ios_version(ios_version)
    url        = _PSIRT_OSTYPE_URL.format(os_type=os_type)

    logger.info("PSIRT query: %s?version=%s  (raw=%s)", url, normalized, ios_version)
    resp = requests.get(
        url,
        params={"version": normalized},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )

    if resp.status_code == 404:
        return []
    if resp.status_code in (401, 403):
        raise RuntimeError(f"PSIRT auth error {resp.status_code}: {resp.text[:120].strip()}")
    resp.raise_for_status()

    raw = (resp.json() or {}).get("advisories") or []
    advisories: list[Advisory] = []
    for item in raw:
        try:
            advisories.append(Advisory(
                advisory_id=     item.get("advisoryId", ""),
                title=           item.get("advisoryTitle", ""),
                sir=             item.get("sir", "Informational"),
                cvss_score=      float(item.get("cvssBaseScore") or 0),
                cves=            item.get("cves") or [],
                publication_url= item.get("publicationUrl", ""),
                summary=         _html_to_text(item.get("summary") or "")[:2000],
                workaround=      _html_to_text(item.get("workarounds") or "")[:1000],
                first_fixed=     item.get("firstFixed") or [],
                first_published= item.get("firstPublished", ""),
                last_updated=    item.get("lastUpdated", ""),
                source=          "PSIRT",
            ))
        except Exception as exc:
            logger.warning("PSIRT parse error for %s: %s", item.get("advisoryId"), exc)

    # Sort: newest first_published first, then by severity, then cvss
    advisories.sort(
        key=lambda a: (a.first_published or "", SIR_RANK.get(a.sir, 0), a.cvss_score),
        reverse=True,
    )
    return advisories


# ── Public entry point ───────────────────────────────────────────────────────

def fetch_advisories_for_version(
    ios_version: str,
    os_platform: str = "cisco_ios",
) -> tuple[list[Advisory], str]:
    """Fetch advisories for a device via Cisco PSIRT openVuln API v2.

    Returns (advisories, 'PSIRT').
    Raises on auth failure or network error.
    """
    if not ios_version or ios_version.lower() in ("unknown", "n/a", ""):
        return [], "none"

    advisories = _fetch_psirt(ios_version, os_platform)
    logger.info("PSIRT: %d advisories for IOS %s", len(advisories), ios_version)
    return advisories, "PSIRT"


# ── LLM summary ──────────────────────────────────────────────────────────────

def generate_vuln_summary(device: dict, advisories: list[Advisory], source: str = "PSIRT") -> str:
    """Use LLM to generate a human-readable security assessment for the device."""
    if not advisories:
        return f"No known vulnerabilities found for IOS {device.get('version', 'unknown')} (Cisco PSIRT)."

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from src.llm_factory import create_chat_model

        llm = create_chat_model()
        crit_high = [a for a in advisories if a.sir in ("Critical", "High")]

        adv_lines: list[str] = []
        for a in advisories[:15]:
            cve_str = ", ".join(a.cves[:3]) if a.cves else a.advisory_id
            wk = (a.workaround or "Upgrade to a fixed release")[:200]
            adv_lines.append(
                f"- [{a.sir}] CVSS:{a.cvss_score} | {a.title[:80]}\n"
                f"  CVE: {cve_str} | Workaround: {wk}"
            )

        prompt = (
            "/no_think\n"
            f"Device: {device.get('hostname')} | Platform: {device.get('os_platform')} | "
            f"IOS: {device.get('version')} | Role: {device.get('device_role')} | "
            f"Site: {device.get('site')}\n\n"
            f"Cisco PSIRT scan found {len(advisories)} advisories "
            f"({len(crit_high)} Critical/High):\n\n"
            + "\n".join(adv_lines)
            + "\n\nWrite a concise security assessment (3-5 sentences) covering:\n"
            "1. Overall risk level\n"
            "2. Most urgent vulnerabilities to address\n"
            "3. Recommended immediate actions (patching path or workaround)\n"
            "Be practical and actionable for a network engineer. Plain text only."
        )

        response = llm.invoke([
            SystemMessage(content="You are a Cisco network security analyst. Be concise and actionable."),
            HumanMessage(content=prompt),
        ])
        return str(response.content).strip()
    except Exception as exc:
        logger.warning("LLM vuln summary failed: %s", exc)
        crit_high = [a for a in advisories if a.sir in ("Critical", "High")]
        return (
            f"Found {len(advisories)} Cisco PSIRT advisories "
            f"({len(crit_high)} Critical/High). Immediate review recommended."
        )
