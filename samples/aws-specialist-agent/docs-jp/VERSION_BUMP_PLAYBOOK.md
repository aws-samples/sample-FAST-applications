# バージョンバンプ Playbook

このドキュメントでは、FAST (Fullstack AgentCore Solution Template) のバージョンをバンプするためのチェックリストを提供します。

## 手動更新が必要なファイル (6 ファイル)

1. **`VERSION`** - ルートのバージョンファイル
2. **`pyproject.toml`** - Python パッケージのバージョン (`version = "X.Y.Z"`)
3. **`frontend/package.json`** - フロントエンドパッケージのバージョン (`"version": "X.Y.Z"`)
4. **`infra-cdk/package.json`** - CDK パッケージのバージョン (`"version": "X.Y.Z"`)
5. **`infra-cdk/lib/fast-main-stack.ts`** - スタックの説明文 (`(vX.Y.Z)`)
6. **`CHANGELOG.md`** - 先頭に新しいバージョンエントリを追加

## 自動生成ファイル (手動で更新しない)

- `frontend/package-lock.json`
- `infra-cdk/package-lock.json`
- `infra-cdk/lib/fast-main-stack.js`

## 手順

### 1. ソースファイルを更新する

上記の 7 ファイルを新しいバージョン番号で手動で更新します。

### 2. 自動生成ファイルを再生成する

```bash
# Frontend
cd frontend && npm install

# Infrastructure
cd infra-cdk && npm install && npm run build
```

### 3. 検証

古いバージョンへの参照が残っていないか検索します:

```bash
find . -type f \( -name "*.md" -o -name "*.py" -o -name "*.js" -o -name "*.ts" -o -name "*.tsx" -o -name "*.json" -o -name "*.yaml" -o -name "*.yml" -o -name "VERSION" \) | grep -v node_modules | grep -v cdk.out | grep -v ".next" | grep -v "dist" | grep -v "build" | grep -v "__pycache__" | xargs grep -n "OLD_VERSION" 2>/dev/null
```

### 4. テスト

```bash
make all                    # lint を実行
cd infra-cdk && cdk synth   # CDK の synth をテスト
cd frontend && npm run build # フロントエンドのビルドをテスト
```

### 5. Git 操作

```bash
git add .
git commit -m "Bump version to X.Y.Z"
git push origin main

# タグを作成してプッシュ
git tag vX.Y.Z
git push origin vX.Y.Z
```

## 補足

- セマンティックバージョニング (MAJOR.MINOR.PATCH) に従う
- git タグには `v` プレフィックスを使用 (例: `v0.1.3`)
- プロジェクトのバージョンのみを更新し、依存関係のバージョンは更新しない
- 過去の changelog エントリはそのまま変更しない
