"""Akamai Bot Manager: generate sensor_data and post it for a fresh _abck.

Environment:
    BYPASS_FAST_API_KEY  your API key
    AKAMAI_PAGE_URL      the protected page (default https://www.example.com/)
    AKAMAI_SCRIPT_URL    absolute URL of the Bot Manager sensor script
    USER_AGENT           the desktop Chrome user agent your client sends
    AKAMAI_POSTS         sensor posts to make (default 3)

httpx stands in for your own browser-like client here; in production make
these target requests with the client, cookies and proxy of your session.
Cookie and sensor values are never printed.
"""

from __future__ import annotations

import json
import os
import sys

import httpx

import bypassfast


def main() -> int:
    api_key = os.environ["BYPASS_FAST_API_KEY"]
    page_url = os.environ.get("AKAMAI_PAGE_URL", "https://www.example.com/")
    script_url = os.environ["AKAMAI_SCRIPT_URL"]
    user_agent = os.environ["USER_AGENT"]
    posts = int(os.environ.get("AKAMAI_POSTS", "3"))

    with (
        bypassfast.Client(api_key) as client,
        httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "en-US,en;q=0.9"},
            follow_redirects=True,
        ) as site,
    ):
        site.get(page_url)  # sets bm_sz and the first _abck
        script = site.get(script_url).content  # raw bytes; the SDK base64-encodes

        session = ""
        for attempt in range(1, posts + 1):
            # The first call uploads the script; later fresh sessions on this
            # client send only its SHA-256 (script_id), and a remote cache miss
            # resends it automatically. With a session the script is not sent.
            solved = client.akamai.sensor(
                url=page_url,
                ua=user_agent,
                abck=site.cookies.get("_abck") or "0~-1~-1~-1~-1",
                bm_sz=site.cookies.get("bm_sz") or "",
                script=script,
                script_url=script_url,
                session=session,
            )
            session = solved.session
            posted = site.post(
                script_url,
                content=json.dumps({"sensor_data": solved.sensor_data}),
                headers={"Content-Type": "text/plain;charset=UTF-8", "User-Agent": solved.ua},
            )
            print(
                f"post {attempt}: status {posted.status_code}, "
                f"attempts {solved.response.attempts}, request {solved.response.request_id}"
            )

        # Attach the latest _abck and the same user agent (solved.ua) to your
        # protected request.
        return 0


if __name__ == "__main__":
    sys.exit(main())
