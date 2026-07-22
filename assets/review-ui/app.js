"use strict";

const $ = (selector) => document.querySelector(selector);

const elements = {
  songList: $("#songList"),
  songCount: $("#songCount"),
  songTitle: $("#songTitle"),
  saveState: $("#saveState"),
  media: $("#media"),
  videoWrap: $("#videoWrap"),
  toggleVideoButton: $("#toggleVideoButton"),
  playButton: $("#playButton"),
  playIcon: $("#playIcon"),
  currentTime: $("#currentTime"),
  durationTime: $("#durationTime"),
  seekBar: $("#seekBar"),
  jumpBackButton: $("#jumpBackButton"),
  jumpForwardButton: $("#jumpForwardButton"),
  selectedLyric: $("#selectedLyric"),
  selectedTimeInput: $("#selectedTimeInput"),
  applyTypedTimeButton: $("#applyTypedTimeButton"),
  markTimeButton: $("#markTimeButton"),
  lyricsList: $("#lyricsList"),
  copyLrcButton: $("#copyLrcButton"),
  globalShiftInput: $("#globalShiftInput"),
  applyGlobalShiftButton: $("#applyGlobalShiftButton"),
  discardButton: $("#discardButton"),
  saveButton: $("#saveButton"),
  finalizeButton: $("#finalizeButton"),
  tutorialButton: $("#tutorialButton"),
  tutorialDialog: $("#tutorialDialog"),
  closeTutorialButton: $("#closeTutorialButton"),
  toast: $("#toast"),
};

const state = {
  songs: [],
  song: null,
  cues: [],
  savedStarts: [],
  selectedIndex: 0,
  activeIndex: -1,
  dirty: false,
  saving: false,
};

let toastTimer = null;

function formatTime(milliseconds) {
  const safe = Math.max(0, Math.round(Number(milliseconds) || 0));
  const minutes = Math.floor(safe / 60000);
  const seconds = Math.floor((safe % 60000) / 1000);
  const millis = safe % 1000;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

function parseTime(value) {
  const match = /^(\d{1,}):(\d{2})\.(\d{3})$/.exec(value.trim());
  if (!match || Number(match[2]) >= 60) {
    throw new Error("请输入 MM:SS.mmm 格式，例如 01:08.200");
  }
  return Number(match[1]) * 60000 + Number(match[2]) * 1000 + Number(match[3]);
}

function showToast(message, isError = false) {
  clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.toggle("is-error", isError);
  elements.toast.classList.add("is-visible");
  toastTimer = setTimeout(() => elements.toast.classList.remove("is-visible"), 2800);
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `请求失败（${response.status}）`);
  }
  return payload;
}

function statusLabel(status) {
  return { unreviewed: "待校对", in_progress: "校对中", finalized: "已定版" }[status] || "待校对";
}

function renderSongList() {
  elements.songCount.textContent = String(state.songs.length);
  elements.songList.replaceChildren();
  state.songs.forEach((song, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "song-item";
    button.classList.toggle("is-active", state.song?.id === song.id);
    button.dataset.songId = song.id;
    button.innerHTML = `
      <span class="song-number">${String(index + 1).padStart(2, "0")}</span>
      <span class="song-meta">
        <span class="song-name"></span>
        <span class="song-detail">${song.cue_count} 句 · ${statusLabel(song.review_status)}</span>
      </span>
      <span class="song-status ${song.review_status}" title="${statusLabel(song.review_status)}"></span>
    `;
    button.querySelector(".song-name").textContent = song.title;
    button.addEventListener("click", () => selectSong(song.id));
    elements.songList.append(button);
  });
}

function setDirty(dirty) {
  state.dirty = dirty;
  const finalized = !dirty && state.song?.review?.status === "finalized";
  elements.saveState.textContent = dirty ? "有未保存修改" : finalized ? "已确认定版" : "已保存";
  elements.saveState.classList.toggle("is-dirty", dirty);
  elements.saveState.classList.toggle("is-finalized", finalized);
  elements.discardButton.disabled = !dirty || state.saving;
  elements.saveButton.disabled = !dirty || state.saving;
  elements.finalizeButton.disabled = state.saving;
}

function updateSelectedPanel() {
  const cue = state.cues[state.selectedIndex];
  const disabled = !cue;
  elements.selectedLyric.textContent = cue?.text || "请选择一句歌词";
  elements.selectedTimeInput.value = formatTime(cue?.start_ms || 0);
  elements.selectedTimeInput.disabled = disabled;
  elements.markTimeButton.disabled = disabled;
  elements.applyTypedTimeButton.disabled = disabled;
  document.querySelectorAll("[data-cue-shift]").forEach((button) => {
    button.disabled = disabled;
  });
}

