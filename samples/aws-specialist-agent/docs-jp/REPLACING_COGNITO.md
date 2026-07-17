# Cognito の置き換え: Identity Provider の交換と Gateway Interceptors ガイド

このドキュメントでは、FAST AgentCore アーキテクチャにおいて Amazon Cognito を別の Identity Provider (IdP) に置き換える方法と、アクセス制御において Cedar Policy の代替もしくは補完として Gateway Interceptors を活用する方法を説明します。

---

## 1. 概要

### 現在のアーキテクチャの概要

FAST デモでは、Amazon Cognito を Identity Provider として使用しており、次のフローで動作します。

```
User JWT → Runtime (validates user) → Runtime gets M2M token from Cognito → Pre-Token Lambda injects user claims into M2M token → Gateway authorizer validates M2M token → Cedar Policy Engine evaluates user claims → allows/denies tool access → Target Lambda receives tool input only
```

**このドキュメントは、次の 2 つの問いに答えます。** Gateway がアクセス制御の実施のためにユーザーをどのように識別するか、そして異なる IdP でこれをどのように実現するか、です。

### 2 つのアプローチ

| アプローチ                                              | 利用するタイミング                                                                                         |
| ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| **アプローチ A: IdP を交換し、Cedar Policy を維持する** | 新しい IdP がトークンの拡張 (発行時に M2M トークンへカスタムクレームを注入する機能) をサポートしている場合 |
| **アプローチ B: Gateway Interceptors**                  | 新しい IdP がトークンの拡張をサポートしていない場合、または完全に動的なアクセス制御が必要な場合            |

---

## 2. アプローチ A — IdP を交換し、Cedar Policy を維持する

**対象:** トークンの拡張をサポートする IdP (Okta (Token Inline Hooks)、Auth0 (Actions)、Entra ID (Claims Mapping Policies) など)。

### 2a. 仕組み

アーキテクチャは現在の Cognito フローと同じままです。Cognito 固有のコンポーネントは新しい IdP の同等機能に置き換えられます。

```
User JWT → Runtime (validates user via new IdP's OIDC) → Runtime gets M2M token from new IdP's token endpoint → IdP's token enrichment hook injects user claims (replaces Pre-Token Lambda) → Gateway authorizer validates M2M token (via new IdP's OIDC discovery URL) → Cedar Policy Engine evaluates user claims → allows/denies (UNCHANGED) → Target Lambda receives tool input only (UNCHANGED)
```

**重要なポイント:** AgentCore Gateway の CUSTOM_JWT authorizer は **IdP に依存しません**。AWS 公式ドキュメントには次のように記載されています。

> "The inbound authorizer is Identity Provider (IdP) agnostic and works with any OAuth 2.0 compatible identity provider."

有効な OIDC discovery URL のみが必要であり、Gateway はあらゆる発行者からのトークンを検証します。

### 2b. 変更が必要な箇所 (コンポーネントマッピング)

| コンポーネント                       | 現在 (Cognito)                                                                          | 新しい構成 (サードパーティ IdP)                                     |
| ------------------------------------ | --------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Gateway authorizer の discovery URL  | `https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration` | `https://{your-idp}/.well-known/openid-configuration`               |
| Token endpoint                       | `POST https://{cognito_domain}/oauth2/token`                                            | IdP の token endpoint (例: `https://{okta_domain}/oauth2/v1/token`) |
| トークン拡張へのアイデンティティ伝播 | `aws_client_metadata: {"verified_user_id": "..."}` (Cognito 固有)                       | IdP 固有の仕組み (下記参照)                                         |
| トークン拡張の仕組み                 | Pre-Token Lambda (V3、Cognito トリガー)                                                 | IdP のネイティブフック (下記参照)                                   |
| Cedar Policy                         | **変更なし** — 引き続き `principal.getTag("department")` などを参照                     | **変更なし**                                                        |
| Target Lambda                        | **変更なし** — ツール入力のみを受け取る                                                 | **変更なし**                                                        |

### IdP 固有のトークン拡張の仕組み

| IdP          | トークン拡張機能                  | アイデンティティの伝播方法                                                                                   |
| ------------ | --------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| **Okta**     | Token Inline Hooks                | フックがクライアントコンテキストを受け取り、トークンリクエストに渡されたカスタムパラメータに基づいて拡張可能 |
| **Auth0**    | Actions (post-client-credentials) | Actions は M2M アプリケーションに紐づくメタデータを参照したり、カスタム `audience` パラメータを使用可能      |
| **Entra ID** | Claims Mapping Policies           | アプリケーションロールとクレームマッピング。グループメンバーシップを直接トークンに含めることが可能           |
| **Cognito**  | Pre-Token Lambda (V3)             | `aws_client_metadata` が Lambda へ user_id を渡す                                                            |

### 2c. よくある懸念点

