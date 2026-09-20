# Step 10：Unity 调用本机检索接口

## 目标与前置条件

完成 [step09](step09.md)，Unity 场景能显示中文。启动 Docker、Qdrant 和 step08 的 FastAPI，确认 `/ready` 返回 200。

本篇仍不调用 DeepSeek，只把真正的检索原文显示在 Unity。先验证 HTTP 和 JSON，再增加聊天模型，排错会容易得多。

## 1. 替换场景中的演示组件

退出 Unity Play 模式。在 ChatRoot 的 Inspector 中移除 **UiPreview 组件**，保留 ChatUI。源文件 UiPreview.cs 可以保留，但不要再挂载，否则一次发送会被两个组件同时处理。

后面创建的普通 DTO、客户端类不挂 GameObject。只有本篇的 `RetrievalPreview` 要挂载。

## 2. 新建 `Assets/Scripts/RagModels.cs`

以下路径相对于 `unity-client`。JSON 使用与 Python 一致的字段名，避免某端写 `topK`，另一端只认识 `top_k`。

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

DTO 只有数据，没有 Unity 生命周期函数。不要把每个 DTO 再拆成需要挂载的 MonoBehaviour。

## 3. 新建通用请求封装 `Assets/Scripts/HttpJson.cs`

它会被检索和 DeepSeek 两个客户端共用。所有调用从 Unity 主线程发起，`Task.Yield()` 续执行时回到 Unity 同步上下文，所以可以安全检查请求、取消和更新 UI。

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
            // 明确拒绝跳转，避免带密钥的请求意外发往其他地址。
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
                catch (JsonException) { /* 非 JSON 错误页，只显示状态，不回显全文。 */ }
                throw new ApiRequestException(request.responseCode, message);
            }
            return body;
        }
    }
}
```

### 关键解释

- `using` 在成功、失败和取消时释放请求与处理器。
- 自己用 Stopwatch 管理总超时，能把“用户取消”和“等待超时”显示为不同状态。
- 不通过 `token.Register` 的后台回调直接操作 Unity 对象；每一帧在主线程检查取消。
- 不使用 `Task.Run` 包住 UnityWebRequest，也不使用 `ConfigureAwait(false)` 后再更新 UI。
- Abort 终止客户端等待，但不能保证 Python 已经开始的 GPU/CPU 工作立刻停止。
- 不打印 Authorization、密钥、完整请求正文或完整错误页。

## 4. 新建 `Assets/Scripts/RagClient.cs`

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

不是“HTTP 200 就一定正确”：客户端仍检查 JSON 内容和关键字段，避免把空值一路传给界面。

## 5. 新建临时 `Assets/Scripts/RetrievalPreview.cs`

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

`generation` 是轮次编号。旧请求完成时即使没有及时取消，也不能更新新一轮界面。取消源由创建它的异步方法 finally 释放，避免新任务先把旧任务仍在用的资源 dispose。

## 6. 挂载和测试

1. 在 ChatRoot 添加 `RetrievalPreview`，绑定它的 Ui；确认 UiPreview 已移除。
2. 启动终端 A 的 FastAPI，浏览器检查 `/ready`。不要同时运行 CLI，因为它会多加载一份 BGE-M3。
3. Unity Play，输入“图书馆几点关门？”，点击发送。
4. 回答区域应显示资料原文，来源区域出现 `[S1] campus/library.md` 等来源。顺序和分数可能不同。
5. 输入带引号的问题，例如 `“图书馆”在哪？`，请求不应因 JSON 拼接错误而失败。
6. 点击发送后立即取消：状态变“已取消”；等一会儿，旧结果不应突然出现。
7. 快速发送另一问题：上轮结果不能覆盖下一轮。若后端还忙，可能得到 429；这是有界并发的正常错误，等待后重试。
8. Ctrl+C 停止 FastAPI，再发送：应显示服务连接失败，而不是“知识库没有答案”。恢复 FastAPI 后可再次发送。

如 CPU 检索确实较慢，退出 Play，在 Console 显示的本机配置文件中增加 `retrievalTimeoutSeconds`；重新 Play 读取新值。不要通过增加超时掩盖每次重复加载模型的问题。

## 7. 排查与验收

| 现象 | 检查与解决 |
| --- | --- |
| JsonConvert 找不到 | 确认 step09 安装 Newtonsoft 包，而非随意复制外部 DLL |
| 422 | C# DTO 是否仍使用 `query` 和 `top_k`，问题是否太长 |
| 连接失败 | 浏览器直接检查本机 `/ready`，确认地址和端口 |
| 结果显示两次 | 场景上还挂着 UiPreview 或重复的 RetrievalPreview |
| 取消后旧文本出现 | 检查每个 await 后的 generation 判断，没有误删 |

- [ ] Unity 能显示真实原文和来源。
- [ ] 错误、无匹配、超时、取消可区分。
- [ ] 旧轮次不覆盖新轮次。
- [ ] 在此阶段没有填写或调用 DeepSeek 密钥。

下一篇：[step11：接入 DeepSeek，形成完整 RAG](step11.md)。
