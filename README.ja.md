# scriptorium

[English](README.md) | 日本語

スキャンした書類（画像と、画像だけの PDF）を OCR のエンジンで読み、読んだ結果を保存する道具です。エンジンは
それぞれ専用の環境で動かします。給与明細・レシート・税の書類のような日本語の書類のために作りました。Python の
ライブラリで、自分のプロジェクトに組み込んで使います。プロジェクトのコマンド（Typer のアプリ）に `ocr` の
コマンドを付け、読んだ結果はプロジェクトのコードから読み出します。単独で動くコマンドは、今はありません。

- 複数のエンジン（YomiToku・PaddleOCR・NDLOCR-Lite・Apple Vision・Windows の OCR・Tesseract）を並べて使えます。
  エンジンはそれぞれ自分の Python の環境で動き、小さな JSON のやりとりで話します。
- 読んだ結果はページごとに保存します。保存のキーは、ファイルの中身と、読み方を決めるすべての設定です。止めても
  続きから読め、同じものを 2 度読みません。
- エンジンを選ぶための道具があります。正解付きの架空の書類でエンジンを採点する機能と、正解のない実際の書類で
  エンジン同士の読み方の違いを一覧する機能です。
- 文字の層のある PDF は、その文字を同じ形で読めます（`scriptorium.pdf`）。

状況: 0.x です。コマンドのヘルプと `docs/` の多くは、今は日本語です。

## 必要なもの

- Python 3.14 以上と [uv](https://docs.astral.sh/uv/)
- エンジンの環境: YomiToku・PaddleOCR・NDLOCR-Lite は Python 3.10〜3.13 が要ります（uv が取ってきます）。ライブラリと
  モデルで数 GB のディスクを使います

## プロジェクトで使う

タグを付けたリリースを依存に書きます:

```toml
[project]
dependencies = ["scriptorium @ git+https://github.com/kuoris-ix/scriptorium@v0.1.0"]
```

そして、自分の設定（`OcrConfig`）と、ヘルプやメッセージに出すコマンド名・環境変数名（`ProgramNames`）を渡して、
Typer のアプリにコマンドを付けます:

```python
from pathlib import Path

from scriptorium.commands import make_app
from scriptorium.config import OcrConfig, ProgramNames


def config() -> OcrConfig:
    # 自分の設定（環境変数・.env など）をここで読む。コマンドを実行するたびに呼ばれる。
    return OcrConfig(
        ocr_dir=Path("data/ocr").absolute(),
        workers_dir=Path("~/scriptorium/workers").expanduser(),
        documents=MY_SAMPLE_DOCUMENTS,  # `ocr make-samples` で作る架空の書類
        check_document=MY_SAMPLE_DOCUMENTS["receipt"],  # `ocr check-engines` で読ませるもの
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

読んだものはすべて、手元の `OcrConfig.ocr_dir` に残ります。中身は書類の文字で、JSON のファイルです。この
フォルダは、スキャンと同じように扱ってください。`ocr remove-missing` で、消したファイルから読んだ結果を消せます。

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
