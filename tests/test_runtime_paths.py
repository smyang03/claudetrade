from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from runtime_paths import _is_writable_dir


def test_writable_probe_is_concurrency_safe(tmp_path: Path) -> None:
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: _is_writable_dir(tmp_path), range(128)))

    assert all(results)
    assert list(tmp_path.glob(".write_probe*")) == []
