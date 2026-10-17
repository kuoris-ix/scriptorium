# OCR の設計メモ

[English](design.md) | 日本語

スキャンした画像や、画像だけの PDF から文字を読み取る（OCR）しくみの設計。土台は実装済み（下の「実装の状況」）。
実装を進めながら、このメモも直す。

## 実装の状況

| 部分 | 状況 |
|---|---|
| ページを画像にする（描き直す・解像度の決め方・回転・画像ファイル） | 済み（`raster.py`） |
| OCR にかけるページの選び方 | 済み |
| エンジンを別の環境で動かす worker とやりとり | 済み（`worker.py`・`workers/ocr_worker.py`） |
| 保存と再開 | 済み（`store.py`） |
| コマンド `ocr run`・`ocr status`・`ocr list-engines`・`ocr dump-text` | 済み（`commands.py`） |
| worker: Tesseract | 済み。Linux（コンテナ）・Windows・Mac で動かして確かめた |
| worker: yomitoku・yomitoku-lite・Windows の OCR | 済み。Windows（yomitoku は CUDA）で動かして確かめた（`ocr check-engines`）。全エンジンの比較は `benchmark-results.ja.md` |
| worker: PaddleOCR・NDLOCR-Lite | 済み。Windows で動かして見つかった問題（PaddleOCR は cuDNN が見つからない、NDLOCR-Lite は 1.3.0 以降の配布の不具合）を直し、Windows（PaddleOCR は CUDA）で動くことを確かめた |
| worker: Apple Vision | 済み。Mac で動かして確かめた（2026-10-09）。ほかの worker も Mac で動き、Windows と同じ結果になった（`benchmark-results.ja.md` の「Mac」） |
| 前処理: ページの向きの判定 | 済み（`--orient tesseract`。Tesseract の OSD で判定し、正しい向きに回してから読む） |
| 前処理: 照明のむらの補正・傾きの補正 | 済み（`--preprocess flatten,deskew`、`preprocess.py`） |
| 何枚も並べたスキャンを切り出す（`ocr split-scans`）・2 ページに分けて読んだ書類をつなぐ（`ocr join-scans`） | 済み（`split.py`・`join.py`） |
| 架空の書類と正解の生成（`ocr make-samples`） | 済み（`samples.py`、`benchmark.ja.md`）。Tesseract の結果は `benchmark-results.ja.md` |
| 正解との比較（`ocr score-engines`） | 済み（`compare.py`）。`--markdown` で結果の表 |
| エンジン同士の照合（`ocr diff-engines`、正解のない書類） | 済み（`diff.py`）。食い違いの一覧まで。照合で読み違いが見つかるかは `ocr score-engines --agreement` で数える |
| 使う側のプログラムに `ocr` コマンドを付ける | 済み（`make_app`。設定は `OcrConfig`、名前は `ProgramNames`。§12） |
| 単独で動くコマンド `scriptorium` | 済み（`cli.py`。設定は `SCRIPTORIUM_*` の変数と `.env`、架空の書類は `fictional.py`。§12） |

## 目的と範囲

- スキャンした書類（画像だけの PDF、jpg・png などの画像）から、文字と位置を取り出す
- 使いみちは 2 つ
  - **調査**: 取り込みの処理を作る前に、書類のレイアウトを調べる（PDF の文字の書き出し `pdf.write_dump` の OCR 版が
    `ocr dump-text`）
  - **取り込みの前段**: 保存した OCR の結果を、使う側のプログラムの取り込み処理が読む（PDF の文字の層と同じく、文字の
    位置から読み取る）
- 対象の書類は日本語を含む
- 使う側のプログラムが、`ocr` のコマンドを自分のコマンドに付けて使う（README）

## 前提として決めたこと

- **ローカルで動くエンジンだけを使う。** 読む書類（個人の書類）を外に出さない。クラウドの OCR は使わない
- **速度より精度を優先する。** 時間がかかる処理はバックグラウンドで放置して待つ
- **GPU は使えれば使う。** どのエンジンも CPU で動くことを前提にし、GPU は速くなるだけの扱いにする
  - 動かして確かめた環境: Windows（CUDA）・macOS（Apple Silicon、MPS）。Linux は Tesseract だけ
- **エンジンは 1 つに固定しない。** 複数のエンジンを使い分け、乗り換えもできる形にする
- **OCR と取り込みを分ける。** 取り込みは OCR を動かさず、保存済みの結果を読むだけにする

## 全体の流れ

```text
入力（スキャン PDF・画像）
  │
  ├─ 1. ページを画像にする（PDF → 画像）
  ├─ 2. 前処理（向きの判定と回転、照明のむら・傾きの補正）
  ├─ 3. OCR エンジン（worker、名前で選ぶ）
  └─ 4. 共通の結果に変換（文字・座標・確からしさ）
        │
        ▼
  ocr_dir に保存（ページごと）       ← ここまでが `ocr run`。重いのでバックグラウンドで動かす
        │
        ▼
  保存した結果を読むだけ
    ├─ ocr status（どこまで読んだか）
    ├─ ocr dump-text（文字と位置のダンプ）
    ├─ ocr score-engines（正解との比較）・ocr diff-engines（エンジン同士の照合）
    └─ 使う側のプログラムの処理（取り込み・個人情報の確認など）
```

## 1. ページを画像にする（`raster.py`）

### ページを描き直す

PDF に埋め込まれた画像を取り出すのではなく、ページを丸ごと画像に描き直す（レンダリング）。pdfplumber が使っている
pypdfium2 で描き直せるので、新しい依存はいらない。

| | 埋め込み画像を取り出す | ページを描き直す |
|---|---|---|
| 画質 | 元の画素のまま | 解像度が元と合わないと縮小・拡大で劣化する |
| 回転・切り抜き | ページの回転・変換の指定・切り抜き範囲を自分で処理する | 見たとおりの画像になる |
| 1 ページに画像が複数 | 帯状・タイル状に分かれていることがあり、つなぎ合わせが要る | 1 枚の画像になる |
| 白黒画像・マスク | 形式（JBIG2・CCITT など）ごとに扱いが要る | 気にしなくてよい |
| 画像以外の部分 | 拾えない（スタンプ・重ねた文字など） | 含まれる |
| 座標を PDF に戻す | 変換の指定から計算する | 倍率を掛けるだけ |

劣化を避けるため、**描き直す解像度はスキャンの元の解像度に合わせる。**

- pdfplumber の `page.images` には、ページ上の画像の位置（ポイント）と元の画素数（`srcsize`）がある。
  ページで一番大きい画像（`pdf.largest_image`）の長い辺について、`画素数 ÷ (置かれた長さ ÷ 72)` が元の解像度になる
  （長い辺どうしで比べるので、90 度回して置かれた画像でも正しい）
- 求めた解像度を、範囲（既定は 300〜600dpi、`OcrConfig.min_dpi`・`max_dpi`）に収める
  - 低すぎるとき（150dpi のスキャンなど）は 300dpi に上げる。情報は増えないが、多くのエンジンは文字が小さすぎると精度が落ちるため
  - 高すぎるとき（1200dpi など）は 600dpi に下げる。メモリと時間を抑えるため
- スキャンでないページ（`--force` で読むとき）は 300dpi で描き直す
- 描くと `MAX_PIXELS`（1 億 8,000 万画素。A2・600dpi は通る）を超えるページは、解像度を下げて描くことはせず、描かずにそのページの失敗にする
- ページの回転の指定（`/Rotate`）は、pdfplumber の座標も pypdfium2 の描き直しも「見たとおりの向き」になる
  （テストで確かめた）。画像の大きさとページの大きさ（ポイント）の比で、画素をポイントに戻す

### 画像ファイル

- png・jpg・tiff（複数ページ）・bmp・webp を読む。iPhone の HEIC は、必要になったら pillow-heif を足す
- EXIF の向き（Orientation）を反映してから使う
- **描き直さない（画素はそのまま）。** 解像度は、画像の dpi の情報が 100〜2400 ならそれを、そうでなければ
  300dpi とみなす（72dpi などはプログラムの既定値で、スキャンの解像度ではないことが多いため）。この解像度で
  「ページの大きさ（ポイント）」を決め、座標を PDF と同じ単位にそろえる

### OCR にかけるページの選び方

PDF のページごとに、文字の層（埋め込まれたテキスト）があるかと、一番大きい画像がページをどれだけ覆っているかで
決める。覆う割合が 0.5 以上ならスキャンとみなす。

| ページの状態 | 扱い（`raster.Reason`） |
|---|---|
| スキャンで、文字の層はない | OCR する（`scanned`） |
| スキャンで、文字の層もある（スキャナーが付けた OCR の層など） | OCR する（`scanned, with a text layer`） |
| スキャンでなく、文字の層もない（文字をアウトライン化した PDF・白紙など） | OCR する（`no text layer`） |
| スキャンでなく、文字の層の文字がすべて `(cid:15)` のように読める（フォントに文字コードの対応表がない。例えば、業務システムが出力する帳票の PDF でありうる） | OCR する（`text layer without the characters' codes`）。描くと正しく見えるので、OCR で読める |
| スキャンでなく、文字の層がある | OCR しない（`text layer`）。`--force` で読む |
| 画像ファイル | OCR する（`image file`） |

### 何枚も並べたスキャンを切り出す（`split.py`、済み）

フラットヘッドスキャナーでまとめて読み取ったページ（例えば、A4 に小さな帳票を 4 枚、A5 の明細を 2 枚並べたもの）や、スキャナーの
空いた所（灰色）が半分写ったページは、読む前に
`ocr split-scans` で 1 枚ずつの PNG に切り出す（使う側の取り込みが「1 ファイル = 1 枚」で扱えるように）。

- ページを 50dpi に小さくし、照明をならして（`flatten` と同じ）インクを 2.5mm ふくらませ、つながったまとまりごとに
  囲む。一辺 40mm 未満はごみ（ほこり・印）とし、囲みが重なるまとまりは 1 枚にまとめる（枠がかすれて切れたもの）
- まとまりの端の、インクが高さ（幅）の 10% 未満の列（行）は削る。例えば、スキャナーのガラスの縁の線が、空いた所を
  通って書類の枠につながると、ページ全体が 1 つのまとまりになってしまうため。インクとみなす濃さは傾きの判定と同じ 80
  （60 にすると、空いた所の灰色が白い所との境でインクになりうる）
- 余白は 3mm。隣の書類との隙間が狭ければ（例えば 2 枚がほぼ接していて、隙間が 4mm しかないとき）、その真ん中までに
  して、隣の枠線が端に入らないようにする
- 1 枚ずつ `deskew` と同じやり方で傾きを直す（6 度まで。手で並べた紙の、1 度前後の傾きを想定）
- 書類の位置・大きさ・直した角度だけを表示する。実データの画像は人が開いて確かめる
- 書類が 1 枚だけのページも切り出す（空いた所を除くため）。同じ名前のファイルは書き換えない

### 2 ページに分けて読んだ書類をつなぐ（`join.py`、済み）

スキャナーのガラスより大きい書類（例えば、A3 の書類を A4 のスキャナーで）を、左半分・右半分の 2 ページに分けて
読み取ったものは、読む前に `ocr join-scans` で 1 枚の PNG につなぐ。分かれたままだと、表の 1 行が切れ目で 2 つに
分かれ（項目の名前が 1 ページ目、値が 2 ページ目）、位置で読む取り込みができないため。

- ページを 2 つずつ（1 と 2、3 と 4、…）左・右として並べる。ページの数が奇数のファイルはつながない
- PDF のページは、埋め込まれた画像を**描き直さずに取り出す**（元の画素のまま。下の「埋め込み画像を取り出す」の
  一部）。対象は、ページを 1 枚の画像が覆い、回転・反転なしで置かれたページだけ（`/Rotate` があるもの、画像が
  複数あるもの、ページを覆わないものはつながない）。画像ファイルは、そのまま使う。2 枚の解像度が違えばつながない
- 2 枚をガラスに置いた高さは少しずつ違う（例えば 1〜2mm）ので、右半分を上下にずらして左半分に合わせる。切れ目の
  両側 15mm の帯の、行ごとの黒さを比べ、一致（相関）が一番よいずらし方を、10mm の範囲で 100dpi で探してから、元の
  解像度で詰める。一致が 0.6 未満なら、間違ってつなぐより止める（`--no-align` で、ずらさずにつなぐ）。架空の書類では、
  正しくずらすと 0.9 を超えた
- スキャナーはガラスの端の 1mm ほどを読めず、白にすることがある。そのままつなぐと切れ目に白い帯ができるので、
  先に各半分の切れ目の側の白い列を、3mm まで削る（`--keep-blank` で削らない）。白い列は、インク（濃さ 128 未満）が
  高さの 0.1% 以下の列。白い帯の列にもごみ・ノイズの点が数画素あるので「インクが 1 つも無い列」では削れず、
  文字や罫線が何本か通る紙の列には、その何倍ものインクがある。読めなかった所の中身は戻らない（書類の左右の
  間の、紙の余白が削られることはある）
- 左右のずれ（切れ目で欠けた所・2 度読んだ所）は見ない。2 枚の傾きの違いも直さない。削った幅・ずらした長さ・
  一致の度合いだけを表示し、つないだ画像は人が開いて確かめる
- ずらして空いた所は白で埋める。2 枚とも白黒（グレースケール）ならグレースケール、そうでなければカラー（RGB）。
  名前は「元の名前_p1+2.png」で、同じ名前のファイルは書き換えない

## 2. 前処理

### ページの向きの判定（済み）

Tesseract は、180 度回ったページをまったく読めなかった（`17-06-28` を `82-90-L1` と読む。`benchmark-results.ja.md`）。
そこで、**読む前に各ページの向きを判定し、正しい向きに回してからエンジンに渡す**。

```bash
myapp ocr run data/scan -e yomitoku --orient tesseract
```

