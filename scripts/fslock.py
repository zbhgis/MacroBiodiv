"""跨进程互斥文件锁：管理台（admin.save_papers）与发布流水线子进程
（prepare_thumbs）对 meta/papers.json 的读-改-写串行。

admin 的 threading.DATA_LOCK 只覆盖本进程线程；prepare_thumbs 由流水线以
子进程运行，两个进程对同一文件的并发读-改-写必须靠文件锁互斥，否则慢的
整快照回写会静默吞掉先落的变更（lost update）。零第三方依赖：
Windows 用 msvcrt.locking，POSIX 用 fcntl.flock。"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parent.parent / "meta" / ".papers.lock"
DEFAULT_DEADLINE = 300.0     # prepare_thumbs 增量一轮约数十秒，等锁上限给足


@contextmanager
def exclusive(path: Path = LOCK_PATH, deadline: float = DEFAULT_DEADLINE):
    """阻塞获取排他锁直到 with 块结束；超时抛 TimeoutError（调用方转成
    任务失败并提示重试，绝不能无锁继续 —— 宁可失败也不丢更新）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
    locked = False
    try:
        end = time.time() + deadline
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.time() >= end:
                    raise TimeoutError(f"papers.json 文件锁等待超时（{deadline:.0f}s）：{path}") from None
                time.sleep(0.2)
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def replace(src: Path, dst: Path, attempts: int = 4, gap: float = 0.05) -> None:
    """os.replace 带短重试：Windows 下无 FILE_SHARE_DELETE，目标恰被某个
    无锁读者（/api/items、weekly_issues 等毫秒级读）持有时会抛 PermissionError
    —— 窗口毫秒级，重试几次即过；原子性由「tmp 写完才 replace」保证，不受影响。"""
    for i in range(attempts):
        try:
            src.replace(dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(gap)
