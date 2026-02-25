"""Entry point for gt.vscode.wrapper.

Invoked as the alias for the ``vscode`` envoy command::

    # envoy_env/commands.json
    "vscode": {
        "alias": ["python", "-m", "gt.vscode.wrapper"],
        "environment": ["python_env.json", "vscode_env.json"]
    }

Generates (or re-uses a cached) ``local_bundles.json`` from the bundles
discovered under ``ENVOY_BNDL_ROOTS``, then launches VS Code with
``ENVOY_BUNDLES_CONFIG`` set so that ``envoy`` commands running in VS Code
terminal sessions resolve bundles from the pre-built list.

Any arguments passed on the command line are forwarded verbatim to ``code``::

    envoy vscode                               # open VS Code
    envoy vscode path/to/workspace.code-workspace
    envoy vscode --new-window
    envoy vscode --wait file.py

"""
from __future__ import annotations

import logging
import sys

from . import _wrapper as wrapper

logging.basicConfig(
    level=logging.WARNING,
    format='%(levelname)s %(name)s: %(message)s',
)
log = logging.getLogger(__name__)


def main() -> None:
    """Discover bundles, write local_bundles.json, and spawn VS Code."""
    extra_args = sys.argv[1:]

    try:
        proc = wrapper.launch(extra_args)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        sys.exit(1)

    proc.wait()

    if proc.returncode:
        log.error("VS Code exited with code %d", proc.returncode)
        sys.exit(proc.returncode)


if __name__ == '__main__':
    main()