- 向きを判定するのも worker（やりとりに `orient` を足した）。今は Tesseract の worker だけが判定できる（OSD:
  Tesseract の向きと文字の種類の判定。`osd` のデータを使う）。Tesseract を入れておけば、どのエンジンで読むときにも使える
- 判定は 1 回の実行の最初に、まだ判定していないページについてまとめて行い、ファイルごとに保存する
  （`<SHA-256>/orient-tesseract.json`。エンジンを変えても判定し直さない）
- 判定の確からしさ（Tesseract の値）が `OcrConfig.orient_min_confidence`（既定 5）未満のページは回さない。
  文字が少なくて判定できないページは、回さない（確からしさ 0 として保存）。架空の書類では、正しい判定は 8.4 以上、
  間違った判定（ぼかしたページを 180 度と判定したもの）は 2.8 以下だったので、その間の 5 にした
- 回したページを読んだ結果は、元のページの向きに戻して保存する（座標はいつも元のページの上）
- `--orient` で読んだ結果は、読まなかったものと別に保存する（`RunKey` に判定のエンジンと確からしさの下限が入る）。
  `ocr status`・`ocr dump-text`・`ocr score-engines` も、同じ `--orient` を付けて見る。設定（`OcrConfig.orient`）で既定にできる
- 架空の書類（128 ページ）では、Tesseract の判定は回したページ（32 ページ）すべてで正しかった。判定してから読むと、
  180 度回ったページも、回っていないページと同じだけ読めた。間違えたのは、ぼかしたページ 8 ページのうち 6 ページ
  （回っていないのに 180 度と判定）で、どれも確からしさが低かった
- yomitoku・PaddleOCR・Apple Vision などが、自分でページの向きを扱えるか（`--orient` が要るか）は `benchmark-results.ja.md` の「Windows: 向きの判定と前処理」で確かめた
- PaddleOCR の文書の向きの補正・ゆがみの補正（`use_doc_orientation_classify`・`use_doc_unwarping`）は使わない。
  結果の座標が補正後の画像の上になり、ページに戻せないため

### 照明のむら・傾きの補正（済み）

Tesseract は照明のむら（金額 50%）と 3 度の傾き（77%）に弱かった。そこで、向きをそろえた後、エンジンに渡す前に
する前処理を、名前で選べるようにした（`preprocess.py`）。Pillow だけで作り、新しい依存はない。

```bash
myapp ocr run data/scan -e yomitoku --orient tesseract --preprocess flatten,deskew
```

| 名前 | すること | しかた |
|---|---|---|
| `flatten` | 照明のむら（影・スマホで撮ったときの明暗）をならす | 小さくした画像で、まわりの一番明るいところ（紙の明るさ）を求め、各画素をそれで割る。文字が残るよう、大きい文字より広い範囲（A4 で約 7mm）で求める。グレースケールになる |
| `deskew` | 6 度までの傾きをまっすぐにする | 照明をならした小さな画像を少しずつ回し、行ごとの黒さのばらつきが一番大きくなる角度（文字の行と行間がはっきり分かれる角度）を求める。0.2 度未満なら回さない |

- 書いた順に行う（`flatten,deskew` が、傾きの判定にも照明をならした画像を使うのでおすすめ）
- 前処理をした結果も、元のページの座標に戻して保存する（`deskew` は回した画像の上の箱を、元の向きに回して囲み直す）
- 前処理の名前と、前処理の改訂番号（`preprocess.REVISION`、やり方を変えたら上げる）は保存のキーに入る。
  `status`・`dump-text`・`score-engines` にも同じ `--preprocess` を付けて見る。設定（`OcrConfig.preprocess`）で既定にできる
- 架空の書類で Tesseract に使うと、金額の完全一致が 84.7% → 93.2%（照明のむら 50% → 96%、3 度の傾き 77% → 95%、
  スマホで撮った風 8.5% → 75%）。白黒の 2 値化した書類の日付だけ少し悪くなった（`benchmark-results.ja.md`）

## 3. OCR エンジン

### エンジンは別の環境・別のプロセスで動かす

scriptorium（と使う側のプログラム）は Python 3.14 以上だが、主な OCR のライブラリはまだ 3.14 に対応していない（2026 年 10 月の時点で、
yomitoku は 3.10〜3.13、PaddleOCR の土台の paddlepaddle も 3.13 まで）。また、エンジンごとに torch などの版が
ぶつかりやすい。そこで、**エンジンは scriptorium と同じ環境に入れず、エンジンごとの環境で別のプロセス（worker）として動かす。**

```text
scriptorium（Python 3.14）                     workers/yomitoku/（Python 3.13、yomitoku・torch）
                                    ──起動──▶  uv run --project workers/yomitoku python …/worker.py
  {"type": "start", device, options} ─stdin─▶  モデルを 1 度だけ読み込む
                                    ◀stdout─   {"type": "ready", engine: 名前・版・モデル・装置}
  ページを PNG にして <ocr_dir>/tmp へ
  （--orient のとき、判定する worker に {"type": "orient", id, image} ─▶ {"type": "oriented", id, rotate, confidence}）
  {"type": "page", id, image, output} ─stdin─▶ 読んで、結果を output（JSON）に書く
                                    ◀stdout─   {"type": "done", id, seconds} か {"type": "failed", id, error}
  結果をポイントに直して保存
  （次のページ … 最後に stdin を閉じる）
```

- 各エンジンの環境は `workers/<worker>/` に置く、小さな uv のプロジェクト（`pyproject.toml`・`uv.lock`・
  `worker.py`。パッケージにはしない）。Python の版・依存はエンジンごとに決める。`uv sync --project workers/<worker>`
  で入れる（初回の `uv run` でも自動で入る）
- worker は scriptorium を読み込まない（Python の版を縛らないため）。共通のやりとりは `workers/ocr_worker.py`
  （標準ライブラリだけ、Python 3.10 以上。ruff は `workers/ruff.toml` で 3.10 として検査する）
- 標準出力の答えには印（`@@scriptorium@@ `）を付ける。ライブラリが標準出力に書くもの（進み具合など）と見分け、
  ログにだけ書くため。worker の Python の `print` は標準エラー出力に回す。標準エラー出力はログファイルへ
- 読んだ結果はファイルで渡す（大きく、標準出力に混ざると壊れるため）。書きかけを読まないよう、一時ファイルに
  書いてから名前を変える
- やりとりの形には版（`"protocol": 1`）を付け、scriptorium と worker の食い違いを検出する
- 1 回の実行で worker を 1 度だけ起動し、すべてのファイルのページを順に渡す（モデルの読み込みは重いため）。
  ページは worker が読んでいる間に、次の数枚を裏で描いておき、読み終わったら消す（一時フォルダに画像をためない）。
  worker を複数にすると（`--jobs`）、それぞれが次のページを取る（§14）
- worker が異常終了したら、そのとき読んでいたページを失敗として記録し、残りのページのために起動し直す。
  3 回続けて止まったら、そのエンジンはあきらめる（残りのページは読まないまま）
- 長い実行では、worker を 1,000 ページごと（`OcrConfig.worker_pages`）に起動し直す。ページごとに少しずつメモリを
  使うエンジンがあっても、使い切らないように（yomitoku の worker は、直す前は 1 ページに約 1.6MB 増えていた）
