"""robots.txt cache and check.

Policy, from the project charter: parse robots.txt with urllib.robotparser on
every host. If a path is disallowed, do not fetch it and record the skip. If
robots.txt itself is unreachable (4xx, 5xx, or network error), treat the host
as allowed but record the reason string "robots_unreachable". Honor
Crawl-delay when it is larger than our configured delay.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib import robotparser

import requests

from ceiling.http.archive import RawArchive
from ceiling.logging import get_logger
from ceiling.util import host_of, utc_now_iso

logger = get_logger("http.robots")

ROBOTS_DISALLOW = "robots_disallow"
ROBOTS_UNREACHABLE = "robots_unreachable"


@dataclass
class RobotsInfo:
    host: str
    url: str
    status: int | None
    reachable: bool
    parser: robotparser.RobotFileParser | None
    crawl_delay: float | None
    body_sha256: str | None
    body_path: str | None
    fetched_at: str


class RobotsCache:
    """Per-host robots.txt cache for the process lifetime.

    before_fetch, when provided, is called with the host right before the
    robots.txt request so the owning client can enforce its rate limits on
    the robots fetch itself.
    """

    def __init__(
        self,
        session: requests.Session,
        user_agent: str,
        archive: RawArchive,
        scan_date: str,
        connect_timeout_s: float = 20.0,
        read_timeout_s: float = 30.0,
        before_fetch: Callable[[str], None] | None = None,
    ) -> None:
        self._session = session
        self._user_agent = user_agent
        self._archive = archive
        self._scan_date = scan_date
        self._timeout = (connect_timeout_s, read_timeout_s)
        self._before_fetch = before_fetch
        self._cache: dict[str, RobotsInfo] = {}

    def info(self, host: str) -> RobotsInfo:
        """Fetch and cache robots.txt for a host, persisting the body."""
        cached = self._cache.get(host)
        if cached is not None:
            return cached
        url = f"https://{host}/robots.txt"
        if self._before_fetch is not None:
            self._before_fetch(host)
        fetched_at = utc_now_iso()
        status: int | None = None
        parser: robotparser.RobotFileParser | None = None
        crawl_delay: float | None = None
        sha: str | None = None
        path: str | None = None
        reachable = False
        try:
            resp = self._session.get(
                url,
                timeout=self._timeout,
                headers={"User-Agent": self._user_agent},
            )
            status = resp.status_code
        except requests.RequestException as exc:
            logger.warning("robots.txt unreachable for %s: %s", host, exc)
        else:
            if 200 <= status < 300:
                reachable = True
                body = resp.content
                sha, body_path = self._archive.store(
                    host,
                    self._scan_date,
                    body,
                    {
                        "url": url,
                        "fetched_at": fetched_at,
                        "http_status": status,
                        "headers": dict(resp.headers),
                    },
                )
                path = str(body_path)
                parser = robotparser.RobotFileParser()
                parser.parse(body.decode("utf-8", errors="replace").splitlines())
                raw_delay = parser.crawl_delay(self._user_agent)
                if raw_delay is not None:
                    crawl_delay = float(raw_delay)
            else:
                logger.warning("robots.txt for %s returned %s", host, status)
        info = RobotsInfo(
            host=host,
            url=url,
            status=status,
            reachable=reachable,
            parser=parser,
            crawl_delay=crawl_delay,
            body_sha256=sha,
            body_path=path,
            fetched_at=fetched_at,
        )
        self._cache[host] = info
        return info

    def allowed(self, url: str, user_agent: str | None = None) -> tuple[bool, str | None]:
        """(True, None) if allowed, (False, "robots_disallow") if not,
        (True, "robots_unreachable") if robots.txt could not be read."""
        host = host_of(url)
        info = self.info(host)
        if not info.reachable or info.parser is None:
            return True, ROBOTS_UNREACHABLE
        ua = user_agent or self._user_agent
        if info.parser.can_fetch(ua, url):
            return True, None
        return False, ROBOTS_DISALLOW

    def crawl_delay(self, host: str) -> float | None:
        """Crawl-delay for a host if its robots.txt has been read, else None."""
        info = self._cache.get(host)
        return info.crawl_delay if info is not None else None
