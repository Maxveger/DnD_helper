"""Run a CI command and expose a concise failure in the job's annotations."""

import subprocess
import sys

from report_test_failures import escape


def main():
    result = subprocess.run(sys.argv[1:], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    sys.stdout.buffer.write(result.stdout)
    sys.stdout.flush()
    if result.returncode:
        lines = result.stdout.decode("utf-8", errors="replace").splitlines()[-60:]
        lines = [line[:800] for line in lines]
        print("::error::" + escape("\n".join(lines)[-12000:]))
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
