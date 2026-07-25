---
name: even-terminal
description: >-
  Even Realities G2 の Terminal Mode を使うために even-terminal を起動する。
  (1) Tailscale に接続し、(2) `even-terminal --tailscale --provider claude` を
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
- even-terminal はグローバル導入済み。起動コマンドは `even-terminal --tailscale --provider claude`（IP指定は不要。`--tailscale` を付けると出力に Tailscale 用URL・IP・トークンが出る）。

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
  - 起動し直す場合のみ、その 3456 を掴んでいるプロセスを停止してから次へ。
  - 起動し直さないなら、ここで終了（既存を使う）。
- 未使用ならそのまま次へ。

### 3. even-terminal をバックグラウンド起動

- **必ずバックグラウンドで**（run_in_background=true）次を実行する。前面実行すると出力を流し続けてブロックする:

  ```
  cd "$HOME" && export PATH="$PATH:/c/Program Files/Tailscale" && even-terminal --tailscale --provider claude
  ```

  - **ホームディレクトリで起動する**（`cd "$HOME"`）: even-terminal は CWD にログファイル（`even-terminal-<timestamp>.log`）を吐くので、プロジェクト内で起動するとリポジトリが汚れる。常にホームで起動してそれを避ける。ユーザーが特定プロジェクトで使いたいと明示した場合のみ、そのディレクトリで起動する。
  - **PATH に Tailscale を足すのが必須**: even-terminal は `--tailscale` 指定時に内部で `tailscale` コマンドを呼んで IPv4 を取得する。Git Bash の既定 PATH には `tailscale` が無く、足さないと `error: failed to get Tailscale IPv4 address` で落ちる（cmd.exe では PATH に入っているので直接動くが、Bash ツール経由では要追加）。
  - `even-terminal` が見つからなければ `even-terminal.cmd` を試す。
  - **CWD の意味**: even-terminal が起動する claude エージェントの作業ディレクトリ＝この実行時の CWD になる。既定はホーム。

### 4. 起動出力を取得して解析

- 4〜6 秒ほど待ってから、バックグラウンドプロセスの出力を読む（起動バナーが出るまで少し待つ）。
- 次の2値を抜き出す:
  - **ホストIP**: `Tailscale:  http://<IP>:3456` の `<IP>`。または接続URL行 `http://<IP>:3456?token=...&defaultProvider=claude` から。
  - **トークン**: `Full token: <token>` の `<token>`（32桁hex）。バナーの `Token: 054e4949...e4b7` は省略表示なので**使わない**。
- どちらかが取れない場合は数秒待ってもう一度出力を読む（起動が遅いことがある）。

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
- even-terminal はバックグラウンドで動き続ける（このセッション終了後も残る想定）。止めたいときは 3456 を掴むプロセスを終了する、と伝える。

## 注意

- even-terminal を前面実行しない（ブロックする）。必ずバックグラウンド。
- コマンドが見つからないときはフルパス／`.cmd` 版を試す（Windows のグローバル npm bin）。
- PC がスリープすると even-terminal も止まり外から繋げなくなる。外出中も使うなら電源接続時のスリープ抑制が別途必要（このスキルの範囲外）。
