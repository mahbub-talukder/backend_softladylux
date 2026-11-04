# Covers M6 restore runbook: restore.sh refuses to overwrite by accident and round-trips a backup.
import gzip
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3] / "scripts"
RESTORE, BACKUP = ROOT / "restore.sh", ROOT / "backup.sh"


@pytest.fixture
def env(tmp_path):
    """Stub psql/pg_dump: a 'database' is a text file; the dump is its content."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    db = tmp_path / "fake.db"
    (bin_dir / "pg_dump").write_text(f"#!/bin/sh\ncat '{db}' 2>/dev/null || true\n")
    (bin_dir / "psql").write_text(
        f"#!/bin/sh\nif echo \"$*\" | grep -q information_schema; then "
        f"[ -s '{db}' ] && echo 5 || echo 0; else cat > '{db}'; fi\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    media = tmp_path / "data" / "media"
    return db, {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "DATABASE_URL": "postgres://x",
                "MEDIA_DIR": str(media), "BACKUP_DIR": str(tmp_path / "bk")}, tmp_path


def run(script, env, *args):
    return subprocess.run(["bash", str(script), *args], env=env, capture_output=True, text=True)


def test_database_round_trip_backup_then_restore(env):
    db, e, tmp = env
    db.write_text("CREATE TABLE orders;\nINSERT 42;\n")
    (tmp / "data" / "media").mkdir(parents=True)
    assert run(BACKUP, e, "--mode", "db-only").returncode == 0
    dump = next((tmp / "bk").glob("db-*.sql.gz"))
    assert "INSERT 42" in gzip.open(dump, "rt").read()
    db.write_text("")  # a fresh, empty database
    r = run(RESTORE, e, "--db", str(dump))
    assert r.returncode == 0 and "database restored" in r.stdout
    assert db.read_text() == "CREATE TABLE orders;\nINSERT 42;\n"


def test_restore_refuses_a_database_that_already_has_tables(env):
    db, e, tmp = env
    db.write_text("existing data")
    (tmp / "data" / "media").mkdir(parents=True)
    run(BACKUP, e, "--mode", "db-only")
    dump = next((tmp / "bk").glob("db-*.sql.gz"))
    db.write_text("live data")
    r = run(RESTORE, e, "--db", str(dump))
    assert r.returncode == 1 and "already has" in r.stderr and db.read_text() == "live data"
    assert run(RESTORE, e, "--db", str(dump), "--force").returncode == 0
    assert db.read_text() == "existing data"


def test_media_round_trip_and_overwrite_protection(env):
    db, e, tmp = env
    media = tmp / "data" / "media"
    (media / "uploads").mkdir(parents=True)
    (media / "uploads" / "a.png").write_bytes(b"png")
    run(BACKUP, e, "--mode", "media-only")
    archive = next((tmp / "bk").glob("media-*.tar.gz"))
    shutil.rmtree(media)  # a fresh server: no media folder at all
    assert run(RESTORE, e, "--media", str(archive)).returncode == 0
    assert (media / "uploads" / "a.png").read_bytes() == b"png"
    r = run(RESTORE, e, "--media", str(archive))  # now non-empty
    assert r.returncode == 1 and "not empty" in r.stderr
    assert run(RESTORE, e, "--media", str(archive), "--force").returncode == 0


@pytest.mark.parametrize("args,code", [
    ([], 2), (["--db"], 1), (["--db", "/nope.gz"], 1), (["--bogus"], 2),
])
def test_bad_usage(env, args, code):
    _, e, _ = env
    assert run(RESTORE, e, *args).returncode == code


def test_corrupt_file_is_refused(env, tmp_path):
    _, e, _ = env
    bad = tmp_path / "bad.sql.gz"
    bad.write_text("not gzip")
    r = run(RESTORE, e, "--db", str(bad))
    assert r.returncode == 1 and "valid gzip" in r.stderr
