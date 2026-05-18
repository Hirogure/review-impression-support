# GitHub Pages公開手順

このプロジェクトでは、公開LPを `docs/` に置いています。

## 1. GitHubで空のリポジトリを作る

おすすめリポジトリ名:

```text
review-impression-support
```

Public / Private はどちらでもよいですが、GitHub Pagesで公開するなら Public が簡単です。

## 2. ローカルでリモートを設定

GitHubで作成したリポジトリURLを使って、PowerShellで以下を実行します。

```powershell
cd C:\Users\hiroa\Documents\Codex\2026-05-17\30-1-3\meo-mini-ops
git remote add origin https://github.com/YOUR_USER/review-impression-support.git
git branch -M main
git push -u origin main
```

## 3. GitHub Pagesを有効化

GitHubのリポジトリ画面で:

```text
Settings
→ Pages
→ Build and deployment
→ Source: Deploy from a branch
→ Branch: main
→ Folder: /docs
→ Save
```

数分後に以下のようなURLで公開されます。

```text
https://YOUR_USER.github.io/review-impression-support/
```

## 4. 営業メールに入れるURL

公開後、営業メール内の `LP_URL` をGitHub PagesのURLに置き換えます。

