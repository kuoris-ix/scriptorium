# scriptorium

[![CI](https://github.com/kuoris-ix/scriptorium/actions/workflows/ci.yml/badge.svg)](https://github.com/kuoris-ix/scriptorium/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-blue.svg)

[English](README.md) | 日本語

スキャンした書類（画像と、画像だけの PDF）を OCR のエンジンで読み、読んだ結果を保存する道具です。給与明細・レシート・
税の書類のような日本語の書類のために作りました。読んだものはすべて手元に残ります。Python のライブラリで、自分の
プロジェクトに組み込んで使います。プロジェクトのコマンド（Typer のアプリ）に `ocr` のコマンドを付け、読んだ結果は
プロジェクトのコードから読み出します。試すときやエンジンを比べるときは、同じコマンドを `scriptorium` として単独で
動かせます。

- **エンジンどうしが邪魔をしない。** エンジン（YomiToku・PaddleOCR・NDLOCR-Lite・Apple Vision・Windows の OCR・
  Tesseract）はそれぞれ自分の Python の環境で動き、小さな JSON のやりとりで話します。ライブラリが食い違うエンジンも
  並べて使え、エンジンの乗り換えは設定を変えるだけです。
- **同じものを 2 度読まない。** 読んだ結果はページごとに保存します。保存のキーは、ファイルの中身と、読み方を決める
  すべての設定です。長い実行も、止めて続きから読めます。
- **根拠を持ってエンジンを選べる。** 正解付きの架空の書類でエンジンを採点する機能と、正解のない実際の書類で
  エンジン同士の読み方の違いを一覧する機能があります。Windows・macOS・Linux でのエンジンの結果も公開しています
  （`docs/benchmark-results.ja.md`）。

状況: 0.x です。コマンドのヘルプと `docs/` の多くは、今は日本語です。

## 必要なもの

- Python 3.14 以上と [uv](https://docs.astral.sh/uv/)
- エンジンの環境: YomiToku・PaddleOCR・NDLOCR-Lite は Python 3.11〜3.13 が要ります（uv が取ってきます）。ライブラリと
  モデルで数 GB のディスクを使います

## 試す

`scriptorium` コマンドは、同じコマンドを単独で動かします。エンジンを採点するための架空の書類も持っています。
clone の中で動かします:

```sh
git clone https://github.com/kuoris-ix/scriptorium
cd scriptorium
uv sync
uv sync --project workers/yomitoku                          # エンジン（「エンジンを入れる」）
uv run scriptorium check-engines                            # 動くか
uv run scriptorium make-samples data/ocr-trial --count 1    # 正解付きの架空の書類
uv run scriptorium run data/ocr-trial -e yomitoku           # 読む
uv run scriptorium score-engines data/ocr-trial             # どれだけ正しく読めたか
```

`uv run scriptorium --help` でコマンドを一覧できます。設定は `SCRIPTORIUM_*` の変数で、環境変数から、無ければ
作業フォルダの `.env` から読みます。パスは作業フォルダからの相対です。各コマンドは、使う設定とその出どころを最初に
表示します。

| 変数 | 既定 | 内容 |
|---|---|---|
| `SCRIPTORIUM_DIR` | `data/ocr` | 読んだ結果の保存先 |
| `SCRIPTORIUM_WORKERS_DIR` | clone の `workers/` | エンジンの worker |
| `SCRIPTORIUM_ENGINES` | （なし） | `-e` を付けないときのエンジン（カンマ区切り） |
| `SCRIPTORIUM_DEVICE` | `auto` | `auto`・`cpu`・`cuda`・`mps` |
| `SCRIPTORIUM_ORIENT` | （なし） | ページの向きを判定するエンジン（`tesseract`） |
| `SCRIPTORIUM_ORIENT_MIN_CONFIDENCE` | `5` | これより確からしさの低い判定では回さない |
| `SCRIPTORIUM_PREPROCESS` | （なし） | 読む前の処理（カンマ区切り。`flatten,deskew` など） |
| `SCRIPTORIUM_MIN_DPI`・`SCRIPTORIUM_MAX_DPI` | `300`・`600` | スキャンのページを描き直す解像度の範囲 |
| `SCRIPTORIUM_WORKER_PAGES` | `1000` | worker をこのページ数ごとに起動し直す（`0` で起動し直さない） |
| `SCRIPTORIUM_JOBS` | `auto` | エンジンごとに同時に読む worker の数（`--jobs`）。`auto` は、複数で速くなるエンジン（`tesseract`・`windows-ocr`）だけ、コア数とメモリに合わせて増やす |
| `SCRIPTORIUM_PDF_PASSWORD` | （なし） | 保護された PDF のパスワード |

## プロジェクトで使う

タグを付けたリリースを依存に書きます:

```toml
[project]
dependencies = ["scriptorium @ git+https://github.com/kuoris-ix/scriptorium@v0.3.0"]
```

そして、自分の設定（`OcrConfig`）と、ヘルプやメッセージに出すコマンド名・環境変数名（`ProgramNames`）を渡して、
Typer のアプリにコマンドを付けます:

```python
from pathlib import Path

import typer

from scriptorium import fictional
from scriptorium.commands import make_app
from scriptorium.config import OcrConfig, ProgramNames

app = typer.Typer()  # 自分のコマンドライン


def config() -> OcrConfig:
    # 自分の設定（環境変数・.env など）をここで読む。コマンドを実行するたびに呼ばれる。
    return OcrConfig(
        ocr_dir=Path("data/ocr").absolute(),
        workers_dir=Path("~/scriptorium/workers").expanduser(),
        # `ocr make-samples` で作る架空の書類（scriptorium のものか自分のもの）と、
        # `ocr check-engines` で読ませるもの。
        documents=fictional.DOCUMENTS,
        check_document=fictional.receipt,
        engines="yomitoku-lite",
    )


names = ProgramNames(
    command="myapp ocr", settings_prefix="MYAPP_OCR_", password_variable="MYAPP_PDF_PASSWORD"
)
app.add_typer(make_app(config, names), name="ocr")
```

これで `myapp ocr list-engines`・`myapp ocr run <フォルダ>`・`myapp ocr status <フォルダ>` などが使えます
（`myapp ocr --help` で一覧）。`tests/ocr_app.py` が、これをする小さなプログラムです。

## エンジンを入れる

worker は、このリポジトリの `workers/` にあります。PC ごとに 1 度 clone し、使いたいエンジンの環境を作ります。
scriptorium を使う各プロジェクトは、`OcrConfig.workers_dir` でそこを指します。

```sh
git clone https://github.com/kuoris-ix/scriptorium
uv sync --project scriptorium/workers/yomitoku
```

`ocr list-engines` で入っているエンジンがわかり、`ocr check-engines` で各エンジンに架空の書類を読ませて、
動くかを確かめられます。Tesseract は別に入れます（日本語のデータも）。Apple Vision は macOS だけ、Windows の OCR は
Windows だけで動きます。詳しい手順は `docs/engine-setup.ja.md` にあります。

| エンジン | worker | エンジンのライセンス |
|---|---|---|
| `yomitoku`・`yomitoku-lite` | `yomitoku` | CC BY-NC-SA 4.0（商用の利用は不可） |
| `paddleocr` | `paddleocr` | Apache-2.0 |
| `ndlocr-lite` | `ndlocr_lite` | CC BY 4.0 |
| `apple-vision` | `apple_vision` | macOS の Vision（ocrmac 経由、MIT） |
| `windows-ocr` | `windows_ocr` | Windows の OCR（winocr 経由） |
| `tesseract` | `tesseract` | Apache-2.0 |

scriptorium はエンジンを含みません。各 worker の `pyproject.toml` がエンジンを依存として名前で書き、`uv sync` で
入ります。使う前にエンジンのライセンスを確かめてください（YomiToku は商用の利用ができません）。モデルは初めて
使うときに取ってきます（画像は送りません）。

## 保存するものとプライバシー

読んだものはすべて、手元の `OcrConfig.ocr_dir`（`SCRIPTORIUM_DIR`）に残ります。中身は書類の文字で、JSON のファイルです。この
フォルダは、スキャンと同じように扱ってください。scriptorium は、このフォルダを作るときは、作ったユーザーだけが読み書き
できるようにします（Linux・macOS ではモード 700）。すでにあるフォルダはそのままなので、共有の PC では `chmod 700` を
実行してください。`ocr remove-missing` で、消したファイルから読んだ結果を消せます。

## 開発

```sh
uv sync
uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest
```

テストは、本物のエンジンの代わりに決まった結果を返すテスト用の worker（スタブ）と、架空の値で生成した
サンプルの書類だけを使うので、エンジンを入れなくても動きます。変更を送る前に
[CONTRIBUTING.md](CONTRIBUTING.md)（英語）を読んでください。やりとりと保存の形は `docs/spec.ja.md`、
設計とエンジンの比較は `docs/` にあります（`*.ja.md`）。

## ライセンス

MIT（`LICENSE`）。エンジンには、それぞれのライセンスがあります（上の表）。
