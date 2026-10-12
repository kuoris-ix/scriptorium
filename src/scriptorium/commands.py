"""The `ocr` commands: reading scanned pages, looking at what was read, choosing engines, tidying.

`make_app` puts them together as a Typer app, for the program using them to add
as its own `ocr` command; they read no settings of their own, but take them
from the `OcrConfig` it gives (read again for each command run). The help and
messages name the program's commands and settings as it tells (`ProgramNames`): they
are written with "{command}", "{settings}" and "{password}" in them, filled in
by `make_app`.
"""

import functools
import inspect
import io
import platform
import sys
import tempfile
from collections.abc import Callable
from copy import copy
from dataclasses import dataclass, replace
from datetime import date
from itertools import combinations
from pathlib import Path
from typing import Any

import typer
from PIL import ImageFont
from typer.models import ParameterInfo

from scriptorium import (
    check,
    cleanup,
    engines,
    preprocess,
    raster,
    runner,
    samples,
    split,
    worker,
)
from scriptorium.compare import EngineScore, markdown, score_engine
from scriptorium.config import OcrConfig, ProgramNames
from scriptorium.diff import Agreement, Count, groups
from scriptorium.dump import FileRead, write_dump
from scriptorium.engines import Engine
from scriptorium.files import absolute_path, longest_path_allowed
from scriptorium.models import Device, StoredPage
from scriptorium.runner import Target
from scriptorium.store import RunKey, Store

ALL = "all"
ConfigOf = Callable[[], OcrConfig]


@dataclass(frozen=True)
class _Program:
    """What's in `ctx.obj` for the commands (see `make_app`)."""

    config_of: ConfigOf
    names: ProgramNames


def _engine_names(incomplete: str) -> list[str]:
    return [e.name for e in engines.ENGINES if e.name.startswith(incomplete)]


def _engine_names_or_all(incomplete: str) -> list[str]:
    return [n for n in [*(e.name for e in engines.ENGINES), ALL] if n.startswith(incomplete)]


def _engines_option(default: str) -> Any:
    return typer.Option(
        None,
        "--engine",
        "-e",
        metavar="ENGINE",
        help=f"エンジン（繰り返して複数）か all。{default}",
        autocompletion=_engine_names_or_all,
        show_default=False,
    )


PATHS = typer.Argument(
    ...,
    metavar="PATH...",
    exists=True,
    readable=True,
    help="PDF・画像か、その下を全部読むフォルダ",
    show_default=False,
)
ENGINES_OF_SETTINGS = _engines_option("省略すると {settings}ENGINES")
EVERY_ENGINE = _engines_option("省略すると全エンジン")
ORIENT = typer.Option(
    None,
    "--orient",
    metavar="ENGINE",
    help="ページの向きを判定して正すエンジン（tesseract）。省略すると {settings}ORIENT",
    autocompletion=_engine_names,
    show_default=False,
)
PREPROCESS = typer.Option(
    None,
    "--preprocess",
    metavar="STEPS",
    help=f"読む前の処理（{'・'.join(preprocess.STEPS)}）をカンマ区切りで。"
    "省略すると {settings}PREPROCESS",
    show_default=False,
)
PASSWORD = typer.Option(
    None,
    envvar="{password}",
    help="保護された PDF のパスワード（履歴に残らないよう環境変数で渡すとよい）",
)
APPLY = typer.Option(False, "--apply", help="実際に消す（省略すると一覧するだけ）")
OUTPUT = typer.Option(
    ..., "--output", "-o", dir_okay=False, help="出力するファイル（.json か .csv）"
)
DEVICE = typer.Option(
    None,
    help="エンジンを動かす装置（auto は CUDA・MPS・CPU の順）。省略すると {settings}DEVICE",
    show_default=False,
)


def _config(ctx: typer.Context) -> OcrConfig:
    """The settings, as the program gives them now; the workers' folder as an absolute path
    without "..", for messages to show it so."""
    program: _Program = ctx.obj
    config = program.config_of()
    return replace(config, workers_dir=absolute_path(config.workers_dir))


def _workers(config: OcrConfig) -> str:
    """The workers' folder, as shown: its path, and where it's from."""
    return f"{config.workers_dir} ({config.origin('workers_dir')})"


def _naming(ctx: typer.Context) -> ProgramNames:
    """How the program names the commands and settings."""
    program: _Program = ctx.obj
    return program.names


def _named(
    names: list[str] | None,
    config: OcrConfig,
    naming: ProgramNames,
    *,
    all_if_none: bool,
    ready_only: bool = False,
) -> tuple[list[Engine], str]:
    """The engines named, else those of the settings (else all of them, or an error).

    "all" names every engine; with `ready_only`, every one that's installed and runs here
    (the others are told). Also returns them as shown: their names, and where they're from.
    """
    origin = "--engine"
    if not names:
        names = [n.strip() for n in config.engines.split(",") if n.strip()]
        origin = config.origin("engines")
    if not names:
        if all_if_none:
            return list(engines.ENGINES), "every engine (default)"
        raise typer.BadParameter(
            naming.fill(
                "name one with --engine (or all), or set {settings}ENGINES "
                "(`{command} list-engines` lists them)"
            ),
            param_hint="--engine",
        )
    if names == [ALL]:
        chosen = _ready(config, naming) if ready_only else list(engines.ENGINES)
    else:
        try:
            chosen = [engines.find(name) for name in names]
        except KeyError as e:
            raise typer.BadParameter(str(e.args[0]), param_hint="--engine") from e
    return chosen, f"{', '.join(e.name for e in chosen)} ({origin})"


