# Step 09：创建新的 Unity 中文聊天界面

> **本节改动说明**：C# 代码没有做任何改动——这几个脚本本身已经很干净（`Awake` 里对必填字段做了防御性检查、`OnDestroy` 里正确移除了事件监听）。这里只是统一了文档结构，把原来分散的"为什么这么做"归并到每段代码后面。

## 本节目标

完成后你能：在一个全新的 Unity 工程里，正常输入和显示中文，点击发送/取消按钮能触发对应逻辑（先接假数据，真正的检索留到 step10）。

**前置条件**：Python 部分已完成到 step08。本篇先用假数据检查界面，可以先 Ctrl+C 停掉 FastAPI 省内存。**不要**打开或修改你原来的数字人工程——这是一个完全独立的新项目。

## 一、创建新项目

1. Unity Hub → Projects → New project。
2. 选择 **2022.3.62f3** 和 **3D Core** 模板。
3. Location 选 `C:\Users\qqcom\PycharmProjects\Rag`，Project name 填 `unity-client`。确认最终路径没有意外多套一层同名目录。
4. 创建后等导入完成，先不要升级包，也不要迁移旧项目的任何内容。
5. Project 窗口 `Assets` 内创建 `Scenes`、`Scripts`、`Fonts` 三个目录。
6. File → Save As，场景保存为 `Assets/Scenes/Main.unity`。
7. Edit → Preferences → External Tools，把 External Script Editor 指向你已装好的 Rider 或 Visual Studio。
8. Window → Package Manager → `+` → Add package by name，安装 `com.unity.nuget.newtonsoft-json`，版本 `3.2.1`。
9. Window → TextMeshPro → Import TMP Essential Resources，导入基础资源。如果看不到这个菜单，先确认 TextMeshPro 包已经装好。

以上操作只影响这个新工程，PyCharm 那边的 Python 代码不受任何影响。

## 二、准备中文字体

1. 把一个本机可用的 `.ttf` 中文字体复制到 `Assets/Fonts`（比如 `C:\Windows\Fonts\simhei.ttf`，仅用于本机学习；正式分发客户端时要换成允许分发的字体）。
2. 在 Unity 里右键该字体 → Create → TextMeshPro → Font Asset。
3. 选中生成的 Font Asset，Inspector 里把 Atlas Population Mode 设为 **Dynamic**，勾选 Multi Atlas Textures。
4. 所有 TMP 文本以及输入框内部的 Text/Placeholder，都要指定这个 Font Asset。
5. 在 Game 窗口检查"图书馆、校园卡、正在检索"这几个词能不能正常显示——如果是方框，通常不是 JSON 编码出了问题，而是字体缺对应字形。

## 三、创建界面层级

Hierarchy 右键 → UI → Canvas；如果没有自动生成 EventSystem，就手动创建 UI → Event System。Canvas 设为 Screen Space - Overlay；Canvas Scaler 用 Scale With Screen Size，Reference Resolution `1200 × 800`，Match `0.5`。

在 Canvas 下创建一个 Panel，命名 `ChatPanel`，RectTransform 四边拉伸，Left/Right/Top/Bottom 都设 24。添加 Vertical Layout Group：Padding 16，Spacing 10，Child Control Size 的 Width/Height 都勾选，Child Force Expand 只勾选 Width。

在 `ChatPanel` 下按顺序创建这些对象：

| 名称 | 创建方式与组件 | Layout Element |
| --- | --- | --- |
| QuestionInput | UI → TextMeshPro Input Field | Preferred Height=100，Flexible Height=0 |
| Buttons | 空 UI 对象，添加 Horizontal Layout Group | Preferred Height=44 |
| StatusText | UI → Text - TextMeshPro | Preferred Height=32 |
| AnswerScroll | UI → Scroll View | Min Height=180，Flexible Height=1 |
| SourcesScroll | UI → Scroll View | Preferred Height=170，Flexible Height=0 |

具体配置：

