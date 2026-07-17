# AgentCore M2M 認証ワークフロー

**AgentCore Runtime <--> OAuth Provider <--> Cognito <--> AgentCore Gateway**

このドキュメントでは、AgentCore Runtime が OAuth2 Credential Provider（AgentCore Identity が管理）を使用して Cognito M2M トークンを取得し、AgentCore Gateway へのリクエストを認証するための完全なワークフローを説明します。内容は **Deployment**（インフラのセットアップ）と **Runtime**（実行時のトークンとリクエストフロー）の 2 フェーズに分かれています。

## 背景: 2 つの Secret

認証ワークフローでは 2 つの Secret が登場します。

**Secret 1:** `/<stack-name>/machine_client_secret`

- 作成元: CDK (`secretsmanager.Secret`)

**Secret 2:** `bedrock-agentcore-identity!default/oauth2/<stack-name>-runtime-gateway-auth`

- 作成元: デプロイ時の `oauth2ProviderLambda` Custom Resource

Secret 1 は `secretsmanager.Secret` で作成され、Cognito machine client が生成した secret から値が投入されます。Secret 2 は `oauth2ProviderLambda` がデプロイ時に `secretsmanager:CreateSecret` および `secretsmanager:PutSecretValue` を使って作成します。

**補足:** `bedrock-agentcore-identity!default/oauth2/<stack-name>-runtime-gateway-auth` という名前空間は、デフォルトの Token Vault における OAuth2 認証情報用の AgentCore Identity の規約であり、本スタックの実装に由来しています。

## 背景: 3 つの IAM Role

1. **AgentCoreRole**
   - 作成元: CDK construct `createAgentCoreRuntime()`
   - 引き受け元: AgentCore Runtime

2. **GatewayRole**
   - 作成元: `createAgentCoreGateway()`
   - 引き受け元: AgentCore Gateway サービス

3. **oauth2ProviderLambda Role**
   - 作成元: CDK（Lambda 関数用に自動生成）
   - 引き受け元: `oauth2ProviderLambda` 関数

`GatewayRole` は trust principal として `new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com")` を使用します。

## 重要: User Auth と M2M Auth は別物

このスタックには相互に独立した 2 つの認証フローが存在します。両者は同じ Cognito User Pool を使いますが、用途は異なります。

**Flow 1 -- Human User --> AgentCore Runtime（Runtime への inbound）:**

- 人間向けの Cognito app client (`userPoolClientId`) を使用
- User Pool の discovery URL を指す `RuntimeAuthorizerConfiguration.usingJWT(...)` で Runtime に設定
- Authorization Code grant を使用（フロントエンド経由の人間によるログイン）
- ユーザーの JWT トークン（`sub` クレームを含む）は Runtime に渡され、許可リスト化された `Authorization` ヘッダー (`requestHeaderConfiguration`) 経由でエージェントコードから利用可能になります

**Flow 2 -- AgentCore Runtime --> AgentCore Gateway（M2M、Runtime からの outbound）:**

- `machineClient` を使用 — `clientCredentials: true` および `generateSecret: true` を持つ別の Cognito app client
- Client Credentials grant を使用（人間ユーザーは関与しない）
- M2M トークンは AgentCore Identity の Token Vault 経由で Runtime が取得し、Gateway 呼び出しの認証に使用します
- Runtime はユーザーのアイデンティティ（および `cognito:groups`）を `aws_client_metadata` でこの M2M トークンに伝播し、Gateway の Cedar policy がユーザーごとに認可できるようにします — [Identity Propagation & Cedar Policy Guide](IDENTITY_POLICY.md) を参照してください

2 つのフローはネストではなく並列の関係です。

```
Human User
    --> Cognito (Authorization Code, userPoolClientId)
    --> User JWT token
    --> AgentCore Runtime (validates via userPoolClientId authorizer)
    --> Agent code runs...
        --> Needs to call Gateway
        --> AgentCore Identity Token Vault (Client Credentials, machineClient)
        --> Cognito issues M2M JWT (machineClient.userPoolClientId)
        --> AgentCore Gateway (validates via machineClient.userPoolClientId authorizer)
        --> Tool Lambda
```

ユーザーの Cognito Pool と machine client は CDK 上の利便性の都合から同じ Cognito User Pool を共有しており、認証目的は異なります。ユーザーの検証済みアイデンティティは M2M トークンの _取得方法_（常に Token Vault 経由の Client Credentials）は変えませんが、`aws_client_metadata` 経由でトークンのクレームに _載せられ_、Gateway がユーザーごとに認可できるようにします（[Identity Propagation](IDENTITY_POLICY.md) を参照）。

## PHASE 1: DEPLOYMENT WORKFLOW

このフェーズは `cdk deploy` 時に一度だけ実行されます。目的は、Runtime が実行時に利用できるように OAuth2 Credential Provider を AgentCore Identity に登録することです。

### Step D1 -- Cognito M2M インフラのセットアップ

CDK が M2M 認証に必要な Cognito リソースをプロビジョニングします。

**作成されるリソース:**

- `UserPoolResourceServer` -- 識別子 `<stack-name>-gateway` のもとで API スコープ（read、write）を定義
- `UserPoolClient`（Machine Client）-- `clientCredentials: true` および `generateSecret: true` を持つ confidential app client
- `secretsmanager.Secret`（Secret 1）-- machine client の `client_id` と `client_secret` を保存

