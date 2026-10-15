/**
 * ИнфоТренер v2 — клиент
 * Мультиязычность (i18n: RU/KK), динамические варианты ответов (таблица options),
 * карта компетенций, объяснения, горячие клавиши.
 */

/* ── State ─────────────────────────────────────────────────────────── */
const state = {
  user: null,
  view: "auth",
  topics: [],
  topic: null,
  questions: [],
  index: 0,
  answers: {},
  remaining: 0,
  timerId: null,
  submitting: false,
  exitOpen: false,
  authMode: "login",
  teacherTab: "dashboard",
  deleteCallback: null,
  editingQuestion: null,
  editingTopic: null,
  lang: localStorage.getItem("infotrainer_lang") || "ru",
  translations: { ru: {}, kk: {} },
};

const views = {
  auth: document.getElementById("view-auth"),
  home: document.getElementById("view-home"),
  quiz: document.getElementById("view-quiz"),
  result: document.getElementById("view-result"),
  teacher: document.getElementById("view-teacher"),
};

/* ── Utilities ─────────────────────────────────────────────────────── */
function $(id) { return document.getElementById(id); }

function escapeHtml(v) {
  return String(v).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;");
}

function formatTime(s) {
  const m = Math.floor(s / 60), sec = s % 60;
  return `${String(m).padStart(2,"0")}:${String(sec).padStart(2,"0")}`;
}

async function api(path, opts = {}) {
  const r = await fetch(path, {
    credentials: "same-origin",
    headers: {"Content-Type": "application/json", ...(opts.headers||{})},
    ...opts,
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || "Ошибка запроса");
  return d;
}

/* ── i18n Локализация ──────────────────────────────────────────────── */
function t(key, params = {}) {
  const dict = state.translations[state.lang] || {};
  let str = dict[key] != null ? dict[key] : key;
  Object.entries(params).forEach(([k, v]) => {
    str = String(str).replaceAll(`{${k}}`, v);
  });
  return str;
}

async function loadTranslations() {
  try {
    const [ru, kk] = await Promise.all([
      api("/api/locales/ru").catch(() => ({})),
      api("/api/locales/kk").catch(() => ({})),
    ]);
    state.translations.ru = ru;
    state.translations.kk = kk;
  } catch (e) {
    console.warn("Could not load locales:", e);
  }
}

function applyLanguage(lang) {
  state.lang = lang === "kk" ? "kk" : "ru";
  localStorage.setItem("infotrainer_lang", state.lang);

  const isRu = state.lang === "ru";
  const btnRu = $("lang-ru");
  const btnKk = $("lang-kk");

  if (btnRu && btnKk) {
    btnRu.classList.toggle("lang-btn-active", isRu);
    btnRu.classList.toggle("bg-white", isRu);
    btnRu.classList.toggle("text-brand-700", isRu);
    btnRu.classList.toggle("shadow-sm", isRu);
    btnRu.classList.toggle("text-slate-600", !isRu);

    btnKk.classList.toggle("lang-btn-active", !isRu);
    btnKk.classList.toggle("bg-white", !isRu);
    btnKk.classList.toggle("text-brand-700", !isRu);
    btnKk.classList.toggle("shadow-sm", !isRu);
    btnKk.classList.toggle("text-slate-600", isRu);
  }

  // Обновляем статические элементы с атрибутом data-i18n
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    if (key) el.textContent = t(key);
  });

  // Обновляем placeholder'ы
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    const key = el.getAttribute("data-i18n-placeholder");
    if (key) el.placeholder = t(key);
  });

  // Обновляем динамические данные текущего экрана
  updateCurrentViewTexts();
}

function updateCurrentViewTexts() {
  if (state.user) {
    const isTeacher = state.user.role === "teacher";
    const label = isTeacher ? t("role_teacher") : t("role_student");
    $("user-chip").textContent = `${state.user.username} · ${label}`;
  }
  if (state.view === "home") {
    if (state.topics.length) renderTopics();
    renderMyResults();
    renderCompetencyMap();
  } else if (state.view === "quiz" && state.questions.length) {
    renderQuestion();
  } else if (state.view === "teacher") {
    renderTeacher();
  }
}

/* Метки сложностей и трендов с учётом i18n */
function getDiffLabel(d) {
  if (d === "easy") return t("diff_easy");
  if (d === "hard") return t("diff_hard");
  return t("diff_medium");
}

function getTrendLabel(tr) {
  if (tr === "improving") return t("trend_improving");
  if (tr === "declining") return t("trend_declining");
  if (tr === "new") return t("trend_new");
  return t("trend_stable");
}

function getLevelLabel(lvl) {
  if (lvl === "excellent") return t("level_excellent");
  if (lvl === "good") return t("level_good");
  if (lvl === "attention") return t("level_attention");
  return t("level_weak");
}

const DIFF_CLASSES = { easy: "badge-easy", medium: "badge-medium", hard: "badge-hard" };
const TREND_CLASSES = { improving: "trend-improving", declining: "trend-declining", stable: "trend-stable", new: "trend-new" };

/* ── Icons ─────────────────────────────────────────────────────────── */
function iconSvg(name) {
  const icons = {
    cpu: '<path d="M9 9h6v6H9zM4 9H2m2 6H2M9 4V2m6 2V2m7 7h-2m2 6h-2M9 22v-2m6 2v-2" stroke-linecap="round"/>',
    code: '<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M13 6l-2 12" stroke-linecap="round" stroke-linejoin="round"/>',
    database: '<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/>',
    hash: '<path d="M5 9h14M5 15h14M9 4L7 20M17 4l-2 16" stroke-linecap="round"/>',
    globe: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15.3 15.3 0 010 20M12 2a15.3 15.3 0 000 20"/>',
    shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    zap: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10"/>',
    book: '<path d="M4 19.5A2.5 2.5 0 016.5 17H20M4 4.5A2.5 2.5 0 016.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15z"/>',
  };
  return `<svg class="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">${icons[name]||icons.hash}</svg>`;
}

