# Sloth Get Suno Lyrics Skill

[中文](#中文) · [English](#english)

## 中文

### 这是做什么的

告诉 Codex 一首 Suno 歌曲的名字，它会像人一样使用 Computer Use 操作 Chrome：找到歌曲、复制歌词、下载带歌词画面的 MP4、在 Suno 里分离并下载纯人声，最后生成带时间戳的歌词 CSV。

CSV 可直接用于游戏歌词、卡拉 OK、字幕、视频剪辑、音乐可视化等场景：

```csv
id,section,start_time,end_time,lyric
lyric-01,Verse 1,00:12.340,00:15.670,第一句歌词
```

### 日常使用的前置条件

Skill 安装完成后，普通用户只需要：

1. 在 Chrome 打开 Suno 并登录自己的账户。
2. 账户能看到目标歌曲，并具备 Video 下载和 Stem 分离权限。
3. 如果 Suno 的人声分离需要积分，账户内有足够积分。
4. 告诉 Codex 准确的歌曲名称。

不需要安装 Demucs，不需要下载 AI 模型，不需要手工准备 CSV，也不需要额外的浏览器扩展。Computer Use 由 Codex Desktop 提供。若 Suno 在执行前显示扣积分、付费或升级，Codex 会在最终确认按钮前说明具体费用并征得同意。

### 安装

可以让 Codex 直接安装：

> 从 `https://github.com/jhwreal/sloth-getsunolyrics-skill` 安装 sloth-getsunolyrics-skill。

也可以由维护者放入 Codex Skills 目录：

```bash
git clone https://github.com/jhwreal/sloth-getsunolyrics-skill.git \
  "${CODEX_HOME:-$HOME/.codex}/skills/sloth-getsunolyrics-skill"
```

安装后重新打开 Codex 任务，使 Skill 被发现。

以后更新 Skill：

```bash
git -C "${CODEX_HOME:-$HOME/.codex}/skills/sloth-getsunolyrics-skill" pull --ff-only
```

### 使用方法

在 Chrome 登录 Suno 后告诉 Codex：

> 用 sloth-getsunolyrics-skill 处理《歌曲名称》，给我带时间戳的 CSV 歌词。

之后 Codex 会：

1. 用 Computer Use 在 Suno 搜索歌曲；若有同名版本，会根据作者、版本、日期、封面和时长核对。
2. 从歌曲页复制原始歌词，保存为无时间戳的 `lyrics.txt`。
3. 从可见菜单下载歌词 Video MP4。
4. 使用 Suno 的 Get Stems/Extract Stems 下载完整歌曲的 Lead Vocal 人声轨。
5. 用视频歌词高亮确定粗略时间，再用人声活动校准演唱起点。
6. 输出并校验 CSV、JSON、LRC、SRT 和 VTT。

人工核对过的 CSV 或 TypeScript 时间轴只用于最后评测，绝不会作为生成输入。这样可以真实检验 Skill 的效果，避免“偷看答案”。

### 实现逻辑

- **Suno 歌词定文本：** 保留原文、顺序、重复句、大小写、标点和分段。
- **MP4 画面定粗时间：** OCR 观察当前高亮/滚动的歌词行，并与原始歌词按顺序匹配。
- **人声校时间：** 在视频给出的窗口内分析 Lead Vocal 的能量和起音，避免时间点落在无人声区域。
- **不确定性可见：** OCR 漏行、文本不一致、静音边界或大幅移动都会写入警告，不静默猜测。
- **结果可追溯：** 保存源文件哈希、歌词哈希、参数、中间 OCR 结果、置信度和校验报告。

内部统一使用整数毫秒和半开区间 `[start_ms, end_ms)`。当前两首人工样本的验收线是行级起点中位误差小于 500 ms；更严格的工程目标是中位数不超过 200 ms、95 分位不超过 500 ms。只有在独立人工答案上测量后才会报告达标。

### 输出目录

```text
song-package/
├── song.mp4
├── vocals.wav
├── lyrics.txt
├── timeline.csv      # 主要交付物
├── timeline.json     # 完整证据和置信度
├── timeline.lrc
├── timeline.srt
├── timeline.vtt
├── manifest.json
├── validation.json
└── work/             # OCR 等可复核中间产物
```

### 开发者说明

普通用户无需本地音频工具。仓库仍保留 `scripts/separate_vocals.py` 和 `requirements-demucs.txt`，仅供开发者在 Suno Stem 不可用时做离线回归，不属于默认流程。

本地流水线入口：

```bash
python3 /absolute/path/to/sloth-getsunolyrics-skill/scripts/process_song.py \
  --video /path/song.mp4 \
  --vocals /path/lead-vocal.wav \
  --lyrics /path/lyrics.txt \
  --output-dir /path/song-package
```

当前实现使用 FFmpeg/FFprobe 读取媒体，并在 macOS 使用 Vision OCR。开发或自行运行脚本时需自行准备这些运行环境；通过已配置好的 Codex Skill 使用时，不需要普通用户手工安装它们。

## English

### What it does

Tell Codex the name of a Suno song. It uses Computer Use to operate Chrome like a person: find the song, copy its lyrics, download the lyric MP4, create and download the Suno lead-vocal stem, and produce a timestamped lyric CSV.

The CSV is ready for games, karaoke, subtitles, editing, and music visualization.

### Everyday prerequisites

Once the Skill is installed, the user only needs:

1. Chrome open with a logged-in Suno account.
2. Access to the target song, Video download, and stem extraction.
3. Enough Suno credits if stem extraction consumes credits.
4. The exact song name.

No Demucs, model download, hand-written CSV, or separate browser extension is required. Computer Use is provided by Codex Desktop. If Suno displays a credit charge, payment, or upgrade, Codex pauses before the final action and asks for confirmation with the visible cost.

### Installation

Ask Codex:

> Install sloth-getsunolyrics-skill from `https://github.com/jhwreal/sloth-getsunolyrics-skill`.

Or clone it into the Codex Skills directory:

```bash
git clone https://github.com/jhwreal/sloth-getsunolyrics-skill.git \
  "${CODEX_HOME:-$HOME/.codex}/skills/sloth-getsunolyrics-skill"
```

Start a new Codex task after installation so the Skill is discovered.

To update it later:

```bash
git -C "${CODEX_HOME:-$HOME/.codex}/skills/sloth-getsunolyrics-skill" pull --ff-only
```

### Usage

With Suno logged in, say:

> Use sloth-getsunolyrics-skill for “Song Name” and give me timestamped lyrics as CSV.

Codex finds and verifies the exact version, copies the visible untimed lyrics, downloads the Video and Lead Vocal through Suno's UI, aligns the video highlights to the lyrics, calibrates line starts with vocal activity, and validates CSV/JSON/LRC/SRT/VTT outputs.

A human-reviewed CSV or TypeScript timeline is evaluation-only and is never passed into generation.

### How it works

- **Suno lyrics define the text:** wording, order, repeats, case, punctuation, and sections are preserved.
- **The MP4 supplies coarse timing:** OCR observes highlighted or scrolling lyric lines and order-aligns them to the canonical lyrics.
- **The vocal stem calibrates timing:** lead-vocal energy and onsets refine boundaries and reject silent candidates.
- **Uncertainty stays visible:** missing OCR lines, text mismatches, silent boundaries, and large shifts become review flags.
- **Outputs are traceable:** source hashes, lyric hashes, parameters, OCR evidence, confidence, and validation are retained.

Internal timing uses integer milliseconds and half-open intervals `[start_ms, end_ms)`. The two current human-reviewed fixtures use a median start-error acceptance line below 500 ms; the stricter engineering target is median ≤200 ms and p95 ≤500 ms. Accuracy claims are made only after comparison with an independent human-reviewed timeline.

### Developer note

The default novice flow does not use local vocal separation. `scripts/separate_vocals.py` and `requirements-demucs.txt` remain only as an optional offline developer fallback. Developers running the local pipeline directly need FFmpeg/FFprobe and macOS Vision OCR available.

See [SKILL.md](SKILL.md) for the agent workflow and [references/timeline-schema.md](references/timeline-schema.md) for output semantics.
