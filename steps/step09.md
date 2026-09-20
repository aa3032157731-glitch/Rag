# Step 09：创建新的 Unity 中文聊天界面

## 目标与前置条件

Python 部分已完成到 step08。本篇先用假数据检查界面，所以可以 Ctrl+C 暂停 FastAPI，降低内存占用。不要打开或修改原数字人工程。

## 1. 创建新项目

1. Unity Hub → Projects → New project。
2. 选择 **2022.3.62f3** 和 **3D Core** 模板。
3. Location 选择 `C:\Users\qqcom\PycharmProjects\Rag`，Project name 填 `unity-client`。确认最终路径没有多套一层同名目录。
4. 创建后等待导入完成；先不要升级包或迁移旧项目。
5. Project 窗口 `Assets` 内创建 `Scenes`、`Scripts`、`Fonts`。
6. File → Save As，将场景保存为 `Assets/Scenes/Main.unity`。
7. Edit → Preferences → External Tools，选择已安装的 Rider 或 Visual Studio 作为 External Script Editor。
8. Window → Package Manager → `+` → Add package by name，安装 `com.unity.nuget.newtonsoft-json`，版本 `3.2.1`。
9. Window → TextMeshPro → Import TMP Essential Resources，导入基础资源。若没有该菜单，先确认项目已安装 TextMeshPro 包。

这些操作只针对新工程。Unity 负责编译 C#，PyCharm 继续负责 Python。

## 2. 准备中文字体

1. 将本机可用于测试的 `.ttf` 中文字体复制到 `Assets/Fonts`。例如若存在，可使用 `C:\Windows\Fonts\simhei.ttf`。只做本机学习；将来分发客户端时使用允许分发的字体。
2. 在 Unity Project 中右键字体 → Create → TextMeshPro → Font Asset。
3. 选中新 Font Asset，在 Inspector 将 Atlas Population Mode 设置为 **Dynamic**，允许 Multi Atlas Textures。
4. 后面所有 TMP 文本和输入框内部的 Text/Placeholder 都指定此 Font Asset。
5. 在 Game 窗口检查 `图书馆、校园卡、正在检索` 能否显示。方框不是 JSON 编码问题，通常是字体没有对应字形。

## 3. 创建界面层级

Hierarchy 右键 → UI → Canvas；若未自动生成 EventSystem，创建 UI → Event System。Canvas：Screen Space - Overlay；Canvas Scaler：Scale With Screen Size，Reference Resolution `1200 × 800`，Match `0.5`。

在 Canvas 下创建 Panel 命名 `ChatPanel`。RectTransform 四边拉伸，Left/Right/Top/Bottom 都设为 24。添加 Vertical Layout Group：Padding 16，Spacing 10，Child Control Size 的 Width/Height 勾选，Child Force Expand Width 勾选、Height 不勾选。

在 ChatPanel 下按顺序创建以下对象：

| 名称 | 创建方式与组件 | Layout Element |
| --- | --- | --- |
| QuestionInput | UI → TextMeshPro Input Field | Preferred Height=100，Flexible Height=0 |
| Buttons | 空 UI 对象，添加 Horizontal Layout Group | Preferred Height=44 |
| StatusText | UI → Text - TextMeshPro | Preferred Height=32 |
| AnswerScroll | UI → Scroll View | Min Height=180，Flexible Height=1 |
| SourcesScroll | UI → Scroll View | Preferred Height=170，Flexible Height=0 |

进一步配置：

