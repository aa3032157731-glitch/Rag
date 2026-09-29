# Step 10：让 Unity 显示真实检索结果

[上一篇](step9.md) · [教程目录](README.md) · [下一篇](step11.md)

## 本篇目标与前置条件

第 9 篇的中文界面已通过验收。启动 Qdrant 和第 8 篇的 FastAPI，浏览器提交一个问题，确认能查到原文，再开始这一篇。

本篇成果：Unity 输入问题，通过 HTTP 得到原文和来源并显示。还不调用聊天模型，也不需要 API Key。

## 1. 清理临时演示组件，安装 JSON 包

退出 Play，在 ChatRoot 上移除 **UiPreview 组件**，保留 ChatUI。UiPreview.cs 可以作为学习记录保留；组件不能继续挂着，否则它会和真实检索同时响应按钮。

打开 Window → Package Manager → + → Add package by name：

- 包名：`com.unity.nuget.newtonsoft-json`
- 版本：`3.2.1`

如果编辑器将名称和版本放在同一个输入框，可填 `com.unity.nuget.newtonsoft-json@3.2.1`。安装后等待 Unity 编译完成。

JSON 序列化将 C# 对象转换成字符串；反序列化把服务器返回的字符串转换回对象。不要自己拼接带引号的问题字符串。

## 2. 到这里再引入本机配置

新建 `unity-client/Assets/Scripts/RuntimeSettings.cs`：

<!-- file: unity-client/Assets/Scripts/RuntimeSettings.cs -->
```csharp
using System;
using System.IO;
using Newtonsoft.Json;
using UnityEngine;

[Serializable]
public sealed class RuntimeSettings
{
    public string retrievalUrl = "http://127.0.0.1:8000/retrieve";
    public int topK = 5;
    public int retrievalTimeoutSeconds = 120;

    public static string FilePath =>
        Path.Combine(Application.persistentDataPath, "rag-settings.json");

    public static RuntimeSettings Load()
    {
        if (!File.Exists(FilePath))
        {
            Directory.CreateDirectory(Application.persistentDataPath);
            File.WriteAllText(FilePath,
                JsonConvert.SerializeObject(new RuntimeSettings(), Formatting.Indented));
        }
        var settings = JsonConvert.DeserializeObject<RuntimeSettings>(
            File.ReadAllText(FilePath));
        if (settings == null)
            throw new InvalidOperationException("本机配置文件没有内容");
        if (settings.retrievalTimeoutSeconds < 1)
            throw new InvalidOperationException("请求超时秒数必须大于零");
        return settings;
    }
}
```

这是普通 C# 类，不挂到场景对象上。第一次使用时会在 `Application.persistentDataPath` 创建 `rag-settings.json`。这个目录由 Unity 管理，与 Assets 不是同一处。

本篇只配置检索地址、最多返回多少条、等待多少秒。第 11 篇才添加聊天服务配置和密钥。

## 3. 写与 Python 对应的数据类

新建 `unity-client/Assets/Scripts/RagModels.cs`：

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
public sealed class RagResponse
{
    public string status;
    public string query;
    public List<RagHit> results;
}
```

这些类叫 DTO，只有用于传输的字段。注意这里必须是 `top_k`，因为 Python 的字段也是这个名字；本机配置里的 `topK` 在 RagClient 中会转换。

用一个文件放这几个小类即可，它们不是 MonoBehaviour，不用挂场景。

## 4. 写共用的 HTTP 请求封装

新建 `unity-client/Assets/Scripts/HttpJson.cs`：

<!-- file: unity-client/Assets/Scripts/HttpJson.cs -->
```csharp
using System;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using UnityEngine.Networking;

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
            request.timeout = timeoutSeconds;
            request.redirectLimit = 0;
            var operation = request.SendWebRequest();
            while (!operation.isDone)
            {
                if (token.IsCancellationRequested)
                {
                    request.Abort();
                    token.ThrowIfCancellationRequested();
                }
                await Task.Yield();
            }
            token.ThrowIfCancellationRequested();
            if (request.result != UnityWebRequest.Result.Success ||
                request.responseCode < 200 || request.responseCode >= 300)
            {
                var message = request.responseCode == 0
                    ? "连接或请求失败：" + request.error
                    : "HTTP " + request.responseCode + "：" + request.error;
                throw new InvalidOperationException(message);
            }
            return request.downloadHandler.text;
        }
    }
}
```

主要过程只有四步：

1. 将对象序列化为 JSON。
2. 设置 UTF-8 请求体和响应接收器。
3. 等待网络完成。
4. 非成功状态抛出异常，成功就返回正文。

`timeout` 使用 Unity 自带功能，不另外维护秒表；`using` 确保请求结束后释放资源。[Unity 请求配置示例](https://docs.unity3d.com/ja/2022.3/Manual/UnityWebRequest-CreatingUnityWebRequests.html)

`bearerKey` 是给下一篇聊天接口预留的可选参数，本篇不传。`redirectLimit=0` 避免携带认证头的请求被重定向到其他地址。

`CancellationToken` 只用于退出 Play 或销毁组件时结束等待，不代表有用户取消按钮。`Task.Yield()` 让等待过程把执行机会还给 Unity；这些方法从 Unity 主线程调用，不套 `Task.Run`。

## 5. 封装检索调用

新建 `unity-client/Assets/Scripts/RagClient.cs`：

<!-- file: unity-client/Assets/Scripts/RagClient.cs -->
```csharp
using System;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;

