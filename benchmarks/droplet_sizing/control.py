"""Change only the disposable experiment's cgroup controls."""

from benchmarks.droplet_sizing import run


def controls(memory, cpu, cores):
    return run.command(
        [
            "docker",
            "run",
            "--rm",
            "--privileged",
            "--cgroupns=host",
            "alpine:3.22",
            "sh",
            "-c",
            f"echo {memory} > /sys/fs/cgroup/travel-scam-sizing-20261103/memory.max; "
            f"echo '{cpu} 100000' > /sys/fs/cgroup/travel-scam-sizing-20261103/cpu.max; "
            f"echo {cores} > /sys/fs/cgroup/travel-scam-sizing-20261103/cpuset.cpus",
        ]
    )
