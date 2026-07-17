# AgentCore Gateway 実装

このドキュメントでは、FAST がスケーラブルで本番運用に耐えるツール実行アーキテクチャを提供するために、AgentCore Gateway と Lambda targets をどのように実装しているかを説明します。

## 概要

FAST は **AgentCore Gateway with Lambda Targets** を使用して、エージェントが外部のツールやサービスにアクセスできるようにしています。このアーキテクチャは、エージェントロジックとツール実装の明確な分離を提供し、個々のツールを独立してスケーリングおよびデプロイできるようにします。

### エージェントが利用できるもの

デプロイされたエージェントは 3 つのケイパビリティソースを持ちます。

1. **Lambda ターゲット（`sample-tool-target`）** — デモ用の `text_analysis_tool`。詳細は後述。
2. **AWS MCP Server ターゲット（`aws-mcp`）** — マネージドな Agent Toolkit for AWS の MCP Server（`https://aws-mcp.<region>.api.aws/mcp`）を MCP gateway ターゲットとして登録したもの。`aws___*` ツール（AWS の知識に加え `aws___call_aws` / `aws___run_script`）を公開します。Gateway はサービス名 `aws-mcp` にスコープした SigV4 でリクエストに署名します。
3. **S3 Files 経由の Skills** — ベンダリングした AWS skills を S3 に同期し、S3 Files で Runtime の `/mnt/skills` にマウントして、Strands `AgentSkills` プラグインがエージェントに公開します（Gateway ターゲットではありません）。`infra-cdk/lib/skills-storage-stack.ts` を参照。

**ユーザーごとのツールアクセス:** Gateway の Cedar policy が、ユーザーの `department` クレーム（`cognito:groups` から解決。[Identity Propagation](IDENTITY_POLICY.md) 参照）に基づいて _誰が_ 各ツールを呼べるかを制御します。読み取り系ツールは finance + engineering に許可、破壊的な `aws___call_aws` / `aws___run_script` は finance のみ、guest は拒否です。拒否されたツールは `tools/list` の段階で非表示になります。

## アーキテクチャの比較

### スタンドアロン MCP Gateway と Lambda Targets

AgentCore Gateway を実装するには 2 つの主要なアプローチがあります。

#### スタンドアロン MCP Gateway

- Gateway が直接 MCP (Model Context Protocol) サーバーを実装
- ツールが gateway インフラストラクチャに組み込まれている
- 基本的なシナリオではセットアップが簡単
- クライアント → gateway の直接通信

#### Lambda Targets (FAST の選択)

- Gateway が外部 Lambda 関数へのプロキシ／ルーターとして動作
- 各ツールは別々の Lambda 関数として実装される
- クライアント → Gateway → Lambda → Gateway → クライアントの流れ
- エンタープライズ用途のメリットを備えた本番運用向けアーキテクチャ

### FAST が Lambda Targets を採用する理由

以下の本番運用上の利点から Lambda targets を選択しました。

1. **関心の分離**: ビジネスロジックは Lambda 関数内にあり、gateway インフラストラクチャ側には存在しない
2. **独立したスケーリング**: 各ツールを利用パターンに応じて独立してスケールできる
3. **保守性**: gateway インフラストラクチャに触れることなくツールロジックを更新できる
4. **再利用性**: 同じ Lambda を複数の gateway や他のサービスから使用できる
5. **言語の柔軟性**: 各 Lambda が異なるプログラミング言語を使用できる
6. **独立したデプロイ**: gateway のダウンタイムなしでツール更新をデプロイできる
7. **コスト最適化**: 実際のツール実行時間に対してのみ料金が発生する
8. **セキュリティ**: 各 Lambda に必要な要件に応じた IAM 権限を個別に設定できる

## 実装の詳細

### Gateway の構成

gateway は以下の構成で AWS CDK の L1 constructs を用いて作成されます。

- **Protocol Type**: MCP (Model Context Protocol)
- **Authorization**: Cognito 統合によるカスタム JWT
- **Authentication**: Machine-to-machine の client credentials フロー
- **Target Type**: AWS Lambda 関数
- **Optional Features**: Semantic search（ツール検索のために有効化可能）

### Lambda Target の構造

FAST における各 Lambda target は次のパターンに従います。

```python
def handler(event, context):
    # context からツール名を取得（target prefix を除去）
    delimiter = "___"
    original_tool_name = context.client_context.custom['bedrockAgentCoreToolName']
    tool_name = original_tool_name[original_tool_name.index(delimiter) + len(delimiter):]

    # event にはツールの引数が直接入っている
    arguments = event

    # 期待される形式でレスポンスを返す
    return {
        'content': [
            {
                'type': 'text',
                'text': 'Tool response here'
            }
        ]
    }
```

