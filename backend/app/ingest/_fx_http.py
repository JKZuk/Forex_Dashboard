"""Small polite HTTP helper shared by the FX price and COT ingesters."""
from __future__ import annotations

import logging
import time
from typing import Any, Mapping

import requests

log = logging.getLogger("ingest.http")

USER_AGENT = "fx-dashboard/1.0 (python-requests)"
TIMEOUT = (10, 60)  # connect, read seconds
RETRY_STATUS = {429, 500, 502, 503, 504}


def get(
    url: str,
    params: Mapping[str, Any] | None = None,
    *,
    retries: int = 3,
    backoff: float = 1.5,
    accept: str = "application/json",
) -> requests.Response:
    """GET with timeout and a small exponential backoff on transient failures."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        if attempt:
            time.sleep(backoff * (2 ** (attempt - 1)))
        try:
            resp = requests.get(
                url,
                params=params,
                timeout=TIMEOUT,
                headers={"User-Agent": USER_AGENT, "Accept": accept},
            )
        except requests.RequestException as exc:
            last_exc = exc
            log.warning("GET %s failed (attempt %d): %s", url, attempt + 1, exc)
            continue
        if resp.status_code in RETRY_STATUS:
            last_exc = requests.HTTPError(f"HTTP {resp.status_code} from {url}")
            log.warning("GET %s -> %d (attempt %d)", url, resp.status_code, attempt + 1)
            continue
        resp.raise_for_status()
        return resp
    assert last_exc is not None
    raise last_exc
