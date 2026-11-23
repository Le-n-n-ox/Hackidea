"""Small, ordered, bounded fan-out helper shared by pipeline stages."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Callable, Generic, Iterable, TypeVar

T = TypeVar("T")
R = TypeVar("R")


@dataclass
class Outcome(Generic[R]):
    value: R | None = None
    error: BaseException | None = None
    cancelled: bool = False


def ordered_fanout(items: Iterable[T], fn: Callable[[T], R], *, workers: int,
                   cancelled: Callable[[], bool] | None = None,
                   progress: Callable[[int, int], None] | None = None) -> list[Outcome[R]]:
    """Run up to ``workers`` items at once and return one outcome per item in input order.

    Errors are isolated to their item. Once cancellation is observed no further
    work is submitted; submitted work is joined before this function returns.
    """
    rows = list(items)
    if not rows:
        return []
    results: list[Outcome[R] | None] = [None] * len(rows)
    limit = max(1, int(workers))
    pool = ThreadPoolExecutor(max_workers=limit, thread_name_prefix="hackidea")
    pending = {}
    next_index = 0
    finished = 0
    stopped = False
    try:
        while next_index < len(rows) and len(pending) < limit:
            if cancelled and cancelled():
                stopped = True
                break
            pending[pool.submit(fn, rows[next_index])] = next_index
            next_index += 1
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                index = pending.pop(future)
                try:
                    results[index] = Outcome(value=future.result())
                except BaseException as exc:  # each work item is isolated
                    results[index] = Outcome(error=exc)
                finished += 1
                if progress:
                    progress(finished, len(rows))
            while next_index < len(rows) and len(pending) < limit:
                if cancelled and cancelled():
                    stopped = True
                    break
                pending[pool.submit(fn, rows[next_index])] = next_index
                next_index += 1
            if stopped:
                break
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    for index in range(next_index, len(rows)):
        results[index] = Outcome(cancelled=True)
    return [row or Outcome(cancelled=True) for row in results]