**アクティブな IAM Role:** なし（CDK CloudFormation の execution role がプロビジョニングを担う）

**データフロー:**

```
CDK CloudFormation
    --> Creates Cognito User Pool Resource Server
        --> Identifier: <stack-name>-gateway
        --> Scopes: read, write
    --> Creates Cognito Machine Client
        --> Grant type: CLIENT_CREDENTIALS
        --> generateSecret: true --> Cognito generates client_id + client_secret
    --> Stores client_secret in Secrets Manager as Secret 1
        --> Path: /<stack-name>/machine_client_secret
```

**理由:** Machine Client は Cognito から M2M トークンを要求する際に使用される OAuth2 アイデンティティです。Resource Server はそれらのトークンが有効となるスコープを定義します。Secret 1 は次のステップで使用される認証情報を保管する、CDK 管理下の安全なストアです。

### Step D2 -- Cognito JWT Authorizer 付きの AgentCore Gateway デプロイ

CDK が `CUSTOM_JWT` authorizer を Cognito User Pool に向けて構成した状態で AgentCore Gateway (`CfnGateway`) をデプロイします。

**主要な構成:**

```
authorizerType: "CUSTOM_JWT"
authorizerConfiguration:
  customJwtAuthorizer:
    discoveryUrl: https://cognito-idp.<region>.amazonaws.com/<userPoolId>/.well-known/openid-configuration
    allowedClients: [ machineClient.userPoolClientId ]
```

**アクティブな IAM Role:** GatewayRole

**GatewayRole の権限:**

- `lambda:InvokeFunction` on `toolLambda` -- MCP tool Lambda ターゲットを呼び出す
- `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`
- `ssm:GetParameter`, `ssm:GetParameters`
- `cognito-idp:DescribeUserPoolClient` on User Pool ARN -- JWT 検証のため Cognito app client 設定を内省する
- `cognito-idp:InitiateAuth` on User Pool ARN -- トークン関連の操作のために Cognito 認証フローを開始する
- `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents`

**データフロー:**

```
CDK CloudFormation
    --> Creates CfnGateway
        --> authorizerType: CUSTOM_JWT
        --> discoveryUrl: Cognito OIDC discovery URL (for JWKS fetching at runtime)
        --> allowedClients: [ machineClient.userPoolClientId ]
    --> Creates CfnGatewayTarget
        --> Protocol: MCP
        --> Target: toolLambda ARN
        --> Credential provider: GATEWAY_IAM_ROLE
    --> Stores Gateway URL in SSM: /<stack-name>/gateway_url
```

**理由:** `discoveryUrl` はトークン検証で信頼する Cognito User Pool を指定し、実行時に JWT 署名検証用の JWKS（公開鍵）を取得するために使用されます。`allowedClients` 制限により、machine client に発行されたトークンのみが受け入れられるようになります。

### Step D3 -- OAuth2 Credential Provider の登録（Custom Resource）

`oauth2ProviderLambda`（CDK Custom Resource により駆動）が実行され、AgentCore Identity の Token Vault に OAuth2 Credential Provider を登録します。

**アクティブな IAM Role:** oauth2ProviderLambda execution role（CDK により自動作成）

**oauth2ProviderLambda Role の権限:**

- `secretsmanager:GetSecretValue` on Secret 1 (`/<stack-name>/machine_client_secret`) -- CDK 管理ストレージから Cognito machine client の `client_id` と `client_secret` を読み取る。これらの認証情報は信頼の起点であり、AgentCore Identity に登録するためにはまず読み出す必要があります。
- `secretsmanager:CreateSecret` on Secret 2 (`bedrock-agentcore-identity!default/oauth2/*`) -- Secrets Manager 内の AgentCore Identity 名前空間に新しい secret を作成する。この名前空間は Token Vault が実行時に認証情報を見つけることを想定する場所です。
- `secretsmanager:PutSecretValue` on Secret 2 -- 新しく作成された AgentCore Identity 管理の secret に `client_id` と `client_secret` の値を書き込み、M2M トークン取得時に Token Vault が読み取れるようにする。
- `secretsmanager:DescribeSecret` on Secret 2 -- 作成を試みる前に Secret 2 が既に存在するかを確認する。これによりべき等性が確保され、スタックを再デプロイしても既存の secret を再作成しようとして失敗することはなくなります。
- `secretsmanager:DeleteSecret` on Secret 2 -- CDK スタックが破棄されるときに Secret 2 を削除する。これがないと、スタック削除後も AgentCore Identity 管理の secret が Secrets Manager に取り残されてしまいます。
- `bedrock-agentcore:CreateOauth2CredentialProvider` on `token-vault/default` and `token-vault/default/oauth2credentialprovider/*` -- Token Vault に OAuth2 Credential Provider を登録し、provider 名 (`<stack-name>-runtime-gateway-auth`) を Cognito の discovery URL、`client_id`、Secret 2 に紐付ける。これは Provider を実行時に Runtime から利用可能にするコアな登録ステップです。
- `bedrock-agentcore:GetOauth2CredentialProvider` on `token-vault/default` and `token-vault/default/oauth2credentialprovider/*` -- 登録後に Provider が正常に作成されたことを確認する。再デプロイ時にも Provider が既に存在するかを作成前に確認する用途で使用されます（べき等性）。
- `bedrock-agentcore:DeleteOauth2CredentialProvider` on `token-vault/default` and `token-vault/default/oauth2credentialprovider/*` -- CDK スタックが破棄されるときに Token Vault から Provider 登録を削除する。これがないと、スタック削除後も Provider のエントリが Token Vault に残り続けてしまいます。
- `bedrock-agentcore:CreateTokenVault` on `token-vault/default` and `token-vault/default/*` -- Provider 登録を試みる前に default Token Vault が存在することを保証する。Token Vault がまだ作成されていない場合、この権限により Lambda が前提条件として作成できます。
- `bedrock-agentcore:GetTokenVault` on `token-vault/default` and `token-vault/default/*` -- 操作の前に default Token Vault のステータスを確認する。Provider 登録の前に Vault が利用可能かつ準備完了であることを確認するために使用されます。
- `bedrock-agentcore:DeleteTokenVault` on `token-vault/default` and `token-vault/default/*` -- 必要に応じてスタック破棄時に Token Vault をクリーンアップする。完全な解体シナリオに備えた防御的なクリーンアップ権限です。

