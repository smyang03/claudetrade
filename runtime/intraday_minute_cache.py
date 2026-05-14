from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from typing import Any, Callable

from runtime.intraday_features import compute_intraday_features


Provider = Callable[..., Any]


def _market_key(market: str) -> str:
    return "US" if str(market or "").upper() == "US" else "KR"


def _ticker_key(market: str, ticker: Any) -> str:
    text = str(ticker or "").strip()
    return text.upper() if _market_key(market) == "US" else text


def _positive(value: Any) -> float | None:
    try:
        parsed = float(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _default_provider(**kwargs):
    from kis_api import get_intraday_candles

    return get_intraday_candles(**kwargs)


class IntradayMinuteCache:
    def __init__(
        self,
        *,
        provider: Provider | None = None,
        provider_name: str = "",
        ttl_sec: float | None = None,
        max_workers: int | None = None,
        min_call_interval_sec: float | None = None,
        timeout_sec: float | None = None,
        now_func: Callable[[], float] | None = None,
    ) -> None:
        self.provider = provider or _default_provider
        self.provider_name = provider_name or os.getenv("INTRADAY_EVIDENCE_PROVIDER", "auto")
        self.ttl_sec = float(ttl_sec if ttl_sec is not None else os.getenv("INTRADAY_EVIDENCE_CACHE_TTL_SEC", "30"))
        self.max_workers = max(1, int(max_workers if max_workers is not None else os.getenv("INTRADAY_EVIDENCE_MAX_WORKERS", "1")))
        self.min_call_interval_sec = max(
            0.0,
            float(
                min_call_interval_sec
                if min_call_interval_sec is not None
                else os.getenv("INTRADAY_EVIDENCE_MIN_CALL_INTERVAL_SEC", "0")
            ),
        )
        self.timeout_sec = max(
            0.05,
            float(timeout_sec if timeout_sec is not None else os.getenv("INTRADAY_EVIDENCE_PREFETCH_TIMEOUT_SEC", "8")),
        )
        self._now = now_func or time.time
        self._cache: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._call_lock = threading.Lock()
        self._last_call_ts = 0.0

    def clear(
        self,
        *,
        market: str | None = None,
        tickers: list[str] | None = None,
        session_date: str | None = None,
    ) -> None:
        market_key = _market_key(market or "") if market else ""
        ticker_set = None
        if tickers is not None:
            ticker_set = {_ticker_key(market_key or "KR", ticker) for ticker in tickers}
            if market_key == "US":
                ticker_set = {ticker.upper() for ticker in ticker_set}
        with self._lock:
            for key in list(self._cache):
                key_market, key_session, key_ticker, _provider = key
                if market_key and key_market != market_key:
                    continue
                if session_date and key_session != str(session_date):
                    continue
                if ticker_set is not None and key_ticker not in ticker_set:
                    continue
                self._cache.pop(key, None)

    def _cache_key(self, market: str, session_date: str, ticker: str, provider_name: str) -> tuple[str, str, str, str]:
        return (_market_key(market), str(session_date), _ticker_key(market, ticker), str(provider_name or self.provider_name))

    def _cached(self, key: tuple[str, str, str, str]) -> dict[str, Any] | None:
        now = self._now()
        with self._lock:
            item = self._cache.get(key)
            if not item:
                return None
            ttl_sec = float(item.get("ttl_sec", self.ttl_sec) or self.ttl_sec)
            if now - float(item.get("cached_at", 0.0) or 0.0) > ttl_sec:
                self._cache.pop(key, None)
                return None
            return dict(item.get("features") or {})

    def _store(self, key: tuple[str, str, str, str], features: dict[str, Any]) -> None:
        quality = str((features or {}).get("data_quality") or "").strip().lower()
        if quality == "minute_missing":
            try:
                missing_ttl = float(os.getenv("INTRADAY_EVIDENCE_MISSING_CACHE_TTL_SEC", "0") or 0)
            except Exception:
                missing_ttl = 0.0
            if missing_ttl <= 0:
                return
            ttl_sec = max(0.05, min(float(self.ttl_sec), missing_ttl))
        elif quality == "minute_partial":
            try:
                partial_ttl = float(os.getenv("INTRADAY_EVIDENCE_PARTIAL_CACHE_TTL_SEC", "5") or 5)
            except Exception:
                partial_ttl = 5.0
            ttl_sec = max(0.05, min(float(self.ttl_sec), partial_ttl))
        else:
            ttl_sec = float(self.ttl_sec)
        with self._lock:
            self._cache[key] = {"cached_at": self._now(), "ttl_sec": ttl_sec, "features": dict(features)}

    def _shutdown_executor(self, executor: ThreadPoolExecutor, *, wait: bool) -> None:
        try:
            executor.shutdown(wait=wait, cancel_futures=True)
        except TypeError:
            executor.shutdown(wait=wait)

    def _call_provider(self, *, timeout_sec: float | None = None, deadline: float | None = None, **kwargs):
        started = self._now()
        if self.min_call_interval_sec > 0:
            with self._call_lock:
                now = self._now()
                wait_sec = self.min_call_interval_sec - (now - self._last_call_ts)
                if wait_sec > 0:
                    if timeout_sec is not None and wait_sec >= timeout_sec:
                        raise TimeoutError("provider_timeout")
                    time.sleep(wait_sec)
                self._last_call_ts = self._now()
        if timeout_sec is not None:
            elapsed = max(0.0, self._now() - started)
            timeout_sec = max(0.0, timeout_sec - elapsed)
            if timeout_sec <= 0:
                raise TimeoutError("provider_timeout")
        provider_kwargs = dict(kwargs)
        if deadline is not None:
            provider_kwargs.setdefault("deadline", deadline)
            provider_kwargs.setdefault("request_timeout", timeout_sec)
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self.provider, **provider_kwargs)
        try:
            result = future.result(timeout=timeout_sec)
        except FuturesTimeoutError as exc:
            future.cancel()
            self._shutdown_executor(executor, wait=False)
            raise TimeoutError("provider_timeout") from exc
        except TypeError:
            future.cancel()
            self._shutdown_executor(executor, wait=False)
            clean_kwargs = dict(kwargs)
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(self.provider, **clean_kwargs)
            try:
                result = future.result(timeout=timeout_sec)
            except FuturesTimeoutError as exc:
                future.cancel()
                self._shutdown_executor(executor, wait=False)
                raise TimeoutError("provider_timeout") from exc
            except Exception:
                self._shutdown_executor(executor, wait=False)
                raise
            self._shutdown_executor(executor, wait=True)
            return result
        except Exception:
            self._shutdown_executor(executor, wait=False)
            raise
        self._shutdown_executor(executor, wait=True)
        return result

    def _fetch_one(
        self,
        *,
        market: str,
        ticker: str,
        session_date: str,
        token: str | None,
        regular_open,
        known_at,
        avg_daily_volume: float | None,
        opening_range_min: int | None,
        provider_name: str,
        deadline: float | None = None,
    ) -> tuple[str, dict[str, Any] | None, bool, str]:
        key = self._cache_key(market, session_date, ticker, provider_name)
        cached = self._cached(key)
        if cached is not None:
            return key[2], cached, True, ""
        try:
            remaining = None
            if deadline is not None:
                remaining = deadline - self._now()
                if remaining <= 0:
                    return key[2], None, False, "prefetch_timeout"
            candles = self._call_provider(
                ticker=key[2],
                token=token,
                market=key[0],
                session_date=str(session_date),
                start_at=regular_open,
                end_at=known_at,
                provider=provider_name,
                timeout_sec=remaining,
                deadline=deadline,
            )
            features = compute_intraday_features(
                candles,
                market=key[0],
                ticker=key[2],
                regular_open=regular_open,
                known_at=known_at,
                avg_daily_volume=avg_daily_volume,
                opening_range_min=opening_range_min,
                source=provider_name,
            )
            self._store(key, features)
            return key[2], features, False, ""
        except TimeoutError as exc:
            return key[2], None, False, str(exc)[:240] or "provider_timeout"
        except Exception as exc:
            return key[2], None, False, str(exc)[:240]

    def get_many(
        self,
        *,
        market: str,
        tickers: list[str],
        session_date: str,
        token: str | None,
        regular_open,
        known_at,
        avg_daily_volume_by_ticker: dict[str, float] | None = None,
        opening_range_min: int | None = None,
        provider_name: str = "",
    ) -> dict[str, Any]:
        market_key = _market_key(market)
        provider_label = str(provider_name or self.provider_name or "auto")
        ordered = []
        seen = set()
        for ticker in tickers or []:
            key = _ticker_key(market_key, ticker)
            if key and key not in seen:
                seen.add(key)
                ordered.append(key)
        features_by_ticker: dict[str, dict[str, Any]] = {}
        errors_by_ticker: dict[str, str] = {}
        used_cache = 0
        avg_map = {
            _ticker_key(market_key, key): float(value)
            for key, value in (avg_daily_volume_by_ticker or {}).items()
            if _positive(value) is not None
        }

        started_at = self._now()
        deadline = started_at + self.timeout_sec

        def _task(ticker: str):
            return self._fetch_one(
                market=market_key,
                ticker=ticker,
                session_date=session_date,
                token=token,
                regular_open=regular_open,
                known_at=known_at,
                avg_daily_volume=avg_map.get(ticker),
                opening_range_min=opening_range_min,
                provider_name=provider_label,
                deadline=deadline,
            )

        if self.max_workers <= 1 or len(ordered) <= 1:
            for idx, ticker in enumerate(ordered):
                if self._now() >= deadline:
                    for remaining in ordered[idx:]:
                        errors_by_ticker[remaining] = "prefetch_timeout"
                    break
                out_ticker, features, cached, error = _task(ticker)
                if cached:
                    used_cache += 1
                if features is not None:
                    features_by_ticker[out_ticker] = features
                elif error:
                    errors_by_ticker[out_ticker] = error
                if self._now() >= deadline:
                    for remaining in ordered[idx + 1:]:
                        errors_by_ticker[remaining] = "prefetch_timeout"
                    break
        else:
            executor = ThreadPoolExecutor(max_workers=self.max_workers)
            try:
                futures = {executor.submit(_task, ticker): ticker for ticker in ordered}
                done = set()
                try:
                    for future in as_completed(futures, timeout=self.timeout_sec):
                        done.add(future)
                        out_ticker, features, cached, error = future.result()
                        if cached:
                            used_cache += 1
                        if features is not None:
                            features_by_ticker[out_ticker] = features
                        elif error:
                            errors_by_ticker[out_ticker] = error
                except Exception as exc:
                    for future, ticker in futures.items():
                        if future in done:
                            continue
                        future.cancel()
                        errors_by_ticker[ticker] = f"timeout_or_cancelled:{exc}"[:240]
            finally:
                self._shutdown_executor(executor, wait=False)

        complete = 0
        partial = 0
        missing = 0
        for ticker in ordered:
            features = features_by_ticker.get(ticker)
            if not features:
                missing += 1
                continue
            quality = str(features.get("data_quality") or "")
            if quality == "minute_complete":
                complete += 1
            elif quality == "minute_missing":
                missing += 1
            else:
                partial += 1
        return {
            "market": market_key,
            "session_date": str(session_date),
            "provider": provider_label,
            "requested": len(ordered),
            "fetched": len(features_by_ticker),
            "complete": complete,
            "partial": partial,
            "missing": missing,
            "features_by_ticker": features_by_ticker,
            "errors_by_ticker": errors_by_ticker,
            "used_cache": used_cache,
        }