#### ツール呼び出しプロトコルの詳細

**重要な実装上の注意点:**

ツール名は event の body には **渡されません**。Gateway は Lambda の context オブジェクト経由でツール名を渡します。

```python
# ツール名の取得場所
original_tool_name = context.client_context.custom['bedrockAgentCoreToolName']

# 引数は event body にある
name = event.get('name', 'World')
```

**ツール名のフォーマット:**

Gateway はターゲット名を 3 つのアンダースコアを区切り文字として prefix に含めます。

```
{target_name}___{tool_name}
```

例: `sample_tool_target___sample_tool`

**完全な動作実装例:**

```python
import json
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    try:
        # context からツール名を取得
        original_tool_name = context.client_context.custom['bedrockAgentCoreToolName']
        logger.info(f"Received tool invocation: {original_tool_name}")
        logger.info(f"Event: {json.dumps(event)}")

        # target prefix を除去
        delimiter = "___"
        if delimiter in original_tool_name:
            tool_name = original_tool_name[original_tool_name.index(delimiter) + len(delimiter):]
        else:
            tool_name = original_tool_name

        # 適切なツールハンドラーへルーティング
        if tool_name == "sample_tool":
            name = event.get('name', 'World')
            result = f"Hello, {name}! This is a sample tool from FAST."
            return {"result": result}
        else:
            raise ValueError(f"Unknown tool: {tool_name}")

    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}", exc_info=True)
        raise
```

**Lambda ごとに複数のツールを処理:**

抽出したツール名でルーティングすることで、1 つの Lambda で複数のツールを処理できます。

```python
if tool_name == "tool_one":
    # tool one を処理
    pass
elif tool_name == "tool_two":
    # tool two を処理
    pass
```

これは AWS samples で使用されている有効な本番運用パターンです。

### ツールスキーマの定義

ツールは CDK スタック内で JSON schema を用いて定義されます。

```json
{
  "name": "sample_tool",
  "description": "A sample tool that returns a greeting",
  "inputSchema": {
    "type": "object",
    "properties": {
      "name": {
        "type": "string",
        "description": "Name to greet"
      }
    },
    "required": ["name"]
  }
}
```

**サポートされる JSON Schema 型:**

Gateway 用のツール仕様を定義する際は、以下の型を使用します。

- `"integer"` - 整数用（"int" ではない）
- `"number"` - 浮動小数点数用
- `"string"` - 文字列用
- `"boolean"` - 真偽値用
- `"array"` - 配列用
- `"object"` - オブジェクト用

### 認証フロー

1. **Machine Client**: CDK が client credentials フロー対応の Cognito machine client を作成
2. **Resource Server**: gateway アクセス用のスコープを定義（read/write）
3. **JWT Authorization**: Gateway が Cognito の OIDC discovery を用いてトークンを検証
4. **SSM Parameters**: クライアント認証情報は SSM Parameter Store に安全に保管

## 主要コンポーネント

### 1. Gateway L1 Construct

gateway は `infra-cdk/lib/backend-stack.ts` 内でネイティブの CloudFormation L1 constructs を用いて作成されます。

- `CfnGateway`: MCP プロトコルで AgentCore Gateway を作成
- `CfnGatewayTarget`: ツールスキーマと共に Lambda targets を構成
- JWT authorization は Cognito 経由で構成
- ライフサイクルは CloudFormation により自動管理

### 2. Sample Tool Lambda

`gateway/tools/sample_tool/sample_tool_lambda.py` に配置されています。

- 適切な Lambda target 実装を示している
- AgentCore Gateway のイベント形式の解析方法を示している
- エラーハンドリングとロギングを含む

### 3. IAM ロールと権限

**Gateway Role**: gateway が Lambda 関数を呼び出し、必要な AWS サービスにアクセスすることを許可します

**Custom Resource Role**: gateway のライフサイクル操作を管理します

### 4. SSM Parameter ストレージ

Gateway 構成は容易にアクセスできるように SSM に格納されます。

- `/stack-name/gateway_url`: Gateway エンドポイント URL
- `/stack-name/machine_client_id`: Cognito client ID
- `/stack-name/machine_client_secret`: Cognito client secret
- `/stack-name/cognito_provider`: Cognito ドメイン URL

## Gateway のテスト

### 直接的な Gateway テスト

提供されているテストスクリプトを使用して gateway の機能を検証します。

```bash
python3 scripts/test-gateway.py
```

このスクリプトは以下を実行します。

