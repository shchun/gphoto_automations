from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Iterable, Optional, TypeVar

import requests
from dateutil.relativedelta import relativedelta
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
T = TypeVar("T")


def kst_today() -> date:
    return datetime.now(tz=KST).date()


def month_range_to_dates(start_month: str, end_month: str) -> tuple[date, date]:
    # start_month/end_month: YYYY-MM (inclusive)
    sy, sm = (int(x) for x in start_month.split("-"))
    ey, em = (int(x) for x in end_month.split("-"))
    start = date(sy, sm, 1)
    end = date(ey, em, 1) + relativedelta(months=1) - relativedelta(days=1)
    if start > end:
        raise ValueError(f"Invalid range: {start_month}..{end_month}")
    return start, end


def recent_month_dates(reference_kst: Optional[date] = None, months: int = 1) -> tuple[date, date]:
    end = reference_kst or kst_today()
    start = end + relativedelta(months=-months)
    if start > end:
        start, end = end, start
    return start, end


def iso_to_kst_date(iso_dt: str) -> str:
    # creationTime is RFC3339, e.g. 2020-01-02T03:04:05Z
    dt = datetime.fromisoformat(iso_dt.replace("Z", "+00:00"))
    return dt.astimezone(KST).date().isoformat()


def json_dumps_compact(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 5
    base_sleep_s: float = 1.0
    max_sleep_s: float = 30.0


def sleep_backoff(attempt: int, policy: RetryPolicy) -> None:
    # Full jitter exponential backoff
    cap = min(policy.max_sleep_s, policy.base_sleep_s * (2**attempt))
    time.sleep(random.uniform(0, cap))


def with_retries(
    fn: Callable[[], T],
    *,
    retry_on: tuple[type[BaseException], ...],
    policy: RetryPolicy,
) -> T:
    last_err: Optional[BaseException] = None
    for attempt in range(policy.max_retries + 1):
        try:
            return fn()
        except retry_on as e:
            last_err = e
            if attempt >= policy.max_retries:
                break
            sleep_backoff(attempt, policy)
    assert last_err is not None
    raise last_err


def download_to_path(
    *,
    url: str,
    path: str,
    timeout_s: tuple[float, float] = (10.0, 60.0),
    policy: RetryPolicy = RetryPolicy(),
) -> None:
    def _once() -> None:
        with requests.get(url, stream=True, timeout=timeout_s) as r:
            r.raise_for_status()
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

    with_retries(_once, retry_on=(requests.RequestException,), policy=policy)


def chunked(iterable: Iterable[T], size: int) -> Iterable[list[T]]:
    batch: list[T] = []
    for x in iterable:
        batch.append(x)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch

