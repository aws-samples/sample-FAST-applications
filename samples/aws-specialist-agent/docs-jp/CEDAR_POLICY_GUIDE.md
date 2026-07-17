# Cedar Policy ガイド

このドキュメントでは、AgentCore Gateway 向けの Cedar policy の書き方、管理方法、拡張方法について解説します。アイデンティティ伝搬アーキテクチャやコンポーネントセットアップについては [Identity Propagation & Cedar Policy Guide](IDENTITY_POLICY.md) を参照してください。

## クレームの理解（カスタム vs 標準）

Cedar policy は `principal.getTag("claim_name")` を介して JWT クレームを参照します。これらのクレームは 2 つのカテゴリに分かれます。

### カスタムクレーム（アプリケーション定義）

カスタムクレームは V3 Pre-Token Lambda が `claimsToAddOrOverride` 経由で注入します。標準的な JWT/OIDC クレームセットの一部ではなく、アプリケーションのアクセス制御のニーズに基づいて定義されます。

**このデモのクレーム:**

| クレーム     | 用途                               | 値の例                   |
| ------------ | ---------------------------------- | ------------------------ |
| `user_id`    | 認証済みユーザーのアイデンティティ | `"yourname@company.com"` |
| `department` | ユーザーの組織単位                 | `"finance"`              |
| `role`       | ユーザーの権限レベル               | `"admin"`                |

**カスタムクレームの追加例:**

| クレーム          | ユースケース               | Cedar での利用                                       |
| ----------------- | -------------------------- | ---------------------------------------------------- |
| `tenant_id`       | マルチテナント分離         | `principal.getTag("tenant_id") == "example-corp"`    |
| `clearance_level` | 階層化されたデータアクセス | `principal.getTag("clearance_level") == "top-level"` |
| `region`          | 地理的に制限されたアクセス | `principal.getTag("region") == "us-east-1"`          |
| `runtime_env`     | Runtime レベルの分離       | `principal.getTag("runtime_env") == "production"`    |

カスタムクレームの追加方法: Pre-Token Lambda の `claimsToAddOrOverride` dict にクレームを注入し、Cedar 内で `principal.getTag("claim_name")` を使って参照します。Gateway の構成変更は不要 — CUSTOM_JWT authorizer がすべての JWT クレームを Cedar タグに自動マッピングします。

### 標準クレーム（Cognito 管理）

標準クレームは Cognito によって自動的にすべてのトークンに含められます。Pre-Token Lambda で上書きすることはできません。

| クレーム              | 説明                                         | 変更可能? |
| --------------------- | -------------------------------------------- | --------- |
| `sub`                 | Subject 識別子（M2M の場合は app client ID） | 不可      |
| `iss`                 | トークン発行者（Cognito user pool URL）      | 不可      |
| `client_id`           | App client ID                                | 不可      |
| `token_use`           | 常に `"access"`                              | 不可      |
| `scope`               | 付与された OAuth スコープ                    | 不可      |
| `exp` / `iat` / `jti` | トークンのタイミングと ID                    | 不可      |

標準クレームも Cedar 内で `principal.getTag()` 経由でアクセス可能ですが、ビジネスロジックよりもインフラレベルのチェックに使われるのが一般的です。

### クレームとして利用できないもの