**データフロー:**

```
oauth2ProviderLambda
    |
    |-- 1. Reads Secret 1
    |       --> Gets: client_id, client_secret
    |
    |-- 2. Creates Secret 2 in Secrets Manager
    |       --> Namespace: bedrock-agentcore-identity!default/oauth2/<stack-name>-runtime-gateway-auth
    |       --> Stores: { client_id, client_secret }
    |
    |-- 3. Calls bedrock-agentcore:CreateOauth2CredentialProvider
    |       --> Provider name: <stack-name>-runtime-gateway-auth
    |       --> discoveryUrl: Cognito OIDC discovery URL
    |       --> clientId: machineClient.userPoolClientId
    |       --> secretArn: Secret 2 ARN
    |       --> grantType: CLIENT_CREDENTIALS (M2M / 2LO)
    |
    └-- 4. Provider is now registered in Token Vault (default)
            --> Provider ARN: arn:aws:bedrock-agentcore:<region>:<account>:token-vault/default/oauth2credentialprovider/<stack-name>-runtime-gateway-auth
```

**理由:** この登録ステップにより、論理的な provider 名 (`GATEWAY_CREDENTIAL_PROVIDER_NAME` 環境変数) と実際の Cognito OAuth2 構成が結び付けられ、Runtime が AgentCore Identity を介して M2M トークンを取得できるようになります。

### Step D4 -- AgentCore Runtime のデプロイ

CDK が AgentCoreRole と環境変数（`GATEWAY_CREDENTIAL_PROVIDER_NAME` を含む）を伴って AgentCore Runtime をデプロイします。

**アクティブな IAM Role:** AgentCoreRole

**AgentCoreRole の権限（M2M 関連）:**

- `bedrock-agentcore:GetOauth2CredentialProvider` on `oauth2-credential-provider/*` -- 論理名（`GATEWAY_CREDENTIAL_PROVIDER_NAME` の値）から登録済みの OAuth2 Credential Provider のメタデータを参照する。これは実行時に `@requires_access_token` デコレーター内部で呼ばれ、Provider 名を Cognito トークン URL、`client_id`、および Secret 2 への参照に解決します。この権限がないとデコレーターはトークン取得プロセスを開始できません。
- `bedrock-agentcore:GetResourceOauth2Token` on `token-vault/*` -- Token Vault から M2M アクセストークンを要求する。これは実行時に Cognito JWT を取得するための主要な権限です。Token Vault はキャッシュされた有効なトークンを返すか、Client Credentials grant を使って Cognito から新しいトークンを取得します。
- `bedrock-agentcore:GetResourceOauth2Token` on `workload-identity-directory/*` -- 呼び出し元の IAM アイデンティティ (AgentCoreRole) を AgentCore Identity ディレクトリに登録された Workload Identity に解決する。Token Vault はこの Workload Identity を使ってキャッシュされたトークンを当該エージェント固有にスコープし、同じ IAM ロールを共有していてもエージェント間でトークンが分離されるようにします。
- `secretsmanager:GetSecretValue` on Secret 2 (`bedrock-agentcore-identity!default/oauth2/<stack-name>-runtime-gateway-auth`) -- IAM 委譲のために必要: Token Vault が Cognito から新しいトークンを取得する必要があるとき（cache miss）、AgentCore Identity は自身のサービスロールではなく Runtime の IAM ロールで Secret 2 を読み取ります。つまり Runtime は Secret 2 への `GetSecretValue` を持つ必要がありますが、これは Runtime が直接読み取るからではなく、AgentCore Identity が Runtime のロールを代行して認証情報にアクセスするためです。この設計は権限昇格を防ぎます: 呼び出し元は AgentCore Identity を踏み台として、本来直接アクセス権を持たない secret に到達することはできません。
- `secretsmanager:GetSecretValue` on Secret 1 (`/<stack-name>/machine_client_secret`) -- 防御的／テスト用途のみ。標準的な Token Vault フローでは Secret 2 へのアクセスのみで十分です（IAM 委譲経由）。Secret 1 へのアクセスは、Runtime またはその内部で動作するテストスクリプトが Token Vault フロー外で直接 Cognito トークンエンドポイントを呼び出す必要があるときのために用意されています。標準の M2M パスでは不要です。
- `ssm:GetParameter`, `ssm:GetParameters` on `/<stack-name>/*` -- 実行時に Gateway URL (`/<stack-name>/gateway_url`) やその他の構成値を SSM Parameter Store から読み取る。Gateway URL は `create_gateway_mcp_client()` が呼ばれた際に一度だけ取得され、以降の MCP 接続すべてで使用されます。

