# Sloth Get Suno Lyrics Skill

[中文](#中文) · [English](#english)

## 中文

### 这是做什么的

告诉 Codex 一首 Suno 歌曲的名字，它会像人一样使用 Computer Use 操作 Chrome：找到歌曲、复制歌词、下载带歌词画面的 MP4、在 Suno 里分离并下载纯人声，最后生成带时间戳的歌词 CSV。

CSV 可直接用于游戏歌词、卡拉 OK、字幕、视频剪辑、音乐可视化等场景：

```csv
id,section,start_time,lyric
lyric-01,Verse 1,00:12.340,第一句歌词
```

### 日常使用的前置条件

Skill 安装完成后，普通用户只需要：

1. 在 Chrome 打开 Suno 并登录自己的账户。
2. 账户能看到目标歌曲，并具备 Video 下载和 Stem 分离权限。
3. 如果 Suno 的人声分离需要积分，账户内有足够积分。
4. 告诉 Codex 准确的歌曲名称。

不需要安装 Demucs，不需要手工准备 CSV，也不需要额外的浏览器扩展。Computer Use 由 Codex Desktop 提供；高精度时间轴会自动发现 Codex 运行环境中已有的本地 `whisper-cli` 与 whisper.cpp 模型（推荐 large-v3）。普通用户不需要自己配置；若运行环境未提供它们，Skill 会明确停止，而不会静默退化成秒级误差。若 Suno 在执行前显示扣积分、付费或升级，Codex 会在最终确认按钮前说明具体费用并征得同意。

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
5. 先比较复制歌词与 MP4 实际显示的歌词；一致时直接继续原流程。
6. 若确认 Suno 在 MP4 中改了歌词，列出时间点、复制文本和视频文本，暂停并请用户决定使用哪一版。
7. 用视频歌词高亮确定粗略顺序，再用歌词提示的 Whisper DTW 逐字时间和人声活动校准演唱起点。
8. 输出并校验网易云四列格式的 start-only CSV、JSON 和 LRC。

人工核对过的 CSV 或 TypeScript 时间轴只用于最后评测，绝不会作为生成输入。这样可以真实检验 Skill 的效果，避免“偷看答案”。

### 歌词不一致时

流水线会先生成 `lyrics-comparison.json` 和便于阅读的 `lyrics-comparison.md`。普通 OCR 错字、漏行或片头标题不会直接当成 Suno 改词；Codex 会先打开报告对应时间点的 MP4 画面核实。

如果画面确实不同，Codex 会停止最终时间轴生成，并把差异逐项列给用户。用户可以选择：

1. 使用 MP4 中实际演唱和显示的歌词（通常最适合游戏、字幕和卡拉 OK）。
2. 继续保留歌曲页复制的歌词，并接受其中可能包含未演唱文字的警告。
3. 提供一版修订歌词。

Codex 不会静默替用户选择，也不会直接把未经画面核对的 OCR 当成最终歌词。若没有真实差异，则完全按原有逻辑继续。

### 实现逻辑

- **先核对文本：** 页面复制歌词是暂定正文；MP4 确认 Suno 实际采用了哪些词。两者真实冲突时由用户决定最终正文。
- **MP4 画面定粗时间：** OCR 观察当前高亮/滚动的歌词行，并与用户选定的歌词按顺序匹配。
- **DTW 定精确起点：** 用规范歌词提示本地 whisper.cpp，关闭 Flash Attention 后启用真正的逐字 DTW；识别结果只用于时间，不改歌词正文。
- **人声否决异常：** 若 DTW 时间回跳到所属 Whisper 句段之前，使用 Lead Vocal onset 否决并修正；不会把普通能量峰值强行替换掉可信的逐字边界。
- **首行与漏行恢复：** 弱匹配首行必须由前置 DTW 和人声 onset 共同确认；连续歌词若共享同一个 DTW 起点，则用 MP4 顺序锚点恢复被漏掉的前一行。
- **不确定性可见：** OCR 漏行、文本不一致、静音边界或大幅移动都会写入警告，不静默猜测。
- **结果可追溯：** 保存源文件哈希、歌词哈希、参数、中间 OCR 结果、置信度和校验报告。

内部统一使用整数毫秒，只保留每行实际开唱的 `start_ms`。当前维护的精度回归要求每一行绝对误差都不超过 500 ms，而不是只让中位数或 p95 达标。人工答案只会在盲生成完成后用于独立评测，绝不会进入生成 prompt、缓存或候选选择。

### 输出目录

```text
song-package/
├── song.mp4
├── vocals.wav
├── lyrics.txt
├── lyrics-comparison.json
├── lyrics-comparison.md
├── timeline.csv      # 主要交付物
├── timeline.json     # 完整证据和置信度
├── timeline.lrc
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

当前实现使用 FFmpeg/FFprobe 读取媒体，在 macOS 使用 Vision OCR，并用 whisper.cpp DTW 生成内容感知的逐字起点。开发者直接运行脚本时需准备 `whisper-cli` 和本地模型，或通过 `--whisper-cli`、`--whisper-model`/对应环境变量指定；通过已配置好的 Codex Skill 使用时不需要普通用户手工安装。

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

No Demucs, hand-written CSV, or separate browser extension is required. Computer Use is provided by Codex Desktop. High-precision timing auto-discovers the local `whisper-cli` and whisper.cpp model provisioned in the Codex runtime (large-v3 recommended); if they are absent, the Skill stops explicitly instead of silently returning second-scale timing. If Suno displays a credit charge, payment, or upgrade, Codex pauses before the final action and asks for confirmation with the visible cost.

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

Codex finds and verifies the exact version, copies the visible untimed lyrics, downloads the Video and Lead Vocal through Suno's UI, compares the copied lyrics with the words shown in the MP4, order-aligns the video highlights after any required user decision, calibrates line starts with lyric-prompted Whisper DTW plus vocal evidence, and validates start-only CSV/JSON/LRC outputs.

A human-reviewed CSV or TypeScript timeline is evaluation-only and is never passed into generation.

### When the lyrics differ

The pipeline first writes `lyrics-comparison.json` and a readable `lyrics-comparison.md`. Codex verifies every candidate against the visible MP4 frame so an OCR typo, missed line, or title card is not mistaken for a Suno rewrite.

If the MP4 visibly contains different lyrics, final timeline generation pauses and Codex shows the user the affected times, copied text, and video text. The user chooses whether to use the words actually performed in the MP4, keep the copied song-page lyrics with explicit warnings, or provide revised lyrics. Codex never chooses silently and never promotes unchecked OCR to final text. With no real difference, the original workflow continues unchanged.

### How it works

- **Text is checked first:** copied page lyrics are provisional; the MP4 confirms what Suno actually generated, and the user resolves any real conflict.
- **The MP4 supplies coarse timing:** OCR observes highlighted or scrolling lyric lines and order-aligns them to the user-selected lyrics.
- **DTW supplies precise starts:** local whisper.cpp runs with the canonical lyric sequence as a prompt and genuine token DTW; recognized words never replace canonical text.
- **The vocal stem rejects impossible timing:** lead-vocal onsets repair DTW backtracks that fall before their containing Whisper segment without overriding valid token boundaries.
- **Intro and omitted-line recovery stays evidence-based:** weak first-line matches require agreement between leading DTW and a vocal onset; consecutive lyrics sharing one DTW start recover the omitted earlier row from its ordered MP4 anchor.
- **Uncertainty stays visible:** missing OCR lines, text mismatches, silent boundaries, and large shifts become review flags.
- **Outputs are traceable:** source hashes, lyric hashes, parameters, OCR evidence, confidence, and validation are retained.

Internal timing uses integer milliseconds and retains only each line's actual singing `start_ms`. The maintained regression requires every line—not merely the median or p95—to have absolute start error at or below 500 ms. Reviewed answers are loaded only after blind generation for evaluation and never enter prompts, caches, or candidate selection.

### Developer note

The default novice flow does not use local vocal separation. `scripts/separate_vocals.py` and `requirements-demucs.txt` remain only as an optional offline developer fallback. Developers running the local pipeline directly need FFmpeg/FFprobe, macOS Vision OCR, `whisper-cli`, and a local whisper.cpp DTW-capable model.

See [SKILL.md](SKILL.md) for the agent workflow and [references/timeline-schema.md](references/timeline-schema.md) for output semantics.
