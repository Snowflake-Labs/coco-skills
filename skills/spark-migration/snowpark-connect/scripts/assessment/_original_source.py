"""Materialize the pre-Phase-0.5 source snapshot from a conversion's git repo.

Phase 0 of the migrate skill (``SKILL.md`` step 6) tags the initial commit
``phase-0-source`` immediately after copying the customer source into
``<CONVERSION>/Output/`` and before Phase 0.5 recipes mutate the tree. This
helper extracts that tagged tree into a fresh temp directory so the Phase 1a
assessment report's :mod:`scan_codebase` and analyzer-line-rebasing logic can
read the customer's ORIGINAL source, not the post-Phase-0.5 version.

Implementation uses ``git archive`` + stdlib ``tarfile`` so there's no index
pollution on the conversion's repo and no dependency on the ``tar`` binary.
"""
from __future__ import annotations

import contextlib
import io
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Iterator

DEFAULT_TAG = "phase-0-source"


class OriginalSourceUnavailable(RuntimeError):
    """Raised when the ``phase-0-source`` tag cannot be resolved.

    Callers (the renderer) treat this as "fall back to scanning ``Output/``"
    with a logged warning, not as a fatal error — older conversions predate
    the tag and should still render.
    """


def _run_git_archive(conversion_root: Path, tag: str) -> bytes:
    """Run ``git -C <conversion_root> archive <tag>`` and return the tarball.

    Raises ``OriginalSourceUnavailable`` if git is missing, the path isn't a
    repo, or the tag doesn't exist. The exception carries the git stderr so
    the renderer can log a meaningful warning.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(conversion_root), "archive", tag],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise OriginalSourceUnavailable(
            f"`git` executable not found on PATH ({e}). Cannot materialize "
            f"original source from tag {tag!r}."
        ) from e

    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise OriginalSourceUnavailable(
            f"`git -C {conversion_root} archive {tag}` exited "
            f"{proc.returncode}: {stderr or '<no stderr>'}"
        )

    if not proc.stdout:
        raise OriginalSourceUnavailable(
            f"`git -C {conversion_root} archive {tag}` produced empty output; "
            f"the tag may point at an empty tree."
        )

    return proc.stdout


@contextlib.contextmanager
def materialize_original_source(
    conversion_root: Path,
    tag: str = DEFAULT_TAG,
) -> Iterator[Path]:
    """Yield a temp directory containing the source at ``tag`` (default
    ``phase-0-source``).

    The directory is created with :func:`tempfile.TemporaryDirectory` and
    deleted on context exit, so callers never need to clean up.

    Path convention: the tag is expected to capture the conversion's whole
    tracked tree, which on Phase 0 is the ``Output/`` directory plus
    ``migration_state.json`` skeleton. The yielded directory is the
    extraction root; callers that want just the ``Output/`` subtree should
    join ``yielded_dir / "Output"``.

    Raises :class:`OriginalSourceUnavailable` if the tag can't be resolved.
    """
    conversion_root = conversion_root.resolve()
    tarball = _run_git_archive(conversion_root, tag)

    with tempfile.TemporaryDirectory(prefix="scos-original-source-") as tmp_str:
        tmp_dir = Path(tmp_str)
        with tarfile.open(fileobj=io.BytesIO(tarball)) as tf:
            # ``data`` filter (Python 3.12+) blocks absolute paths, parent
            # traversal, and special files. Fall back to no filter on older
            # Python so the helper still works during the project's CI window.
            try:
                tf.extractall(tmp_dir, filter="data")  # type: ignore[arg-type]
            except TypeError:
                tf.extractall(tmp_dir)  # noqa: S202
        yield tmp_dir
