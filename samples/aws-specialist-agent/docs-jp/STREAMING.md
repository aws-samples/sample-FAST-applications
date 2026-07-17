# エージェント向けストリーミングガイド

## 概要

エージェントはストリーミングイベントを SSE 形式で送信します。このガイドでは、ストリーミングをフロントエンドと統合する方法について説明します。

## 統合手順

1. **エージェントがストリーミングイベントを送信** (SSE 形式)
2. **`agentcore-client` ライブラリ** が SSE ストリームを読み取り、適切なパーサーにルーティング:
   - **Strands エージェント (デフォルト)** の場合: `frontend/src/lib/agentcore-client/parsers/strands.ts` — Strands スキーマイベントを解析
   - **LangGraph エージェント** の場合: `frontend/src/lib/agentcore-client/parsers/langgraph.ts`
   - **Bedrock Converse (汎用)** の場合: `frontend/src/lib/agentcore-client/parsers/converse.ts` — 生の Bedrock Converse ストリームイベントを解析
   - **その他のエージェントフレームワーク** の場合: 新しいパーサーを作成し、`frontend/src/lib/agentcore-client/client.ts` に登録
3. **パーサーが型付けされた `StreamEvent` を発行** (text、tool_use_start、tool_use_delta、tool_result、message、result、lifecycle)
4. **`ChatInterface.tsx`** がイベントを処理し、メッセージセグメント (テキスト + ツール呼び出しのインターリーブ) を構築
5. **`ChatMessage.tsx`** がセグメントを Markdown フォーマットとツール呼び出しコンポーネントとともにインラインでレンダリング

---

## 現在の実装

### バックエンド: Strands エージェント

**ファイル:** `agent/strands-single-agent/basic_agent.py`

バックエンドは、JSON 安全な dict にシリアライズされたすべての生の Strands ストリーミングイベントを yield します:

```python
async for event in agent.stream_async(user_query):
    yield json.loads(json.dumps(dict(event), default=str))
```

**補足:** Strands イベントには JSON シリアライズできない Python オブジェクト (エージェントインスタンス、UUID、`ModelStopReason` タプルなど) が含まれることがあります。`json.dumps(default=str)` 呼び出しはこれらを文字列に変換し、すべてのイベントが SSE 経由で安全に送信できることを保証します。

### フロントエンド: イベントパーサー

**ファイル:** `frontend/src/lib/agentcore-client/parsers/strands.ts`

`strands-single-agent` のデフォルトパーサーは、Strands スキーマイベントを処理します:

```typescript
export const parseStrandsChunk: ChunkParser = (line, callback) => {
  if (!line.startsWith("data: ")) return;
  const json = JSON.parse(line.substring(6).trim());

  // テキストトークン: {"data": "Hello"}
  if (typeof json.data === "string") {
    callback({ type: "text", content: json.data });
  }

  // ツール使用: {"current_tool_use": {...}, "delta": {"toolUse": {"input": "..."}}}
  if (json.current_tool_use) {
    // 最初のデルタ (空の入力) → tool_use_start
    // 後続のデルタ → tool_use_delta
  }

  // ツール結果: {"message": {"role": "user", "content": [{"toolResult": {...}}]}}
  if (json.message?.role === "user") {
    // toolResult ブロックを抽出 → callback({ type: "tool_result", ... })
  }

  // 完了: {"result": {"stop_reason": "end_turn"}}
  if (json.result) {
    callback({ type: "result", stopReason: "end_turn" });
  }

  // ライフサイクル: {"init_event_loop": true}
  if (json.init_event_loop || json.start_event_loop) { ... }
};
```

エッジケースを含む完全な実装はソースファイルを参照してください。

### イベント構造

Strands は以下のイベントタイプを提供します:

- `data`: テキストチャンク (到着すると蓄積される)
- `current_tool_use`: ツール名、ID、入力パラメータ (ストリーミング用の `delta` を含む)
- `message`: 完全なコンテンツを持つ最終的な構造化メッセージ (`toolUse` を持つ assistant、`toolResult` を持つ user)
- `result`: 停止理由とメトリクスを持つ AgentResult
- `init_event_loop`、`start_event_loop`、`complete`: ライフサイクルマーカー
- `tool_stream_event`: ツール実行からストリーミングされるイベント
- `event`: 生の Bedrock Converse イベント (以下の代替 converse パーサーで使用)

```javascript
// テキストストリーミング
data: {"data": "Hello"}
data: {"data": " there"}

// ツール使用開始 — 最初のデルタは空の入力を持つ
data: {"current_tool_use": {"toolUseId": "tool_abc123", "name": "text_analysis"}, "delta": {"toolUse": {"input": ""}}}

// ツール入力ストリーミング
data: {"current_tool_use": {"toolUseId": "tool_abc123", "name": "text_analysis"}, "delta": {"toolUse": {"input": "{\"text\": \"hello\"}"}}}

// 完全な assistant メッセージ
data: {"message": {"role": "assistant", "content": [{"toolUse": {"toolUseId": "tool_abc123", "name": "text_analysis", "input": {"text": "hello"}}}]}}

// ツール結果 (toolResult ブロックを持つ user メッセージ)
data: {"message": {"role": "user", "content": [{"toolResult": {"toolUseId": "tool_abc123", "content": [{"text": "Analysis complete: 1 word"}]}}]}}

// 最終結果
data: {"result": {"stop_reason": "end_turn"}}

// ライフサイクルイベント
data: {"init_event_loop": true}
data: {"start_event_loop": true}
```

