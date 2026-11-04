# Covers SRS §9 Reliability and scenario 30: backup.sh --mode db-only | media-only | all (default),
# keeping the last 14. A stub `pg_dump` is put on PATH so the test needs no database tools.
import os
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "backup.sh"


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "pg_dump"
    stub.write_text("#!/bin/sh\necho '-- stub dump of '\"$*\"\n")
    stub.chmod(0o755)
    media = tmp_path / "data" / "media"
    (media / "uploads").mkdir(parents=True)
    (media / "uploads" / "a.png").write_bytes(b"png")
    out = tmp_path / "out"
    e = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "BACKUP_DIR": str(out),
         "MEDIA_DIR": str(media), "DATABASE_URL": "postgres://u:p@db/x"}
    return e, out


def run(env, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True)


def names(out):
    return sorted(p.name.split("-")[0] for p in out.iterdir()) if out.exists() else []


def test_db_only_creates_only_a_database_dump(env):
    e, out = env
    r = run(e, "--mode", "db-only")
    assert r.returncode == 0 and names(out) == ["db"] and "database backup" in r.stdout


def test_media_only_creates_only_a_media_archive_with_the_files(env):
    e, out = env
    r = run(e, "--mode", "media-only")
    assert r.returncode == 0 and names(out) == ["media"]
    listing = subprocess.run(["tar", "-tzf", str(next(out.iterdir()))], capture_output=True,
                             text=True).stdout
    assert "media/uploads/a.png" in listing


def test_all_is_the_default_and_makes_both(env):
    e, out = env
    assert run(e).returncode == 0 and names(out) == ["db", "media"]
    out2 = out.parent / "out2"
    e2 = {**e, "BACKUP_DIR": str(out2)}
    assert run(e2, "--mode=all").returncode == 0 and names(out2) == ["db", "media"]


def test_keeps_only_the_last_14_of_each_kind(env):
    e, out = env
    out.mkdir()
    for i in range(20):  # 20 old backups of each kind
        for kind, suffix in (("db", ".sql.gz"), ("media", ".tar.gz")):
            p = out / f"{kind}-20260101-0000{i:02d}{suffix}"
            p.write_text("x")
            os.utime(p, (time.time() - 1000 + i, time.time() - 1000 + i))
    assert run(e, "--mode", "all").returncode == 0
    db = sorted(p.name for p in out.glob("db-*"))
    media = sorted(p.name for p in out.glob("media-*"))
    assert len(db) == 14 and len(media) == 14
    assert "db-20260101-000000.sql.gz" not in db  # the oldest were pruned
    assert any(not n.startswith("db-20260101") for n in db)  # today's backup was kept


def test_db_only_run_does_not_prune_media_backups(env):
    e, out = env
    out.mkdir()
    for i in range(16):
        (out / f"media-20260101-0000{i:02d}.tar.gz").write_text("x")
    assert run(e, "--mode", "db-only").returncode == 0
    assert len(list(out.glob("media-*"))) == 16


@pytest.mark.parametrize("args", [["--mode", "everything"], ["--mode"], ["--nope"]])
def test_bad_arguments_fail_without_creating_anything(env, args):
    e, out = env
    r = run(e, *args)
    assert r.returncode != 0 and not out.exists() or not any(out.iterdir())


def test_missing_database_url_and_missing_media_fail_clearly(env, tmp_path):
    e, out = env
    no_db = {k: v for k, v in e.items() if k != "DATABASE_URL"}
    assert run(no_db, "--mode", "db-only").returncode != 0
    assert "DATABASE_URL" in run(no_db, "--mode", "db-only").stderr
    assert run({**e, "MEDIA_DIR": str(tmp_path / "nope")}, "--mode", "media-only").returncode == 1
    assert run(no_db, "--mode", "media-only").returncode == 0  # media-only needs no database


def test_failed_dump_leaves_no_half_written_file(env, tmp_path):
    e, out = env
    bad = tmp_path / "bin" / "pg_dump"
    bad.write_text("#!/bin/sh\nexit 3\n")
    r = run(e, "--mode", "db-only")
    assert r.returncode != 0
    assert not list(out.glob("db-*.sql.gz"))  # never a final file
    assert not list(out.glob("*.partial"))  # and the half-written one is cleaned up