**データフロー:**

```
CDK CloudFormation
    --> Creates AgentCore Runtime
        --> Assigns AgentCoreRole as execution role
        --> Sets environment variables:
            GATEWAY_CREDENTIAL_PROVIDER_NAME = <stack-name>-runtime-gateway-auth
            STACK_NAME = <stack-name>
            MEMORY_ID = <memoryId>
        --> Configures inbound JWT authorizer (for human users calling the Runtime):
            discoveryUrl: Cognito User Pool discovery URL
            allowedAudiences: [ userPoolClientId ]  <-- human-facing app client, NOT machine client
        --> Configures requestHeaderConfiguration:
            allowlistedHeaders: [ "Authorization" ]  <-- so agent code can read the user's JWT
```

**`GATEWAY_CREDENTIAL_PROVIDER_NAME` 環境変数を使う理由:** エージェントコードを特定の Provider ARN から切り離すためです。エージェントコードは論理名さえ知っていればよく、Provider 構成の解決は AgentCore Identity が行います。これにより、エージェントコードは環境やスタックを跨いでポータブルになります。

## PHASE 2: RUNTIME WORKFLOW

このフェーズは、エージェントコードが AgentCore Gateway を呼び出す必要があるたびに実行されます。目的は、有効な Cognito M2M トークンを取得し、それを使って Gateway リクエストを認証することです。

### Step R1 -- MCP Client の作成とトークン取得のセットアップ

`create_gateway_mcp_client()` が呼び出され、Gateway 通信用の MCP クライアントをセットアップします。Gateway URL はこの時点で SSM から一度だけ読み取られます（安定しており変化しないため）。トークン取得は lambda factory 内に遅延され、MCP の接続および再接続のたびに最新のものが取得されます。

**アクティブな IAM Role:** AgentCoreRole

**使用される権限:** `ssm:GetParameter` on `/<stack-name>/*`（Gateway URL の参照のため）

**データフロー:**

```
create_gateway_mcp_client() is called
    |
    |-- 1. Reads STACK_NAME from env var
    |
    |-- 2. Reads Gateway URL from SSM:
    |       ssm:GetParameter --> /<stack-name>/gateway_url
    |       <-- Returns: Gateway URL (stable, fetched once)
    |
    └-- 3. Creates MCPClient with lambda factory:
            MCPClient(
                lambda: streamablehttp_client(
                    url=gateway_url,
                    headers={"Authorization": f"Bearer {_fetch_gateway_token()}"}
                ),
                prefix="gateway",
            )
            --> Token is NOT fetched here -- deferred into the lambda
            --> _fetch_gateway_token() will be called on every MCP connection/reconnection
```

**lambda factory パターンの理由:** 「クロージャの罠」を避けるためです。`_fetch_gateway_token()` を lambda の外で呼び出すと、Python のクロージャはクライアント作成時のトークン値をキャプチャしてしまいます。そのトークンは 60 分間有効です。MCP クライアントが期限切れ後に再接続すると、キャプチャされた古いトークンを使用して Gateway から 401 を受け取ることになります。lambda 内部で `_fetch_gateway_token()` を呼ぶことで、すべての MCP 接続時に最新のトークンが取得されます。Token Vault のキャッシュ層（`@requires_access_token` デコレーター内）により、これは効率的です -- トークンがまだ有効ならキャッシュされたものが Cognito を呼ばずに即座に返されます。

**SSM 読み取りが lambda の外にある理由:** Gateway URL は接続ごとに変化しない安定したインフラエンドポイントです。再接続のたびではなく、クライアント作成時に一度読み取るのが安全かつ効率的です。

### Step R2 -- @requires_access_token デコレーターによるトークン取得

MCP の接続または再接続のたびに lambda factory が実行され、`_fetch_gateway_token()` が呼び出されます。`@requires_access_token` デコレーター（AgentCore Identity Python SDK の一部）がこの呼び出しを横取りし、すべての OAuth メカニクスを内部で処理します。

**アクティブな IAM Role:** AgentCoreRole

**データフロー:**

```
Lambda factory executes (on each MCP connection/reconnection)
    --> _fetch_gateway_token() is called
    --> @requires_access_token decorator intercepts:
        --> provider_name = GATEWAY_CREDENTIAL_PROVIDER_NAME env var
        --> auth_flow = "M2M"  (Client Credentials grant)
        --> scopes = []  (Cognito embeds scopes based on machine client authorization)
        --> custom_parameters = {"aws_client_metadata": json({verified_user_id, verified_groups})}
                                 (Pre-Token Lambda 向けのユーザーアイデンティティ -- Identity Propagation guide 参照)
    --> Decorator calls AgentCore Identity API internally (see R3 for details)
    --> Decorator injects the obtained JWT as access_token argument
    --> _fetch_gateway_token(access_token=<jwt>) returns the JWT string
    --> MCPClient uses JWT in Authorization: Bearer header
```

