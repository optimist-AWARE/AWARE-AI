const APP_VERSION = "2026-02-20_04-10_reset-hardreload";
console.log("[AWARE FRONT LOADED]", APP_VERSION, "href=", location.href);

const API_BASE = "https://3.39.252.231";

const el = (id) => document.getElementById(id);
const screens = {
  upload: el("screen-upload"),
  result: el("screen-result"),
  styled: el("screen-styled"),
};
const loading = el("loading");

const state = {
  name: "000",
  gender: null,
  age: null,
  originalFile: null,
  originalUrl: null,
  analysis: null,
  selectedStyleKey: null,

  personKey: null,
  generatedByPerson: new Map(),
  generated: [],
  selectedGeneratedId: null,
};

function showLoading(on) {
  loading.classList.toggle("hidden", !on);
  loading.setAttribute("aria-hidden", String(!on));
}

function showScreen(which) {
  Object.values(screens).forEach((s) => s.classList.add("hidden"));
  screens[which].classList.remove("hidden");
}

function setPreview(selectorId, url) {
  const img = el(selectorId);
  img.src = url;
  img.classList.remove("hidden");
}

function enableAnalyzeButton() {
  const btn = el("btn-analyze");
  btn.disabled = !state.originalFile;
}

function renderResult() {
  const a = state.analysis;
  if (!a) return;

  el("result-title").textContent = `${a.name}님의 외모는`;
  el("result-score").textContent = `${a.style_score} / 10 점`;
  el("result-vibe").textContent = a.vibe;
  el("result-reason").textContent = a.vibe_reason || "";

  const tips = el("tips");
  tips.innerHTML = "";
  (a.styling_tips || []).forEach((t) => {
    const li = document.createElement("li");
    li.textContent = t;
    tips.appendChild(li);
  });

  const options = Array.isArray(a.style_options) ? a.style_options : [];
  const box = el("options");
  box.innerHTML = "";

  const fallback = [
    { key: "clean", title: "클린", summary: "단정하고 밝은 프로필 톤", edit_prompt: "깔끔한 조명과 단정한 헤어로 정리. 배경은 심플." },
    { key: "street", title: "스트릿", summary: "힙한 캐주얼 무드", edit_prompt: "스트릿 캐주얼 무드. 오버핏 느낌, 색감 세련되게." },
    { key: "formal", title: "포멀", summary: "정돈된 포멀 프로필", edit_prompt: "포멀한 프로필 톤. 차분하고 단정하게." },
  ];
  const used = options.length ? options : fallback;

  const defaultKey = a.default_style_key || used[0].key;
  state.selectedStyleKey = state.selectedStyleKey || defaultKey;

  used.forEach((opt) => {
    const div = document.createElement("div");
    div.className = "opt" + (opt.key === state.selectedStyleKey ? " active" : "");
    div.dataset.key = opt.key;

    div.innerHTML = `
      <div class="opt-title">${opt.title}</div>
      <div class="opt-desc">${opt.summary || ""}</div>
    `;

    div.addEventListener("click", () => {
      state.selectedStyleKey = opt.key;
      renderResult();
    });

    box.appendChild(div);
  });
}

function renderStyledThumbs() {
  const thumbs = el("thumbs");
  thumbs.innerHTML = "";

  state.generated.forEach((g) => {
    const div = document.createElement("div");
    div.className = "thumb" + (g.id === state.selectedGeneratedId ? " active" : "");
    div.title = g.title;

    const img = document.createElement("img");
    img.src = g.url;
    img.alt = g.title;

    div.appendChild(img);
    div.addEventListener("click", () => {
      state.selectedGeneratedId = g.id;
      setPreview("preview-styled", g.url);
      renderStyledThumbs();
    });

    thumbs.appendChild(div);
  });
}

async function postAnalyze() {
  const fd = new FormData();
  fd.append("image", state.originalFile);
  fd.append("name", state.name);
  if (state.gender) fd.append("gender", state.gender);
  if (state.age !== null) fd.append("age", state.age);

  const res = await fetch(`${API_BASE}/api/analyze`, {
    method: "POST",
    body: fd,
  });

  if (!res.ok) {
    let msg = await res.text();
    throw new Error(msg);
  }
  return await res.json();
}

