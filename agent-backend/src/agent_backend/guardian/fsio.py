"""Atomic file I/O shared by the guardian data stores.

Every store write goes through ``atomic_write_text`` (sibling temp file +
``os.replace``) so a crash mid-write can never leave a truncated file: readers
see either the old complete content or the new complete content. A truncated
rules/policy file would otherwise be silently replaced by its empty default on
the next load — losing the parent's configuration.
"""

from __future__ import annotations

import os
from pathlib import Path


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` atomically, creating parent directories.

    The temp file lives in the same directory as the target so ``os.replace``
    stays within one filesystem (rename is atomic on POSIX). On failure the
    temp file is removed and the original target is left untouched.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding=encoding)
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
