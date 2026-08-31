---
name: even-terminal
description: >-
  Even Realities G2 の Terminal Mode を使うために even-terminal を起動する。
  (1) Tailscale に接続し、(2) Claude Code の OAuth 認証を実リフレッシュで検証し、
  (3) `even-terminal --interface Tailscale --provider claude` を
  バックグラウンド起動し、(4) Even アプリ（even utility）に入力する「ホストIP」と
  「トークン」を、それぞれコピペしやすい形で返す。ユーザーが「even-terminal を起動」
  「G2 に繋ぐ」「ターミナルモードを立ち上げる」等と言ったときに使う。
  G2 から使っていて 401 / 認証エラーが出たときの切り分けにも使う。
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

### 2. Claude Code の認証を確認

even-terminal はプロンプトを受けるたびに Claude Code SDK のサブプロセスを起動し、`%USERPROFILE%\.claude\.credentials.json` の OAuth トークンを読む。ここが死んでいると、**even-terminal 自体は正常に動いてホスト/トークンも正しいのに、G2 からプロンプトを送った瞬間だけ 401 になる**（実測あり）。

`.credentials.json` にはトークンが2種類ある:

| フィールド | 中身 | 寿命 | 切れていたら |
|---|---|---|---|
| `expiresAt` | アクセストークン | **8時間** | **正常**。リフレッシュトークンで自動更新される |
| `refreshToken` | リフレッシュトークン | **約1か月** | ここで初めて `/login` が必要 |
| `refreshTokenExpiresAt` | 上の期限 | — | **存在しないことが多い**。判定に使わない |

**ファイルを読むだけでは「リフレッシュトークンが生きているか」は判定できない。** `refreshToken` に文字列が入っていることと、サーバがそれを受け付けることは別物。実測（2026-08-31）: `has refreshToken: True` かつ `refreshTokenExpiresAt` 欠損（＝旧手順なら「正常」判定）だったが、実際にはサーバ側で失効していて `/login` が必要だった。`claude auth status --json` も同じ理由で使えない — 返ってくる `loggedIn` / `email` / `orgName` は `~/.claude.json` の `oauthAccount` ローカルキャッシュと一致する値で、サーバ検証をしていない。

確実なのは**実際にリフレッシュを走らせてみること**だけ。以下の3段階で、コストを抑えつつ確実に判定する。

#### ① ファイル検査（ノーコスト）

```
$p = "$env:USERPROFILE\.claude\.credentials.json"; if (-not (Test-Path $p)) { "NG-LOGIN: no credentials file at $p" } else { $o = (Get-Content $p -Raw | ConvertFrom-Json).claudeAiOauth; if (-not $o.refreshToken) { "NG-LOGIN: refreshToken is empty" } else { $a = [DateTimeOffset]::FromUnixTimeMilliseconds([int64]$o.expiresAt).ToLocalTime(); if ($a -gt (Get-Date)) { "OK: access token valid until $a" } else { "PROBE: access token stale since $a" } } }
```

- **`NG-LOGIN:`** → 再ログイン案内へ（下記）。リフレッシュ失敗直後の状態はここで確実に捕まる（②の副作用で CLI がトークンを空にするため）
- **`OK:`** → 認証は確実に生きている。**何も案内せず**手順3へ
- **`PROBE:`** → ②へ

#### ② プローブ（`expiresAt` が過去のときだけ・API 1回・10秒程度）

```
claude -p "ok"
```

exit code で分岐する:

| 結果 | 意味 | 対応 |
|---|---|---|
| **exit 0** | リフレッシュ成功。`expiresAt` が8時間先に伸びる | 正常。**何も案内せず**手順3へ |
| **exit 1** + `could not be refreshed` / `OAuth session expired` | リフレッシュトークン失効 | 再ログイン案内へ（下記） |
| **exit 126** / `is not recognized` | **認証ではなく claude インストール破損** | ③へ |

**副作用（把握しておくこと）**: リフレッシュに失敗すると claude CLI が `.credentials.json` の accessToken / refreshToken を空にし `expiresAt` を 0 にする（実測）。プローブが壊したように見えるが、そのトークンは既に死んでいたので実害はない。むしろ次回は①の `NG-LOGIN` で一意に判定できるようになる。

#### ③ claude インストールが壊れていたとき

認証とは無関係。Claude Code の自動アップデータが `bin\claude.exe` を `claude.exe.old.<timestamp>` にリネームした後、新バイナリの配置に失敗すると起きる（実測 2026-08-27 に発生、`claude` が exit 126 の `is not recognized` になった）。Volta 管理下のグローバル導入だと再発しうる。

- 復旧: `volta install @anthropic-ai/claude-code`（最新版が入り直る。実測で復旧確認済み）
- 応急処置: `bin\claude.exe.old.<timestamp>` を `claude.exe` にリネームで戻す（旧バージョンのまま・ダウンロード不要）
- 復旧しても認証は別問題なので、①からやり直す

