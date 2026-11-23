"""Test a minor-version upgrade and dump/restore using disposable, isolated data."""

import argparse
import hashlib
import json
import subprocess
import time
from uuid import uuid4

OLD_IMAGE = (
    "pgvector/pgvector:0.8.6-pg17-trixie@sha256:"
    "724a4041afdb1750446e3f6b5cfa8f3b0ac5a2cf538ddfa6bfee4f94c2fa85c6"
)


def docker(*args, data=None):
    return subprocess.run(
        ["docker", *args], input=data, capture_output=True, check=True, timeout=120
    ).stdout


def sql(name, query):
    return (
        docker(
            "exec",
            name,
            "psql",
            "-U",
            "travel",
            "-d",
            "acceptance",
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            query,
        )
        .decode()
        .strip()
    )


def run(image):
    prefix = "tsi-db-check-" + uuid4().hex[:12]
    containers, volumes = [], []

    def start(suffix, selected, volume):
        name = prefix + suffix
        containers.append(name)
        docker(
            "run",
            "-d",
            "--name",
            name,
            "--network",
            "none",
            "-v",
            volume + ":/var/lib/postgresql/data",
            "-e",
            "POSTGRES_USER=travel",
            "-e",
            "POSTGRES_DB=acceptance",
            "-e",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            selected,
        )
        for _ in range(60):
            try:
                sql(name, "SELECT 1")
                return name
            except subprocess.CalledProcessError:
                time.sleep(1)
        raise RuntimeError("isolated_database_not_ready")

    def verify(name):
        assert sql(name, "SELECT extversion FROM pg_extension WHERE extname='vector'") == "0.8.6"
        assert (
            sql(name, "SELECT label FROM samples ORDER BY embedding <-> '[1,0,0]' LIMIT 1")
            == "Bangkok"
        )
        assert sql(name, "SELECT count(*) FROM samples") == "2"
        assert sql(name, "SELECT count(*) FROM pg_indexes WHERE indexname='samples_hnsw'") == "1"
        assert (
            sql(
                name,
                "SELECT count(*) FROM pg_database WHERE datname=current_database() "
                "AND datcollversion IS DISTINCT FROM pg_database_collation_actual_version(oid)",
            )
            == "0"
        )
        return sql(name, "SHOW server_version")

    try:
        for suffix in ("-upgrade-data", "-restore-data"):
            volume = prefix + suffix
            docker("volume", "create", volume)
            volumes.append(volume)
        old = start("-old", OLD_IMAGE, volumes[0])
        sql(
            old,
            "CREATE EXTENSION vector; CREATE TABLE samples(label text, embedding vector(3)); "
            "INSERT INTO samples VALUES ('Bangkok','[1,0,0]'), ('Suva','[0,1,0]'); "
            "CREATE INDEX samples_hnsw ON samples USING hnsw(embedding vector_l2_ops)",
        )
        before = verify(old)
        dump = docker("exec", old, "pg_dump", "-U", "travel", "-Fc", "acceptance")
        docker("stop", old)
        upgraded = start("-upgraded", image, volumes[0])
        after = verify(upgraded)
        assert int(sql(upgraded, "SHOW server_version_num")) >= 170012
        restored = start("-restored", image, volumes[1])
        docker(
            "exec",
            "-i",
            restored,
            "pg_restore",
            "-U",
            "travel",
            "-d",
            "acceptance",
            "--exit-on-error",
            "--no-owner",
            data=dump,
        )
        assert verify(restored) == after
        print(
            json.dumps(
                {
                    "old_version": before,
                    "new_version": after,
                    "vector": "0.8.6",
                    "in_place_upgrade": "passed",
                    "dump_restore": "passed",
                    "vector_hnsw_and_collation": "passed",
                    "dump_sha256": hashlib.sha256(dump).hexdigest(),
                }
            )
        )
    finally:
        for name in reversed(containers):
            subprocess.run(["docker", "rm", "-fv", name], capture_output=True, check=False)
        for volume in volumes:
            subprocess.run(["docker", "volume", "rm", volume], capture_output=True, check=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Already-built patched database image")
    run(parser.parse_args().image)