1. SSM から取得した machine client 認証情報で認証
2. MCP プロトコル経由で利用可能なツールをリスト
3. テストパラメータで sample tool を呼び出し
4. 検証用のレスポンスを表示

### AgentCore Runtime との統合

gateway は以下を介して AgentCore Runtime と統合されます。

1. **Runtime Configuration**: Runtime に SSM 経由で gateway URL が設定される
2. **Authentication**: Runtime は JWT トークンに同じ Cognito user pool を使用する
3. **Tool Discovery**: Runtime は gateway の `tools/list` エンドポイント経由でツールを発見する
4. **Tool Execution**: Runtime は gateway の `tools/call` エンドポイント経由でツールを呼び出す

### MCP 経由でのエージェントとの統合

エージェントは Model Context Protocol (MCP) を使用して Gateway に接続します。以下に 2 つの代表的な統合アプローチを示します。

#### LangGraph と MultiServerMCPClient の組み合わせ

`langchain-mcp-adapters` の `MultiServerMCPClient` は、自動的なセッション管理を提供します。

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent

# Gateway 構成で MCP クライアントを作成
mcp_client = MultiServerMCPClient({
    "gateway": {
        "transport": "streamable_http",
        "url": gateway_url,
        "headers": {
            "Authorization": f"Bearer {access_token}"
        }
    }
})

# Gateway からツールを読み込む
tools = await mcp_client.get_tools()

# ツールを伴うエージェントを作成
graph = create_react_agent(
    model=bedrock_model,
    tools=tools,
    checkpointer=checkpointer
)
```

#### Strands と直接的な MCP セッションの組み合わせ

Strands エージェントはより細かな制御のために MCP セッションを直接管理することができます。

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from langchain_mcp_adapters.tools import load_mcp_tools

async with streamablehttp_client(
    gateway_url,
    headers={"Authorization": f"Bearer {access_token}"}
) as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await load_mcp_tools(session)
        # エージェントでツールを使用
```

**例:** 完全な実装は `agent/strands-single-agent/basic_agent.py` を参照してください。

## 新しいツールの追加

gateway に新しいツールを追加するには以下を行います。

1. **Lambda 関数を作成**: Lambda target パターンに従ってツールロジックを実装
2. **ツールスキーマを定義**: CDK スタックに JSON schema 定義を追加
3. **Gateway 構成を更新**: gateway custom resource に新しい target を追加
4. **デプロイ**: CDK deploy を実行してインフラストラクチャを更新

### 例: Weather ツールの追加

```typescript
// backend-stack.ts 内
const weatherLambda = new lambda.Function(this, "WeatherToolLambda", {
  runtime: lambda.Runtime.PYTHON_3_13,
  handler: "weather_tool.handler",
  code: lambda.Code.fromAsset(path.join(__dirname, "../../gateway/tools/sample_tool")),
})

const weatherToolSchema = {
  name: "get_weather",
  description: "Get current weather for a location",
  inputSchema: {
    type: "object",
    properties: {
      location: {
        type: "string",
        description: "City and state, e.g. 'Seattle, WA'",
      },
    },
    required: ["location"],
  },
}
```

## セキュリティ上の考慮事項

### 認証

- Cognito client credentials を用いた machine-to-machine 認証
- 設定可能な有効期限を持つ JWT トークン
- Cognito resource server を用いたスコープ付きアクセス

### 認可

- Gateway はリクエストごとに JWT トークンを検証する
- Lambda 関数は gateway の IAM ロール権限を継承する
- すべてのコンポーネントで最小権限の原則

### ネットワークセキュリティ

- Gateway エンドポイントは HTTPS のみ
- Lambda 関数は AWS マネージド VPC 内で動作する
- Lambda 関数は直接インターネットアクセスを必要としない

## モニタリングとロギング

### CloudWatch Logs

- Gateway 操作は `/aws/bedrock-agentcore/gateway/*` に記録される
- Lambda 関数のログは `/aws/lambda/function-name`
- カスタムリソース操作は `/aws/lambda/gateway-custom-resource`

### メトリクス

- Gateway の呼び出しメトリクスは CloudWatch 経由で利用可能
- Lambda 関数の duration およびエラーメトリクス
- Lambda 関数にカスタムメトリクスを追加することも可能

## トラブルシューティング

### よくある問題

**"Unknown tool: None" エラー**

- Lambda 関数が context を正しく解析していないことを示します
- Lambda が AgentCore Gateway の入力形式に従っているか確認してください
- 詳細なエラー情報は CloudWatch logs を確認してください

**認証失敗**

- SSM 内の Cognito client 認証情報を確認してください
- JWT トークンの有効期限を確認してください
- gateway authorization 構成が正しいことを確認してください

