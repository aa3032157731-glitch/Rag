# Step 9 参考答案

**验证范围要先说清楚：**

- `RagRetrievalService.cs`、`BuildRagContextAsync` 和 `BuildQuery`：我用你机器上的 .NET SDK 编译过（C# 9，引用的是 Unity 工程里那份 Newtonsoft.Json.dll），也实际请求过真实的检索服务。中文正常，分数正确，服务没开时也能正确降级。
- **没能验证的部分**：我没有打开 Unity 工程，所以对 `VoiceChatController` 和设置面板的改动，只核对过它们周围的代码，没有在 Unity 里编译运行过。另外 Unity 用的是 Mono 运行时，报错信息的文字可能和下面写的不完全一样。

---

## Assets/Scripts/Services/RagRetrievalService.cs

```csharp
using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;

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
    private static readonly HttpClient HttpClient = new HttpClient();
    private readonly string endpoint;

    public RagRetrievalService(string baseUrl)
    {
        endpoint = baseUrl.TrimEnd('/') + "/retrieve";
    }

    public async Task<List<RagHit>> RetrieveAsync(string question, int topK, CancellationToken cancellationToken)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(TimeSpan.FromSeconds(5));
        var body = new JObject { ["question"] = question, ["top_k"] = topK };
        using var content = new StringContent(body.ToString(), Encoding.UTF8, "application/json");
        using var response = await HttpClient.PostAsync(endpoint, content, timeout.Token);
        var json = await response.Content.ReadAsStringAsync();
        if (!response.IsSuccessStatusCode)
            throw new InvalidOperationException($"检索服务返回 {(int)response.StatusCode}：{json}");
        return JObject.Parse(json)["hits"].ToObject<List<RagHit>>();
    }

    public static string BuildQuery(IReadOnlyList<ChatCompletionService.ChatMessage> conversation, string userText)
    {
        var skippedCurrent = false;
        for (var i = conversation.Count - 1; i >= 0; i--)
        {
            if (conversation[i].role != "user") continue;
            if (!skippedCurrent)
            {
                skippedCurrent = true;
                continue;
            }
            return conversation[i].content + "\n" + userText;
        }
        return userText;
    }

    public static string FormatContext(List<RagHit> hits)
    {
        if (hits.Count == 0)
            return "本轮没有从本地知识库检索到相关资料。如果用户问的是校园资料，请直接说明知识库里没有，不要编造。";

        var builder = new StringBuilder();
        builder.AppendLine("以下是根据用户本轮问题从本地知识库检索到的资料，按相关度从高到低排列。");
        builder.AppendLine("回答涉及这些内容时只能依据资料；资料没写的内容要说明知识库中没有，不要编造。不要念出资料编号。");
        for (var i = 0; i < hits.Count; i++)
        {
            builder.AppendLine();
            builder.AppendLine($"[资料{i + 1}] {hits[i].title} {hits[i].section}".TrimEnd());
            builder.AppendLine(hits[i].text);
        }
        return builder.ToString().Trim();
    }
}
```

"不要念出资料编号"这句是专门为数字人加的：回答会被 TTS 朗读出来，"根据资料1"这种话念出来很别扭。

---

## VoiceChatController.cs 的改动

**字段**，放在 `maxInitialKnowledgeBaseCharacters` 后面：

```csharp
    [Header("RAG Retrieval")]
    [Tooltip("每轮按问题检索知识库片段，代替新会话时注入完整知识库。需要先启动 Python 检索服务。")]
    public bool useRagRetrieval = false;
    public string ragServiceUrl = "http://127.0.0.1:8000";
    public int ragTopK = 3;
```

**新增的两个方法**，可以放在 `BuildRuntimeSystemContext` 附近：

