# コンテキスト管理ガイド

FAST 内における長時間実行型あるいはマルチターン型のエージェント会話で、LLM のコンテキストウィンドウを管理するための実践ガイドです。

エージェントが長い会話を扱うにつれ（特に大量のツール呼び出し、巨大なツール結果、反復ワークフローを伴う場合）、会話履歴はモデルのコンテキストウィンドウを超えるほど膨らんでいきます。オーバーフローに到達する前であっても、巨大なコンテキストはモデル性能の劣化、レイテンシの増加、コストの肥大化を引き起こします。コンテキスト管理戦略は、会話履歴を能動的または受動的に圧縮・トリミング・要約することで、これらの問題に対処します。

このガイドでは Strands と LangGraph で利用可能な組み込みオプション、それぞれの使いどころ、そして組み込みオプションがユースケースに合わない場合に完全カスタムなソリューションを実装する方法について解説します。

---

## どのような場合にコンテキスト管理が必要か

エージェントが以下に該当する場合、コンテキスト管理が必要となる可能性があります。

- 時間とともにメッセージが蓄積する **マルチターン会話** を実行する
- 多数の連続したツール呼び出しを伴う **反復ワークフロー** を実行する（例: コード生成ループ、データ分析パイプライン）
- **大きなツール結果** を返す（例: ファイル内容、API レスポンス、スクリーンショット）
- 人間の介在なしに **長期間自律的に動作する**

シンプルな単発のチャットや短いマルチターンチャットエージェントでは、デフォルトの挙動（明示的なコンテキスト管理なし）で通常は十分です。特に、200k トークン以上の大きなコンテキストウィンドウを持つ最強の LLM を使う場合はそうです。

---

## 戦略の比較

| 戦略                         | 情報損失                                | 複雑性 | 適した用途                                                                                                                               |
| ---------------------------- | --------------------------------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------- |
| **スライディングウィンドウ** | 高 — 古いメッセージは完全に破棄される   | 低     | シンプルなチャットボット、短い会話、古いメッセージを参照しない UX、または古いトピックの参照を扱う長期メモリを内蔵したアプリケーション    |
| **要約**                     | 低 — 重要情報が圧縮された形で保持される | 中     | マルチターンアシスタント、反復ワークフロー、コンテキストウィンドウがオーバーフローした際に数秒間停止しても許容される（要約生成のため）UX |
| **能動的圧縮**               | 低〜中 — オーバーフロー前にトリガー     | 中     | 長時間自律稼働するエージェント、コンテキストウィンドウが 50% を超えると高品質な結果を返すのが難しい弱い LLM を使うアプリケーション       |
| **カスタムフックベース**     | 設定可能 — 何を残すかを完全に制御       | 高     | 外部メモリを伴う特殊な長時間稼働エージェント                                                                                             |

---

## オプション 1: スライディングウィンドウ (Strands)

最もシンプルなアプローチです。直近 N 件のメッセージを保持し、それより古いものを破棄します。Strands は `SlidingWindowConversationManager` を標準で提供しています。

### 仕組み

- 固定ウィンドウサイズでメッセージを保持（デフォルト: 40 件）
- ウィンドウを超えると最も古いメッセージから削除
- 不正な会話状態にならないよう tool use / result のペアを維持
- 大きなツール結果を必要に応じて切り詰める（先頭・末尾 200 文字を保持）

### 設定

```python
from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager

agent = Agent(
    model=model,
    tools=tools,
    conversation_manager=SlidingWindowConversationManager(
        window_size=40,                # 保持する最大メッセージ数（デフォルト: 40）
        should_truncate_results=True,  # 大きなツール結果を切り詰める（デフォルト: True）
    ),
)
```

### 能動的圧縮

スライディングウィンドウマネージャは能動的圧縮もサポートしており、エラーを待たずにコンテキストウィンドウがオーバーフローする前にコンテキスト削減をトリガーできます。

```python
conversation_manager=SlidingWindowConversationManager(
    window_size=40,
    proactive_compression=True,  # コンテキスト使用率 70% で圧縮（デフォルト閾値）
)

# あるいはカスタム閾値で:
conversation_manager=SlidingWindowConversationManager(
    window_size=40,
    proactive_compression={"compression_threshold": 0.5},  # 50% で圧縮
)
```

### ターンごとの管理

ループ内で多くのツール操作を行うエージェント（例: 頻繁なスクリーンショットを伴う Web ブラウジング）の場合、毎モデル呼び出し前に能動的にトリミングするようターンごとの管理を有効化します。