**Q: LLM はトークンを参照することがありますか？**

ありません。トークンは Python エージェントコードと MCP クライアントライブラリが管理する HTTP トランスポート層に存在します。LLM はツールスキーマとツールの実行結果のみを操作し、HTTP ヘッダーやトークンへのアクセスは持ちません。

**Q: IdP が M2M トークンに任意のクレームを注入できない場合はどうしますか？**

次のセクションで説明するアプローチ B (Gateway Interceptors) を使用してください。

---

## 3. アプローチ B — Gateway Interceptors (トークン拡張不要)

**対象:** OIDC 準拠の任意の IdP。トークン拡張フックを持たないものでも対応可能です。完全に動的なアクセス制御が必要な場合 (実行時に変化する権限がデータベースに格納されている場合など) にも有用です。

### 3a. 仕組み

M2M トークンをユーザークレームで拡張する代わりに、Runtime はユーザーアイデンティティを **別のカスタムヘッダー** として渡します。Gateway Interceptor Lambda がこのヘッダーを読み取り、アクセス制御の判断を下します。

Runtime が Gateway へユーザーアイデンティティを渡す方法は 2 つあります。

#### オプション 1: ユーザー ID のみを渡す

Runtime は検証済みのユーザー JWT から user_id を取り出し、プレーンな文字列ヘッダーとして渡します。

```
User JWT → Runtime (validates user JWT via any IdP's OIDC)
  → Runtime extracts user_id from validated JWT
  → Runtime gets a plain M2M token from IdP (no user claims needed)
  → Runtime sends to Gateway:
    - Authorization: Bearer <M2M_token>        ← proves machine trust
    - X-User-Id: alice@company.com             ← carries user identity (plain string)
  → Gateway authorizer validates M2M token only (machine trust)
  → Request Interceptor Lambda fires:
    - Reads X-User-Id from custom header
    - Looks up user's permissions (from IdP groups, DB, YAML, etc.)
    - Allows or denies the tools/call request
  → Response Interceptor Lambda fires (for tools/list):
    - Same permission lookup
    - Filters the tool list to only show permitted tools
  → Target receives tool input only (no tokens, no headers)
```

**Runtime 側のコード:**

```python
# エージェントコード内 (AgentCore Runtime で実行)
user_id = extract_user_id_from_context(context)  # 検証済みユーザー JWT から取得
m2m_token = await get_m2m_token()  # ユーザークレームを含まないプレーンな M2M

mcp_client = MCPClient(
    gateway_url=GATEWAY_URL,
    headers={
        "Authorization": f"Bearer {m2m_token}",  # マシンの信頼性
        "X-User-Id": user_id,                     # ユーザーアイデンティティ (プレーン文字列)
    }
)
```

**Interceptor 側の読み取り:**

```python
def lambda_handler(event, context):
    """Request interceptor: プレーン文字列ヘッダーからユーザーアイデンティティを読み取る。"""
    gateway_request = event['mcp']['gatewayRequest']
    headers = gateway_request.get('headers', {})

    # シンプルな文字列抽出 — JWT のデコードは不要
    user_id = headers.get('X-User-Id', '')

    # このユーザーの権限を検索 (DB、YAML、IdP API など)
    permissions = get_user_permissions(user_id)
    # ... 権限に基づいて許可または拒否
```

| 利点                                         | 欠点                                                                                    |
| -------------------------------------------- | --------------------------------------------------------------------------------------- |
| シンプル — interceptor で JWT デコードが不要 | Gateway 境界の信頼に依存する                                                            |
| 低レイテンシ — 署名検証なし                  | interceptor がユーザー属性を取得するために外部ソース (DB、IdP API) を呼び出す必要がある |
| データの露出が最小限                         | interceptor 内で user_id が真正であることを検証できない                                 |

**利用するタイミング:** Gateway 境界が信頼境界であり、(M2M トークンで検証された) 正規の Runtime のみがこのヘッダーを設定できる場合。

---

#### オプション 2: ユーザー JWT 全体を渡す

Runtime は元のユーザー JWT を別のヘッダーとして渡します。これにより、interceptor は **JWT 署名を検証** して改ざんされていないことを証明し、すべてのユーザークレームを直接抽出できます。

```
User JWT → Runtime (validates user JWT via any IdP's OIDC)
  → Runtime keeps the original user JWT
  → Runtime gets a plain M2M token from IdP (no user claims needed)
  → Runtime sends to Gateway:
    - Authorization: Bearer <M2M_token>        ← proves machine trust
    - X-User-Token: <original_user_JWT>        ← carries full user identity (verifiable)
  → Gateway authorizer validates M2M token only (machine trust)
  → Request Interceptor Lambda fires:
    - Reads X-User-Token from custom header
    - Verifies the JWT signature (using IdP's public keys from JWKS endpoint)
    - Extracts claims: user_id, email, groups, department, etc.
    - Decides allow/deny based on claims
  → Response Interceptor Lambda fires (for tools/list):
    - Same JWT verification and claim extraction
    - Filters the tool list based on user's claims
  → Target receives tool input only (no tokens, no headers)
```

