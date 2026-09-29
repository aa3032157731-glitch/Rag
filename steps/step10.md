# Step 10：Unity 调用本机检索接口

> **本节改动说明**：C# 代码未做改动——`HttpJson` 里手动用 `Stopwatch` 管理超时、每帧检查取消令牌的写法已经是 `UnityWebRequest` 异步场景下的标准做法；`RetrievalPreview` 里的 `generation` 轮次计数器设计也是正确处理"取消后旧结果不覆盖新界面"的关键机制，不需要改。这里只是重新组织了文档的呈现顺序。

## 本节目标

完成后你能：在 Unity 里输入问题，看到从本机 FastAPI 检索到的真实原文和来源，而且断网、超时、取消这几种情况都有明确区分的界面反馈。

**前置条件**：完成 [step09](step09.md)，Unity 场景能正常显示中文。启动 Docker、Qdrant 和 step08 的 FastAPI，先用浏览器确认 `/ready` 返回 200。

本篇仍然不调用 DeepSeek，只把真实检索的原文显示到 Unity 里。**先把 HTTP 和 JSON 这一层跑通，再接聊天模型**——这样如果后面出问题，你能立刻知道是"检索这一层"还是"聊天这一层"的锅，排错会容易得多。

## 一、替换场景中的演示组件

退出 Unity Play 模式。在 `ChatRoot` 的 Inspector 里**移除 UiPreview 组件**，保留 `ChatUI`。`UiPreview.cs` 源文件可以留着，但不要再挂载它，否则一次发送会被两个组件同时处理，界面上会看到重复或冲突的结果。

后面新建的 DTO、客户端类都是纯数据/纯逻辑类，不挂 GameObject。只有本篇最后的 `RetrievalPreview` 要挂载。

## 二、新建 `Assets/Scripts/RagModels.cs`

以下路径都相对于 `unity-client`。这里的 JSON 字段名要和 Python 端保持完全一致——一边写 `topK`、另一边只认 `top_k` 是最常见的联调翻车点。

<!-- file: unity-client/Assets/Scripts/RagModels.cs -->
```csharp
using System;
using System.Collections.Generic;

[Serializable]
public sealed class RagRequest
{
    public string query;
    public int top_k;
}

[Serializable]
public sealed class RagHit
{
    public string chunk_id;
    public string source;
    public string title;
    public string section;
    public string record_key;
    public string text;
    public float score;
}

[Serializable]
public sealed class RagTimings
{
    public double embedding;
    public double search;
    public double total;
}

[Serializable]
public sealed class RagResponse
{
    public string request_id;
    public string status;
    public string query;
    public string index_version;
    public List<RagHit> results;
    public RagTimings timings_ms;
}

[Serializable]
public sealed class ApiErrorDetail
{
    public string code;
    public string message;
}

[Serializable]
public sealed class ApiErrorResponse
{
    public string request_id;
    public ApiErrorDetail error;
}
```

这些 DTO（数据传输对象）只有字段，没有任何 Unity 生命周期方法——不要把每个 DTO 又拆成一个需要挂载的 `MonoBehaviour`，那是完全不必要的复杂度。

## 三、新建通用请求封装 `Assets/Scripts/HttpJson.cs`

这个类会被检索客户端和 step11 的 DeepSeek 客户端共用。所有调用都从 Unity 主线程发起，`await Task.Yield()` 之后会回到 Unity 的同步上下文继续执行，所以可以安全地检查取消状态、更新 UI。

<!-- file: unity-client/Assets/Scripts/HttpJson.cs -->
```csharp
using System;
using System.Diagnostics;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using UnityEngine.Networking;

public sealed class ApiRequestException : Exception
{
    public long StatusCode { get; }
    public ApiRequestException(long status, string message) : base(message)
    {
        StatusCode = status;
    }
}

public static class HttpJson
{
    public static async Task<string> PostAsync(
        string url, object payload, int timeoutSeconds,
        CancellationToken token, string bearerKey = null)
    {
        token.ThrowIfCancellationRequested();
        var json = JsonConvert.SerializeObject(payload);
        using (var request = new UnityWebRequest(url, "POST"))
        {
            request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(json));
            request.downloadHandler = new DownloadHandlerBuffer();
            request.SetRequestHeader("Content-Type", "application/json; charset=utf-8");
            if (!string.IsNullOrWhiteSpace(bearerKey))
                request.SetRequestHeader("Authorization", "Bearer " + bearerKey.Trim());
            // 明确拒绝跳转，避免带着密钥的请求意外发往其他地址。
            request.redirectLimit = 0;
            var watch = Stopwatch.StartNew();
            var operation = request.SendWebRequest();
            while (!operation.isDone)
            {
                if (token.IsCancellationRequested)
                {
                    request.Abort();
                    token.ThrowIfCancellationRequested();
                }
                if (watch.Elapsed.TotalSeconds > timeoutSeconds)
                {
                    request.Abort();
                    throw new TimeoutException("请求超时，请检查服务或增加本机配置中的超时秒数");
                }
                await Task.Yield();
            }
            token.ThrowIfCancellationRequested();
            var body = request.downloadHandler.text ?? "";
            if (request.result != UnityWebRequest.Result.Success ||
                request.responseCode < 200 || request.responseCode >= 300)
            {
                var message = request.responseCode == 0
                    ? "连接失败：" + request.error
                    : "HTTP " + request.responseCode;
                try
                {
                    var error = JsonConvert.DeserializeObject<ApiErrorResponse>(body);
                    if (!string.IsNullOrWhiteSpace(error?.error?.message))
                        message += "：" + error.error.message;
                    if (!string.IsNullOrWhiteSpace(error?.request_id))
                        message += " [request_id=" + error.request_id + "]";
                }
                catch (JsonException) { /* 非 JSON 错误页，只显示状态码，不回显全文。 */ }
                throw new ApiRequestException(request.responseCode, message);
            }
            return body;
        }
    }
}
```

