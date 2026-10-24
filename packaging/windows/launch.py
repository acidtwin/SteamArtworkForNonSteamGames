"""Entry point for the Windows build (PyInstaller needs a script, not a package)."""

import sys

from steam_artwork_finder.gui.app import main

sys.exit(main())