**Runtime 側のコード:**

```python
# エージェントコード内 (AgentCore Runtime で実行)
user_jwt = get_user_jwt_from_request(context)  # 元のユーザー JWT (Runtime の authorizer で検証済み)
m2m_token = await get_m2m_token()  # ユーザークレームを含まないプレーンな M2M

mcp_client = MCPClient(
    gateway_url=GATEWAY_URL,
    headers={
        "Authorization": f"Bearer {m2m_token}",    # マシンの信頼性
        "X-User-Token": user_jwt,                   # 完全なユーザー JWT (検証可能)
    }
)
```

**Interceptor 側の読み取りと検証:**

```python
import jwt
import requests
from functools import lru_cache

# IdP から取得した JWKS (公開鍵) をキャッシュ
@lru_cache(maxsize=1)
def get_jwks(jwks_url):
    """IdP から JWKS 公開鍵を取得してキャッシュする。"""
    response = requests.get(jwks_url)
    return response.json()

def lambda_handler(event, context):
    """Request interceptor: ユーザー JWT を検証してクレームを抽出する。"""
    gateway_request = event['mcp']['gatewayRequest']
    headers = gateway_request.get('headers', {})

    # 完全なユーザー JWT を抽出
    user_token = headers.get('X-User-Token', '')

    if not user_token:
        return deny_request("No user token provided")

    # JWT 署名を検証してクレームを抽出
    try:
        # IdP の JWKS エンドポイントから公開鍵を取得
        jwks = get_jwks(IDP_JWKS_URL)  # 例: https://the-idp/.well-known/jwks.json

        # JWT をデコードして検証
        claims = jwt.decode(
            user_token,
            jwks,
            algorithms=["RS256"],
            audience=EXPECTED_AUDIENCE,
            issuer=EXPECTED_ISSUER
        )
    except jwt.ExpiredSignatureError:
        return deny_request("User token expired")
    except jwt.InvalidTokenError as e:
        return deny_request(f"Invalid user token: {e}")

    # 検証済みクレームから直接ユーザー属性を抽出
    user_id = claims.get('sub', '')
    department = claims.get('department', '')
    groups = claims.get('groups', [])  # Cognito の場合は 'cognito:groups'
    role = claims.get('role', 'viewer')

    # 検証済みクレームに基づいてアクセス制御を判断
    tool_name = gateway_request['body'].get('params', {}).get('name', '')

    if not is_authorized(user_id, department, groups, role, tool_name):
        return deny_request(f"User {user_id} ({department}) not authorized for {tool_name}")

    # 認可済み — そのまま通過
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayRequest": gateway_request
        }
    }
```

| 利点                                                                     | 欠点                                                                                        |
| ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| 暗号学的に検証可能 — interceptor が JWT の真正性を証明できる             | より複雑 — interceptor 内で JWT ライブラリと JWKS の取得が必要                              |
| すべてのユーザークレームが直接利用可能 (外部参照不要)                    | レイテンシがやや高い (署名検証 + JWKS キャッシュ)                                           |
| 多層防御 — M2M トークンが侵害されてもユーザー JWT も有効である必要がある | ヘッダーサイズが大きい (完全な JWT 対 シンプルな文字列)                                     |
| Runtime 境界が完全に信頼されていない場合でも動作する                     | トークンの有効期限を扱う必要がある (ユーザー JWT が M2M トークンより先に期限切れする可能性) |

**利用するタイミング:** 多層防御が必要な場合 (2 つの独立した検証)、外部サービスを呼び出さずに interceptor 内で複数のユーザークレームが必要な場合、もしくはセキュリティ要件として Gateway レベルでユーザーアイデンティティの暗号学的証明が求められる場合。

---

#### 比較: オプション 1 とオプション 2

| 観点                           | オプション 1: X-User-Id (文字列)                                | オプション 2: X-User-Token (完全な JWT)                     |
| ------------------------------ | --------------------------------------------------------------- | ----------------------------------------------------------- |
| **セキュリティモデル**         | Gateway 境界を信頼する (M2M トークンで呼び出し元の正当性を証明) | 独立して検証する (JWT 署名でユーザーアイデンティティを証明) |
| **interceptor が受け取るもの** | プレーンな user_id 文字列                                       | すべてのクレームを含む完全な JWT                            |
| **interceptor の複雑さ**       | シンプル — ヘッダーを読み、権限を検索するだけ                   | より複雑 — JWT を検証し、クレームを抽出                     |
| **必要となる外部参照**         | あり — ユーザー属性のために DB/IdP に問い合わせる必要           | なし — クレームは JWT 内に存在                              |
| **レイテンシ**                 | より低い (暗号処理なし) + 検索時間                              | より高い (暗号検証あり) だが検索は不要                      |
| **トークン有効期限の懸念**     | なし (単なる文字列)                                             | 必要 — ユーザー JWT が期限切れする可能性                    |
| **なりすましリスク**           | 低い (正規の Runtime のみが Gateway に到達可能)                 | なし (JWT 署名は暗号学的証明)                               |
| **適している用途**             | 信頼された Runtime 境界を持つ内部システム                       | 高セキュリティ環境、ゼロトラストアーキテクチャ              |