/* ── View management ───────────────────────────────────────────────── */
function showView(name) {
  state.view = name;
  Object.entries(views).forEach(([k,n]) => n.classList.toggle("hidden", k !== name));
  const isStudent = state.user?.role === "student";
  const isTeacher = state.user?.role === "teacher";
  $("main-nav").classList.toggle("hidden", !state.user);
  $("nav-home").classList.toggle("hidden", !isStudent);
  $("nav-teacher").classList.toggle("hidden", !isTeacher);
  $("nav-home").classList.toggle("nav-btn-active", name === "home");
  $("nav-teacher").classList.toggle("nav-btn-active", name === "teacher");
  $("user-box").classList.toggle("hidden", !state.user);
  $("user-box").classList.toggle("flex", Boolean(state.user));
  if (state.user) {
    const label = isTeacher ? t("role_teacher") : t("role_student");
    $("user-chip").textContent = `${state.user.username} · ${label}`;
  }
}

/* ── Auth ───────────────────────────────────────────────────────────── */
function setAuthMode(mode) {
  state.authMode = mode;
  const reg = mode === "register";
  $("tab-login").classList.toggle("auth-tab-active", !reg);
  $("tab-register").classList.toggle("auth-tab-active", reg);
  $("role-box").classList.toggle("hidden", !reg);
  $("password-hint").classList.toggle("hidden", !reg);
  $("auth-heading").textContent = reg ? t("auth_title_register") : t("auth_title_login");
  $("auth-submit").textContent = reg ? t("btn_register") : t("btn_login");
  $("auth-password").autocomplete = reg ? "new-password" : "current-password";
  $("auth-error").classList.add("hidden");
}

function showAuthError(msg) {
  const n = $("auth-error"); n.textContent = msg; n.classList.remove("hidden");
}

/* ── Timer ──────────────────────────────────────────────────────────── */
function stopTimer() { if (state.timerId) { clearInterval(state.timerId); state.timerId = null; } }

function startTimer(sec) {
  stopTimer();
  state.remaining = sec;
  renderTimer();
  state.timerId = setInterval(() => {
    state.remaining -= 1; renderTimer();
    if (state.remaining <= 0) { stopTimer(); submitQuiz(); }
  }, 1000);
}

function renderTimer() {
  const n = $("quiz-timer");
  n.textContent = formatTime(Math.max(0, state.remaining));
  n.classList.remove("timer-ok","timer-warn","timer-hot");
  n.classList.add(state.remaining <= 30 ? "timer-hot" : state.remaining <= 90 ? "timer-warn" : "timer-ok");
}

/* ── Quiz helpers ──────────────────────────────────────────────────── */
function selectedOf(qid) { return state.answers[qid] || []; }

function toggleAnswer(q, oid) {
  const cur = selectedOf(q.id);
  state.answers[q.id] = q.type === "single" ? [oid] : (cur.includes(oid) ? cur.filter(x=>x!==oid) : [...cur,oid]);
  renderQuestion();
}

/* ── Topics ─────────────────────────────────────────────────────────── */
function renderTopics() {
  const grid = $("topics-grid");
  grid.innerHTML = state.topics.map(tData => `
    <button type="button" data-topic="${escapeHtml(tData.id)}"
      class="group rounded-3xl border border-slate-200 bg-white p-5 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-blue-200 hover:shadow-md">
      <div class="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-blue-50 text-brand-600 group-hover:bg-brand-600 group-hover:text-white">
        ${iconSvg(tData.icon)}
      </div>
      <h3 class="text-lg font-extrabold">${escapeHtml(tData.title)}</h3>
      <p class="mt-1 text-sm text-slate-500">${escapeHtml(tData.subtitle)}</p>
      <div class="mt-4 flex gap-3 text-xs font-semibold text-slate-500">
        <span>${tData.question_count} ${t("unit_questions")}</span><span>·</span>
        <span>${Math.round(tData.time_limit_sec/60)} ${t("unit_min")}</span>
      </div>
    </button>`).join("");
  grid.querySelectorAll("[data-topic]").forEach(b => b.addEventListener("click", () => startQuiz(b.dataset.topic)));
}

/* ── Quiz rendering ────────────────────────────────────────────────── */
function renderQuestion() {
  const q = state.questions[state.index], total = state.questions.length, sel = selectedOf(q.id);
  $("quiz-kicker").textContent = t("quiz_kicker");
  $("quiz-title").textContent = state.topic.title;
  $("quiz-index").textContent = t("quiz_q_of", { cur: state.index + 1, total });
  $("quiz-type").textContent = q.type === "single" ? t("quiz_single") : t("quiz_multiple");
  $("quiz-question").textContent = q.text;
  $("quiz-progress").style.width = `${((state.index+1)/total)*100}%`;
  const diff = $("quiz-difficulty");
  if (diff) {
    const d = q.difficulty || "medium";
    diff.textContent = getDiffLabel(d);
    diff.className = `badge ${DIFF_CLASSES[d] || "badge-medium"}`;
  }

  const box = $("quiz-options");
  box.innerHTML = q.options.map((o,i) => {
    const on = sel.includes(o.id);
    return `
      <button type="button" data-option="${escapeHtml(o.id)}" class="option ${on?"option-selected":""}">
        <span class="keycap">${i+1}</span>
        <span class="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center ${q.type==="single"?"rounded-full":"rounded"} border ${on?"border-brand-600 bg-brand-600 text-white":"border-slate-300"}">
          ${on?"✓":""}
        </span>
        <span class="text-sm font-medium leading-snug">${escapeHtml(o.text)}</span>
      </button>`;
  }).join("");
  box.querySelectorAll("[data-option]").forEach(b => b.addEventListener("click", () => toggleAnswer(q, b.dataset.option)));

  $("btn-prev").disabled = state.index === 0;
  const last = state.index === total - 1;
  $("btn-next").classList.toggle("hidden", last);
  $("btn-finish").classList.toggle("hidden", !last);
}

/* ── Exit modal ────────────────────────────────────────────────────── */
function setExitOpen(v) { state.exitOpen = v; $("exit-modal").classList.toggle("hidden", !v); }
function leaveQuiz() { stopTimer(); setExitOpen(false); state.topic = null; goHome(); }
function goNext() { if (state.index < state.questions.length-1) { state.index += 1; renderQuestion(); return; } submitQuiz(); }

/* ── Quiz flow ─────────────────────────────────────────────────────── */
async function startQuiz(topicId) {
  try {
    const d = await api(`/api/topics/${topicId}`);
    state.topic = d.topic; state.questions = d.questions;
    state.index = 0; state.answers = {}; state.submitting = false;
    setExitOpen(false); showView("quiz"); renderQuestion(); startTimer(d.topic.time_limit_sec);
  } catch(e) { alert(e.message); }
}

