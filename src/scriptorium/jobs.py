"""How many workers an engine reads with at once: as asked, or as the machine allows ("auto").

Only some engines read faster with several workers (`Engine.parallel`: Tesseract, Windows' OCR
and Apple Vision, each worker using about a core); the others use every core with one (models
run with ONNX Runtime or PyTorch on the CPU), or the GPU, and read no faster, or slower, with
more. With "auto", a parallel engine gets a worker for each physical core but some kept free
for the rest of the machine, as many as the memory free allows, and none more on a machine with
few cores or little memory. The workers run at a low priority (see `worker`), so what else runs
comes first.
"""

import os
from dataclasses import dataclass

import psutil

from scriptorium.engines import Engine

GB = 1024**3
MIN_CORES = 4  # fewer physical cores: one worker
MIN_MEMORY = 8 * GB  # less memory in all: one worker
MEMORY_KEPT = 4 * GB  # memory left free, at least (and a fifth of it all)


@dataclass(frozen=True)
class Machine:
    """What the workers may use."""

    cores: int  # physical cores this process may use
    memory: int  # bytes in all
    free: int  # bytes free now

    @classmethod
    def here(cls) -> Machine:
        logical = os.process_cpu_count() or 1  # those this process may use
        physical = psutil.cpu_count(logical=False) or logical
        # As many physical cores as the logical ones allowed, where a core runs two threads.
        threads_per_core = max((psutil.cpu_count() or logical) // physical, 1)
        memory = psutil.virtual_memory()
        return cls(
            cores=max(min(physical, logical // threads_per_core), 1),
            memory=memory.total,
            free=memory.available,
        )


@dataclass(frozen=True)
class Jobs:
    """The workers to read with, and why so many, as shown (e.g. "auto: 16 cores, 4 kept
    free"); and a warning if asked for more than is any use."""

    count: int
    why: str
    warning: str | None = None


def decide(asked: int | None, engine: Engine, machine: Machine) -> Jobs:
    """The workers `engine` reads with: `asked`, or as `machine` allows if None."""
    if asked is not None:
        return Jobs(asked, "asked", _warning(asked, engine, machine))
    if not engine.parallel:
        return Jobs(1, f"auto: {engine.name} reads fastest with one")
    if machine.cores < MIN_CORES:
        return Jobs(1, f"auto: {machine.cores} cores, fewer than {MIN_CORES}")
    if machine.memory < MIN_MEMORY:
        return Jobs(1, f"auto: {_gb(machine.memory)} of memory, less than {_gb(MIN_MEMORY)}")
    kept = max(1, machine.cores // 4)
    count, why = machine.cores - kept, f"auto: {machine.cores} cores, {kept} kept free"
    for_memory = int((machine.free - max(MEMORY_KEPT, machine.memory // 5)) // (engine.memory * GB))
    if for_memory < count:
        count, why = max(for_memory, 1), f"{why}; {_gb(machine.free)} of memory free"
    return Jobs(count, why)


def _warning(asked: int, engine: Engine, machine: Machine) -> str | None:
    if asked > 1 and not engine.parallel:
        return f"{engine.name} reads no faster with more than one worker"
    if asked > machine.cores:
        return f"more workers than the {machine.cores} cores: they read no faster"
    needed = asked * engine.memory * GB
    if needed > machine.free:
        return f"{asked} workers may need {_gb(needed)} of memory; {_gb(machine.free)} is free"
    return None


def _gb(size: float) -> str:
    return f"{size / GB:.1f} GB"
