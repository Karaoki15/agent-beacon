"""Frozen setup entry point; installed copy also provides safe uninstall."""
from agent_beacon.windows_installer import main

if __name__ == "__main__":
    raise SystemExit(main())