async function submitQuiz() {
  if (state.submitting || !state.topic) return;
  state.submitting = true; stopTimer(); setExitOpen(false);
  const used = state.topic.time_limit_sec - Math.max(0, state.remaining);
  try {
    const d = await api("/api/submit", {
      method: "POST",
      body: JSON.stringify({ topic_id: state.topic.id, answers: state.answers, duration_sec: used }),
    });
    renderResult(d); showView("result");
  } catch(e) { state.submitting = false; alert(e.message); }
}

/* ── Result view ───────────────────────────────────────────────────── */
function optionClass(oid, sel, cor) {
  if (cor.includes(oid)) return "option option-correct";
  if (sel.includes(oid)) return "option option-wrong";
  return "option";
}

function renderResult(data) {
  $("result-title").textContent = data.topic.title;
  $("result-meta").textContent = `${data.student} · ${t("result_meta_review")}`;
  $("result-score").textContent = `${data.score} / ${data.max_score}`;
  $("result-percent").textContent = `${data.percent}%`;
  $("result-time").textContent = formatTime(data.duration_sec);

  $("result-review").innerHTML = data.review.map((item, idx) => {
    const badge = item.is_correct
      ? `<span class="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-bold text-emerald-700">${t("badge_correct")}</span>`
      : `<span class="rounded-full bg-rose-50 px-2 py-0.5 text-xs font-bold text-rose-700">${t("badge_wrong")}</span>`;
    const diffBadge = `<span class="badge ${DIFF_CLASSES[item.difficulty]||"badge-medium"}">${getDiffLabel(item.difficulty)}</span>`;
    const options = item.options.map(o => {
      const mark = item.correct.includes(o.id) ? t("label_correct_answer") : item.selected.includes(o.id) ? t("label_your_answer") : "";
      return `<div class="${optionClass(o.id, item.selected, item.correct)} pointer-events-none">
        <span class="text-sm">${escapeHtml(o.text)}</span>
        ${mark ? `<span class="ml-auto text-[11px] font-semibold uppercase text-slate-500">${mark}</span>` : ""}
      </div>`;
    }).join("");
    const explId = `expl-${idx}`;
    const explanation = item.explanation
      ? `<div class="mt-3">
           <button type="button" class="explanation-toggle" onclick="document.getElementById('${explId}').classList.toggle('open')">
             ${t("toggle_explanation")}
           </button>
           <div id="${explId}" class="explanation-content">
             <div class="explanation-box">${escapeHtml(item.explanation)}</div>
           </div>
         </div>`
      : "";
    return `
      <article class="rounded-2xl border border-slate-200 bg-white p-5">
        <div class="mb-2 flex items-center justify-between gap-2">
          <div class="flex items-center gap-2">
            <p class="text-xs font-semibold text-slate-500">${t("quiz_kicker")} #${idx+1}</p>
            ${diffBadge}
          </div>
          ${badge}
        </div>
        <h4 class="font-bold">${escapeHtml(item.text)}</h4>
        <div class="mt-3 space-y-2">${options}</div>
        ${explanation}
      </article>`;
  }).join("");
}

/* ── My Results (student) ──────────────────────────────────────────── */
async function renderMyResults() {
  const d = await api("/api/my-results");
  const body = $("my-results");
  if (!d.results.length) {
    body.innerHTML = `<tr><td class="py-3 text-slate-400" colspan="4">${t("my_results_empty")}</td></tr>`;
    return;
  }
  body.innerHTML = d.results.map(r => `
    <tr class="border-t border-slate-100">
      <td class="py-2.5 pr-3">${escapeHtml(r.topic_title)}</td>
      <td class="py-2.5 pr-3">${r.score}/${r.max_score}</td>
      <td class="py-2.5 pr-3">${r.percent}%</td>
      <td class="py-2.5">${formatTime(r.duration_sec)}</td>
    </tr>`).join("");
}

/* ── Competency Map ────────────────────────────────────────────────── */
async function renderCompetencyMap() {
  const section = $("competency-section");
  if (!section) return;
  try {
    const d = await api("/api/competency-map");
    if (!d.topics.length) {
      section.innerHTML = `<div class="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm text-center">
        <p class="text-slate-400">${t("competency_empty")}</p></div>`;
      return;
    }
    const pct = d.overall_percent;
    const circumference = 2 * Math.PI * 65;
    const offset = circumference - (pct / 100) * circumference;
    const gaugeColor = pct >= 80 ? "#16a34a" : pct >= 60 ? "#2563eb" : pct >= 40 ? "#f59e0b" : "#ef4444";

    const topicBars = d.topics.map(topic => {
      const w = Math.min(100, Math.max(2, topic.percent));
      const trendHtml = `<span class="trend-icon ${TREND_CLASSES[topic.trend]||""}">${getTrendLabel(topic.trend)}</span>`;
      const worstHtml = topic.worst_questions.length
        ? topic.worst_questions.map(q => `<li class="truncate text-xs text-slate-500">×${q.count}: ${escapeHtml(q.text)}</li>`).join("")
        : "";
      return `
        <div class="fade-in">
          <div class="flex items-center justify-between mb-1">
            <span class="text-sm font-bold">${escapeHtml(topic.title)}</span>
            <div class="flex items-center gap-2">
              ${trendHtml}
              <span class="text-sm font-extrabold">${topic.percent}%</span>
            </div>
          </div>
          <div class="competency-bar level-${topic.level}"><span style="width:${w}%"></span></div>
          <div class="flex items-center gap-2 mt-1">
            <span class="badge ${topic.level==='excellent'?'badge-easy':topic.level==='good'?'badge-teacher':topic.level==='attention'?'badge-medium':'badge-hard'}">${getLevelLabel(topic.level)}</span>
            <span class="text-xs text-slate-400">${topic.attempts} ${t("competency_attempts_count")}</span>
          </div>
          ${worstHtml ? `<ul class="mt-1 space-y-0.5 ml-2">${worstHtml}</ul>` : ""}
        </div>`;
    }).join("");

    const strengths = d.strengths.map(s => `<span class="badge badge-easy">✓ ${escapeHtml(s)}</span>`).join(" ");
    const weaknesses = d.weaknesses.map(s => `<span class="badge badge-hard">✗ ${escapeHtml(s)}</span>`).join(" ");
    const recs = d.recommendations.map(r => `<li class="text-sm text-slate-600">${escapeHtml(r)}</li>`).join("");

    section.innerHTML = `
      <div class="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
        <p class="text-xs font-semibold uppercase tracking-wider text-brand-600">${t("competency_badge")}</p>
        <h2 class="mt-1 text-2xl font-extrabold">${t("competency_title")}</h2>
        <p class="mt-1 text-sm text-slate-500">${t("competency_desc")}</p>
        <div class="mt-6 flex flex-col items-center gap-8 sm:flex-row sm:items-start">
          <div class="gauge-container shrink-0">
            <svg class="gauge-svg" width="160" height="160" viewBox="0 0 160 160">
              <circle class="gauge-bg" cx="80" cy="80" r="65"/>
              <circle class="gauge-fill" cx="80" cy="80" r="65"
                stroke="${gaugeColor}"
                stroke-dasharray="${circumference}"
                stroke-dashoffset="${offset}"/>
            </svg>
            <div class="gauge-label">
              <span class="text-3xl font-extrabold" style="color:${gaugeColor}">${pct}%</span>
              <span class="text-xs text-slate-500">${t("gauge_label")}</span>
            </div>
          </div>
          <div class="flex-1 w-full space-y-4">${topicBars}</div>
        </div>
        ${(strengths||weaknesses) ? `
          <div class="mt-6 flex flex-wrap gap-3">
            ${strengths} ${weaknesses}
          </div>` : ""}
        ${recs ? `
          <div class="mt-5">
            <p class="text-xs font-semibold uppercase text-slate-400 mb-2">${t("competency_recs_title")}</p>
            <ul class="space-y-1.5 list-disc list-inside">${recs}</ul>
          </div>` : ""}
      </div>`;
  } catch(e) {
    section.innerHTML = `<p class="text-sm text-slate-400">Ошибка: ${escapeHtml(e.message)}</p>`;
  }
}