def _ready(config: OcrConfig, naming: ProgramNames) -> list[Engine]:
    """Every engine that runs here and is installed; the others are told."""
    ready = []
    for engine in engines.ENGINES:
        reason = engine.unavailable(config.workers_dir)
        if reason is None and not engine.installed(config.workers_dir):
            folder = engine.folder(config.workers_dir)
            reason = f"not installed (uv sync --project {folder})"
        if reason is None:
            ready.append(engine)
        else:
            typer.echo(f"leaving out {engine.name}: {reason}")
    if not ready:
        raise typer.BadParameter(
            naming.fill("no engine is installed here (`{command} list-engines` tells how)"),
            param_hint="--engine",
        )
    return ready


def _targets(
    paths: list[Path], config: OcrConfig, password: str | None
) -> tuple[list[Target], int]:
    """The files under `paths` and their pages; those that can't be opened are counted and told."""
    found, failed = runner.plan(
        runner.find(paths),
        password=password,
        min_dpi=config.min_dpi,
        max_dpi=config.max_dpi,
    )
    for name, reason in failed.items():
        typer.echo(f"NG {name}: {reason}")
    return found, len(failed)


def _orient(name: str | None, config: OcrConfig) -> tuple[Engine | None, str]:
    """The engine to tell how pages are turned (the one named, the settings', or none), as shown."""
    origin = "--orient"
    if name is None:
        name, origin = config.orient, config.origin("orient")
    if not name:
        return None, f"none ({origin})"
    try:
        return engines.find(name), f"{name} ({origin})"
    except KeyError as e:
        raise typer.BadParameter(str(e.args[0]), param_hint="--orient") from e


def _steps(names: str | None, config: OcrConfig) -> tuple[tuple[str, ...], str]:
    """The preprocessing steps named (comma-separated), or the settings', as shown."""
    origin = "--preprocess"
    if names is None:
        names, origin = config.preprocess, config.origin("preprocess")
    steps = tuple(n.strip() for n in names.split(",") if n.strip())
    try:
        preprocess.steps(steps)
    except KeyError as e:
        raise typer.BadParameter(str(e.args[0]), param_hint="--preprocess") from e
    return steps, f"{','.join(steps) or 'none'} ({origin})"


def _device(device: Device | None, config: OcrConfig) -> tuple[str, str]:
    """The device engines run on, as shown."""
    if device is not None:
        return device, f"{device} (--device)"
    return config.device, f"{config.device} ({config.origin('device')})"


def _key(
    engine: Engine, config: OcrConfig, orient: Engine | None, steps: tuple[str, ...]
) -> RunKey:
    return RunKey.of(
        engine,
        min_dpi=config.min_dpi,
        max_dpi=config.max_dpi,
        orient=orient.name if orient else None,
        orient_min_confidence=config.orient_min_confidence,
        preprocess=steps,
    )


def _otherwise(run: dict[str, Any], key: RunKey) -> str:
    """How the RunKey `run` (as kept) differs from `key`: what the pages were read with instead."""
    parts = []
    if run.get("orient") != key.orient:
        parts.append(f"orient {run.get('orient') or 'none'}")
    steps = tuple(run.get("preprocess", ()))
    if steps != key.preprocess:
        parts.append(f"preprocess {','.join(steps) or 'none'}")
    if (run.get("min_dpi"), run.get("max_dpi")) != (key.min_dpi, key.max_dpi):
        parts.append(f"{run.get('min_dpi')}-{run.get('max_dpi')} dpi")
    return ", ".join(parts) or "an earlier version of the engine's options"


def _tell_read_otherwise(store: Store, targets: list[Target], keys: dict[str, RunKey]) -> None:
    """Tell the settings the files were read with, by each engine, when not with `keys`.

    Pages are kept apart by the settings they were read with, so what was read
    otherwise looks unread: this tells why, and how to see it.
    """
    for name, key in keys.items():
        found: dict[str, int] = {}
        for target in targets:
            if store.done(target.sha256, key):
                continue
            for how in {_otherwise(run, key) for run in store.other_runs(target.sha256, key)}:
                found[how] = found.get(how, 0) + 1
        for how, count in sorted(found.items()):
            typer.echo(f"{name}: {count} file(s) read with other settings only ({how})", err=True)


def _check_path_lengths(store: Store, keys: list[RunKey], naming: ProgramNames) -> None:
    """Stop before reading if what's read couldn't be kept: its paths too long for Windows."""
    allowed = longest_path_allowed()
    if allowed is None:
        return
    longest = max(store.longest_path(key) for key in keys)
    if longest <= allowed:
        return
    root = absolute_path(store.root)
    typer.echo(
        f"Error: the folder what's read is kept in is too deep: {root} "
        f"({len(str(root))} characters). The paths in it would be up to {longest} characters "
        f"long, and Windows takes {allowed} unless long paths are enabled. "
        + naming.fill("Set {settings}DIR to a folder of at most ")
        + f"{len(str(root)) - (longest - allowed)} characters, or enable long paths in Windows "
        "(LongPathsEnabled).",
        err=True,
    )
    raise typer.Exit(2)


