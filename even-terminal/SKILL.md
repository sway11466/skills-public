---
name: even-terminal
description: >-
  Even Realities G2 の Terminal Mode を使うために even-terminal を起動する。
  (1) Tailscale に接続し、(2) `even-terminal --interface Tailscale --provider claude` を
  バックグラウンド起動し、(3) Even アプリ（even utility）に入力する「ホストIP」と
  「トークン」を、それぞれコピペしやすい形で返す。ユーザーが「even-terminal を起動」
  「G2 に繋ぐ」「ターミナルモードを立ち上げる」等と言ったときに使う。
---

# even-terminal 起動スキル

Even Realities G2 の Terminal Mode 用に even-terminal を起動し、接続情報（ホストIP・トークン）を返す。

## ゴール

最終的にユーザーへ返すのは次の2つ（Even アプリのホスト/トークン欄に入力する値）。それぞれ**単独の行でコードブロックに入れて、1タップでコピーできる形**にする。

- ホスト（`IP:ポート` 形式。ポートは常に `3456`。例 `100.103.35.66:3456`）── ポート番号まで含めるのが必須（IP だけでは繋がらない）。
- トークン（32桁hex。例 `054e49497635925a96bf81a120a5e4b7`）

## 前提

- Windows + Git Bash（Bash ツール）想定。
- even-terminal はグローバル導入済み。**推奨の起動コマンドは `even-terminal --interface Tailscale --provider claude`**（IP指定は不要。ネットワークインターフェイス名 `Tailscale` からアダプタのIPv4を直接取得する）。
- `--tailscale` フラグは使わないのが既定。こちらは内部で `tailscale ip -4` を `execSync` で呼ぶため、バックグラウンド起動時に失敗することがある（PATH に Tailscale を足しても、cmd.exe 経由でも駄目なケースを実測）。`--interface Tailscale` なら同じIP（実測 `100.103.35.66`）が確実に取れる。

## 手順

### 1. Tailscale 接続を確認・接続

- `tailscale status` を実行。
  - `tailscale` が見つからなければ `"$PROGRAMFILES/Tailscale/tailscale.exe" status` を試す。
- 出力が `Logged out` や未接続を示すなら `tailscale up` を実行して接続する。
  - 初回や認証切れだとブラウザでの対話認証が要る場合がある。そのときはユーザーにその旨を伝えて待つ。
- 既に接続済みならそのまま次へ。

### 2. 既存の even-terminal を確認

- ポート 3456 の使用状況を確認: `netstat -ano | grep 3456`
- 使用中なら even-terminal が**既に起動している**可能性が高い。実行中プロセスからトークンは取り直せないので、ユーザーに「既に起動しているようです。起動し直しますか？」と確認する。
  - ただし手順4のログ読み出しフォールバックが使えるなら、**再起動せずに既存プロセスの Full token を回収できる**（`even-terminal-*.log` の最新ファイルを見る）。停止する前にまずこれを試す。
  - 起動し直す場合のみ、その 3456 を掴んでいるプロセスを停止してから次へ。PID は `netstat -ano | grep 3456` の最終列。停止は PowerShell ツールで:

    ```
    Stop-Process -Id <PID> -Force
    ```
  - 起動し直さないなら、ここで終了（既存を使う）。
- 未使用ならそのまま次へ。

### 3. even-terminal をバックグラウンド起動

- **必ずバックグラウンドで**（run_in_background=true）次を実行する。前面実行すると出力を流し続けてブロックする:

  ```
  cd "$HOME" && even-terminal --interface Tailscale --provider claude
  ```

  - **`--tailscale` ではなく `--interface Tailscale` を使う**: `--tailscale` は内部で `tailscale ip -4` を `execSync` で呼ぶため、バックグラウンド起動だと `error: failed to get Tailscale IPv4 address` で落ちることがある（PATH に `/c/Program Files/Tailscale` を足しても、`cmd.exe /c` 経由にしても駄目だった実測あり）。`--interface Tailscale` は Windows のネットワークアダプタ「Tailscale」から直接IPv4を読むので外部コマンドに依存せず、同じIP（実測 `100.103.35.66`）が得られる。
  - **PATH に Tailscale を足す必要はない**（`--interface` 方式は `tailscale` コマンドを呼ばないため）。`--tailscale` をどうしても使う場合のみ `export PATH="$PATH:/c/Program Files/Tailscale"` を付ける。
  - **フォールバック順**: ① `--interface Tailscale` → ② それでも駄目なら `--tailscale`（PATH追加付き）→ ③ どちらも駄目なら `tailscale ip -4` で得たIPを直接指定する方式を検討。
  - **ホームディレクトリで起動する**（`cd "$HOME"`）: even-terminal は CWD にログファイル（`even-terminal-<timestamp>.log`）を吐くので、プロジェクト内で起動するとリポジトリが汚れる。常にホームで起動してそれを避ける。ユーザーが特定プロジェクトで使いたいと明示した場合のみ、そのディレクトリで起動する。ホームで起動していれば手順4のログ・フォールバックも効く。
  - `even-terminal` が見つからなければ `even-terminal.cmd` を試す。
  - **CWD の意味**: even-terminal が起動する claude エージェントの作業ディレクトリ＝この実行時の CWD になる。既定はホーム。

