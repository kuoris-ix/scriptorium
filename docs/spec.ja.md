# OCR の仕様（worker とのやりとり・保存の形・保存のキー）

[English](spec.md) | 日本語

scriptorium（`src/scriptorium`・`workers/`）のうち、**変えると手元の結果が読み直しになる所、または古い worker と
話せなくなる所**をまとめたもの。設計の考え方は `design.ja.md`、使い方は README。ここに書いたことは今のコードの
ふるまいで、食い違ったらコードが正しい（直すのはこの文書）。

ここに書いた形を変えるときは、§5 の決まりに従う。

## 1. worker とのやりとり（PROTOCOL 1）

エンジンは、使う側のプログラムとは別の環境・別のプロセスで動く（worker）。scriptorium 側は `worker.py`、worker 側の共通部分は
`workers/ocr_worker.py`。

### 起動

```text
uv run --project workers/<worker> python workers/<worker>/worker.py
```

- `uv` は環境変数 `UV`、なければ PATH から探す（Windows では作業フォルダは探さない）
- 使う側のプログラムの環境を指す変数（`VIRTUAL_ENV`・`UV_PROJECT_ENVIRONMENT`・`PYTHONHOME`・`PYTHONPATH`）と、
  PDF のパスワードを入れる変数（`ProgramNames.password_variable`）は渡さない（worker は画像しか読まない）。
  `PYTHONIOENCODING=utf-8` を付ける（Windows でも日本語をログに書けるように）
- worker の標準エラー出力は、そのままログ（`<ocr_dir>/logs/<日時>-<プロセス ID>-<エンジン>.log`）に書く

### scriptorium → worker（標準入力、1 行に JSON を 1 つ）

| 種類 | 形 | いつ |
|---|---|---|
| `start` | `{"type": "start", "protocol": 1, "device": "auto", "options": {...}}` | 最初に 1 回。`device` は `auto`・`cpu`・`cuda`・`mps`、`options` はエンジンの一覧（`engines.py`）の options |
| `page` | `{"type": "page", "id": "...", "image": "<PNG>", "output": "<JSON>"}` | 1 ページごと |
| `orient` | `{"type": "orient", "id": "...", "image": "<PNG>"}` | 向きの判定をするエンジンに（`--orient`） |

標準入力を閉じたら、worker は終わる。画像は scriptorium が描いた PNG（解像度の情報付き、名前は `page-<ページ>.png`）。

### worker → scriptorium（標準出力）

行頭に印 `@@scriptorium@@ `（最後に空白 1 つ）の付いた行だけがやりとりで、ほかの行（エンジンのライブラリが
表示したもの）はログに書くだけ。

| 種類 | 形 |
|---|---|
| `ready` | `{"type": "ready", "engine": {"name", "version", "model", "device"}}`（`start` への答え） |
| `done` | `{"type": "done", "id": "...", "seconds": ...}`（結果を `output` に書き終えた） |
| `oriented` | `{"type": "oriented", "id": "...", "rotate": 0/90/180/270, "confidence": ...}`（時計回りに回す角度と、エンジン自身の確からしさ） |
| `failed` | `{"type": "failed", "id": "...", "error": "..."}`（そのページを読めなかった・知らない種類） |
| `fatal` | `{"type": "fatal", "error": "..."}`（起動できない。この後 worker は終わる） |

`start` の `protocol` が違えば、worker は `fatal` で終わる。

### 結果のファイル（`page` の `output`）

```json
{"protocol": 1, "boxes": [{"text": "...", "x0": 0, "y0": 0, "x1": 0, "y1": 0, "confidence": 0.9}], "extra": {}}
```

- 位置は**画像の左上からの画素**。`confidence` は 0〜1、エンジンが出さなければ `null`
- `extra` はエンジンごとのもの（そのまま保存する）
- 丸ごと書くか、何も書かない（`<output>.part` に書いてから名前を変える）

### 失敗と起動し直し（scriptorium 側）

- `failed` のページは、失敗として保存する（§2 の `page-NNNN.failed.json`）。次の `ocr run` で読み直す
- worker が途中で終わったら、そのときのページを失敗にして、残りのために起動し直す。3 回続けて終わったら、そのエンジンを
  あきらめる
- `OcrConfig.worker_pages`（既定 1,000）ページごとに起動し直す
- worker が複数（`OcrConfig.jobs`・`--jobs`）なら、それぞれが次のページを取る。起動し直しと、続けて終わった回数は
  worker ごとに数える。すべての worker があきらめたら、そのエンジンをあきらめる
- worker は低い優先度で動かす（Windows は `BELOW_NORMAL_PRIORITY_CLASS`、Linux・macOS は nice 値を 10 上げる）。
  worker が起動するものも同じ優先度になる

## 2. 保存の置き方

```text
<ocr_dir>/                              `OcrConfig.ocr_dir`。読んだ書類の文字が入る
    <ファイルの SHA-256>/
        source.json                     ファイルの名前・ページ・読んだときの絶対パス
        <エンジン>-<RunKey のハッシュ>/     保存のキーごと（§3 の slug）
            run.json                    保存のキー（RunKey.as_dict）
            page-0001.json              読んだページ
            page-0002.failed.json       読めなかったページと、その理由
        orient-<エンジン>.json            各ページの向き（向きの判定をしたエンジンごと）
        orient-<エンジン>.lock            そのファイルを書き直す間、ロックする
    logs/<日時>-<プロセス ID>-<エンジン>.log   worker のログ
    tmp/                                worker に渡す画像の一時フォルダ（止めた実行のものが残りうる）
```