- 起動できないとき（ライブラリが入っていない・指定した装置がないなど）は、そのエンジンの残りのページを読まないまま次のエンジンへ
- 3.14 で動くエンジン（ocrmac など）も、形をそろえるため同じく worker にする
- worker の起動には、使う側のプログラムの環境の変数（`VIRTUAL_ENV`・`UV_PROJECT_ENVIRONMENT`・`PYTHONPATH`・
  `PYTHONHOME`）を渡さない（worker の uv・Python がその環境を使わないように）。`PYTHONIOENCODING=utf-8`
  を付ける（Windows でも日本語のエラーをログに書けるように）

### エンジンの形

エンジンごとの違いは、**worker の中だけ**に置く。worker の `worker.py` は、エンジンを起動する関数と、1 ページを
読む関数を持つクラスを書き、`ocr_worker.serve` に渡すだけ:

```python
class YomiToku:
    name = "yomitoku"

    def __init__(self, device: str, options: dict[str, Any]) -> None:
        """モデルを読み込む。device は auto・cpu・cuda・mps。"""
        self.device = ocr_worker.pick_device(device, ocr_worker.torch_devices())
        self.version = ...
        self.model = ...

    def read(self, image: Path) -> ocr_worker.Result:
        """1 ページの画像を読む。座標は画像の画素（左上から）で返す。"""


if __name__ == "__main__":
    ocr_worker.serve(YomiToku)
```

scriptorium の側は、エンジンの一覧（`engines.py`）に 1 行ずつ持つだけ:

```python
ENGINES = (
    Engine("yomitoku", "yomitoku", "YomiToku: ..."),
    Engine("yomitoku-lite", "yomitoku", "...", options={"lite": True}),
    Engine("apple-vision", "apple_vision", "...", platforms=("darwin",)),
    ...,
)
```

- 名前（`--engine` で選ぶ）・worker のフォルダ・説明・動く OS・worker に渡す設定（`options`）・改訂番号（`revision`）
- 1 つの worker を、設定を変えて複数のエンジンとして使える（`yomitoku` と `yomitoku-lite`）
- **新しいエンジンを足すのは、`workers/` にフォルダを 1 つ（`pyproject.toml`・`worker.py`）と、一覧に 1 行。**
  worker の起動・やりとり・異常終了の扱い・座標の変換は共通
- worker の結果の変換を直したら（読む中身が変わったら）、`revision` を上げる。保存のキーが変わり、読み直しになる

### 装置（GPU）の選び方

- `--device` か設定（`OcrConfig.device`）: `auto`（既定）・`cpu`・`cuda`・`mps`
- 選ぶのは worker（`ocr_worker.pick_device`）。`auto` は、そのエンジンが使えるものの中から CUDA → MPS → CPU の順
- 指定した装置が使えないときは、黙って CPU にせず失敗させる（`auto` のときだけ自動で選ぶ）
- 使った装置は、各ページの結果に記録する（保存のキーには含めない。GPU の有無で読み直しにならないように）
- Apple Vision は装置を自分で選ぶので、`auto`（と `mps`）だけ受け付け、装置は `apple` と記録する

### 使うエンジンの選び方

- `ocr run` の `--engine`（複数指定できる）か、設定（`OcrConfig.engines`、カンマ区切り）で選ぶ
- 複数指定したときは、**全部で順に読む**（比較・照合のため）
- 使えないエンジン（OS が違う・worker がない）は、理由を表示して飛ばす（終了コード 1）
- `ocr list-engines` で、エンジンの一覧と、この環境で使えるか（入っているか）を表示する

## 4. 共通の結果の形（`models.py`）

worker は、文字の塊ごとに画素の箱（左上が原点）と確からしさを返す。scriptorium はそれを、`pdf.Word` と同じ
単位と向き（ポイント、`top` はページ上端から）に直して保存する。

```python
@dataclass(frozen=True)
class OcrWord:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    confidence: float | None = None  # 0〜1。出さないエンジンは None


@dataclass(frozen=True)
class OcrPage:
    page: int  # 1 始まり
    width: float  # ポイント
    height: float
    dpi: float  # エンジンが読んだ画像の解像度
    words: list[OcrWord]
```

- **単位はエンジンが返すまとまりのまま**（yomitoku・PaddleOCR・NDLOCR-Lite は行や文字列の塊、Tesseract・Windows の
  OCR は単語。日本語では 1〜2 文字のことが多い）。pdfplumber の日本語の「単語」も、文字の間の隙間で区切った塊なので、
  PDF の文字の層を位置で読む処理の考え方はそのまま使える
- 斜めの文字は、四隅を囲む箱にする。四隅そのものは、各エンジンの追加情報（`extra`）に残す
- 表の構造（yomitoku のセル・行・列）や段落、行の番号などは、エンジンごとの追加情報として保存だけしておく。
  使い道がはっきりしたら共通の形に入れる（表・読む順・縦書きは、使う側で要るようになったら入れる）
- 確からしさの意味はエンジンごとに違う（NDLOCR-Lite は「行を見つけた」確からしさで、読み取りの確からしさではない）

## 5. 保存と再開（`store.py`）

保存の置き方・各ファイルの形・保存のキー（`RunKey`）の決まりは `spec.ja.md` にまとめた（変えるときの決まりも）。

```text
<ocr_dir>/
  <ファイルの SHA-256>/
    source.json                 … ファイルの名前（最後に読んだときの）と、各ページの判定・解像度
    <エンジン>-<RunKey のハッシュ>/
      run.json                  … RunKey（エンジン・設定・改訂番号・解像度の範囲・やりとりの版・向きの判定）
      page-0001.json            … そのページの OcrPage・エンジンの名前と版とモデルと装置・かかった時間・追加情報
      page-0002.failed.json     … 読めなかったページと、その理由（読めたら消える）
    orient-<エンジン>.json        … `--orient` で判定した各ページの向き（回す角度・確からしさ）
    orient-<エンジン>.lock        … そのファイルを書き直す間のロック（同時に動く実行のため）
  logs/<日時>-<プロセス ID>-<エンジン>.log … worker が書いたもの（実行ごと）
  tmp/                          … worker に渡すページの画像（読み終わったら消す）
```

- **保存のキー**: ファイルの中身（SHA-256）＋ `RunKey`（エンジン名・`options`・`revision`・解像度の範囲・やりとりの版）。
  どれかが変われば、別の結果として読み直す
  - ファイル名はキーにしない（名前を変えたり移したりしても読み直さない）
  - 装置（CPU/GPU）はキーに含めず、記録だけする。装置を `auto` 以外にして読んだとき、別の装置で読んだページが
    あれば、その数を示して `--reread` を案内する（装置を変えたのに読み直していないと気づけるように）
  - **ライブラリ・モデルの版もキーに含めない。** 版は worker を起動するまでわからないため。版は各ページに記録する。
    版を上げて読み直したいときは `--reread` を使う
- **途中で止めても再開できる**: ページごとに一時ファイルへ書いてから名前を変える。再実行すると、保存済みのページを飛ばす
  （失敗したページは読み直す）