async function postApply(editPrompt) {
  const fd = new FormData();
  fd.append("image", state.originalFile);
  fd.append("edit_prompt", editPrompt);

  const res = await fetch(`${API_BASE}/api/apply`, {
    method: "POST",
    body: fd,
  });

  if (!res.ok) {
    let msg = await res.text();
    throw new Error(msg);
  }

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  return url;
}

function getSelectedOption() {
  const a = state.analysis;
  if (!a || !Array.isArray(a.style_options)) return null;
  return a.style_options.find((o) => o.key === state.selectedStyleKey) || a.style_options[0] || null;
}

function makePersonKey(file) {
  return `${file.name}_${file.size}_${file.lastModified}`;
}

function resetCurrentPersonHistory() {
  state.analysis = null;
  state.selectedStyleKey = null;
  state.selectedGeneratedId = null;
}

function forceHardReload(reason = "manual-reset") {
  // ✅ “진짜 새로고침”이 됐는지 확인용 로그
  console.log("[HARD RELOAD TRIGGERED]", reason, "version=", APP_VERSION);

  // ✅ 캐시/복원(BackForwardCache)까지 피하려고 URL에 랜덤 쿼리 붙여서 로드
  const base = location.origin + location.pathname;
  const next = `${base}?reload=${Date.now()}&reason=${encodeURIComponent(reason)}`;
  location.replace(next);
}

/* ----------------------------
   이벤트 바인딩
---------------------------- */

el("file").addEventListener("change", (e) => {
  const file = e.target.files?.[0] || null;
  if (!file) return;

  state.originalFile = file;

  state.personKey = makePersonKey(file);

  if (!state.generatedByPerson.has(state.personKey)) {
    state.generatedByPerson.set(state.personKey, []);
  }
  state.generated = state.generatedByPerson.get(state.personKey);

  resetCurrentPersonHistory();

  if (state.originalUrl) URL.revokeObjectURL(state.originalUrl);
  state.originalUrl = URL.createObjectURL(file);

  el("placeholder").classList.add("hidden");
  setPreview("preview-upload", state.originalUrl);
  enableAnalyzeButton();
});

el("name").addEventListener("input", (e) => {
  state.name = (e.target.value || "000").trim() || "000";
});

el("age").addEventListener("input", (e) => {
  const v = parseInt(e.target.value, 10);
  state.age = Number.isNaN(v) ? null : v;
});

/* --- 카메라 촬영 --- */
let cameraStream = null;

function stopCamera() {
  if (cameraStream) {
    cameraStream.getTracks().forEach((t) => t.stop());
    cameraStream = null;
  }
  el("camera").srcObject = null;
  el("camera").classList.add("hidden");
  el("camera-actions").classList.add("hidden");
  el("btn-camera-start").classList.remove("hidden");
}

el("btn-camera-start").addEventListener("click", async () => {
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user" },
      audio: false,
    });
    const video = el("camera");
    video.srcObject = cameraStream;
    video.classList.remove("hidden");
    el("placeholder").classList.add("hidden");
    el("preview-upload").classList.add("hidden");
    el("camera-actions").classList.remove("hidden");
    el("btn-camera-start").classList.add("hidden");
  } catch (err) {
    console.error(err);
    alert("카메라를 사용할 수 없습니다: " + (err.message || err));
  }
});