def run(
    ctx: typer.Context,
    paths: list[Path] = PATHS,
    engine_names: list[str] | None = ENGINES_OF_SETTINGS,
    device: Device | None = DEVICE,
    force: bool = typer.Option(
        False, "--force", help="文字のあるページも読む（省略するとスキャンのページだけ）"
    ),
    reread: bool = typer.Option(False, "--reread", help="読んだページも読み直す"),
    orient_name: str | None = ORIENT,
    steps_names: str | None = PREPROCESS,
    password: str | None = PASSWORD,
) -> None:
    """スキャンのページを OCR で読み、読んだ結果を保存する（{settings}DIR）。

    指定した PDF・画像と、指定したフォルダの下にあるものの、スキャンの
    ページを全部、エンジンごとに順に読む。使うエンジン・装置・向きの判定・
    読む前の処理・worker のフォルダと、その出どころを最初に表示する。

    読んだページは読み直さないので、止まった（失敗した）ときは、もう一度
    実行すれば続きから読む。時間がかかるので、裏で動かしておいてよい。
    読んだファイルの場所（絶対パス）も記録する（{command} remove-missing が使う）。
    読めなかったページがあれば終了コード 1 で終わる。保存先のパスが Windows
    で長すぎる（長いパスを有効にしていなければ 259 文字まで）ときは、読む
    前に終了コード 2 で止まる。
    """
    config = _config(ctx)
    chosen, engines_shown = _named(
        engine_names, config, _naming(ctx), all_if_none=False, ready_only=True
    )
    device_name, device_shown = _device(device, config)
    orient, orient_shown = _orient(orient_name, config)
    steps, steps_shown = _steps(steps_names, config)
    if orient is not None and (reason := orient.unavailable(config.workers_dir)):
        raise typer.BadParameter(f"{orient.name} {reason}", param_hint="--orient")
    typer.echo(
        f"Using: engines {engines_shown}; device {device_shown}; orient {orient_shown}; "
        f"preprocess {steps_shown}; workers {_workers(config)}"
    )
    store = Store(config.ocr_dir)
    _check_path_lengths(store, [_key(e, config, orient, steps) for e in chosen], _naming(ctx))
    found, problems = _targets(paths, config, password)
    pages = sum(len(t.to_read(force=force)) for t in found)
    typer.echo(f"{len(found)} file(s), {pages} page(s) to read by OCR")
    for engine in chosen:
        typer.echo(f"== {engine.name}")
        reason = engine.unavailable(config.workers_dir)
        if reason is not None:
            typer.echo(f"skipped: {reason}")
            problems += 1
            continue
        outcome = runner.run(
            found,
            engine,
            store=store,
            workers_dir=config.workers_dir,
            launcher=worker.uv_command,
            device=device_name,
            min_dpi=config.min_dpi,
            max_dpi=config.max_dpi,
            password=password,
            force=force,
            reread=reread,
            orient=orient,
            orient_min_confidence=config.orient_min_confidence,
            steps=steps,
            worker_pages=config.worker_pages,
            say=typer.echo,
        )
        if outcome.log is None:
            typer.echo("nothing left to read")
            continue
        summary = f"{outcome.read} page(s) read, {outcome.failed} failed"
        if outcome.left:
            summary += f", {outcome.left} left ({outcome.error})"
        typer.echo(f"{summary}; log: {outcome.log}")
        problems += outcome.failed + outcome.left
    if problems:
        raise typer.Exit(1)


def status(
    ctx: typer.Context,
    paths: list[Path] = PATHS,
    engine_names: list[str] | None = EVERY_ENGINE,
    orient_name: str | None = ORIENT,
    steps_names: str | None = PREPROCESS,
    password: str | None = PASSWORD,
) -> None:
    """どのページを OCR で読んだかを、エンジンごとに表示する。

    読んだ結果は設定（向きの判定・読む前の処理・解像度）ごとに分けて保存
    してあり、ここで使う設定で読んだものを数える。別の設定でだけ読んだ
    エンジンは、その設定を示す。
    """
    config = _config(ctx)
    chosen, engines_shown = _named(engine_names, config, _naming(ctx), all_if_none=True)
    orient, orient_shown = _orient(orient_name, config)
    steps, steps_shown = _steps(steps_names, config)
    typer.echo(f"Using: engines {engines_shown}; orient {orient_shown}; preprocess {steps_shown}")
    store = Store(config.ocr_dir)
    for target in _targets(paths, config, password)[0]:
        to_read = {p.page for p in target.to_read(force=False)}
        typer.echo(f"{target.name}: {len(target.pages)} page(s), {len(to_read)} to read by OCR")
        for engine in chosen:
            key = _key(engine, config, orient, steps)
            done = store.done(target.sha256, key)
            failed = store.failed(target.sha256, key)
            if not done and not failed:
                others = {_otherwise(run, key) for run in store.other_runs(target.sha256, key)}
                if others:
                    how = "; ".join(sorted(others))
                    typer.echo(f"    {engine.name}: read with other settings only ({how})")
                continue
            line = f"    {engine.name}: {len(done & to_read)}/{len(to_read)} read"
            if done - to_read:
                line += f" (and {len(done - to_read)} with text)"
            typer.echo(line)
            for page, error in sorted(failed.items()):
                typer.echo(f"        p{page} FAILED: {error}")


def list_engines(ctx: typer.Context) -> None:
    """OCR のエンジンを一覧し、それぞれがこの環境で使えるかを示す。

    入っていないエンジンには、入れ方を示す。入っているエンジンが実際に
    動くかは check-engines で確かめる。worker のフォルダとその出どころを
    最初に表示する。
    """
    config = _config(ctx)
    typer.echo(f"Using: workers {_workers(config)}")
    for engine in engines.ENGINES:
        typer.echo(f"{engine.name:<14} {_availability(engine, config)}")
        typer.echo(f"{'':<14} {engine.description}")


def _availability(engine: Engine, config: OcrConfig) -> str:
    """ "ready", or why the engine can't be used here."""
    reason = engine.unavailable(config.workers_dir)
    if reason is not None:
        return reason
    if not engine.installed(config.workers_dir):
        folder = engine.folder(config.workers_dir)
        return f"not installed yet (uv sync --project {folder})"
    return "ready"


