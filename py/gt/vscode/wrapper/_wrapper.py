"""VS Code wrapper — bundles-config pre-generation.

Discovers all envoy bundles under ``ENVOY_BNDL_ROOTS`` and writes the result
to a ``local_bundles.json`` file in a user-local directory.  The file is then
injected into VS Code's environment as ``ENVOY_BUNDLES_CONFIG``, so that
``envoy`` commands run in VS Code terminals resolve bundles from the cached
list instead of performing a full git-repo scan each time.

Staleness detection avoids re-scanning on every launch.  The scan is repeated
only when:

- ``ENVOY_BNDL_ROOTS`` changes (different set of roots).
- Any root directory or its immediate subdirectories have a newer mtime
  (catches new clones, deletions, or ``envoy_env/`` appearing in a repo).

"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

import gt.envoy as envoy

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_LOCAL_DATA = Path(os.environ.get('LOCALAPPDATA') or '~').expanduser()
_CONFIG_DIR = _LOCAL_DATA / 'gt' / 'envoy'

#: Path where the generated bundles-config file is written.
LOCAL_BUNDLES_PATH: Path = _CONFIG_DIR / 'local_bundles.json'

#: Sidecar metadata file used for staleness detection.
_META_PATH: Path = _CONFIG_DIR / 'local_bundles_meta.json'

# ---------------------------------------------------------------------------
# Staleness detection
# ---------------------------------------------------------------------------

def _scan_mtimes(roots: list[str]) -> dict[str, float]:
    """Return last-modified times for each root dir and its immediate children.

    Two levels are sufficient to detect:

    - A new bundle cloned under a root (modifies the namespace dir's mtime).
    - A bundle directory removed (same).
    - ``envoy_env/`` added to or removed from an existing bundle (modifies the
      bundle directory's mtime since it is a direct child of the namespace dir).
    """
    mtimes: dict[str, float] = {}
    for root_str in roots:
        root = Path(root_str)
        if not root.is_dir():
            continue
        try:
            mtimes[str(root)] = root.stat().st_mtime
            for child in root.iterdir():
                if child.is_dir():
                    mtimes[str(child)] = child.stat().st_mtime
        except OSError as exc:
            log.debug("Could not stat %s: %s", root_str, exc)
    return mtimes


def _is_stale(roots_str: str) -> bool:
    """Return ``True`` if ``local_bundles.json`` should be regenerated."""
    if not LOCAL_BUNDLES_PATH.exists() or not _META_PATH.exists():
        return True

    try:
        meta = json.loads(_META_PATH.read_text(encoding='utf-8'))
    except Exception:
        return True

    if meta.get('bndl_roots') != roots_str:
        log.debug("ENVOY_BNDL_ROOTS changed — will regenerate local_bundles.json")
        return True

    sep = ';' if os.name == 'nt' else ':'
    roots = [r.strip() for r in roots_str.split(sep) if r.strip()]
    if _scan_mtimes(roots) != meta.get('depth2_mtimes', {}):
        log.debug("Bundle root layout changed — will regenerate local_bundles.json")
        return True

    return False


# ---------------------------------------------------------------------------
# Bundle config generation
# ---------------------------------------------------------------------------

def write_local_bundles(*, force: bool = False) -> Path:
    """Discover envoy bundles and write ``local_bundles.json``.

    Skips regeneration when the cached file is already up to date (based on
    mtime comparison of the bundle root directories).  Pass ``force=True`` to
    unconditionally regenerate regardless of the staleness check.

    Args:
        force: Bypass staleness detection and always regenerate.

    Returns:
        The path to the written (or unchanged) ``local_bundles.json``.
    """
    sep = ';' if os.name == 'nt' else ':'
    roots_str = os.environ.get('ENVOY_BNDL_ROOTS', '')

    if not force and not _is_stale(roots_str):
        log.debug("local_bundles.json is up to date: %s", LOCAL_BUNDLES_PATH)
        return LOCAL_BUNDLES_PATH

    log.debug("Scanning for envoy bundles under ENVOY_BNDL_ROOTS...")
    bundles = envoy.discover_bundles_auto()
    bundle_paths = [str(b.root) for b in bundles]

    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_BUNDLES_PATH.write_text(
        json.dumps({'bundles': bundle_paths}, indent=2),
        encoding='utf-8',
    )
    log.info("Wrote %d bundle(s) to %s", len(bundle_paths), LOCAL_BUNDLES_PATH)

    roots = [r.strip() for r in roots_str.split(sep) if r.strip()]
    _META_PATH.write_text(
        json.dumps(
            {
                'bndl_roots': roots_str,
                'depth2_mtimes': _scan_mtimes(roots),
            },
            indent=2,
        ),
        encoding='utf-8',
    )
    return LOCAL_BUNDLES_PATH


# ---------------------------------------------------------------------------
# VS Code executable resolution
# ---------------------------------------------------------------------------

_FALLBACK_CODE_PATHS: tuple[Path, ...] = (
    # Per-user installer (most common on Windows)
    Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Microsoft VS Code' / 'Code.exe',
    # System-wide installer
    Path('C:/Program Files/Microsoft VS Code/Code.exe'),
)


def resolve_code_exe() -> str:
    """Return the absolute path to the VS Code executable.

    Resolution order:

    1. ``VSCODE_EXE`` environment variable — set via ``vscode_env.json`` for
       non-standard install locations.
    2. ``code`` on ``PATH`` via :func:`shutil.which`.
    3. Standard per-user and system-wide Windows install paths.

    Raises:
        FileNotFoundError: VS Code cannot be located by any of the above means.
    """
    explicit = os.environ.get('VSCODE_EXE', '').strip()
    if explicit and Path(explicit).exists():
        return explicit

    found = shutil.which('code')
    if found:
        return found

    for candidate in _FALLBACK_CODE_PATHS:
        if candidate.exists():
            return str(candidate)

    paths_tried = ', '.join(str(p) for p in _FALLBACK_CODE_PATHS)
    raise FileNotFoundError(
        "Cannot locate the VS Code executable. "
        f"Tried PATH and fallback locations ({paths_tried}). "
        "Set VSCODE_EXE in vscode_env.json to override."
    )


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------

def launch_vscode(extra_args: list[str]) -> subprocess.Popen:
    """Generate ``local_bundles.json`` (if stale) and spawn VS Code.

    The subprocess inherits the current process environment with
    ``ENVOY_BUNDLES_CONFIG`` added, pointing to the freshly written (or
    cached) ``local_bundles.json``.  Envoy commands executed in VS Code
    terminal sessions will read this file instead of triggering a fresh
    git-repo discovery scan.

    Args:
        extra_args: Arguments forwarded verbatim to the ``code`` executable
            (e.g. workspace paths, ``--new-window``, ``--wait``, etc.).

    Returns:
        The running :class:`subprocess.Popen` instance.

    Raises:
        FileNotFoundError: VS Code executable cannot be found.
    """
    bundles_config = write_local_bundles()
    code_exe = resolve_code_exe()

    # Inject ENVOY_BUNDLES_CONFIG into the current environment before spawning
    # so it is visible to VS Code and to any envoy commands run from its
    # terminals.  proc.spawn with inherit_env=True copies os.environ, so
    # setting the variable here is sufficient — no separate env dict needed.
    os.environ['ENVOY_BUNDLES_CONFIG'] = str(bundles_config)

    log.debug("Spawning VS Code: %s %s", code_exe, extra_args)
    return envoy.proc.spawn([code_exe] + list(extra_args), inherit_env=True)
