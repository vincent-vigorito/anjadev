"""Optional CI gate: a skipped test must not silently certify a release."""
import pytest


def pytest_addoption(parser):
    parser.addoption("--fail-on-skip", action="store_true", help="Fail if any test or module is skipped")


def pytest_configure(config):
    if config.getoption("--fail-on-skip"):
        config.pluginmanager.register(_SkipGate(), "anja-skip-gate")


class _SkipGate:
    def __init__(self):
        self.skipped = []

    def pytest_collectreport(self, report):
        if report.skipped:
            self.skipped.append(report.nodeid)

    def pytest_runtest_logreport(self, report):
        if report.skipped:
            self.skipped.append(report.nodeid)

    def pytest_sessionfinish(self, session, exitstatus):
        if self.skipped and exitstatus in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED):
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter):
        if self.skipped:
            terminalreporter.write_sep("=", "--fail-on-skip: skipped tests are not allowed")
            for nodeid in self.skipped:
                terminalreporter.write_line(nodeid)