def check_engines(ctx: typer.Context, device: Device | None = DEVICE) -> None:
    """入っているエンジンが動くかを、架空の書類を 1 枚読ませて確かめる。

    入っているエンジンを 1 つずつ起動して、使う側のプログラムが渡す架空の
    書類を読ませ、エンジンが名乗ったもの（版・モデル・装置）、起動と
    読み取りにかかった時間、書類の金額をいくつ正しく読めたか、ページの
    向きを判定できるか（--orient に使えるか）を示す。使う装置・worker の
    フォルダとその出どころを最初に表示する。失敗したエンジンがあれば
    終了コード 1 で終わる。
    """
    config = _config(ctx)
    device_name, device_shown = _device(device, config)
    typer.echo(f"Using: device {device_shown}; workers {_workers(config)}")
    store = Store(config.ocr_dir)
    failed = 0
    with tempfile.TemporaryDirectory(dir=store.scratch()) as scratch:
        sample = None
        for engine in engines.ENGINES:
            reason = _availability(engine, config)
            if reason == "ready":
                if sample is None:
                    font = samples.find_font()
                    loader = samples.font_loader(font) if font else ImageFont.load_default
                    document = config.check_document
                    sample = check.sample_page(Path(scratch), loader, document)
                image, truth = sample
                log = store.new_log(f"{engine.name}-check")
                with log.open("a", encoding="utf-8") as stream:
                    try:
                        command = worker.uv_command(engine, config.workers_dir)
                    except worker.WorkerFailed as e:
                        checked = check.Checked(None, f"couldn't start: {e}")
                    else:
                        checked = check.check(
                            engine,
                            command,
                            device=device_name,
                            image=image,
                            truth=truth,
                            log=stream,
                        )
                if checked.error is not None:
                    failed += 1
                    reason = f"FAILED: {checked.error} (log: {log})"
                else:
                    reason = _checked(checked)
            typer.echo(f"{engine.name:<14} {reason}")
            typer.echo(f"{'':<14} {engine.description}")
    if failed:
        raise typer.Exit(1)


def _checked(checked: check.Checked) -> str:
    info = checked.engine
    assert info is not None
    right, of = checked.amounts
    turns = "tells how pages are turned" if checked.tells_turns else "doesn't tell turns"
    return (
        f"works: {info.name} {info.version} ({info.model or 'its default model'}) on "
        f"{info.device or '?'}; started in {checked.start_seconds:.1f}s, read a fictional "
        f"document in {checked.read_seconds:.1f}s ({right} of {of} amounts right); {turns}"
    )


def dump_text(
    ctx: typer.Context,
    paths: list[Path] = PATHS,
    output: Path = OUTPUT,
    engine_name: str | None = typer.Option(
        None,
        "--engine",
        "-e",
        metavar="ENGINE",
        help="読んだ結果を書き出すエンジン。省略すると {settings}ENGINES の最初のもの",
        autocompletion=_engine_names,
        show_default=False,
    ),
    orient_name: str | None = ORIENT,
    steps_names: str | None = PREPROCESS,
    password: str | None = PASSWORD,
) -> None:
    """OCR で読んだ文字と位置を、1 つのファイルに書き出す。

    形は PDF の文字の書き出し（scriptorium.pdf）と同じ。読んだページ
    （{command} run）だけを書き出す。書き出したものは書類の文字なので、人に
    見せる前に、名前などの個人の情報を消したか確かめる。
    """
    if output.suffix.lower() not in {".json", ".csv"}:
        raise typer.BadParameter("must end with .json or .csv", param_hint="--output")
    config = _config(ctx)
    if engine_name == ALL:
        raise typer.BadParameter("name one engine", param_hint="--engine")
    chosen, engine_shown = _named(
        [engine_name] if engine_name else None, config, _naming(ctx), all_if_none=False
    )
    engine = chosen[0]
    orient, orient_shown = _orient(orient_name, config)
    steps, steps_shown = _steps(steps_names, config)
    typer.echo(f"Using: engine {engine_shown}; orient {orient_shown}; preprocess {steps_shown}")
    key = _key(engine, config, orient, steps)
    store = Store(config.ocr_dir)
    targets = _targets(paths, config, password)[0]
    files = []
    for target in targets:
        pages = [store.load(target.sha256, key, p.page) for p in target.pages]
        read = [p for p in pages if p is not None]
        if read:
            files.append(FileRead(file=target.name, engine=engine.name, pages=read))
    if not files:
        typer.echo(f"Nothing read with {engine.name} under {', '.join(map(str, paths))}", err=True)
        _tell_read_otherwise(store, targets, {engine.name: key})
        raise typer.Exit(1)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_dump(files, output)
    words = sum(len(p.page.words) for f in files for p in f.pages)
    pages_count = sum(len(f.pages) for f in files)
    typer.echo(
        f"{words} piece(s) of text from {pages_count} page(s) of {len(files)} file(s) "
        f"written to {output}"
    )