- 結果には読んだ書類の文字が入るので、Git の管理外に置く（場所は使う側が `OcrConfig.ocr_dir` で渡す）
- **Windows のパスの長さ**: 長いパスを有効にしていない Windows では、パスは 259 文字まで。ページのファイルのパスは
  `ocr_dir` より 120 文字ほど長い（SHA-256 とエンジンのフォルダの名前）。`ocr run` は読む前に一番長くなるパス
  （書いている途中の失敗の記録）の長さを確かめ、超えるなら、`ocr_dir` を何文字までにすればよいかと、長いパスを
  有効にする方法を示して、終了コード 2 で止まる（読んでから保存に失敗しないように）
- **掃除（`ocr remove-missing`・`remove-engine`・`remove-logs`、済み）**: もう無いファイル（消したスキャン）の結果・
  指定したエンジンの結果・ログと、止まった実行の一時ファイル（1 日触られていないもの）を、まず一覧し、`--apply` で
  消す（`cleanup.py`）。読んだ文字は書類の中身なので、スキャンを消したら結果も消す
  - `ocr run` が、読んだファイルの絶対パスを `source.json` の `paths` に記録する（読み直さないファイルも、結果が
    あれば記録する）。`remove-missing` は、記録したパスのどれにも同じ中身のファイルが無ければ消えたとみなすので、
    フォルダを渡さなくてよい（以前はフォルダを渡させていて、渡し忘れたフォルダのものまで消えたとみなしていた）
  - 記録したパスのフォルダごと無いもの（外付けドライブのつなぎ忘れ）は残す（`--missing-dirs-too` で消す）
  - パスを記録する前に読んだものは、渡したフォルダの下のすべてのファイル（フォルダへのリンクの先も）の中身で見る
    （`ocr run` はファイルを直接渡せば拡張子によらず読むので）。フォルダを渡さなければ残す

## 6. コマンド（`commands.py`）

`myapp ocr` は、コマンドを付けたプログラムの例。単独のコマンドでは、同じものを `ocr` を付けずに動かす
（`uv run scriptorium list-engines` など。§12）。

```bash
myapp ocr list-engines                             # エンジンの一覧と、使えるか
myapp ocr check-engines                            # 入っているエンジンに架空のページを読ませて確かめる
myapp ocr join-scans data/halves -o data/scan      # 2 ページに分けて読んだ書類を 1 枚につなぐ
myapp ocr run data/scan -e yomitoku                # フォルダ以下のスキャンを全部
myapp ocr run data/scan -e yomitoku -e tesseract   # 複数のエンジンで順に
myapp ocr run data/scan/a.pdf -e yomitoku --device cpu
myapp ocr status data/scan                         # どのページを読んだか（エンジンごと）
myapp ocr dump-text data/scan -e yomitoku -o data/ocr_words.csv
myapp ocr make-samples data/benchmark --count 2    # 架空の書類と正解を作る
myapp ocr score-engines data/benchmark             # 読んだエンジンを正解と比べて採点する
myapp ocr run data/benchmark -e all                # 環境を作ってあるエンジン全部で
myapp ocr score-engines data/benchmark --markdown  # 結果の表を Markdown で
myapp ocr score-engines data/benchmark --agreement  # 2 つずつ、読み違いが食い違いに出るか
myapp ocr run data/scan -e yomitoku --orient tesseract  # 向きを判定し、正しい向きにして読む
myapp ocr diff-engines data/scan -e yomitoku -e paddleocr  # エンジン同士の食い違い
myapp ocr remove-missing                           # 消したスキャンの結果を一覧（--apply で消す）
```

- `run`: 1 ページ読むごとに、進み具合（何枚目か・ファイル・ページ・文字の塊の数・時間）を表示する。1 ページの失敗で
  全体を止めない。読めなかったページ・開けなかったファイル・起動できなかったエンジンがあれば終了コード 1
  - `--force`: 文字の層のあるページも読む。`--reread`: 読んだページも読み直す
  - パスワード付きの PDF は `--password` か環境変数（変数の名前は使う側が `ProgramNames.password_variable` で渡す）
- `dump-text`: PDF の文字の書き出し（`pdf.write_dump`）と同じ形（CSV は BOM 付き UTF-8）に、`engine`・`dpi`・`confidence` を足したもの。CSV では、表計算ソフトが式と取る文字（`=`・`+`・`-`・`@`）で始まる値は、数だけのものを除き、先頭に `'` を付ける。
  エンジンの追加情報は入れない（共有する前に個人情報を消すファイルなので）
- バックグラウンドでの放置は OS の機能を使う（`engine-setup.ja.md` に例を書いた）。止めても続きから読める

## 7. 結果の使い方

**OCR を動かすのは `ocr run` だけ。** ほかは保存した結果を読むだけにし、OCR のライブラリを読み込まない。

- 使う側のプログラムは、保存した結果を `Store` で読む（`Store.load` はキーとページを指定して、`Store.readings` は
  どのエンジン・設定のものも全部）。保存のキーは `ocr run` と同じ設定から `RunKey.of` で作る
- 位置は PDF の文字の層の語（`pdf.Word`）と同じ単位と向きなので、文字の層を位置で読む処理にそのまま渡せる
- 円の金額の欄は `amounts.read_yen` で読める（3 桁区切りのピリオドを救う）。どの欄が円の金額かは、使う側が知っている
- 2 つのエンジンの食い違いは、取り込む前に `ocr diff-engines` で人が見る。取り込みの側は、主のエンジンの結果を読み、
  書類の中の合計との検算に任せる（§8）

## 8. 精度の確かめ方

### 正解との比較（エンジンを選ぶとき、済み）

`ocr make-samples` で架空の書類と正解を作り、`ocr run` で読ませ、`ocr score-engines` で採点する。対応のさせ方・表記の
そろえ方・指標は `benchmark.ja.md` のとおり。

金額は、完全一致のほかに「値の一致」も数える（`compare.LineScore.same_value`）: 読んだものを円の金額として読み
（`amounts.read_yen`: `¥`・`円`・カンマ・マイナスの記号をそろえ、3 桁ごとのピリオドを 3 桁区切りとみなす）、
正解と同じ値か。取り込みで円の金額の欄を読むときも `read_yen` を使う想定（どの欄が円の金額かは取り込みの処理が
知っている。小数のある欄・日付には使わない）。

Tesseract（この環境、Linux の CPU）で通した結果は `benchmark-results.ja.md`。180 度回ったページはまったく
読めず（`17-06-28` を `82-90-L1` と読む）、前処理（向きの判定）が要ることがはっきりした。

### エンジン同士の照合（`ocr diff-engines`、済み）

正解のない書類（実データ）で、同じページの複数のエンジンの結果を比べる（`diff.py`）。

```bash
myapp ocr diff-engines data/scan -e yomitoku -e paddleocr
```

- エンジンごとに塊の切り方が違うので、**位置でまとめる**: 違うエンジンの塊どうしが、小さいほうの半分以上重なって
  いれば同じまとまりにする（つながっているものも全部）。まとまりごとに、各エンジンの塊を読んだ順につなぐ