```csharp
    private async Task<string> BuildRagContextAsync(
        List<ChatCompletionService.ChatMessage> conversation, string userText, CancellationToken token)
    {
        if (!useRagRetrieval || !enableKnowledgeBase) return string.Empty;
        var query = RagRetrievalService.BuildQuery(conversation, userText);
        try
        {
            var hits = await new RagRetrievalService(ragServiceUrl).RetrieveAsync(query, ragTopK, token);
            var context = RagRetrievalService.FormatContext(hits);
            SetKnowledgeStatus(hits.Count == 0
                ? "RAG：本轮没有检索到相关资料"
                : $"RAG：本轮检索到 {hits.Count} 段，最高相关度 {hits[0].score:F2}");
            Debug.Log($"[RAG] 检索词：{query}\n{context}");
            return context;
        }
        catch (Exception ex) when (!token.IsCancellationRequested)
        {
            SetKnowledgeStatus("RAG 检索失败：" + ex.Message);
            return "本地知识库检索服务暂时不可用。如果用户问的是校园资料，请告诉用户知识库服务没有启动，不要凭印象回答。";
        }
    }

    private void SetKnowledgeStatus(string status)
    {
        currentKnowledgeBaseStatus = status;
        EnqueueMainThread(() => onKnowledgeBaseChanged?.Invoke(status));
    }
```

**RunAssistantTurnAsync**：在原来的两行后面插入三行。

```csharp
            var conversation = BuildConversation(userText);
            var context = BuildRuntimeSystemContext(userText, conversation.Count);
            var knowledge = await BuildRagContextAsync(conversation, userText, token);
            if (!string.IsNullOrEmpty(knowledge))
                context = string.IsNullOrEmpty(context) ? knowledge : context + "\n\n" + knowledge;
            record = BeginAssistantMessage(out session, out conversationGeneration);
```

**EnsureInitialKnowledgeBaseInjected**：在第一个判断里加上 `useRagRetrieval ||`。

```csharp
        if (useRagRetrieval || !injectFullKnowledgeBaseOnNewConversation || !enablePersistentConversation || !enableKnowledgeBase)
        {
            return;
        }
```

**BuildRuntimeSystemContext**：把"应优先依据本会话已注入的本地知识库快照回答"改成"应优先依据本地知识库资料回答"。

---

## VoiceChatRuntimeConfigStore.cs 的改动

```csharp
public sealed class KnowledgeRuntimeSettings
{
    public bool enableKnowledgeBase = true;
    public string knowledgeBaseFolderName = "KnowledgeBase";
    public bool refreshKnowledgeBaseOnStart = true;
    public bool injectFullKnowledgeBaseOnNewConversation = true;
    public int maxInitialKnowledgeBaseCharacters;
    public bool useRagRetrieval;
    public string ragServiceUrl = "http://127.0.0.1:8000";
    public int ragTopK = 3;
}
```

在 `CaptureKnowledge` 里加：

```csharp
        target.useRagRetrieval = source.useRagRetrieval;
        target.ragServiceUrl = source.ragServiceUrl;
        target.ragTopK = source.ragTopK;
```

在 `ApplyKnowledge` 里加：

```csharp
        target.useRagRetrieval = source.useRagRetrieval;
        target.ragServiceUrl = source.ragServiceUrl;
        target.ragTopK = Mathf.Clamp(source.ragTopK, 1, 10);
```

可选：在 `AssistantSettingsHub.Label()` 的 switch 里加中文名：

```csharp
            case "useRagRetrieval": return "按问题检索知识库（RAG）";
            case "ragServiceUrl": return "检索服务地址";
            case "ragTopK": return "每轮检索片段数（1–10）";
```

如果把 `ragServiceUrl` 清空了，请求会因为地址不合法而抛异常，然后走降级流程。所以这里不需要再单独校验地址。

---

## 问题的答案

**`BuildRuntimeSystemContext` 的结果放在哪里？** 它会作为 `additionalSystemPrompt` 传进去，`BuildRequestBody` 把它接在系统提示词后面，合并成请求开头的**那一条** system 消息。它只存在于这一次请求里，不会被 `chatHistoryStore.AppendMessage` 写进会话文件。所以 RAG 片段不会在历史里越积越多。

