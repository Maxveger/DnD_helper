"""Expose CI test failures as annotations, readable without downloading job logs."""

import sys
from pathlib import Path
from xml.etree import ElementTree


def escape(value):
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main():
    report = Path(sys.argv[1])
    if not report.exists():
        print("::error::Pytest did not create its XML report. Inspect the test step log.")
        return
    root = ElementTree.parse(report).getroot()
    for case in root.iter("testcase"):
        for failure in [*case.findall("failure"), *case.findall("error")]:
            text = case.get("name", "test") + "\n" + (failure.text or failure.get("message", ""))
            print("::error::" + escape(text[-10000:]))


if __name__ == "__main__":
    main()