1. **QuestionInput**：Line Type 改成 `Multi Line Newline`，Character Limit 设 1000；Placeholder 改为"输入知识库问题"。文字大小 24。
2. **Buttons** 下创建两个 UI → Button - TextMeshPro，命名 `SendButton`、`CancelButton`，按钮文字分别是"发送""取消"。各自加 Layout Element，Preferred Width=120。
3. **StatusText** 字号 20，初始文字"就绪"。
4. **AnswerScroll / SourcesScroll**：Scroll Rect 关闭 Horizontal、开启 Vertical；保留模板自带的 Viewport、Content 和垂直滚动条，去掉横向滚动条。
5. 两者的 Content 锚点都设为水平拉伸、垂直顶部（Min `(0,1)`、Max `(1,1)`），Pivot=`(0.5,1)`，Left/Right/Pos Y 都设 0。
6. Content 加 Vertical Layout Group（Control Child Size 的 Width/Height 都开，Force Expand Height 关）+ Content Size Fitter（Vertical Fit=`Preferred Size`，Horizontal Fit=`Unconstrained`）。
7. Content 下创建 TextMeshPro Text，分别命名 `AnswerText`、`SourcesText`，开启自动换行，Overflow=`Overflow`，字号分别 24/18；不要给文本设固定高度，让布局系统按内容自动算。
8. 把中文 Font Asset 分配给所有文本，**包括** Input Field 内部的 Text 和 Placeholder。

不用手动给 `SendButton` 绑定 On Click 事件——下面的脚本会用代码注册，重复绑定会导致一次点击触发两次发送。

## 四、新建本机运行配置类

文件：`unity-client/Assets/Scripts/RuntimeSettings.cs`。在 Unity 里右键 Assets/Scripts → Create → C# Script，改成这个文件名后双击打开，用下面的内容**完整替换**模板代码。

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

这个类**不**挂到任何 GameObject 上，它就是一个普通的数据类，负责读取本机配置。第一次运行会自动创建一个密钥为空的模板文件，路径在 `Application.persistentDataPath` 下（不在 Assets 目录里，也**不**使用 PlayerPrefs 存 API Key——PlayerPrefs 在 Windows 上是明文写进注册表的，不适合放密钥）。

## 五、新建 UI 脚本

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
        // 允许新问题打断上一轮，具体的取消和轮次检查交给控制器负责。
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

**关键点**：关掉 `richText` 是为了让检索到的原文按字面显示 `<...>` 这类符号，避免用户资料里恰好出现的尖括号被 TMP 当成样式标签解析。用 C# 的 `event` 让 `ChatUI` 只负责"通知有人点了发送/取消"，完全不需要知道下一步是走检索还是走聊天——这是关注点分离，后面 step10/11 挂不同的控制器脚本时，这个类完全不用改。

## 六、新建临时演示脚本

文件：`unity-client/Assets/Scripts/UiPreview.cs`。这是一个**临时**组件，下一篇会把它从场景里移除，但源文件可以留着当参考。

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

## 七、挂载与运行

1. Hierarchy → Create Empty，命名 `ChatRoot`。
2. Add Component 加上 `ChatUI`，把前面创建的输入框、两个按钮、三个文本、`AnswerScroll` 分别拖到对应字段。
3. 再加 `UiPreview`，把 `ChatRoot` 上的 `ChatUI` 拖到它的 Ui 字段。
4. Ctrl+S 保存场景，等 Console 没有编译错误后点顶部 Play。
5. 输入中文并点发送，应该看到演示文字；点取消，状态变成"已取消演示"。
6. Console 会打印真实的 `rag-settings.json` 路径，打开确认模板文件存在。这一步先不用填密钥。
7. 退出 Play 后**再保存一次场景**——Play 期间对 Inspector 的普通修改一般不会被保留，正式绑定要在退出 Play 之后再检查一遍。

## 八、排查与验收

| 现象 | 原因与解决 |
| --- | --- |
| 找不到 TMP 或 Newtonsoft 类型 | 检查包是否装好、Unity 是否编译完成 |
| 中文显示成方框 | 给 Input 的 Text/Placeholder 和输出文本都设置中文字体 |
| ChatUI 字段没绑定 | 逐个字段拖对象，不能只拖一个 `ChatPanel` 了事 |
| 文本超出范围或无法滚动 | 检查 Content 的锚点、Content Size Fitter 配置、ScrollRect 有没有正确绑定 Content |
| 点一次发送触发两次 | Inspector 里的手工 OnClick 和脚本代码可能重复绑定了，清空手工绑定 |

- [ ] 新 Unity 工程能进入 Play 模式，Console 没有编译错误。
- [ ] 中文输入输出正常，长文本可以滚动。
- [ ] 找到本机配置文件，确认密钥没有写进 Assets。

下一篇：[step10：Unity 调用检索服务](step10.md)。
