const API_BASE = "http://127.0.0.1:8000";

const el = (id) => document.getElementById(id);
const screens = {
  upload: el("screen-upload"),
  result: el("screen-result"),
  styled: el("screen-styled"),
};
const loading = el("loading");

const state = {
  name: "000",
  originalFile: null,
  originalUrl: null,
  analysis: null,
  selectedStyleKey: null,

  personKey: null,                 // ✅ 현재 사람(사진) 식별자
  generatedByPerson: new Map(),     // ✅ 사람별 생성 기록 저장소
  generated: [],                   // ✅ 현재 사람의 생성 기록만 가리키는 배열
  selectedGeneratedId: null,
};


function showLoading(on) {
  loading.classList.toggle("hidden", !on);
  loading.setAttribute("aria-hidden", String(!on));
}

function showScreen(which) {
  Object.values(screens).forEach(s => s.classList.add("hidden"));
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

function resetToUploadView(keepGenerated = true) {
  showScreen("upload");

  if (!keepGenerated) {
    // 필요시만 초기화
    state.analysis = null;
    state.generated.forEach(g => URL.revokeObjectURL(g.url));
    state.generated = [];
    state.selectedGeneratedId = null;
  }
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
  (a.styling_tips || []).forEach(t => {
    const li = document.createElement("li");
    li.textContent = t;
    tips.appendChild(li);
  });

  // ✅ undefined 방지: 정확히 style_options로 접근
  const options = Array.isArray(a.style_options) ? a.style_options : [];
  const box = el("options");
  box.innerHTML = "";

  // 혹시 모델이 옵션을 빼먹었을 때 프론트 fallback
  const fallback = [
    { key: "clean", title: "클린", summary: "단정하고 밝은 프로필 톤", edit_prompt: "깔끔한 조명과 단정한 헤어로 정리. 배경은 심플." },
    { key: "street", title: "스트릿", summary: "힙한 캐주얼 무드", edit_prompt: "스트릿 캐주얼 무드. 오버핏 느낌, 색감 세련되게." },
    { key: "formal", title: "포멀", summary: "정돈된 포멀 프로필", edit_prompt: "포멀한 프로필 톤. 차분하고 단정하게." },
  ];
  const used = options.length ? options : fallback;

  const defaultKey = a.default_style_key || used[0].key;
  state.selectedStyleKey = state.selectedStyleKey || defaultKey;

  used.forEach(opt => {
    const div = document.createElement("div");
    div.className = "opt" + (opt.key === state.selectedStyleKey ? " active" : "");
    div.dataset.key = opt.key;

    div.innerHTML = `
      <div class="opt-title">${opt.title}</div>
      <div class="opt-desc">${opt.summary || ""}</div>
    `;

    div.addEventListener("click", () => {
      state.selectedStyleKey = opt.key;
      renderResult(); // active 표시 업데이트
    });

    box.appendChild(div);
  });
}

function renderStyledThumbs() {
  const thumbs = el("thumbs");
  thumbs.innerHTML = "";

  state.generated.forEach(g => {
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

  const fallback = [
    { key: "clean", title: "클린", summary: "단정하고 밝은 프로필 톤", edit_prompt: "깔끔한 조명과 단정한 헤어로 정리. 배경은 심플." },
    { key: "street", title: "스트릿", summary: "힙한 캐주얼 무드", edit_prompt: "스트릿 캐주얼 무드. 오버핏 느낌, 색감 세련되게." },
    { key: "formal", title: "포멀", summary: "정돈된 포멀 프로필", edit_prompt: "포멀한 프로필 톤. 차분하고 단정하게." },
  ];

  const used = (a && Array.isArray(a.style_options) && a.style_options.length)
    ? a.style_options
    : fallback;

  return used.find(o => o.key === state.selectedStyleKey) || used[0];
}


function makePersonKey(file) {
  // 가장 간단한 사람(사진) 식별자: 파일 특성 기반
  // (새 사진이면 값이 달라져서 자동으로 다른 사람으로 인식됨)
  return `${file.name}_${file.size}_${file.lastModified}`;
}

el("file").addEventListener("change", (e) => {
  const file = e.target.files?.[0] || null;
  if (!file) return;

  state.originalFile = file;

  // ✅ personKey 갱신 + 사람별 기록 스위칭
  state.personKey = makePersonKey(file);

  // 이 사람의 생성 기록이 있으면 불러오고, 없으면 새로 생성
  if (!state.generatedByPerson.has(state.personKey)) {
    state.generatedByPerson.set(state.personKey, []);
  }
  state.generated = state.generatedByPerson.get(state.personKey);

  // 사람 바뀌면 분석/선택 상태는 새로 시작(중요)
  state.analysis = null;
  state.selectedStyleKey = null;
  state.selectedGeneratedId = null;

  if (state.originalUrl) URL.revokeObjectURL(state.originalUrl);
  state.originalUrl = URL.createObjectURL(file);

  el("placeholder").classList.add("hidden");
  setPreview("preview-upload", state.originalUrl);
  enableAnalyzeButton();
});

el("name").addEventListener("input", (e) => {
  state.name = (e.target.value || "000").trim() || "000";
});

el("btn-analyze").addEventListener("click", async () => {
  if (!state.originalFile) return;

  showLoading(true);
  try {
    state.analysis = await postAnalyze();

    // 원본 프리뷰를 결과 화면에서도 동일하게
    setPreview("preview-result", state.originalUrl);

    // 선택지 렌더
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

el("btn-back-upload").addEventListener("click", () => {
  // ✅ 생성 이미지 유지한 채 업로드 화면으로
  resetToUploadView(true);
  // 업로드 화면에서 미리보기도 유지
  if (state.originalUrl) {
    el("placeholder").classList.add("hidden");
    setPreview("preview-upload", state.originalUrl);
    enableAnalyzeButton();
  }
});

function resetCurrentPersonHistory() {
  if (!state.personKey) return;
  const arr = state.generatedByPerson.get(state.personKey) || [];
  arr.forEach(g => URL.revokeObjectURL(g.url));
  state.generatedByPerson.set(state.personKey, []);
  state.generated = state.generatedByPerson.get(state.personKey);
  state.selectedGeneratedId = null;
}


el("btn-go-styled").addEventListener("click", async () => {
  // 선택한 옵션으로 생성
  const opt = getSelectedOption();
  if (!opt) {
    alert("스타일 옵션이 없습니다. 먼저 분석을 완료해줘.");
    return;
  }

  // 이미 생성한 스타일이면 재호출 없이 바로 보여줌
  const existing = state.generated.find(g => g.key === opt.key);
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

el("btn-back-result").addEventListener("click", () => {
  // ✅ 생성 이미지 유지한 채 결과 화면으로
  setPreview("preview-result", state.originalUrl);
  renderResult();
  showScreen("result");
});