**auth_flow="M2M" と scopes=[] の理由:** `auth_flow="M2M"` はデコレーターに対し、Client Credentials grant（ユーザーの介在なし）を使うよう指示します。`scopes=[]` は M2M では正しい設定です -- スコープ (`<stack-name>-gateway/read`、`<stack-name>-gateway/write`) は、machine client が認可されている内容に基づき Cognito によってトークンに埋め込まれるため、呼び出し側で指定する必要はありません。

**デコレーター内部に関する補足:** `@requires_access_token` デコレーターは AgentCore Identity Python SDK の一部です。その内部のトークン取得は、後述の R3 で説明する 2 段階のサブステップで構成されます。これはエージェントコードからは見えません -- エージェント側から見ると、デコレーターはトークンを返す単一の呼び出しに過ぎません。

### Step R3 -- デコレーター内部: Token Vault からのトークン取得

`@requires_access_token` デコレーターの内部で、AgentCore Identity は 2 段階のサブステップでトークンを取得します。AgentCore Identity は Token Vault にキャッシュされた有効なトークンがあるかを確認し、なければ、または期限切れの場合は Cognito から新しいトークンを取得します。

**アクティブな IAM Role:** AgentCoreRole

**使用される権限:**

- `bedrock-agentcore:GetOauth2CredentialProvider` on `oauth2-credential-provider/*` -- Provider メタデータの参照（サブステップ 3a）
- `bedrock-agentcore:GetResourceOauth2Token` on `token-vault/*` -- Token Vault からのトークン取得（サブステップ 3b）
- `bedrock-agentcore:GetResourceOauth2Token` on `workload-identity-directory/*` -- トークンスコープのための Workload Identity 解決（サブステップ 3b）
- `secretsmanager:GetSecretValue` on Secret 2 -- client_secret アクセスのための IAM 委譲（cache miss 時のみ、サブステップ 3b）

**データフロー -- サブステップ 3a: Provider メタデータの参照:**

```
@requires_access_token decorator (SDK internals)
    --> bedrock-agentcore:GetOauth2CredentialProvider
        --> Input: provider_name = <stack-name>-runtime-gateway-auth
        --> AgentCore Identity looks up the provider registered in Token Vault (Step D3)
    <-- Returns:
        --> Provider ARN
        --> Cognito token URL (derived from discoveryUrl registered in D3)
        --> client_id (machineClient.userPoolClientId)
        --> Reference to Secret 2 (where client_secret lives)
        --> Grant type: CLIENT_CREDENTIALS
```

**データフロー -- サブステップ 3b: Token Vault チェックとトークン発行:**

**Cache Hit（トークンがまだ有効）:**

```
    --> bedrock-agentcore:GetResourceOauth2Token
        --> Resolves Workload Identity from AgentCoreRole ARN via workload-identity-directory
        --> Checks Token Vault: token for this Workload Identity + provider = VALID (< 60 min old)
    <-- Returns: cached JWT access token (no Cognito call needed)
```

**Cache Miss（トークン未取得または期限切れ）:**

```
    --> bedrock-agentcore:GetResourceOauth2Token
        --> Resolves Workload Identity from AgentCoreRole ARN via workload-identity-directory
        --> Checks Token Vault: no valid token found
        --> Reads Secret 2 using AgentCoreRole (IAM delegation)
            --> Gets: client_secret
        --> Calls Cognito token endpoint:
            POST https://<cognito-domain>.auth.<region>.amazoncognito.com/oauth2/token
            grant_type=client_credentials
            client_id=<machineClient.userPoolClientId>
            client_secret=<from Secret 2>
            scope=<stack-name>-gateway/read <stack-name>-gateway/write
            aws_client_metadata=<JSON {verified_user_id, verified_groups}>
            --> Cognito が V3 Pre-Token Lambda を呼び出し、グループを読み取って
                department/role クレームを注入（Identity Propagation guide 参照）
        <-- Cognito returns: JWT access token (valid 60 minutes by default)
        --> Stores token in Token Vault, scoped to this Workload Identity
    <-- Returns: new JWT access token
```

**Workload Identity が重要な理由:** Token Vault はトークンを IAM ロール単位ではなく Workload Identity 単位で保存します。つまり、2 つの異なるエージェントランタイムが同じ IAM ロールを共有していたとしても、Vault 内では別々のトークンエントリを持ちます。Workload Identity は、エージェント間でのトークン分離を保証する、エージェントレベルのきめ細かなプリンシパルです。

**Secret 2 で IAM 委譲を行う理由:** AgentCore Identity は自身のサービスロールではなく、呼び出し元の IAM ロール (AgentCoreRole) を使って Secret 2 を読み取ります。これは意図的なセキュリティ設計です: secret にアクセスできるのは、呼び出し元が `GetResourceOauth2Token` API 権限と Secret 2 への `secretsmanager:GetSecretValue` 権限の両方を持つ場合に限ります。これにより権限昇格を防ぎます -- 呼び出し元は AgentCore Identity を踏み台にして、自身が直接読む権限を持たない secret にアクセスすることはできません。

