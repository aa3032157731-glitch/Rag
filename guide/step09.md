# Step 9：接入数字人

**做完这一步**：在数字人里勾选"按问题检索知识库"，然后问"图书馆几点关门"。数字人会先调用 `/retrieve`，再根据检索到的片段回答；知识库状态栏会显示"RAG：本轮检索到 N 段"。检索服务没开时，数字人照样能聊天，只是会告诉你知识库服务没启动。

**前提**：step8 的服务能正常启动。

**开始之前**：数字人项目里现在有不少还没提交的改动。先把它们提交掉，再新建一个分支，这样随时都能退回去：

```powershell
cd 'C:\Users\qqcom\Documents\ChatGPT\数字人\camel-digitalman-main'
git status
# 先提交你手上的改动，然后：
git switch -c feature/rag-retrieval
```

下面提到的行号，都是按你现在的文件写的；如果你改过代码，行号可能会有偏移，按方法名去找就行。

---

## 0. 先读懂：现在的知识库是怎么工作的

先不写代码，打开下面这几个方法读一遍：

| 位置 | 做了什么 |
|---|---|
| `VoiceChatController.RunAssistantTurnAsync`（约 857 行） | 每一轮回答的主流程 |
| `VoiceChatController.EnsureInitialKnowledgeBaseInjected`（约 1492 行） | 新会话的第一句话之前，把**整个知识库**存成一条隐藏的 system 消息 |
| `VoiceChatController.BuildRuntimeSystemContext`（约 1868 行） | 生成**本轮**额外的 system 提示，比如工具说明、相关历史 |
| `ChatCompletionService.BuildRequestBody` | 把 `additionalSystemPrompt` 和系统提示词合并成请求里的 system 消息 |

读完后先回答下面两个问题，step9 的设计就是从这里来的：

1. `BuildRuntimeSystemContext` 返回的字符串，最后放进了请求的什么位置？它会被存进聊天记录文件吗？
2. 全文注入那条 system 消息，是在什么时候生成的？会话进行到一半时你修改了知识库，这次会话能看到改动吗？

**全文注入的问题**：知识库有多大，每轮请求就要带多少字，所以库越大越慢、越贵，迟早会超过上下文长度；内容是会话开始时拍的"快照"，之后改了知识库，这次会话也看不到；模型还得在一大堆资料里自己去找答案。

**RAG 的做法**：每一轮都用用户的问题去检索，把 top-k 片段放进**这一轮**的 runtime system context。它不会存进聊天记录，下一轮会重新检索。

```text
RunAssistantTurnAsync
  → BuildConversation               历史消息
  → BuildRuntimeSystemContext       工具说明等
  → BuildRagContextAsync    ← 新增：POST /retrieve，拿到片段，拼进 context
  → ResolveAgentToolCallsAsync      工具决策（这里也能看到检索结果）
  → StreamChatAsync                 生成回答
```

---

## 1. 练习一：RagRetrievalService.cs

新建 `Assets/Scripts/Services/RagRetrievalService.cs`：

```csharp
public sealed class RagHit
{
    public string source;
    public string title;
    public string section;
    public string text;
    public float score;
}

public sealed class RagRetrievalService
{
    public RagRetrievalService(string baseUrl);
    public Task<List<RagHit>> RetrieveAsync(string question, int topK, CancellationToken cancellationToken);
    public static string FormatContext(List<RagHit> hits);
}
```

**规则**

- 请求发到 `{baseUrl}/retrieve`，请求体是 `{"question": ..., "top_k": ...}`，必须是 **UTF-8** 编码的 JSON。
- 要有**自己的 5 秒超时**：用传进来的 token 创建一个 linked token，再调用 `CancelAfter`。检索卡住了，数字人不能跟着一起卡住。
- 状态码不是 2xx 时，抛出异常，异常信息里要带上状态码和响应内容。
- 用 Newtonsoft 解析响应：`JObject.Parse(json)["hits"].ToObject<List<RagHit>>()`。RagHit 的字段名和 JSON 的键名一样，不需要加任何特性标注。
- `FormatContext` 有两种情况：没有结果时，返回一句话，告诉模型"知识库没检索到相关资料，涉及校园资料的问题就说没有，不要编造"；有结果时，先写一段说明，再按 `[资料1] 标题 章节` 加正文的格式，逐段列出来。

