import os
import sys

import pytest

from bogus_security_answers.single_instance import SingleInstance


@pytest.mark.skipif(sys.platform != "win32", reason="Windows mutex")
def test_second_instance_is_rejected_until_release():
    name = f"Local\\BogusSecurityAnswers.Test.{os.getpid()}"
    first = SingleInstance(name)
    second = SingleInstance(name)
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()
    third = SingleInstance(name)
    assert third.acquire() is True
    third.release()