```python
conversation_manager=SlidingWindowConversationManager(
    window_size=40,
    per_turn=True,   # 毎モデル呼び出し前に適用
    # per_turn=5,    # または 5 回ごとに適用
)
```

### FAST Strands パターンへの適用

`agent/strands-single-agent/basic_agent.py` にスライディングウィンドウ管理を追加するには、`Agent` 構築時に `conversation_manager` パラメータを渡します。

```python
from strands.agent.conversation_manager import SlidingWindowConversationManager

# create_strands_agent() 内:
agent = Agent(
    model=model,
    system_prompt=SYSTEM_PROMPT,
    tools=[gateway_client, code_tools.execute_python_securely],
    conversation_manager=SlidingWindowConversationManager(window_size=40),
    session_manager=session_manager,
)
```

### 長所と短所

| 長所                           | 短所                                         |
| ------------------------------ | -------------------------------------------- |
| 追加の LLM 呼び出しがゼロ      | 古いコンテキストが完全に失われる             |
| レイテンシ増加・コスト増加なし | エージェントが過去の会話を「忘れる」         |
| 設定がシンプル                 | 完全な履歴が必要な長時間稼働タスクには不向き |

ドキュメント: **Strands Docs**: [SlidingWindowConversationManager API](https://strandsagents.com/docs/api/python/strands.agent.conversation_manager.sliding_window_conversation_manager/)

---

## オプション 2: 要約型 Conversation Manager (Strands)

古いメッセージを完全に破棄するのではなく、LLM 呼び出しを使って要約します。これによりトークン数を削減しつつ重要情報を保持できます。

### 仕組み

- コンテキストオーバーフロー（または能動的圧縮閾値到達）時に、最古の N% のメッセージを要約
- 要約は単一のユーザーメッセージとして元のメッセージ群を置き換える
- 直近メッセージはそのまま保持
- 別途の LLM 呼び出しで要約を生成

### 設定

```python
from strands import Agent
from strands.agent.conversation_manager import SummarizingConversationManager

agent = Agent(
    model=model,
    tools=tools,
    conversation_manager=SummarizingConversationManager(
        summary_ratio=0.3,               # 最古の 30% のメッセージを要約（デフォルト）
        preserve_recent_messages=10,      # 直近 10 件のメッセージを常に保持（デフォルト）
        proactive_compression=True,       # コンテキスト使用率 70% で圧縮
    ),
)
```

### 専用要約エージェントの利用

デフォルトでは要約マネージャは親エージェント（とそのツール）を使って要約を生成します。より細かく制御したい場合は、要約専用エージェントを指定できます。

```python
from strands import Agent
from strands.agent.conversation_manager import SummarizingConversationManager

# 要約専用の軽量エージェント — ツールは不要
summarizer = Agent(
    model="us.anthropic.claude-sonnet-4-20250514-v1:0",
    system_prompt="You are a conversation summarizer. Create concise bullet-point summaries.",
)

agent = Agent(
    model=model,
    tools=tools,
    conversation_manager=SummarizingConversationManager(
        summary_ratio=0.3,
        preserve_recent_messages=10,
        summarization_agent=summarizer,
        proactive_compression={"compression_threshold": 0.5},
    ),
)
```

### FAST Strands パターンへの適用

```python
from strands.agent.conversation_manager import SummarizingConversationManager

# create_strands_agent() 内:
agent = Agent(
    model=model,
    system_prompt=SYSTEM_PROMPT,
    tools=[gateway_client, code_tools.execute_python_securely],
    conversation_manager=SummarizingConversationManager(
        summary_ratio=0.3,
        preserve_recent_messages=10,
        proactive_compression=True,
    ),
    session_manager=session_manager,
)
```

### 長所と短所

| 長所                             | 短所                                              |
| -------------------------------- | ------------------------------------------------- |
| 古いコンテキストの重要情報を保持 | 追加の LLM 呼び出しによりレイテンシとコストが増加 |
| 圧縮率を設定可能                 | 要約品質はモデル依存                              |
| 組み込み済みでカスタムコード不要 | ニュアンスや具体的な詳細が失われる可能性          |

ドキュメント: **Strands Docs**: [SummarizingConversationManager API](https://strandsagents.com/docs/api/python/strands.agent.conversation_manager.summarizing_conversation_manager/)

---

## オプション 3: LangGraph ミドルウェア (Trim / Summarize)

LangGraph は `@before_model` デコレータを使ったミドルウェアベースのコンテキスト管理アプローチを提供します。

### メッセージのトリミング

毎モデル呼び出し前に古いメッセージを削除し、直近のものだけを残します。

```python
from langchain.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langchain.agents import create_agent, AgentState
from langchain.agents.middleware import before_model
from langgraph.runtime import Runtime
from typing import Any


@before_model
def trim_messages(state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
    """コンテキストウィンドウに収まるよう直近の数メッセージのみ残す。"""
    messages = state["messages"]
    if len(messages) <= 10:
        return None

    first_msg = messages[0]
    recent_messages = messages[-10:]
    return {
        "messages": [
            RemoveMessage(id=REMOVE_ALL_MESSAGES),
            first_msg,
            *recent_messages,
        ]
    }
```

### メッセージの要約

組み込みの `SummarizationMiddleware` を使って自動要約を行います。

```python
from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langgraph.checkpoint.memory import InMemorySaver

agent = create_agent(
    model="us.anthropic.claude-sonnet-4-20250514-v1:0",
    tools=tools,
    middleware=[
        SummarizationMiddleware(
            model="us.anthropic.claude-sonnet-4-20250514-v1:0",
            trigger=("tokens", 4000),   # トークン数が 4000 を超えたらトリガー
            keep=("messages", 20),      # 直近 20 メッセージはそのまま保持
        )
    ],
    checkpointer=InMemorySaver(),
)
```

### 長所と短所

| 長所                                     | 短所                                             |
| ---------------------------------------- | ------------------------------------------------ |
| LangGraph とのネイティブな統合           | LangGraph 固有のパターンが必要                   |
| 他のミドルウェアと組み合わせ可能         | Strands とは API が異なる                        |
| `SummarizationMiddleware` が複雑性を吸収 | トークンカウンティングのオーバーヘッドが少々ある |

ドキュメント: **LangGraph Docs**: [Short-term Memory & Summarization](https://docs.langchain.com/oss/python/langchain/short-term-memory)

---

## オプション 4: カスタムフックベースのコンテキスト管理 (Strands)

高度なユースケース、特に長時間自律稼働エージェントでは、組み込みマネージャでは制御が不足する場合があります。Strands フックを使えば完全カスタムなソリューションを実装できます。

このアプローチは以下の場合に最適です:

- コンテキストウィンドウ使用率の特定の **パーセント** で要約をトリガー（オーバーフロー時のみではない）
- 圧縮後に **外部メモリの再注入**（例: ログファイル、ナレッジベース、構造化された状態）を行いたい
- 要約に **生の Bedrock Converse 呼び出し** を使いたい（エージェントループやツール呼び出しを回避）
- コンテキスト管理アクティビティをフロントエンドに通知するため **カスタムストリームイベント** を発火したい
- 要約失敗時の **フォールバック戦略** を実装したい

### アーキテクチャ

このパターンでは 2 つのコンポーネントを協調させます:

1. **No-op の `ConversationManager`** — Strands の組み込みコンテキスト管理を完全に無効化
2. **`HookProvider`** — `BeforeModelCallEvent` に登録され、毎 LLM 呼び出し前に能動的なコンテキスト管理を実行

### 実装

#### ステップ 1: `strands.agent.conversation_manager.ConversationManager` を継承した No-op Conversation Manager を作成

```python
from strands.agent.conversation_manager import ConversationManager


class NoOpConversationManager(ConversationManager):
    """組み込みコンテキスト管理を無効化する。

    Strands は ConversationManager を要求するが、コンテキスト削減は
    フックで自前処理する。両メソッドは意図的に空とする。
    """

    def apply_management(self, agent, **kwargs):
        """No-op: コンテキスト管理はフックで行う。"""
        pass

    def reduce_context(self, agent, **kwargs):
        """No-op: コンテキスト管理はフックで行う。"""
        pass
```

#### ステップ 2: コンテキストチェックフックを作成

```python
import logging
import os

import boto3
from strands.hooks import HookProvider, HookRegistry, BeforeModelCallEvent

logger = logging.getLogger(__name__)

# ご利用のモデルのコンテキストウィンドウサイズに合わせて設定してください
CONTEXT_WINDOW_TOKENS = 200_000  # 例: Claude Sonnet

SUMMARIZATION_PROMPT = """You are a conversation summarizer. Provide a concise summary.

Format Requirements:
- Create a structured summary in bullet-point format
- Do NOT respond conversationally
- Include: key decisions, tool executions and results, current state, next steps
"""


class ContextCheckHook(HookProvider):
    """コンテキスト使用率が閾値を超えた際に能動的に会話を要約する。

    BeforeModelCallEvent で発火する。コンテキストが threshold_pct を超えると、
    直接 Bedrock Converse 呼び出し（エージェントループ・ツールなし）で
    古いメッセージを要約しつつ、直近のメッセージはそのまま保持する。

    Args:
        threshold_pct: 要約をトリガーするコンテキストウィンドウのパーセント。
        preserve_recent: そのまま保持する直近メッセージの数。
        model_id: 要約呼び出しに使用するモデル ID。
    """

    def __init__(
        self,
        threshold_pct: float = 50.0,
        preserve_recent: int = 6,
        model_id: str = "us.anthropic.claude-sonnet-4-20250514-v1:0",
    ):
        self.threshold_pct = threshold_pct
        self.preserve_recent = preserve_recent
        self._model_id = model_id
        self._bedrock = None

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        """before-model-call チェックを登録する。"""
        registry.add_callback(BeforeModelCallEvent, self._check)

    def _check(self, event: BeforeModelCallEvent) -> None:
        """コンテキスト使用率を確認し、閾値超過時に要約をトリガーする。"""
        agent = event.agent
        # 後ろから走査して、usage メタデータを持つ最新の assistant メッセージを探す
        for msg in reversed(agent.messages):
            if msg.get("role") == "assistant":
                usage = msg.get("metadata", {}).get("usage", {})
                if usage:
                    input_tokens = usage.get("inputTokens", 0)
                    cache_tokens = usage.get("cacheReadInputTokens", 0)
                    pct = (input_tokens + cache_tokens) / CONTEXT_WINDOW_TOKENS * 100
                    if pct >= self.threshold_pct:
                        logger.info("Context at %.1f%% — summarizing", pct)
                        self._summarize_and_replace(agent)
                return  # 最新の assistant メッセージだけをチェック

    def _summarize_and_replace(self, agent) -> None:
        """古いメッセージを要約し、要約で置き換える。"""
        messages = agent.messages
        if len(messages) <= self.preserve_recent:
            return

        # 分割点を探す — tool_use/tool_result のペアを破壊しないように
        split = len(messages) - self.preserve_recent
        while split > 0 and self._is_tool_result(messages[split]):
            split -= 1
        if split <= 0:
            return

        to_summarize = messages[:split]
        to_keep = messages[split:]

        # 要約呼び出し用にテキストのみのフォーマットへ変換
        converse_messages = self._to_text_only(to_summarize)
        converse_messages.append({
            "role": "user",
            "content": [{"text": "Please summarize this conversation."}],
        })

        # Bedrock Converse の直接呼び出し — エージェントループもツールもなし
        if not self._bedrock:
            self._bedrock = boto3.client(
                "bedrock-runtime",
                region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
            )

        try:
            response = self._bedrock.converse(
                modelId=self._model_id,
                system=[{"text": SUMMARIZATION_PROMPT}],
                messages=converse_messages,
                inferenceConfig={"maxTokens": 4096},
            )
            summary_text = response["output"]["message"]["content"][0]["text"]
        except Exception as e:
            logger.error("Summarization failed: %s — falling back to truncation", e)
            summary_text = "(conversation history truncated due to context limits)"

        # エージェントのメッセージを in-place で置き換える
        agent.messages[:] = [
            {"role": "user", "content": [{"text": f"## Previous Conversation Summary\n\n{summary_text}"}]},
            {"role": "assistant", "content": [{"text": "Understood. I'll continue from where we left off."}]},
        ] + to_keep

    def _is_tool_result(self, msg: dict) -> bool:
        """メッセージに toolResult ブロックが含まれるかチェックする。"""
        content = msg.get("content", [])
        if isinstance(content, list):
            return any(isinstance(b, dict) and "toolResult" in b for b in content)
        return False

    def _to_text_only(self, messages: list[dict]) -> list[dict]:
        """メッセージをテキストのみのフォーマットに変換する（toolUse/toolResult ブロックを除去）。

        Bedrock Converse API では、対応するツール定義をリクエストに含めずに
        toolUse ブロックを送ることはできないため、この変換が必要となる。
        """
        result = []
        for msg in messages:
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            content = msg.get("content", [])
            if isinstance(content, str):
                result.append({"role": role, "content": [{"text": content}]})
                continue
            text_blocks = []
            for block in content:
                if isinstance(block, dict):
                    if "text" in block:
                        text_blocks.append({"text": block["text"]})
                    elif "toolUse" in block:
                        name = block["toolUse"].get("name", "unknown")
                        text_blocks.append({"text": f"[Called tool: {name}]"})
                    elif "toolResult" in block:
                        tr_content = block["toolResult"].get("content", [])
                        snippet = next(
                            (c["text"][:200] for c in tr_content if isinstance(c, dict) and "text" in c),
                            "result received",
                        )
                        text_blocks.append({"text": f"[Tool result: {snippet}]"})
            if text_blocks:
                result.append({"role": role, "content": text_blocks})

        # user / assistant が交互になるよう保証する（Bedrock の要件）
        cleaned = []
        for msg in result:
            if cleaned and cleaned[-1]["role"] == msg["role"]:
                cleaned[-1]["content"].extend(msg["content"])
            else:
                cleaned.append(msg)
        if cleaned and cleaned[0]["role"] == "assistant":
            cleaned.insert(0, {"role": "user", "content": [{"text": "(start of conversation)"}]})

        return cleaned
```

#### ステップ 3: エージェントに組み込む

```python
from strands import Agent

agent = Agent(
    model=model,
    system_prompt=SYSTEM_PROMPT,
    tools=tools,
    hooks=[ContextCheckHook(threshold_pct=50.0, preserve_recent=6)],
    conversation_manager=NoOpConversationManager(),
    session_manager=session_manager,
)
```

### 高度な使い方: 要約後の外部メモリ再注入

会話外に構造化された状態を持つ長時間稼働エージェント（例: 進捗ログ、結果ファイル、ナレッジベース）では、要約後にその状態を再注入することで、圧縮で失われた可能性のある重要なコンテキストを復元できます。

```python
def _summarize_and_replace(self, agent) -> None:
    # ... （上と同じ要約処理）...

    # メッセージ置き換え後、外部状態を再注入する
    self._inject_external_state(agent)

def _inject_external_state(self, agent) -> None:
    """コンテキスト圧縮後、構造化された外部状態を再注入する。"""
    state_path = os.environ.get("AGENT_STATE_FILE", "")
    if not state_path:
        return
    try:
        with open(state_path) as f:
            state_content = f.read()
    except FileNotFoundError:
        return

    agent.messages.append({
        "role": "user",
        "content": [{"text": (
            "Context was compressed. Here is the current state log — "
            "use it to recall what has been done and the current status:\n\n"
            + state_content
        )}],
    })
```

このパターンは以下のエージェントで特に強力です:

- 実行したアクションと観測された結果のログを保持しているエージェント
- 圧縮を跨いで構造化データ（テーブル、設定）を保持する必要があるエージェント
- 試行履歴が重要な反復最適化ループで動作するエージェント

### 長所と短所

| 長所                                                                 | 短所                                                        |
| -------------------------------------------------------------------- | ----------------------------------------------------------- |
| トリガータイミングと圧縮挙動を完全制御                               | 書く・保守するコード量が多い                                |
| 外部メモリの再注入を統合可能                                         | Bedrock API のフォーマット制約を自前で扱う必要がある        |
| 能動的 — オーバーフロー前に発火し、より多くの詳細を保持              | メッセージフォーマット内部の理解が必要                      |
| Bedrock 直接呼び出しでエージェントループやツール呼び出しの問題を回避 | tool_use / tool_result ペアの分割を自前で処理する必要がある |

---

## 設計上の判断とベストプラクティス

### 閾値の選び方

- **70%**（組み込み能動的圧縮のデフォルト）— 大半のマルチターンチャットエージェントに適しています。モデルの応答用にヘッドルームを確保できます。
- **50%** — 急速にコンテキストを蓄積する長時間自律稼働エージェントに適しています。早めにトリガーし、要約により多くの詳細を残せます。
- **オーバーフロー時のみ**（受動的）— 最もシンプルですが、オーバーフローエラーで最後のリクエストが破棄され情報を失うリスクがあります。

総じて、閾値はテストデータがあれば定量的に決定できる指標です。例えばさまざまな閾値で実行し、ハルシネーションが発生し始める閾値を見つけ、それより低い値を設定するなどです。A/B テストとユーザーフィードバックシグナルの収集でも判断できます。

### ツールペアの保持

メッセージをトリミング・分割する際、`toolUse` ブロックとそれに対応する `toolResult` を絶対に分断しないでください。これは不正な会話状態を生み、API エラーを引き起こします。分割点からは必ず後ろ向きに走査して、クリーンな境界を見つけてください。

### 要約のためのテキストのみへの変換

要約用に別途 Bedrock Converse 呼び出しを行う場合、対応するツール定義を提供せずに `toolUse` や `toolResult` ブロックを含めることはできません。最もシンプルな解決策は、これらのブロックをテキスト記述に変換することです（例: `[Called tool: analyze_data]`、`[Tool result: 42 records processed]`）。

### コストの考慮

要約呼び出しは追加の LLM 呼び出しになります:

- Claude Sonnet で約 50K トークンのコンテキストを要約するコストは概ね $0.15〜$0.25 です
- 要約を頻繁にトリガーするエージェントでは、要約呼び出しに小型・低コストのモデルの利用を検討してください
- スライディングウィンドウ方式は追加コストはゼロですが情報を失います

### 会話の妥当性ルール (Bedrock Converse API)

`agent.messages` を直接操作する際は、以下を保証してください:

1. メッセージは `user` と `assistant` のロールを交互に持つ
2. 会話は `user` メッセージで開始する
3. すべての `toolUse` ブロックには次の user メッセージに対応する `toolResult` がある
4. 空の content ブロックがない

---

## クイックリファレンス: どのオプションを選ぶか

| ユースケース                                         | 推奨アプローチ                                                 |
| ---------------------------------------------------- | -------------------------------------------------------------- |
| シンプルなチャットボット、短い会話                   | スライディングウィンドウ（オプション 1）                       |
| 中程度の長さのマルチターンアシスタント               | 能動的圧縮を有効化した要約マネージャ（オプション 2）           |
| LangGraph ベースのエージェント                       | LangGraph ミドルウェア（オプション 3）                         |
| 長時間（数時間）自律稼働するエージェント             | 外部メモリ再注入を伴うカスタムフック（オプション 4）           |
| 大きなツール結果（画像、ファイル）を扱うエージェント | `per_turn=True` と切り詰めを有効化したスライディングウィンドウ |

---

## 参考資料

- [Strands ConversationManager API](https://strandsagents.com/docs/api/python/strands.agent.conversation_manager.conversation_manager/)
- [Strands SlidingWindowConversationManager](https://strandsagents.com/docs/api/python/strands.agent.conversation_manager.sliding_window_conversation_manager/)
- [Strands SummarizingConversationManager](https://strandsagents.com/docs/api/python/strands.agent.conversation_manager.summarizing_conversation_manager/)
- [Strands Hooks API](https://strandsagents.com/docs/api/python/strands.hooks.events/)
- [LangGraph Short-term Memory](https://docs.langchain.com/oss/python/langchain/short-term-memory)
- [LangMem Summarization Guide](https://langchain-ai.github.io/langmem/guides/summarization/)
- [FAST Memory Integration Guide](./MEMORY_INTEGRATION.md)

## Design notes

本派生プロジェクト固有のチャット UX 設計判断:

- **Lambda バックの履歴サイドバー**: 過去の会話は専用 history API 経由で一覧し、各セッションは DynamoDB 上のインデックスから復元する。DDB にはインデックスだけを保存しメッセージ本体は保存しないので書き込みが安く、Memory との重複も避けられる。
- **ストリーミングイベントのスリム化**: AgentCore Runtime のイベントサイズ上限に当たらないように、ストリーミングペイロードを最小化している。診断用データはワイヤフォーマットから外し、クライアント側で再構築する。
- **スティッキー auto-scroll**: チャットの自動スクロールはユーザーが最下部にいるときだけ作動する。ストリーミング中に過去メッセージを読んでいる最中に途切れさせない。
- **Cmd+Enter で送信 (IME 安全)**: 日本語などの IME 変換イベントを検出し、変換中の Enter では送信しない。Cmd/Ctrl+Enter を送信の正規バインディングにしている。
- **OpenAI の toolUseId 使い回しを許容**: OpenAI は同一ターン内で `toolUseId` をラウンド跨ぎで再利用するが Anthropic は再利用しない。チャット UI はツール呼び出しを ID 一意の前提を持たずに追跡する。
- **Mermaid レンダリング**: エージェント応答中の ` ```mermaid ` フェンスをクライアント側で SVG にレンダリングし、別ツール不要でアーキテクチャ説明に図を載せられるようにしている。
