from __future__ import annotations

import argparse
import asyncio
import math
from dataclasses import dataclass
from time import perf_counter

import httpx


@dataclass(slots=True)
class Result:
    duration_ms: float
    status_code: int | None
    error: str | None = None


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(percent / 100 * len(ordered)) - 1)
    return ordered[max(index, 0)]


async def run_request(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    base_url: str,
    index: int,
) -> Result:
    path = "/health" if index % 5 == 0 else "/api/v1/menu?language=ru"
    async with semaphore:
        started = perf_counter()
        try:
            response = await client.get(base_url + path)
            duration_ms = (perf_counter() - started) * 1000
            return Result(duration_ms, response.status_code)
        except httpx.HTTPError as exc:
            duration_ms = (perf_counter() - started) * 1000
            return Result(duration_ms, None, type(exc).__name__)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only load test for the bar bot API")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=600)
    parser.add_argument("--concurrency", type=int, default=50)
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        parser.error("--requests and --concurrency must be positive")

    semaphore = asyncio.Semaphore(args.concurrency)
    timeout = httpx.Timeout(15.0)
    limits = httpx.Limits(
        max_connections=args.concurrency,
        max_keepalive_connections=args.concurrency,
    )
    started = perf_counter()
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        results = await asyncio.gather(
            *(
                run_request(client, semaphore, args.base_url.rstrip("/"), index)
                for index in range(args.requests)
            )
        )
    elapsed = perf_counter() - started

    successes = [result for result in results if result.status_code == 200]
    failures = [result for result in results if result.status_code != 200]
    durations = [result.duration_ms for result in results]
    print(f"requests={len(results)} concurrency={args.concurrency}")
    print(f"success={len(successes)} failures={len(failures)}")
    print(f"elapsed_seconds={elapsed:.2f} requests_per_second={len(results) / elapsed:.2f}")
    print(
        "latency_ms "
        f"p50={percentile(durations, 50):.1f} "
        f"p95={percentile(durations, 95):.1f} "
        f"p99={percentile(durations, 99):.1f} "
        f"max={max(durations, default=0):.1f}"
    )
    if failures:
        summary: dict[str, int] = {}
        for result in failures:
            key = str(result.status_code) if result.status_code else result.error or "unknown"
            summary[key] = summary.get(key, 0) + 1
        print(f"failure_summary={summary}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