1. QuestionInput：Line Type=`Multi Line Newline`，Character Limit=1000；Placeholder 改为“输入知识库问题”。文字大小 24。
2. Buttons 下创建两个 UI → Button - TextMeshPro，命名 `SendButton`、`CancelButton`，按钮文字分别为“发送”“取消”。按钮各添加 Layout Element，Preferred Width=120。
3. StatusText 字号 20，初始文字“就绪”。
4. 对 AnswerScroll 和 SourcesScroll：Scroll Rect 关闭 Horizontal，开启 Vertical；保留模板已绑定的 Viewport、Content 和垂直滚动条，移除或禁用横向滚动条。
5. 各自的 Content：锚点设为水平拉伸、垂直顶部（Min `(0,1)`、Max `(1,1)`），Pivot=`(0.5,1)`，Left/Right/Pos Y=0。
6. Content 添加 Vertical Layout Group：Control Child Size Width/Height 开启，Force Expand Height 关闭；再添加 Content Size Fitter，Vertical Fit=`Preferred Size`、Horizontal Fit=`Unconstrained`。
7. Content 下创建 TextMeshPro Text，分别命名 `AnswerText`、`SourcesText`。开启自动换行，Overflow=`Overflow`，字号 24/18；不要给文本固定高度，让布局系统按内容计算。
8. 将中文 Font Asset 分配给所有文本，包括 Input Field 里的 Text 和 Placeholder。

不用手动设置 SendButton 的 On Click 事件，下面的脚本会注册它。重复绑定会导致一次点击发送两次。

## 4. 新建本机运行配置类

文件：`unity-client/Assets/Scripts/RuntimeSettings.cs`。Unity Project 中 Assets/Scripts 右键 Create → C# Script，改为该名称后双击，用以下内容完整替换模板。

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
    public string chatUrl = "https://api.deepseek.com/chat/completions";
    public string chatModel = "deepseek-flash";
    public string apiKey = "";
    public int chatTimeoutSeconds = 120;

    public static string FilePath => Path.Combine(Application.persistentDataPath, "rag-settings.json");

    public static RuntimeSettings Load()
    {
        if (!File.Exists(FilePath))
        {
            Directory.CreateDirectory(Application.persistentDataPath);
            File.WriteAllText(FilePath, JsonConvert.SerializeObject(new RuntimeSettings(), Formatting.Indented));
        }
        var value = JsonConvert.DeserializeObject<RuntimeSettings>(File.ReadAllText(FilePath));
        if (value == null) throw new InvalidOperationException("配置文件为空");
        value.Validate();
        return value;
    }

    public void Validate()
    {
        if (!Uri.TryCreate(retrievalUrl, UriKind.Absolute, out var retrieval) ||
            (retrieval.Scheme != "http" && retrieval.Scheme != "https"))
            throw new InvalidOperationException("retrievalUrl 必须是 HTTP 或 HTTPS 地址");
        if (!Uri.TryCreate(chatUrl, UriKind.Absolute, out var chat) || chat.Scheme != "https")
            throw new InvalidOperationException("chatUrl 必须是 HTTPS 地址");
        if (topK < 1 || topK > 10) throw new InvalidOperationException("topK 必须在 1～10");
        if (retrievalTimeoutSeconds < 1 || chatTimeoutSeconds < 1)
            throw new InvalidOperationException("超时秒数必须大于零");
        if (string.IsNullOrWhiteSpace(chatModel)) throw new InvalidOperationException("chatModel 不能为空");
    }
}
```

这个类不挂到 GameObject。它是普通数据类，读取本机配置。第一次运行创建空密钥模板，文件在 `Application.persistentDataPath` 下，不在 Assets，也不使用 PlayerPrefs 存 API Key。

## 5. 新建 UI 脚本

文件：`unity-client/Assets/Scripts/ChatUI.cs`。

<!-- file: unity-client/Assets/Scripts/ChatUI.cs -->
```csharp
using System;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

public sealed class ChatUI : MonoBehaviour
{
    public TMP_InputField questionInput;
    public Button sendButton;
    public Button cancelButton;
    public TMP_Text statusText;
    public TMP_Text answerText;
    public TMP_Text sourcesText;
    public ScrollRect answerScroll;

    public event Action<string> Submitted;
    public event Action Cancelled;

    private void Awake()
    {
        if (questionInput == null || sendButton == null || cancelButton == null ||
            statusText == null || answerText == null || sourcesText == null || answerScroll == null)
            throw new InvalidOperationException("ChatUI Inspector 字段尚未全部绑定");
        statusText.richText = false;
        answerText.richText = false;
        sourcesText.richText = false;
        sendButton.onClick.AddListener(Submit);
        cancelButton.onClick.AddListener(Cancel);
        SetBusy(false);
        ShowAnswer("");
        ShowSources("");
        SetStatus("就绪");
    }

