import pytest

from kbagent.schema import require_supported_version


@pytest.mark.parametrize("version", ["5.7.44", "5.7.44-log", "8.0.40", "8.4.0"])
def test_supported_mysql_versions(version):
    require_supported_version(version)


@pytest.mark.parametrize(
    "version", ["5.6.51", "5.5.62", "5.7.44-MariaDB", "10.11.6-MariaDB", "unknown"]
)
def test_unsupported_versions_have_clear_error(version):
    with pytest.raises(RuntimeError, match="MySQL 5.7 or newer is required"):
        require_supported_version(version)