/* ── Teacher: tabs ─────────────────────────────────────────────────── */
function setTeacherTab(name) {
  state.teacherTab = name;
  ["dashboard","topics","questions","users"].forEach(tabKey => {
    const tab = $(`teacher-tab-${tabKey}`), pane = $(`teacher-${tabKey}`);
    if (tab) tab.classList.toggle("teacher-tab-active", tabKey === name);
    if (pane) pane.classList.toggle("hidden", tabKey !== name);
  });
}

/* ── Teacher: dashboard ────────────────────────────────────────────── */
async function renderTeacherDashboard(stats, topicsData) {
  $("stat-total").textContent = stats.total_attempts;
  $("stat-weak").textContent = stats.weak_topics.length > 0
    ? stats.weak_topics.join(" · ")
    : stats.total_attempts ? t("stat_no_weak") : t("stat_no_data");

  $("stat-topics").innerHTML = stats.topics.map(topic => {
    const w = Math.min(100, topic.avg_percent);
    const weak = topic.is_weak ? `<span class="badge badge-hard">${t("stat_weak_badge")}</span>` : "";
    const hardest = topic.hardest_questions.length
      ? topic.hardest_questions.map(q => `<li class="truncate">×${q.errors}: ${escapeHtml(q.text)}</li>`).join("")
      : `<li class="text-slate-400">${t("stat_no_errors")}</li>`;
    return `
      <article class="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm">
        <div class="flex items-start justify-between gap-2">
          <h3 class="font-extrabold">${escapeHtml(topic.title)}</h3>${weak}
        </div>
        <p class="mt-1 text-xs text-slate-500">${topic.attempts} ${t("competency_attempts_count")} · ${t("stat_avg_time")} ${formatTime(topic.avg_time_sec)}</p>
        <div class="mt-4 flex items-center gap-3">
          <div class="bar flex-1"><span style="width:${w}%"></span></div>
          <span class="text-sm font-extrabold">${topic.avg_percent}%</span>
        </div>
        <p class="mt-4 text-xs font-semibold uppercase text-slate-400">${t("stat_hardest")}</p>
        <ul class="mt-1 space-y-1 text-sm text-slate-600">${hardest}</ul>
      </article>`;
  }).join("");

  const recent = $("stat-recent");
  recent.innerHTML = stats.recent.length
    ? stats.recent.map(r => `
      <tr class="border-t border-slate-100">
        <td class="py-2.5 pr-3 font-semibold">${escapeHtml(r.student)}</td>
        <td class="py-2.5 pr-3">${escapeHtml(r.topic_title)}</td>
        <td class="py-2.5 pr-3">${r.score}/${r.max_score}</td>
        <td class="py-2.5 pr-3">${r.percent}%</td>
        <td class="py-2.5">${formatTime(r.duration_sec)}</td>
      </tr>`).join("")
    : `<tr><td class="py-3 text-slate-400" colspan="5">${t("stat_recent_empty")}</td></tr>`;
}

/* ── Teacher: users ────────────────────────────────────────────────── */
function renderUsers(stats) {
  $("users-table").innerHTML = stats.users.map(u => {
    const role = u.role === "teacher" ? t("role_teacher") : t("role_student");
    const avg = u.avg_percent == null ? "—" : `${u.avg_percent}%`;
    const compBtn = u.role === "student" && u.attempts > 0
      ? `<button type="button" class="btn-ghost btn-sm text-xs" onclick="showStudentCompetency(${u.id},'${escapeHtml(u.username)}')">${t("btn_student_comp")}</button>`
      : '';
    return `
      <tr class="border-t border-slate-100">
        <td class="py-2.5 pr-3 font-semibold">${escapeHtml(u.username)}</td>
        <td class="py-2.5 pr-3"><span class="badge ${u.role==='teacher'?'badge-teacher':'badge-student'}">${role}</span></td>
        <td class="py-2.5 pr-3">${u.attempts}</td>
        <td class="py-2.5 pr-3">${avg}</td>
        <td class="py-2.5">${compBtn}</td>
      </tr>`;
  }).join("");
}

