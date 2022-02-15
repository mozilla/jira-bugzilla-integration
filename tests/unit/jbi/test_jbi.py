# pylint: disable=cannot-enumerate-pytest-fixtures
import pytest

from src.core import configurator
from src.jbi import configuration


def test_mock_jbi_files():
    with pytest.raises(AssertionError):
        configurator.process_all_files_in_path(
            process=configuration.jbi_config_process,
            folder_path="tests/unit/jbi/mock_jbi_files",
        )


def test_actual_jbi_files():
    jbi_map = configuration.jbi_config_map()
    assert jbi_map