---

**重要なポイント (両オプションに共通):** M2M トークンとユーザーアイデンティティは **別々の関心事** です。

- **M2M トークン** (`Authorization` ヘッダー内) → Runtime が正規の呼び出し元であることを証明 → Gateway authorizer によって検証
- **ユーザーアイデンティティ** (`X-User-Id` または `X-User-Token` ヘッダー内) → リクエストが誰のためのものかを interceptor に伝える → アクセス制御判断に使用

interceptor は、Pre-Token Lambda と Cedar Policy Engine がユーザーアイデンティティをチェックする役割の **両方** を置き換えます。Gateway authorizer は引き続き M2M トークンを検証 (呼び出しが正規であることを保証) しますが、アクセス制御判断は Cedar Policy から Interceptor Lambda に移ります。

> **補足:** どちらのオプションでも、LLM がトークンやヘッダーにアクセスすることはありません。これらはエージェントの Python コードと MCP クライアントライブラリが管理する HTTP トランスポート層にのみ存在します。LLM はツールスキーマとツールの実行結果のみを操作します。

### 3b. Request Interceptor (`tools/call` を制御)

request interceptor は target Lambda が実行される **前** に発火します。ツール呼び出しを許可するか拒否するかを判断します。

**フロー:**

```
Agent calls tools/call → Gateway → Request Interceptor Lambda → (if allowed) → Target
```

**動作内容:**

1. カスタムヘッダー (例: `X-User-Id`) からユーザーアイデンティティを抽出する
2. 呼び出されているツールを特定する
3. ユーザーが権限を持っているかを確認する (スコープ、DB 検索、YAML など)
4. **認可されている場合** → リクエストをターゲットに通過させる
5. **認可されていない場合** → 構造化された MCP エラーを返し、ターゲットは実行されない

**サンプルコード:**

```python
def lambda_handler(event, context):
    """Request interceptor: tools/call のアクセスを制御する。"""
    gateway_request = event['mcp']['gatewayRequest']

    # カスタムヘッダーからユーザーアイデンティティを抽出
    headers = gateway_request.get('headers', {})
    user_id = headers.get('X-User-Id', '')

    # 呼び出されているツールを特定
    tool_name = gateway_request['body'].get('params', {}).get('name', '')
    target = gateway_request.get('target', '')

    # 権限を検索 (DB、YAML、IdP groups など)
    if not check_tool_authorization(user_id, tool_name, target):
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {
                "transformedGatewayRequest": {
                    "statusCode": 403,
                    "body": {
                        "error": {
                            "code": "UNAUTHORIZED",
                            "message": f"User {user_id} is not authorized to call {tool_name}"
                        }
                    }
                }
            }
        }

    # 認可済み — ターゲットへ通過
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayRequest": gateway_request
        }
    }


def check_tool_authorization(user_id, tool, target):
    """ユーザーがこのツールを呼び出す権限を持つかチェックする。

    データベースへの問い合わせ、YAML 設定の読み込み、IdP API の呼び出しなどが可能。
    """
    user_scopes = get_user_scopes(user_id)  # DB または IdP から取得
    if target in user_scopes:
        return True
    return f"{target}:{tool}" in user_scopes
```

### 3c. Response Interceptor (`tools/list` を制御)

response interceptor はターゲットが応答した **後** に発火します。エージェントがユーザーに認可されたツールのみを参照するように、ツールリストをフィルタリングします。

**フロー:**

```
Agent calls tools/list → Gateway → Target returns ALL tools → Response Interceptor → Filtered list
```

**動作内容:**

1. ターゲットからツールリスト全体を受け取る
2. レスポンスペイロードのヘッダーからユーザーアイデンティティを抽出する
3. 各ツールについて、ユーザーが認可されているかを確認する
4. 許可されたツールのみを含むよう変換されたレスポンスを返す

**サンプルコード:**

