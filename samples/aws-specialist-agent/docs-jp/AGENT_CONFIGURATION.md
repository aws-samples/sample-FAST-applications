# エージェント設定ガイド

FAST はコンテナ内で動作する任意のエージェントフレームワークをサポートします。このガイドでは、既存のパターンを使用する方法、独自のパターンを作成する方法、エージェントの動作を設定する方法について説明します。

---

## 既存のパターン

### Strands Single Agent パターン

**場所**: `agent/strands-single-agent/`

AgentCore Memory との統合を備えた、Strands フレームワークを使用した基本的な会話エージェントです。

**このエージェントが行うこと**:

- マルチターンの会話チャット
- 短期メモリでの会話履歴の維持
- **オプションの長期メモリ**: `config.yaml` で `use_long_term_memory: true` を設定すると、エージェントは `SemanticMemoryStrategy` を使用してセッションをまたいで事実を抽出して呼び出します (Cognito ユーザー ID をキーとして使用)。詳細は [Memory Integration Guide](MEMORY_INTEGRATION.md#enabling-long-term-memory) を参照してください。
- より良い UX のためにレスポンスをストリーミング
- Cognito で認証 (ユーザー ID はメモリで追跡)

**主要な設定ファイル**:

- **エージェントのロジック**: `agent/strands-single-agent/basic_agent.py` - メモリ統合、モデル設定、ストリーミングロジックを含むメインのエージェント実装
- **Python の依存関係**: `agent/strands-single-agent/requirements.txt` - 必要な Python パッケージ (Strands、bedrock-agentcore など)
- **コンテナ設定**: `agent/strands-single-agent/Dockerfile` - Docker コンテナの定義 (`deployment_type: docker` の場合のみ使用)
- **インフラストラクチャ**: `infra-cdk/lib/backend-stack.ts` - メモリリソースとランタイムデプロイメントのための CDK 設定

**モデル設定** (レジストリ駆動):

チャットモデルは `basic_agent.py` にハードコードされていません。ユーザーが UI
でモデルを選び、選択肢はレジストリで一元定義されます:

```typescript
// infra-cdk/lib/utils/model-registry.ts
export const SELECTABLE_MODELS: readonly SelectableModel[] = [
  {
    key: "opus-4.8",
    label: "Claude Opus 4.8",
    id: "global.anthropic.claude-opus-4-8",
    provider: "anthropic",
  },
  {
    key: "sonnet-5",
    label: "Claude Sonnet 5",
    id: "global.anthropic.claude-sonnet-5",
    provider: "anthropic",
  },
  {
    key: "sonnet-4.6",
    label: "Claude Sonnet 4.6",
    id: "global.anthropic.claude-sonnet-4-6",
    provider: "anthropic",
    default: true,
  },
  // ...ここに 1 行追加して再デプロイ
]
```

CDK がバックエンドの allowlist (`MODEL_MAP` / `DEFAULT_MODEL_KEY` 環境変数) と
フロントエンドのピッカー選択肢 (`aws-exports.json`) をこの単一ソースから導出する
ため、両者がズレることはありません。モデルの変更・追加はこの配列を編集して再
デプロイするだけで、`basic_agent.py` の変更は不要です。論理キーから物理モデルへの
解決は `agent/strands-single-agent/models.py` が担います。Claude は bedrock-runtime
(Converse)、OpenAI GPT は bedrock-mantle の OpenAI Responses API で、両プロバイダ
とも稼働中です。どのモデルにも `temperature` は渡しません (現行世代は拒否するため)。

**システムプロンプト** (`agent/strands-single-agent/basic_agent.py`):

```python
system_prompt = """You are a helpful assistant. Answer questions clearly and concisely."""
```

**変更後**: 再デプロイの手順については [Deployment Guide](DEPLOYMENT.md) を参照してください。

---

## 独自のエージェントパターンを作成する

### Step 1: パターンディレクトリを作成する

```bash
mkdir -p agent/my-custom-agent
cd agent/my-custom-agent
```

### Step 2: エージェントを実装する

以下を行うエージェントコードを作成します:

- AgentCore Runtime からの HTTP リクエストを受信する
- ユーザークエリを処理する
- レスポンスを返す (ストリーミングまたは非ストリーミング)
- AgentCore Memory と統合する (オプション)

**構造の例**:

```python
from bedrock_agentcore.runtime import BedrockAgentCoreApp, RequestContext
from utils.auth import extract_user_id_from_context

app = BedrockAgentCoreApp()

@app.entrypoint
async def agent_handler(payload, context: RequestContext):
    """エージェントのメインエントリポイント"""
    user_query = payload.get("prompt")
    session_id = payload.get("runtimeSessionId")

    # 検証済みの JWT トークンからユーザー ID を安全に抽出する
    # (操作される可能性のある) ペイロード本体を信頼するのではなく
    user_id = extract_user_id_from_context(context)

    # ここにエージェントのロジック
    # ...

    yield response

if __name__ == "__main__":
    app.run()
```

### Step 3: Dockerfile を作成する (Docker デプロイメントのみ)

設定で `deployment_type: docker` を使用する場合は、Dockerfile を作成します:

```dockerfile
FROM public.ecr.aws/docker/library/python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["python", "your_agent.py"]
```

**ZIP デプロイメントの場合**: Dockerfile は不要です。ZIP パッケージャーは `agent/<pattern>/` ディレクトリと `agent/utils/`、`gateway/`、`tools/` ディレクトリ、および `requirements.txt` の依存関係を自動的にバンドルします。

### Step 4: CDK 設定を更新する

`infra-cdk/config.yaml` で:

```yaml
backend:
  pattern: "my-custom-agent" # パターンディレクトリ名
```

**エージェントが追加の AWS サービスを必要とする場合** (Knowledge Bases、DynamoDB、S3 など)、`infra-cdk/lib/` の CDK スタックを変更します:

**例**: Knowledge Base を追加する

```typescript
// Knowledge Base コンストラクトを作成
const knowledgeBase = new bedrock.CfnKnowledgeBase(this, "KB", {
  name: "MyKnowledgeBase",
  // ... 設定
});

// backend-stack.ts でエージェントの環境変数に追加
EnvironmentVariables: {
  KNOWLEDGE_BASE_ID: knowledgeBase.attrKnowledgeBaseId,
  // ... その他の変数
}
```

### Step 5: デプロイ

完全なデプロイ手順については [Deployment Guide](DEPLOYMENT.md) を参照してください。

## Design notes

本派生プロジェクト固有の設計判断:

- **選択可能モデル、単一の信頼ソース**: モデルピッカーは任意の ID からの推測ではなくレジストリで駆動する。レジストリは表示名・プロバイダ・推論プロファイル/エンドポイント・機能フラグを宣言し、フロントエンドとバックエンドの双方がここから読むのでピッカーと Runtime が常に同期する。
- **OpenAI モデルを Bedrock 経由で配信**: GPT モデルもレジストリエントリとして追加し、Bedrock が公開している OpenAI Responses API エンドポイント経由で呼ぶ。認証と可観測性の経路を一本化でき、別途 OpenAI API キーを配線する必要はない。
- **直接呼び出しによる簡素化**: OpenAI 統合は同一リージョンのエンドポイントを直叩きする。同一リージョン版エンドポイントが提供された段階で旧クロスリージョン peering 構成は撤去し、ネットワーク構成の単純化と遅延削減を達成した。
- **モデル構築時に temperature を渡さない**: 一部の reasoning 系エンドポイントは `temperature` を拒否するため、モデル構築経路では一切設定しない。エンドポイント依存のパラメータは各モデルのレジストリフラグで管理する。
- **System prompt をセクション分割**: Language / Skills first / AWS guidance / Tool routing といった名前付きセクションに分け、追記が適切な場所に入り複数プロバイダ間でも保守性を保てるようにしている。
