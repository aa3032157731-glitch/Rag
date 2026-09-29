# Step 9：先做出能输入和显示中文的 Unity 界面

[上一篇](step8.md) · [教程目录](README.md) · [下一篇](step10.md)

## 本篇目标与前置条件

Python 已经能通过 HTTP 检索。本篇先暂停 API，专心处理 Unity 的字体、输入、布局和按钮，使用固定文字验证界面。

这一篇不需要 API Key，不访问网络，不读取运行配置，也不安装 JSON 包。

## 1. 创建新的 Unity 工程

1. Unity Hub → Projects → New project。
2. 选择 `2022.3.62f3`，模板选 `3D Core`。
3. Location 选择 `C:\Users\qqcom\PycharmProjects\Rag`，Project name 填 `unity-client`。
4. 确认最终路径为 `Rag\unity-client`，创建后等待导入完成。
5. 在 Assets 下创建 `Scenes`、`Scripts`、`Fonts` 三个文件夹。
6. 保存当前场景为 `Assets/Scenes/Main.unity`。
7. 在 Edit → Preferences → External Tools 中选择自己使用的 C# 编辑器。

用新的工程完成练习，不需要把代码放回之前的数字人工程。后文 C# 文件都在新工程的 Assets/Scripts 下。

## 2. 先解决中文字体

1. 执行 Window → TextMeshPro → Import TMP Essential Resources。如果已导入就继续。
2. 将本机可用的中文 `.ttf` 字体复制到 Assets/Fonts。可用 Windows 字体做本机练习；正式分发时选允许分发的字体。
3. 在 Unity 中右键字体 → Create → TextMeshPro → Font Asset。
4. 选中生成的 Font Asset，将 Atlas Population Mode 设为 **Dynamic**，开启 Multi Atlas Textures。
5. 后面所有 TMP 文本都指定这个 Font Asset，包括输入框的 Text 和 Placeholder。

先理解一点：中文显示成方框时，往往是字体没有相应字形，不一定是后端编码错误。

## 3. 创建 Canvas 和布局

Hierarchy 右键 → UI → Canvas。若没有 EventSystem，再新建一个 EventSystem。

Canvas 设置：

| 项目 | 值 |
| --- | --- |
| Render Mode | Screen Space - Overlay |
| Canvas Scaler / UI Scale Mode | Scale With Screen Size |
| Reference Resolution | 1200 × 800 |
| Match | 0.5 |

在 Canvas 下创建 UI → Panel，命名 `ChatPanel`：

1. RectTransform 四边拉伸，Left/Right/Top/Bottom 均设为 24。
2. 添加 Vertical Layout Group，Padding 16，Spacing 10。
3. 勾选 Child Control Size 的 Width/Height，Child Force Expand 只勾 Width。

在 ChatPanel 下按顺序建立：

| 名称 | 创建方式 | Layout Element |
| --- | --- | --- |
| QuestionInput | UI → TextMeshPro Input Field | Preferred Height=100 |
| SendButton | UI → Button - TextMeshPro | Preferred Height=44 |
| StatusText | UI → Text - TextMeshPro | Preferred Height=36 |
| AnswerScroll | UI → Scroll View | Min Height=180，Flexible Height=1 |
| SourcesScroll | UI → Scroll View | Preferred Height=180 |

本版只有“发送”，没有取消按钮。请求期间由代码禁用发送，避免多轮请求同时竞争界面。

## 4. 设置输入与两块滚动区域

**QuestionInput：**

- Line Type 选择 Multi Line Newline。
- Character Limit 设置为 1000。
- Placeholder 改成“请输入完整问题，例如：图书馆几点关门？”。
- Text 和 Placeholder 的字体都设为中文 Font Asset，字号可用 24。

**SendButton：** 按钮文字改成“发送”。Inspector 的 On Click 列表保持为空，后面由脚本绑定，避免一次点击调用两次。

**StatusText：** 字号 20，初始文字“就绪”。

**AnswerScroll 与 SourcesScroll：**

1. 保留默认 Viewport、Content、垂直滚动条，关闭 Scroll Rect 的 Horizontal，开启 Vertical。
2. 删除不用的横向滚动条对象。
3. Content 锚点水平拉伸、垂直顶部：Min=(0,1)、Max=(1,1)，Pivot=(0.5,1)，Left/Right/Pos Y 为 0。
4. Content 添加 Vertical Layout Group：控制子物体 Width/Height，Force Expand Height 关闭。
5. Content 添加 Content Size Fitter：Vertical Fit=Preferred Size，Horizontal Fit=Unconstrained。
6. 每个 Content 下新建一个 TextMeshPro Text，分别命名 `AnswerText`、`SourcesText`。
7. 两个文本都启用自动换行，Overflow=Overflow，字号分别为 24/18，指定中文字体，不设置固定文本高度。

回答区显示较长文字，来源区显示原文出处。Content 随文字变高，ScrollRect 才有内容可滚动。

## 5. 编写 ChatUI