def split_scans(
    ctx: typer.Context,
    paths: list[Path] = PATHS,
    output: Path = typer.Option(
        ..., "--output", "-o", file_okay=False, help="切り出した画像を書き出すフォルダ"
    ),
    password: str | None = PASSWORD,
) -> None:
    """スキャンしたページから、書類を 1 枚ずつ画像に切り出す。

    フラットヘッドスキャナーでまとめて読み取ったページや、空いた所の
    多いページから、余白で隔てられた書類を探し、1 枚ずつ傾きを直して
    PNG（ページと同じ解像度）で書き出す。名前は「元の名前_p<ページ>-
    <番号>.png」で、番号は上の行から、左から右の順。文字のあるページは
    書き出さない。同じ名前のファイルがあれば書き換えない。

    見つけた書類の大きさ・位置・傾きを表示する。切り出した画像を開いて
    確かめてから {command} run で読む。書類の見つからないページや、開けない
    ファイルがあれば終了コード 1 で終わる。
    """
    config = _config(ctx)
    found, problems = _targets(paths, config, password)
    written = 0
    for target in found:
        for page in target.to_read(force=False):
            image = raster.render(target.path, page, password=password)
            boxes = split.find(image, page.dpi)
            typer.echo(f"{target.name} p{page.page}: {len(boxes)} document(s)")
            if not boxes:
                problems += 1
            stem = Path(target.name).with_suffix("").as_posix()
            for number, box in enumerate(boxes, start=1):
                path = output / f"{stem}_p{page.page}-{number}.png"
                document = split.cut(image, box)
                line = f"    {number}: {_where(box, page.dpi)}, {_straightened(document.skew)}"
                if path.exists():
                    typer.echo(f"{line}; {path.name} exists, kept")
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                document.image.save(path, dpi=(page.dpi, page.dpi))
                written += 1
                typer.echo(f"{line} -> {path.name}")
    typer.echo(f"{written} image(s) written to {output}")
    if problems:
        raise typer.Exit(1)


def _where(box: split.Box, dpi: float) -> str:
    """A box on a page, in millimetres: its size and where its top left corner is."""
    x0, y0, x1, y1 = (round(v / dpi * 25.4) for v in box)
    return f"{x1 - x0}x{y1 - y0} mm at ({x0}, {y0}) mm"


def _straightened(skew: float) -> str:
    return f"turned {skew:+.2f} degrees" if skew else "level"


def make_samples(
    ctx: typer.Context,
    folder: Path = typer.Argument(
        ..., metavar="DIR", file_okay=False, help="書き出すフォルダ", show_default=False
    ),
    count: int = typer.Option(2, min=1, help="書類の種類ごとの数"),
    seed: int = typer.Option(1, help="乱数の種（同じ種なら同じ書類になる）"),
    font: Path | None = typer.Option(
        None,
        exists=True,
        dir_okay=False,
        help="日本語のフォント（省略するとシステムのものを探す）",
        show_default=False,
    ),
) -> None:
    """エンジンを比べるための架空の書類を、正解と一緒に書き出す。

    使う側のプログラムが渡す書類（レシート・明細など）のページを、架空の
    値で、スキャンらしく劣化させて作る（解像度の高低・傾き・回転・JPEG・
    ノイズ・ぼけ・影・白黒・PDF）。それぞれに正解（<ファイル名>.truth.json）を付ける。
    {command} run で読み、{command} score-engines で採点する。
    """
    font = font or samples.find_font()
    if font is None:
        raise typer.BadParameter("no Japanese font found; give one", param_hint="--font")
    written = 0
    loader = samples.font_loader(font)
    for path in samples.write(folder, loader, _config(ctx).documents, seed=seed, count=count):
        written += 1
        typer.echo(f"  {path.name}")
    typer.echo(f"{written} sample(s) written to {folder} (font: {font})")


def _percent(exact: int, count: int) -> str:
    return f"{exact}/{count} ({exact / count:.1%})" if count else "-"


def score_engines(
    ctx: typer.Context,
    paths: list[Path] = PATHS,
    engine_names: list[str] | None = EVERY_ENGINE,
    shown: int = typer.Option(20, "--show", help="読み違えた金額・日付を一覧する数"),
    orient_name: str | None = ORIENT,
    steps_names: str | None = PREPROCESS,
    as_markdown: bool = typer.Option(False, "--markdown", help="比べた表を Markdown で出す"),
    agreement: bool = typer.Option(
        False, "--agreement", help="2 つずつ組にして、読み違いが差分に出るか数える"
    ),
    by_digits: bool = typer.Option(
        False, "--digits", help="--agreement で、数字と符号だけで比べる"
    ),
) -> None:
    """架空の書類（{command} make-samples）を、エンジンがどれだけ正しく読めたか採点する。

    エンジンごと（省略すると読んだことのある全エンジン）に、金額・日付を
    正しく読めた割合、文字の誤り率、読み落とした行、何も無い所に読んだ
    文字を、劣化のさせ方ごとに示し、読み違えた金額・日付を一覧する。
    正解（<ファイル名>.truth.json）のある書類だけを採点する。

    --agreement では、エンジンを 2 つずつ組にして、両方が読んだ書類の
    金額・日付を数える。片方だけが読み違えたもの・両方が違う読み違いを
    したものは、読み方が食い違うので diff-engines に出る。両方が同じに
    読み違えたものは出ない（突き合わせでは見つからない）。
    """
    config = _config(ctx)
    chosen, engines_shown = _named(engine_names, config, _naming(ctx), all_if_none=True)
    orient, orient_shown = _orient(orient_name, config)
    steps, steps_shown = _steps(steps_names, config)
    if not as_markdown:
        typer.echo(
            f"Using: engines {engines_shown}; orient {orient_shown}; preprocess {steps_shown}"
        )
    store = Store(config.ocr_dir)
    found = []
    for target in _targets(paths, config, None)[0]:
        truth = samples.load_truth(target.path)
        if truth is not None:
            found.append((target, truth))
    if not found:
        typer.echo("No samples with a truth (<file>.truth.json) found", err=True)
        raise typer.Exit(1)
    named = [(t.name, truth) for t, truth in found]
    keys = {engine.name: _key(engine, config, orient, steps) for engine in chosen}
    if agreement:
        _agreements(store, found, keys, by_digits, as_markdown, _naming(ctx))
        return
    scores = []
    for name, key in keys.items():

        def load(index: int, key: RunKey = key) -> StoredPage | None:
            return store.load(found[index][0].sha256, key, 1)

        score = score_engine(name, named, load)
        if score.total.pages:
            scores.append(score)
    if not scores:
        names = ", ".join(e.name for e in chosen)
        typer.echo(f"No sample read yet with {names} ({_naming(ctx).command} run)", err=True)
        _tell_read_otherwise(store, [t for t, _ in found], keys)
        raise typer.Exit(1)
    done_first = ([f"turned upright by {orient.name}"] if orient else []) + list(steps)
    if as_markdown:
        settings_line = ", ".join(
            ([f"--orient {orient.name}"] if orient else [])
            + ([f"--preprocess {','.join(steps)}"] if steps else [])
        )
        environment = f"{platform.platform()}, {date.today().isoformat()}"
        typer.echo(markdown(scores, settings_line or "なし", environment), nl=False)
        return
    for score in scores:
        _echo_score(score, done_first, shown)