function renderLyrics() {
  elements.lyricsList.replaceChildren();
  state.cues.forEach((cue, index) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "lyric-row";
    row.role = "option";
    row.dataset.index = String(index);
    row.classList.toggle("is-selected", index === state.selectedIndex);
    row.classList.toggle("is-playing", index === state.activeIndex);
    row.setAttribute("aria-selected", index === state.selectedIndex ? "true" : "false");
    const time = document.createElement("span");
    time.className = "lyric-time";
    time.textContent = formatTime(cue.start_ms);
    const text = document.createElement("span");
    text.className = "lyric-text";
    text.textContent = cue.text;
    const section = document.createElement("span");
    section.className = "lyric-section";
    section.textContent = cue.section || "";
    row.append(time, text, section);
    row.addEventListener("click", () => selectCue(index, true));
    elements.lyricsList.append(row);
  });
  updateSelectedPanel();
}

async function selectSong(songId, force = false) {
  if (!force && state.song?.id === songId) return;
  if (!force && state.dirty && !window.confirm("当前歌曲还有未保存修改，确定切换歌曲吗？")) return;
  try {
    elements.songTitle.textContent = "正在载入歌曲…";
    elements.media.pause();
    const detail = await request(`/api/songs/${encodeURIComponent(songId)}`);
    state.song = detail;
    state.cues = detail.cues.map((cue) => ({ ...cue }));
    state.savedStarts = state.cues.map((cue) => cue.start_ms);
    state.selectedIndex = 0;
    state.activeIndex = -1;
    elements.songTitle.textContent = detail.title;
    elements.media.src = detail.media_url;
    elements.media.load();
    elements.seekBar.max = String(detail.duration_ms / 1000);
    elements.durationTime.textContent = formatTime(detail.duration_ms);
    elements.currentTime.textContent = "00:00.000";
    elements.seekBar.value = "0";
    setDirty(false);
    renderSongList();
    renderLyrics();
  } catch (error) {
    showToast(error.message, true);
  }
}

function selectCue(index, play = false) {
  if (index < 0 || index >= state.cues.length) return;
  state.selectedIndex = index;
  const cue = state.cues[index];
  elements.media.currentTime = cue.start_ms / 1000;
  renderLyrics();
  if (play) elements.media.play().catch(() => showToast("浏览器阻止了自动播放，请点播放按钮", true));
}

