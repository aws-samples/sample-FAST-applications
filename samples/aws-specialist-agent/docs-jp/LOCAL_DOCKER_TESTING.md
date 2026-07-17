# ローカル Docker テストガイド

AgentCore エージェントの Docker イメージをローカルでビルドおよびテストして、Dockerfile の設定と依存関係を検証します。

## 制限事項

**スタンドアロンの Docker テストでは Gateway ツールは動作しません**。理由:

- `@requires_access_token` デコレーターには AgentCore Identity サービスが必要
- AgentCore Identity は AgentCore Runtime コンテキスト内でのみ動作する
- OAuth2 M2M 認証は Runtime の外部ではモックできない

**動作するもの:** Dockerfile のビルド、依存関係のインストール、Code Interpreter、Gateway 以外のツール  
**動作しないもの:** AgentCore Gateway ツール (MCP ベースの Lambda ツール)

**Gateway をサポートする完全なローカルテスト** には、`docker-compose` を使用してください ([Local Development Guide](LOCAL_DEVELOPMENT.md) を参照)。

## なぜ Docker テストを行うのか?

| Testing Mode             | Gateway Tools | Code Interpreter | Use Case                           |
| ------------------------ | ------------- | ---------------- | ---------------------------------- |
| `test-agent.py --local`  | Yes           | Yes              | 高速な Python の反復開発           |
| **Manual Docker**        | No            | Yes              | Dockerfile/依存関係の検証          |
| **`docker-compose`**     | Yes           | Yes              | 完全なローカル開発                 |
| `test-agent.py` (remote) | Yes           | Yes              | デプロイされたエージェントのテスト |

Docker テストでは、以下を検証します:

- Dockerfile が正しくビルドされること
- 依存関係がコンテナ内に適切にインストールされること
- コンテナが起動し、ヘルスチェックに応答すること
- エージェントコードがコンテナ化された環境で動作すること (Gateway ツールなし)

## 前提条件

1. **Docker** がインストールされ、実行中であること (`docker ps` が動作する必要がある)
2. **デプロイ済みのスタック** - Memory ID と SSM パラメータに必要
3. **AWS 認証情報** が環境に設定されていること

## Docker イメージのビルド

```bash
# エージェントパターン用のイメージをビルド
docker build -f agent/strands-single-agent/Dockerfile \
  -t fast-agent-local \
  --platform linux/arm64 .
```

### プラットフォーム要件

AgentCore Runtime には ARM64 アーキテクチャが必要です。x86/amd64 マシンでは、エミュレーションを有効にします:

```bash
# ARM64 エミュレーションのワンタイムセットアップ
docker run --privileged --rm tonistiigi/binfmt --install all
```

## コンテナの実行

```bash
# CloudFormation の outputs から Memory ID を取得
MEMORY_ID=$(aws cloudformation describe-stacks \
  --stack-name <your-stack-name> \
  --query 'Stacks[0].Outputs[?OutputKey==`MemoryArn`].OutputValue' \
  --output text | awk -F'/' '{print $NF}')

# AWS 認証情報をエクスポート (コンテナに必要)
export AWS_ACCESS_KEY_ID=$(aws configure get aws_access_key_id)
export AWS_SECRET_ACCESS_KEY=$(aws configure get aws_secret_access_key)
export AWS_SESSION_TOKEN=$(aws configure get aws_session_token)  # 一時的な認証情報を使用する場合

# コンテナを実行
docker run --rm -it -p 8080:8080 \
  --platform linux/arm64 \
  -e MEMORY_ID=$MEMORY_ID \
  -e STACK_NAME=<your-stack-name> \
  -e AWS_DEFAULT_REGION=us-east-1 \
  -e AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY \
  -e AWS_SESSION_TOKEN=$AWS_SESSION_TOKEN \
  fast-agent-local
```

**重要:** AWS 認証情報は環境変数としてエクスポートする必要があります。Docker コンテナは `~/.aws/credentials` または `~/.aws/config` から認証情報を読み取ることはできません。

## エージェントのテスト

### ヘルスチェック

```bash
curl http://localhost:8080/ping
# Returns: {"status":"Healthy","time_of_last_update":...}
```

### テスト用のモック JWT

検証済みの JWT を提供する AgentCore Runtime がないため、モックの未署名 JWT を作成します:

```bash
# sub=test-user でモック JWT を生成
MOCK_JWT=$(python3 -c "import base64,json; h=base64.urlsafe_b64encode(json.dumps({'alg':'none','typ':'JWT'}).encode()).rstrip(b'=').decode(); p=base64.urlsafe_b64encode(json.dumps({'sub':'test-user'}).encode()).rstrip(b'=').decode(); print(f'{h}.{p}.')")

# エージェントをテスト (Gateway ツールでは失敗するが、Code Interpreter は動作するはず)
curl -X POST http://localhost:8080/invocations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $MOCK_JWT" \
  -d '{"prompt": "Execute Python: print(2+2)", "runtimeSessionId": "test-123"}'
```