el("btn-camera-capture").addEventListener("click", () => {
  const video = el("camera");
  const canvas = el("camera-canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);

  canvas.toBlob((blob) => {
    if (!blob) return;
    const file = new File([blob], `capture-${Date.now()}.jpg`, { type: "image/jpeg" });

    state.originalFile = file;
    state.personKey = makePersonKey(file);
    if (!state.generatedByPerson.has(state.personKey)) {
      state.generatedByPerson.set(state.personKey, []);
    }
    state.generated = state.generatedByPerson.get(state.personKey);
    resetCurrentPersonHistory();

    if (state.originalUrl) URL.revokeObjectURL(state.originalUrl);
    state.originalUrl = URL.createObjectURL(file);

    stopCamera();
    setPreview("preview-upload", state.originalUrl);
    enableAnalyzeButton();
  }, "image/jpeg", 0.92);
});

el("btn-camera-stop").addEventListener("click", () => {
  stopCamera();
  if (state.originalUrl) {
    setPreview("preview-upload", state.originalUrl);
  } else {
    el("placeholder").classList.remove("hidden");
  }
});

["gender-m", "gender-f"].forEach((id) => {
  el(id).addEventListener("click", () => {
    const value = id === "gender-m" ? "male" : "female";
    state.gender = state.gender === value ? null : value;
    el("gender-m").classList.toggle("active", state.gender === "male");
    el("gender-f").classList.toggle("active", state.gender === "female");
  });
});

el("btn-analyze").addEventListener("click", async () => {
  if (!state.originalFile) return;

  showLoading(true);
  try {
    state.analysis = await postAnalyze();

    setPreview("preview-result", state.originalUrl);

    state.selectedStyleKey = state.analysis.default_style_key || "clean";
    renderResult();

    showScreen("result");
  } catch (err) {
    console.error(err);
    alert("분석 실패: " + String(err.message || err));
  } finally {
    showLoading(false);
  }
});

// ✅ “처음부터 다시하기” 버튼들: 전부 ‘강제 새로고침’으로 통일
["btn-back-upload"].forEach((id) => {
  const btn = el(id);
  if (!btn) return;
  btn.addEventListener("click", (e) => {
    e.preventDefault();
    forceHardReload(id);
  });
});

// 결과/스타일 화면에서 “돌아가기”는 화면만 전환
el("btn-back-result").addEventListener("click", () => {
  setPreview("preview-result", state.originalUrl);
  renderResult();
  showScreen("result");
});

el("btn-view-generated").addEventListener("click", () => {
  if (!state.generated.length) {
    alert("아직 생성된 이미지가 없습니다.");
    return;
  }
  const last = state.generated[state.generated.length - 1];
  state.selectedGeneratedId = state.selectedGeneratedId || last.id;
  const sel = state.generated.find((g) => g.id === state.selectedGeneratedId) || last;
  setPreview("preview-styled", sel.url);
  renderStyledThumbs();
  showScreen("styled");
});

el("btn-go-styled").addEventListener("click", async () => {
  const opt = getSelectedOption();
  if (!opt) {
    alert("스타일 옵션이 없습니다. 먼저 분석을 완료해줘.");
    return;
  }

  const existing = state.generated.find((g) => g.key === opt.key);
  if (existing) {
    state.selectedGeneratedId = existing.id;
    setPreview("preview-styled", existing.url);
    renderStyledThumbs();
    showScreen("styled");
    return;
  }

  showLoading(true);
  try {
    const url = await postApply(opt.edit_prompt);

    const id = `${opt.key}-${Date.now()}`;
    state.generated.push({
      id,
      key: opt.key,
      title: opt.title,
      url,
      prompt: opt.edit_prompt,
    });

    state.selectedGeneratedId = id;
    setPreview("preview-styled", url);
    renderStyledThumbs();

    showScreen("styled");
  } catch (err) {
    console.error(err);
    alert("스타일링 생성 실패: " + String(err.message || err));
  } finally {
    showLoading(false);
  }
});

el("btn-custom").addEventListener("click", async () => {
  const p = (el("custom-prompt").value || "").trim();
  if (!p) {
    alert("원하는 스타일을 입력해줘.");
    return;
  }

  showLoading(true);
  try {
    const url = await postApply(p);

    const id = `custom-${Date.now()}`;
    state.generated.push({
      id,
      key: "custom",
      title: "커스텀",
      url,
      prompt: p,
    });

    state.selectedGeneratedId = id;
    setPreview("preview-styled", url);
    renderStyledThumbs();
  } catch (err) {
    console.error(err);
    alert("커스텀 생성 실패: " + String(err.message || err));
  } finally {
    showLoading(false);
  }
});