function updateActiveCue() {
  const currentMs = Math.round(elements.media.currentTime * 1000);
  let active = -1;
  for (let index = 0; index < state.cues.length; index += 1) {
    if (state.cues[index].start_ms <= currentMs) active = index;
    else break;
  }
  if (active === state.activeIndex) return;
  const previous = elements.lyricsList.querySelector(".is-playing");
  previous?.classList.remove("is-playing");
  state.activeIndex = active;
  const row = elements.lyricsList.querySelector(`[data-index="${active}"]`);
  row?.classList.add("is-playing");
  if (row && !elements.lyricsList.matches(":hover")) {
    row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

function changeCueTime(index, milliseconds) {
  if (!state.song || !state.cues[index]) return false;
  const rounded = Math.round(milliseconds);
  const previous = index > 0 ? state.cues[index - 1].start_ms : -1;
  const next = index + 1 < state.cues.length ? state.cues[index + 1].start_ms : state.song.duration_ms;
  if (rounded <= previous || rounded >= next) {
    showToast(`时间必须位于 ${formatTime(previous + 1)} 和 ${formatTime(next - 1)} 之间`, true);
    return false;
  }
  state.cues[index].start_ms = rounded;
  setDirty(state.cues.some((cue, cueIndex) => cue.start_ms !== state.savedStarts[cueIndex]));
  renderLyrics();
  return true;
}

function shiftAll(milliseconds) {
  if (!state.song || !Number.isFinite(milliseconds) || milliseconds === 0) return;
  const shifted = state.cues.map((cue) => cue.start_ms + Math.round(milliseconds));
  if (shifted[0] < 0 || shifted.at(-1) >= state.song.duration_ms) {
    showToast("整体偏移会使歌词超出歌曲范围，已取消", true);
    return;
  }
  state.cues.forEach((cue, index) => { cue.start_ms = shifted[index]; });
  setDirty(state.cues.some((cue, index) => cue.start_ms !== state.savedStarts[index]));
  renderLyrics();
  showToast(`整条时间轴已${milliseconds < 0 ? "提前" : "延后"} ${Math.abs(milliseconds)}ms`);
}

async function save(finalize) {
  if (!state.song || state.saving) return;
  state.saving = true;
  setDirty(state.dirty);
  try {
    const result = await request(`/api/songs/${encodeURIComponent(state.song.id)}/timeline`, {
      method: "POST",
      body: JSON.stringify({
        revision: state.song.revision,
        starts: state.cues.map((cue) => cue.start_ms),
        finalize,
      }),
    });
    state.song.revision = result.revision;
    state.song.review = result.review;
    state.savedStarts = state.cues.map((cue) => cue.start_ms);
    const listSong = state.songs.find((song) => song.id === state.song.id);
    if (listSong) {
      listSong.review_status = result.review.status;
      listSong.edited_count = result.review.edited_count;
    }
    state.saving = false;
    setDirty(false);
    renderSongList();
    showToast(finalize ? "时间轴已确认定版并通过保存校验" : "校对进度已保存");
  } catch (error) {
    state.saving = false;
    setDirty(state.dirty);
    showToast(error.message, true);
  }
}

async function copyNeteaseLrc() {
  const content = state.cues.map((cue) => `[${formatTime(cue.start_ms)}]${cue.text}`).join("\n");
  try {
    await navigator.clipboard.writeText(content);
    showToast("已复制网易云毫秒格式歌词");
  } catch {
    showToast("浏览器未允许剪贴板访问", true);
  }
}

function updateTransport() {
  const current = Number.isFinite(elements.media.currentTime) ? elements.media.currentTime : 0;
  elements.currentTime.textContent = formatTime(current * 1000);
  elements.seekBar.value = String(current);
  updateActiveCue();
}

async function initialize() {
  try {
    const payload = await request("/api/songs");
    state.songs = payload.songs;
    renderSongList();
    if (state.songs.length) await selectSong(state.songs[0].id, true);
  } catch (error) {
    elements.songTitle.textContent = "载入失败";
    showToast(error.message, true);
  }
}

elements.playButton.addEventListener("click", () => {
  if (elements.media.paused) elements.media.play().catch(() => showToast("无法播放当前媒体", true));
  else elements.media.pause();
});
elements.media.addEventListener("play", () => { elements.playIcon.textContent = "Ⅱ"; });
elements.media.addEventListener("pause", () => { elements.playIcon.textContent = "▶"; });
elements.media.addEventListener("timeupdate", updateTransport);
elements.media.addEventListener("loadedmetadata", () => {
  elements.seekBar.max = String(elements.media.duration || state.song?.duration_ms / 1000 || 1);
});
elements.seekBar.addEventListener("input", () => {
  elements.media.currentTime = Number(elements.seekBar.value);
  updateTransport();
});
elements.jumpBackButton.addEventListener("click", () => {
  elements.media.currentTime = Math.max(0, elements.media.currentTime - 0.1);
});
elements.jumpForwardButton.addEventListener("click", () => {
  elements.media.currentTime = Math.min(elements.media.duration || Infinity, elements.media.currentTime + 0.1);
});
elements.toggleVideoButton.addEventListener("click", () => {
  const hidden = elements.videoWrap.classList.toggle("is-hidden");
  elements.toggleVideoButton.textContent = hidden ? "显示歌词视频" : "隐藏歌词视频";
});
elements.markTimeButton.addEventListener("click", () => {
  if (changeCueTime(state.selectedIndex, elements.media.currentTime * 1000)) {
    showToast(`已把当前播放时间写入第 ${state.selectedIndex + 1} 句`);
  }
});
elements.applyTypedTimeButton.addEventListener("click", () => {
  try {
    changeCueTime(state.selectedIndex, parseTime(elements.selectedTimeInput.value));
  } catch (error) {
    showToast(error.message, true);
  }
});
elements.selectedTimeInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") elements.applyTypedTimeButton.click();
});
document.querySelectorAll("[data-cue-shift]").forEach((button) => {
  button.addEventListener("click", () => {
    const cue = state.cues[state.selectedIndex];
    if (cue) changeCueTime(state.selectedIndex, cue.start_ms + Number(button.dataset.cueShift));
  });
});
document.querySelectorAll("[data-global-shift]").forEach((button) => {
  button.addEventListener("click", () => shiftAll(Number(button.dataset.globalShift)));
});
elements.applyGlobalShiftButton.addEventListener("click", () => shiftAll(Number(elements.globalShiftInput.value)));
elements.discardButton.addEventListener("click", () => {
  if (!state.dirty || !window.confirm("撤销当前歌曲所有未保存修改？")) return;
  state.cues.forEach((cue, index) => { cue.start_ms = state.savedStarts[index]; });
  setDirty(false);
  renderLyrics();
});
elements.saveButton.addEventListener("click", () => save(false));
elements.finalizeButton.addEventListener("click", () => {
  if (window.confirm("确认已经逐句核对完成，并把这首歌标记为最终版本？")) save(true);
});
elements.copyLrcButton.addEventListener("click", copyNeteaseLrc);
elements.tutorialButton.addEventListener("click", () => elements.tutorialDialog.showModal());
elements.closeTutorialButton.addEventListener("click", () => elements.tutorialDialog.close());
elements.tutorialDialog.addEventListener("click", (event) => {
  if (event.target === elements.tutorialDialog) elements.tutorialDialog.close();
});
window.addEventListener("beforeunload", (event) => {
  if (!state.dirty) return;
  event.preventDefault();
  event.returnValue = "";
});
window.addEventListener("keydown", (event) => {
  if (["INPUT", "TEXTAREA", "BUTTON"].includes(document.activeElement?.tagName)) return;
  if (event.code === "Space") {
    event.preventDefault();
    elements.playButton.click();
  } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    const direction = event.key === "ArrowLeft" ? -1 : 1;
    const step = event.shiftKey ? 1 : 0.1;
    elements.media.currentTime = Math.max(0, Math.min(elements.media.duration || Infinity, elements.media.currentTime + direction * step));
  } else if (event.key === "Enter") {
    event.preventDefault();
    elements.markTimeButton.click();
  }
});

initialize();