```python
def lambda_handler(event, context):
    """Response interceptor: tools/list の結果をフィルタリングする。"""
    # gateway response と Authorization ヘッダーを抽出
    gateway_response = event['mcp']['gatewayResponse']
    auth_header = gateway_response['headers'].get('Authorization', '')

    # ユーザーアイデンティティを抽出 (透過したカスタムヘッダーから)
    user_id = gateway_response['headers'].get('X-User-Id', '')

    # gateway response からツールを取得
    tools = gateway_response['body']['result'].get('tools', [])
    # structuredContent も確認 (セマンティック検索のレスポンス用)
    if not tools:
        tools = gateway_response['body']['result'].get('structuredContent', {}).get('tools', [])

    # ユーザー権限を検索してツールをフィルタリング
    user_scopes = get_user_scopes(user_id)  # DB、YAML、IdP などから取得
    filtered_tools = filter_tools_by_scope(tools, user_scopes)

    # フィルタリング済みツールを含む変換済みレスポンスを返す
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": 200,
                "headers": {"Authorization": auth_header},
                "body": {
                    "result": {"tools": filtered_tools}
                }
            }
        }
    }


def filter_tools_by_scope(tools, allowed_scopes):
    """ユーザーの許可スコープに基づきツールをフィルタリングする。"""
    filtered_tools = []
    for tool in tools:
        target, action = tool['name'].split('___')
        # ユーザーがターゲット全体へのアクセス、または特定ツールへのアクセスを持つか確認
        if target in allowed_scopes or f"{target}:{action}" in allowed_scopes:
            filtered_tools.append(tool)
    return filtered_tools
```

### 3d. CDK 設定

```typescript
import { LambdaInterceptor } from "aws-cdk-lib/aws-bedrockagentcore"

// Request interceptor — ターゲットの前に発火
const requestInterceptor = LambdaInterceptor.forRequest(requestInterceptorLambda, {
  passRequestHeaders: true, // 必須: カスタムヘッダー (X-User-Id など) の読み取りを有効化
})

// Response interceptor — ターゲット応答の後に発火
const responseInterceptor = LambdaInterceptor.forResponse(responseInterceptorLambda, {
  passRequestHeaders: true, // 必須: レスポンスペイロード内でヘッダーの読み取りを有効化
})

// Gateway にアタッチ
const gateway = new Gateway(this, "MyGateway", {
  // ... その他の設定
  interceptors: [requestInterceptor, responseInterceptor],
})
```

**重要:** `passRequestHeaders: true` は必須です。デフォルトでは、セキュリティ上の理由 (ヘッダーには機密性のあるクレデンシャルが含まれる可能性があるため) からヘッダーは interceptor へ転送されません。明示的にオプトインする必要があります。

### 3e. セキュリティとよくある懸念点

**Q: LLM がユーザートークンに触れることはありますか？**

ありません。トークンとユーザーアイデンティティは完全に HTTP トランスポート層に存在します。

```
┌─────────────────────────────────────────────────────────┐
│ AgentCore Runtime                                        │
│                                                          │
│  ┌──────────────────┐    ┌───────────────────────────┐  │
│  │ Agent Code       │    │ LLM (Bedrock)             │  │
│  │ (Python)         │◄──►│                           │  │
│  │                  │    │ Only sees:                │  │
│  │ Has access to:   │    │ - Tool schemas            │  │
│  │ - User JWT       │    │ - Tool results            │  │
│  │ - M2M token      │    │ - Conversation text       │  │
│  │ - HTTP headers   │    │                           │  │
│  └────────┬─────────┘    └───────────────────────────┘  │
│           │                                              │
│           ▼                                              │
│  ┌──────────────────┐                                   │
│  │ MCP Client       │ ← handles HTTP transport          │
│  │ (tokens, headers │   (invisible to LLM)              │
│  │  live here)      │                                   │
│  └──────────────────┘                                   │
└─────────────────────────────────────────────────────────┘
```

LLM は「ツール X を入力 Y で呼び出す」と指示するだけで、トークンやヘッダーを含むすべての HTTP 通信は MCP クライアントが処理します。

**Q: 何が何を検証するのですか？**

| コンポーネント                  | 検証内容                                         | 目的                                                                 |
| ------------------------------- | ------------------------------------------------ | -------------------------------------------------------------------- |
| Gateway Authorizer (CUSTOM_JWT) | IdP からの M2M トークン                          | 「私を呼び出しているのは正規の Runtime か？」(マシンの信頼性)        |
| Request Interceptor Lambda      | X-User-Id ヘッダーからのユーザーアイデンティティ | 「このユーザーはこのツールの利用を許可されているか？」(アクセス制御) |

これらは順次動作します。

1. authorizer が最初に発火 → M2M トークンが無効ならリクエストは拒否 (401)
2. interceptor が次に発火 → ユーザーが権限を持たなければリクエストは拒否 (403)
3. ターゲットが最後に発火 → ツール入力のみを受け取る (トークンもユーザーコンテキストもなし)

**Q: interceptor と Cedar Policy は共存できますか？**

はい。両者は補完的な役割を果たします。

