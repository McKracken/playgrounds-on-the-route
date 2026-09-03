"""OSM Overpass fast-path playground detection (spec Feature 2: FR-2.1, FR-2.2,
FR-2.3, AR-2.1).

Depends only on an HTTP client (``httpx``) -- no Playwright, no Anthropic
dependency -- so this module stays independently unit-testable and reusable
(AR-2.1).
"""

from __future__ import annotations

import httpx

#: Sent with every Overpass request so the public instance can identify the
#: caller (Integration Points: "send a descriptive User-Agent header").
_USER_AGENT = "playground-check/0.1 (POI playground classifier CLI)"

#: Overpass QL query template (spec FR-2.1). Uses the combined `nwr` selector
#: so relation-tagged playgrounds (e.g. multipolygons) aren't missed, and
#: `out count;` since only existence -- not geometry -- is needed.
_QUERY_TEMPLATE = (
    "[out:json][timeout:{timeout}];\n"
    'nwr(around:{radius},{lat},{lng})["leisure"="playground"];\n'
    "out count;"
)


def check_nearby(
    lat: float,
    lng: float,
    *,
    radius: float,
    timeout: float,
    endpoint: str,
) -> bool:
    """Check Overpass for a tagged `leisure=playground` within `radius` meters
    of (`lat`, `lng`).

    Returns ``True`` only when a nonzero playground count is confirmed by a
    well-formed Overpass response. Every other outcome -- a confirmed zero
    count, an HTTP timeout, a connection error, a non-2xx HTTP response, or a
    response whose JSON/shape can't be parsed -- is swallowed and reported as
    ``False`` (spec FR-2.3): a miss and an error are both "inconclusive" to the
    caller, which always falls through to the Google Maps photo path
    afterward regardless of which one occurred, so this layer never raises.
    """
    query = _QUERY_TEMPLATE.format(timeout=timeout, radius=radius, lat=lat, lng=lng)

    try:
        response = httpx.post(
            endpoint,
            data={"data": query},
            headers={"User-Agent": _USER_AGENT},
            # The server's own `[timeout:]` gets `timeout` seconds to fire and
            # return a clean error; the client waits 5s longer than that
            # before giving up on the connection itself (spec FR-2.3).
            timeout=timeout + 5,
        )
        response.raise_for_status()
        payload = response.json()
        total = payload["elements"][0]["tags"]["total"]
        return int(total) > 0
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        return False