### 4. 起動出力を取得して解析

- 4〜6 秒ほど待ってから、バックグラウンドプロセスの出力を読む（起動バナーが出るまで少し待つ）。
- 次の2値を抜き出す:
  - **ホストIP**: `Tailscale:  http://<IP>:3456` の `<IP>`。または接続URL行 `http://<IP>:3456?token=...&defaultProvider=claude` から。
  - **トークン**: `Full token: <token>` の `<token>`（32桁hex）。バナーの `Token: 054e4949...e4b7` は省略表示なので**使わない**。
- どちらかが取れない場合は数秒待ってもう一度出力を読む（起動が遅いことがある）。

#### 4-b. バナーが標準出力に出ないときのフォールバック（ログから抜く）

バックグラウンド実行だとバナーが stdout に流れてこないことがある（プロセスは正常に起動していて 3456 も掴んでいるのに出力が空）。その場合は even-terminal が CWD に吐くログファイルから拾う。手順3の通りホーム（`$HOME` / `%USERPROFILE%`）で起動していれば、そこに `even-terminal-<timestamp>.log` がある。

- 最新ログを特定して両方を一気に抜く（Bash）:

  ```
  ls -t "$HOME"/even-terminal-*.log | head -1 | xargs grep -aE "Full token:|http://[0-9.]+:3456"
  ```

- PowerShell でも同じことができる:

  ```
  Get-ChildItem "$env:USERPROFILE\even-terminal-*.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | Select-String -Pattern "Full token:","http://[0-9.]+:3456"
  ```

- 注意点:
  - **必ず最新のログを見る**（`even-terminal-*.log` は起動ごとに増える）。古いログのトークンは今動いているプロセスのものではない。タイムスタンプが今回の起動時刻と合っているか確認する。
  - ここでも `Full token:` 行を使う（省略表示の `Token:` ではない）。
  - IP は `http://<IP>:3456` を含む行から取る。`100.x.x.x` なら Tailscale のIP。
  - ログにも出ていなければ、プロセスが起動途中か失敗している。手順2でポート3456の状況を再確認する。

### 5. 返却（コピペしやすく）

ユーザーへ次の形で返す。ホストとトークンをそれぞれ独立したコードブロックにする:

ホスト（`IP:ポート`）:

```
<抜き出したIP>:3456
```

トークン:

```
<抜き出したトークン>
```

- ホストは必ず `IP:3456` の形（ポート番号込み）で返す。IP だけでは Even アプリが繋がらないため、ポート `3456` を付けて1つのコードブロックにまとめる。
- この2つを Even アプリ（even utility）のホスト/トークン欄に入れると G2 に繋がる旨を一言添える。
- **トークンは実質シークレット**（このIP＋トークンを知る者は誰でもエージェントを操作できる）。第三者に共有しないよう注意を添える。
- even-terminal はバックグラウンドで動き続ける（このセッション終了後も残る想定）。止めたいときは 3456 を掴むプロセスを終了する、と伝える（下記の停止手順）。

## 注意

- even-terminal を前面実行しない（ブロックする）。必ずバックグラウンド。
- **起動フラグは `--interface Tailscale` が既定**。`--tailscale` は execSync 依存でバックグラウンド起動時に落ちうるため、フォールバック扱い。
- **プロセスの停止**: `netstat -ano | grep 3456` で PID を調べ、PowerShell で `Stop-Process -Id <PID> -Force`。Git Bash の `kill` は Windows ネイティブプロセスに効かないことがあるので、Stop-Process の方が確実。
- **ログファイルが溜まる**: 起動のたびにホームに `even-terminal-<timestamp>.log` が増える。トークンが平文で入っているので、不要になったものは削除してよい（削除前にユーザーに確認する）。
- コマンドが見つからないときはフルパス／`.cmd` 版を試す（Windows のグローバル npm bin）。
- PC がスリープすると even-terminal も止まり外から繋げなくなる。外出中も使うなら電源接続時のスリープ抑制が別途必要（このスキルの範囲外）。