**Token Vault のキャッシュにより lambda factory が効率的になる理由:** `_fetch_gateway_token()` は MCP 接続のたびに呼ばれますが、Token Vault のキャッシュにより、Cognito が呼ばれるのはトークン期限切れ時（およそ 60 分ごと）だけです。それ以外の呼び出しはすべてキャッシュされたトークンを即座に返すため、このパターンは「常に最新」と「効率」の両方を実現します。

### Step R4 -- エージェントコードが Gateway へリクエストを送信

MCPClient は `_fetch_gateway_token()` から返された JWT を使い、AgentCore Gateway に対して認証付きの HTTP リクエストを送信します。

**アクティブな IAM Role:** AgentCoreRole

**データフロー:**

```
MCPClient lambda factory
    --> streamablehttp_client(
          url=gateway_url,  <-- read from SSM in Step R1, stable
          headers={"Authorization": f"Bearer {jwt}"}  <-- fresh token from Step R2/R3
      )
    --> Sends HTTP request:
        POST <gateway_url>/mcp
        Authorization: Bearer <cognito-jwt-token>
        Content-Type: application/json
        { ... MCP tool invocation payload ... }
```

**理由:** Gateway URL は安定しており（R1 で一度だけ読み取り）、トークンは最新（R2/R3 で接続ごとに取得）です。`Authorization: Bearer` ヘッダーは、保護されたリソースへトークンを提示する OAuth2 の標準的なメカニズムです。

### Step R5 -- Gateway が JWT トークンを検証

AgentCore Gateway がリクエストを受信し、`CUSTOM_JWT` authorizer が Bearer トークンを検証します。

**アクティブな IAM Role:** GatewayRole

**データフロー:**

```
AgentCore Gateway (CUSTOM_JWT authorizer)
    |
    |-- 1. Extracts Bearer token from Authorization header
    |
    |-- 2. Fetches JWKS from Cognito discovery URL:
    |       GET https://cognito-idp.<region>.amazonaws.com/<userPoolId>/.well-known/openid-configuration
    |       --> Gets JWKS URI
    |       GET <jwks_uri>
    |       --> Gets Cognito public keys for signature verification
    |
    |-- 3. Verifies JWT signature using JWKS public keys
    |
    |-- 4. Checks token claims:
    |       --> client_id claim ∈ allowedClients (machineClient.userPoolClientId)
    |       --> token not expired
    |       --> issuer matches Cognito User Pool
    |
    └-- 5. Authorization decision:
            --> VALID --> forwards request to Gateway Target (Step R6)
            --> INVALID --> returns 401 Unauthorized
```

**allowedClients によるスコープ制限の理由:** AgentCore Gateway は、ユーザーが作成した特定の machine client に発行されたトークンのみを受け入れます。同じ User Pool に存在する別の Cognito クライアントがトークンを取得したとしても、それは拒否されます。これは意図的な厳格なスコープ設定であり、この AgentCore Gateway を呼び出せるのは AgentCore Runtime の machine client だけになります。

### Step R6 -- Gateway が MCP Tool Lambda へ転送

検証済みのリクエストは Gateway Target（MCP tool Lambda）に転送されます。

**アクティブな IAM Role:** GatewayRole

**使用される権限:** `lambda:InvokeFunction` on `toolLambda`

**データフロー:**

```
AgentCore Gateway (GatewayRole)
    --> lambda:InvokeFunction on toolLambda
        --> MCP tool invocation payload
    <-- Lambda returns tool result
    <-- AgentCore Gateway returns MCP response to AgentCore Runtime
    <-- MCPClient delivers result to agent code
    <-- Agent code continues execution with tool result
```

---

## APPENDIX: M2M 認証の Cognito を別の IdP に置き換える

このセクションでは、Runtime --> Gateway 間の M2M 認証フローにおいて、Cognito を別の identity provider (IdP) に置き換える方法を説明します。Client --> Runtime 間のユーザー認証フローは独立しており、変更されません（上述の「User Auth と M2M Auth は別物」を参照）。

### この変更の範囲

M2M フローは OAuth2 Client Credentials grant を使用します: Runtime は IdP に登録された `client_id` と `client_secret` で machine トークンを取得し、Gateway は IdP の JWKS を使ってそのトークンを検証します。ユーザーアイデンティティもユーザートークンもこのフローには関与しません — Runtime はユーザーの代理ではなく、自分自身として認証します。

**将来のユーザー委譲フローに関する補足:** このドキュメントは現行の Phase 1 実装（Runtime から Gateway へのフローが純粋な M2M、Client Credentials grant）を扱います。将来の Phase では、Runtime が認証済みユーザーの代理でトークンを取得し、Gateway がユーザーアイデンティティを含むトークンを受け取るユーザー委譲フロー（3-legged OAuth / Authorization Code grant）が導入される可能性があります。AgentCore Identity は両方のフローをサポートします。その Phase が実装される際には、Gateway authorizer の構成と `@requires_access_token` デコレーターの `auth_flow` パラメータを更新する必要があります — このセクションの IdP 置き換えガイドはどちらのフローにも適用されます。

このフローで Cognito を置き換えるには、2 つの信頼関係を更新する必要があります。