**参考:** [Strands Streaming Documentation](https://strandsagents.com/latest/documentation/docs/user-guide/concepts/streaming/overview/)

---

## 代替: 生の Converse イベントを使用する

Strands スキーマイベントを解析する代わりに、`event` キーの下にネストされた生の Bedrock Converse イベントを解析することもできます。これにより、Converse ストリーム API 構造への低レベルアクセスが得られます。

**補足:** ツール結果は Converse ストリームイベントとしては発行されません — それらは次の `converse_stream` 呼び出しへの入力となります。Strands はこれを内部で処理し、ツール結果を `message` イベントとして発行します。converse パーサーはツール結果を処理しません。代わりに、`ChatInterface.tsx` は次のテキストセグメントがストリーミングを開始したときにツールを完了としてマークします。

### フロントエンドパーサー

**ファイル:** `frontend/src/lib/agentcore-client/parsers/converse.ts`

デフォルトの strands パーサーの代わりにこのパーサーを使用するには、`client.ts` を更新します:

```typescript
import { parseConverseChunk } from "./parsers/converse";

const PARSERS: Record<AgentPattern, ChunkParser> = {
  "strands-single-agent": parseConverseChunk,  // Converse パーサーに切り替える
  ...
};
```

このパーサーは生の Bedrock Converse イベントを処理します:

```typescript
export const parseConverseChunk: ChunkParser = (line, callback) => {
  if (!line.startsWith("data: ")) return;
  const json = JSON.parse(line.substring(6).trim());

  const event = json.event;
  if (event) {
    // テキストストリーミング
    if (event.contentBlockDelta?.delta?.text) {
      callback({ type: "text", content: event.contentBlockDelta.delta.text });
    }

    // ツール使用開始
    if (event.contentBlockStart?.start?.toolUse) {
      const toolUse = event.contentBlockStart.start.toolUse;
      callback({ type: "tool_use_start", toolUseId: toolUse.toolUseId, name: toolUse.name });
    }

    // ツール使用入力ストリーミング
    if (event.contentBlockDelta?.delta?.toolUse?.input) {
      callback({ type: "tool_use_delta", toolUseId: currentToolUseId, input: ... });
    }

    // メッセージ停止
    if (event.messageStop?.stopReason) {
      callback({ type: "result", stopReason: event.messageStop.stopReason });
    }
  }
};
```

エッジケースを含む完全な実装はソースファイルを参照してください。

### イベント構造

Converse イベントは `event` キーの下にネストされます:

```javascript
// メッセージライフサイクル
data: {"event": {"messageStart": {"role": "assistant"}}}

// テキストストリーミング
data: {"event": {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Hello"}}}}
data: {"event": {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": " there"}}}}

// ツール使用開始
data: {"event": {"contentBlockStart": {"contentBlockIndex": 1, "start": {"toolUse": {"toolUseId": "tool_abc123", "name": "text_analysis"}}}}}

// ツール使用入力ストリーミング
data: {"event": {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": "{\"text\": \"hello\"}"}}}}}

// コンテンツブロックとメッセージの完了
data: {"event": {"contentBlockStop": {"contentBlockIndex": 0}}}
data: {"event": {"messageStop": {"stopReason": "end_turn"}}}

// メタデータ
data: {"event": {"metadata": {"usage": {"inputTokens": 88, "outputTokens": 30}}}}
```

**参考:** [Bedrock Converse Stream API](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/bedrock-runtime/client/converse_stream.html)

---

## LangGraph/LangChain 実装

**補足:** LangGraph はタプルベースのストリーミング `(message_chunk, metadata)` を使用し、コンテンツが配列となる LangChain メッセージオブジェクトを返します。

### バックエンド

LangGraph エージェントを `messages` モードでストリーミングすると、生の LangChain メッセージチャンクが yield されます:

```python
# messages モードでストリーミング - 生の LangChain メッセージチャンクを yield する
async for event in graph.astream(
    {"messages": [("user", user_query)]},
    config=config,
    stream_mode="messages"
):
    message_chunk, metadata = event
    yield message_chunk.model_dump()  # JSON 安全な dict にシリアライズ
```

### イベント構造

LangGraph は LangChain メッセージオブジェクトを発行し、これらは **コンテンツブロックの配列** をコンテンツとして JSON にシリアライズされます:

```javascript
// テキストストリーミング (AIMessageChunk)
data: {"content": [{"type": "text", "text": "Hello", "index": 0}], "type": "AIMessageChunk", ...}
data: {"content": [{"type": "text", "text": " there", "index": 0}], "type": "AIMessageChunk", ...}

// ツール使用開始 — コンテンツブロックは id と name を持つ
data: {"content": [{"type": "tool_use", "id": "tool_abc123", "name": "text_analysis", "input": {}, "index": 1}], "type": "AIMessageChunk", ...}

// ツール入力ストリーミング — partial_json が増分入力を運ぶ
data: {"content": [{"type": "tool_use", "partial_json": "{\"text\":", "index": 1}], "type": "AIMessageChunk", ...}
data: {"content": [{"type": "tool_use", "partial_json": " \"hello\"}", "index": 1}], "type": "AIMessageChunk", ...}

// ツールレスポンス (ToolMessage — 別のメッセージタイプ)
data: {"content": "Tool result text", "type": "tool", "name": "text_analysis", "tool_call_id": "tool_abc123", ...}

// 停止理由
data: {"content": [], "type": "AIMessageChunk", "response_metadata": {"stop_reason": "end_turn"}, ...}

// 使用量メタデータを含む最終チャンク
data: {"content": [], "type": "AIMessageChunk", "chunk_position": "last", "usage_metadata": {"input_tokens": 88, "output_tokens": 30}}
```

**現在のパーサーが処理するもの:**

- `content[].type === "text"` を持つ `AIMessageChunk`: 表示用のテキストトークン
- `content[].type === "tool_use"` + `id` + `name` を持つ `AIMessageChunk`: ツール呼び出し開始
- `content[].type === "tool_use"` + `partial_json` を持つ `AIMessageChunk`: ストリーミングツール入力
- `type === "tool"` (ToolMessage): ツール実行結果
- `response_metadata.stop_reason`: ストリーム完了

**Strands との主な違い:** LangGraph の `content` は常に型付けされたブロック (text、tool_use) の配列であり、フラットな文字列ではありません。ツール結果は user メッセージ内にネストされるのではなく、別の `ToolMessage` オブジェクトとして提供されます。

### フロントエンドパーサー

**ファイル:** `frontend/src/lib/agentcore-client/parsers/langgraph.ts`

同じパターンで、SSE 行を解析し、型付けされたイベントを発行します。LangGraph は LangChain メッセージタイプを使用します:

```typescript
export const parseLanggraphChunk: ChunkParser = (line, callback) => {
  if (!line.startsWith("data: ")) return
  const json = JSON.parse(line.substring(6).trim())

  // ツール結果: {"type": "tool", "tool_call_id": "...", "content": "result"}
  if (json.type === "tool") {
    callback({ type: "tool_result", toolUseId: json.tool_call_id, result: json.content })
  }

  // AIMessageChunk — コンテンツはブロックの配列
  if (json.type === "AIMessageChunk" && Array.isArray(json.content)) {
    for (const block of json.content) {
      if (block.type === "text" && block.text) {
        callback({ type: "text", content: block.text })
      }
      if (block.type === "tool_use" && block.id && block.name) {
        callback({ type: "tool_use_start", toolUseId: block.id, name: block.name })
      }
    }

    // レスポンスメタデータからの停止理由
    if (json.response_metadata?.stop_reason) {
      callback({ type: "result", stopReason: json.response_metadata.stop_reason })
    }
  }
}
```

ツール入力デルタストリーミングとエッジケースを含む完全な実装を参照してください。

**重要なポイント:**

- アシスタントレスポンスのみを処理するために `type === 'AIMessageChunk'` でフィルタリングする
- `ToolMessage` やその他の内部メッセージタイプは無視する
- `content` は文字列ではなく **コンテンツブロックの配列** である
- 各ブロックには `type`、`text`、`index` フィールドがある
- テキストコンテンツを抽出するために `type === 'text'` でフィルタリングする
- 複数のテキストブロックがある場合は結合する

**コンテンツが配列である理由:**
LangChain は、Anthropic/OpenAI のメッセージ形式に従って、マルチモーダルメッセージ (テキスト、画像、ツール呼び出し) をサポートするためにコンテンツブロックを使用します。

**参考:**

- [LangGraph Streaming](https://docs.langchain.com/oss/python/langgraph/streaming)
- [LangChain Streaming](https://docs.langchain.com/oss/python/langchain/streaming)

---

## 新しいエージェントパターンの追加

1. エージェントコードを含む `agent/my-pattern/` を作成する
2. パーサーを作成する: `frontend/src/lib/agentcore-client/parsers/my-pattern.ts`
   - `callback()` を介して SSE 行を `StreamEvent` に変換する `ChunkParser` 関数をエクスポートする
3. `frontend/src/lib/agentcore-client/client.ts` に登録する (コンストラクタのパーサーマップに追加)
4. `infra-cdk/config.yaml` で `pattern: my-pattern` を設定する

---

## デバッグ

パーサーでコンソールロギングを有効にします:

```javascript
console.log("[Streaming Event]", data)
```

ブラウザコンソール (F12) を開いて、エージェントからのすべてのイベントを確認します。
