# AgentCore AWS Specialist Agent

> For the English README, see [README.md](README.md).

Amazon Bedrock AgentCore 上に構築した AgentCore AWS Specialist Agent のデモです。セキュアでウェブからアクセスできるチャットアプリケーションで、エージェントは AWS について推論し、AWS API やマネージドツールを呼び出し、ウェブを検索し、セッションをまたいで事実を記憶できます。

![AgentCore AWS Specialist Agent — chat UI](docs/img/demo.webp)

> **このリポジトリは派生物 (derivative work) です。** AWS Labs の [`awslabs/fullstack-solution-template-for-agentcore`](https://github.com/awslabs/fullstack-solution-template-for-agentcore) (FAST, Apache-2.0) をベースに、選択可能な Bedrock / OpenAI モデル、長期記憶、ウェブ検索、チャット履歴サイドバー、完全閉域 (NAT なし) VPC といった機能を追加し、AgentCore AWS Specialist Agent のデモへとカスタマイズしたものです。主要な派生固有の設計判断の背景は、`docs/*.md` の「Design notes」セクションを参照してください。以下の説明の多くは、本プロジェクトが土台とする FAST のベースラインを説明しているため、「FAST」は文中を通してそのベースラインを指します。

Fullstack AgentCore Solution Template (FAST) は、ユーザー (データサイエンティストやエンジニア) が、セキュアでウェブからアクセスできる React フロントエンドを AgentCore バックエンドに接続した状態で素早くデプロイできるようにするスターターリポジトリです。その目的は、インフラ構築という差別化につながらない重労働を肩代わりすることで、AgentCore 上のフルスタックアプリ構築を数週間から数日へと加速し、その上での vibe-coding スタイルの開発を可能にすることにあります。FAST の唯一の中心的な依存は AgentCore です。エージェント SDK (Strands、LangGraph など) にもコーディングアシスタントのプラットフォーム (Q、Kiro、Cline、Claude Code など) にも依存しません。

FAST はセキュリティと vibe-codability を主要なテーマとして設計されています。ベストプラクティスや専門家の知見は、_コード_ ではなくこのリポジトリの _ドキュメント_ に体系化されています。このドキュメントを AI コーディングアシスタントのコンテキストに含めたり、ドキュメント内のベストプラクティスやコードスニペットを活用するようアシスタントに指示したりすることで、データサイエンティストや開発者は、あらゆるユースケースの AgentCore アプリを素早く vibe-build できます。AI コーディングアシスタントを使ってフロントエンドやインフラを完全にカスタマイズできるため、サイエンティストは自身の知識が最も活きる領域、すなわち実際のプロンプトエンジニアリングと GenAI 実装の細部に集中できます。

FAST を出発点かつ開発フレームワークとして使うことで、データサイエンティストやエンジニアは開発プロセスを加速し、フロントエンドやインフラのコードを学ぶことなく、アーキテクチャとセキュリティのベストプラクティスに沿った本番品質の AgentCore コードを提供できます。

## AgentCore AWS Specialist Agent の機能

AgentCore AWS Specialist Agent は、完全に機能するフルスタックアプリケーションとして、すぐにデプロイできる状態で提供されます。FAST ベースラインの上に、AWS の専門アシスタントとしての機能 (各種ツール・長期記憶・モデル選択) を追加したマルチターンチャットエージェントです。これは出発点であり、あなたのユースケースに合わせて自由にカスタマイズできます。すぐに使える機能は次のとおりです。

1. **AgentCore Gateway 経由の MCP ツール** (Cedar ABAC により department 単位で認可):
   - **aws-mcp** - AWS 操作全般。AWS CLI コマンド実行 (`call_aws`)、boto3 スクリプト実行 (`run_script`)、AWS ドキュメントの検索・取得 (`search_documentation` / `read_documentation` / `recommend`)、リージョン情報 (`get_regional_availability` / `list_regions`)、スキル取得 (`retrieve_skill`)
   - **web-search-tool** - Amazon マネージドのリアルタイムウェブ検索 (`WebSearch`)。学習データのカットオフより新しい情報を取得し、出典付きで回答を裏取り
   - **strands-mcp** - Strands Agents SDK ドキュメントの検索・取得 (`search_docs` / `fetch_doc`)
   - **ltm-mcp** - 長期メモリ (過去セッション情報) の一覧取得 (`list_long_term_memories`)
   - **sample-tool-target** - テキスト解析サンプルツール (`text_analysis_tool`。単語数と文字の出現頻度をカウント)
2. **ローカルツール** (Runtime 組込み・全ユーザー利用可):
   - **Code Interpreter** - Amazon Bedrock AgentCore Code Interpreter との直接統合。隔離サンドボックスでのセキュアなコード実行 (Python / JS / TS)、状態を永続化するセッション管理
   - **skills** / **file_read** - S3 Files から `/mnt/skills` にマウントされた AWS スキル (aws-cdk・aws-iam・amazon-bedrock など) の読み込み・アクティベート
3. **長期記憶 (AgentCore Memory)** - SemanticMemoryStrategy が会話から事実を抽出し、次セッション以降に関連する事実を自動で注入 (Cognito ユーザー単位)
4. **モデル選択** - UI のモデルセレクタでチャットごとに切替え。Bedrock 上の Claude (Fable 5 / Opus / Sonnet / Haiku) と Bedrock 経由の OpenAI GPT

エージェントに「Lambda は us-east-2 で使える?」「CDK で VPC を作る方法をスキルに従って教えて」「最近発表された Bedrock AgentCore の新機能を、最新情報を検索して出典付きで教えて」などと尋ねて、これらのツールが動作する様子を確かめてみてください。

## AgentCore AWS Specialist Agent ユーザーセットアップ

AgentCore AWS Specialist Agent を使ってフルスタックアプリケーションを構築・カスタマイズしたいデータサイエンティストやエンジニアの方は、このセクションが該当します。

本プロジェクトは、フォークして、セキュリティ承認済みのベースラインが動作する状態のまま、すぐにデプロイできるよう設計されています。あなたの仕事は、それをカスタマイズして、AgentCore 上で (文字どおり) 何でも行う独自のフルスタックアプリケーションを作ることです。

すぐに使えるフルスタックアプリケーションをデプロイするのは、リポジトリをフォークしたあとは数個の cdk コマンドだけです。すなわち:

```bash
cd infra-cdk
npm install
cdk bootstrap # 初回のみ
cdk deploy
cd ..
python scripts/deploy-frontend.py
```

本アプリケーションを AWS アカウントへデプロイする詳細な手順は、[デプロイガイド](docs/DEPLOYMENT.md) を参照してください。

次に何をするか。それは開発者であるあなた次第です。要件を念頭に置きながら、コーディングアシスタントを開き、やりたいことを記述して、始めましょう。このリポジトリのステアリングドキュメントは、コーディングアシスタントをベストプラクティスへと導き、素晴らしいものを作れるよう、常にリポジトリに組み込まれたドキュメントを参照するよう促します。

## アーキテクチャ

![Architecture Diagram](docs/architecture-diagram/aws-specialist-agent-architecture.png)

上の図は本プロジェクトのアーキテクチャを示しています。FAST ベースラインの上に、選択可能な Bedrock / OpenAI モデル、長期記憶、ウェブ検索、チャット履歴サイドバー、完全閉域 (NAT なし) VPC を追加しています。

**認証フロー:**

1. フロントエンドへのユーザーログイン (Cognito User Pool — Authorization Code grant): ユーザーは AWS Amplify でホストされたウェブアプリケーションを通じて Cognito で認証します。Cognito はセッション用の JWT アクセストークンを発行します。
2. フロントエンドから AgentCore Runtime へ (Cognito User Pool JWT 検証): フロントエンドはユーザーの JWT を Authorization ヘッダーに乗せて渡します。Runtime はそのトークンを Cognito User Pool に対して検証します。
3. AgentCore Runtime から AgentCore Gateway へ (OAuth2 Client Credentials / M2M): Runtime は OAuth2 Client Credentials grant を使って認証し、Cognito V3 Pre-Token Lambda を介してユーザー ID を M2M トークンに伝播させます。Gateway はユーザーのクレームに対して Cedar ポリシーを評価し、きめ細かなアクセス制御 (finance / engineering / guest ロール) を強制します。
4. フロントエンドから API Gateway へ (Cognito User Pool JWT 検証): API リクエスト (チャット履歴、フィードバック) は、フロー 1 と同じユーザー JWT を使い Cognito User Pools Authorizer で認証されます。

**ツールと機能** (AgentCore Gateway と Runtime を通じて公開され、Cedar ポリシーでゲートされます):

- AWS API アクセスのための **AWS MCP Server** (リモート MCP ターゲット)、および **text-analyzer Lambda** ターゲット
- **Web Search** (マネージドコネクタ)、および **Strands docs** / **long-term-memory listing** MCP server (それぞれ独自の AgentCore Runtime 上でホスト)
- **Code Interpreter**、**AgentCore Memory** (長期記憶)、および S3 Files から `/mnt/skills` にマウントされる **skills**

**閉域ネットワーク**: Runtime と補助 Lambda (チャット履歴、フィードバック、pre-token) は隔離された VPC 内で動作し、すべての AWS サービスへインターフェース / ゲートウェイ VPC エンドポイント経由で到達します。NAT ゲートウェイはありません。背景は `docs/*.md` の「Design notes」セクションを参照してください。

### 技術スタック

- **フロントエンド**: React + TypeScript、Vite、Tailwind CSS、shadcn コンポーネント。無限に柔軟で、コーディングアシスタントとの相性も抜群
- **モデル**: 単一のレジストリからチャットごとに選択可能 — Bedrock 上の Claude (Fable 5 / Opus / Sonnet / Haiku) と、Bedrock 経由で提供される OpenAI GPT
- **エージェント**: AgentCore Runtime (VPC モード) 内で動作する Strands エージェント
- **認証**: OAuth サポート付きの AWS Cognito User Pool。Cognito の差し替えも容易
- **インフラ**: CDK デプロイ (TypeScript)。フロントエンドは Amplify Hosting、バックエンドは AgentCore

## プロジェクト構成

```
aws-specialist-agent/
├── docker/                 # Docker 開発環境
│   ├── docker-compose.yml  # ローカル開発スタック
│   └── Dockerfile.frontend.dev # フロントエンド開発用コンテナ
├── frontend/               # React フロントエンドアプリケーション
│   ├── src/
│   │   ├── components/     # React コンポーネント (shadcn/ui)、チャット UI を含む
│   │   ├── hooks/          # カスタム React フック
│   │   ├── lib/            # ユーティリティライブラリ
│   │   │   └── agentcore-client/ # AgentCore ストリーミングクライアント
│   │   ├── test/           # フロントエンドテスト
│   │   └── types/          # TypeScript 型定義
│   ├── public/             # 静的アセット
│   ├── components.json     # shadcn/ui 設定
│   ├── vite.config.ts      # Vite 設定
│   └── package.json
├── infra-cdk/              # CDK インフラコード
│   ├── lib/                # CDK スタック定義
│   │   ├── utils/          # 共有 CDK ユーティリティ (モデルレジストリを含む)
│   │   ├── amplify-hosting-stack.ts
│   │   ├── backend-stack.ts
│   │   ├── cognito-stack.ts
│   │   ├── skills-storage-stack.ts # S3 Files skills バケット + マウント
│   │   ├── vpc-stack.ts    # 閉域 VPC + interface/gateway エンドポイント
│   │   └── fast-main-stack.ts
│   ├── bin/                # CDK アプリのエントリポイント
│   ├── lambdas/            # Lambda 関数コード
│   │   ├── cedar-policy/    # Cedar Policy Engine ライフサイクル
│   │   ├── oauth2-provider/ # OAuth2 Credential Provider ライフサイクル
│   │   ├── pretoken-v3/     # Cognito V3 Pre-Token Generation Lambda
│   │   ├── feedback/       # フィードバック API ハンドラ
│   │   ├── history/        # チャット履歴 API ハンドラ
│   │   ├── sessions/       # セッションタイトル生成 / 一覧
│   │   └── zip-packager/   # Runtime ZIP パッケージャー
│   └── config.yaml         # デプロイ設定
├── agent/                  # エージェントパターン実装
│   ├── strands-single-agent/ # Strands エージェント (デプロイされるパターン)
│   │   ├── basic_agent.py  # エージェントのエントリポイント、ツール、システムプロンプト
│   │   ├── models.py       # モデルファクトリ (Bedrock + Bedrock 上の OpenAI)
│   │   ├── tools/          # エージェント側のツール配線 (gateway クライアントなど)
│   │   ├── requirements.txt # エージェントの依存関係
│   │   └── Dockerfile      # コンテナ設定
│   └── utils/              # 共有エージェントユーティリティ (auth, ssm)
├── gateway/                # Gateway ツールと Cedar ポリシー
│   ├── policies/           # Cedar ポリシー定義 (1 ファイル 1 ステートメント)
│   │   ├── 01-sample-tool.cedar
│   │   ├── 02-aws-mcp-read.cedar
│   │   ├── 03-aws-mcp-destructive.cedar
│   │   ├── 04-ltm-mcp.cedar
│   │   ├── 05-strands-mcp.cedar
│   │   └── 06-web-search.cedar
│   └── tools/              # Gateway ツール実装
│       ├── sample_tool/    # テキスト解析 Lambda ターゲット
│       ├── ltm_mcp_server/ # 長期記憶一覧 MCP server
│       └── strands_mcp_server/ # Strands docs MCP server
├── skills/                 # S3 Files 経由で Runtime にマウントされる skills
│   ├── agent-toolkit-for-aws/ # ベンダリングした upstream AWS skills (ピン留め)
│   └── aws-specialist-agent/  # 本プロジェクト独自の skills
│       └── fast-project-guide/ # プロジェクト自己解説スキル
├── scripts/                # デプロイ・ユーティリティスクリプト (scripts/README.md 参照)
│   ├── deploy-frontend.py  # クロスプラットフォームのフロントエンドデプロイ
│   ├── deploy-with-codebuild.py # CodeBuild 経由のクラウドデプロイ
│   ├── build-project-guide.py # fast-project-guide skill のビルド
│   ├── vendor-skills.py    # upstream AWS skills の再ベンダリング
│   ├── create-demo-users.py # デモ用 Cognito ユーザー管理
│   └── utils.py            # 共有スクリプトユーティリティ
├── test-scripts/           # エンドツーエンド検証スクリプト
├── tests/                  # ユニット / 統合テストスイート
│   ├── unit/
│   └── conftest.py         # Pytest 設定
├── docs/                   # ドキュメント (英語。docs-jp/ に日本語版)
│   ├── architecture-diagram/ # アーキテクチャ図
│   ├── DEPLOYMENT.md       # デプロイガイド
│   ├── AGENT_CONFIGURATION.md # エージェント + モデル設定
│   ├── MEMORY_INTEGRATION.md # 長期記憶ガイド
│   ├── GATEWAY.md          # Gateway + ツールターゲットガイド
│   ├── CEDAR_POLICY_GUIDE.md # Cedar ポリシー / ABAC リファレンス
│   ├── CONTEXT_MANAGEMENT.md # コンテキストウィンドウ + チャット履歴ガイド
│   ├── SKILLS.md           # skills マウント + 設計ノート
│   └── ...                 # (ほか)
├── docs-jp/                # docs/ の日本語訳
├── vibe-context/           # AI コーディングアシスタント向けコンテキストとルール
│   ├── AGENTS.md           # AI アシスタント向けルール
│   ├── coding-conventions.md # コードスタイルガイドライン
│   └── development-best-practices.md # 開発ガイドライン
├── CHANGELOG.md            # バージョン履歴
├── Makefile                # プロジェクトレベルのビルドコマンド
└── README.md
```

## セキュリティ

注意: このアセットは、含まれるサービスの proof-of-value であり、本番運用可能なソリューションを意図したものではありません。AWS 責任共有モデルが自身のユースケースにどう適用されるかを判断し、望むセキュリティ成果を達成するために必要なコントロールを実装する必要があります。AWS はお客様を支援する幅広いセキュリティツールと設定を提供しています。

最終的に、フルスタックアプリケーションのすべての側面がセキュアであることを保証するのは、その開発者であるあなたの責任です。私たちはリポジトリのドキュメントでセキュリティのベストプラクティスを提供し、セキュアなベースラインを提供しますが、本ツールから構築されたアプリケーションのセキュリティについて Amazon は一切の責任を負いません。

## ライセンス

本プロジェクトは Apache-2.0 License のもとでライセンスされています。
