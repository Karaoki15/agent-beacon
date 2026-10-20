"""Console-subsystem frozen helper: retain native hook stdin/stdout."""
import sys
from agent_beacon.adapters import reporter, configure

if __name__ == "__main__":
    if sys.argv[1:2] == ["--configure"]:
        raise SystemExit(configure.main(sys.argv[2:]))
    if sys.argv[1:2] == ["--self-check"]:
        print('{"app":"Agent Beacon Hooks","version":"0.2.0-beta.4"}')
    else:
        raise SystemExit(reporter.main())