def _agreements(
    store: Store,
    found: list[tuple[Target, samples.Truth]],
    keys: dict[str, RunKey],
    by_digits: bool,
    as_markdown: bool,
    naming: ProgramNames,
) -> None:
    """Count, for each pair of engines, how their readings of the samples agree."""
    pairs = []
    for (name_a, key_a), (name_b, key_b) in combinations(keys.items(), 2):
        tally = Agreement(digits=by_digits)
        for target, truth in found:
            a = store.load(target.sha256, key_a, 1)
            b = store.load(target.sha256, key_b, 1)
            if a is not None and b is not None:
                tally.add(truth, a.page, b.page)
        if tally.pages:
            pairs.append((name_a, name_b, tally))
    if not pairs:
        names = ", ".join(keys)
        typer.echo(f"No sample read yet by two of {names} ({naming.command} run)", err=True)
        raise typer.Exit(1)
    if as_markdown:
        typer.echo(_agreement_markdown(pairs), nl=False)
        return
    compared = " (compared by digits)" if by_digits else ""
    for name_a, name_b, tally in pairs:
        typer.echo(f"== {name_a} and {name_b}: {tally.pages} sample(s) read by both{compared}")
        for kind, label in (("amount", "amounts"), ("date", "dates")):
            typer.echo(
                f"{label} {tally.count(kind)}: both right {tally.count(kind, wrong='none')}; "
                f"wrong by {name_a} only {tally.count(kind, wrong='a')}, "
                f"by {name_b} only {tally.count(kind, wrong='b')}; "
                f"both wrong {tally.count(kind, wrong='both', shown=True)} differently, "
                f"{tally.count(kind, wrong='both', shown=False)} alike (not shown by diff-engines)"
            )


def _agreement_markdown(pairs: list[tuple[str, str, Agreement]]) -> str:
    rows = [
        "| エンジン A | エンジン B | 種類 | 数 | 両方正しい | A だけ誤り | B だけ誤り "
        "| 両方誤り（違う読み） | 両方誤り（同じ読み。差分に出ない） |",
        "|---|---|---|--:|--:|--:|--:|--:|--:|",
    ]
    for name_a, name_b, tally in pairs:
        for kind, label in (("amount", "金額"), ("date", "日付")):
            counts = [
                tally.count(kind),
                tally.count(kind, wrong="none"),
                tally.count(kind, wrong="a"),
                tally.count(kind, wrong="b"),
                tally.count(kind, wrong="both", shown=True),
                tally.count(kind, wrong="both", shown=False),
            ]
            cells = " | ".join(str(n) for n in counts)
            rows.append(f"| {name_a} | {name_b} | {label} | {cells} |")
    return "".join(row + "\n" for row in rows)


def _echo_score(score: EngineScore, done_first: list[str], shown: int) -> None:
    total = score.total
    seconds = score.seconds_a_page
    timing = f", {seconds:.1f}s a page" if seconds is not None else ""
    upright = f" ({', '.join(done_first)})" if done_first else ""
    typer.echo(
        f"== {score.engine}{upright}: {total.pages} sample(s) scored, "
        f"{score.unread} not read yet{timing}"
    )
    typer.echo(
        f"amounts {_percent(total.exact_by_kind['amount'], total.by_kind['amount'])}, "
        f"dates {_percent(total.exact_by_kind['date'], total.by_kind['date'])} read exactly; "
        f"amounts {_percent(total.amounts_by_value, total.by_kind['amount'])} by value; "
        f"character errors {total.character_error_rate:.1%}; "
        f"{total.missed} of {total.lines} line(s) missed; {total.extra} piece(s) read elsewhere"
    )
    typer.echo(f"    {'variant':<12} {'amounts':>8} {'dates':>8} {'char err':>8} {'missed':>7}")
    for name, tally in sorted(score.by_variant.items()):
        amounts, dates = tally.exact_rate("amount"), tally.exact_rate("date")
        typer.echo(
            f"    {name:<12} {_rate(amounts):>8} {_rate(dates):>8} "
            f"{tally.character_error_rate:>8.1%} {tally.missed:>7}"
        )
    for label, wrong in (("amounts", total.wrong_amounts), ("dates", total.wrong_dates)):
        if wrong:
            typer.echo(f"    {label} read wrong ({len(wrong)}; truth -> read):")
            for truth_text, read in wrong[:shown]:
                typer.echo(f"        {truth_text} -> {read}")
            if len(wrong) > shown:
                typer.echo(f"        ... and {len(wrong) - shown} more")