/* ── Teacher: student competency detail ────────────────────────────── */
async function showStudentCompetency(studentId, studentName) {
  try {
    const d = await api(`/api/student-competency/${studentId}`);
    if (!d.topics.length) {
      alert(`${studentName} әлі тест тапсырған жоқ.`);
      return;
    }
    const pct = d.overall_percent;
    const circumference = 2 * Math.PI * 65;
    const offset = circumference - (pct / 100) * circumference;
    const gaugeColor = pct >= 80 ? "#16a34a" : pct >= 60 ? "#2563eb" : pct >= 40 ? "#f59e0b" : "#ef4444";

    const topicBars = d.topics.filter(item => item.attempts > 0).map(topic => {
      const w = Math.min(100, Math.max(2, topic.percent));
      const trendHtml = `<span class="trend-icon ${TREND_CLASSES[topic.trend]||""}">${getTrendLabel(topic.trend)}</span>`;
      const worstHtml = topic.worst_questions.length
        ? topic.worst_questions.map(q => `<li class="truncate text-xs text-slate-500">×${q.count}: ${escapeHtml(q.text)}</li>`).join("")
        : "";
      return `
        <div class="fade-in">
          <div class="flex items-center justify-between mb-1">
            <span class="text-sm font-bold">${escapeHtml(topic.title)}</span>
            <div class="flex items-center gap-2">
              ${trendHtml}
              <span class="text-sm font-extrabold">${topic.percent}%</span>
            </div>
          </div>
          <div class="competency-bar level-${topic.level}"><span style="width:${w}%"></span></div>
          <div class="flex items-center gap-2 mt-1">
            <span class="badge ${topic.level==='excellent'?'badge-easy':topic.level==='good'?'badge-teacher':topic.level==='attention'?'badge-medium':'badge-hard'}">${getLevelLabel(topic.level)}</span>
            <span class="text-xs text-slate-400">${topic.attempts} ${t("competency_attempts_count")}</span>
          </div>
          ${worstHtml ? `<ul class="mt-1 space-y-0.5 ml-2">${worstHtml}</ul>` : ""}
        </div>`;
    }).join("");

    const strengths = (d.strengths||[]).map(s => `<span class="badge badge-easy">✓ ${escapeHtml(s)}</span>`).join(" ");
    const weaknesses = (d.weaknesses||[]).map(s => `<span class="badge badge-hard">✗ ${escapeHtml(s)}</span>`).join(" ");
    const recs = (d.recommendations||[]).map(r => `<li class="text-sm text-slate-600">${escapeHtml(r)}</li>`).join("");

    const modal = $("student-competency-modal");
    $("student-comp-title").textContent = `${t("student_comp_title")}: ${studentName}`;
    $("student-comp-body").innerHTML = `
      <div class="flex flex-col items-center gap-8 sm:flex-row sm:items-start">
        <div class="gauge-container shrink-0">
          <svg class="gauge-svg" width="160" height="160" viewBox="0 0 160 160">
            <circle class="gauge-bg" cx="80" cy="80" r="65"/>
            <circle class="gauge-fill" cx="80" cy="80" r="65"
              stroke="${gaugeColor}"
              stroke-dasharray="${circumference}"
              stroke-dashoffset="${offset}"/>
          </svg>
          <div class="gauge-label">
            <span class="text-3xl font-extrabold" style="color:${gaugeColor}">${pct}%</span>
            <span class="text-xs text-slate-500">${t("gauge_label")}</span>
          </div>
        </div>
        <div class="flex-1 w-full space-y-4">${topicBars}</div>
      </div>
      ${(strengths||weaknesses) ? `
        <div class="mt-6 flex flex-wrap gap-3">
          ${strengths} ${weaknesses}
        </div>` : ""}
      ${recs ? `
        <div class="mt-5">
          <p class="text-xs font-semibold uppercase text-slate-400 mb-2">${t("competency_recs_title")}</p>
          <ul class="space-y-1.5 list-disc list-inside">${recs}</ul>
        </div>` : ""}`;
    modal.classList.remove("hidden");
  } catch(e) { alert('Ошибка: ' + e.message); }
}

window.showStudentCompetency = showStudentCompetency;

/* ── Teacher: topics CRUD ──────────────────────────────────────────── */
async function renderTopicsList() {
  const d = await api("/api/topics");
  const container = $("topics-list");
  container.innerHTML = d.topics.map(topicItem => `
    <div class="flex items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div class="flex items-center gap-3 min-w-0">
        <div class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-brand-600">
          ${iconSvg(topicItem.icon)}
        </div>
        <div class="min-w-0">
          <p class="font-bold truncate">${escapeHtml(topicItem.title)}</p>
          <p class="text-xs text-slate-500 truncate">${escapeHtml(topicItem.subtitle)} · ${topicItem.question_count} ${t("unit_questions")} · ${Math.round(topicItem.time_limit_sec/60)} ${t("unit_min")}</p>
        </div>
      </div>
      <div class="flex gap-1 shrink-0">
        <button type="button" class="btn-ghost btn-sm" onclick="openTopicEdit('${escapeHtml(topicItem.id)}')">✏️</button>
        <button type="button" class="btn-danger btn-sm" onclick="confirmDeleteTopic('${escapeHtml(topicItem.id)}','${escapeHtml(topicItem.title)}')">🗑</button>
      </div>
    </div>`).join("");
}

function openTopicModal(topic) {
  state.editingTopic = topic;
  $("topic-modal-title").textContent = topic ? t("modal_topic_edit") : t("modal_topic_add");
  $("topic-form-id").value = topic?.id || "";
  $("topic-form-title").value = topic?.title || "";
  $("topic-form-subtitle").value = topic?.subtitle || "";
  $("topic-form-icon").value = topic?.icon || "hash";
  $("topic-form-time").value = topic?.time_limit_sec || 480;
  $("topic-error").classList.add("hidden");
  $("topic-modal").classList.remove("hidden");
}

window.openTopicEdit = async function(topicId) {
  try {
    const d = await api("/api/topics");
    const foundTopic = d.topics.find(x => x.id === topicId);
    if (foundTopic) openTopicModal(foundTopic);
  } catch(e) { alert(e.message); }
};

window.confirmDeleteTopic = function(id, title) {
  $("delete-text").textContent = t("modal_delete_confirm_topic", { title });
  state.deleteCallback = async () => {
    await api(`/api/topics/manage/${id}`, {method:"DELETE"});
    await renderTopicsList();
    await loadQuestionFilters();
    await renderQuestionsList();
  };
  $("delete-modal").classList.remove("hidden");
};