**ツールが見つからない**

- ツールスキーマが Lambda の実装と一致するか確認してください
- gateway target 構成を確認してください
- Lambda 関数がデプロイされアクセス可能であることを確認してください

**Gateway が "An internal error occurred" を返す**

- CDK construct で `exceptionLevel: 'DEBUG'` を設定するか、AWS CLI 経由で gateway を更新してデバッグを有効にし、詳細なエラーメッセージを確認できます。

```bash
# gateway のデバッグを有効化
aws bedrock-agentcore-control update-gateway \
  --gateway-identifier <GATEWAY_ID> \
  --name <GATEWAY_NAME> \
  --role-arn <ROLE_ARN> \
  --protocol-type MCP \
  --authorizer-type CUSTOM_JWT \
  --authorizer-configuration <AUTH_CONFIG> \
  --exception-level DEBUG
```

または CDK 内の gateway construct を更新します。

```typescript
const gateway = new bedrockagentcore.CfnGateway(this, "AgentCoreGateway", {
  name: `${config.stack_name_base}-gateway`,
  roleArn: gatewayRole.roleArn,
  protocolType: "MCP",
  exceptionLevel: "DEBUG", // 詳細エラーメッセージのためにこの行を追加
  // ... 残りの構成
})
```

### デバッグ手順

1. **SSM パラメータの確認**: すべての gateway 構成パラメータが存在するか検証
2. **認証のテスト**: テストスクリプトでトークン生成を検証
3. **CloudWatch Logs のレビュー**: gateway および Lambda 関数のログを確認
4. **ツールスキーマの検証**: スキーマが期待される形式と一致するか確認
5. **Lambda の直接テスト**: Lambda 関数を独立して呼び出してロジックを検証

## ベストプラクティス

### Lambda 関数開発

- デバッグのために受信イベントを必ずログに記録する
- 適切なエラーハンドリングを実装し、意味のあるエラーメッセージを返す
- 構成には環境変数を使用する
- 関数は単一のツールに責任を集中させる

### スキーマ設計

- ツールおよびパラメータの説明は明確かつ説明的に書く
- 適切な JSON schema 型と制約を使用する
- 役立つ場合は説明文に例を含める
- 入力スキーマはシンプルで焦点を絞ったものに保つ

### デプロイ

- gateway と統合する前に各ツールを個別にテストする
- Lambda 関数のデプロイにバージョンタグを使用する
- デプロイ後は CloudWatch メトリクスを監視する
- 本番環境への変更には段階的なロールアウトを実装する

## 関連ドキュメント

- [Identity Propagation & Cedar Policy Guide](IDENTITY_POLICY.md) - Gateway tools のためのユーザーレベルアクセス制御
- [Cedar Policy Guide](CEDAR_POLICY_GUIDE.md) - Cedar policy の構文、機能、リファレンス
- [Replacing Cognito](REPLACING_COGNITO.md) - Identity provider の差し替えと Gateway interceptors のガイド
- [Runtime-Gateway Authentication](RUNTIME_GATEWAY_AUTH.md) - Runtime と Gateway 間の M2M トークンフロー
- [Deployment Guide](DEPLOYMENT.md) - FAST インフラストラクチャのデプロイ方法
- [AWS AgentCore Gateway Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore-gateway.html) - 公式 AWS ドキュメント
- [AWS Gateway Lambda Target Documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-add-target-lambda.html) - Lambda target 実装の詳細

## Design notes

本派生プロジェクト固有の設計判断 (Gateway target まわり):

- **Lambda だけでなく MCP server target**: AWS MCP server・Strands docs MCP server・長期記憶のメタ想起 MCP server を Gateway の MCP server target として公開する。agent 内に直接組み込むのではなく target 化することで、ネイティブツールと MCP ツールでツール面・認証・Cedar ポリシーを統一できる。
- **AWS MCP target は広めの role を継承**: AWS MCP server の `call_aws` と `run_script` を成立させるには Gateway target role に AWS サービス列挙・呼び出し権限が必要。デモでは read-broad / write-narrow に絞ってある。プロダクション利用時は脅威モデルに応じて再評価する想定。
- **SigV4 service name の固定**: Runtime 内から Gateway target endpoint を呼ぶ際、SigV4 署名の service identifier が重要なため、推論ではなくコードで固定している。軽微な API 変更に伴う認証無音失敗を避ける狙い。
- **マネージド Web Search target**: GA された Web Search connector を Gateway target として配線し、別途検索 SDK やブラウザツールを抱え込まずに引用付きの一般質問応答ができるようにしている。
