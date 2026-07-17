# Skills

このデプロイでは、キュレーション済みの「スキル」(自己記述型の機能バンドル) を S3 Files 経由で Runtime にマウントしています。モデルの system prompt を肥大化させずにドメイン知識を持たせるためのしくみです。

## スキルのマウント方法

- スキルは `skills/agent-toolkit-for-aws/` (ピン留めされた第三者コンテンツ。LICENSE/NOTICE 付きでそのまま保持) と `skills/aws-specialist-agent/` (本プロジェクト独自スキル) の配下に置く。
- ビルドステップ (`scripts/build-project-guide.py`) がプロジェクト固有のガイドを組み立てて全体を S3 バケットにアップロードする。
- AgentCore Runtime コンテナは S3 Files でこのバケットを既知のパスにマウントする。エージェントは必要な機能の `SKILL.md` を遅延読み込みする。

## Design notes

- **イメージ同梱ではなく S3 Files**: スキルは Runtime イメージより更新頻度が高い。S3 Files でマウントすることで、Runtime を再デプロイせずにスキル更新を反映できる。
- **submodule ではなく vendored**: 第三者スキルは当初 git submodule で取り込んでいたが、コミットハッシュをピン留めしたコピー (LICENSE 同梱) に切り替えた。サプライチェーンの予期せぬ変化を避け、submodule init 無しでもビルドできる利点がある。
- **マウントパス vs. VPC のトレードオフ**: S3 Files は Runtime が S3 に到達できる必要がある。閉域構成では S3 (Gateway) endpoint の追加コストを払う必要があるが、スキルを別経路で更新できる柔軟性とのトレードオフで S3 Files を選択した。
- **S3 Files 用 IAM ロールを厳格化**: Runtime がスキルを取得する execution role はスキル用 bucket/prefix に限定し、AgentCore 側の IAM 要件をデプロイ時に検証する。
- **自己説明型プロジェクトガイド**: `skills/aws-specialist-agent/fast-project-guide/` はライブのソースツリーから (サニタイズしたうえで) 生成する。デプロイ済みエージェントが「このアプリはどう組み立てられているか」という質問に、ドキュメントではなく実コードに基づいて答えられる。
- **公式 Code Interpreter**: 独自サンドボックスを再実装せず AgentCore 提供の Code Interpreter ツールを直接使う。フレームワーク固有のラッパ (`tools/code_interpreter/`) で選択したエージェント SDK に橋渡しする。
- **補助 Lambda も閉域 VPC に接続**: 補助 Lambda (feedback / history / session / oauth2-provider / zip-packager / pre-token) はすべて Runtime と同じ VPC で動かし、トラフィックをプライベート経路に閉じる。各 SG は必要な endpoint だけを許可する。
