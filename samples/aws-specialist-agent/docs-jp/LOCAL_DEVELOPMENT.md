# Docker Compose を使用したローカル開発

このガイドでは、開発目的で Docker Compose を使用して FAST スタック全体をローカルで実行する方法について説明します。

## 前提条件

**重要**: ローカル開発でも、バックエンドの依存関係 (Memory、Gateway、SSM パラメータ) のために、AWS にデプロイされた FAST スタックが必要です。Docker Compose はフロントエンドとエージェントをコンテナ化するだけで、AWS サービスを置き換えるものではありません。

### 必須

1. **デプロイ済みの FAST スタック**: 以下を使用してすでに FAST を AWS にデプロイしている必要があります:

   ```bash
   cd infra-cdk
   cdk deploy
   ```

2. **AWS 認証情報**: AWS 認証情報は **環境変数としてエクスポートする必要があります** — Docker コンテナは `~/.aws/credentials` や `~/.aws/config` を読み取れません:

   ```bash
   # Option 1: 既存の aws configure プロファイルからエクスポート
   export AWS_ACCESS_KEY_ID=$(aws configure get aws_access_key_id)
   export AWS_SECRET_ACCESS_KEY=$(aws configure get aws_secret_access_key)
   export AWS_SESSION_TOKEN=$(aws configure get aws_session_token)  # 一時的な認証情報を使用する場合

   # Option 2: 直接設定
   export AWS_ACCESS_KEY_ID=your-key
   export AWS_SECRET_ACCESS_KEY=your-secret
   export AWS_SESSION_TOKEN=your-token  # 一時的な認証情報を使用する場合
   ```

3. **Docker と Docker Compose**: Compose サポートを備えた Docker Desktop または Docker Engine をインストールします

4. **環境変数**: 以下の必須変数を設定します:
   ```bash
   export MEMORY_ID=your-memory-id
   export STACK_NAME=your-stack-name
   export AWS_DEFAULT_REGION=us-east-1
   ```

### 環境変数の検索

これらの値はデプロイ済みのスタックから取得します:

```bash
# スタックの outputs を取得
aws cloudformation describe-stacks --stack-name your-stack-name --query 'Stacks[0].Outputs'

# MemoryArn から Memory ID を抽出 (最後の / 以降の部分)
# Stack Name を抽出 (デプロイ時に使用した名前)
# デプロイしたのと同じリージョンを使用
```

## クイックスタート

1. **環境変数を設定**:

   ```bash
   export MEMORY_ID=your-memory-id-from-stack-outputs
   export STACK_NAME=your-stack-name
   export AWS_DEFAULT_REGION=us-east-1
   ```

2. **スタックを起動**:

   ```bash
   cd docker && docker compose up --build
   ```

3. **アプリケーションへのアクセス**:
   - Frontend: http://localhost:3000
   - Agent API: http://localhost:8080
   - Agent Health: http://localhost:8080/ping

## ローカルモードでの認証

本番環境では、AgentCore Runtime がユーザーの JWT を検証し、それをエージェントに渡します。エージェントは、リクエストペイロードを信頼するのではなく、JWT の `sub` クレームからユーザー ID を抽出します (プロンプトインジェクションによるなりすましを防止)。

Docker Compose 経由でローカル実行する場合、AgentCore Runtime はありません。テストスクリプトは、`sub` クレームとしてテストユーザー ID を持つモックの未署名 JWT を生成し、`Authorization: Bearer` ヘッダーで送信します。これにより、実際の Cognito トークンを必要とせずに、本番環境と同じコードパスが実行されます。

## 環境設定

利便性のために、リポジトリのルートに `.env` ファイルを作成します:

```bash
# 必須 - デプロイされた AWS スタックから取得
MEMORY_ID=your-memory-id
STACK_NAME=your-stack-name
AWS_DEFAULT_REGION=us-east-1

# AWS 認証情報 (必須 - Docker コンテナは ~/.aws/credentials を読み取れません)
AWS_ACCESS_KEY_ID=your-key
AWS_SECRET_ACCESS_KEY=your-secret
AWS_SESSION_TOKEN=your-token
```

その後、以下を実行: `cd docker && docker compose up --build`

## 開発ワークフロー

### 変更を加える

- **フロントエンドの変更**: ファイルはボリュームとしてマウントされているため、変更はすぐに反映されます
- **エージェントの変更**: エージェントコンテナを再ビルドします:
  ```bash
  cd docker && docker compose up --build agent
  ```

### 異なるエージェントパターンの使用

異なるエージェントパターンを使用するには:

1. **docker/docker-compose.yml を編集**:

   ```yaml
   agent:
     build:
       dockerfile: agent/<your-pattern>/Dockerfile
   ```

2. **再ビルド**:
   ```bash
   cd docker && docker compose up --build agent
   ```

### ログとデバッグ

```bash
# すべてのログを表示
docker compose logs -f

# 特定のサービスのログを表示
docker compose logs -f agent
docker compose logs -f frontend

# コンテナのシェルにアクセス
docker compose exec agent bash
docker compose exec frontend sh
```

## トラブルシューティング

### エージェントが起動しない

**症状**: エージェントコンテナが終了するか、ヘルスチェックが失敗する

**解決策**:

1. AWS 認証情報を確認: `aws sts get-caller-identity`
2. 環境変数が正しく設定されていることを確認
3. デプロイされたスタックが存在し、正常であることを確認
4. エージェントログを確認: `docker compose logs agent`

### フロントエンドがエージェントに接続できない

**症状**: フロントエンドはロードされるが、バックエンドと通信できない

**解決策**:

1. エージェントが正常か確認: `curl http://localhost:8080/ping`
2. コンテナ間のネットワーク接続を確認
3. フロントエンドがローカルエージェントエンドポイントを使用するように設定されていることを確認

### AWS 権限エラー

**症状**: エージェントは起動するが、AWS API 呼び出しで失敗する

**解決策**:

1. AWS 認証情報の IAM 権限を確認
2. デプロイされたスタックリソースにアクセスできることを確認
3. 正しい AWS リージョンが設定されていることを確認

### Memory/Gateway が見つからない

**症状**: エージェントが Memory または Gateway リソースの不足を報告する

**解決策**:

1. `MEMORY_ID` がデプロイされたスタックの Memory リソースと一致することを確認
2. `STACK_NAME` が CloudFormation スタック名と一致することを確認
3. スタックのデプロイが正常に完了したことを確認

## スタックの停止

```bash
# すべてのサービスを停止
cd docker && docker compose down

# 停止してボリュームも削除
cd docker && docker compose down -v

# 停止してイメージも削除
cd docker && docker compose down --rmi all
```

## 本番環境へのデプロイ

この Docker Compose のセットアップは開発専用です。本番環境のデプロイには以下を使用します:

```bash
cd infra-cdk
cdk deploy
cd ..
python scripts/deploy-frontend.py
```

## 次のステップ

- `agent/` のエージェントコードをカスタマイズする
- `frontend/src/` のフロントエンドを変更する
- `tools/` または `gateway/tools/` に新しいツールを追加する
- `infra-cdk/` のインフラストラクチャを更新する

補足: インフラストラクチャの変更には、Docker Compose の再起動だけでなく、CDK 経由での再デプロイが必要です。