- ファイルは中身（SHA-256）で知る。名前を変えたり移したりしても読み直さない
- どのファイルも丸ごと書く（`<名前>.<乱数の 16 進 8 桁>.part` に書いてから名前を変える）。UTF-8、`ensure_ascii` なし、
  インデント 1
- 同じ保存先に、いくつかの実行が同時に保存してよい: 一時ファイルは書くたびに別の名前にする。`orient-<エンジン>.json` は
  `orient-<エンジン>.lock` をロックして（OS のファイルロック）書き直す。Windows では、ほかのプロセスが開いているファイルは
  10 秒まで待ってやり直す。読めない `orient-<エンジン>.json` は、ページごとに向きを判定し直す

| ファイル | 中身 |
|---|---|
| `source.json` | `{"name": 最後に読んだときの名前, "pages": [{"page", "width", "height", "dpi", "reason"}], "paths": [絶対パス]}`。`pages` は `raster.PagePlan`（`reason` は `scanned`・`image file`・`text layer` など） |
| `run.json` | §3 の `RunKey.as_dict()` |
| `page-NNNN.json` | `{"page", "width", "height", "dpi", "words": [{"text", "x0", "x1", "top", "bottom", "confidence"}], "engine": {"name", "version", "model", "device"}, "seconds", "extra"}`。位置は**ページの左上からのポイント**（PDF の語と同じ。画素から `ページの幅 / 画像の幅` を掛けて直したもの）、`dpi` はエンジンが読んだ画像の解像度 |
| `page-NNNN.failed.json` | `{"page", "error", "engine"}`（`engine` は起動できていれば上と同じ形、なければ `null`） |
| `orient-<エンジン>.json` | `{"<ページ>": {"rotate": 0/90/180/270, "confidence": ...}}` |

## 3. 保存のキー（RunKey）

読んだ結果が何によって決まるか。どれかが変わると、別の結果として読み直す（前の結果も残る）。

| 項目 | 中身 |
|---|---|
| `engine` | エンジンの名前 |
| `options` | エンジンの一覧の options（例: yomitoku-lite は `{"lite": true}`） |
| `revision` | エンジンの改訂番号（`Engine.revision`） |
| `min_dpi`・`max_dpi` | スキャンのページを描き直す解像度の範囲 |
| `protocol` | やりとりの版（`PROTOCOL`） |
| `orient`・`orient_min_confidence` | 向きを判定したエンジンと、回す下限。**向きの判定なしのときは書かない** |
| `preprocess`・`preprocess_revision` | 前処理の並びと、前処理の改訂番号（`preprocess.REVISION`）。**前処理なしのときは書かない** |

```python
slug = f"{engine}-{sha256(json.dumps(as_dict, sort_keys=True, ensure_ascii=True)).hexdigest()[:12]}"
```

- 向きの判定・前処理を足す前の鍵と同じ形になるよう、使わないときは項目そのものを書かない（足す前に読んだ結果が
  そのまま使える）
- **キーに入らないもの**: エンジンの版（worker が起動してからわかる。ページごとに `engine.version` として残す）、
  装置（GPU と CPU の結果は同じキーになる。分けるときは `ocr_dir` を分ける）、worker のプロジェクト名
- 全エンジンの、そのまま・向きの判定あり・向きの判定と前処理ありの `slug` は、テスト
  （`tests/unit/test_store.py` の `KEPT_IN`）で固定している

## 4. 正解のファイル（架空の書類）

`ocr make-samples` が書類のファイルの隣に書く `<ファイル名>.truth.json`:

```json
{"file": "receipt-1-300.png", "width": 226.77, "height": 405.35,
 "lines": [{"text": "¥1,234", "kind": "amount", "x0": 0, "x1": 0, "top": 0, "bottom": 0}]}
```

- 大きさと位置はポイント（ページの左上から）。`kind` は `amount`・`date`・`number`・`text`
- 同じ `--count` と `--seed` からは、同じ画像と正解ができる（バイトまで同じ。PDF は作るたびに変わる）。架空の書類で
  読んだ結果は、ファイルの中身で保存されるので、作り直しても同じなら読み直さない。書類の作り方を変えるときは、
  既にある書類と同じバイトになることを確かめる（同じでなければ、架空の書類の結果は全部読み直し）

## 5. 変えるときの決まり

| 変えるもの | 起きること | すること |
|---|---|---|
| エンジンの読み方（worker の出力の直し方・エンジンの設定） | 前の結果と混ざる | そのエンジンの `revision` を上げる（そのエンジンの全ページを読み直す） |
| エンジンの options | キーが変わる | 意図したときだけ。`KEPT_IN` を書き直す |
| 前処理のやり方（`flatten`・`deskew` の中身） | 前の結果と混ざる | `preprocess.REVISION` を上げる（前処理ありの全ページを読み直す） |
| やりとりの形（§1） | 古い worker と話せない | `PROTOCOL` を上げ、scriptorium 側と全 worker を同時に直す（全エンジンの全ページを読み直す）。避けられるなら、項目を足すだけにして古い形も受け付ける |
| 印（`@@scriptorium@@ `） | 古い worker と話せない（キーは変わらない） | scriptorium 側と全 worker を同時に直す。`PROTOCOL` は上げない |
| 鍵の書き方（`as_dict`・`slug`） | 全エンジンの全ページを読み直す | 避ける。項目を足すなら、使わないときは書かない形にする。`KEPT_IN` が守る |
| 保存のファイルの形（`page-NNNN.json` など） | 前に保存した結果を読めない | 読み込み（`store._stored_page` など）が古い形も読めるようにする |
| worker のプロジェクト名（`pyproject.toml` の `name`） | 何も起きない | — |

キーが変わる変更をしたら、`KEPT_IN` を新しい値に書き直し、PR に「読み直しになる」ことを書く。