1. **AgentCore Gateway の JWT authorizer** — 新しい IdP の discovery URL を指し、新しいクライアントの `client_id` を受け入れる必要があります
2. **AgentCore Identity の OAuth2 Credential Provider** — 新しい IdP の discovery URL、`client_id`、`client_secret` を構成する必要があります

Token Vault、`@requires_access_token` デコレーター、Workload Identity、AgentCore Runtime コードは IdP 非依存であり、変更は必要ありません。

### サポートされる IdP

AgentCore Identity は OAuth2 Provider について 2 つのモードをサポートします。

1. **組み込みのマネージド Provider**（AWS が事前構成・保守）: Amazon Cognito, Auth0 by Okta, Atlassian, CyberArk, Dropbox, Facebook, FusionAuth, GitHub, Google, HubSpot, LinkedIn, Microsoft, Notion, Okta, OneLogin, Ping Identity, Reddit, Salesforce, Slack, Spotify, Twitch, X, Yandex, Zoom

2. **CustomOauth2**（このスタックで使用）: `.well-known/openid-configuration` の discovery エンドポイントを公開している任意の OIDC 準拠 IdP。これは汎用的なパスであり、上記すべての IdP に対して機能します。

**Cognito が組み込みベンダーであるにもかかわらず、このスタックが CustomOauth2 を使う理由:** AgentCore Identity の組み込みベンダー `AmazonCognito` は、エージェントが人間ユーザーの代理として動作するユーザー委譲フロー（3-legged OAuth / Authorization Code grant）向けに設計されています。ここで使用する M2M Client Credentials grant ではユーザーは関与しません — Runtime は自分自身として認証します。`discoveryUrl` 付きの `CustomOauth2` は、どの IdP がバックエンドにあっても Client Credentials / M2M フローにとっては正しい選択肢であり、Cognito を標準的な OIDC Provider として扱います。この選択により、スタックは設計上 IdP 移植可能になります: 別の IdP に切り替える際には、`discoveryUrl` とクライアント認証情報の値を変更するだけでよく、Custom Lambda コードを変更する必要はありません。

M2M（Client Credentials grant）の場合、置き換える IdP は次をサポートする必要があります。

- OAuth 2.0 Client Credentials grant (`grant_type=client_credentials`)
- JWKS ベースの JWT 検証のための OIDC Discovery (`.well-known/openid-configuration`)
- クライアントアプリケーションでの設定可能なスコープ
- client secret を持つ confidential client

ほとんどのエンタープライズ IdP はこれらをネイティブにサポートしています。例として Microsoft Entra ID、Okta、Auth0 などがあります — 進める前に、IdP のドキュメントを参照して Client Credentials grant と OIDC Discovery のサポートを確認してください。

### 必要な CDK スタックの修正

#### 1. createMachineAuthentication() — 全面置き換え

このメソッドは Cognito 固有です。`UserPoolResourceServer`、`UserPoolClient`（machine client）、`MachineClientSecret` を作成します。これらのリソースは OAuth2 machine client のアイデンティティを確立し、要求できるスコープを定義します。別の IdP の場合は、その IdP 自身のコンソールや管理ツールで同等のセットアップを行います。

**このステップが後続のステップに対して提供すべき内容:**

- **`client_id`**: machine client の OAuth2 クライアント識別子
  - 利用先: Gateway authorizer (`allowedClients`)、Custom Resource (`properties.ClientId`)
- **`client_secret`**: machine client の OAuth2 クライアントシークレット
  - 利用先: Secret 1 (`/<stack-name>/machine_client_secret`) として Secrets Manager に保存
- **Discovery URL**: IdP の OIDC discovery エンドポイント
  - 利用先: Gateway authorizer (`discoveryUrl`)、Custom Resource (`properties.DiscoveryUrl`)

オリジナルと同じ `secretsmanager.Secret` パターンを使い、`/<stack-name>/machine_client_secret` に `client_id` と `client_secret` を保存します。Discovery URL は静的な文字列です — 以降のステップで使えるよう、変数または SSM パラメータとして保管してください。

IdP 固有のセットアップ（confidential client の作成、Client Credentials grant の有効化、スコープの定義）については、IdP のドキュメントを参照してください。

#### 2. createAgentCoreGateway() — Discovery URL と allowedClients の値

**変更 A — Discovery URL 変数:**

```typescript
// Current (Cognito):
const cognitoIssuer = `https://cognito-idp.${this.region}.amazonaws.com/${this.userPool.userPoolId}`
const cognitoDiscoveryUrl = `${cognitoIssuer}/.well-known/openid-configuration`