**关键点**：

- `using` 语句保证成功、失败、取消三种情况下请求和处理器都会被正确释放。
- 用自己的 `Stopwatch` 管理总超时，而不是依赖 `UnityWebRequest` 自带的 `timeout` 属性——这样能把"用户主动取消"和"等待超时"区分成两种不同的界面提示，`UnityWebRequest.timeout` 做不到这一点。
- 不通过 `token.Register` 的后台回调直接操作 Unity 对象，而是每一帧在主线程里检查取消状态——这是 Unity 异步编程里避免"跨线程访问 UI 对象崩溃"的关键约束。
- 不用 `Task.Run` 包住 `UnityWebRequest`，也不在 `await` 之后用 `ConfigureAwait(false)`——这两者都会导致后续代码跑到非主线程，从而在更新 UI 时崩溃。
- `Abort()` 只能终止客户端这边的等待，不能保证 Python 那边已经开始的 CPU/GPU 计算立刻停止。
- 不打印 Authorization 头、密钥、完整请求体或完整错误页——避免密钥意外出现在日志里。

## 四、新建 `Assets/Scripts/RagClient.cs`

<!-- file: unity-client/Assets/Scripts/RagClient.cs -->
```csharp
using System;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;

public sealed class RagClient
{
    private readonly RuntimeSettings settings;
    public RagClient(RuntimeSettings settings) { this.settings = settings; }

    public async Task<RagResponse> RetrieveAsync(string query, CancellationToken token)
    {
        var body = await HttpJson.PostAsync(
            settings.retrievalUrl,
            new RagRequest { query = query, top_k = settings.topK },
            settings.retrievalTimeoutSeconds, token);
        RagResponse response;
        try { response = JsonConvert.DeserializeObject<RagResponse>(body); }
        catch (JsonException exc) { throw new InvalidOperationException("检索响应不是有效 JSON", exc); }
        if (response == null || response.results == null ||
            (response.status != "ok" && response.status != "no_match") ||
            string.IsNullOrWhiteSpace(response.request_id) || string.IsNullOrWhiteSpace(response.index_version))
            throw new InvalidOperationException("检索响应字段缺失或状态不合法");
        if ((response.status == "no_match" && response.results.Count != 0) ||
            (response.status == "ok" && response.results.Count == 0) || response.results.Count > 10)
            throw new InvalidOperationException("检索状态与片段数量不一致");
        var totalCharacters = 0;
        foreach (var hit in response.results)
        {
            if (hit == null || string.IsNullOrWhiteSpace(hit.text) ||
                string.IsNullOrWhiteSpace(hit.source) || string.IsNullOrWhiteSpace(hit.chunk_id))
                throw new InvalidOperationException("检索片段缺少正文、来源或 ID");
            totalCharacters += hit.text.Length;
        }
        if (totalCharacters > 20000)
            throw new InvalidOperationException("检索上下文过大，请检查后端配置");
        return response;
    }
}
```

**关键点**：这里不是"HTTP 200 就默认一切正确"——客户端还会检查 JSON 内容本身是否自洽（状态和结果数量是否匹配、每条结果是否缺字段），避免把不完整的数据一路传到界面层才崩溃。

## 五、新建临时 `Assets/Scripts/RetrievalPreview.cs`