/* ── Teacher: questions CRUD с динамическими options ──────────────── */
async function loadQuestionFilters() {
  const d = await api("/api/topics");
  const sel = $("filter-topic");
  const qTopicSel = $("q-topic");
  const opts = d.topics.map(tTopic => `<option value="${escapeHtml(tTopic.id)}">${escapeHtml(tTopic.title)}</option>`).join("");
  sel.innerHTML = `<option value="">${t("filter_all_topics")}</option>` + opts;
  qTopicSel.innerHTML = opts;
}

async function renderQuestionsList() {
  const topicId = $("filter-topic").value;
  const search = $("filter-search").value.trim();
  const difficulty = $("filter-difficulty").value;
  let url = "/api/questions/list?";
  if (topicId) url += `topic_id=${encodeURIComponent(topicId)}&`;
  if (search) url += `search=${encodeURIComponent(search)}&`;
  if (difficulty) url += `difficulty=${encodeURIComponent(difficulty)}&`;
  try {
    const d = await api(url);
    const tbody = $("questions-table");
    if (!d.questions.length) {
      tbody.innerHTML = `<tr><td class="py-3 text-slate-400" colspan="5">${t("questions_empty")}</td></tr>`;
      return;
    }
    tbody.innerHTML = d.questions.map(q => `
      <tr class="border-t border-slate-100">
        <td class="py-2.5 pr-3 max-w-[280px]"><p class="truncate-2 text-sm">${escapeHtml(q.text)}</p></td>
        <td class="py-2.5 pr-3 text-xs text-slate-500">${escapeHtml(q.topic_title||q.topic_id)}</td>
        <td class="py-2.5 pr-3"><span class="badge ${q.type==='single'?'badge-teacher':'badge-student'}">${q.type==='single'? t("type_single") : t("type_multiple")}</span></td>
        <td class="py-2.5 pr-3"><span class="badge ${DIFF_CLASSES[q.difficulty]||'badge-medium'}">${getDiffLabel(q.difficulty)}</span></td>
        <td class="py-2.5 text-right">
          <div class="flex gap-1 justify-end">
            <button type="button" class="btn-ghost btn-sm" onclick="openQuestionEdit('${escapeHtml(q.qid)}')">✏️</button>
            <button type="button" class="btn-danger btn-sm" onclick="confirmDeleteQuestion('${escapeHtml(q.qid)}')">🗑</button>
          </div>
        </td>
      </tr>`).join("");
  } catch(e) { console.error(e); }
}

/* ── Динамический редактор вариантов ответов ────────────────────────── */
let currentEditorOptions = [];

function syncOptionsFromDOM() {
  const container = $("q-options");
  if (!container) return;
  const rows = container.querySelectorAll(".q-option-row");
  rows.forEach((row, idx) => {
    if (idx < currentEditorOptions.length) {
      const input = row.querySelector(".q-option-text");
      const check = row.querySelector(".q-correct-input");
      if (input) currentEditorOptions[idx].text = input.value;
      if (check) currentEditorOptions[idx].is_correct = check.checked;
    }
  });
}

function renderOptionEditor() {
  const container = $("q-options");
  const qType = $("q-type").value;
  const inputType = qType === "single" ? "radio" : "checkbox";
  const letters = ["A", "B", "C", "D", "E", "F"];

  container.innerHTML = currentEditorOptions.map((opt, idx) => {
    const letter = letters[idx] || String(idx + 1);
    const checked = opt.is_correct ? "checked" : "";
    const removeBtn = currentEditorOptions.length > 2
      ? `<button type="button" class="text-slate-400 hover:text-rose-600 transition p-1" onclick="removeOptionRow(${idx})" title="${t("btn_remove_option")}">
           <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
             <path d="M18 6L6 18M6 6l12 12" stroke-linecap="round"/>
           </svg>
         </button>`
      : '';

    return `
      <div class="q-option-row flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50/50 p-2 sm:gap-3">
        <label class="flex items-center gap-1.5 cursor-pointer shrink-0" title="${t("opt_correct_tooltip")}">
          <input type="${inputType}" name="q-correct-option" class="q-correct-input h-4 w-4 text-brand-600 focus:ring-brand-500 rounded" data-index="${idx}" ${checked} />
          <span class="w-5 text-center text-xs font-extrabold text-slate-500">${letter}</span>
        </label>
        <input class="field q-option-text flex-1 text-sm bg-white" placeholder="${t("ph_option_text")}" maxlength="240" value="${escapeHtml(opt.text)}" data-index="${idx}" />
        ${removeBtn}
      </div>`;
  }).join("");

  // Привязываем слушатели для радиокнопок/чекбоксов
  container.querySelectorAll(".q-correct-input").forEach(input => {
    input.addEventListener("change", (e) => {
      const idx = Number(e.target.dataset.index);
      if (qType === "single") {
        currentEditorOptions.forEach((o, i) => { o.is_correct = (i === idx); });
      } else {
        currentEditorOptions[idx].is_correct = e.target.checked;
      }
    });
  });

  // Обновляем текст при вводе
  container.querySelectorAll(".q-option-text").forEach(input => {
    input.addEventListener("input", (e) => {
      const idx = Number(e.target.dataset.index);
      if (currentEditorOptions[idx]) {
        currentEditorOptions[idx].text = e.target.value;
      }
    });
  });

  // Управляем доступностью кнопки добавления варианта (макс. 6)
  const btnAddRow = $("btn-add-option-row");
  if (btnAddRow) {
    btnAddRow.classList.toggle("opacity-50", currentEditorOptions.length >= 6);
    btnAddRow.disabled = currentEditorOptions.length >= 6;
  }
}

window.removeOptionRow = function(index) {
  if (currentEditorOptions.length <= 2) return;
  syncOptionsFromDOM();
  currentEditorOptions.splice(index, 1);
  const qType = $("q-type").value;
  // Если удалили единственный правильный ответ в single, ставим первому
  if (qType === "single" && !currentEditorOptions.some(o => o.is_correct) && currentEditorOptions.length > 0) {
    currentEditorOptions[0].is_correct = true;
  }
  renderOptionEditor();
};

function addOptionRow() {
  if (currentEditorOptions.length >= 6) return;
  syncOptionsFromDOM();
  const letters = ["a", "b", "c", "d", "e", "f"];
  const newId = letters[currentEditorOptions.length] || `opt-${currentEditorOptions.length+1}`;
  currentEditorOptions.push({ id: newId, text: "", is_correct: false });
  renderOptionEditor();
}

