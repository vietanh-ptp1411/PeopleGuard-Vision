"""Hide credentials before anything is written to a log, a UI field or an event view.

One implementation, imported by everything that handles a stream URL. There used to be
two copies and both split the URL at the first "@", which is wrong whenever the password
contains one - and Hikvision insists on a special character in the password, so people
reach for "@" all the time:

    rtsp://admin:Hik@2026!@192.168.1.64/Streaming/Channels/101
      first "@"  ->  rtsp://admin:***@2026!@192.168.1.64/...   <- publishes "2026!"
      last  "@"  ->  rtsp://admin:***@192.168.1.64/...         <- correct

RFC 3986 agrees: inside an authority, the userinfo runs up to the *last* "@".
"""
from __future__ import annotations

MASK = "***"


def mask_url(url: str) -> str:
    """The same URL with the password replaced by ***, everything else left readable."""
    text = str(url or "")
    if "//" not in text:
        return text
    scheme, rest = text.split("//", 1)
    # The authority ends at the first /, ? or # - an "@" after that belongs to the path
    # (Hikvision query strings can carry one) and must not be mistaken for the separator.
    cut = len(rest)
    for sep in "/?#":
        found = rest.find(sep)
        if found != -1:
            cut = min(cut, found)
    authority, tail = rest[:cut], rest[cut:]
    if "@" not in authority:
        return text
    creds, host = authority.rsplit("@", 1)
    if ":" not in creds:
        return text                      # a user and no password: nothing to hide
    user = creds.split(":", 1)[0]
    return f"{scheme}//{user}:{MASK}@{host}{tail}"
