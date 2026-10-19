# 漫画朗读 · GPT-SoVITS

[介绍视频（哔哩哔哩）：【工具】让任意 GPT-SoVITS 模型朗读任意漫画](https://www.bilibili.com/video/BV1ooan6iEgm/)

在 Android 上打开漫画，点击浮窗即可开始朗读；读完还可以自动翻页。项目采用电脑端与 Android 端分离的设计，两端通过局域网配合，使用时电脑需保持开机。

## 电脑端与 Android 端分工

- **Android 端**负责截取当前屏幕、提供朗读浮窗、播放返回的语音，并在开启自动翻页后操作屏幕。
- **电脑端**接收截图，用 RapidOCR 识字、Magi v2 分析分镜和阅读顺序，再用 GPT-SoVITS 合成语音；开启语音完整性检查时，还会用 ASR 复核朗读结果。

模型和主要计算都在电脑端，Android 设备无需安装这些模型。截图与语音在局域网内的两端之间传送，不依赖云端处理。

## 使用的主要模型

- **RapidOCR（PP-OCRv6）** 识别漫画页面中的文字。
- **Magi v2** 检测分镜和文字区域，辅助确定阅读顺序。
- **GPT-SoVITS** 用所选角色的声音朗读文字。你可以把自己喜欢的、与当前 GPT-SoVITS 运行版本兼容的语音模型放进电脑端模型目录，在 Android 应用中选择；“蔚蓝档案·日奈”只是项目提供的可复现示例。模型需有 GPT `.ckpt`、SoVITS `.pth` 和参考音频，切换时会实际加载并试读。

## 效果预览

<p align="center">
  <img src="docs/readme-assets/manga-page.jpg" alt="漫画页面预览" width="420">
</p>

<audio controls preload="metadata" src="https://raw.githubusercontent.com/adadsws/manga-reader-gpt-sovits/main/docs/readme-assets/reading-preview.wav">
  当前页面不支持直接播放音频。
</audio>

▶ [下载试听音频](docs/readme-assets/reading-preview.wav)

| 连接与设置 | 选择角色 |
|---|---|
| <img src="docs/readme-assets/settings.png" alt="连接与设置" width="360"> | <img src="docs/readme-assets/models.png" alt="选择角色" width="360"> |
| 自动翻页设置 | 朗读浮窗 |
| <img src="docs/readme-assets/advanced.png" alt="自动翻页设置" width="360"> | <img src="docs/readme-assets/overlay.png" alt="朗读浮窗" width="360"> |

## 第一次准备

需要 Windows 电脑和 NVIDIA 显卡。先按当前电脑修改 `config/runtime.json`、`config/reader.json` 和 `config/tts.yaml` 中的路径，再在项目目录依次运行：

```powershell
python tools/download_blue_archive_hina.py
powershell -NoProfile -ExecutionPolicy Bypass -File tools/prepare_runtime.ps1
python tools/prepare_layout.py
```

第一条命令只下载“蔚蓝档案·日奈”这个示例角色，已有兼容的 GPT-SoVITS 语音模型时可以跳过；第二、三条命令准备运行环境和漫画分析模型。

模型来源及重新下载方法见[日奈模型说明](models/gpt-sovits/蔚蓝档案-日语.md)和[Magi 模型说明](models/layout/magiv2.md)。

## 路线一：安卓模拟器

项目使用 Google 官方 Android Emulator，不使用第三方模拟器。

首次使用时，在项目目录运行：

```powershell
python tools/prepare_android.py
python tools/prepare_avd.py
python tools/build_android.py
```

以后双击 [启动电脑服务和安卓模拟器.bat](启动电脑服务和安卓模拟器.bat)，等待应用显示“电脑已连接 · 版本 4”。漫画图片可以直接拖进模拟器窗口打开。启动器会把模拟器窗口移入当前显示器的可用区域；窗口过高时自动等比例缩小，标题栏不会再跑到屏幕上方。

单独双击 [仅启动安卓模拟器.bat](仅启动安卓模拟器.bat) 会先停止本项目由启动器记录的电脑服务，因此安卓应显示“电脑未连接”；它不会结束无法确认属于本项目的 Python 进程。

## 路线二：真实手机

1. 让电脑和手机连接同一个 Wi-Fi，不要使用访客网络。
2. 把 [reader.apk](reader.apk) 发送到手机并安装；以后更新仍安装这个文件。
3. 双击 [仅启动电脑服务.bat](仅启动电脑服务.bat)。
4. 把电脑窗口显示的连接地址和配对口令填入手机应用，等待“电脑已连接”。

真机路线仍在完善；遇到问题时建议先用模拟器确认电脑端能够正常运行。

## 开始朗读

模拟器和真实手机的操作相同：

1. 选择角色并调整朗读设置。
2. 首次使用时点 `1 允许浮窗`，在系统设置中授权后返回应用。
3. 需要自动翻页时，点 `2 开启翻页无障碍`，选择“漫画朗读”并允许完全控制设备；手动翻页可跳过。
4. 点 `3 保存并启动朗读浮窗`，确认系统的整屏共享；Android 14 及更高版本已由 Reader 固定为“整个屏幕”，不会误选只共享单个应用。
5. 打开漫画，点浮窗上的 `▶ 重新开始`。

浮窗和无障碍权限通常只需授权一次；“整个屏幕”需要在启动朗读时确认。

开启自动翻页后，如果某一页没有识别到文字，Reader 会直接尝试翻到下一页；翻页后画面不变时仍会正常停止，避免末页无限重试。

高级选项中的历史复用默认读取同一匹配条件下最新的未隐藏记录。关闭“匹配历史结果时跳过处理”可强制重新处理；开启“隐藏本次记录”时，新结果仍会保存，但以后匹配历史时会跳过它。配对口令按设置要求在列表和编辑框中直接显示，请不要在不可信设备上保存真实口令。

## 常见问题

- 手机连不上：检查 Wi-Fi、连接地址和配对口令，并关闭访客网络或设备隔离。
- 显示黄色版本警告：安卓与电脑端版本不同，但连接和朗读仍可继续；空闲时更新较旧的一端即可。旧版 Android 不会上报版本，电脑窗口也会提示版本警告。
- 读错或漏读：放大漫画后重新开始；复杂字体、极小文字和特殊分镜仍可能识别错误。
- 语音失败：点浮窗中的“查看完整错误”，同时检查电脑窗口中的提示。
- 停止电脑服务：在项目目录运行 `仅启动电脑服务.bat stop`。

版本变化见[更新记录](CHANGELOG.md)。
