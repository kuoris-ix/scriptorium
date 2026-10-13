import pytest

from scriptorium import engines
from scriptorium.engines import Engine
from scriptorium.jobs import GB, Jobs, Machine, decide

PARALLEL = Engine("cores", "cores", "", parallel=True, memory=1.0)
ONE = Engine("one", "one", "")
BIG = Machine(cores=16, memory=64 * GB, free=48 * GB)


@pytest.mark.parametrize(
    ("engine", "machine", "expected"),
    [
        (ONE, BIG, Jobs(1, "auto: one reads fastest with one")),
        (PARALLEL, BIG, Jobs(12, "auto: 16 cores, 4 kept free")),
        (
            PARALLEL,
            Machine(cores=4, memory=16 * GB, free=12 * GB),
            Jobs(3, "auto: 4 cores, 1 kept free"),
        ),
        (
            PARALLEL,
            Machine(cores=2, memory=16 * GB, free=12 * GB),
            Jobs(1, "auto: 2 cores, fewer than 4"),
        ),
        (
            PARALLEL,
            Machine(cores=8, memory=6 * GB, free=4 * GB),
            Jobs(1, "auto: 6.0 GB of memory, less than 8.0 GB"),
        ),
        # What's free, less 4 GB (more than a fifth of 16 GB), is for 2 workers of 1 GB.
        (
            PARALLEL,
            Machine(cores=16, memory=16 * GB, free=6 * GB),
            Jobs(2, "auto: 16 cores, 4 kept free; 6.0 GB of memory free"),
        ),
        (
            PARALLEL,
            Machine(cores=16, memory=16 * GB, free=2 * GB),
            Jobs(1, "auto: 16 cores, 4 kept free; 2.0 GB of memory free"),
        ),
    ],
)
def test_auto_reads_with_as_many_workers_as_the_engine_and_the_machine_allow(
    engine: Engine, machine: Machine, expected: Jobs
) -> None:
    assert decide(None, engine, machine) == expected


@pytest.mark.parametrize(
    ("asked", "engine", "warning"),
    [
        (4, PARALLEL, None),
        (1, ONE, None),
        (2, ONE, "one reads no faster with more than one worker"),
        (20, PARALLEL, "more workers than the 16 cores: they read no faster"),
    ],
)
def test_the_workers_asked_for_are_had_and_too_many_told(
    asked: int, engine: Engine, warning: str | None
) -> None:
    assert decide(asked, engine, BIG) == Jobs(asked, "asked", warning)


def test_workers_asked_for_that_may_not_fit_in_memory_are_told() -> None:
    small = Machine(cores=16, memory=16 * GB, free=3 * GB)

    assert decide(4, PARALLEL, small).warning == (
        "4 workers may need 4.0 GB of memory; 3.0 GB is free"
    )


def test_only_the_engines_that_read_faster_with_several_workers_have_more() -> None:
    assert [e.name for e in engines.ENGINES if e.parallel] == ["windows-ocr", "tesseract"]


def test_the_machine_here_is_told() -> None:
    machine = Machine.here()

    assert machine.cores >= 1
    assert machine.memory >= machine.free > 0