#### 再ログインの案内

ブラウザでの対話認証なのでこちらからは実行できない:

- PC のターミナル（PowerShell か Git Bash）で `claude` を起動 → `/login` → ブラウザ認証 → `/exit`。
- 再ログイン後、**even-terminal の再起動は原則不要**（プロンプトごとに新しいサブプロセスが認証情報を読み直す）。ホストもトークンも変わらない。
- ユーザーが「先に起動だけしておいて」と言う場合はそのまま手順3へ進み、「使う前に再ログインが必要」と明示して返す。

#### やってはいけないこと

- **`expiresAt` が過去なのを見ただけで「認証が切れています」と報告する。** アクセストークンは8時間で切れるので1日3回はそうなる。誤診した実績あり — 「expired」と報告した4分後に SDK が自動更新して期限が8時間先に伸びていた。ユーザーの体感「1か月ログイン不要」はリフレッシュトークンの寿命そのもの。
- **`refreshToken` に文字列があるだけで「正常」と結論する。** サーバ側で失効していることがある（上記の実測）。②のプローブでしか分からない。
- **`refreshTokenExpiresAt` の欠損を失効と読む。** 素朴に `FromUnixTimeMilliseconds` へ渡すと 1970-01-01 になり、健在なのに「要再ログイン」と誤報する。このフィールドはもう判定に使わない。

### 3. 既存の even-terminal を確認

- ポート 3456 の使用状況を確認: `netstat -ano | grep 3456`
- 使用中なら even-terminal が**既に起動している**可能性が高い。実行中プロセスからトークンは取り直せないので、ユーザーに「既に起動しているようです。起動し直しますか？」と確認する。
  - ただし手順5のログ読み出しフォールバックが使えるなら、**再起動せずに既存プロセスの Full token を回収できる**（`even-terminal-*.log` の最新ファイルを見る）。停止する前にまずこれを試す。
  - 起動し直す場合のみ、その 3456 を掴んでいるプロセスを停止してから次へ。PID は `netstat -ano | grep 3456` の最終列。停止は PowerShell ツールで:

    ```
    Stop-Process -Id <PID> -Force
    ```
  - 起動し直さないなら、ここで終了（既存を使う）。
- 未使用ならそのまま次へ。

### 4. even-terminal をバックグラウンド起動

- **必ずバックグラウンドで**（run_in_background=true）次を実行する。前面実行すると出力を流し続けてブロックする:

  ```
  cd "$HOME" && even-terminal --interface Tailscale --provider claude
  ```

  - **`--tailscale` ではなく `--interface Tailscale` を使う**: `--tailscale` は内部で `tailscale ip -4` を `execSync` で呼ぶため、バックグラウンド起動だと `error: failed to get Tailscale IPv4 address` で落ちることがある（PATH に `/c/Program Files/Tailscale` を足しても、`cmd.exe /c` 経由にしても駄目だった実測あり）。`--interface Tailscale` は Windows のネットワークアダプタ「Tailscale」から直接IPv4を読むので外部コマンドに依存せず、同じIP（実測 `100.103.35.66`）が得られる。
  - **PATH に Tailscale を足す必要はない**（`--interface` 方式は `tailscale` コマンドを呼ばないため）。`--tailscale` をどうしても使う場合のみ `export PATH="$PATH:/c/Program Files/Tailscale"` を付ける。
  - **フォールバック順**: ① `--interface Tailscale` → ② それでも駄目なら `--tailscale`（PATH追加付き）→ ③ どちらも駄目なら `tailscale ip -4` で得たIPを直接指定する方式を検討。
  - **ホームディレクトリで起動する**（`cd "$HOME"`）: even-terminal は CWD にログファイル（`even-terminal-<timestamp>.log`）を吐くので、プロジェクト内で起動するとリポジトリが汚れる。常にホームで起動してそれを避ける。ユーザーが特定プロジェクトで使いたいと明示した場合のみ、そのディレクトリで起動する。ホームで起動していれば手順5のログ・フォールバックも効く。
  - `even-terminal` が見つからなければ `even-terminal.cmd` を試す。
  - **CWD の意味**: even-terminal が起動する claude エージェントの作業ディレクトリ＝この実行時の CWD になる。既定はホーム。

### 5. 起動出力を取得して解析

- 4〜6 秒ほど待ってから、バックグラウンドプロセスの出力を読む（起動バナーが出るまで少し待つ）。
- 次の2値を抜き出す:
  - **ホストIP**: `Tailscale:  http://<IP>:3456` の `<IP>`。または接続URL行 `http://<IP>:3456?token=...&defaultProvider=claude` から。
  - **トークン**: `Full token: <token>` の `<token>`（32桁hex）。バナーの `Token: 054e4949...e4b7` は省略表示なので**使わない**。