| データ                | 理由                       | 代替手段                                                                                                                             |
| --------------------- | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| リクエストヘッダ / IP | Cedar に公開されていない   | N/A（サポートされていません）                                                                                                        |
| Runtime ARN           | Cedar スキーマに存在しない | Pre-Token Lambda 経由で `runtime_env` を注入（[Runtime-Level Access Control](IDENTITY_POLICY.md#runtime-level-access-control) 参照） |
| ツール入力パラメータ  | クレームではない           | Cedar 内で `context.input.<field>` を使用                                                                                            |

## Policy ファイルの場所

`gateway/policies/` — 1 ファイル = 1 Cedar ステートメント（AgentCore `CreatePolicy` は 1 ポリシーにつき 1 ステートメント）です。出荷されるセットは以下のとおりです。

| ファイル                       | ツール                                                   | 許可される部門       |
| ------------------------------ | -------------------------------------------------------- | -------------------- |
| `01-sample-tool.cedar`         | `sample-tool-target___text_analysis_tool`                | finance, engineering |
| `02-aws-mcp-read.cedar`        | AWS MCP の読み取り系ツール                               | finance, engineering |
| `03-aws-mcp-destructive.cedar` | `aws-mcp___aws___call_aws`、`aws-mcp___aws___run_script` | finance のみ         |

該当ファイルを編集して `cdk deploy` を実行すると変更が適用されます。Custom Resource Lambda が Policy Engine を再作成することなくポリシーをインプレースで更新します。guest（グループなし）はどの `permit` にもマッチせず、deny-by-default により拒否されます。

## アクション名のフォーマット

Cedar のアクション名は `<TargetName>___<tool_name>`（アンダースコア 3 つ）のフォーマットに従います。

- **TargetName** は `backend-stack.ts` 内の `CfnGatewayTarget` 名から取得（例: `sample-tool-target`）
- **tool_name** は `tool_spec.json` から取得（例: `text_analysis_tool`）
- 結合: `sample-tool-target___text_analysis_tool`

これらは大文字小文字を区別します。不一致があると、ポリシーロジックが正しく見えてもすべてのリクエストが密かに拒否されます。

## Deny-by-Default

Cedar は deny-by-default です: いずれの `permit` 文もリクエストにマッチしなければ、自動的に拒否されます。アクセスをブロックするのに明示的な `forbid` 文は不要 — permit の条件から該当の部門を単に外せばよいだけです。

例えば、ゲストを拒否するには、permit の部門リストから `"guest"` を削除します。`forbid` 文は不要です。

## ツールディスカバリー vs 実行

AgentCore Policy Engine はツールライフサイクルの **2 つの時点** で認可を強制します。

### 1. ディスカバリー（`tools/list`）— ツールフィルタリング

Runtime が Gateway に対して `tools/list` を呼び出すと、Policy Engine は `PartiallyAuthorizeActions` を使って **すべてのツール** を呼び出し元のアイデンティティに対して評価します。呼び出し元が利用を許可されていないツールは **レスポンスから削除** されます。エージェントはそれらの存在に気付きません。

```
Agent → Runtime → Gateway tools/list → Policy Engine (PartiallyAuthorizeActions)
                                         ↓
                                    各ツールを principal のクレームに対して評価
                                         ↓
                                    許可されたツールのみを返す
                                         ↓
                              Agent はフィルタリングされたツールリストを受け取る
```

**効果:** Version 2（ゲスト拒否）がアクティブな状態で `department=guest` のユーザーが `tools/list` を呼び出すと、`text_analysis_tool` はレスポンスに含まれません。エージェントはツールの存在を知らないため、呼び出そうとすることもありません。

### 2. 実行（`tools/call`）— 完全コンテキスト強制

エージェントが特定のツールを呼び出すと、Policy Engine はツールの入力パラメータ（`context.input`）を含む **完全なコンテキスト** でリクエストを評価します。実際のリクエストペイロードにアクセスできるため、ディスカバリーよりも厳格な評価です。

```
Agent → Runtime → Gateway tools/call → Policy Engine (AuthorizeAction)
                                         ↓
                                    principal クレーム + context.input を評価
                                         ↓
                                    Allow → ツール実行
                                    Deny  → 認可エラーを返す
```

**なぜ両方必要か?** ツールがディスカバリーフィルタリングを通過する（ユーザーは一般的にツール利用を許可されている）ものの、入力固有の条件で実行時に拒否される場合があります。例えば:

```cedar
// ユーザーは refund ツールをディスカバリーできる（tools/list フィルタリングを通過）
// ただし amount > 1000 の場合は実行が拒否される（tools/call チェックで失敗）
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"billing-target___process_refund",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("department") &&
  principal.getTag("department") == "finance" &&
  context.input.amount < 1000
};
```

この例では、finance ユーザーは `tools/list` で `process_refund` を確認できます（finance 部門だから）が、$5000 の返金を処理しようとすると、`context.input.amount < 1000` が失敗するため `tools/call` は拒否されます。

### CloudWatch でのディスカバリーフィルタリングの確認

拒否されたツールが `tools/list` からフィルタリングされていることを確認するには:

1. Runtime と Gateway の両方でトレーシングを有効化（[Verifying Policy Decisions via Tracing](IDENTITY_POLICY.md#verifying-policy-decisions-via-tracing) 参照）
2. フロントエンドからクエリをトリガー
3. CloudWatch → `aws/spans` ロググループで `PartiallyAuthorizeActions` をフィルタ
4. スパンに含まれる情報:
   - `aws.agentcore.policy.allowed_tools`: エージェントに返されたツール
   - `aws.agentcore.policy.denied_tools`: フィルタアウトされたツール
   - `aws.agentcore.gateway.policy.mode`: `ENFORCE` と表示されるはず

> **Runtime レベルでの確認:** エージェントコードにログ行を追加して、Cedar policy フィルタリング後にエージェントが受け取ったツールを確認します。Strands エージェントパターンの例:
>
> **Strands パターン**（`agent/strands-single-agent/basic_agent.py`）— `Agent()` 作成後に追加:
>
> ```python
> agent = Agent(
>     name="strands_agent",
>     tools=[gateway_client, code_tools.execute_python_securely],
>     ...
> )
> specs = agent.tool_registry.get_all_tool_specs()
> logger.info(f"[GATEWAY] Raw tool specs: {specs}")
> return agent
> ```
>
> **確認場所:** CloudWatch → Log groups → `/aws/bedrock-agentcore/runtimes/{runtime_name}` → ログストリーム `otel-rt-logs`。`[GATEWAY] Raw tool specs` で検索。

### サマリー

| ステージ                       | API                         | 評価対象                                       | 拒否時の挙動                                        |
| ------------------------------ | --------------------------- | ---------------------------------------------- | --------------------------------------------------- |
| ディスカバリー（`tools/list`） | `PartiallyAuthorizeActions` | Principal クレームのみ（入力コンテキストなし） | ツールが隠される — エージェントは存在を知らない     |
| 実行（`tools/call`）           | `AuthorizeAction`           | Principal クレーム + `context.input`           | リクエスト拒否 — エージェントは認可エラーを受け取る |

## 新しいツールの追加

新しい Gateway target とツールを追加する場合:

1. `backend-stack.ts` で新しい Lambda ツールと `CfnGatewayTarget` を作成
2. 正しいアクション名で `gateway/policies/` に新しいポリシーファイル（1 ステートメント）を追加
3. `cdk deploy` を実行

各 `create_policy` 呼び出しは 1 つの Cedar 文を含む 1 つのポリシーを作成します。Custom Resource は現状デプロイごとに単一ポリシーを作成します。複数のポリシーを追加するには（例: permit と forbid 文を別々に）、文ごとに `create_policy()` を呼び出すように Custom Resource Lambda を更新してください。

## Cedar スキーマ制約

AgentCore Gateway は、Gateway の MCP ツールマニフェストから自動生成されたスキーマに対して Cedar policy を検証します。サポートされていないフィールドを参照するポリシーは作成時に失敗し、CloudFormation がロールバックします。

**Cedar policy でサポートされている要素:**

| 要素                                        | 参照可能なもの                                              | 例                                                             |
| ------------------------------------------- | ----------------------------------------------------------- | -------------------------------------------------------------- |
| `principal`                                 | `AgentCore::OAuthUser` でなければならない                   | `principal is AgentCore::OAuthUser`                            |
| `principal.hasTag()` / `principal.getTag()` | CUSTOM_JWT authorizer がマッピングした任意の JWT クレーム   | `principal.getTag("department")`                               |
| `action`                                    | `<TargetName>___<tool_name>` フォーマットのツールアクション | `AgentCore::Action::"sample-tool-target___text_analysis_tool"` |
| `resource`                                  | Gateway ARN                                                 | `AgentCore::Gateway::"arn:aws:..."`                            |
| `context.input`                             | MCP マニフェストで定義されたツール入力パラメータ            | `context.input.query`                                          |

**Cedar policy でサポートされていない要素:**

| 要素                                    | 理由                                                        |
| --------------------------------------- | ----------------------------------------------------------- |
| `context.runtime.arn`                   | スキーマに存在しない — `context.input` のみ利用可能         |
| カスタムエンティティタイプ              | `AgentCore` 名前空間外でエンティティを定義できない          |
| `OAuthUser` 上のカスタム属性            | プロパティ直接アクセスではなく `hasTag()`/`getTag()` を使用 |
| リクエストメタデータ（ヘッダ、IP など） | Cedar に公開されていない                                    |

`context.input` で利用できない情報にアクセス制御の判断が依存する場合は、Pre-Token Lambda 経由で JWT クレームとして注入し、`principal.getTag()` でアクセスしてください。このパターンの例は [Runtime-Level Access Control](IDENTITY_POLICY.md#runtime-level-access-control) を参照してください。

## Cedar Policy 機能

Cedar は認可向けに設計された専用ポリシー言語です。このセクションでは、AgentCore Gateway の Cedar policy で表現できる内容を、各機能の実用的な例と共に解説します。

> **このプロジェクトで既に実演されているもの:**
>
> - アイデンティティベースのアクセス（`principal.getTag("department") == "finance"`）— [Cedar Policy ファイル](IDENTITY_POLICY.md#cedar-policy-file) の Version 1 & 2 を参照
> - 多値 OR 条件（`department == "finance" || department == "engineering"`）— Version 1 ポリシーを参照
>
> 以下の機能は同じインフラを使って実装できる **追加のパターン** を示します。

### 機能 1: 入力検証 (`context.input`)

**シナリオ:** Finance ユーザーは返金処理ができますが、$1000 までです。$1000 を超える返金には別の承認ワークフローが必要です。

**仕組み:** `context.input` により Cedar はツール入力パラメータ（MCP ツールマニフェストで定義されたもの）にアクセスできます。これらの値に対して条件を書けます。ツールは finance ユーザーには `tools/list` で表示されます（ディスカバリーは principal クレームのみをチェック）が、$1000 制限は実際の入力が利用可能な `tools/call` 時に強制されます。

```cedar
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"billing-target___process_refund",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("department") &&
  principal.getTag("department") == "finance" &&
  context.input.amount < 1000
};
```

**結果:**

- Finance ユーザー、amount=500 → 許可
- Finance ユーザー、amount=5000 → 拒否（制限超過）
- Engineering ユーザー、amount=100 → 拒否（部門が違う）

**重要:** これは [ツールディスカバリー vs 実行](#tool-discovery-vs-execution) が最も重要となる例です。ツールはディスカバリーフィルタリングを通過する（finance ユーザーは一般的に許可される）ものの、入力が条件に違反する場合は実行が拒否されます。

---

### 機能 2: 複数ツールポリシー (`action in [...]`)

**シナリオ:** 開発者はすべての読み取り専用ツール（list、get、search）を使えますが、書き込みツール（create、update、delete）は使えません。

**仕組み:** ツールごとに別々の `permit` 文を書く代わりに、`action in [...]` を使って 1 つのポリシーを複数のツールに同時に適用します。

```cedar
permit(
  principal is AgentCore::OAuthUser,
  action in [
    AgentCore::Action::"data-target___list_records",
    AgentCore::Action::"data-target___get_record",
    AgentCore::Action::"data-target___search_records"
  ],
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("role") &&
  principal.getTag("role") == "developer"
};
```

**結果:**

- 開発者が `list_records` を呼び出す → 許可
- 開発者が `search_records` を呼び出す → 許可
- 開発者が `delete_record` を呼び出す → 拒否（アクションリストに含まれない）

> **補足:** 各ツールに対して個別の `permit` 文を書くこともできます。`action in [...]` 構文は、同じ条件下でツールをグループ化するための便利な手段です。

---

### 機能 3: 明示的拒否 (`forbid`)

**シナリオ:** すべての部門がツールを使用できますが、特定のユーザー（例: 漏洩したアカウント）は部門に関係なく明示的にブロックする必要があります。

**仕組み:** `forbid` 文は `permit` 文をオーバーライドします。Cedar の競合解決は「forbid wins」です — `permit` と `forbid` の両方がマッチする場合、リクエストは拒否されます。

```cedar
// すべての部門を許可
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"sample-tool-target___text_analysis_tool",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("department")
};

// ただし特定のユーザーは明示的にブロック
forbid(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"sample-tool-target___text_analysis_tool",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("user_id") &&
  principal.getTag("user_id") == "compromised-user@example.com"
};
```

**結果:**

- 部門を持つ任意のユーザー → 許可
- `compromised-user@example.com` → 拒否（forbid が permit に勝つ）

> **補足:** Cedar の deny-by-default により、ユーザーや部門を `permit` から単に省略するだけで
> アクセスを拒否するのに十分なことが多いです。`forbid` は広範な `permit` を特定のケースで
> オーバーライドするとき（漏洩ユーザーのブロック、インシデント時のツール無効化、
> 緊急停止の実装など）に使ってください。

---

### 機能 4: ワイルドカード文字列マッチング (`like`)

**シナリオ:** `@example.com` メールドメインのユーザーのみが内部ツールにアクセスできます。他のメールドメインの外部請負業者は拒否されます。

**仕組み:** 文字列クレーム値のパターンマッチングには `like` と `*` ワイルドカードを使います。

```cedar
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"internal-target___internal_tool",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("user_id") &&
  principal.getTag("user_id") like "*@example.com"
};
```

**結果:**

- `alice@example.com` → 許可
- `bob@example.com` → 許可
- `contractor@external.com` → 拒否（パターンに一致しない）

> **補足:** `like` はワイルドカードとして `*` のみをサポートし、
> 任意の種類の文字（文字、数字、記号、ドットなど）が 0 個以上にマッチします。
> 正規表現、単一文字ワイルドカード、文字クラス、その他のパターン構文はサポートされていません。

---

### 機能 5: 環境ベースのアクセス制御

**シナリオ:** 本番ツールは本番 runtime からのみアクセス可能であるべきです。Staging runtime は、ユーザーが正しい部門・役割を持っていても本番ツールを呼び出せないようにします。

**仕組み:** Pre-Token Lambda が Cognito の `clientId` を `runtime_env` クレームにマッピングします（[Runtime-Level Access Control](IDENTITY_POLICY.md#runtime-level-access-control) 参照）。Cedar はユーザーアイデンティティと runtime 環境の両方をチェックします。

```cedar
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"prod-target___production_tool",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("runtime_env") &&
  principal.getTag("runtime_env") == "production" &&
  principal.hasTag("department") &&
  principal.getTag("department") == "finance"
};
```

**結果:**

- 本番 runtime からの finance ユーザー → 許可
- Staging runtime からの finance ユーザー → 拒否（環境が違う）
- 本番 runtime からの engineering ユーザー → 拒否（部門が違う）

---

### クイックリファレンス: Cedar 演算子

| 演算子               | 意味                         | 例                                                   |
| -------------------- | ---------------------------- | ---------------------------------------------------- |
| `==`                 | 等しい                       | `principal.getTag("role") == "admin"`                |
| `!=`                 | 等しくない                   | `principal.getTag("department") != "restricted"`     |
| `&&`                 | AND（両方とも真）            | `condition_a && condition_b`                         |
| `\|\|`               | OR（いずれかが真）           | `value == "a" \|\| value == "b"`                     |
| `<`, `>`, `<=`, `>=` | 数値比較                     | `context.input.amount < 1000`                        |
| `in [...]`           | アクションが集合に含まれる   | `action in [Action::"a", Action::"b"]`               |
| `like`               | ワイルドカード文字列マッチ   | `principal.getTag("email") like "*@example.com"`     |
| `hasTag()`           | クレームがトークンに存在する | `principal.hasTag("department")`                     |
| `getTag()`           | クレーム値を取得             | `principal.getTag("department")`                     |
| `has`                | フィールド/属性が存在する    | `context.input has shippingAddress`                  |
| `.contains()`        | 集合のメンバーシップ         | `["US", "CA", "MX"].contains(context.input.country)` |

### Cedar でできないこと

| 制限事項                                                                | 回避策                                                                                                                                                                                            |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 正規表現                                                                | シンプルなパターンには `like` と `*` ワイルドカードを使う                                                                                                                                         |
| 算術演算（例: `a + b > c`）                                             | Pre-Token Lambda で事前計算してクレームとして注入                                                                                                                                                 |
| 外部データ参照（例: データベースクエリ）                                | Pre-Token Lambda で解決してクレームとして注入                                                                                                                                                     |
| 時刻ベースのルール（例: 「営業時間中のみ」）                            | Pre-Token Lambda から `time_window` クレームを注入                                                                                                                                                |
| 配列/リストメンバーシップ（例: 「ユーザーが allowed_list に含まれる」） | ハードコードされたリストには `.contains()` を使う: `["a", "b"].contains(context.input.x)`。動的なリスト（データベースから読み込み）の場合は Pre-Token Lambda で解決し、ブール値クレームとして注入 |
| リクエストヘッダ、IP アドレス、ネットワークコンテキスト                 | Cedar に公開されていない — 利用不可                                                                                                                                                               |

> **アーキテクチャパターン:** Cedar が直接評価できないもの（時刻、外部データ、
> 複雑なロジック）は Pre-Token Lambda で解決し、結果をカスタムクレームとして
> 注入してください。Cedar は事前解決済みの値をチェックします。これによりポリシーは
> シンプル、決定的、監査可能に保たれます。

## Design notes

本派生プロジェクト固有の設計判断:

- **Cedar v2 + guest 既定拒否**: 未認証呼び出しはポリシーセット側で弾き、ツール呼び出しには必ず認証済み principal を要求する。アプリ層のフィルタリング後置きには依存しない。
- **Cognito グループによるユーザー単位 ABAC**: 部門相当のグループメンバーシップを pre-token Lambda で Cedar 属性に投影し、ポリシー本体を書き換えずにユーザー単位のツール許可/拒否を実現する。解決は Cognito 側のサーバーサイドで行うため閉域構成でも動作する。
- **Cedar policy Lambda の権限**: Cedar policy を生成する Custom Resource は target カタログを列挙・検証するために `bedrock-agentcore:ListGatewayTargets` と `bedrock-agentcore:InvokeGateway` が必要。いずれも欠落するとデプロイ時にエラーになるため、回帰の切り分けが容易になっている。