public sealed class RagClient
{
    private readonly RuntimeSettings settings;

    public RagClient(RuntimeSettings settings)
    {
        this.settings = settings;
    }

    public async Task<RagResponse> RetrieveAsync(string question, CancellationToken token)
    {
        var body = await HttpJson.PostAsync(
            settings.retrievalUrl,
            new RagRequest { query = question, top_k = settings.topK },
            settings.retrievalTimeoutSeconds, token);
        var result = JsonConvert.DeserializeObject<RagResponse>(body);
        if (result == null || result.results == null)
            throw new InvalidOperationException("检索响应缺少 results");
        return result;
    }
}
```

RagClient 负责知道“请求哪个接口、用什么字段、返回什么类型”。它只检查反序列化后是否有 results，不重复检查后端的全部业务规则；JSON 或网络错误交给外层显示。

## 6. 用真实检索替换演示

新建 `unity-client/Assets/Scripts/RetrievalPreview.cs`：

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

    private void Start()
    {
        client = new RagClient(RuntimeSettings.Load());
        Debug.Log("本机配置文件：" + RuntimeSettings.FilePath);
        ui.Submitted += Submit;
    }

    private async void Submit(string question)
    {
        var own = new CancellationTokenSource();
        current = own;
        ui.SetBusy(true);
        ui.SetStatus("正在检索……");
        ui.ShowAnswer("");
        ui.ShowSources("");
        try
        {
            var result = await client.RetrieveAsync(question, own.Token);
            if (this == null || ui == null) return;
            ui.ShowAnswer(result.results.Count == 0
                ? "没有返回检索资料。"
                : string.Join("\n\n", result.results.Select((hit, i) =>
                    "[S" + (i + 1) + "] " + hit.text)));
            ui.ShowSources(string.Join("\n", result.results.Select((hit, i) =>
                "[S" + (i + 1) + "] " + hit.source + " / " + hit.section)));
            ui.SetStatus("检索完成");
        }
        catch (OperationCanceledException) { }
        catch (Exception exc)
        {
            if (this != null && ui != null)
                ui.SetStatus("检索失败：" + exc.Message);
        }
        finally
        {
            current = null;
            own.Dispose();
            if (this != null && ui != null)
                ui.SetBusy(false);
        }
    }

    private void OnDestroy()
    {
        current?.Cancel();
        if (ui != null)
            ui.Submitted -= Submit;
    }
}
```

这是本篇唯一需要新增挂载的组件：

1. 在 ChatRoot 上添加 RetrievalPreview。
2. 把 ChatUI 拖到它的 Ui 字段。
3. 确认场景中已没有 UiPreview 组件。
4. 保存场景，进入 Play。
5. Console 会打印实际配置文件路径，按该路径找到 rag-settings.json。

首次自动生成的内容为：

```json
{
  "retrievalUrl": "http://127.0.0.1:8000/retrieve",
  "topK": 5,
  "retrievalTimeoutSeconds": 120
}
```

地址或超时需要调整时，退出 Play，编辑这个文件，再进入 Play。配置不会每一帧重新读取。

## 7. 理解这里的 async、await 和清理

`Submit` 是按钮事件的入口，因此是 `async void`；RagClient 与 HttpJson 返回 `Task`，调用者可以 await 它们。

开始请求时禁用发送，完成或失败时在 `finally` 恢复。每次请求自己创建并释放取消源；`OnDestroy` 只通知仍在等待的请求取消，防止退出场景后继续更新已销毁 UI。

没有“新问题打断旧问题”的功能，因此也没有轮次编号或旧结果筛选系统。保留的生命周期处理直接对应 Unity 场景会被销毁这一事实。

## 8. 实际运行与验收

1. Unity 中输入“图书馆几点关门？”。
2. 发送按钮在等待时不可点击。
3. 返回后回答区显示原文，来源区显示类似 `[S1] campus/library.md / 开放时间`。
4. 再输入 `"图书馆"在哪？`，确认引号不会破坏 JSON。
5. 停止 FastAPI 后再次发送，应显示连接失败，而不是“没有知识”。
6. 恢复服务后再发送，应可以正常工作。
7. 请求还在等待时退出 Play，界面不应在退出后被旧请求更新。

客户端超时不保证 Python 的推理立即停止。如果超时，先查看后端是否仍在工作，再决定是否重试；避免连续发起多个慢请求。没有自动重试逻辑。

## 为什么这么设计

HTTP 和模型逻辑分开，界面只消费“原文列表”。先显示检索原文再生成回答，能帮助你判断错误到底发生在找资料还是组织答案。

只有一个共享 HttpJson，就能在下一篇复用网络代码。单次请求期间禁用发送，省掉了取消、抢占和轮次控制的大量状态处理。

## 排查与验收

| 现象 | 处理 |
| --- | --- |
| 连接失败 | 用浏览器查看 `/health` 和 `/docs`，核对 URL |
| 422 | 问题是否过长，topK 是否为 1～10 的整数 |
| 缺少 results | 检查是否访问了正确接口，是否使用同一套新版字段 |
| 显示演示答案 | 场景上是否仍挂着 UiPreview |
| 请求超时 | 查看 Python 日志和本机耗时，再调整等待秒数 |

- [ ] 原文来自实际后端，不再是固定演示文字。
- [ ] 来源路径正确。
- [ ] 请求期间发送禁用，成功或失败后恢复。
- [ ] 网络失败不会被显示为“资料不足”。

---

下一篇：[step11：将资料交给聊天模型](step11.md)。
