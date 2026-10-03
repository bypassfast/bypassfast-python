"""PerimeterX / HUMAN: init, send your request, solve a press-and-hold block.

Environment:
    BYPASS_FAST_API_KEY  your API key
    PX_PAGE_URL          the page a user would open (default https://www.example.com/)
    PX_PROTECTED_URL     the request you need (default: PX_PAGE_URL)
    PX_PROXY             http(s):// or socks5(h):// proxy URL with credentials
    PX_APP_ID            the site's HUMAN app id, when it is not inferred

Every request in the session must use the same proxy exit, Accept-Language
and the user agent init returns. httpx stands in for your own Chrome-like
client here (socks5 needs ``httpx[socks]``). Cookie values are never printed.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable

import httpx

import bypassfast

ACCEPT_LANGUAGE = "en-US,en;q=0.9"


def install(site: httpx.Client, cookies: Iterable[bypassfast.PerimeterxCookie]) -> None:
    for cookie in cookies:
        site.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path or "/")


def is_human_block(response: httpx.Response) -> bool:
    if response.status_code == 428 and b"blockScript" in response.content:
        return True
    return b"px-captcha" in response.content


def blocked_from(response: httpx.Response) -> bypassfast.PerimeterxBlockedResponse:
    headers: dict[str, str] = {}
    for name, value in response.headers.multi_items():
        key = name.lower()
        headers[key] = f"{headers[key]}, {value}" if key in headers else value
    return {
        "url": str(response.url),
        "method": response.request.method,
        "status": response.status_code,
        "headers": headers,
        "body": response.content,  # bytes are sent base64-encoded
    }


def main() -> int:
    api_key = os.environ["BYPASS_FAST_API_KEY"]
    page_url = os.environ.get("PX_PAGE_URL", "https://www.example.com/")
    protected_url = os.environ.get("PX_PROTECTED_URL", page_url)
    proxy = os.environ["PX_PROXY"]
    app_id = os.environ.get("PX_APP_ID", "")

    with bypassfast.Client(api_key) as client:
        init = client.perimeterx.init(
            url=page_url, proxy=proxy, accept_language=ACCEPT_LANGUAGE, app_id=app_id
        )
        print(f"init: success {init.success}, {len(init.cookies)} cookies")

        transport = httpx.HTTPTransport(proxy=proxy)
        headers = {"User-Agent": init.ua, "Accept-Language": ACCEPT_LANGUAGE}
        with httpx.Client(transport=transport, headers=headers) as site:
            install(site, init.cookies)
            response = site.get(protected_url)
            if not is_human_block(response):
                print(f"not blocked: status {response.status_code}")
                return 0

            hold = client.perimeterx.solve_hold(
                session=init.session, proxy=proxy, blocked=blocked_from(response)
            )
            if not hold.success:
                # A rejected hold is billed. Stop instead of looping; move to
                # another exit only when the API says so.
                print(f"hold rejected; change exit: {hold.change_exit}")
                return 1
            install(site, hold.cookies)
            retried = site.get(protected_url)  # retry once
            print(f"after hold: status {retried.status_code}")
            return 0 if not is_human_block(retried) else 1


if __name__ == "__main__":
    sys.exit(main())
