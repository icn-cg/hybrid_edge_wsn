"""PlatformIO runner for the self-reporting native C++ test executable."""

from platformio.public import TestRunnerBase


class CustomTestRunner(TestRunnerBase):
    """Use PlatformIO's build/execute stages and the program exit status."""