- **Cedar Policy** は静的・宣言的なルール (例: 「finance 部門は財務ツールへアクセス可能」) を扱う
- **Interceptors** は動的なケース (例: 実行時に変化する DB 内の権限) を扱う

両方が有効な場合の評価順序は次のとおりです。

1. Gateway authorizer がトークンを検証
2. (設定されていれば) Cedar Policy Engine が評価
3. interceptor が発火 (request はターゲット前、response はターゲット後)

リクエストはすべてのチェックを通過する必要があります。

---

## 4. Cedar Policy 対 Interceptors — どちらをいつ使うか

| 機能                              | Cedar Policy                                    | Gateway Interceptors                                                           |
| --------------------------------- | ----------------------------------------------- | ------------------------------------------------------------------------------ |
| フィルタリングが行われる場所      | Policy Engine (組み込み、マネージド)            | Lambda コード                                                                  |
| チェックする内容                  | JWT クレーム (タグ) + context.input             | ユーザーアイデンティティ (ヘッダーから) + 任意の外部ソース (DB、YAML、IdP API) |
| 動的か？                          | 静的ルール (変更にはポリシーの再デプロイが必要) | 完全に動的 (Lambda は実行時に何でも問い合わせ可能)                             |
| ツールフィルタリング (tools/list) | PartiallyAuthorizeActions により自動            | response interceptor で実装                                                    |
| ツール実行 (tools/call)           | AuthorizeAction により自動                      | request interceptor で実装                                                     |
| トークン拡張が必要か？            | 必要 — クレームが JWT に含まれている必要がある  | 不要 — ヘッダーを読み、外部ソースを問い合わせ可能                              |
| 入力バリデーション                | 可能 — `context.input.amount < 1000`            | 可能 — Lambda はリクエストペイロード全体にアクセス可能                         |
| スキーマ変換 / PII マスキング     | 不可                                            | 可能 — interceptor がリクエスト/レスポンスを変換可能                           |
| マルチテナント分離                | 限定的 (クレームベースのみ)                     | 完全な柔軟性 (テナント DB へ問い合わせ可能)                                    |
| コード不要                        | はい — 宣言的な Cedar ポリシー                  | いいえ — Lambda コードが必要                                                   |

### ガイダンス

- **Cedar Policy を使うとき:** アクセスルールが頻繁に変化しないユーザー属性 (department、role) に基づいており、コードを書かずにシンプルかつ監査可能で宣言的なポリシーを使いたい場合。
- **Interceptors を使うとき:** 権限が動的 (DB に保存されている)、認可判断のために外部サービスを呼び出す必要がある、スキーマ変換や PII マスキングが必要、または IdP がトークン拡張をサポートしていない場合。
- **両方を使うとき:** Cedar が基本ルール (例: 「finance のみが財務ツールへアクセス可能」) を扱い、interceptor がエッジケース (例: 「ただしメンテナンス中は除く」「特定テナントのみ」) を扱う場合。

---

## 5. Cognito User Groups と Cedar Policy の連携

ハードコードされたマッピングではなくネイティブの Cognito グループを活用したい、Cognito を継続して利用するチーム向けの内容です。

### 5a. Cognito User Groups とは何か

Cognito User Groups は、ユーザーを論理的なグループ (例: finance、engineering、admin) に整理するための組み込み機能です。次の機能を提供します。

- ロール、部門、アクセスレベル別にユーザーを分類する手段
- ユーザー認証トークンへのグループ名の自動的な含有 (`cognito:groups` クレーム)
- グループごとの IAM ロール関連付け (AWS リソースアクセス用)

**ユーザーがグループに割り当てられる方法:**

| 方法                             | 発生タイミング                   | ユースケース                               |
| -------------------------------- | -------------------------------- | ------------------------------------------ |
| AWS Console                      | 管理者の手動操作                 | アドホックなグループ管理                   |
| AdminAddUserToGroup API          | プログラム経由 (例: Lambda 内)   | 登録時の自動割り当て                       |
| Post-Confirmation Lambda Trigger | ユーザーがメール確認した後に自動 | 新規ユーザーへのデフォルトグループ割り当て |
| Admin SDK / CLI                  | バッチ処理                       | 一括ユーザー管理                           |

**例: 登録時にグループを自動割り当て (Post-Confirmation Lambda):**

```python
import boto3

cognito = boto3.client('cognito-idp')

def lambda_handler(event, context):
    """Post-Confirmation トリガー: 新規ユーザーをデフォルトグループへ割り当てる。"""
    user_pool_id = event['userPoolId']
    username = event['userName']

    # メールドメインなどのロジックに基づきデフォルトグループを割り当て
    email = event['request']['userAttributes'].get('email', '')

    if email.endswith('@finance.company.com'):
        group = 'finance'
    elif email.endswith('@eng.company.com'):
        group = 'engineering'
    else:
        group = 'general'

    cognito.admin_add_user_to_group(
        UserPoolId=user_pool_id,
        Username=username,
        GroupName=group
    )

    return event
```

