# OCR エンジンを入れて確かめる手順

[English](engine-setup.md) | 日本語

手元の Mac・Windows・Linux で、各エンジンの worker（`workers/`）を入れ、架空の書類を `scriptorium run` で読ませて、入れ方の
問題と読み取りの様子を確かめる手順。

Windows（6 エンジン）と Mac（Windows の OCR を除く 6 エンジン）では、この手順で動かして確かめ、動かなかったところは
直してある（2026-10、結果は `benchmark-results.ja.md`）。Linux はまだ。新しい環境・新しい版で動かなかったら、エラーや
ログをそのまま Issue に貼れば、直す材料になる。

コマンドは clone の中で、`scriptorium` コマンド（`uv run scriptorium`、README の「試す」）で動かす。scriptorium の
`ocr` コマンドを付けたプログラムにも同じコマンドがある（`myapp ocr run` など。設定はそのプログラムのもの）。

## 0. 準備

### 守ること

- **実データは使わない。** 下の手順で作る架空の書類だけを読ませる
- 読んだ結果は clone の `data/ocr/` に保存される（`SCRIPTORIUM_DIR`。`data/` は Git の管理外）

### scriptorium を用意する

worker は、このリポジトリを PC に 1 つ clone した中の `workers/` で動かす（scriptorium を使う各プログラムは
`OcrConfig.workers_dir` でそこを指す）。下のコマンドは clone の中で動かす。

```bash
git clone https://github.com/kuoris-ix/scriptorium
cd scriptorium
uv sync
uv run scriptorium list-engines
```

`scriptorium list-engines` は、エンジンごとに「この環境で使えるか」を表示する（`runs on macOS only` などは、その OS では使えない）。

### 架空の書類を用意する

**まずは生成したもの**で試す（正解付きなので、`scriptorium score-engines` で数字が出る）:

```bash
uv run scriptorium make-samples data/ocr-trial --count 1
```

scriptorium の架空の書類（レシート・カードの明細・請求書・通帳）を、劣化（低い解像度・傾き・回転・JPEG・ノイズ・影・PDF など）
16 通りで作る（64 ページ）。重いエンジンを CPU で動かすと時間がかかるので、最初は数枚だけのフォルダ（例:
`*-300.png` と `*-turned90.png` だけを別のフォルダにコピーしたもの）で試してから、全部を読ませるとよい。

**余裕があれば、実際に印刷してスキャン・撮影したもの**も試す（生成したものより実物に近い）。値はすべて架空にする
（金額・日付・店名・名前を、実在のものや自分の書類から写さない）。

1. `data/ocr-trial` の生成した書類（例: `invoice-1-300.png`）を印刷する
2. 次のものを作って `data/ocr-trial/printed/` に置く
   - スキャナー（なければスマホのスキャンアプリ）で 300dpi・150dpi の PDF にしたもの
   - スマホで少し斜めから撮ったもの（jpg）
3. 生成した正解（`invoice-1-300.png.truth.json`）は、印刷したものの位置とは合わないので、目で見比べる

## 1. エンジンごとに入れて読む

### まとめて試す

エンジンの環境を作ったら（下のエンジンごとの手順の `uv sync`）、まず動くかを確かめる。入っているエンジンを 1 つずつ
起動し、架空の書類を 1 枚読ませて、版・装置・時間・正しく読めた金額の数・向きを判定できるかを表示する
（動かないエンジンは `FAILED` と、ログの場所を表示する）:

```bash
uv run scriptorium check-engines
```

動いたら、入っているエンジン全部で読んで、比べた表を作れる。時間がかかるので放置してよい（下の「5. バックグラウンドで
読ませる」）。

```bash
uv run scriptorium run data/ocr-trial -e all                 # 入っているエンジン全部で順に読む
uv run scriptorium score-engines data/ocr-trial --markdown   # 比べた表（Markdown）
uv run scriptorium run data/ocr-trial -e all --orient tesseract --preprocess flatten,deskew
uv run scriptorium score-engines data/ocr-trial --orient tesseract --preprocess flatten,deskew --markdown
```

`-e all` は、この OS で動き、環境を作ってあるエンジンだけを使う（ほかは理由を表示して飛ばす）。`--markdown` の表は、
そのまま結果の文書（`docs/benchmark-results.ja.md`。英訳ができたら英語版にも）に貼れる。

### 1 つずつ試す

どのエンジンも、次の 3 つを行う。`<worker>` は `workers/` の下のフォルダ名、`<engine>` は `scriptorium list-engines` に出る名前。