- 表記をそろえて比べる（`compare.normalize`。数字のあるまとまりは金額のそろえ方: `¥`・`円`・カンマを除き、マイナスの
  記号を 1 つに）
- 数字のあるまとまりのうち、食い違うもの・一部のエンジンしか読まなかったものを、ページと位置（ポイント）付きで一覧する
  （`--all` で数字のないものも）。最後に、数字のまとまりがいくつ一致したかを出す
- `--digits`: 数字のあるまとまりを、数字と、値を変える符号（マイナス・小数点）だけで比べる（`diff.digits`。金額の
  そろえ方の後なので、`¥` や 3 桁区切りのカンマは見ない）。数字を含む文の、数字以外の字の違い（`〒` を `デ`、住所の
  漢字）を出さないため。架空の書類（yomitoku-lite と PaddleOCR、向きの判定あり、256 ページ）では、一覧が 138 → 35 に
  なり、どちらかが読み違えた金額・日付 25 件はすべて残った。向きの判定なし（1,600 ページ）では、値の違う読み違いのうち、
  逆さまのページで `▲` を `A` と読んだ金額 6 件（もう一方は `▲` を落とす）と、`令和` を `今和` と読んだ日付 6 件が
  見えなくなった（ほかに見えなくなったのは `¥` を `木`・`*` と読んだもので、数字は合っている）。向きの判定を付けて
  読んだものに使う。向きの判定ありの 1,600 ページでは一覧が 940 → 309 になり、主の yomitoku-lite の読み違い（金額 81 件）
  はすべて残った。見えなくなったのは PaddleOCR だけの誤り（`令和` を `今和` と読んだ日付 6 件など。元号の字は比べない）
- `--digits` は、3 桁ごとのピリオドを 3 桁区切りとみなす（`amounts.without_thousands_periods`。yomitoku が `194,800` を
  `194.800` と読むため。値は同じ）。これで一覧は向きの判定ありの 256 ページで 35 → 24、1,600 ページで 309 → 227 になり、
  主の yomitoku-lite の値の読み違い（1,600 ページで金額 7 件）はすべて残った。`29.80` のような小数・`2026.10.09` の
  ような日付はそのまま比べる。`1.500` のような 3 桁の小数は 3 桁区切りとみなされる
- 架空の書類を Tesseract が前処理なし・ありで読んだものを比べると、きれいなページでは食い違いは 0 で、影や傾きの
  あるページでは前処理なしの読み落とし・読み違いが一覧された
- 多数決で 1 つの結果を作るか（エンジンが 3 つ以上のとき）・確からしさの低い所も一覧するかは、実データで比べてから決める

### 取り込み時の検算

書類の中の合計と明細の和が合うかなどを、使う側の取り込み処理で確かめる。OCR の読み違いは
ここで見つかることが多い。

## 9. エンジン

※ 2026 年 10 月の時点で、各ライブラリの配布物（PyPI のパッケージ）のソースで確かめた内容。

| エンジン（`--engine`） | worker | 特徴 | 装置 | Python | ライセンス |
|---|---|---|---|---|---|
| `yomitoku`・`yomitoku-lite` | `yomitoku` | yomitoku 0.15。日本語に特化。文字・表・レイアウトの解析まで。最有力 | CUDA・MPS・CPU（lite は CPU 向けの軽いモデル） | 3.10〜3.13 | CC BY-NC-SA 4.0（個人利用は可） |
| `paddleocr` | `paddleocr` | PaddleOCR 3.7、PP-OCRv6（1 つのモデルで日本語・中国語・英語など）。比較の相手 | CUDA（Windows）・CPU | 3.10〜3.13 | Apache-2.0 |
| `ndlocr-lite` | `ndlocr_lite` | 国立国会図書館。日本語の資料向け。ONNX Runtime で CPU でも軽い | CPU | 3.10〜3.13 | CC BY 4.0 |
| `apple-vision` | `apple_vision` | macOS の Vision（ocrmac 1.0 経由）。速い（1 ページ 0.4 秒）が、マイナスの記号（`▲`・`△`）を読めず金額 94.8%（`benchmark-results.ja.md` の「Mac」） | Apple が選ぶ | 3.10 以上 | OS の機能（ocrmac は MIT） |
| `windows-ocr` | `windows_ocr` | Windows の OCR（winocr 経由）。確からしさはない | CPU | 3.10 以上 | OS の機能 |
| `tesseract` | `tesseract` | 精度の基準にする最低限のもの。日本語は弱い。本体は別に入れる | CPU | 3.10 以上 | Apache-2.0 |

- yomitoku・PaddleOCR のモデルは、初回にインターネット（Hugging Face・PaddlePaddle の配布元）から取ってくる
  （画像は送らない）。NDLOCR-Lite はモデルをリポジトリに含む
- NDLOCR-Lite は、worker の起動時にモデル（そのコマンドが読み込むもの、コマンドの既定の設定で）を 1 度だけ読み込み、
  ページごとには、コマンドが画像 1 枚を読むのと同じ処理（`ocr` モジュールの関数）を呼ぶ。ページごとにコマンドを起動する
  と毎回モデルを読み込み、1 ページ 9 秒ほどかかる（今は 1 秒足らず）。PyPI にないので、GitHub のリポジトリから入れる
- 文書向けの言語モデル（PaddleOCR-VL・Qwen-VL 系など）は、数字を作り出してしまうことがあり、座標も弱い。
  主役にはせず、照合役（食い違った箇所だけを読ませる、など）として、ほかのエンジンを比べた後で検討する
- RapidOCR（PaddleOCR のモデルを ONNX で動かす）は、PaddleOCR と同じモデルなので後回し

## 10. 依存の分け方

- **scriptorium の依存は少なくする。** ページを画像にする部分（pypdfium2・Pillow）は、pdfplumber も使っているもの
  （pypdfium2 は型情報がないので、mypy の設定で `ignore_missing_imports` にした）
- OCR のライブラリは、エンジンごとの worker の環境（`workers/<worker>/pyproject.toml`）にだけ入れる
  - OS 専用のもの（Apple Vision・Windows の OCR）は、`[tool.uv] environments` で OS を限る
- **Windows の GPU**: Windows では、yomitoku の worker は PyTorch の CUDA 版（`download.pytorch.org/whl/cu130`。
  CUDA 12.8 版は PyTorch 2.11 までで、脆弱性の直った版が無いため）を、
  PaddleOCR の worker は `paddlepaddle-gpu`（PaddlePaddle の配布元の `cu126`）を入れる。macOS・Linux は PyPI の
  通常版（Mac の PyTorch は MPS を使える）。GPU のない Windows で PaddleOCR を使うなら、`pyproject.toml` の
  `paddlepaddle-gpu` の行を消し、`paddlepaddle` の条件を外す