### 5b. 課題: グループは M2M トークンに含まれない

`cognito:groups` クレームは、ユーザー認証トークン (Authorization Code フロー) には自動的に含まれます。しかし、M2M トークン (Client Credentials フロー) には含まれません。

現在のアーキテクチャでは Gateway が M2M トークンを受け取るため、Cedar Policy はネイティブにユーザーのグループを参照できません。

```
User Auth Token (has groups):     M2M Token (NO groups):
{                                 {
  "sub": "alice",                   "sub": "machine-client-id",
  "cognito:groups": [               "scope": "gateway/read gateway/write",
    "finance",                      "token_use": "access"
    "admin"                         // No user context!
  ]                               }
}
```

### 5c. 解決策: Pre-Token Lambda がグループを読み取る

現在の実装では、Runtime が検証済みの access token からユーザーの `cognito:groups` を読み取り、`aws_client_metadata` 経由で Pre-Token Lambda に渡すため、Lambda は API 呼び出しを必要としません。access token にグループメンバーシップが含まれない IdP に切り替える場合は、以下のように Lambda が Cognito の `AdminListGroupsForUser` などの API を呼び出してグループを取得し、カスタムクレームとして M2M トークンに注入することもできます。

```python
import boto3
import os
import logging

logger = logging.getLogger()
cognito = boto3.client('cognito-idp')

USER_POOL_ID = os.environ.get('USER_POOL_ID')

def lambda_handler(event, context):
    """V3 Pre-Token Lambda: 実際の Cognito グループ情報を M2M トークンに注入する。"""
    trigger_source = event.get("triggerSource", "")

    # M2M (Client Credentials) フローのみ処理
    if trigger_source != "TokenGeneration_ClientCredentials":
        return event

    # Runtime から渡された検証済み user_id を取得
    client_metadata = event.get("request", {}).get("clientMetadata", {})
    verified_user_id = client_metadata.get("verified_user_id", "")

    if not verified_user_id:
        logger.warning("No verified_user_id in clientMetadata")
        return event

    # --- ハードコードされたマッピングを置き換え ---
    # ユーザーの実際の Cognito グループを取得
    user_groups = get_user_groups(verified_user_id)

    # グループから department と role を決定
    department = resolve_department(user_groups)
    role = resolve_role(user_groups)

    # M2M アクセストークンに注入
    event["response"]["claimsAndScopeOverrideDetails"] = {
        "accessTokenGeneration": {
            "claimsToAddOrOverride": {
                "user_id": verified_user_id,
                "department": department,
                "role": role,
                "user_groups": ",".join(user_groups),  # 例: "finance,admin"
            }
        }
    }

    return event


def get_user_groups(username):
    """Admin API 経由でユーザーの Cognito グループを取得する。"""
    try:
        response = cognito.admin_list_groups_for_user(
            UserPoolId=USER_POOL_ID,
            Username=username,
        )
        return [group['GroupName'] for group in response.get('Groups', [])]
    except Exception as e:
        logger.error("Failed to fetch groups for user %s: %s", username, str(e))
        return []


def resolve_department(groups):
    """グループメンバーシップから department を解決する。"""
    if 'finance' in groups:
        return 'finance'
    elif 'engineering' in groups:
        return 'engineering'
    return 'guest'


def resolve_role(groups):
    """グループメンバーシップから role を解決する。"""
    if 'admin' in groups:
        return 'admin'
    elif 'developer' in groups:
        return 'developer'
    return 'viewer'
```

### 5d. グループを使った Cedar Policy の例

グループ情報がクレームとして注入されると、Cedar はそれをアクセス制御に利用できます。

**オプション 1: department を確認する (グループから解決)**

```cedar
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"finance-target___generate_report",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("department") &&
  principal.getTag("department") == "finance"
};
```

**オプション 2: role を確認する (グループから解決)**

```cedar
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"admin-target___delete_records",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("role") &&
  principal.getTag("role") == "admin"
};
```

**オプション 3: `like` を用いて生のグループメンバーシップを確認する**

```cedar
// user_groups claim contains: "finance,admin,reporting"
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"finance-target___view_reports",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("user_groups") &&
  principal.getTag("user_groups") like "*finance*"
};
```

> **カンマ区切りグループに対する `like` の注意点:** パターン `like "*finance*"` は "refinance" という名前のグループにもマッチしてしまいます。厳密な一致のためには `like "finance,*"`、`like "*,finance,*"`、`like "*,finance"` のようなパターンを利用するか、より望ましい方法として Lambda 内で boolean に事前解決してください (オプション 4 参照)。

**オプション 4: 事前解決した boolean (複雑なグループロジックには推奨)**

Pre-Token Lambda 内でグループメンバーシップをシンプルな boolean クレームに解決します。

