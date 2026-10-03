"""Kasada: solve p.js and post the sensor to the site's ``tl`` endpoint.

Environment:
    BYPASS_FAST_API_KEY  your API key
    KASADA_PAGE_URL      the protected page (default https://www.example.com/)
    KASADA_SCRIPT_URL    absolute URL of the challenge's p.js, as served to you
    USER_AGENT           the desktop Chrome user agent your client sends

httpx stands in for your own browser-like client here; in production make
these target requests with the client, cookies and proxy of your session.
Token and cookie values are never printed.
"""

from __future__ import annotations

import base64
import os
import sys

import httpx

import bypassfast


def main() -> int:
    api_key = os.environ["BYPASS_FAST_API_KEY"]
    page_url = os.environ.get("KASADA_PAGE_URL", "https://www.example.com/")
    script_url = os.environ["KASADA_SCRIPT_URL"]
    user_agent = os.environ["USER_AGENT"]

    with (
        bypassfast.Client(api_key) as client,
        httpx.Client(headers={"User-Agent": user_agent}) as site,
    ):
        # 1. Fetch p.js with your own client and session.
        p_js = site.get(script_url).text

        # 2. Solve. The payload is computed from the script; nothing is sent
        #    to the target by Bypass Fast.
        solved = client.kasada.sensor(
            script=p_js, ua=user_agent, url=page_url, script_url=script_url
        )
        print(f"solved: request {solved.response.request_id}, {solved.duration_ms} ms")

        # 3. POST the decoded payload to the tl endpoint next to p.js, with
        #    every returned header and the returned user agent.
        tl_url = script_url.rsplit("/", 1)[0] + "/tl"
        tl = site.post(
            tl_url,
            content=base64.b64decode(solved.payload),
            headers={
                **solved.headers,
                "Content-Type": "application/octet-stream",
                "User-Agent": solved.user_agent,
            },
        )
        token = tl.headers.get("x-kpsdk-ct", "")
        print(f"tl answered {tl.status_code}; x-kpsdk-ct present: {bool(token)}")
        if not token:
            return 1

        # 4. Send the protected request with x-kpsdk-ct (plus x-kpsdk-cd from
        #    client.kasada.cd(...) when the site issues a CD challenge) and
        #    solved.user_agent verbatim.
        return 0


if __name__ == "__main__":
    sys.exit(main())