function openQuestionModal(data) {
  state.editingQuestion = data;
  $("question-modal-title").textContent = data ? t("modal_q_edit") : t("modal_q_add");
  $("q-edit-qid").value = data?.qid || "";
  if (data?.topic_id) $("q-topic").value = data.topic_id;
  $("q-type").value = data?.type || "single";
  $("q-difficulty").value = data?.difficulty || "medium";
  $("q-text").value = data?.text || "";
  $("q-explanation").value = data?.explanation || "";

  if (data && data.options && data.options.length) {
    currentEditorOptions = data.options.map((o, idx) => ({
      id: o.id || chr(ord("a") + idx),
      text: o.text || "",
      is_correct: Boolean(o.is_correct) || (data.correct || []).includes(o.id),
    }));
  } else {
    currentEditorOptions = [
      { id: "a", text: "", is_correct: true },
      { id: "b", text: "", is_correct: false },
      { id: "c", text: "", is_correct: false },
      { id: "d", text: "", is_correct: false },
    ];
  }

  renderOptionEditor();
  $("question-error").classList.add("hidden");
  $("question-ok").classList.add("hidden");
  $("question-modal").classList.remove("hidden");
}

window.openQuestionEdit = async function(qid) {
  try {
    const d = await api("/api/questions/list");
    const q = d.questions.find(x => x.qid === qid);
    if (q) openQuestionModal(q);
  } catch(e) { alert(e.message); }
};

window.confirmDeleteQuestion = function(qid) {
  $("delete-text").textContent = t("modal_delete_confirm_q");
  state.deleteCallback = async () => {
    await api(`/api/questions/${qid}`, {method:"DELETE"});
    await renderQuestionsList();
  };
  $("delete-modal").classList.remove("hidden");
};

/* ── Import / Export ───────────────────────────────────────────────── */
function downloadFile(data, filename, mime) {
  const blob = new Blob([data], {type: mime});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

async function exportJSON() {
  try {
    const r = await fetch("/api/export/json", {credentials:"same-origin"});
    const text = await r.text();
    downloadFile(text, "infotrainer-export.json", "application/json");
  } catch(e) { alert(e.message); }
}

async function exportCSV() {
  try {
    const r = await fetch("/api/export/csv", {credentials:"same-origin"});
    const text = await r.text();
    downloadFile(text, "infotrainer-export.csv", "text/csv");
  } catch(e) { alert(e.message); }
}

async function importJSON(file) {
  try {
    const text = await file.text();
    const data = JSON.parse(text);
    const result = await api("/api/import/json", {method:"POST", body: JSON.stringify(data)});
    alert(`Импорт завершён: ${result.imported_topics||0} тем, ${result.imported_questions||0} вопросов.`);
    await renderTopicsList();
    await loadQuestionFilters();
    await renderQuestionsList();
  } catch(e) { alert("Ошибка импорта: " + e.message); }
}

async function importCSV(file) {
  try {
    const formData = new FormData();
    formData.append('file', file);
    const r = await fetch('/api/import/csv', {
      method: 'POST',
      credentials: 'same-origin',
      body: formData,
    });
    const result = await r.json();
    if (!r.ok) throw new Error(result.error || 'Ошибка импорта');
    alert(`Импорт CSV завершён: ${result.imported_topics||0} тем, ${result.imported_questions||0} вопросов.`);
    await renderTopicsList();
    await loadQuestionFilters();
    await renderQuestionsList();
  } catch(e) { alert('Ошибка импорта CSV: ' + e.message); }
}

/* ── Teacher: full render ──────────────────────────────────────────── */
async function renderTeacher() {
  const [stats, topicsData] = await Promise.all([api("/api/stats"), api("/api/topics")]);
  await renderTeacherDashboard(stats, topicsData);
  renderUsers(stats);
  await renderTopicsList();
  await loadQuestionFilters();
  await renderQuestionsList();
}

/* ── Navigation ────────────────────────────────────────────────────── */
async function goHome() {
  stopTimer(); setExitOpen(false);
  if (!state.user) { showView("auth"); return; }
  if (state.user.role === "teacher") { await goTeacher(); return; }
  showView("home");
  const d = await api("/api/topics");
  state.topics = d.topics;
  renderTopics();
  await renderMyResults();
  await renderCompetencyMap();
}

async function goTeacher() {
  if (state.user?.role !== "teacher") return;
  stopTimer(); showView("teacher"); setTeacherTab("dashboard");
  await renderTeacher();
}

async function afterLogin(user) {
  state.user = user;
  if (user.role === "teacher") await goTeacher(); else await goHome();
}

/* ── Event Listeners ───────────────────────────────────────────────── */
$("tab-login").addEventListener("click", () => setAuthMode("login"));
$("tab-register").addEventListener("click", () => setAuthMode("register"));

$("auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = $("auth-username").value.trim();
  const password = $("auth-password").value;
  const role = document.querySelector('input[name="role"]:checked').value;
  const path = state.authMode === "register" ? "/api/register" : "/api/login";
  const body = state.authMode === "register" ? {username,password,role} : {username,password};
  try { const d = await api(path, {method:"POST", body: JSON.stringify(body)}); await afterLogin(d.user); }
  catch(e) { showAuthError(e.message); }
});

$("btn-logout").addEventListener("click", async () => {
  await api("/api/logout", {method:"POST"});
  stopTimer(); state.user = null; showView("auth");
});

$("logo-btn").addEventListener("click", goHome);
$("nav-home").addEventListener("click", goHome);
$("nav-teacher").addEventListener("click", goTeacher);
$("btn-home").addEventListener("click", goHome);

const btnCompetency = $("btn-competency");
if (btnCompetency) btnCompetency.addEventListener("click", async () => { await goHome(); });

$("btn-prev").addEventListener("click", () => { if (state.index > 0) { state.index -= 1; renderQuestion(); } });
$("btn-next").addEventListener("click", goNext);
$("btn-finish").addEventListener("click", submitQuiz);
$("exit-stay").addEventListener("click", () => setExitOpen(false));
$("exit-leave").addEventListener("click", leaveQuiz);