```python
claims["is_finance_team"] = "true" if "finance" in user_groups else "false"
claims["is_admin"] = "true" if "admin" in user_groups else "false"
claims["can_delete"] = "true" if "admin" in user_groups and "finance" in user_groups else "false"
```

そして Cedar 側では次のように記述します。

```cedar
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"admin-target___delete_records",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  principal.hasTag("is_admin") &&
  principal.getTag("is_admin") == "true"
};
```

このアプローチでは複雑なグループロジックを Lambda に閉じ込め、Cedar ポリシーはシンプルで読みやすい状態を保てます。

---

## 6. 進化のパス

現在のデモアーキテクチャから派生する 3 つのパスのまとめです。

```
Current Demo (Cognito + hardcoded user mapping in Pre-Token Lambda)
    │
    ├── Path 1: Keep Cognito, use real groups
    │   ├── Replace hardcoded mapping with AdminListGroupsForUser
    │   ├── Cedar Policy checks group-based claims
    │   └── See: Section 5
    │
    ├── Path 2: Swap IdP that supports token enrichment
    │   ├── Replace Cognito with Okta/Auth0/Entra
    │   ├── Use IdP's native token hook (replaces Pre-Token Lambda)
    │   ├── Cedar Policy remains unchanged
    │   └── See: Section 2
    │
    └── Path 3: Swap to any IdP + Gateway Interceptors
        ├── No token enrichment needed
        ├── Interceptors handle all access control dynamically
        ├── Works with ANY OIDC-compliant IdP
        └── See: Section 3
```

---

## 7. FAQ / クイックリファレンス

**Q1: AgentCore Gateway は任意の IdP で利用できますか？**

はい。Gateway の CUSTOM_JWT authorizer は OAuth 2.0 / OIDC 準拠の任意のアイデンティティプロバイダーで動作します。有効な OIDC discovery URL (`.well-known/openid-configuration`) のみが必要です。Gateway はこれを利用して動的に公開鍵を取得し、トークンを検証します。

**Q2: IdP を交換するための最小限の変更は何ですか？**

新しい IdP がトークン拡張をサポートしている場合は次の通りです。

1. Gateway authorizer の discovery URL を変更
2. Runtime のトークンエンドポイント呼び出しを変更
3. Pre-Token Lambda を IdP のネイティブなトークンフックに置き換える
4. その他 (Cedar Policy、ターゲット) はそのまま

**Q3: interceptor は Cedar Policy を置き換えますか？**

置き換えることは可能ですが、必須ではありません。interceptor と Cedar Policy は補完的な役割を持ちます。

- **Cedar Policy** = JWT クレームに基づく静的・宣言的・コード不要のルール
- **Interceptors** = 外部ソースを問い合わせられる動的・コードベースのロジック

どちらか単独でも、多層防御のために両方を併用することも可能です。

**Q4: アプローチ A と B のどちらを選ぶか？**

- **アプローチ A を選ぶケース** IdP がトークン拡張をサポートしており、アクセスルールが (department/role などのユーザー属性に基づく) 比較的静的な場合
- **アプローチ B を選ぶケース** IdP がトークン拡張をサポートしていない、もしくは動的な権限・スキーマ変換・PII マスキング・マルチテナント分離が必要な場合

**Q5: Cedar Policy と interceptor を同時に使えますか？**

はい。両方が有効な場合は次の通りです。

1. Gateway authorizer がまずトークンを検証
2. 次に Cedar Policy Engine が評価 (設定されていれば)
3. interceptor が発火 (request はターゲット前、response はターゲット後)

リクエストはすべてのチェックを通過する必要があります。これにより多層防御を実現できます。

**Q6: Cognito user groups は Cedar から利用できますか？**

はい。`cognito:groups` クレームは M2M トークン自体には現れませんが、ユーザーの access token には含まれます。Runtime がそこから読み取り、`aws_client_metadata` 経由で Pre-Token Lambda に渡し、Lambda が結果の `department`/`role` をカスタムクレームとして注入します — `AdminListGroupsForUser` の呼び出しは不要です。詳細とコード例はセクション 5 を参照してください。

**Q7: カスタムヘッダー経由でのユーザーアイデンティティの受け渡しは安全ですか？**

はい、その理由は次の通りです。

1. Gateway authorizer が最初に M2M トークンを検証する (呼び出し元が正規の Runtime であることを証明)
2. カスタムヘッダー (`X-User-Id`) は (`passRequestHeaders: true` を設定した) interceptor Lambda のみが読み取れる
3. LLM は HTTP ヘッダーへアクセスできない — それらはトランスポート層にのみ存在する

セキュリティ境界は Gateway レベルで強制されます。誰かが (有効な M2M トークンなしで) 直接 Gateway を呼び出そうとした場合、interceptor が発火する前に authorizer がそれを拒否します。
