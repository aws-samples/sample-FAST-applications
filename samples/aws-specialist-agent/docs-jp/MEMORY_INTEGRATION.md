# AgentCore Memory 統合ガイド

AWS Bedrock AgentCore Memory をエージェントに統合するための、簡潔で実践的なガイドです。

AgentCore は 2 種類のメモリを提供します。**short-term memory** は生の会話履歴を保存し、エージェントに最近のやり取りからのコンテキストを提供します。**Long-term memory** は AI による戦略を用いて、セッション要約、ユーザーの好み、重要な事実といった意味のあるインサイトを抽出・保存し、エージェントが時間とともに深い理解を構築できるようにします。詳細は [Amazon Bedrock AgentCore Memory blog post](https://aws.amazon.com/blogs/machine-learning/amazon-bedrock-agentcore-memory-building-context-aware-agents/) を参照してください。

---

## Long-Term Memory の有効化

short-term memory（STM — セッション内の生の会話履歴）と long-term memory（LTM — セッションを跨いで思い出せる事実）の両方が利用可能です。LTM は **`strands-single-agent`** パターンでサポートされ、`SemanticMemoryStrategy` を使用して会話から事実を自動的に抽出・保存します。`actorId` はユーザーの検証済み JWT `sub`（Runtime がアイデンティティ伝搬のために抽出するのと同じ識別子）であるため、ユーザーごとに固有の永続メモリが提供されます。

### 動作の仕組み

1. **インフラストラクチャ**: CDK スタックは常にメモリリソース上に `SemanticMemoryStrategy` を作成します（戦略を定義するだけではコストは発生しません）。`USE_LONG_TERM_MEMORY` 環境変数が agent runtime に渡されます。
2. **エージェントの動作**: `USE_LONG_TERM_MEMORY` が `"true"` の場合、Strands エージェントの `AgentCoreMemorySessionManager` は、ターンごとに `/facts/{actorId}` 名前空間から読み取る `retrieval_config` を伴って構成されます。`"false"`（デフォルト）の場合は、short-term の会話履歴のみが有効になります。
3. **事実抽出**: AgentCore は会話イベントを非同期に処理し、事実情報（例: 「ユーザーは Seattle に住んでいる」、「ユーザーは Python を好む」）を抽出します。これらの事実は `/facts/{actorId}` の下に保存され、以降のターンで取得されてレスポンスをパーソナライズします。

### 構成

`infra-cdk/config.yaml` で LTM を切り替えます。

```yaml
backend:
  use_long_term_memory: true # long-term semantic memory 取得を有効化
```

その後、再デプロイします。

```bash
cd infra-cdk && cdk deploy --all
```

### コストに関する考慮事項

LTM は short-term memory に加えて追加料金が発生します。

- **Storage**: $0.75 per 1,000 memory records stored
- **Retrieval**: $0.50 per 1,000 retrieval calls

`use_long_term_memory` が `false` の場合はどちらの料金も発生せず、short-term memory（会話履歴）のみが有効な機能となります。

---

## ステップ 1: CDK によるメモリの構成

メモリリソースは [CloudFormation L1 constructs](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-memory.html) を使用して作成します。**L2 constructs は将来のリリースで提供される予定です。**

### 基本的なメモリ（Short-Term のみ）

```typescript
const memory = new cdk.CfnResource(this, "AgentMemory", {
  type: "AWS::BedrockAgentCore::Memory",
  properties: {
    Name: "MyAgentMemory",
    EventExpiryDuration: 7, // 保持日数（7-365）
    MemoryStrategies: [], // 空 = short-term のみ
    MemoryExecutionRoleArn: executionRole.roleArn,
  },
})
```

### 高度なメモリ（戦略あり）

```typescript
const memory = new cdk.CfnResource(this, "AgentMemory", {
  type: "AWS::BedrockAgentCore::Memory",
  properties: {
    Name: "MyAgentMemory",
    EventExpiryDuration: 30,
    Description: "Memory with intelligent extraction",
    MemoryStrategies: [
      {
        SummaryMemoryStrategy: {
          Name: "SessionSummarizer",
          Namespaces: ["/summaries/{actorId}/{sessionId}"],
        },
      },
      {
        UserPreferenceMemoryStrategy: {
          Name: "PreferenceLearner",
          Namespaces: ["/preferences/{actorId}"],
        },
      },
      {
        SemanticMemoryStrategy: {
          Name: "FactExtractor",
          Namespaces: ["/facts/{actorId}"],
        },
      },
    ],
    MemoryExecutionRoleArn: executionRole.roleArn,
  },
})
```

### 必要な IAM 権限

```typescript
new iam.PolicyStatement({
  effect: iam.Effect.ALLOW,
  actions: [
    "bedrock-agentcore:CreateEvent",
    "bedrock-agentcore:GetEvent",
    "bedrock-agentcore:ListEvents",
    "bedrock-agentcore:RetrieveMemoryRecords",
  ],
  resources: [memoryArn],
})
```

**権限の内訳:**

- `CreateEvent`、`GetEvent`、`ListEvents`: short-term memory（会話履歴）用
- `RetrieveMemoryRecords`: long-term memory（戦略から得られる要約、好み、事実）用

### メモリ構成の理解

詳細な構成内容については、[AgentCore Memory Overview](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html) および [Memory API Reference](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/welcome.html) を参照してください。

**メモリパラメータ**

- **EventExpiryDuration**: 7-365 日
- **MemoryStrategies**: short-term の場合は空、long-term の場合は配列
- **actor_id**: ユーザー識別子
- **session_id/thread_id**: 会話識別子

**メモリ戦略**

| 戦略                           | 目的                 | 名前空間                           |
| ------------------------------ | -------------------- | ---------------------------------- |
| `summaryMemoryStrategy`        | セッションを自動要約 | `/summaries/{actorId}/{sessionId}` |
| `userPreferenceMemoryStrategy` | 好みを学習           | `/preferences/{actorId}`           |
| `semanticMemoryStrategy`       | 事実を抽出           | `/facts/{actorId}`                 |

---

## ステップ 2: フレームワークとの統合

### Strands を使用する場合

Strands 統合の完全なドキュメントは、[official Strands Memory Integration guide](https://strandsagents.com/latest/documentation/docs/community/session-managers/agentcore-memory/) を参照してください。

**インストール:**

```bash
pip install bedrock-agentcore[strands-agents]
```

**コード:**

```python
import os
from strands import Agent
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig

memory_id = os.environ.get("MEMORY_ID")
if not memory_id:
    raise ValueError("MEMORY_ID environment variable is required")

# 基本構成
config = AgentCoreMemoryConfig(
    memory_id=memory_id,
    session_id=session_id,
    actor_id=user_id
)

session_manager = AgentCoreMemorySessionManager(
    agentcore_memory_config=config,
    region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
)

agent = Agent(
    system_prompt="You are a helpful assistant.",
    model=bedrock_model,
    session_manager=session_manager
)
```

**戦略を伴う場合（CDK で構成済みの場合）:**

```python
from bedrock_agentcore.memory.integrations.strands.config import RetrievalConfig

config = AgentCoreMemoryConfig(
    memory_id=memory_id,
    session_id=session_id,
    actor_id=user_id,
    retrieval_config={
        "/preferences/{actorId}": RetrievalConfig(top_k=5, relevance_score=0.7),
        "/facts/{actorId}": RetrievalConfig(top_k=10, relevance_score=0.3)
    }
)
```

**long-term memory を有効化した場合**（上記の [Enabling Long-Term Memory](#enabling-long-term-memory) を参照）:

`strands-single-agent` パターンは、`USE_LONG_TERM_MEMORY` 環境変数に基づいて条件付きで LTM の取得を有効にします。有効化されると、エージェントはターンごとに `/facts/{actorId}` 名前空間から事実を取得します。

```python
use_ltm = os.environ.get("USE_LONG_TERM_MEMORY", "false").lower() == "true"

retrieval_config = (
    {
        "/facts/{actorId}": RetrievalConfig(
            top_k=10,
            relevance_score=0.3,
        )
    }
    if use_ltm
    else None
)

config = AgentCoreMemoryConfig(
    memory_id=memory_id,
    session_id=session_id,
    actor_id=user_id,
    retrieval_config=retrieval_config,
)
```

**ヒント 例:** このアプローチの実装は `agent/strands-single-agent/basic_agent.py` を参照してください。

**公式 AWS ガイド:** [Strands SDK Memory Integration](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/strands-sdk-memory.html)

#### 代替案: フックベースのアプローチ

メモリのライフサイクルやカスタムなメモリ操作をより細かく制御したい場合は、Strands hooks を MemorySession と組み合わせて使用できます。

```python
from strands import Agent
from strands.hooks import AgentInitializedEvent, HookProvider, HookRegistry, MessageAddedEvent
from bedrock_agentcore.memory.session import MemorySession, MemorySessionManager
from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole

# session manager を初期化してセッションを作成
session_manager = MemorySessionManager(memory_id=memory_id, region_name="us-east-1")
user_session = session_manager.create_memory_session(
    actor_id=user_id,
    session_id=session_id
)

# カスタムフックプロバイダを作成
class MemoryHookProvider(HookProvider):
    def __init__(self, memory_session: MemorySession):
        self.memory_session = memory_session

    def on_agent_initialized(self, event: AgentInitializedEvent):
        """エージェント開始時に最近の会話履歴をロード"""
        recent_turns = self.memory_session.get_last_k_turns(k=5)
        if recent_turns:
            # 整形してエージェントの context に追加
            context_messages = []
            for turn in recent_turns:
                for message in turn:
                    role = message['role']
                    content = message['content']['text']
                    context_messages.append(f"{role}: {content}")

            context = "\n".join(context_messages)
            event.agent.system_prompt += f"\n\nRecent conversation:\n{context}"

    def on_message_added(self, event: MessageAddedEvent):
        """新しいメッセージをメモリに保存"""
        messages = event.agent.messages
        if messages and len(messages) > 0:
            message_text = messages[-1]["content"][0]["text"]
            message_role = MessageRole.USER if messages[-1]["role"] == "user" else MessageRole.ASSISTANT

            self.memory_session.add_turns(
                messages=[ConversationalMessage(message_text, message_role)]
            )

    def register_hooks(self, registry: HookRegistry):
        registry.add_callback(MessageAddedEvent, self.on_message_added)
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)

# メモリフックを伴うエージェントを作成
agent = Agent(
    system_prompt="You are a helpful assistant.",
    model=bedrock_model,
    hooks=[MemoryHookProvider(user_session)],
    tools=[...]
)
```

**使用するケース:** カスタムなメモリロードロジック、複数のフックの組み合わせ、細かな制御が必要な場合に使用します。

**ヒント 完全な例:** 動作するコードサンプルについては、[AWS AgentCore Samples repository](https://github.com/awslabs/amazon-bedrock-agentcore-samples) を参照してください。例えば、この [Strands with hooks tutorial](https://github.com/awslabs/amazon-bedrock-agentcore-samples/blob/main/01-tutorials/04-AgentCore-memory/01-short-term-memory/01-single-agent/with-strands-agent/) があります。

### LangGraph を使用する場合

LangGraph 統合の完全なドキュメントは [official LangGraph Memory Integration guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-integrate-lang.html) を参照してください。動作するコードサンプルについては、[LangChain AWS Integration samples](https://github.com/langchain-ai/langchain-aws/tree/main/samples/memory) を参照してください。

**インストール:**

```bash
pip install langgraph-checkpoint-aws langchain-mcp-adapters
```

**Gateway tools を伴う完全な統合:**

```python
from langchain_aws import ChatBedrock
from langgraph.prebuilt import create_react_agent
from langgraph_checkpoint_aws import AgentCoreMemorySaver
from langchain_mcp_adapters.client import MultiServerMCPClient

# memory checkpointer を構成
checkpointer = AgentCoreMemorySaver(
    memory_id=memory_id,
    region_name="us-east-1"
)

# Bedrock model を作成
bedrock_model = ChatBedrock(
    model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    temperature=0.1,
    streaming=True
)

# Gateway tools 用の MCP クライアントを作成
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

# memory とツールを伴うエージェントを作成
graph = create_react_agent(
    model=bedrock_model,
    tools=tools,
    checkpointer=checkpointer
)

# actor と session を指定して invoke
config = {
    "configurable": {
        "thread_id": session_id,
        "actor_id": user_id
    }
}

# レスポンスをストリーム
async for event in graph.astream(
    {"messages": [("user", "Hello")]},
    config=config,
    stream_mode="messages"
):
    message_chunk, metadata = event
    # ストリーミングチャンクを処理
```

**Long-term memory (Store):**

```python
from langgraph_checkpoint_aws import AgentCoreMemoryStore
from langchain_core.runnables import RunnableConfig
import uuid

store = AgentCoreMemoryStore(MEMORY_ID, region_name="us-west-2")

def pre_model_hook(state, config: RunnableConfig, *, store):
    """抽出のためにメッセージを保存"""
    actor_id = config["configurable"]["actor_id"]
    thread_id = config["configurable"]["thread_id"]
    namespace = (actor_id, thread_id)

    messages = state.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            store.put(namespace, str(uuid.uuid4()), {"message": msg})
            break

    return {"llm_input_messages": messages}

graph = create_react_agent(
    model=llm,
    tools=tools,
    checkpointer=checkpointer,
    store=store,
    pre_model_hook=pre_model_hook
)
```

---

## 追加リソース

詳細情報やコミュニティサポートについては以下を参照してください。

- **Community Slack**: `#bedrock-agentcore-memory-interest`
- **All Code Examples**: [AgentCore Samples Repository](https://github.com/awslabs/amazon-bedrock-agentcore-samples)

## Design notes

本派生プロジェクト固有の設計判断:

- **長期記憶 (LTM) をデモで有効化**: Memory リソースには常に semantic memory strategy をプロビジョンし、Runtime の env var `USE_LONG_TERM_MEMORY` で `AgentCoreMemorySessionManager` を retrieval 付きで構築するかを切り替える。LTM 常時保持の追加コストは小さく、「セッションを跨いで記憶する」というデモ価値の方が大きいと判断した。
- **意味検索を補うメタ想起ツール**: ベクトル類似度では「過去に何を話したか」のようなメタ質問にヒットしない。クエリ無しで Memory レコードを列挙する `list_long_term_memories` ツールを別途用意し、エージェント自身の履歴に関するメタ質問に答えられるようにしている。
