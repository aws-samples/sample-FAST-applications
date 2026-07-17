# Identity Propagation & Cedar Policy ガイド

このドキュメントでは、FAST がフロントエンドから AgentCore Gateway の Cedar policy までユーザーアイデンティティをどのように伝搬し、Gateway ツール上できめ細かなユーザー単位のアクセス制御を実現するかを説明します。

## 概要

AgentCore Gateway は CUSTOM_JWT authorizer によって検証された OAuth2 トークンでリクエストを認証します。デフォルトでは、Runtime は Client Credentials フローで M2M トークンを取得し、すべてのリクエストが同じマシンアイデンティティを持つことになります。つまり Gateway は個々のユーザーを区別できません。

この機能は、既存の M2M フローの上に **アイデンティティ伝搬** を追加します。認証済みユーザーのアイデンティティと Cognito グループメンバーシップは、Cognito の `aws_client_metadata` パラメータと V3 Pre-Token Lambda トリガを使って M2M トークンに埋め込まれます。Runtime は M2M トークンを AgentCore Identity（Token Vault）経由で取得するため、パブリックな Cognito ホストドメインを直接呼び出すことはなく、Runtime を閉域（NAT 不要）に保てます。リッチ化されたトークンは Gateway で Cedar policy により評価され、「財務部門のユーザーのみが破壊的ツールを実行できる」といったアクセス制御ルールを実現します。

**こんな時に使う:** Gateway tool が、部門・役割・ユーザー ID といった属性に基づくユーザー単位のアクセス制御を必要とする場合。