/* Переключатели языков */
$("lang-ru").addEventListener("click", () => applyLanguage("ru"));
$("lang-kk").addEventListener("click", () => applyLanguage("kk"));

/* Teacher tabs */
["dashboard","topics","questions","users"].forEach(tabKey => {
  const btn = $(`teacher-tab-${tabKey}`);
  if (btn) btn.addEventListener("click", () => setTeacherTab(tabKey));
});

/* Add topic */
const btnAddTopic = $("btn-add-topic");
if (btnAddTopic) btnAddTopic.addEventListener("click", () => openTopicModal(null));

$("topic-cancel").addEventListener("click", () => $("topic-modal").classList.add("hidden"));

$("topic-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("topic-error");
  err.classList.add("hidden");
  const id = $("topic-form-id").value.trim();
  const body = {
    title: $("topic-form-title").value.trim(),
    subtitle: $("topic-form-subtitle").value.trim(),
    icon: $("topic-form-icon").value,
    time_limit_sec: parseInt($("topic-form-time").value) || 480,
  };
  try {
    if (id) {
      await api(`/api/topics/manage/${id}`, {method:"PUT", body: JSON.stringify(body)});
    } else {
      await api("/api/topics/manage", {method:"POST", body: JSON.stringify(body)});
    }
    $("topic-modal").classList.add("hidden");
    await renderTopicsList();
    await loadQuestionFilters();
  } catch(e) { err.textContent = e.message; err.classList.remove("hidden"); }
});

/* Добавление строки варианта ответа */
const btnAddOptionRow = $("btn-add-option-row");
if (btnAddOptionRow) btnAddOptionRow.addEventListener("click", addOptionRow);

/* Смена типа вопроса (single / multiple) */
$("q-type").addEventListener("change", () => {
  syncOptionsFromDOM();
  const qType = $("q-type").value;
  if (qType === "single") {
    let hadCorrect = false;
    currentEditorOptions.forEach(o => {
      if (o.is_correct && !hadCorrect) {
        hadCorrect = true;
      } else if (o.is_correct && hadCorrect) {
        o.is_correct = false;
      }
    });
    if (!hadCorrect && currentEditorOptions.length > 0) {
      currentEditorOptions[0].is_correct = true;
    }
  }
  renderOptionEditor();
});

/* Add/edit question modal triggers */
const btnAddQuestion = $("btn-add-question");
if (btnAddQuestion) btnAddQuestion.addEventListener("click", () => openQuestionModal(null));

$("question-cancel").addEventListener("click", () => $("question-modal").classList.add("hidden"));

$("question-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  syncOptionsFromDOM();

  const err = $("question-error"), ok = $("question-ok");
  err.classList.add("hidden"); ok.classList.add("hidden");

  const qid = $("q-edit-qid").value.trim();
  const options = currentEditorOptions
    .map(o => ({ id: o.id, text: o.text.trim(), is_correct: Boolean(o.is_correct) }))
    .filter(o => o.text);

  if (options.length < 2) {
    err.textContent = "Нужно заполнить минимум 2 варианта ответа.";
    err.classList.remove("hidden");
    return;
  }

  const correctCount = options.filter(o => o.is_correct).length;
  const qType = $("q-type").value;
  if (qType === "single" && correctCount !== 1) {
    err.textContent = "Для вопроса с одним ответом выберите ровно один правильный вариант.";
    err.classList.remove("hidden");
    return;
  }
  if (qType === "multiple" && correctCount < 1) {
    err.textContent = "Для вопроса с несколькими ответами выберите хотя бы один правильный вариант.";
    err.classList.remove("hidden");
    return;
  }

  const body = {
    topic_id: $("q-topic").value,
    type: qType,
    difficulty: $("q-difficulty").value,
    text: $("q-text").value.trim(),
    explanation: $("q-explanation").value.trim(),
    options,
  };

  try {
    if (qid) {
      await api(`/api/questions/${qid}`, {method:"PUT", body: JSON.stringify(body)});
    } else {
      await api("/api/questions", {method:"POST", body: JSON.stringify(body)});
    }
    $("question-modal").classList.add("hidden");
    await renderQuestionsList();
  } catch(e) { err.textContent = e.message; err.classList.remove("hidden"); }
});

/* Question filters */
$("filter-topic").addEventListener("change", renderQuestionsList);
$("filter-difficulty").addEventListener("change", renderQuestionsList);
let searchTimer = null;
$("filter-search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(renderQuestionsList, 300);
});

/* Delete modal */
$("delete-cancel").addEventListener("click", () => { $("delete-modal").classList.add("hidden"); state.deleteCallback = null; });
$("delete-confirm").addEventListener("click", async () => {
  $("delete-modal").classList.add("hidden");
  if (state.deleteCallback) { try { await state.deleteCallback(); } catch(e) { alert(e.message); } state.deleteCallback = null; }
});

/* Import/Export */
$("btn-export-json").addEventListener("click", exportJSON);
$("btn-export-csv").addEventListener("click", exportCSV);
$("btn-import-json").addEventListener("click", () => {
  const f = $("import-file");
  f.accept = ".json";
  f.click();
});
$("btn-import-csv").addEventListener("click", () => {
  const f = $("import-file");
  f.accept = ".csv";
  f.click();
});

$("import-file").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (file) {
    if (file.name.endsWith(".csv")) {
      importCSV(file);
    } else {
      importJSON(file);
    }
    e.target.value = "";
  }
});

/* ── Hotkeys ───────────────────────────────────────────────────────── */
document.addEventListener("keydown", (e) => {
  if (state.view !== "quiz") return;
  if (state.exitOpen) { if (e.key === "Escape") { e.preventDefault(); setExitOpen(false); } return; }
  if (e.key === "Escape") { e.preventDefault(); setExitOpen(true); return; }
  if (e.key === "Enter") { e.preventDefault(); goNext(); return; }
  if (/^[1-6]$/.test(e.key)) {
    const q = state.questions[state.index], o = q?.options[Number(e.key)-1];
    if (o) { e.preventDefault(); toggleAnswer(q, o.id); }
  }
});

/* ── Boot ──────────────────────────────────────────────────────────── */
async function boot() {
  await loadTranslations();
  applyLanguage(state.lang);
  setAuthMode("login");
  try {
    const d = await api("/api/me");
    if (d.user) { await afterLogin(d.user); return; }
  } catch(_) {}
  showView("auth");
}

boot();
