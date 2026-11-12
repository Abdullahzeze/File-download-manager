"""Start-at-login support via a macOS LaunchAgent (no admin rights needed)."""
import os
import plistlib
import sys

LABEL = "com.yourname.downloadmanager"   # keep in sync with BUNDLE_ID in build_app.sh
PLIST = os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")


def _command():
    if getattr(sys, "frozen", False):                      # running as the packaged .app
        bundle = os.path.abspath(os.path.join(os.path.dirname(sys.executable), "..", ".."))
        if bundle.endswith(".app"):
            return ["/usr/bin/open", "-a", bundle, "--args", "--hidden"]
        return [sys.executable, "--hidden"]
    return [sys.executable, os.path.abspath(sys.argv[0]), "--hidden"]   # running as a script


def is_enabled():
    return os.path.exists(PLIST)


def set_enabled(on):
    if on:
        os.makedirs(os.path.dirname(PLIST), exist_ok=True)
        with open(PLIST, "wb") as f:
            plistlib.dump({"Label": LABEL, "ProgramArguments": _command(), "RunAtLoad": True}, f)
    elif os.path.exists(PLIST):
        os.remove(PLIST)
