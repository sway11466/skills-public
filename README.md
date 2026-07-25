# skills-public

公開できる [Claude Code](https://docs.claude.com/en/docs/claude-code) スキルの置き場。

スキルは Claude Code が特定タスク向けの手順をまとめたもので、`~/.claude/skills/<name>/SKILL.md`（ユーザー全体）または `.claude/skills/<name>/SKILL.md`（プロジェクト単位）に置くと、会話中に該当する状況で自動的に使われる。

## 収録スキル

| スキル | 概要 |
|---|---|
| [even-terminal](even-terminal/SKILL.md) | Even Realities G2 の Terminal Mode 用に `even-terminal` を Tailscale 経由で起動し、接続情報（ホスト・トークン）を返す。 |

## 導入

使いたいスキルのフォルダを、Claude Code のスキルディレクトリにコピーするだけ。

```bash
# 例: even-terminal をユーザー全体のスキルとして導入
cp -r even-terminal ~/.claude/skills/
```

以降、対象の状況（例:「G2 に繋いで」）で Claude Code が自動的にそのスキルを使う。各スキルの前提・手順は各 `SKILL.md` を参照。

## ライセンス

MIT