新建 `unity-client/Assets/Scripts/ChatUI.cs`，完整替换 Unity 自动生成的模板：

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
    public TMP_Text statusText;
    public TMP_Text answerText;
    public TMP_Text sourcesText;
    public ScrollRect answerScroll;

    public event Action<string> Submitted;

    private void Awake()
    {
        if (questionInput == null || sendButton == null || statusText == null ||
            answerText == null || sourcesText == null || answerScroll == null)
            throw new InvalidOperationException("请先绑定 ChatUI 的全部 Inspector 字段");

        statusText.richText = false;
        answerText.richText = false;
        sourcesText.richText = false;
        sendButton.onClick.AddListener(Submit);
        SetBusy(false);
        SetStatus("就绪");
        ShowAnswer("");
        ShowSources("");
    }

    private void Submit()
    {
        var question = questionInput.text.Trim();
        if (question.Length == 0)
        {
            SetStatus("请输入完整问题");
            return;
        }
        Submitted?.Invoke(question);
    }

    public void SetBusy(bool busy)
    {
        sendButton.interactable = !busy;
    }

    public void SetStatus(string text)
    {
        statusText.text = text;
    }

    public void ShowAnswer(string text)
    {
        answerText.text = text;
        Canvas.ForceUpdateCanvases();
        answerScroll.verticalNormalizedPosition = 1f;
    }

    public void ShowSources(string text)
    {
        sourcesText.text = text;
    }

    private void OnDestroy()
    {
        if (sendButton != null)
            sendButton.onClick.RemoveListener(Submit);
    }
}
```

这里先学三个概念：

- `MonoBehaviour` 是可挂到场景对象上的组件。
- public 字段可以在 Inspector 中拖入 UI 对象。
- `Submitted` 是“用户已经提交问题”的通知。ChatUI 不决定后面是显示假数据、检索还是调用模型。

`Awake` 在组件初始化时设置界面；`OnDestroy` 在对象销毁时解除按钮事件。关闭 richText，是为了让资料中的尖括号按文字显示，而不是被解析成样式。

## 6. 用最简单的预览组件验证

新建 `unity-client/Assets/Scripts/UiPreview.cs`：

<!-- file: unity-client/Assets/Scripts/UiPreview.cs -->
```csharp
using UnityEngine;

public sealed class UiPreview : MonoBehaviour
{
    public ChatUI ui;

    private void Start()
    {
        ui.Submitted += Preview;
    }

    private void Preview(string question)
    {
        ui.ShowAnswer("你输入的是：" + question +
            "\n这只是界面演示，还没有检索或调用聊天模型。");
        ui.ShowSources("演示来源：campus/library.md");
        ui.SetStatus("演示完成，可以继续输入");
    }

    private void OnDestroy()
    {
        if (ui != null)
            ui.Submitted -= Preview;
    }
}
```

现在挂载：

1. Hierarchy → Create Empty，命名 `ChatRoot`。
2. 添加 ChatUI 组件。
3. 从 Hierarchy 把 QuestionInput、SendButton、StatusText、AnswerText、SourcesText 和 AnswerScroll 拖到 ChatUI 对应字段。
4. 在同一个 ChatRoot 添加 UiPreview。
5. 将 ChatUI 组件拖到 UiPreview 的 Ui 字段。
6. 保存场景，确认 Console 没有编译错误，然后进入 Play。

输入“图书馆几点关门？”并点击发送，应看到原样回显和演示来源。继续输入第二个问题，应仍可发送。这个同步预览没有网络等待，所以不需要把按钮设为忙碌。

## 7. 做完界面验收再接网络

- 输入中文，确认没有方框。
- 输入仅有空格的内容，应提示输入完整问题。
- 将预览文字临时加长，检查换行和滚动；完成后恢复。
- 退出 Play 后检查 Inspector 引用仍在，并保存场景。

Play 期间的普通 Inspector 修改一般不会保存到场景。需要永久修改的绑定在退出 Play 后再做一次。

## 为什么这么设计

把界面单独验证，可以先排除字体、对象绑定和滚动布局问题。下一篇接 HTTP 后，如果网络返回正确却显示异常，就知道应检查 UI；如果这里都没通过，就不必怀疑模型。

ChatUI 只关心显示和输入，后面替换控制器就能接入真实业务。临时 UiPreview 只负责本篇演示，第 10 篇会从场景移除。

## 排查与验收

| 现象 | 处理 |
| --- | --- |
| 找不到 TMP 类型 | 导入 TMP 资源，确认包存在且 Unity 编译完成 |
| 只有部分中文显示 | 检查字体的 Dynamic 设置，以及输入框 Text/Placeholder 的字体 |
| 组件字段没绑定 | 拖入正确对象的组件，不是只拖整个 ChatPanel |
| 长文字不能滚动 | 检查 Content 高度、布局组件和 ScrollRect 的 Content 引用 |
| 一次发送显示两次 | 检查手动 OnClick、重复组件或重复事件绑定 |

- [ ] 能输入、显示和滚动中文内容。
- [ ] 输入两次问题都能看到结果。
- [ ] 场景中只有一个 ChatUI 和一个 UiPreview。
- [ ] 明白本篇的来源和回答都是演示文字。

---

下一篇：[step10：用 Unity 调用真实检索](step10.md)。
