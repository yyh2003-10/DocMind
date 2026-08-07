"""以分离进程方式运行命令，输出到日志，避免 bash 超时中断。

用法: python run_detached.py LOGFILE CMD [ARG...]
"""
import subprocess
import sys

logf = sys.argv[1]
cmd = sys.argv[2:]
log = open(logf, "w", encoding="utf-8")
p = subprocess.Popen(
    cmd,
    stdout=log,
    stderr=subprocess.STDOUT,
    stdin=subprocess.DEVNULL,
    creationflags=(
        subprocess.CREATE_NEW_PROCESS_GROUP
        | subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NO_WINDOW
    ),
    close_fds=True,
)
print("PID", p.pid)