**期待される動作:**

- Code Interpreter のリクエストは動作する
- Gateway ツールのリクエストは認証エラーで失敗する

## アーキテクチャ

```
┌─────────────────────────────────────────────────────────────┐
│  Local Machine                                              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Docker Container (ARM64)                           │   │
│  │  ┌─────────────────────────────────────────────┐   │   │
│  │  │  Agent (basic_agent.py / langgraph_agent.py)│   │   │
│  │  │  - :8080 をリッスン                         │   │   │
│  │  │  - 渡された AWS 認証情報を使用              │   │   │
│  │  │  - Gateway 認証は失敗する                   │   │   │
│  │  └─────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────┘   │
│                           │                                 │
│                           ▼                                 │
│              http://localhost:8080/invocations              │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
              ┌─────────────────────────────────┐
              │  AWS (デプロイされたリソース)   │
              │  - AgentCore Memory      (Yes)  │
              │  - Code Interpreter      (Yes)  │
              │  - AgentCore Gateway     (No)   │
              │  - SSM Parameters        (Yes)  │
              └─────────────────────────────────┘
```

## トラブルシューティング

### コンテナは起動するが Gateway 認証が失敗する

これは **想定通りの動作** です。`@requires_access_token` デコレーターには AgentCore Identity サービスが必要であり、これは AgentCore Runtime 内でのみ動作します。

**解決策:** 完全なローカルテストには `docker-compose` を使用してください ([Local Development Guide](LOCAL_DEVELOPMENT.md) を参照)。

### コンテナは起動するがエージェントがすぐに失敗する

コンテナのログを確認します:

```bash
# コンテナ ID を見つける
docker ps

# ログを表示
docker logs <container-id>
```

よくある問題:

- **AWS 認証情報の不足**: `AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY` が設定されていることを確認
- **期限切れのセッショントークン**: AWS 認証情報を更新
- **スタックがデプロイされていない**: スクリプトは Memory ID を取得するためにデプロイされたスタックが必要

### "platform mismatch" でビルドが失敗する

ARM64 エミュレーションを有効にします (上記のプラットフォーム要件を参照)。

### localhost:8080 で "Connection refused" になる

エージェントがまだ起動中の可能性があります。10〜30 秒待ってから再試行してください。問題が続く場合はログを確認してください。

### ログ内の ECS/EKS 警告

これらの警告はローカル実行時に予想されるものです:

```
AwsEcsResourceDetector failed: Missing ECS_CONTAINER_METADATA_URI...
AwsEksResourceDetector failed: No such file or directory...
```

OpenTelemetry インストルメンテーションは、ローカルには存在しない ECS/EKS メタデータを探します。これらは安全に無視できます。

## 高度な使い方

### コンテナログをリアルタイムで表示する

```bash
# コンテナをフォアグラウンドで起動 (デタッチしない)
docker run --rm -p 8080:8080 \
  --platform linux/arm64 \
  -e MEMORY_ID=<memory-id> \
  -e STACK_NAME=<stack-name> \
  -e AWS_DEFAULT_REGION=us-east-1 \
  -e AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY \
  -e AWS_SESSION_TOKEN=$AWS_SESSION_TOKEN \
  fast-agent-local
```

### ビルドのみの検証

実行せずに Dockerfile を検証するには:

```bash
docker build -f agent/strands-single-agent/Dockerfile \
  -t fast-agent-local \
  --platform linux/arm64 .

# ビルドが成功したか確認
echo $?  # 0 を返すはず
```

## 各テストモードを使用するタイミング

| Scenario                               | Recommended Mode                        |
| -------------------------------------- | --------------------------------------- |
| エージェントロジックの高速な反復開発   | `test-agent.py --local`                 |
| Dockerfile が正しくビルドされるか検証  | Manual Docker build                     |
| Gateway ツールでローカルテスト         | `docker-compose` (LOCAL_DEVELOPMENT.md) |
| デプロイされた本番エージェントのテスト | `test-agent.py` (remote)                |
| CI/CD パイプライン検証                 | Manual Docker build                     |

## 関連ドキュメント

- [Local Development Guide](LOCAL_DEVELOPMENT.md) - `docker-compose` と Gateway サポートを使用した完全なローカル開発
- [Deployment Guide](DEPLOYMENT.md) - フルスタックデプロイ手順
- [Agent Configuration](AGENT_CONFIGURATION.md) - エージェントパターンの設定
- [Streaming Guide](STREAMING.md) - ストリーミングイベントの理解
