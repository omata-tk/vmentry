"""Backward compatible wrapper for install-time bootstrap.

Use installer.bootstrap as the canonical location.
"""

from installer.bootstrap import main, run_installation_bootstrap


if __name__ == '__main__':
    main()