def _rate(rate: float | None) -> str:
    return "-" if rate is None else f"{rate:.1%}"


def diff_engines(
    ctx: typer.Context,
    paths: list[Path] = PATHS,
    engine_names: list[str] | None = EVERY_ENGINE,
    everything: bool = typer.Option(
        False, "--all", help="数字の無い文字も一覧する（省略すると数字だけ）"
    ),
    by_digits: bool = typer.Option(
        False,
        "--digits",
        help="数字を、数字と符号（マイナス・小数点）だけで比べる（〒 をデと読んだ違いなどは除く）",
    ),
    orient_name: str | None = ORIENT,
    steps_names: str | None = PREPROCESS,
    password: str | None = PASSWORD,
) -> None:
    """同じページを、エンジンによって違って読んだ所を一覧する（正解の無い書類向け）。

    指定したエンジン（省略すると読んだことのある全エンジン。2 つ以上）が
    読んだページを、読んだ文字の位置でまとめ、読み方が違う所と、一部の
    エンジンだけが読んだ所を一覧する。--all を付けなければ数字だけ。
    --digits を付けると、数字と符号が違うものだけ。一覧には書類の文字が
    そのまま出るので、そのまま人に見せない。
    """
    config = _config(ctx)
    # Not the engines of the settings by default: one is usual there.
    if engine_names:
        chosen, engines_shown = _named(engine_names, config, _naming(ctx), all_if_none=True)
    else:
        chosen, engines_shown = list(engines.ENGINES), "every engine (default)"
    orient, orient_shown = _orient(orient_name, config)
    steps, steps_shown = _steps(steps_names, config)
    typer.echo(f"Using: engines {engines_shown}; orient {orient_shown}; preprocess {steps_shown}")
    store = Store(config.ocr_dir)
    keys = {engine.name: _key(engine, config, orient, steps) for engine in chosen}
    count, used, partly = Count(digits=by_digits), set[str](), 0
    targets = _targets(paths, config, password)[0]
    for target in targets:
        for plan in target.pages:
            readings = {}
            for name, key in keys.items():
                stored = store.load(target.sha256, key, plan.page)
                if stored is not None:
                    readings[name] = stored.page
            if len(readings) < 2:
                continue
            if engine_names and len(readings) < len(keys):
                partly += 1
            used |= set(readings)
            found = groups(readings)
            count.add(found)
            shown = [
                g
                for g in found
                if not (g.digits_agreed if by_digits else g.agreed) and (everything or g.has_number)
            ]
            if shown:
                typer.echo(f"{target.name} p{plan.page}")
            for group in shown:
                read = " | ".join(
                    f"{name}: {text or '(nothing)'}" for name, text in group.texts.items()
                )
                typer.echo(f"    ({group.x0:.0f}, {group.top:.0f}) {read}")
    if not count.pages:
        typer.echo(f"No page read by two engines or more ({_naming(ctx).command} run)", err=True)
        _tell_read_otherwise(store, targets, keys)
        raise typer.Exit(1)
    numbers_differ = count.numbers - count.numbers_agreed
    summary = (
        f"{count.pages} page(s) read by {', '.join(sorted(used))}: "
        f"{count.numbers_agreed} of {count.numbers} number(s) read alike"
        f"{' by their digits' if by_digits else ''}, "
        f"{numbers_differ} differ ({count.numbers_missed} read by some only); "
        f"{count.texts_agreed} of {count.texts} other piece(s) of text read alike"
    )
    if partly:
        summary += f"; {partly} page(s) not read by every engine given"
    typer.echo(summary)


def remove_missing(
    ctx: typer.Context,
    folders: list[Path] | None = typer.Argument(
        None,
        metavar="[DIR]...",
        exists=True,
        file_okay=False,
        help="場所の記録が無い（前の版で読んだ）ファイルを探すフォルダ",
        show_default=False,
    ),
    missing_dirs_too: bool = typer.Option(
        False,
        "--missing-dirs-too",
        help="フォルダごと見つからないファイルの結果も消す",
    ),
    apply: bool = APPLY,
) -> None:
    """消したスキャンの、OCR で読んだ結果を消す。

    読んだ結果には書類の文字が残るので、ファイルを消したら結果も消す。
    {command} run が記録したファイルの場所（絶対パス）を見て、そこにもう無い
    （中身が変わった）ファイルの結果を消す。どのフォルダを見るかは指定
    しなくてよい。--apply を付けなければ一覧するだけ。

    フォルダごと見つからないもの（外付けドライブのつなぎ忘れなど）は
    消さずに残す。そのフォルダを消したのなら --missing-dirs-too を付ける。
    場所の記録が無いもの（この版より前に読んだもの）も残す。そのファイルの
    フォルダで {command} run をもう一度実行すれば場所が記録される。消えたファイル
    の結果なら、スキャンを置いているフォルダを全部 DIR に渡すと、その
    どこにも無いものを消えたとみなす（渡し忘れたフォルダのものも消えた
    とみなすので、一覧を確かめてから --apply を付ける）。
    """
    store = Store(_config(ctx).ocr_dir)
    found = cleanup.find_missing(store, folders or [], folder_missing_too=missing_dirs_too)
    for folder, names in sorted(found.folder_missing.items()):
        typer.echo(
            f"kept {len(names)} file(s) read in {folder}: the folder isn't there "
            "(a drive not connected? if it's gone, add --missing-dirs-too)"
        )
    if found.unrecorded:
        typer.echo(
            f"kept {len(found.unrecorded)} file(s) read before where they were read was kept: "
            f"run `{_naming(ctx).command} run` on their folders again to keep it, or give the "
            "folders all scans are in"
        )
    _remove(found.going, apply)


