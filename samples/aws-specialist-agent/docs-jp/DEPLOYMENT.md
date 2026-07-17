# デプロイガイド

このガイドでは、Fullstack AgentCore Solution Template (FAST) を AWS にデプロイする手順を説明します。

## 前提条件

デプロイを行う前に、以下を準備してください。

- **Node.js 20+** がインストールされていること（[AWS guide for installing Node.js on EC2](https://docs.aws.amazon.com/sdk-for-javascript/v2/developer-guide/setting-up-node-on-ec2-instance.html) を参照）
- **AWS CLI** が認証情報とともに構成されていること（`aws configure`）- [AWS CLI Configuration guide](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-quickstart.html) を参照
- **AWS CDK CLI** がインストールされていること: `npm install -g aws-cdk`（[CDK Getting Started guide](https://docs.aws.amazon.com/cdk/v2/guide/getting-started.html) を参照）
- **Python 3.11 以上**（標準ライブラリのみ - デプロイには仮想環境は不要）
- **Docker** - すべてのデプロイで必要です。[Install Docker Engine](https://docs.docker.com/engine/install/) を参照してください。`docker ps` で確認できます。代わりに Mac では [Finch](https://github.com/runfinch/finch) を使用することもできます。non-ARM マシンを使用している場合は以下を参照してください。
- 以下を作成するための十分な権限を持つ AWS アカウント:
  - S3 buckets
  - CloudFront distributions
  - Cognito User Pools
  - Amplify Hosting projects
  - Bedrock AgentCore resources
  - IAM roles and policies

### 別のアカウント／リージョンへデプロイする場合

コミット済みの `config.yaml` は **us-east-1** で検証済みです。同一アカウント・同一リージョン内であれば、`vpc_cidr` を他と重複しない値にし、`admin_user_email` を実在アドレスに変えるだけでそのままデプロイできます。**別アカウントや別リージョン**へデプロイする場合は、加えて以下が必要です。

1. **リージョンは us-east-1（現時点）。** AgentCore Web Search が us-east-1 のみ提供のため、それ以外のリージョンでは意図的にデプロイが早期失敗します。OpenAI（GPT）モデルも同リージョンの `bedrock-mantle` エンドポイント経由で到達します。別リージョンにするには、先に Web Search ターゲットと OpenAI モデルを外す必要があります。
2. **アカウントに合った AZ をピン留めする。** AgentCore Runtime の VPC モードは特定の AZ **ID**（us-east-1: `use1-az1` / `use1-az2` / `use1-az4`）のみ対応し、AZ 名 → ID のマッピングはアカウント固有です。既定の `availability_zones: [us-east-1b, us-east-1d]` は検証アカウントでは対応 ID にマップされますが、あなたのアカウントでは異なる可能性があります。`aws ec2 describe-availability-zones --query "AvailabilityZones[].[ZoneName,ZoneId]" --output table` で正しい AZ 名を導出し、対応 ID にマップされる 2 つの名前を `backend.availability_zones` に設定してください（外れるとサブネット作成が失敗します）。
3. **Bedrock のモデルアクセスを有効化する。** 選択可能なモデル（Claude Fable 5 / Opus / Sonnet / Haiku と OpenAI GPT）がアカウントで有効である必要があります。Claude Fable 5 はさらに、呼び出し元リージョンで Bedrock のデータ保持モードを `provider_data_share` に設定する必要があります（後述の「Claude Fable 5 の有効化」を参照）。アクセスのないモデルは `infra-cdk/lib/utils/model-registry.ts` で `available: false` にしてください。

## 構成

### 1. 構成ファイルの更新

`infra-cdk/config.yaml` を編集してデプロイをカスタマイズします。

```yaml
stack_name_base: your-project-name # 任意のスタック名に変更してください（最大 35 文字）

admin_user_email: null # 任意: admin@example.com（ユーザーを自動作成して認証情報をメール送信）

backend:
  pattern: strands-single-agent # 利用可能なパターン: strands-single-agent, langgraph
  deployment_type: docker # 利用可能なデプロイタイプ: docker (default), zip
```

**重要**:

- 競合を避けるため、`stack_name_base` をプロジェクト固有の一意な名前に変更してください
- 最大長は 35 文字です（AWS AgentCore runtime の命名規則による制約）
- コミット済みの `infra-cdk/config.yaml` では `admin_user_email` がダミー値（`your-email+fastprojectadmin@example.com`）になっています。**デプロイ前に必ず自分の実在する受信可能なアドレスに変更してください** — 管理者ユーザーの一時パスワードがこのアドレスに送信されるため、ダミーのままだとサインインできません。
- department/role の認可はメールアドレスではなく Cognito の**グループ所属**で決まります。グループ所属は CDK 管理外の運用データなので、デプロイ後に管理者ユーザーをグループに追加してください（例: `aws cognito-idp admin-add-user-to-group --user-pool-id <pool-id> --username <email> --group-name finance`）。未所属のままだと "guest" 扱いで全 Gateway ツールが拒否されます。

### デプロイタイプ

FAST は AgentCore Runtime に対して 2 つのデプロイタイプをサポートしています。`infra-cdk/config.yaml` で `deployment_type` を設定します。

| タイプ             | 説明                                                         |
| ------------------ | ------------------------------------------------------------ |
| `docker` (default) | コンテナイメージをビルドし、ECR にプッシュします             |
| `zip`              | Lambda 経由でコードをパッケージ化し、S3 にアップロードします |

**補足**: どちらのデプロイタイプでも Docker は必要です。`zip` オプションは agent runtime のパッケージ化方法にのみ影響します。スタック内の他の Lambda 関数は依然として依存関係のバンドルに Docker を使用します。

**Docker (デフォルト) を使用するケース:**

- PyPI で ARM64 wheel が提供されていないネイティブ C/C++ ライブラリが必要な場合
- デプロイパッケージが 250 MB を超える場合
- カスタムの OS レベル依存関係が必要な場合
- 最大限の互換性を求める場合

**ZIP を使用するケース:**

- 開発時の反復速度を高めたい場合
- 依存関係が pure Python であるか、ARM64 wheel が利用可能な場合
- セッションスループットを高めたい場合

**ZIP パッケージに含まれるもの**: `agent/<your-pattern>/`、`agent/utils/`、`gateway/`、`tools/` の各ディレクトリが、`requirements.txt` の依存関係とともにバンドルされます。これは Docker デプロイの Dockerfile における `COPY` コマンドに対応しています。

### VPC デプロイ（プライベートネットワーク）

デフォルトでは、AgentCore Runtime はインターネットアクセス可能な PUBLIC ネットワークモードで動作します。プライベートなネットワーク分離のために既存の VPC へ runtime をデプロイするには、`infra-cdk/config.yaml` で `network_mode: VPC` を設定し、VPC の詳細を指定します。

#### VPC の内側／外側で動作するもの

VPC モードを有効にすると、**AgentCore Runtime**（エージェントコード）は VPC のプライベートサブネット内で動作します。エージェントが行うすべてのネットワーク呼び出しは VPC のネットワーキングルールに従い、AWS サービスには VPC エンドポイント経由で到達します。エージェントが直接インターネット呼び出しを行うことはありません。

以下のコンポーネントは VPC の **外側** にある AWS マネージドインフラストラクチャで動作します。

- **Gateway tool Lambdas** — エージェントは `bedrock-agentcore.gateway` VPC エンドポイント経由で Gateway を呼び出します（プライベートネットワーキング）。Gateway はその後、AWS マネージドインフラストラクチャ上で Lambda 関数を呼び出します。エージェントのネットワーク呼び出し自体はプライベートに保たれ、Lambda の実行のみ VPC の外で行われます。
- **Code Interpreter** — エージェントは `bedrock-agentcore` VPC エンドポイント経由で Code Interpreter API を呼び出します。サンドボックス実行は Bedrock のマネージド環境で行われます。
- **Bedrock model invocations** — モデル呼び出しは `bedrock-runtime` VPC エンドポイント経由で Bedrock のマネージドインフラストラクチャに到達します。
- **Frontend (Amplify/CloudFront)** — 完全に分離されており、パブリック向けで、VPC デプロイの一部ではありません。

要するに、エージェントの送信ネットワークトラフィックは VPC エンドポイント経由でプライベートな AWS ネットワーキング上に留まります。エージェントが呼び出すサービス（Bedrock、Gateway、Code Interpreter）は VPC 外のインフラストラクチャ上で実行される場合がありますが、エージェントからそれらのサービス API へのネットワーク経路はプライベートです。

#### 構成

```yaml
backend:
  pattern: strands-single-agent
  deployment_type: docker
  network_mode: VPC
  vpc:
    vpc_id: vpc-0abc1234def56789a
    subnet_ids:
      - subnet-aaaa1111bbbb2222c
      - subnet-cccc3333dddd4444e
    security_group_ids: # 任意 - 省略時はデフォルトの SG が作成されます
      - sg-0abc1234def56789a
```

`vpc_id` および `subnet_ids` フィールドは必須です。`security_group_ids` フィールドは任意で、省略した場合は CDK construct が runtime 用のデフォルトセキュリティグループを作成します。

#### 必要な VPC エンドポイント

VPC モードでデプロイすると、runtime はインターネットアクセスのないプライベートサブネット内で動作します。エージェントが依存する AWS サービスに到達できるよう、VPC には以下の VPC エンドポイントを構成しておく必要があります。

| Endpoint                                           | Service                          | Type      |
| -------------------------------------------------- | -------------------------------- | --------- |
| `com.amazonaws.{region}.bedrock-runtime`           | Bedrock model invocation         | Interface |
| `com.amazonaws.{region}.bedrock-agentcore`         | AgentCore Identity (Token Vault) | Interface |
| `com.amazonaws.{region}.bedrock-agentcore.gateway` | AgentCore Gateway (MCP tools)    | Interface |
| `com.amazonaws.{region}.ssm`                       | SSM Parameter Store              | Interface |
| `com.amazonaws.{region}.secretsmanager`            | Secrets Manager                  | Interface |
| `com.amazonaws.{region}.logs`                      | CloudWatch Logs                  | Interface |
| `com.amazonaws.{region}.ecr.api`                   | ECR API (Docker deployment)      | Interface |
| `com.amazonaws.{region}.ecr.dkr`                   | ECR Docker (Docker deployment)   | Interface |
| `com.amazonaws.{region}.s3`                        | S3 (ZIP deployment, ECR layers)  | Gateway   |
| `com.amazonaws.{region}.dynamodb`                  | DynamoDB (feedback table)        | Gateway   |
| `com.amazonaws.{region}.xray`                      | X-Ray (OTel trace export)        | Interface |
| `com.amazonaws.{region}.bedrock-mantle`            | OpenAI GPT-5.x (Responses API)   | Interface |

`{region}` をデプロイリージョン（例: `us-east-1`）に置き換えてください。

すべての interface endpoint は private DNS を有効化し、AgentCore Runtime からのトラフィックを許可するサブネットおよびセキュリティグループに関連付ける必要があります。

#### サブネット要件

- CDK が管理する VPC は **完全に隔離されたプライベートサブネット**（`PRIVATE_ISOLATED`）を使用します。`0.0.0.0/0` ルートは一切ありません
- 高可用性のため、AgentCore がサポートする AZ（少なくとも 2 つ）にピン留めします
- runtime ENI のために十分な利用可能 IP アドレスを持つサブネットを使用してください

#### NAT Gateway なし（完全閉域）

デフォルトのデプロイには **NAT Gateway がなく**、アウトバウンドのインターネットアクセスもありません。すべての依存先に VPC エンドポイント経由で到達できるため、これで動作します。

- Gateway 用 M2M トークンは AgentCore Identity（Token Vault）経由で取得します。Cognito トークン交換は AWS 内のサーバーサイドで実行され、`bedrock-agentcore` VPC エンドポイント経由で到達可能なため、Runtime はパブリックな Cognito ホストドメインを呼び出しません。
- ユーザーアイデンティティは `aws_client_metadata` で M2M トークンに伝播されます（追加の egress 不要）。
- S3 Files（skills）は **VPC 内の** マウントターゲット ENI に NFS（ポート 2049）でマウントします。AgentCore の VPC ドキュメントによれば、マウントに必要なのは runtime ENI とマウントターゲット間の TCP 2049 接続のみ（self-reference のセキュリティグループで許可）で、専用の VPC エンドポイントも NAT も不要です（TLS と IAM 認証は自動で処理されます）。

> **補足:** アウトバウンドの _パブリックインターネット_ 呼び出しを行うカスタムツール（または Browser ツール）を追加する場合は、NAT Gateway を再導入する必要があります。AWS サービスであれば、対応する VPC エンドポイントを追加することで到達できます。

#### セキュリティグループの構成

CDK スタックは AgentCore Runtime 用のセキュリティグループを自動作成します。同じセキュリティグループは通常 VPC エンドポイントにも適用されます。runtime からエンドポイントへ到達できるように、自己参照のインバウンドルールを追加する必要があります。

- Protocol: TCP, Port: 443, Source: セキュリティグループ自身

### OpenAI モデル（GPT-5.x / bedrock-mantle 経由）

ピッカーに表示される OpenAI モデル（GPT-5.4 / GPT-5.5）は `bedrock-mantle` エンドポイント（OpenAI Responses API）経由で提供され、2026-06 から us-east-1 でも利用可能になりました。CDK 管理の VPC は同リージョンの `bedrock-mantle` interface endpoint を常設します（上のエンドポイント表を参照）。追加スタック・フラグ・クロスリージョンネットワークは不要で、素の `cdk deploy` だけで GPT モデルも使えます（旧 `OPENAI_MANTLE` の us-east-2 ピアリングスタックは撤去済み）。

### 複数環境のデプロイ（同一アカウント・同一リージョン）

第 2 の環境（本番と並存する開発スタックなど）にコード変更は不要です。config ファイルを別途作成し、`CONFIG_FILE` 環境変数で選択します。

```yaml
# infra-cdk/config.dev.yaml（gitignore 対象 — 実在の管理者メールを含むため）
stack_name_base: FAST-dev # 必ず変える: CloudFormation エクスポートや Cognito ドメインがここから導出される
admin_user_email: you@example.com
backend:
  pattern: strands-single-agent
  deployment_type: docker
  network_mode: VPC
  vpc_management: CDK
  vpc_cidr: 10.30.0.0/16 # 他環境と重複させない（本番は 10.20.0.0/16）
  use_long_term_memory: true
  skills:
    enabled: true
    mount_path: /mnt/skills
```

```bash
cd infra-cdk
# 全機能（VPC + skills + LTM + OpenAI モデル）を一括デプロイ:
CONFIG_FILE=config.dev.yaml npx cdk deploy --all --require-approval never
cd ..
python scripts/deploy-frontend.py FAST-dev # 指定したスタックの Outputs からフロントを構成
```

並存のルール:

- `stack_name_base` は環境ごとに一意にすること — すべての名前付きリソースと CloudFormation エクスポートがここから導出されます。
- `vpc_cidr` は他環境と重複させないこと: ネットワークの区別が運用上明確に保たれます（将来ピアリングが必要になった場合にも張れる状態を保てます）。
- `CONFIG_FILE` なしの素の `cdk deploy` は常に本番の `config.yaml` を対象とします — 切り替えは環境変数のみなので、「ファイルの戻し忘れ」が構造的に発生しません。
- **別の AWS アカウント**にデプロイする場合は `backend.availability_zones` も設定してください: AgentCore Runtime の VPC モードはリージョンごとに特定の AZ _ID_（us-east-1 では use1-az1/az2/az4）しかサポートせず、AZ 名 → ID のマッピングはアカウント固有です。`aws ec2 describe-availability-zones` で正しい AZ 名を導出してください。

## デプロイ手順

### TL;DR 版

バックエンドおよびフロントエンドをデプロイするコマンドは以下のとおりです。

```bash
cd infra-cdk
npm install
cdk bootstrap # 一度だけ実行
cdk deploy
cd ..
python scripts/deploy-frontend.py
```

### ローカルツールなしでデプロイ（CodeBuild 経由）

ローカルに Node.js、Docker、または CDK がインストールされていない場合でも、一時的な CodeBuild プロジェクトを利用して完全にクラウド上でデプロイできます。必要なのは Python 3.8+ と AWS CLI のみです。

```bash
python scripts/deploy-with-codebuild.py
```

詳細および必要な IAM 権限については `scripts/README.md` を参照してください。

### 1. 依存関係のインストール

インフラストラクチャの依存関係をインストールします。

```bash
cd infra-cdk
npm install
```

**補足**: フロントエンドの依存関係はデプロイ時に Docker bundling 経由で自動インストールされるため、別途フロントエンドの `npm install` を実行する必要はありません。

### 2. CDK のブートストラップ（初回のみ）

この AWS アカウント／リージョンで初めて CDK を使用する場合は以下を実行します。

```bash
cdk bootstrap
```

### 3. CDK でバックエンドをデプロイ

スタック全体をビルドおよびデプロイします。

```bash
cdk deploy
```

デプロイでは以下が実行されます。

1. 認証用の Cognito User Pool を作成
1. agent コンテナをビルドして ECR にプッシュ
1. AgentCore runtime を作成
1. フロントエンド用の CloudFront ディストリビューションを構成

**補足**: コンテナビルドおよび AgentCore のセットアップにより、デプロイには約 5〜10 分かかります。

**デプロイ前にスキル関連スクリプトを手動実行する必要はありません。** `skills/agent-toolkit-for-aws/` 配下のベンダリング済み AWS スキルはリポジトリにコミットされており、`fast-project-guide` スキルは synth 時に `cdk` が自動再生成します（skills-storage スタックの bundling 経由で `scripts/build-project-guide.py` を実行）。`scripts/vendor-skills.py` は上流の新しいコミットからベンダリング済みスキルを更新したいときだけ実行するメンテ用スクリプトで、デプロイ手順ではありません。`cdk deploy` だけで完結します。

### 4. フロントエンドをデプロイ

```bash
# ルートディレクトリから実行
python scripts/deploy-frontend.py
```

このスクリプトは自動的に以下を行います。

- CDK スタックの出力から最新の `aws-exports.json` を生成（`aws-exports.json` の詳細は後述）
- 必要に応じて npm 依存関係をインストール／更新
- フロントエンドをビルド
- AWS Amplify Hosting にデプロイ

スクリプトの出力にアプリケーションの URL が表示されます。次のような形式になります。

```
ℹ App URL: https://main.d123abc456def7.amplifyapp.com
```

### 5. Cognito ユーザーの作成（必要な場合）

**`admin_user_email` を config に指定した場合:**

- 仮パスワードがメールで届きます
- サインインして初回ログイン時にパスワードを変更します

**メールを指定しなかった場合:**

1. [AWS Cognito Console](https://console.aws.amazon.com/cognito/) を開きます
2. 該当する User Pool（名前は `{stack_name_base}-user-pool`）を見つけます
3. User Pool をクリックします
4. "Users" タブに移動します
5. "Create user" をクリックします
6. ユーザー詳細を入力します:
   - **Email**: メールアドレス
   - **Temporary password**: 仮パスワードを作成
   - **Mark email as verified**: このボックスにチェック
7. "Create user" をクリックします

### 6. アプリケーションへのアクセス

1. Amplify Hosting の URL をブラウザで開きます
1. 作成した Cognito ユーザーでサインインします
1. 初回ログイン時に仮パスワードの変更を求められます

## デプロイ後

### アプリケーションの更新

フロントエンドコードを更新する場合:

```bash
# ルートディレクトリから実行
python scripts/deploy-frontend.py
```

バックエンドエージェントを更新する場合:

**Docker デプロイ:**

```bash
cd infra-cdk
cdk deploy --all
```

### モニタリングとログ

- **フロントエンドログ**: CloudFront アクセスログを確認
- **バックエンドログ**: AgentCore runtime の CloudWatch logs を確認
- **ビルドログ**: コンテナビルドの CodeBuild プロジェクトログを確認

## クリーンアップ

すべてのリソースを削除するには以下を実行します。

```bash
cd infra-cdk
cdk destroy --force
```

**警告**: これによりデプロイ中に作成された S3 buckets および ECR images を含むすべてのデータが削除されます。

## トラブルシューティング

### よくある問題

1. **`cdk deploy` が Docker エラーで失敗する**
   - Docker がインストールされ、デーモンが動作していることを確認: `docker ps`
   - Mac では Docker Desktop を開くか Finch を起動: `finch vm start`
   - Linux では: `sudo systemctl start docker`

2. **Docker ビルド中に "Architecture incompatible" や "exec format error" が出る**
   - クロスプラットフォームビルドの設定なしに non-ARM マシンからデプロイした場合に発生します
   - 前提条件セクションの "Docker Cross-Platform Build Setup" の手順に従ってください
   - QEMU エミュレーションをインストール済みであることを確認: `docker run --privileged --rm tonistiigi/binfmt --install all`
   - ARM64 サポートを確認: `docker buildx ls` でプラットフォームに `linux/arm64` が表示されるはずです

3. **"Agent Runtime ARN not configured"**
   - バックエンドスタックが正常にデプロイされたことを確認してください
   - SSM パラメータが正しく作成されているか確認してください

4. **認証エラー**
   - Cognito ユーザーを作成したことを確認
   - ユーザーのメールアドレスが verified になっていることを確認

5. **ビルド失敗**
   - AWS Console で CodeBuild ログを確認
   - `agent/` 内の agent コードが正しいことを確認

6. **権限エラー**
   - AWS 認証情報に十分な権限があることを確認
   - スタックが作成した IAM ロールを確認

### サポートを得る

- 詳細なエラーメッセージは CloudWatch logs を確認してください
- CDK デプロイ出力に警告がないか確認してください
- すべての前提条件が満たされていることを確認してください

## セキュリティ上の考慮事項

- Cognito User Pool は強力なパスワードポリシーで構成されています
- すべての通信は CloudFront 経由で HTTPS を使用します
- AgentCore runtime は JWT 認証を使用します
- IAM ロールは最小権限の原則に従います

本番環境のデプロイでは、以下の検討をお勧めします。

- Cognito ユーザーで MFA を有効にする
- 独自証明書を用いたカスタムドメインを設定する
- 追加のモニタリングとアラートを構成する
- 永続データに対するバックアップ戦略を実装する

## Docker クロスプラットフォームビルド設定（non-ARM マシンで必要）

**重要**: BedrockAgentCore Runtime は ARM64 アーキテクチャのみをサポートしています。non-ARM マシン（x86_64/amd64）からデプロイする場合は、Docker のクロスプラットフォームビルド機能を有効にする必要があります。

マシンのアーキテクチャを確認します。

```bash
uname -m
```

出力が `x86_64`（`aarch64` や `arm64` ではない）の場合は、以下のコマンドを実行してください。

1. **ARM64 エミュレーション用に QEMU をインストール:**

   ```bash
   docker run --privileged --rm tonistiigi/binfmt --install all
   ```

2. **Docker buildx を有効化し、マルチプラットフォームビルダーを作成:**

   ```bash
   docker buildx create --use --name multiarch --driver docker-container
   docker buildx inspect --bootstrap
   ```

3. **ARM64 サポートが利用可能であることを確認:**
   ```bash
   docker buildx ls
   ```
   プラットフォーム一覧に `linux/arm64` が表示されるはずです。

**補足**: この設定はマシンごとに 1 度だけ必要です。CDK デプロイは ARM64 コンテナをビルドするためにこれらの機能を自動的に使用します。

## aws-exports.json の理解

`aws-exports.json` ファイルは、React フロントエンドが AWS Cognito と通信して認証を行うための重要な構成ファイルです。このファイルはフロントエンドデプロイ時に自動生成され、Cognito 認証に必要な構成パラメータを含みます。

**aws-exports.json とは何か?**

`aws-exports.json` ファイルは、React アプリケーションが Cognito Authentication を適切に構成するために読み込む認証構成を含みます。デプロイスクリプトによって自動的に作成され、`frontend/public/aws-exports.json` に配置されます。

**なぜ必要か?**

この構成ファイルは以下の理由で不可欠です。

- React アプリケーションに正しい Cognito User Pool ID および Client ID を提供する
- 認証エンドポイントとリダイレクト URI を指定する
- 認証フローのパラメータを構成する
- このファイルがないと Cognito 認証は機能しない

**どのように作成されるか?**

このファイルは `deploy-frontend.py` によって自動生成され、以下の処理を行います。

1. デプロイ済み CDK スタックの出力から構成を抽出
2. CloudFormation スタック ARN から AWS リージョンを自動検出
3. 必要な値（`CognitoClientId`、`CognitoUserPoolId`、`AmplifyUrl`）を取得
4. 以下の構造で構成ファイルを生成:

```json
{
  "authority": "https://cognito-idp.region.amazonaws.com/user-pool-id",
  "client_id": "your-client-id",
  "redirect_uri": "https://your-amplify-url",
  "post_logout_redirect_uri": "https://your-amplify-url",
  "response_type": "code",
  "scope": "email openid profile",
  "automaticSilentRenew": true
}
```

**重要**: このファイルはデプロイのたびに再生成されるため、手動で編集しないでください。認証が機能しない場合は、フロントエンドを再デプロイして最新の構成を取得してください。

## Design notes

本派生プロジェクトに固有の設計判断 (upstream にない補足):

- **完全閉域 (NAT なし)**: AWS API への外向きトラフィックは全て interface VPC endpoint 経由。NAT gateway は意図的に未配置で、NAT の固定費を削減しつつ egress を明示的に許可した endpoint だけに制限する。
- **AgentCore Gateway VPC endpoint は必須**: Runtime が VPC 内から Gateway を呼ぶ場合、`bedrock-agentcore` interface endpoint がないとツール呼び出しがネットワーク層で失敗する。
- **endpoint 集合はキュレーション**: 各 interface endpoint は VPC 内からの実呼び出しコードに紐付けて配置している。使用しなくなった endpoint (例: 直接 Bedrock Agent 呼び出しを廃止した後の `bedrock-agent-runtime`) は削除しコストと攻撃面を抑える。
- **VPC CIDR と AZ をパラメータ化**: `config.yaml` で `vpc_cidr` と AZ リストを公開し、同一アカウントに複数環境を共存させたり、必要なサービス非対応の AZ を回避したりできる。
- **Cold-start 緩和**: AgentCore Runtime のコンテナ寿命を長く取り、フロントエンドから pre-warm ping を送る構成にしている。別途 warmer Lambda を運用せずに初トークン遅延を抑える狙い。
