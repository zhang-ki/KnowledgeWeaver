"""Drive only immediately-completing fake/offline coroutines without a socket loop."""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any


def drive_ready_coroutine(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """Run fake/offline coroutine paths that perform no yielding I/O.

    Windows loopback socket creation is restricted in some isolated worktrees.
    This helper intentionally refuses any coroutine that suspends; live async
    provider and service calls still require a normal event loop.
    """
    try:
        yielded = coroutine.send(None)
    except StopIteration as finished:
        return finished.value
    finally:
        if coroutine.cr_await is not None:
            coroutine.close()
    raise RuntimeError(f"offline coroutine attempted asynchronous I/O: {type(yielded).__name__}")
