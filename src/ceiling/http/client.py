"""Rate limited, robots respecting HTTP client with raw archive and provenance.

Enforcement, per the project charter:
- robots.txt is checked before every request; disallowed URLs are never
  fetched and the skip is recorded.
- Per-host token spacing at the configured floor (>= 1 second per host) and a
  global inter-host delay (>= 2 seconds), both enforced with time.monotonic.
- tenacity retry on 429, 503, and connection errors with exponential backoff
  and full jitter from a 5 second base, at most 4 attempts.
- After 3 consecutive 429s a host is stopped for the rest of the run.
- Successful bodies are archived under data/raw/{host}/{date}/{sha256}.html
  with a JSON sidecar, and every fetch writes a fetches row.
- Same-day refetches of a URL with a 200 body on disk are served from cache.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import requests
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from ceiling.db import repo
from ceiling.http.archive import RawArchive
from ceiling.http.robots import ROBOTS_DISALLOW, RobotsCache
from ceiling.logging import get_logger
from ceiling.settings import Settings
from ceiling.util import host_of, utc_now_iso

logger = get_logger("http.client")

RATE_LIMIT_STOP = "rate_limit_stop"


class HostStoppedError(Exception):
    def __init__(self, host: str) -> None:
        self.host = host
        super().__init__(f"host stopped after consecutive 429s: {host}")


class RetryableStatusError(Exception):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"retryable status: {status}")


@dataclass
class FetchResult:
    url: str
    final_url: str | None = None
    status: int | None = None
    body: bytes | None = None
    body_sha256: str | None = None
    body_path: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: int | None = None
    robots_allowed: bool = True
    skipped: bool = False
    skip_reason: str | None = None
    from_cache: bool = False
    cross_host_redirect: bool = False

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.body is not None

    @property
    def text(self) -> str:
        return (self.body or b"").decode("utf-8", errors="replace")


class Client:
    def __init__(
        self,
        settings: Settings,
        conn: sqlite3.Connection,
        scan_date: str,
        session: requests.Session | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.settings = settings
        self.conn = conn
        self.scan_date = scan_date
        # resolved at call time so tests can monkeypatch time.sleep
        self._clock = clock if clock is not None else time.monotonic
        self._sleep = sleeper if sleeper is not None else (lambda s: time.sleep(s))
        self.session = session or requests.Session()
        self.user_agent = settings.user_agent()
        self.session.headers["User-Agent"] = self.user_agent
        self.archive = RawArchive(settings.paths.raw_dir)
        self.robots = RobotsCache(
            self.session,
            self.user_agent,
            self.archive,
            scan_date,
            settings.http.connect_timeout_s,
            settings.http.read_timeout_s,
            before_fetch=self._begin_request,
        )
        self._host_last: dict[str, float] = {}
        self._last_host: str | None = None
        self._last_time: float = 0.0
        self._host_429: dict[str, int] = {}
        self._stopped_hosts: set[str] = set()
        self._timeout = (settings.http.connect_timeout_s, settings.http.read_timeout_s)

    # rate limiting

    def _begin_request(self, host: str, min_interval: float | None = None) -> None:
        """Sleep until this host's turn, then mark the request as started."""
        limits = self.settings.rate_limits
        per_host = limits.per_host_min_interval_s
        crawl_delay = self.robots.crawl_delay(host)
        if crawl_delay is not None and crawl_delay > per_host:
            per_host = crawl_delay
        if min_interval is not None and min_interval > per_host:
            per_host = min_interval
        now = self._clock()
        ready = now
        if host in self._host_last:
            ready = max(ready, self._host_last[host] + per_host)
        if self._last_host is not None and self._last_host != host:
            ready = max(ready, self._last_time + limits.inter_host_min_interval_s)
        if ready > now:
            self._sleep(ready - now)
        started = self._clock()
        self._host_last[host] = started
        self._last_host = host
        self._last_time = started

    # request with retry

    def _attempt(self, url: str, host: str, min_interval: float | None) -> tuple[requests.Response, int]:
        self._begin_request(host, min_interval)
        t0 = self._clock()
        resp = self.session.get(url, timeout=self._timeout, allow_redirects=True)
        elapsed_ms = int((self._clock() - t0) * 1000)
        if resp.status_code == 429:
            count = self._host_429.get(host, 0) + 1
            self._host_429[host] = count
            logger.warning("429 from %s (%d consecutive)", host, count)
            if count >= self.settings.rate_limits.max_consecutive_429_per_host:
                raise HostStoppedError(host)
            raise RetryableStatusError(429)
        self._host_429[host] = 0
        if resp.status_code == 503:
            raise RetryableStatusError(503)
        return resp, elapsed_ms

    def _request_with_retry(
        self, url: str, host: str, min_interval: float | None = None
    ) -> tuple[requests.Response, int]:
        limits = self.settings.rate_limits
        retryer = Retrying(
            retry=retry_if_exception_type(
                (RetryableStatusError, requests.ConnectionError, requests.Timeout)
            ),
            wait=wait_random_exponential(
                multiplier=limits.backoff_base_s, max=limits.backoff_max_s
            ),
            stop=stop_after_attempt(limits.max_attempts),
            sleep=self._sleep,
            reraise=True,
        )
        return retryer(self._attempt, url, host, min_interval)

    # recorded store fetches

    def get(
        self, url: str, page_role: str, store_id: int, scan_id: int
    ) -> FetchResult:
        """Fetch a URL for a store within a scan, recording a fetches row."""
        host = host_of(url)
        now = utc_now_iso()

        existing = repo.find_fetch_in_scan(self.conn, scan_id, url)
        if existing is not None:
            return self._result_from_row(existing, from_cache=True)

        if host in self._stopped_hosts:
            repo.insert_fetch(
                self.conn,
                scan_id=scan_id,
                store_id=store_id,
                url=url,
                page_role=page_role,
                fetched_at=now,
                robots_allowed=True,
                skip_reason=RATE_LIMIT_STOP,
                now=now,
            )
            self.conn.commit()
            return FetchResult(url=url, skipped=True, skip_reason=RATE_LIMIT_STOP)

        allowed, _reason = self._robots_check(url, host, store_id, scan_id)
        if not allowed:
            logger.info("robots disallow, skipping %s", url)
            repo.insert_fetch(
                self.conn,
                scan_id=scan_id,
                store_id=store_id,
                url=url,
                page_role=page_role,
                fetched_at=now,
                robots_allowed=False,
                skip_reason=ROBOTS_DISALLOW,
                now=now,
            )
            self.conn.commit()
            return FetchResult(
                url=url,
                robots_allowed=False,
                skipped=True,
                skip_reason=ROBOTS_DISALLOW,
            )

        cached = repo.find_cached_body(self.conn, url, self.scan_date)
        if cached is not None and Path(str(cached["body_path"])).exists():
            logger.info("cache hit for %s", url)
            body = RawArchive.load(str(cached["body_path"]))
            repo.insert_fetch(
                self.conn,
                scan_id=scan_id,
                store_id=store_id,
                url=url,
                page_role=page_role,
                fetched_at=str(cached["fetched_at"]),
                robots_allowed=True,
                http_status=200,
                body_sha256=str(cached["body_sha256"]),
                body_path=str(cached["body_path"]),
                content_type=cached["content_type"],
                elapsed_ms=0,
                now=now,
            )
            self.conn.commit()
            return FetchResult(
                url=url,
                status=200,
                body=body,
                body_sha256=str(cached["body_sha256"]),
                body_path=str(cached["body_path"]),
                from_cache=True,
            )

        result = self._perform(url, host)
        repo.insert_fetch(
            self.conn,
            scan_id=scan_id,
            store_id=store_id,
            url=url,
            page_role=page_role,
            fetched_at=now,
            robots_allowed=True,
            http_status=result.status,
            skip_reason=result.skip_reason,
            body_sha256=result.body_sha256,
            body_path=result.body_path,
            content_type=result.headers.get("Content-Type"),
            elapsed_ms=result.elapsed_ms,
            now=now,
        )
        self.conn.commit()
        return result

    def get_external(
        self, url: str, min_interval: float | None = None
    ) -> FetchResult:
        """Robots-checked, rate limited, archived fetch without store context.

        Used for hosts that are not panel stores (Wayback, job boards, review
        vendors discovered via label helpers). No fetches row is written;
        provenance lives in the raw archive sidecar and the calling table.
        """
        host = host_of(url)
        if host in self._stopped_hosts:
            return FetchResult(url=url, skipped=True, skip_reason=RATE_LIMIT_STOP)
        allowed, _reason = self.robots.allowed(url)
        if not allowed:
            logger.info("robots disallow, skipping %s", url)
            return FetchResult(
                url=url,
                robots_allowed=False,
                skipped=True,
                skip_reason=ROBOTS_DISALLOW,
            )
        return self._perform(url, host, min_interval)

    # internals

    def _robots_check(
        self, url: str, host: str, store_id: int, scan_id: int
    ) -> tuple[bool, str | None]:
        info = self.robots.info(host)
        robots_url = info.url
        if repo.find_fetch_in_scan(self.conn, scan_id, robots_url) is None:
            now = utc_now_iso()
            repo.insert_fetch(
                self.conn,
                scan_id=scan_id,
                store_id=store_id,
                url=robots_url,
                page_role="robots",
                fetched_at=info.fetched_at,
                robots_allowed=True,
                http_status=info.status,
                body_sha256=info.body_sha256,
                body_path=info.body_path,
                now=now,
            )
            self.conn.commit()
        return self.robots.allowed(url)

    def _perform(
        self, url: str, host: str, min_interval: float | None = None
    ) -> FetchResult:
        try:
            resp, elapsed_ms = self._request_with_retry(url, host, min_interval)
        except HostStoppedError:
            logger.error("stopping host for rest of run: %s", host)
            self._stopped_hosts.add(host)
            return FetchResult(url=url, skipped=True, skip_reason=RATE_LIMIT_STOP)
        except RetryableStatusError as exc:
            logger.warning("gave up on %s after retries, last status %s", url, exc.status)
            return FetchResult(url=url, status=exc.status)
        except requests.Timeout:
            logger.warning("timeout fetching %s", url)
            return FetchResult(url=url, skipped=True, skip_reason="timeout")
        except requests.ConnectionError:
            logger.warning("connection error fetching %s", url)
            return FetchResult(url=url, skipped=True, skip_reason="connection_error")
        except requests.RequestException:
            logger.warning("request error fetching %s", url)
            return FetchResult(url=url, skipped=True, skip_reason="connection_error")

        final_url = str(resp.url)
        cross_host = host_of(final_url) != host
        if cross_host:
            logger.warning("cross-host redirect: %s -> %s", url, final_url)
        headers = {str(k): str(v) for k, v in resp.headers.items()}
        result = FetchResult(
            url=url,
            final_url=final_url,
            status=resp.status_code,
            headers=headers,
            elapsed_ms=elapsed_ms,
            cross_host_redirect=cross_host,
        )
        if resp.status_code == 200:
            body = resp.content
            sha, path = self.archive.store(
                host,
                self.scan_date,
                body,
                {
                    "url": url,
                    "final_url": final_url,
                    "fetched_at": utc_now_iso(),
                    "http_status": resp.status_code,
                    "headers": headers,
                    "elapsed_ms": elapsed_ms,
                    "cross_host_redirect": cross_host,
                },
            )
            result.body = body
            result.body_sha256 = sha
            result.body_path = str(path)
        logger.info(
            "fetched %s status=%s elapsed_ms=%s", url, resp.status_code, elapsed_ms
        )
        return result

    def _result_from_row(self, row: sqlite3.Row, from_cache: bool) -> FetchResult:
        body: bytes | None = None
        path = row["body_path"]
        if path is not None and Path(str(path)).exists():
            body = RawArchive.load(str(path))
        status = row["http_status"]
        return FetchResult(
            url=str(row["url"]),
            status=int(status) if status is not None else None,
            body=body,
            body_sha256=row["body_sha256"],
            body_path=path,
            robots_allowed=bool(row["robots_allowed"]),
            skipped=row["skip_reason"] is not None or not bool(row["robots_allowed"]),
            skip_reason=row["skip_reason"],
            from_cache=from_cache,
        )