**提示**：照着 `ChatCompletionService` 的写法来：用 `static readonly HttpClient`，请求体用 `new StringContent(json, Encoding.UTF8, "application/json")`。

**想一想**：为什么 HttpClient 要写成 static 的一个实例，而不是每次请求都 `new` 一个？

---

## 2. 练习二：改 VoiceChatController

**a) 加三个字段**，放在 Knowledge Base 那组字段附近：

```csharp
[Header("RAG Retrieval")]
public bool useRagRetrieval = false;
public string ragServiceUrl = "http://127.0.0.1:8000";
public int ragTopK = 3;
```

**b) 写一个方法**：

```csharp
private async Task<string> BuildRagContextAsync(
    List<ChatCompletionService.ChatMessage> conversation, string userText, CancellationToken token)
```

- `useRagRetrieval` 或 `enableKnowledgeBase` 是 false 时，返回空字符串。FactoryLab 场景会关掉 `enableKnowledgeBase`，所以也要检查它。
- 检索成功时：更新 `currentKnowledgeBaseStatus`，比如"RAG：本轮检索到 3 段，最高相关度 0.66"；再用 `Debug.Log` 打印检索词和拼好的 context，方便调试；最后返回 context。
- 检索失败时：更新状态栏，比如"RAG 检索失败：……"；返回一句话，告诉模型"知识库服务暂时不可用，涉及校园资料的问题请告诉用户服务没启动，不要凭印象回答"。
- **用户取消这一轮时（按了停止，或者发了新消息），不要把取消也当成失败来处理**，要让取消异常继续往上抛。用 `catch (Exception ex) when (!token.IsCancellationRequested)` 来实现。
- 更新状态栏后，参照 `BuildInitialKnowledgeBaseSystemMessage` 的写法，用 `EnqueueMainThread` 触发 `onKnowledgeBaseChanged`。

**c) 在 `RunAssistantTurnAsync` 里调用它**：放在 `BuildRuntimeSystemContext` 之后、`ResolveAgentToolCallsAsync` 之前，把结果拼到 `context` 后面。

**想一想**：为什么要放在工具决策**之前**？

**d) 开着 RAG 时，不再做全文注入**：在 `EnsureInitialKnowledgeBaseInjected` 开头的判断里，加上 `useRagRetrieval` 这个条件。

**e) 改一句提示词**：`BuildRuntimeSystemContext` 里有一句"应优先依据本会话已注入的本地知识库快照回答"。把"本会话已注入的本地知识库快照"改成"本地知识库资料"，这样两种模式下这句话都说得通。

### 这一步最重要的设计原则：失败要降级，不要中断

回顾一下 step6 的 ingest：资料有问题就立刻报错退出。那里这么做是对的，因为导入是你手动运行的批处理任务，失败了你就在旁边，可以修好再重跑。

这里情况相反：用户正对着数字人说话。检索服务没开，不应该导致数字人整句话都回答不出来。它应该照样聊天、照样能用工具，只是遇到校园资料的问题时说明知识库不可用。

**同样是"出错了"，放在不同的地方，处理策略就不一样。**

---

## 3. 练习三：让设置面板能控制这三个字段

数字人的设置会保存在 `voice-chat-settings.json` 里，需要改 `Assets/Scripts/Config/VoiceChatRuntimeConfigStore.cs` 里的三处：

1. `KnowledgeRuntimeSettings` 类（约 256 行）：加上同样的三个字段。
2. `CaptureKnowledge`：把这三个值从 controller 复制到 settings。
3. `ApplyKnowledge`：把这三个值从 settings 复制回 controller。`ragTopK` 要用 `Mathf.Clamp` 限制在 1 到 10 之间，和 API 的限制保持一致。