- **NDLOCR-Lite の依存**: NDLOCR-Lite は依存の版をすべて固定していて（`requirements.txt`）、脆弱性の直った版に
  上がらない。worker の `pyproject.toml` の `override-dependencies` で、そのうち脆弱性の知られたもの（Pillow・lxml・
  protobuf・pypdf・oauthlib）を、同じメジャー版の直った版に上げる。読んだ結果が変わらないことは、架空の書類 64 ページで
  確かめた（読んだ文字・位置・確からしさが同じ）
  - Windows の `paddlepaddle-gpu` は CUDA の部品（cuDNN・cuBLAS など）を含まず、名前で（PATH から）読み込む。
    別に入れた CUDA は cuDNN がなかったり版が古かったりするので、NVIDIA が
    PyPI で配る部品（`nvidia-cudnn-cu12` など）を worker の環境に入れ、worker が起動時にその場所を PATH の先頭に足す
- 各 worker の `uv.lock` はコミットする
- NDLOCR-Lite は 1.2.3 に固定する（1.3.0 以降は、配布の設定から `pdf_output_utils`・`version` が抜けていて、入れた
  コマンドが起動できない）。直った版が出たら上げる
- CI では worker の環境を入れない。worker のコードは ruff だけで検査する（mypy は、テストが読み込む
  `ocr_worker.py` だけを検査する）

## 11. モジュールの置き場所

```text
src/scriptorium/
  models.py        … OcrWord・OcrPage・EngineInfo・StoredPage
  raster.py        … どのページを読むか（PagePlan）、ページを画像にする、90 度単位で回す
  preprocess.py    … 前処理（照明のむら・傾きの補正）
  split.py         … 何枚も並べたスキャンから 1 枚ずつ切り出す（`ocr split-scans`）
  join.py          … 2 ページに分けて読んだ書類を 1 枚につなぐ（`ocr join-scans`）
  engines.py       … エンジンの一覧
  worker.py        … worker の起動・やりとり・結果の読み込み
  store.py         … 保存・読み込み・保存のキー（RunKey）
  runner.py        … `ocr run` の流れ（ファイルを探す・読むページ・再開・失敗の記録・worker の再起動）
  dump.py          … `ocr dump-text` の書き出し
  check.py         … `ocr check-engines`（エンジンを起動し、渡された架空の書類を 1 枚読ませる）
  samples.py       … 架空の書類の描き方・劣化・正解と書き出し（書類そのものは使う側が渡す）
  compare.py       … 正解との比較の採点（`ocr score-engines`）
  diff.py          … エンジン同士の照合（`ocr diff-engines`）
  amounts.py       … 円の金額の読み方（`read_yen`）と、マイナス・¥ のそろえ方
  cleanup.py       … 要らなくなった結果の掃除（`ocr remove-*`）
  images.py        … ページで一番大きい画像（PageImage・largest_image）
  files.py         … ファイルを探す・保存するパスの書き方
  pdf.py           … PDF の文字の層の語と位置（`extract_pdf`）と、その書き出し（`write_dump`）
  commands.py      … `ocr` のコマンド（`make_app` が Typer のアプリにして返す。設定は読まず、使う側が渡す）
  config.py        … コマンドが使う設定（OcrConfig。設定の値と出どころ、架空の書類）
  fictional.py     … 架空の書類（レシート・明細・請求書・通帳）。`scriptorium` コマンドとテストが使う
  cli.py           … 単独で動く `scriptorium` コマンド（設定は `SCRIPTORIUM_*` と `.env`）
tests/ocr_app.py   … `make_app` を使うプログラムの例（テストで使う。設定は `TEST_OCR_*`）

workers/
  ocr_worker.py             … worker の共通部分（やりとり・装置の選び方）
  ruff.toml                 … worker を Python 3.10 として検査する
  <worker>/pyproject.toml   … Python の版・依存
  <worker>/uv.lock
  <worker>/worker.py        … エンジンを起動し、1 ページを読む
```

- scriptorium は使う側のプログラムを読み込まない。設定は使う側が `OcrConfig`・`ProgramNames` で渡す（`commands.make_app`）。
  使う側の取り込みで保存のキーを作るのも、使う側（`RunKey.of` に設定を渡す）

## 12. 設定（`OcrConfig`・`ProgramNames`）

コマンドは設定を自分で読まない。使う側のプログラムが、自分の設定（環境変数・`.env` など）から `OcrConfig` を作って
渡す（コマンドを実行するたびに作り直す）。

| 項目 | 既定 | 内容 |
|---|---|---|
| `ocr_dir` | （必須） | 結果の保存先 |
| `workers_dir` | （必須） | worker のフォルダ（このリポジトリの `workers/`） |
| `documents`・`check_document` | （必須） | `ocr make-samples` で作る架空の書類と、`ocr check-engines` で読ませる 1 つ |
| `engines` | （なし） | `--engine` を付けないときのエンジン（カンマ区切り） |
| `device` | `auto` | `auto`・`cpu`・`cuda`・`mps` |
| `min_dpi`・`max_dpi` | `300`・`600` | スキャンのページを描き直す解像度の範囲 |
| `worker_pages` | `1000` | worker をこのページ数ごとに起動し直す（`0` で起動し直さない） |
| `jobs` | `None`（auto） | エンジンごとに同時に読む worker の数。`None` はマシンに合わせて決める（§14） |
| `orient` | （なし） | `--orient` を付けないときの、向きを判定するエンジン（`tesseract`） |
| `orient_min_confidence` | `5` | これより確からしさの低い判定では回さない |
| `preprocess` | （なし） | `--preprocess` を付けないときの前処理（`flatten,deskew` など） |
| `origins` | （なし） | `engines`・`device`・`orient`・`preprocess`・`jobs`・`workers_dir` の出どころ（「`MYAPP_OCR_ENGINES` in .env」など）。各コマンドが最初に表示する（`workers_dir` は `..` の無い絶対パスにして） |

`ProgramNames` は、ヘルプとメッセージに出す、使う側のコマンド名（`command`、例: `myapp ocr`）・設定の環境変数の頭
（`settings_prefix`、例: `MYAPP_OCR_`）・PDF のパスワードの環境変数の名前（`password_variable`、例: `MYAPP_PDF_PASSWORD`）。

### 単独で動くコマンド（`cli.py`）

`scriptorium` コマンドは、`make_app` のコマンドを最上位に置いた（`scriptorium run` など）、自分の設定を読む
プログラム。scriptorium を試す人や、エンジンを並べて比べたい人のためのもので、clone の中で `uv run scriptorium` と
動かす（パッケージだけを入れる使い方は考えない）。

- 設定は `SCRIPTORIUM_<項目>`（`DIR`・`WORKERS_DIR`・`ENGINES`・`DEVICE`・`ORIENT`・`ORIENT_MIN_CONFIDENCE`・
  `PREPROCESS`・`MIN_DPI`・`MAX_DPI`・`WORKER_PAGES`・`JOBS`）と `SCRIPTORIUM_PDF_PASSWORD`。一覧と既定は README
- 環境変数から読み、無ければ作業フォルダの `.env` から読む。出どころは「`SCRIPTORIUM_ENGINES`」「`SCRIPTORIUM_ENGINES in .env`」
  と表示する。空の値は、無いのと同じ