def remove_engine(
    ctx: typer.Context,
    names: list[str] = typer.Argument(
        ...,
        metavar="ENGINE...",
        help="結果を消すエンジン",
        autocompletion=_engine_names,
        show_default=False,
    ),
    apply: bool = APPLY,
) -> None:
    """使わなくなったエンジンの、OCR で読んだ結果を消す。

    どの設定で読んだものも消す。もう一覧（{command} list-engines）に無い
    エンジンでも、結果が残っていれば消せる。--apply を付けなければ
    一覧するだけ。
    """
    store = Store(_config(ctx).ocr_dir)
    known = {e.name for e in engines.ENGINES} | cleanup.engines_kept(store)
    unknown = [name for name in names if name not in known]
    if unknown:
        raise typer.BadParameter(
            f"no engine {', '.join(unknown)} (use one of: {', '.join(sorted(known))})",
            param_hint="ENGINE",
        )
    _remove(cleanup.find_engine_runs(store, set(names)), apply)


def remove_logs(ctx: typer.Context, apply: bool = APPLY) -> None:
    """エンジンのログと、止まった実行の一時ファイルを消す。

    --apply を付けなければ一覧するだけ。動いている実行の一時ファイルは
    残す（1 日以上触られていないものだけを消す）。
    """
    _remove(cleanup.find_logs(Store(_config(ctx).ocr_dir)), apply)


def _remove(going: list[cleanup.Item], apply: bool) -> None:
    """List what's going with its size, and remove it if `apply`; exit 1 if some can't be."""
    total = 0
    failures: list[str] = []
    for item in going:
        size = item.size
        total += size
        typer.echo(f"  {item.label}, {_megabytes(size)}")
        if apply:
            for path in item.paths:
                failures += cleanup.remove(path)
    if not going:
        typer.echo("Nothing to remove")
    elif not apply:
        typer.echo(f"{len(going)} item(s), {_megabytes(total)}; add --apply to remove them")
    elif failures:
        for failure in failures:
            typer.echo(f"couldn't remove {failure}", err=True)
        raise typer.Exit(1)
    else:
        typer.echo(f"Removed {len(going)} item(s), {_megabytes(total)}")


def print_what_the_console_cannot() -> None:
    """Write what the console's encoding has no character for (as ½ or · in cp932, the
    Windows console's) escaped, as \\xbd, instead of failing: what engines read can be
    anything."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="backslashreplace")


def _megabytes(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB"


def _with_names(command: Callable[..., None], names: ProgramNames) -> Callable[..., None]:
    """`command` with `names` filled in its help: its docstring's, and its parameters'
    (their help, and the environment variables they're read from)."""
    signature = inspect.signature(command)
    parameters = []
    for parameter in signature.parameters.values():
        info = parameter.default
        if isinstance(info, ParameterInfo):
            # A copy: the options are shared by the commands, and by every app made.
            info = copy(info)
            if info.help:
                info.help = names.fill(info.help)
            if isinstance(info.envvar, str):
                info.envvar = names.fill(info.envvar)
            parameter = parameter.replace(default=info)
        parameters.append(parameter)

    @functools.wraps(command)
    def named(*args: Any, **kwargs: Any) -> None:
        command(*args, **kwargs)

    named.__signature__ = signature.replace(parameters=parameters)  # type: ignore[attr-defined]
    named.__doc__ = names.fill(command.__doc__ or "")
    return named


def make_app(config_of: ConfigOf, names: ProgramNames | None = None) -> typer.Typer:
    """The `ocr` commands as a Typer app, with the settings `config_of` gives for each run,
    named in their help and messages as `names` tells."""
    names = names or ProgramNames()
    app = typer.Typer(
        help=names.fill(
            "スキャンした書類（画像・画像だけの PDF）を OCR で読み、読んだ結果を見る。\n\n"
            "読んだ結果は {settings}DIR に、ファイルの中身ごとに\n"
            "保存する。エンジン・向きの判定（--orient）・読む前の処理（--preprocess）・\n"
            "解像度の組み合わせごとに分けて保存するので、読んだときと違う設定で見ると\n"
            "読んでいないように見える（各コマンドは使う設定を最初に表示する）。"
        ),
        epilog="使う順序: 1. list-engines → 2. run → 3. status・dump-text。"
        "何枚も並べたスキャンなどは先に split-scans。スキャンを消したら remove-missing",
        no_args_is_help=True,
    )

    @app.callback()
    def _settings(ctx: typer.Context) -> None:
        # Not read yet: only once a command runs (not for its --help).
        ctx.obj = _Program(config_of, names)
        print_what_the_console_cannot()

    def add(name: str, panel: str, command: Callable[..., None]) -> None:
        app.command(name, rich_help_panel=panel)(_with_names(command, names))

    read = "読む・読んだ結果を見る"
    add("split-scans", read, split_scans)
    add("run", read, run)
    add("status", read, status)
    add("dump-text", read, dump_text)
    choose = "エンジンを選ぶ"
    add("list-engines", choose, list_engines)
    add("check-engines", choose, check_engines)
    add("make-samples", choose, make_samples)
    add("score-engines", choose, score_engines)
    add("diff-engines", choose, diff_engines)
    tidy = "片付ける（--apply を付けなければ一覧だけ）"
    add("remove-missing", tidy, remove_missing)
    add("remove-engine", tidy, remove_engine)
    add("remove-logs", tidy, remove_logs)
    return app