    private void Submit()
    {
        var text = questionInput.text.Trim();
        if (text.Length == 0) { SetStatus("请输入问题"); return; }
        Submitted?.Invoke(text);
    }

    private void Cancel() => Cancelled?.Invoke();

    public void SetBusy(bool busy)
    {
        // 允许新问题打断上一轮，由控制器负责取消和轮次检查。
        sendButton.interactable = true;
        cancelButton.interactable = busy;
    }

    public void SetStatus(string text) => statusText.text = text ?? "";

    public void ShowAnswer(string text)
    {
        answerText.text = text ?? "";
        Canvas.ForceUpdateCanvases();
        answerScroll.verticalNormalizedPosition = 1f;
    }

    public void ShowSources(string text) => sourcesText.text = text ?? "";

    private void OnDestroy()
    {
        if (sendButton != null) sendButton.onClick.RemoveListener(Submit);
        if (cancelButton != null) cancelButton.onClick.RemoveListener(Cancel);
    }
}
```

关掉 richText 是为了按原文显示 `<...>`，避免用户资料被当成 TMP 样式标签。`event` 让 UI 只负责通知，不需要知道下一步是检索还是聊天。

## 6. 新建临时演示脚本

文件：`unity-client/Assets/Scripts/UiPreview.cs`。这是临时组件，下一篇从场景移除，但可以保留源文件用于参考。

<!-- file: unity-client/Assets/Scripts/UiPreview.cs -->
```csharp
using UnityEngine;

public sealed class UiPreview : MonoBehaviour
{
    public ChatUI ui;

    private void Start()
    {
        RuntimeSettings.Load();
        Debug.Log("本机配置文件：" + RuntimeSettings.FilePath);
        ui.Submitted += Preview;
        ui.Cancelled += Cancel;
    }

    private void Preview(string question)
    {
        ui.SetBusy(true);
        ui.SetStatus("界面演示：没有调用模型");
        ui.ShowAnswer("你输入的是：" + question + "\n这是临时演示文本。");
        ui.ShowSources("演示来源：campus/library.md");
    }

    private void Cancel()
    {
        ui.SetBusy(false);
        ui.SetStatus("已取消演示");
    }

    private void OnDestroy()
    {
        if (ui == null) return;
        ui.Submitted -= Preview;
        ui.Cancelled -= Cancel;
    }
}
```

## 7. 挂载与运行

1. Hierarchy → Create Empty，命名 `ChatRoot`。
2. Add Component 添加 `ChatUI`，将前面创建的输入框、两个按钮、三个文本、AnswerScroll 分别拖到对应字段。
3. 再添加 `UiPreview`，把 ChatRoot 上的 ChatUI 拖到它的 Ui 字段。
4. Ctrl+S 保存场景，等待 Console 无编译错误，点击顶部 Play。
5. 输入中文并点击发送，应看到演示文字；点击取消，状态变为“已取消演示”。
6. Console 会显示真实的 `rag-settings.json` 路径，打开确认模板存在。暂时不用填密钥。
7. 退出 Play 后再次保存场景。Play 期间对 Inspector 的普通修改通常不会保存，正式绑定在退出后检查。

## 8. 排查与验收

| 现象 | 原因与解决 |
| --- | --- |
| 类型找不到 TMP 或 Newtonsoft | 检查包安装与导入完成，等待 Unity 编译 |
| 中文方框 | 给 Input Text/Placeholder 和输出文本都设置中文字体 |
| ChatUI 字段未绑定 | 按字段逐一拖入对象，不能只拖 ChatPanel |
| 文本超出或不能滚动 | 检查 Content 的锚点、Content Size Fitter、ScrollRect 的 Content 绑定 |
| 点击发送出现两次 | Inspector OnClick 与脚本可能重复绑定；清空手工 OnClick |

- [ ] 新 Unity 工程能 Play，Console 无编译错误。
- [ ] 输入和输出中文正常，长文本可滚动。
- [ ] 找到本机配置文件，未把密钥写入 Assets。

下一篇：[step10：Unity 调用检索服务](step10.md)。