```bash
uv sync --project workers/<worker>                            # worker の環境を作る（初回は時間がかかる）
uv run scriptorium run data/ocr-trial -e <engine>             # 読む（初回はモデルの取得で時間がかかる）
uv run scriptorium score-engines data/ocr-trial -e <engine>   # 正解と比べて採点する
```

- `scriptorium run` は、エンジンの版・モデル・使った装置（cpu・cuda・mps）を最初に表示し、1 ページごとに読めた塊の数と
  時間を表示する。最後にログファイルの場所を表示する（エラーの詳しい中身はそこにある）
- 読んだ文字と位置を見るときは `uv run scriptorium dump-text data/ocr-trial -e <engine> -o data/ocr-trial/<engine>.csv`
- **装置を変えて比べる**ときは、`--device cpu`（または `cuda`・`mps`）に `--reread` を付ける。保存のキーに装置は
  入らないので、付けないと読み直さない
- **ページの向き**: 生成した書類には 90 度・180 度回したもの（`turned90`・`turned180`）がある。Tesseract を入れたら、
  `--orient tesseract` を付けて読んだ場合（向きを判定し、正しい向きにしてから読む）も試し、`scriptorium score-engines` にも
  `--orient tesseract` を付けて比べる（付けずに読んだものとは別に保存される）
- **前処理**: 照明のむら（`shadow`・`photo`）や傾き（`slant`）のある書類で、`--preprocess flatten,deskew` を付けた
  場合も試す。`scriptorium score-engines` にも同じものを付けて比べる
- 失敗したページは、もう一度 `scriptorium run` すると読み直す。`uv run scriptorium status data/ocr-trial` で、どのページを
  どのエンジンで読んだかがわかる
- `uv sync` で `workers/<worker>/uv.lock` が変わったら、その変更もコミットする

### Tesseract（Mac・Windows・Linux）

本体を先に入れる:

- Mac: `brew install tesseract tesseract-lang`
- Linux（Ubuntu など）: `sudo apt install tesseract-ocr tesseract-ocr-jpn`
- Windows: UB Mannheim の配布するインストーラーで入れ、途中で日本語（Japanese）の言語データを選ぶ。
  `C:\Program Files\Tesseract-OCR\` に入れれば、PATH に足さなくても見つける（別の場所なら、環境変数
  `TESSERACT_CMD` に tesseract.exe の場所を入れる）

```bash
uv sync --project workers/tesseract
uv run scriptorium run data/ocr-trial -e tesseract
```

### yomitoku（Mac・Windows・Linux）

```bash
uv sync --project workers/yomitoku
uv run scriptorium run data/ocr-trial -e yomitoku
uv run scriptorium run data/ocr-trial -e yomitoku-lite
```

- 初回は、モデルを Hugging Face から取ってくる
- **Windows**: CUDA 版の PyTorch が入る。入ったら、GPU が使えるかを確かめる:

  ```powershell
  uv run --project workers/yomitoku python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
  ```

  `True` と GPU の名前が出ればよい。`uv sync` で PyTorch が見つからないと言われたら、
  `workers/yomitoku/pyproject.toml` の `cu128` を、PyTorch の公式サイトの選択欄
  （https://pytorch.org/get-started/locally/ ）に出る CUDA の版（`cu126`・`cu130` など）に変える
- **Mac**: `auto` で MPS を使う。エラーになったら `--device cpu --reread` で CPU でも読ませて、両方の結果を貼る
- `scriptorium run` の最初の行の装置が `cuda`（Windows）・`mps`（Mac）になっているかを見る

### PaddleOCR（Mac・Windows・Linux）

```bash
uv sync --project workers/paddleocr
uv run scriptorium run data/ocr-trial -e paddleocr
```

- 初回は、モデルを PaddlePaddle の配布元から取ってくる
- **Windows**: GPU 版の PaddlePaddle（`paddlepaddle-gpu`）を、PaddlePaddle の配布元（`cu126`）から入れる。
  `uv sync` で見つからないと言われたら、PaddlePaddle の公式の導入ページで、Windows・pip・CUDA の組み合わせの
  URL を確かめ、`workers/paddleocr/pyproject.toml` の `url` を直す
  - Windows の GPU 版は CUDA の部品（cuDNN など）を含まないので、NVIDIA が PyPI で配る部品（`nvidia-cudnn-cu12` など）も
    worker の環境に入れ、worker が起動するときにそれを先に見つけるようにしてある。CUDA を別に入れなくてよい
    （入っていても、こちらが使われる）。`cudnn64_9.dll` などが見つからないと言われたら、`uv sync` をやり直す
- Mac・Linux は CPU 版

### NDLOCR-Lite（Mac・Windows・Linux）

Git が要る（uv が GitHub のリポジトリから取ってくる）。版は 1.2.3 に固定してある（1.3.0 以降は、配布の設定から
`pdf_output_utils` などが抜けていて、入れたコマンドが `ModuleNotFoundError` で止まる）。

```bash
uv sync --project workers/ndlocr_lite
uv run scriptorium run data/ocr-trial -e ndlocr-lite
```

- 「No module named 'ocr'」で起動できないと言われたら、NDLOCR-Lite の本体のモジュールの名前が違う（worker は
  `ndlocr-lite` コマンドの本体の `ocr` モジュールを読み込んで使う）。`workers/ndlocr_lite/.venv/lib/python3.*/site-packages/`
  （Windows は `.venv\Lib\site-packages\`）にある `ndlocr_lite-*.dist-info/entry_points.txt` の中身を貼る

### Apple Vision（Mac だけ）

```bash
uv sync --project workers/apple_vision
uv run scriptorium run data/ocr-trial -e apple-vision
```

- 日本語（`ja-JP`）が使えないと言われたら、エラーに出る言語の一覧を貼る
- 箱の位置が文字とずれていないか（上下が逆になっていないか）を、ダンプの座標と書類を見比べて確かめる

### Windows の OCR（Windows だけ）

日本語の OCR の部品が入っているかを確かめる（日本語版の Windows なら普通は入っている）:

```powershell
Get-WindowsCapability -Online | Where-Object Name -like 'Language.OCR*ja-JP*'
```

`State` が `Installed` ならよい。

```powershell
uv sync --project workers/windows_ocr
uv run scriptorium run data/ocr-trial -e windows-ocr
```

- 600dpi の A4 は大きすぎて読めないことがある（Windows の OCR は 1 辺の画素数に上限がある）。そう言われたら、
  解像度の上限（`SCRIPTORIUM_MAX_DPI`）を 400 などにして試す（保存のキーが変わるので、読み直しになる）

## 2. 見ること

エンジンと装置の組み合わせごとに、次を見る。

- **入ったか・起動したか**: `uv sync` か `scriptorium run` のエラー（ログファイルの中身も）
- **版と装置**: `scriptorium run` の最初の行
- **時間**: 1 ページあたりのおよその時間（初回のモデルの取得・読み込みは除く）
- **読めたか**: `scriptorium score-engines` の結果（金額・日付を正しく読めた割合、劣化ごとの表、読み違えた金額・日付の一覧）。
  印刷したものは、ダンプの CSV を書類と見比べる
- **塊の単位**: 行ごとか、単語（1〜2 文字）ごとか（ダンプの CSV で見る）

## 3. 結果の書き方

エンジンと環境ごとに、次を Issue に貼る。架空の書類の結果なので、読み取った文字が
そのまま入っていてよい。

- `scriptorium run` の最初の行（版・モデル・装置）と最後の行
- `scriptorium check-engines` の出力
- `scriptorium score-engines --markdown` の出力（前処理なし・ありの 2 つ）
- つまずいた点（`uv sync` や `scriptorium run` のエラー、ログファイルの中身）
- 印刷したものを試したなら、気づいたこと

## 4. 後片付け

- 試した結果は `data/ocr/` に残る。要らなければ消してよい
- エンジンが初回に取ってきたモデルは、別の場所に保存されている（yomitoku は Hugging Face のキャッシュ、
  PaddleOCR はホームの下の `.paddlex` など）。容量が気になったら消す
- worker の環境は `workers/<worker>/.venv`（Git の管理外）。消しても、次の `uv sync` で作り直せる

## 5. バックグラウンドで読ませる

時間がかかるときは、OS の機能で放置する。止めても、もう一度実行すれば続きから読む。進み具合を書くフォルダ
（下の例では `data/ocr/`）は先に作っておく。

- macOS・Linux:

  ```bash
  nohup uv run scriptorium run data/scan -e yomitoku > data/ocr/run.txt 2>&1 &
  ```

- Windows（PowerShell）:

  ```powershell
  Start-Process uv -ArgumentList 'run','scriptorium','run','data/scan','-e','yomitoku' -WindowStyle Hidden -RedirectStandardOutput data/ocr/run.txt
  ```

`data/ocr/run.txt` に進み具合が書かれる。
