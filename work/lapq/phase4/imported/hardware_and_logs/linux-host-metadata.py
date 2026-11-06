#!/usr/bin/env python3
import json
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
from datetime import datetime, timezone


def command(argv):
    result = subprocess.run(argv, capture_output=True, text=True)
    return {'argv': argv, 'returncode': result.returncode,
            'stdout': result.stdout, 'stderr': result.stderr}


def content(path):
    p = Path(path)
    return p.read_text() if p.is_file() else None


metadata = {
    'captured_utc': datetime.now(timezone.utc).isoformat(),
    'label': 'Windows 11 + WSL2 on the user-identified second physical laptop',
    'uname': list(platform.uname()), 'python': sys.version,
    'python_executable': sys.executable, 'uid': os.getuid(),
    'distro_environment': os.environ.get('WSL_DISTRO_NAME'),
    'os_release': content('/etc/os-release'),
    'kernel_version': content('/proc/version'),
    'cpuinfo': content('/proc/cpuinfo'), 'meminfo': content('/proc/meminfo'),
    'process_limits': content('/proc/self/limits'),
    'cgroup': content('/proc/self/cgroup'),
    'cgroup_limits': {name: content('/sys/fs/cgroup/' + name) for name in
                      ('cpu.max', 'memory.max', 'memory.swap.max', 'pids.max')},
    'visible_cpu_count': os.cpu_count(),
    'affinity': sorted(os.sched_getaffinity(0)), 'load_average': os.getloadavg(),
    'resource_limits': {name: resource.getrlimit(getattr(resource, name)) for name in
                        ('RLIMIT_AS', 'RLIMIT_CPU', 'RLIMIT_DATA', 'RLIMIT_STACK',
                         'RLIMIT_NPROC', 'RLIMIT_NOFILE', 'RLIMIT_MEMLOCK')},
    'thread_environment': {name: os.environ.get(name) for name in
                           ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS',
                            'PYTHONOPTIMIZE', 'PYTHONHASHSEED')},
    'commands': [command(argv) for argv in
                 (['lscpu'], ['g++', '--version'], ['ldd', '--version'],
                  ['df', '-T', '.'], ['free', '-b'], ['swapon', '--show', '--bytes'])],
    'runtime_conditions': 'Sequential workers; Linux ext4 study storage; no CPU affinity or frequency control; Windows/WSL background activity and thermals uncontrolled. Windows power observation is recorded separately. Explicit root WSL user for installation and research; no interactive user/password was created.',
}
Path(sys.argv[1]).write_text(json.dumps(metadata, indent=2)+'\n')
print(json.dumps({'output': sys.argv[1], 'distro': metadata['distro_environment'],
                  'kernel': platform.release(), 'python': platform.python_version(),
                  'visible_cpus': metadata['visible_cpu_count']}))
