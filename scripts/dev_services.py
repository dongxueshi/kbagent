"""Start local MySQL/MinIO; generate private local credentials only for a new .env."""

import argparse
import os
import secrets
import subprocess
from pathlib import Path

import pymysql
from dotenv import dotenv_values

from kbagent.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def prepare_env():
    path = ROOT / ".env"
    if path.exists():
        return
    values = {
        "replace-with-a-strong-password": secrets.token_urlsafe(32),
        "replace-with-a-different-strong-password": secrets.token_urlsafe(32),
        "replace-with-access-key": "kb-" + secrets.token_hex(12),
        "replace-with-secret-key": secrets.token_urlsafe(32),
    }
    content = (ROOT / ".env.example").read_text()
    for old, new in values.items():
        content = content.replace(old, new)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(content)
    print("Created ignored .env with generated local credentials (values not displayed)")


def prepare_test_db(settings):
    values = dotenv_values(ROOT / ".env")
    name = os.environ.get("KBAGENT_TEST_MYSQL_DATABASE") or values.get(
        "KBAGENT_TEST_MYSQL_DATABASE", "kbagent_test"
    )
    name = Settings.valid_database(name)
    if not name.endswith("_test") or name == settings.mysql_database:
        raise RuntimeError("Use a separate database whose name ends with _test")
    root_password = os.environ.get("MYSQL_ROOT_PASSWORD") or values.get("MYSQL_ROOT_PASSWORD")
    if not root_password:
        raise RuntimeError("Local test initialization needs MYSQL_ROOT_PASSWORD")
    sql = f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;"
    account = "'" + pymysql.converters.escape_string(settings.mysql_user) + "'@'%';"
    sql += f"GRANT ALL PRIVILEGES ON `{name}`.* TO " + account
    # Credentials enter docker exec by environment name, never by command-line value.
    env = dict(os.environ, MYSQL_PWD=root_password)
    subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            "kbagent",
            "exec",
            "-T",
            "-e",
            "MYSQL_PWD",
            "mysql",
            "mysql",
            "-u",
            "root",
        ],
        input=sql.encode(),
        env=env,
        cwd=ROOT,
        check=True,
    )
    print("Prepared separate MySQL test database")


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-db", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT)
    prepare_env()
    settings = Settings()
    if settings.mysql_host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("dev_services only starts local services; use existing remote servers")
    subprocess.run(
        ["docker", "compose", "-p", "kbagent", "up", "-d", "--wait"],
        cwd=ROOT,
        check=True,
    )
    if args.test_db:
        prepare_test_db(settings)
    print("Local MySQL and MinIO are healthy; connection settings are in .env")


if __name__ == "__main__":
    run()