- どちらかが取れない場合は数秒待ってもう一度出力を読む（起動が遅いことがある）。

#### 5-b. バナーが標準出力に出ないときのフォールバック（ログから抜く）

バックグラウンド実行だとバナーが stdout に流れてこないことがある（プロセスは正常に起動していて 3456 も掴んでいるのに出力が空）。その場合は even-terminal が CWD に吐くログファイルから拾う。手順4の通りホーム（`$HOME` / `%USERPROFILE%`）で起動していれば、そこに `even-terminal-<timestamp>.log` がある。

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
  - ログにも出ていなければ、プロセスが起動途中か失敗している。手順3でポート3456の状況を再確認する。

### 6. 返却（コピペしやすく）

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

## トラブルシュート: G2 から 401 / 認証エラーが出る

「起動して接続情報も入れたのに、G2 からプロンプトを送ると 401 になる」場合。まず**どのレイヤの401か**を切り分ける。even-terminal のトークンの問題と Claude Code 側の問題は別物。

- 最新ログの末尾を見る:

  ```
  LOG=$(ls -t "$HOME"/even-terminal-*.log | head -1); echo "== $LOG =="; tail -60 "$LOG"
  ```

- レイヤの切り分け:
  - `/api/sessions` などの HTTP 行が **200** で返っていれば、**even-terminal 側の認証は成功している**（ホスト・トークンは正しい）。Even アプリの設定を疑う必要はない。
  - HTTP 行そのものが **401** なら、Even アプリに入れたトークンが違う（古いログのトークンを渡した等）。最新ログの `Full token:` を取り直す。ここで話は終わり。
  - `[claude-sdk] ... "error_status":401,"error":"authentication_failed"` や `OAuth access token has expired. Re-authenticate to continue.` が出ていれば **Claude Code 側**。以下へ。

- Claude Code 側の401だったとき。**「アクセストークンが切れていた」＝「再ログインが必要」ではない**。手順2の① →（必要なら）② を実行し、**プローブの exit code で分岐する**:
  - **exit 0（リフレッシュ成功）** → 認証は生きている。再ログインは不要。**G2 からもう一度プロンプトを送ってもらう**。次のサブプロセスが更新後のトークンを読んで通る。
    - それでもまだ401なら、even-terminal 側が古い状態を掴んでいる疑い。そこで初めて even-terminal を再起動する（手順3の停止 → 手順4の起動。**ホストは同じだがトークンは変わる**ので新しい値を返し直すこと）。
  - **exit 1（`could not be refreshed`）** → ここで初めて `claude` → `/login` を案内する。even-terminal の再起動は不要、ホストもトークンも変わらない。
  - **exit 126（`is not recognized`）** → 認証ではなく claude インストール破損。手順2の③で復旧してから①に戻る。

- **やってはいけない切り分け**:
  - `expiresAt` が過去なのを見ただけで「認証が切れているので再ログインしてください」と結論すること。8時間ごとに必ずそうなるので、大半が誤診になる。
  - 逆に、`refreshToken` に文字列があるだけで「正常」と結論すること。サーバ側で失効していても文字列は残っている。判定は必ずプローブで行う。

## 注意

- even-terminal を前面実行しない（ブロックする）。必ずバックグラウンド。
- **認証の判定はファイル読みではなく `claude -p "ok"` のプローブで行う**（手順2）。`expiresAt`（アクセストークン・8時間）が過去なのは1日3回起きる正常状態なので、そこを見て再ログインを案内すると誤診になる。`refreshToken` の文字列有無や `refreshTokenExpiresAt` はサーバ側の失効を検出できないので、判定の根拠にしない。
- **`claude` コマンド自体が壊れていることがある**（Volta + 自動アップデートで `bin\claude.exe` が消える実測あり）。401 に見えて実はこれ、というケースがあるので手順2の②で切り分ける。復旧は `volta install @anthropic-ai/claude-code`。
- **起動フラグは `--interface Tailscale` が既定**。`--tailscale` は execSync 依存でバックグラウンド起動時に落ちうるため、フォールバック扱い。
- **プロセスの停止**: `netstat -ano | grep 3456` で PID を調べ、PowerShell で `Stop-Process -Id <PID> -Force`。Git Bash の `kill` は Windows ネイティブプロセスに効かないことがあるので、Stop-Process の方が確実。
- **ログファイルが溜まる**: 起動のたびにホームに `even-terminal-<timestamp>.log` が増える。トークンが平文で入っているので、不要になったものは削除してよい（削除前にユーザーに確認する）。
- コマンドが見つからないときはフルパス／`.cmd` 版を試す（Windows のグローバル npm bin）。
- PC がスリープすると even-terminal も止まり外から繋げなくなる。外出中も使うなら電源接続時のスリープ抑制が別途必要（このスキルの範囲外）。
