# AgentCore Code Interpreter 統合

このドキュメントでは、Amazon Bedrock AgentCore Code Interpreter を FAST に統合するためのアーキテクチャ上の決定について説明します。

## AgentCore Code Interpreter とは?

Amazon Bedrock AgentCore Code Interpreter は、AI エージェントが分離されたサンドボックス環境でコードを安全に実行できるようにするフルマネージド機能です。主な機能:

- コンテナ化された環境での安全なコード実行
- 複数言語のサポート (Python、JavaScript、TypeScript)
- 一般的なライブラリを含む事前構築済みランタイム
- 状態の永続化を伴うセッション管理
- 長い実行時間 (デフォルト 15 分、最大 8 時間)

**ドキュメント**: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-tool.html

## なぜ Gateway ではなく直接統合なのか?

FAST は Code Interpreter を Gateway 経由ではなく **エージェントに直接統合** しています。理由は以下の通りです:

### アプローチ 1: 直接統合 ✅ (採用)

**アーキテクチャ**: `Agent → Code Interpreter SDK → Code Interpreter Service`

**長所**:

- **シンプルな実装** - 最小限のコード、追加のインフラ不要
- **低レイテンシ** - Gateway/Lambda のホップなし
- **低コスト** - Lambda 呼び出しなし
- **セッション管理** - Code Interpreter が呼び出し間で状態を維持
- **AWS のパターンに準拠** - 公式ドキュメントの例と一致
- **より良いエラー処理** - Code Interpreter のエラーへの直接アクセス

**短所**:

- Gateway 経由で発見できない
- 更新にはエージェントの再デプロイが必要
- ツールロジックがエージェントコード内に存在する

### アプローチ 2: Gateway 統合 ❌ (不採用)

**アーキテクチャ**: `Agent → Gateway → Lambda → Code Interpreter SDK → Code Interpreter Service`

**長所**:

- Gateway パターンとの一貫性
- MCP 経由で発見可能
- 独立したデプロイ

**短所**:

- **より複雑** - Lambda ラッパー + Gateway ターゲット + IAM ロール
- **より高いレイテンシ** - リクエストパスでの追加のホップ
- **より高いコスト** - Lambda 呼び出し + Code Interpreter 使用料
- **セッションの複雑さ** - Lambda がコールドスタートをまたいでセッションを管理する必要がある
- **AWS 参照なし** - このパターンの公式な例がない
- **意図された使用例ではない** - Code Interpreter は組み込みサービスであり、カスタムツールではない

### 決定の根拠

Code Interpreter は **組み込みの AgentCore サービス** であり、Bedrock モデルや AgentCore Memory に類似しています。AWS は Gateway 経由でプロキシするのではなく、直接統合のために設計しました。Gateway は組み込みサービスではなく、**カスタム Lambda ベースのツール** を対象としています。

**比較**:

| Aspect       | Direct                | Gateway        |
| ------------ | --------------------- | -------------- |
| 複雑さ       | 低                    | 高             |
| レイテンシ   | ~100ms                | ~300-500ms     |
| コスト       | CI のみ               | Lambda + CI    |
| AWS パターン | ✅ ドキュメント化済み | ❌ 例なし      |
| 使用例       | 組み込みサービス      | カスタムツール |

## 実装アーキテクチャ

エージェントは、自前のラッパーではなく公式の Strands Code Interpreter ツール
(`strands_tools.code_interpreter.AgentCoreCodeInterpreter`) を直接使用します。
統合はエージェントのエントリポイントに完結しています:

```
agent/strands-single-agent/
└── basic_agent.py    # AgentCoreCodeInterpreter をインポートしツールを登録
```

### 主要コンポーネント

**エージェント統合** (`agent/strands-single-agent/basic_agent.py`):

```python
from strands_tools.code_interpreter import AgentCoreCodeInterpreter

# サンドボックスを会話セッションに紐づけることで、同一会話内の 2 回目以降の
# 呼び出しは新規にコールドクリエイトせず、同じ AgentCore サンドボックスへ
# 再接続する (warm reconnect)。
code_interpreter_tool = AgentCoreCodeInterpreter(
    region=region, session_name=session_id
)

# Gateway MCP クライアントと file_read と並べてツールを登録する。
tools = [gateway_client, code_interpreter_tool.code_interpreter, file_read]
```

### 設計原則

1. **保守されているツールを使う** - 公式の `strands_tools` Code Interpreter が
   AgentCore Code Interpreter API に追従するため、同期を取り続ける自前ラッパーは不要。
2. **セッションに紐づくサンドボックス** - `session_name` に会話の `session_id` を
   設定するため、ツールのモジュールレベルキャッシュが呼び出しをまたいで同じ
   サンドボックスへ再接続する (warm reconnect)。VPC コールドスタート緩和と相乗する。
3. **直接統合** - 上の意思決定のとおり、Gateway/Lambda を挟まず Code Interpreter
   サービスへ直接アクセスする。

## このアーキテクチャの利点

1. **保守コストの低減**: 自前ラッパーが無く、API 表面は公式ツールが担う。
2. **パフォーマンス**: 直接統合 = 低レイテンシ。warm reconnect により会話内の
   繰り返しコールドスタートを避ける。
3. **コスト**: Lambda のオーバーヘッドなし。
4. **シンプルさ**: AWS のドキュメント化されたパターンに従う。

## 使い方

エージェントは、ユーザーがコード実行を要求したときに自動的に Code Interpreter を使用します:

**プロンプトの例**:

- "Calculate the factorial of 20"
- "Create a list of the first 50 Fibonacci numbers"
- "Generate 100 random numbers and calculate statistics"

このツールは `execute_python_securely` として登録され、組み込みの Python 実行と比較してセキュリティを強調しています。

## セッション管理

- **自動**: Code Interpreter が初回使用時にセッションを作成する
- **永続化**: セッションは複数の呼び出しをまたいで状態を維持する (`clearContext=False`)
- **クリーンアップ**: AgentCore はタイムアウト後に非アクティブなセッションを自動的にクリーンアップする
- **手動クリーンアップ**: 即時のリソース解放のために `cleanup()` メソッドでオプションで実行可能

## テスト

**ローカル Docker ビルド**:

```bash
docker build -f agent/strands-single-agent/Dockerfile -t test-agent .
docker run --rm test-agent python -c "from strands_tools.code_interpreter import AgentCoreCodeInterpreter; print('✓ Import successful')"
```

**デプロイ**:

```bash
cd infra-cdk
cdk deploy
```

**フロントエンドテスト**: 機能を検証するために、コード実行を必要とするプロンプトを使用します。

## 将来の拡張

潜在的な改善:

- ファイル操作のための `write_files` ツールを追加
- サンドボックスの内容を確認するための `list_files` ツールを追加
- JavaScript/TypeScript の実行をサポート
- S3 からのファイルアップロードを追加
- カスタムタイムアウト設定を実装

## 参考資料

- [AgentCore Code Interpreter Documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-tool.html)
- [AWS IDP Reference Implementation](https://github.com/aws-solutions-library-samples/accelerated-intelligent-document-processing-on-aws)
- [FAST Gateway Documentation](./GATEWAY.md)