<!-- file: unity-client/Assets/Scripts/RetrievalPreview.cs -->
```csharp
using System;
using System.Linq;
using System.Threading;
using UnityEngine;

public sealed class RetrievalPreview : MonoBehaviour
{
    public ChatUI ui;
    private RagClient client;
    private CancellationTokenSource current;
    private int generation;

    private void Start()
    {
        try { client = new RagClient(RuntimeSettings.Load()); }
        catch (Exception exc) { ui.SetStatus(exc.Message); enabled = false; return; }
        ui.Submitted += Submit;
        ui.Cancelled += Cancel;
    }

    private async void Submit(string question)
    {
        Cancel();
        var id = ++generation;
        var own = new CancellationTokenSource();
        current = own;
        ui.SetBusy(true);
        ui.SetStatus("正在检索……");
        ui.ShowAnswer("");
        ui.ShowSources("");
        try
        {
            var result = await client.RetrieveAsync(question, own.Token);
            if (id != generation) return;
            ui.ShowAnswer(result.status == "no_match" ? "知识库未找到相关资料。" :
                string.Join("\n\n", result.results.Select((h, i) => "[S" + (i + 1) + "] " + h.text)));
            ui.ShowSources(string.Join("\n", result.results.Select((h, i) =>
                "[S" + (i + 1) + "] " + h.source + " / " + h.section)));
            ui.SetStatus("检索完成，版本：" + result.index_version);
        }
        catch (OperationCanceledException) { if (id == generation) ui.SetStatus("已取消"); }
        catch (Exception exc) { if (id == generation) ui.SetStatus("检索失败：" + exc.Message); }
        finally
        {
            if (id == generation) { current = null; ui.SetBusy(false); }
            own.Dispose();
        }
    }

    private void Cancel()
    {
        ++generation;
        current?.Cancel();
        current = null;
        ui.SetBusy(false);
        ui.SetStatus("已取消");
    }

    private void OnDestroy()
    {
        ++generation;
        current?.Cancel();
        current = null;
        if (ui != null) { ui.Submitted -= Submit; ui.Cancelled -= Cancel; }
    }
}
```

**`generation` 是这里最值得记住的设计**：它是一个轮次编号。即使某个旧请求没有被及时取消而是自然完成了，只要它的 `id` 和当前 `generation` 对不上，就说明用户已经开始了新的一轮，旧结果直接丢弃，不更新界面。取消令牌源（`CancellationTokenSource`）由创建它的那次异步调用自己在 `finally` 里释放，避免新任务提前把旧任务还在用的资源 dispose 掉。

## 六、挂载和测试

1. 在 `ChatRoot` 上添加 `RetrievalPreview`，绑定它的 Ui 字段；确认 `UiPreview` 已经移除。
2. 启动终端 A 的 FastAPI，浏览器检查 `/ready`。**不要**同时运行 CLI，那会多加载一份 BGE-M3，白白占用内存。
3. Unity Play，输入"图书馆几点关门？"，点发送。
4. 回答区应该显示资料原文，来源区出现类似 `[S1] campus/library.md` 的来源信息（顺序和分数可能和示例不同，这是正常的）。
5. 输入带引号的问题，比如 `"图书馆"在哪？`，请求不应该因为 JSON 拼接错误而失败——这验证了用 `JsonConvert` 序列化而不是手工拼字符串的好处。
6. 点发送后立刻点取消：状态应变成"已取消"；再等一会儿，旧结果不应该突然冒出来。
7. 快速连续发送两个问题：上一轮的结果不能覆盖下一轮。如果后端还在忙，可能会收到 429，这是有界并发下的正常错误，等一会儿重试即可。
8. Ctrl+C 停掉 FastAPI 再发送：应该显示服务连接失败，而不是"知识库没有答案"——这是本篇最重要的验收点之一，因为这两种情况对用户的意义完全不同。恢复 FastAPI 后再试一次应该能正常工作。

如果 CPU 检索确实比较慢，退出 Play，在 Console 打印出的本机配置文件里调大 `retrievalTimeoutSeconds`，重新 Play 生效。不要用"加大超时"去掩盖"每次都在重复加载模型"这种真正的问题。

## 七、排查与验收

| 现象 | 检查与解决 |
| --- | --- |
| 找不到 `JsonConvert` | 确认 step09 装好了 Newtonsoft 包，而不是随便复制了外部 DLL |
| 收到 422 | 检查 C# DTO 是否还是 `query`/`top_k` 这两个字段名，问题是不是太长 |
| 连接失败 | 直接用浏览器检查本机 `/ready`，确认地址和端口是否一致 |
| 结果显示了两次 | 场景上可能还挂着 `UiPreview`，或者重复挂载了 `RetrievalPreview` |
| 取消后旧文本又出现了 | 检查每个 `await` 之后的 `generation` 判断有没有被误删 |

- [ ] Unity 能显示真实的检索原文和来源。
- [ ] 错误、无匹配、超时、取消四种情况能清楚区分。
- [ ] 旧一轮结果不会覆盖新一轮。
- [ ] 这一阶段还没有填写或调用 DeepSeek 的密钥。

下一篇：[step11：接入 DeepSeek，形成完整 RAG](step11.md)。
