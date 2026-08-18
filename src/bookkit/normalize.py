"""Input normalisation: type fast, store clean.

Every free-text field that enters through a form passes through here. The
bias is auto-fixing over rejecting — reject only when the value is clearly
not the thing (an email without an @). Dates and money have their own richer
parsers (dates.py / money.py); this module covers the rest: whitespace,
emails, phones, URLs, domains, LinkedIn handles, NAICS codes.

Deliberately dependency-free: a full phonenumbers dep is heavyweight for a
single-user book that is overwhelmingly US risks; international numbers pass
through digit-stripped with their + intact rather than being guessed at.
"""

from __future__ import annotations

import re

_WS_RE = re.compile(r"\s+")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s.]+$")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def clean_text(text: str) -> str:
    """Collapse runs of whitespace and strip — for single-line text fields."""
    return _WS_RE.sub(" ", text).strip()


def clean_email(text: str) -> str:
    """' Rosa.Silva@EXAMPLE.COM ' → 'Rosa.Silva@example.com'. The DOMAIN
    lowercases and the local part does not — RFC 5321 makes local parts
    case-sensitive, and flattening them can address mail to the wrong person.
    Accepts pasted mailto: links and 'Name <addr>' forms; raises when it isn't
    an email."""
    cleaned = clean_text(text)
    if cleaned.lower().startswith("mailto:"):
        cleaned = cleaned[7:]
    match = re.search(r"<([^<>\s]+@[^<>\s]+)>", cleaned)
    if match:
        cleaned = match.group(1)
    cleaned = cleaned.strip(" ,;")
    if not _EMAIL_RE.match(cleaned):
        raise ValueError(f"{text.strip()!r} is not an email address")
    local, _, domain = cleaned.partition("@")
    return f"{local}@{domain.lower()}"


def clean_phone(text: str) -> str:
    """'312.555.0142' → '(312) 555-0142'; '1-312-555-0142' → '+1 (312) 555-0142';
    international keeps its + and loses the noise. Extensions survive."""
    cleaned = clean_text(text)
    ext = ""
    ext_match = re.search(r"(?:ext\.?|x)\s*(\d+)\s*$", cleaned, re.IGNORECASE)
    if ext_match:
        ext = f" x{ext_match.group(1)}"
        cleaned = cleaned[: ext_match.start()]
    plus = cleaned.lstrip().startswith("+")
    digits = re.sub(r"\D", "", cleaned)
    if not digits:
        raise ValueError(f"{text.strip()!r} has no digits")
    if not plus and len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}{ext}"
    if not plus and len(digits) == 11 and digits[0] == "1":
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}{ext}"
    if plus and len(digits) == 11 and digits[0] == "1":
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}{ext}"
    if plus:
        return f"+{digits}{ext}"
    return f"{digits}{ext}"


def clean_url(text: str) -> str:
    """'Example.COM/About' → 'https://example.com/About' — scheme added when
    missing, scheme and host lowercased, path left alone."""
    cleaned = clean_text(text)
    if not _SCHEME_RE.match(cleaned):
        cleaned = f"https://{cleaned}"
    scheme, _, rest = cleaned.partition("://")
    host, slash, path = rest.partition("/")
    out = f"{scheme.lower()}://{host.lower()}"
    if slash and path:
        out += f"/{path}"
    return out.rstrip("/") if not path else out


def clean_domain(text: str) -> str:
    """'https://www.Example.com/about' → 'example.com'."""
    cleaned = clean_text(text)
    cleaned = _SCHEME_RE.sub("", cleaned)
    cleaned = cleaned.split("/")[0].split("?")[0].lower().strip(".")
    if cleaned.startswith("www."):
        cleaned = cleaned[4:]
    if "." not in cleaned or " " in cleaned:
        raise ValueError(f"{text.strip()!r} is not a domain")
    return cleaned


def clean_linkedin(text: str) -> str:
    """Accepts a profile URL, 'in/handle', or a bare handle → canonical URL."""
    cleaned = clean_text(text).rstrip("/")
    if "linkedin.com" in cleaned.lower():
        cleaned = _SCHEME_RE.sub("", cleaned)
        host, _, path = cleaned.partition("/")
        return f"https://www.linkedin.com/{path}" if path else "https://www.linkedin.com"
    cleaned = cleaned.lstrip("@")
    if cleaned.lower().startswith("in/"):
        cleaned = cleaned[3:]
    if not cleaned or "/" in cleaned or " " in cleaned:
        raise ValueError(f"{text.strip()!r} is not a LinkedIn profile")
    return f"https://www.linkedin.com/in/{cleaned}"


def clean_naics(text: str) -> str:
    digits = re.sub(r"\D", "", text)
    if not 2 <= len(digits) <= 6:
        raise ValueError(f"{text.strip()!r} is not a NAICS code (2-6 digits)")
    return digits