- `.env` は `NAME=VALUE` の行（前に `export ` があってもよい）。`#` で始まる行と空の行は飛ばす。値は引用符で囲めば
  そのまま、囲まなければ ` #` から後はコメント。依存を増やさないよう、自分で読む
- 知らない名前（綴りの誤り）・使えない値（`DEVICE=gpu`、数でない解像度）・読めない `.env` の行は、どれと何が
  いけないかを示して、終了コード 2 で終わる
- 保存先の既定は作業フォルダの `data/ocr`（`.gitignore` の `data/`）、worker の既定は clone の `workers/`
  （`uv sync` はパッケージを clone のまま入れる（editable）ので、`cli.py` の場所からわかる）
- パスワードはコマンドのオプションが環境変数から読む（Typer）ので、`.env` にあれば、起動するときに環境変数に入れる
- 架空の書類は `fictional.py` のもの（使う側のプログラムも使える）

## 13. テスト

- **テストデータは架空の値で作る**（CONTRIBUTING.md）。`tests/ocr_files.py` で、白地に黒い四角を描いた「スキャン」と、
  それを貼った PDF（回転の指定・パスワード付きも）を作る。四角の位置で、描き直しと座標の変換を確かめる
- `tests/ocr_app.py`: `make_app` を使う小さなプログラム（設定は `TEST_OCR_*`）。コマンドのテストはこれで動かす。
  `scriptorium` コマンドの設定の読み方は `tests/unit/test_cli.py`
- `tests/ocr_fake_worker.py`: 本物の `ocr_worker.py` で話す、テスト用の worker。決まった箱を返す・指定したページで
  失敗する・異常終了する・起動に失敗する・標準出力に余計なものを書く、を変数 `FAKE_OCR` で指示する。テストを
  動かしている Python で起動する（uv を使わない）
- 確かめていること: ページの選び方と解像度、回転したページ・EXIF の向き・複数ページの TIFF、座標の変換、保存と
  再開、失敗したページの読み直し、worker の再起動とあきらめ、起動できないとき、ログ、保存のキー、CLI の各コマンド
- 本物のエンジンは CI では動かさない（手元で `engine-setup.ja.md` の手順で確かめる）

## 14. 速さ

`-e all` はエンジンを 1 つずつ順に動かす。1 つのエンジンの中では、次のページを先に描いておき、エンジンによっては
worker を複数にする。考えた改善と、その結果（測った値は [測った結果](benchmark-results.ja.md) の「Linux: 速さ」
「Windows: 速さ」）:

1. **NDLOCR-Lite のモデルを読み込んだままにする（済み）**: ページごとに `ndlocr-lite` コマンドを起動し、毎回モデルを
   読み込んでいたのを、worker の中でモデルを 1 度だけ読み込んで使い回すようにした。Windows の CPU で 1 ページ
   9.2 秒 → 0.7 秒（`ocr-trial` の 64 ページ。読んだ内容は前と同じ）。代わりに worker の起動が 7 秒ほどになった
2. **ページを描くのと読むのを重ねる**: 次のページを、エンジンが今のページを読んでいる間に描いておく（先読み）。
   GPU で 1 ページ 1 秒前後のエンジンでは、描いて PNG に保存する時間が目立つ。PNG の圧縮は軽くした（`compress_level=1`、
   済み）。先読みも済み: 描くのは 1 ページを読む時間の 1〜5 割なので、1.2〜2 倍ほど速くなる（読むのが速い
   エンジンほど、解像度が高いほど効く）
3. **エンジンを同時に動かす**: `-e all` で、GPU のエンジンと CPU のエンジンを同時に動かす（取り合わない）。
   入れない: 互いの遅れは小さいが、Tesseract を複数で動かすようにした後は、全体の時間がほとんど縮まない
4. **worker を複数起動して、ページを分け合う（済み、`--jobs`）**: 架空の書類で測ると、エンジンは 3 つに分かれた
   - **複数で速くなる**（`Engine.parallel`）: Tesseract（OpenMP のスレッドを 1 つにして、物理コアの数まで
     ほぼ比例して速くなる。論理コアまで増やしても 1 割ほど）、Windows の OCR、Apple Vision（Mac で測った。12 個で
     5.9 倍、worker 1 つ 1GB 以下）
   - **1 つで全コアを使うのがいちばん速い**: NDLOCR-Lite と、CPU で動かす YomiToku。複数にしても 1.3 倍まで、
     増やしすぎると遅くなる（CPU に空きがあっても。worker ごとにモデルの重みを持つので、メモリかキャッシュの
     取り合いと考えられる）。スレッドを絞って数を増やしても速くならない
   - **GPU 1 枚に 1 つ**: GPU で動かすエンジン。2 つにしても 1.4 倍まで、GPU のメモリが足りないと遅くなる

worker の数は `--jobs`（`OcrConfig.jobs`）で決める。既定の `auto` は、複数で速くなるエンジンだけ増やす（`jobs.py`）:
物理コアから 4 分の 1（少なくとも 1 つ）を残した数、ただし空きメモリから 4GB か 5 分の 1 を残して、エンジンの
目安（`Engine.memory`）で割った数まで。物理コアが 4 未満か、メモリが 8GB 未満の PC では 1 つ。物理コアの数と
空きメモリは psutil で知る。数を指定したら、そのとおりにする（速くならないエンジン・コアより多い数・メモリが
足りなさそうな数なら、警告する）。worker は低い優先度で動かすので、ほかのアプリが先に動く。

次のページは裏で描いておく: worker が 1 つならスレッド 1 つで、複数なら worker と同じ数のプロセスで（pdfium は
1 つのプロセスで同時に 1 ページしか描けないため。スレッドで並べても 1.4 倍ほどで止まる）。Windows の OCR は
読むのが描くのと同じくらい速く、worker を 2 つにすると、描くのが 1 つでは追いつかない。前処理の戻し方
（`preprocess.Back`）は、プロセスの間で渡せるよう、関数ではなくデータクラスにした。

並べて動かすには、保存も同時の書き込みに耐えるようにする（済み）。一時ファイルは書くたびに別の名前にし、
`orient-<engine>.json` はロックの中で読んで書き直す。Windows では、ほかのプロセスが開いているファイルを置き換え
られない（開くこともできない）ので、少し待ってやり直す。読めない `orient-<engine>.json` は空として扱い、向きを
判定し直す。ログの名前にはプロセス ID を入れる。同じページを 2 つの実行が両方とも読む無駄は残る。

別々の実行が同時に動くと、エンジンのスレッドも取り合う。Tesseract は OpenMP で全コアを使おうとし、2 つ同時に
動くと何十倍も遅くなる。1 つで動くときも、スレッドを増やしても速くならないので、worker は Tesseract を
1 スレッドで動かす（`OMP_THREAD_LIMIT=1`、設定されていればそのまま）。YomiToku は CUDA で動かすとき、PyTorch の
CPU のスレッドを 1 つにする（`OMP_NUM_THREADS` が設定されていればそのまま）。モデルは GPU で動き、CPU の
スレッドを増やしても速くならず、CPU を使うだけになる。