// Any OIDC-compliant IdP:
const discoveryUrl = `<idp-issuer-url>/.well-known/openid-configuration`
```

**変更 B — CfnGateway authorizer 構成:**

```typescript
authorizerConfiguration: {
  customJwtAuthorizer: {
    allowedClients: [<new-idp-client-id>],   // replace machineClient.userPoolClientId
    discoveryUrl: discoveryUrl,               // replace cognitoDiscoveryUrl
  },
},
```

**変更 C — GatewayRole から Cognito 固有の IAM 権限を削除:**

```typescript
// Remove these — they are Cognito-specific and not needed for other IdPs:
// cognito-idp:DescribeUserPoolClient
// cognito-idp:InitiateAuth
```

**理由**: Gateway は実行時に `discoveryUrl` を使って IdP の JWKS を取得し、JWT 署名の検証に使用します。`allowedClients` の制限により、特定の machine client に発行されたトークンだけが受け入れられるようになります。Cognito の IAM 権限は Cognito 固有の API 呼び出しにのみ必要なものであり、他の IdP は AWS IAM 権限を必要としない標準的な OIDC/JWKS エンドポイントを使用します。

#### 3. Custom Resource プロパティ — 値のみ変更、Lambda コードは不要

`oauth2ProviderLambda` のコードは既に IdP 非依存です。汎用的な `discoveryUrl` を伴う `credentialProviderVendor="CustomOauth2"` を使用しているため、コード変更は不要です。Custom Resource に渡されるプロパティのみ変更します。

```typescript
const runtimeCredentialProvider = new cdk.CustomResource(this, "RuntimeCredentialProvider", {
  serviceToken: oauth2Provider.serviceToken,
  properties: {
    ProviderName: providerName,
    ClientSecretArn: this.machineClientSecret.secretArn,  // same pattern, new IdP secret
    DiscoveryUrl: discoveryUrl,                           // new IdP discovery URL
    ClientId: <new-idp-client-id>,                        // new IdP client_id
  },
})
```

**理由**: Lambda は渡された `DiscoveryUrl` と `ClientId` をそのまま読み取り、`CustomOauth2` プロバイダーとして AgentCore Identity に登録します。Token Vault は実行時にこれらの値を使って正しい IdP のトークンエンドポイントを呼び出します。Lambda の `handle_create`、`handle_update`、`handle_delete` ハンドラーには変更は一切不要です。

**組み込みベンダーに関する補足**: `CustomOauth2` の代わりに組み込みベンダー（上述「サポートされる IdP」セクションを参照）を使いたい場合は、Lambda の `handle_create` と `handle_update` で `credentialProviderVendor` を変更します。組み込みベンダーは AWS が管理するエンドポイント構成を持ち、プロバイダー固有のクセを自動的に処理する場合があります。ただし、`discoveryUrl` 付きの `CustomOauth2` はすべての OIDC 準拠 IdP に対して機能し、環境を跨いで保守しやすいです。

#### 4. createAgentCoreRuntime() — 変更不要

Runtime は `GATEWAY_CREDENTIAL_PROVIDER_NAME` 環境変数のみを把握しています。どの IdP がプロバイダーをバックアップしているかは関知しません。Token Vault が、Step D3 で登録された構成を使って IdP との対話を透過的に解決します。

#### 5. createCognitoSSMParameters() — Cognito 固有のパラメータ名を変更（任意）

SSM パラメータ `cognito-user-pool-id`、`cognito-user-pool-client-id`、`cognito_provider` は名前的に Cognito 固有です。別の IdP の場合は、これらを IdP 非依存の名称（例: `idp-discovery-url`、`machine-client-id`）にリネームします。`gateway_url` と `machine_client_id` のパラメータは既に IdP 非依存です。

---

### サマリー: 何が変わり、何が変わらないか

**変更が必要なコンポーネント:**

- **createMachineAuthentication()** — 全面置き換えが必要
  - Cognito 固有のリソースを同等の IdP クライアントセットアップに置き換える
  - 提供すべきもの: `client_id`、`client_secret`、Discovery URL
- **CfnGateway authorizerConfiguration** — 値の更新
  - `discoveryUrl` を新しい IdP の OIDC discovery エンドポイントに変更
  - `allowedClients` を新しい IdP の `client_id` に変更
- **GatewayRole IAM 権限** — Cognito 固有の権限を削除
  - `cognito-idp:DescribeUserPoolClient` を削除
  - `cognito-idp:InitiateAuth` を削除
- **Custom Resource プロパティ** — 値のみ更新
  - `DiscoveryUrl` を新しい IdP の discovery エンドポイントに変更
  - `ClientId` を新しい IdP の `client_id` に変更
  - `ClientSecretArn` を Secrets Manager の新しい IdP secret に向ける
- **SSM パラメータ名** — 任意のリネーム（見た目のみ）
  - 必要に応じて Cognito 固有のパラメータ名を IdP 非依存の名称にリネーム

**変更が不要なコンポーネント:**

- **oauth2ProviderLambda コード** — 既に IdP 非依存
  - 汎用的な `CustomOauth2` ベンダーを `discoveryUrl` パラメータと共に使用
  - `handle_create`、`handle_update`、`handle_delete` のコード変更は不要
  - **任意**: Lambda コードで `credentialProviderVendor` を変更することで組み込みベンダーに切り替え可能。ただし `CustomOauth2` はすべての OIDC 準拠 IdP に対して機能する
- **createAgentCoreRuntime()** — 既に IdP 非依存
  - `GATEWAY_CREDENTIAL_PROVIDER_NAME` 環境変数のみを把握
  - Token Vault が IdP との対話を透過的に解決
- **エージェントコード** — 既に IdP 非依存
  - IdP 非依存の `@requires_access_token` デコレーターを使用
  - 採用しているエージェントパターンに関わらずエージェントコードに変更は不要
- **Client → Runtime ユーザー認証** — 独立したフロー
  - Runtime → Gateway の M2M 認証とは別の関心事
  - M2M IdP の変更による影響を受けない