设置面板（`AssistantSettingsHub.ObjectFields`）是用**反射**读取字段的，所以新加的字段会自动出现在"设置 → 机体模块 → 知识库"里，不用改任何 UI 代码。字段名默认会原样显示；想显示中文，可以在 `AssistantSettingsHub.Label()` 的 switch 里加几个 case。

旧的配置文件里没有这三个字段，Newtonsoft 反序列化时会保留默认值，也就是 `useRagRetrieval = false`。所以你不去勾选，数字人的行为就和原来完全一样。

---

## 4. 跑起来

启动顺序：

```powershell
# Rag 项目根目录
docker compose --env-file infra/.env -f infra/compose.yaml up -d
.\.venv\Scripts\python.exe -m uvicorn backend.api:app --host 127.0.0.1 --port 8000
```

等看到 `startup complete` 后，在 Unity 里打开 `main` 场景，然后点 Play：

1. 设置 → 机体模块 → 知识库 → 勾选 `useRagRetrieval` → 点"保存并应用"。
2. 点"新对话"。旧会话里可能已经存了全文注入的那条 system 消息，所以一定要新开一个对话。
3. 问"图书馆几点关门"。

检查三个地方：

- Console 里有 `[RAG] 检索词：图书馆几点关门`，后面跟着拼好的资料。
- 知识库状态栏显示"RAG：本轮检索到 N 段"。
- 回答里说的是 22:00。

---

## 5. 实验

### 实验 1：检索服务没开

1. 在 uvicorn 的终端按 Ctrl+C，关掉服务。
2. 问"图书馆几点关门"。状态栏显示的是什么？数字人怎么回答？
3. 再说一句"你好"。能正常聊天吗？

### 实验 2：追问

1. 先问"食堂几点开饭"，再问"那周末呢"。
2. 看 Console 里第二轮的检索词和检索结果。检索到的是哪份资料？数字人又是怎么回答的？

### 练习四：修好追问

在 `RagRetrievalService` 里加一个方法：

```csharp
public static string BuildQuery(IReadOnlyList<ChatCompletionService.ChatMessage> conversation, string userText)
```

规则：找到 `conversation` 里**上一条**用户消息，返回"上一条 + 换行 + 本条"；如果没有上一条，就只返回本条。

注意：调用这个方法时，`conversation` 的最后一条用户消息就是**这一句本身**（`AppendUserMessage` 比 `BuildConversation` 先执行），所以要跳过它，取再往前的那一条。

在 `BuildRagContextAsync` 里，用 `BuildQuery` 的结果作为检索词，然后重做实验 2。

**想一想**：如果用户换了话题，比如先问"图书馆几点开门"，再问"食堂有素食吗"，这个做法会带来什么问题？严重吗？

### 实验 3：资料里没有的问题

问"宿舍几点熄灯"。这个问题在 step7 里的分数是 0.526，能通过 0.45 的阈值，所以检索会返回一段并不相关的资料。数字人是老实说"知识库里没有"，还是编了一个答案出来？

如果它编了，就去改 `FormatContext` 里的说明文字，再试一次。

---

## 6. 验收

- [ ] 开启 RAG 后，数字人能根据检索结果正确回答；Console 和状态栏都能看到检索过程
- [ ] 检索服务关掉时，数字人还能正常聊天，并说明知识库服务不可用
- [ ] 追问的问题修好了
- [ ] 关掉 `useRagRetrieval` 后，数字人的行为和原来一样
- [ ] 能说清楚：为什么检索结果放进 runtime context，而不是存进聊天记录？为什么这里要降级，而 ingest 那里要直接报错？
- [ ] 在数字人项目的分支上提交一次

对照答案：[step09-answer.md](step09-answer.md)。下一步：[step10：评估与对比](step10.md)。