**全文注入是什么时候生成的？** 在新会话的第一句用户消息之前，生成后作为 system 消息**存进了会话**。`SessionHasInitialKnowledgeBaseInjection` 会阻止重复注入，所以会话中途修改了知识库，这个会话看不到。RAG 每一轮都重新检索，改完资料、重新 ingest 之后，下一句话就能用上新内容。

**为什么 HttpClient 要用 static？** 每个 HttpClient 都有自己的连接池。每次请求都新建一个，就无法复用 TCP 连接，旧连接还会停留在 TIME_WAIT 状态占着端口，请求多了可能把端口耗尽。项目里其他服务也都是这么写的。

**为什么放在工具决策之前？** 工具决策那次请求带的也是这个 `context`。模型先看到了知识库的检索结果，才能判断"资料里已经有答案，不需要 web_search"。放在后面的话，模型可能先去网上搜一遍，搜到的内容还可能和你的知识库对不上。

**为什么 catch 要加 `when (!token.IsCancellationRequested)`？** 用户按停止或发新消息时，token 会被取消，`PostAsync` 随之抛出取消异常。如果把它也当成"检索失败"吞掉，这一轮就会带着"知识库不可用"的提示继续去调用 DeepSeek，而用户其实已经不想要这个回答了。加上过滤条件后，取消异常会继续往上抛，交给 `RunAssistantTurnAsync` 原有的"interrupted"逻辑处理。

我的 5 秒超时用的是另一个 linked token，原来的 token 并没有被取消，所以超时会被当成失败、走降级流程。这正是我们想要的效果。

---

## 实验

### 实验 1：服务没开

用 .NET 实测：连接被拒绝时大约 2 秒后报错（Windows 会重试连接），状态栏显示"RAG 检索失败：由于目标计算机积极拒绝，无法连接"。数字人照常回答，会说知识库服务没启动；"你好"也能正常聊。

如果 Unity 的 HttpClient 走了系统代理，状态栏显示的可能是"检索服务返回 502"，原因和 step6 实验 3 一样。

### 实验 2 和练习四：追问

通过检索服务实测，在 top_k=3、MIN_SCORE=0.45 的条件下：

| 检索词 | 第 1 条 |
|---|---|
| `那周末呢` | 图书馆·开放时间 0.496 ✗（用户问的是食堂） |
| `食堂几点开饭\n那周末呢` | 食堂 0.672 ✓ |
| `它在哪` | 什么都没检索到（全部低于 0.45） |
| `图书馆几点开门\n它在哪` | 图书馆·开放时间 0.673，"所在位置"那一段排在第 3（0.545） |

拼上上一句之后，追问基本能找对了。不过最后一行暴露了这个方法的局限：**上一句的分量太重**。"它在哪"想问的是位置，可"开放时间"那一段排在了最前面。好在 top_k=3 的范围里还是包含了位置那一段，模型能从中找到答案。

**换话题的时候**：`图书馆几点开门\n食堂有素食吗` 实测第 1 条是食堂（0.683），第 2 条是图书馆（0.640）。也就是多带了一段不相关的资料，模型一般能分辨出来，问题不严重。

更好的做法是先让大模型把追问改写成一个完整的问题（比如"图书馆在哪"），再拿去检索。代价是每轮多一次大模型调用，大约增加 1 秒延迟。可以在 step10 评估之后，再决定值不值得这么做。

### 实验 3：资料里没有的问题

这个实验的结果取决于 DeepSeek，我没有实际调用过，所以给不出实测结果。

如果模型编了答案，可以把 `FormatContext` 里的说明写得更具体一些，例如："如果下面的资料没有直接回答用户的问题，就说'知识库里没有这方面的信息'，不要根据相近的资料推测。"

改完要用同一个问题多问几次，确认不是碰巧答对。