> **このデモのスコープ:** この実装では、ユーザー → ツール のアクセス制御（例: 「ゲストユーザーは AgentCore Gateway から text_analysis_tool を利用できない」）を示します。AgentCore Policy は、入力検証、リクエストパラメータに基づく条件付きアクセス、複数ツールに渡るポリシーなど、追加の機能もサポートしており、これらは [Cedar Policy Capabilities](CEDAR_POLICY_GUIDE.md#cedar-policy-capabilities) に記載されています。

## AgentCore Policy とは

AgentCore Policy は、AI エージェントが実行可能な操作を制御するサービスです。エージェントとそのツールの間に立つセキュリティガードのようなもので、エージェントがツールを使おうとするたびに、ガードがルールをチェックして許可するか拒否するかを判断します。

**シンプルに言うと:**

- 誰がどのツールをどのような条件下で使えるかを定めるルール（Cedar policy）を書きます
- Policy Engine がツール呼び出しのたびにそのルールを自動的に強制します
- どのルールも明示的に許可しなければ、その操作は拒否されます（deny-by-default）
- 強制は決定的です — プロンプトエンジニアリングと違い、巧妙な言い回しでバイパスできません

**制御できること:**

| 機能                       | ルール例                                                     | このデモで実演しているか                                                             |
| -------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------------ |
| ユーザー → ツール アクセス | 「財務ユーザーのみが billing tool にアクセスできる」         | はい                                                                                 |
| 入力検証                   | 「返金額は $1000 を超えてはならない」                        | いいえ（[Cedar Policy Guide](CEDAR_POLICY_GUIDE.md#cedar-policy-capabilities) 参照） |
| 複数ツールポリシー         | 「開発者は読み取りツールは使えるが書き込みツールは使えない」 | いいえ（[Cedar Policy Guide](CEDAR_POLICY_GUIDE.md#cedar-policy-capabilities) 参照） |
| 環境分離                   | 「本番 runtime のみが本番ツールにアクセスできる」            | いいえ（[Runtime-Level Access Control](#runtime-level-access-control) 参照）         |
| 条件付きアクセス           | 「クエリが特定のアカウントを対象とする場合のみツールを許可」 | いいえ（[Cedar Policy Guide](CEDAR_POLICY_GUIDE.md#cedar-policy-capabilities) 参照） |

**このデモでは** カスタムの `department` クレームに基づくユーザー → ツール アクセス制御を実装します。他の機能も同じインフラ（Policy Engine + Cedar + Gateway）で異なるポリシー条件を使うだけで実現できます。各機能の例を含む完全な構文リファレンスは [Cedar Policy Capabilities](CEDAR_POLICY_GUIDE.md#cedar-policy-capabilities) を参照してください。

**主要コンセプト:**

- **Policy Engine** — Cedar policy を処理する評価エンジン。1 つのエンジンは 1 つの Gateway にアタッチされます。
- **Cedar Policy** — AWS のオープンソースポリシー言語 [Cedar](https://www.cedarpolicy.com/) で書かれた宣言的ルール。確率的ではなく決定的です。
- **CUSTOM_JWT Authorizer** — トークンを検証し、JWT クレームを Cedar の principal タグにマッピングする Gateway コンポーネント。
- **Deny-by-default** — どの `permit` 文にもマッチしなければ、リクエストは拒否されます。明示的な `forbid` は不要です。
- **ツールフィルタリング** — 拒否されたツールは実行時にブロックされるだけでなく、ディスカバリー時（`tools/list`）にエージェントから隠されます。[Tool Discovery vs Execution](CEDAR_POLICY_GUIDE.md#tool-discovery-vs-execution) を参照してください。

## アーキテクチャ / フロー

アイデンティティ伝搬フローは 6 ステップで構成されます。

```
1. ユーザーがログイン → フロントエンドが Cognito から JWT を取得（access token は cognito:groups を含む）
2. フロントエンドがリクエストを送信 → Runtime が JWT を検証し user_id（sub）と cognito:groups を抽出
3. Runtime が AgentCore Identity（Token Vault）経由で M2M トークンを要求し、aws_client_metadata に user_id + groups を載せる
4. Cognito V3 Pre-Token Lambda が発火 → グループを department/role クレームにマッピング → M2M トークンに注入
5. Runtime がリッチ化された M2M トークンで Gateway tool を呼び出し
6. Gateway の CUSTOM_JWT Authorizer がトークンクレームを Cedar principal タグにマッピング → Policy Engine が Cedar policy を評価 → 許可または拒否
```

主要なセキュリティ特性: `user_id`（`sub`）と `cognito:groups` はいずれも Runtime の Session Context で検証済みの JWT から取得され、LLM やリクエストペイロードからではありません。これにより、エンドツーエンドで暗号学的に安全なアイデンティティチェーンが保証されます。

## コンポーネント

### Cognito ESSENTIALS Tier

**ファイル:** `infra-cdk/lib/cognito-stack.ts`

Cognito User Pool は `featurePlan: ESSENTIALS` で構成されます。これは、V3 Pre-Token Generation Lambda トリガが ESSENTIALS tier を有効にした場合にのみ Client Credentials（M2M）グラントで発火するため、必須となります。これがないと、M2M トークン生成時に Pre-Token Lambda が呼び出されません。

### V3 Pre-Token Lambda

**ファイル:** `infra-cdk/lambdas/pretoken-v3/index.py`

この Lambda はあらゆるトークン生成イベント（ユーザーログインと M2M の両方）で発火します。M2M フロー（`TokenGeneration_ClientCredentials`）のみを処理し、ユーザーログインフローはスキップします。

M2M フローでは、`clientMetadata` から `verified_groups`（ユーザーの `cognito:groups`、カンマ区切り）を読み取り、グループ名を department / role クレームにマッピングします。

| Cognito グループ | Department  | Role      |
| ---------------- | ----------- | --------- |
| `finance`        | finance     | admin     |
| `engineering`    | engineering | developer |
| （グループなし） | guest       | viewer    |

これらのクレームは `claimsToAddOrOverride` 経由で M2M アクセストークンに注入されます。

- `user_id` — 認証済みユーザーの ID（`sub`）
- `department` — ユーザーの部門（Cognito グループ名）
- `role` — ユーザーの役割

> **補足:** これらのクレーム名（`user_id`、`department`、`role`）はカスタムのアプリケーション定義クレームで、標準的な JWT/OIDC クレームではありません。必要に応じて任意の名前を定義できます。詳細は [Understanding Claims](CEDAR_POLICY_GUIDE.md#understanding-claims-custom-vs-standard) を参照してください。

グループは検証済みの access token から直接読み取られるため、Lambda は `AdminListGroupsForUser` を呼び出しません。割り当てを変更するには、ユーザーを Cognito グループに追加・削除するか、Pre-Token Lambda 内の `GROUP_ROLES` マップを編集してください。

### Cedar Policy ファイル

**ディレクトリ:** `gateway/policies/`

Cedar policy は Gateway tool に対するアクセス制御ルールを定義します。1 ファイル = 1 Cedar ステートメント（AgentCore `CreatePolicy` は 1 ポリシーにつき 1 ステートメント）です。デプロイ時に CDK によって読み込まれ、`//` のコメント行が削除され、`{{GATEWAY_ARN}}` プレースホルダが実際の Gateway ARN に置換されます。

| ファイル                       | ツール                                                   | 許可される部門       |
| ------------------------------ | -------------------------------------------------------- | -------------------- |
| `01-sample-tool.cedar`         | `sample-tool-target___text_analysis_tool`                | finance, engineering |
| `02-aws-mcp-read.cedar`        | AWS MCP の読み取り系ツール（`aws-mcp___aws___*`）        | finance, engineering |
| `03-aws-mcp-destructive.cedar` | `aws-mcp___aws___call_aws`、`aws-mcp___aws___run_script` | finance のみ         |

guest（グループなし）はどの `permit` にもマッチせず、Cedar の deny-by-default により拒否されます。engineering は読み取り系ツールを使えますが、破壊的な `call_aws` / `run_script` は使えません。finance はすべて使えます。マトリクスを変更するには該当ファイルを編集し `cdk deploy` を実行します。

### Policy Engine カスタムリソース

**ファイル:**

- `infra-cdk/lambdas/cedar-policy/index.py` — Custom Resource Lambda
- `infra-cdk/lib/backend-stack.ts` — CDK リソース定義

AgentCore Policy には L1/L2 CDK construct が存在しないため、CloudFormation Custom Resource が Policy Engine のライフサイクル全体を管理します。Lambda は 3 つの CloudFormation イベントを処理します。

- **Create:** Policy Engine を作成 → Cedar Policy を作成 → Policy Engine を Gateway にアタッチ
- **Update:** 既存ポリシーを削除 → 更新されたドキュメントで新規ポリシーを作成 → エンジンが Gateway にアタッチされたままか確認
- **Delete:** Policy Engine を Gateway からデタッチ → すべてのポリシーを削除 → Policy Engine を削除

すべての操作は公式の boto3 waiter（`policy_engine_active`、`policy_engine_deleted`、`policy_active`、`policy_deleted`）を使います。Gateway のステータス変更は公式 waiter が存在しないため、カスタムポーリングループを使います。

### Gateway Authorizer

**ファイル:** `infra-cdk/lib/backend-stack.ts`

Gateway は Cognito の OIDC discovery URL とマシンクライアント ID で構成された `CUSTOM_JWT` authorizer を使います。authorizer は M2M トークンを検証し、JWT クレームを Cedar の principal タグにマッピングします。

| JWT クレーム | Cedar Principal タグ             | クレームタイプ                      |
| ------------ | -------------------------------- | ----------------------------------- |
| `department` | `principal.getTag("department")` | カスタム（Pre-Token Lambda が注入） |
| `role`       | `principal.getTag("role")`       | カスタム（Pre-Token Lambda が注入） |
| `user_id`    | `principal.getTag("user_id")`    | カスタム（Pre-Token Lambda が注入） |

## Cedar Policy ガイド

クレーム、アクションフォーマット、スキーマ制約、ツールディスカバリー vs 実行、ポリシー機能を含む完全な Cedar policy リファレンスについては [Cedar Policy Guide](CEDAR_POLICY_GUIDE.md) を参照してください。

## Gateway 認証: AgentCore Identity（Token Vault）

Runtime は Gateway 用の M2M トークンを AgentCore Identity の `@requires_access_token` デコレータ経由で取得します（各パターンの `tools/gateway.py` を参照）。AgentCore Identity が Cognito トークン交換を AWS 内のサーバー側で実行し、`bedrock-agentcore` VPC エンドポイント経由で到達可能なため、Runtime はパブリックな Cognito ホストドメインを呼び出さず、NAT Gateway は不要です。

ユーザーアイデンティティの伝搬は、デコレータに `custom_parameters={"aws_client_metadata": json.dumps({"verified_user_id": <sub>, "verified_groups": <cognito:groups のカンマ区切り>})}` を渡すことで行います。`aws_client_metadata` は Cognito が Pre-Token Lambda に転送する唯一の `custom_parameters` キー（`ClientMetadata` として）であり、フラットな文字列マップである必要があるため、グループはカンマ区切り文字列に結合します。Lambda は `verified_groups` を読み取り、Cedar policy が評価する `department`/`role` クレームを注入します。

> **Cognito を置き換えるには?** Cognito を別の Identity Provider（Okta、Auth0、Entra ID など）に切り替える方法や、動的なアクセス制御に Gateway Interceptor を使う方法は [Replacing Cognito](REPLACING_COGNITO.md) を参照してください。

## カスタマイズ

### グループ割り当ての変更

最も簡単な変更は、ユーザーを `finance` / `engineering` の Cognito グループに追加・削除することです（コード変更も再デプロイも不要）。グループとクレームのマッピングを変えるには、`infra-cdk/lambdas/pretoken-v3/index.py` の `GROUP_ROLES` マップを編集します。まったく別のアイデンティティソース（DynamoDB、LDAP など）を使う場合は、`verified_groups` の参照部分を独自の解決に置き換えてください。

### 新しいクレームの追加

M2M トークンに新しいクレームを追加するには:

1. Pre-Token Lambda の `claimsToAddOrOverride` にクレームを追加
2. Cedar policy で `principal.getTag("claim_name")` を使ってクレームを参照
3. Gateway の構成変更は不要 — CUSTOM_JWT authorizer がすべての JWT クレームを Cedar タグに自動マッピングします

### VPC モード

VPC モードのデフォルトデプロイは **完全閉域（NAT Gateway なし）** です。M2M トークンを AgentCore Identity 経由で取得するため（Cognito トークン交換は AWS 内のサーバー側で実行され、`bedrock-agentcore` VPC エンドポイント経由で到達可能）、Runtime はアウトバウンドのインターネットアクセスを必要としません。プライベートサブネットは隔離（`0.0.0.0/0` ルートなし）され、すべての AWS アクセスは VPC エンドポイント経由で行われます。

VPC 構成の詳細は `docs/DEPLOYMENT.md` を参照してください。

### Runtime レベルのアクセス制御

デフォルトでは、Gateway 経由のすべてのリクエストは同じマシンクライアントアイデンティティを共有します。複数の AgentCore Runtime をデプロイし、どの runtime がどのツールにアクセスできるかを制御したい場合、Cognito の `clientId` を暗号学的に検証された runtime アイデンティティとして使えます。

**Cedar で `context.runtime.arn` を使えないのはなぜ?**
Cedar スキーマは `context.input`（ツールパラメータ）のみをサポートしており、`context.runtime.arn` のようなフィールドは存在しません。サポートされていない context フィールドを参照しようとするとポリシー作成が失敗します。

**runtime アイデンティティに Cognito Groups を使えないのはなぜ?**
Cognito User Pool Groups はユーザーアイデンティティに適用され、app client には適用されません。M2M トークン自体には `cognito:groups` クレームが付かないため、runtime を識別できません。（ユーザーアイデンティティには引き続きグループを使います — 上述のとおり Runtime はユーザーの access token から `cognito:groups` を読み取り `aws_client_metadata` で伝搬します。これはここで論じる runtime ごとの `clientId` アイデンティティとは別軸です。）

**解決策: Runtime ごとに Cognito App Client を 1 つ用意する**

各 CDK スタックは Cognito app client と AgentCore Runtime の両方を作成するため、`clientId` は runtime アイデンティティとして機能します — `client_secret` 経由で暗号学的に検証されます。Pre-Token Lambda は自己申告なしで `clientId` を `runtime_env` クレームにマッピングします。

**アーキテクチャ:**

```
Runtime A (production) → Client A (client_secret_A) で認証
                          → Cognito が clientId = "abc123" を検証
                          → Pre-Token Lambda が "abc123" → runtime_env: "production" にマッピング
                          → Cedar policy が principal.getTag("runtime_env") をチェック

Runtime B (staging)    → Client B (client_secret_B) で認証
                          → Cognito が clientId = "def456" を検証
                          → Pre-Token Lambda が "def456" → runtime_env: "staging" にマッピング
                          → Cedar policy が principal.getTag("runtime_env") をチェック
```

**ステップ 1: CDK で別々のマシンクライアントを作成**

```typescript
// runtime 環境ごとに 1 つのマシンクライアントを作成
const machineClientProd = new cognito.UserPoolClient(this, "MachineClientProd", {
  userPool: this.userPool,
  generateSecret: true,
  oAuth: {
    flows: { clientCredentials: true },
    // 既存のマシンクライアントと同じ resource server スコープを使用
    scopes: [
      cognito.OAuthScope.resourceServer(
        resourceServer,
        new cognito.ResourceServerScope({ scopeName: "read", scopeDescription: "Read access" })
      ),
      cognito.OAuthScope.resourceServer(
        resourceServer,
        new cognito.ResourceServerScope({ scopeName: "write", scopeDescription: "Write access" })
      ),
    ],
  },
})

const machineClientStaging = new cognito.UserPoolClient(this, "MachineClientStaging", {
  userPool: this.userPool,
  generateSecret: true,
  oAuth: {
    flows: { clientCredentials: true },
    scopes: [
      cognito.OAuthScope.resourceServer(
        resourceServer,
        new cognito.ResourceServerScope({ scopeName: "read", scopeDescription: "Read access" })
      ),
      cognito.OAuthScope.resourceServer(
        resourceServer,
        new cognito.ResourceServerScope({ scopeName: "write", scopeDescription: "Write access" })
      ),
    ],
  },
})

// マッピングを環境変数として Pre-Token Lambda に渡す
preTokenLambda.addEnvironment(
  "CLIENT_RUNTIME_MAP",
  JSON.stringify({
    [machineClientProd.userPoolClientId]: "production",
    [machineClientStaging.userPoolClientId]: "staging",
  })
)
```

**ステップ 2: Pre-Token Lambda で clientId → runtime_env のマッピングを行う**

```python
import os, json

def lambda_handler(event, context):
    if event["triggerSource"] != "TokenGeneration_ClientCredentials":
        return event

    # clientId は Cognito によって検証済み
    client_id = event["callerContext"]["clientId"]

    # CDK がデプロイ時にセットしたマッピング
    client_runtime_map = json.loads(os.environ.get("CLIENT_RUNTIME_MAP", "{}"))
    runtime_env = client_runtime_map.get(client_id, "unknown")

    # 既存のユーザーアイデンティティロジック（変更なし）
    meta = event["request"].get("clientMetadata", {})
    user_id = meta.get("verified_user_id", "")
    groups = [g for g in meta.get("verified_groups", "").split(",") if g]

    GROUP_ROLES = {"finance": "admin", "engineering": "developer"}
    department = next((g for g in groups if g in GROUP_ROLES), "guest")
    role = GROUP_ROLES.get(department, "viewer")

    event["response"]["claimsAndScopeOverrideDetails"] = {
        "accessTokenGeneration": {
            "claimsToAddOrOverride": {
                "user_id": user_id,
                "department": department,
                "role": role,
                "runtime_env": runtime_env,
            }
        }
    }
    return event
```

**ステップ 3: Cedar policy に runtime_env を追加**

```cedar
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"sample-tool-target___text_analysis_tool",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("runtime_env") &&
  principal.getTag("runtime_env") == "production" &&
  principal.hasTag("department") &&
  (principal.getTag("department") == "finance" ||
   principal.getTag("department") == "engineering")
};
```

**セキュリティモデル — 二層構造のアイデンティティ:**

| レイヤー                 | クレーム                        | ソース                                                                  | 信頼レベル                                                             |
| ------------------------ | ------------------------------- | ----------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Runtime アイデンティティ | `runtime_env`                   | `callerContext.clientId`（Cognito 検証済み）                            | 暗号学的 — `client_secret` が必要                                      |
| ユーザーアイデンティティ | `user_id`、`department`、`role` | `clientMetadata.verified_user_id`（検証済み JWT の `sub` クレーム由来） | JWT 検証済み — Runtime が Cognito 検証済みトークンからサーバー側で抽出 |

両レイヤーとも Cognito によって保護されています: `clientId` は client secret 交換で検証され、`user_id` は Runtime の `extract_user_id_from_context()` によって抽出された検証済み JWT の `sub` クレームに由来します。

> **補足:** このセクションでは runtime レベルのアクセス制御のアーキテクチャパターンを説明しています。現状の FAST 実装は単一のマシンクライアントを使っています。このパターンを実装するには、`cognito-stack.ts` で追加のマシンクライアントを作成し、上記のマッピングロジックで Pre-Token Lambda を更新してください。

## デプロイ済みポリシーの確認

Gateway に現在アクティブな Cedar policy を確認するには:

1. **AWS Console → Bedrock AgentCore → Policy** に移動
2. Policy engines セクションから対象の Policy Engine（例: `fast_specialist_agent_policy_engine`）をクリック
3. **Policies** セクションで対象のポリシー（例: `fast_specialist_agent_policy_engine_cp_<timestamp>`）をクリック
4. **Definition** セクションにポリシーの構成要素が表示されます:
   - **Effect**: `permit` または `forbid`
   - **Scope: Principal**: `AgentCore::OAuthUser`
   - **Scope: Actions**: ツールアクション名（例: `sample-tool-target___text_analysis_tool`）
   - **Scope: Resource**: Gateway 名
   - **Conditions**: `when` 句のロジック
5. **Cedar** セクションにはデプロイ済みの完全な Cedar policy 文が表示されます

これを使って、`cdk deploy` で期待したポリシーバージョンが適用されたかを確認できます。

## トレースによるポリシー判定の検証

Cedar policy の許可/拒否判定を CloudWatch ログで確認するには:

1. **AWS Console → Bedrock AgentCore → Runtimes** に移動
2. Runtime resources セクションから対象の runtime（例: `fast_specialist_agent_FASTAgent`）をクリック
3. **Tracing** セクションまでスクロールし、**Edit** をクリックして **Enable tracing** を Enable に切り替え
4. **Bedrock AgentCore → Gateways** に移動
5. 対象の gateway（例: `fast-specialist-agent-gateway`）をクリックし、**Tracing** までスクロールして **Edit** をクリック、**Enable tracing** を Enable に切り替え
6. フロントエンドからツール呼び出しをトリガーするクエリを実行
7. **CloudWatch Console → Log Management → Log groups** に移動
8. `aws/spans` ロググループを検索してクリックし、デフォルトのログストリームをクリック
9. **Filter events** 検索ボックスに `policy` と入力
10. `AgentCore.Policy.PartiallyAuthorizeActions` スパンを探します — 含まれる情報:
    - `aws.agentcore.policy.allowed_tools`: ユーザーが利用を許可されたツール
    - `aws.agentcore.policy.denied_tools`: ユーザーがアクセスを拒否されたツール
    - `aws.agentcore.gateway.policy.mode`: `ENFORCE` と表示されるはず
