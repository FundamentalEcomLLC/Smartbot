
(function () {
  function resolveScriptElement() {
    if (document.currentScript) {
      return document.currentScript;
    }
    const candidates = Array.from(document.querySelectorAll("script[data-bot-id]"));
    if (candidates.length) {
      return candidates[candidates.length - 1];
    }
    return null;
  }

  const scriptEl = resolveScriptElement();
  if (!scriptEl) {
    console.error("Chat widget: failed to locate chat embed <script> element.");
    return;
  }

  const botId = scriptEl.dataset.botId;
  const scriptUrl = new URL(scriptEl.src);
  const defaultApiBase = `${scriptUrl.origin}`;
  if (!botId) {
    console.error("Chat widget: missing data-bot-id attribute.");
    return;
  }

  const apiBase = scriptEl.dataset.apiBase || defaultApiBase;
  const SESSION_STORAGE_KEY = `chatbot-session-${botId}`;
  const AUTO_OPEN_STORAGE_KEY = `chatbot-auto-open-${botId}`;
  const AUTO_OPEN_DELAY_MS = Number(scriptEl.dataset.autoOpenDelayMs || 10000);
  const AUTO_WELCOME_MESSAGE =
    scriptEl.dataset.autoWelcomeMessage ||
    "Thanks for visiting us!";
  const sizePresets = [
    { id: "compact", label: "Compact", width: 320, height: 420 },
    { id: "comfort", label: "Comfort", width: 380, height: 520 },
    { id: "expanded", label: "Expanded", width: 460, height: 620 },
  ];

  const state = {
    sessionId: null,
    isOpen: false,
    isSending: false,
    activeSize: "expanded",
    customSize: null,
    panelSize: null,
    isResizing: false,
    hasConversation: false,
    panelEl: null,
    messagesEl: null,
    autoWelcomeShown: false,
    launcherTheme: "dark",
  };

  const LAUNCHER_THEMES = {
    dark: {
      id: "dark",
      background: "#0f172a",
      color: "#ffffff",
      border: "none",
      shadow: "0 22px 45px rgba(15,23,42,0.35)",
      shadowHover: "0 28px 55px rgba(15,23,42,0.45)",
    },
    light: {
      id: "light",
      background: "#f8fafc",
      color: "#111827",
      border: "1px solid rgba(15,23,42,0.18)",
      shadow: "0 22px 45px rgba(15,23,42,0.22)",
      shadowHover: "0 28px 55px rgba(15,23,42,0.3)",
    },
  };

  let autoContrastListenersBound = false;
  let contrastUpdateScheduled = false;

  function setLauncherTheme(theme) {
    if (!theme || theme.id === state.launcherTheme) {
      return;
    }
    const root = document.documentElement;
    root.style.setProperty("--chatbot-launcher-bg", theme.background);
    root.style.setProperty("--chatbot-launcher-fg", theme.color);
    root.style.setProperty("--chatbot-launcher-border", theme.border);
    root.style.setProperty("--chatbot-launcher-shadow", theme.shadow);
    root.style.setProperty("--chatbot-launcher-shadow-hover", theme.shadowHover);
    state.launcherTheme = theme.id;
  }

  function parseHexColor(value) {
    const hex = value.replace("#", "");
    if (hex.length !== 3 && hex.length !== 6) {
      return null;
    }
    const segments = hex.length === 3 ? hex.split("").map((char) => char + char) : hex.match(/.{2}/g);
    if (!segments) {
      return null;
    }
    const [r, g, b] = segments.map((segment) => parseInt(segment, 16));
    if ([r, g, b].some(Number.isNaN)) {
      return null;
    }
    return { r, g, b, a: 1 };
  }

  function parseRgbColor(value) {
    const match = value.match(/rgba?\(([^)]+)\)/i);
    if (!match) {
      return null;
    }
    const parts = match[1].split(",").map((part) => part.trim());
    if (parts.length < 3) {
      return null;
    }
    const parseChannel = (input) => {
      if (input.endsWith("%")) {
        return Math.round((parseFloat(input) / 100) * 255);
      }
      return parseFloat(input);
    };
    const r = parseChannel(parts[0]);
    const g = parseChannel(parts[1]);
    const b = parseChannel(parts[2]);
    const a = parts[3] !== undefined ? parseFloat(parts[3]) : 1;
    if ([r, g, b, a].some((channel) => Number.isNaN(channel))) {
      return null;
    }
    return { r, g, b, a };
  }

  function hslToRgb(h, s, l) {
    const saturation = s / 100;
    const lightness = l / 100;
    const c = (1 - Math.abs(2 * lightness - 1)) * saturation;
    const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
    const m = lightness - c / 2;
    let rPrime = 0;
    let gPrime = 0;
    let bPrime = 0;

    if (h >= 0 && h < 60) {
      rPrime = c;
      gPrime = x;
    } else if (h >= 60 && h < 120) {
      rPrime = x;
      gPrime = c;
    } else if (h >= 120 && h < 180) {
      gPrime = c;
      bPrime = x;
    } else if (h >= 180 && h < 240) {
      gPrime = x;
      bPrime = c;
    } else if (h >= 240 && h < 300) {
      rPrime = x;
      bPrime = c;
    } else {
      rPrime = c;
      bPrime = x;
    }

    return {
      r: Math.round((rPrime + m) * 255),
      g: Math.round((gPrime + m) * 255),
      b: Math.round((bPrime + m) * 255),
      a: 1,
    };
  }

  function parseHslColor(value) {
    const match = value.match(/hsla?\(([^)]+)\)/i);
    if (!match) {
      return null;
    }
    const parts = match[1].split(",").map((part) => part.trim());
    if (parts.length < 3) {
      return null;
    }
    const h = parseFloat(parts[0]);
    const s = parseFloat(parts[1]);
    const l = parseFloat(parts[2]);
    const a = parts[3] !== undefined ? parseFloat(parts[3]) : 1;
    if ([h, s, l, a].some((channel) => Number.isNaN(channel))) {
      return null;
    }
    const rgb = hslToRgb(h, s, l);
    rgb.a = a;
    return rgb;
  }

  function parseColor(value) {
    if (!value) {
      return null;
    }
    const normalized = value.trim().toLowerCase();
    if (normalized.startsWith("#")) {
      return parseHexColor(normalized);
    }
    if (normalized.startsWith("rgb")) {
      return parseRgbColor(normalized);
    }
    if (normalized.startsWith("hsl")) {
      return parseHslColor(normalized);
    }
    return null;
  }

  function relativeLuminance(rgb) {
    if (!rgb) {
      return null;
    }
    const transformChannel = (channel) => {
      const c = channel / 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    };
    const r = transformChannel(rgb.r);
    const g = transformChannel(rgb.g);
    const b = transformChannel(rgb.b);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }

  function effectiveBackgroundColor(target) {
    let node = target;
    while (node) {
      const style = window.getComputedStyle(node);
      const colorValue = style && style.backgroundColor;
      const parsed = parseColor(colorValue);
      if (parsed && (parsed.a ?? 1) > 0.05) {
        return { raw: colorValue, parsed };
      }
      node = node.parentElement;
    }
    const fallback = window.getComputedStyle(document.body || document.documentElement).backgroundColor;
    return { raw: fallback, parsed: parseColor(fallback) };
  }

  function samplePageBackgroundColor() {
    if (typeof document.elementFromPoint !== "function") {
      return effectiveBackgroundColor(document.body || document.documentElement);
    }
    const offsets = [
      { x: window.innerWidth - 32, y: window.innerHeight - 32 },
      { x: window.innerWidth - 80, y: window.innerHeight - 32 },
      { x: window.innerWidth - 32, y: window.innerHeight - 80 },
    ];
    for (const point of offsets) {
      const clampedX = Math.max(1, Math.min(point.x, window.innerWidth - 1));
      const clampedY = Math.max(1, Math.min(point.y, window.innerHeight - 1));
      const target = document.elementFromPoint(clampedX, clampedY);
      if (target) {
        if (target.closest && target.closest(".chatbot-launcher, .chatbot-panel")) {
          continue;
        }
        const result = effectiveBackgroundColor(target);
        if (result && result.parsed) {
          return result;
        }
      }
    }
    return effectiveBackgroundColor(document.body || document.documentElement);
  }

  function applyAutoContrastTheme() {
    try {
      const sample = samplePageBackgroundColor();
      if (!sample || !sample.parsed) {
        return;
      }
      const luminance = relativeLuminance(sample.parsed);
      if (luminance === null) {
        return;
      }
      const theme = luminance < 0.45 ? LAUNCHER_THEMES.light : LAUNCHER_THEMES.dark;
      setLauncherTheme(theme);
    } catch (err) {
      console.warn("Chat widget: auto-contrast failed", err);
    }
  }

  function scheduleAutoContrastUpdate() {
    if (contrastUpdateScheduled) {
      return;
    }
    contrastUpdateScheduled = true;
    requestAnimationFrame(() => {
      contrastUpdateScheduled = false;
      applyAutoContrastTheme();
    });
  }

  function readPersistedSession() {
    try {
      const raw = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (err) {
      return null;
    }
  }

  function persistSessionState() {
    if (!state.sessionId) {
      clearPersistedSession();
      return;
    }
    try {
      const payload = {
        sessionId: state.sessionId,
        hasConversation: Boolean(state.hasConversation),
      };
      window.sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(payload));
    } catch (err) {
      /* ignore storage issues */
    }
  }

  function clearPersistedSession() {
    try {
      window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
    } catch (err) {
      /* ignore storage issues */
    }
  }

  async function closePersistedSessionIfNeeded() {
    const cached = readPersistedSession();
    if (!cached || !cached.sessionId) {
      return;
    }
    if (!cached.hasConversation) {
      clearPersistedSession();
      return;
    }
    try {
      await closeSessionRequest({ sessionId: cached.sessionId, skipStateReset: true });
    } catch (err) {
      console.warn("Chat widget: failed to close previous session", err);
    }
  }

  function hasAutoOpened() {
    try {
      return window.sessionStorage.getItem(AUTO_OPEN_STORAGE_KEY) === "1";
    } catch (err) {
      return false;
    }
  }

  function markAutoOpened() {
    try {
      window.sessionStorage.setItem(AUTO_OPEN_STORAGE_KEY, "1");
    } catch (err) {
      /* ignore storage issues */
    }
  }

  const styles = `
    @import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap");
    :root {
      --cc-surface: #ffffff;
      --cc-primary: #2563eb;
      --cc-primary-dark: #1d4ed8;
      --cc-dark: #0f172a;
      --cc-border: rgba(15, 23, 42, 0.12);
      --cc-muted: #64748b;
      --cc-radius: 24px;
      --cc-font: "Inter", "Segoe UI", system-ui, -apple-system, sans-serif;
      --chatbot-launcher-bg: var(--cc-dark);
      --chatbot-launcher-fg: #ffffff;
      --chatbot-launcher-border: none;
      --chatbot-launcher-shadow: 0 22px 45px rgba(15,23,42,0.35);
      --chatbot-launcher-shadow-hover: 0 28px 55px rgba(15,23,42,0.45);
    }
    .chatbot-launcher {
      position: fixed;
      bottom: 28px;
      right: 28px;
      width: 64px;
      height: 64px;
      border-radius: 18px;
      background: var(--chatbot-launcher-bg, var(--cc-dark));
      color: var(--chatbot-launcher-fg, #fff);
      border: var(--chatbot-launcher-border, none);
      cursor: pointer;
      box-shadow: var(--chatbot-launcher-shadow, 0 22px 45px rgba(15,23,42,0.35));
      font-size: 28px;
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 2147483647;
      transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .chatbot-launcher:hover {
      transform: translateY(-2px);
      box-shadow: var(--chatbot-launcher-shadow-hover, 0 28px 55px rgba(15,23,42,0.45));
    }
    .chatbot-panel {
      position: fixed;
      top: 12px;
      left: 12px;
      border-radius: var(--cc-radius);
      background: var(--cc-surface);
      box-shadow: 0 35px 85px rgba(15,23,42,0.35);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      font-family: var(--cc-font);
      z-index: 2147483647;
      border: 1px solid var(--cc-border);
      opacity: 0;
      transform: translateY(12px);
      transition: opacity 0.25s ease, transform 0.25s ease;
      pointer-events: none;
    }
    .chatbot-panel.is-open {
      opacity: 1;
      transform: translateY(0);
      pointer-events: auto;
    }
    .chatbot-header {
      padding:45px 24px 12px 24px;
      display: flex;
      justify-content: space-between;
      gap: 1rem;
      align-items: flex-start;
      border-bottom: 1px solid rgba(15, 23, 42, 0.06);
    }
    .chatbot-heading {
      margin: 0;
      font-size: 1rem;
      letter-spacing: 0.15em;
      text-transform: uppercase;
      color: var(--cc-dark);
      font-weight: 700;
    }
    .chatbot-header-right {
      display: inline-flex;
      flex-direction: row;
      align-items: center;
      gap: 0.5rem;
      margin-left: auto;
    }
    .chatbot-controls-row {
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
    }
    .chatbot-chip {
      padding: 0.3rem 0.9rem;
      border-radius: 999px;
      background: #fee2e2;
      color: #b91c1c;
      font-size: 0.85rem;
      font-weight: 600;
      border: none;
      cursor: pointer;
      font-family: var(--cc-font);
      /* Hide the size chip from the UI, but keep it in the DOM
         so existing JS (size presets, resizer label updates) keeps working */
      display: none;
    }
    .chatbot-status-pill {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      padding: 0.2rem 0.7rem;
      border-radius: 999px;
      background: rgba(16, 185, 129, 0.12);
      color: #059669;
      font-size: 0.8rem;
      font-weight: 600;
    }
    .chatbot-status-pill::before {
      content: '';
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: currentColor;
    }
    .chatbot-close {
      border: none;
      background: transparent;
      color: var(--cc-muted);
      font-size: 1.2rem;
      cursor: pointer;
      width: 32px;
      height: 32px;
      border-radius: 50%;
      transition: background 0.2s ease;
    }
    .chatbot-close:hover {
      background: rgba(15,23,42,0.06);
    }
    .chatbot-messages {
      flex: 1;
      overflow-y: auto;
      padding: 0 24px 24px 24px;
      background: linear-gradient(180deg, #f8fafc 0%, #eef2ff 100%);
    }
    .chatbot-input {
      display: flex;
      gap: 12px;
      padding: 16px 24px;
      border-top: 1px solid #e2e8f0;
      background: #fff;
    }
    .chatbot-input input {
      flex: 1;
      border: 1px solid #cbd5f5;
      border-radius: 16px;
      padding: 12px 14px;
      font-size: 0.95rem;
      font-family: var(--cc-font);
    }
    .chatbot-input input:focus {
      outline: none;
      border-color: var(--cc-primary);
      box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.18);
    }
    .chatbot-input button {
      border: none;
      border-radius: 14px;
      padding: 0 18px;
      background: var(--cc-primary);
      color: #fff;
      cursor: pointer;
      font-weight: 600;
      min-width: 92px;
      height: 44px;
    }
    .chatbot-input button:hover {
      background: var(--cc-primary-dark);
    }
    .chatbot-message {
      margin-top: 18px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .chatbot-message.user {
      align-items: flex-end;
    }
    .chatbot-bubble {
      max-width: 85%;
      padding: 12px 16px;
      border-radius: 18px;
      background: rgba(15,23,42,0.07);
      color: var(--cc-dark);
      font-size: 0.95rem;
      line-height: 1.45;
      white-space: pre-wrap;
      word-break: break-word;
      overflow-wrap: anywhere;
    }
    .chatbot-message.user .chatbot-bubble {
      background: var(--cc-primary);
      color: #fff;
    }
    .chatbot-resizer {
      position: absolute;
      top: 18px;
      left: 16px;
      width: 22px;
      height: 22px;
      border-radius: 8px;
      border: 1px solid rgba(37,99,235,0.25);
      background: rgba(37,99,235,0.1);
      cursor: nwse-resize;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 6px 14px rgba(37,99,235,0.25);
    }
    .chatbot-resizer::after {
      content: '';
      width: 60%;
      height: 60%;
      border-right: 2px solid rgba(37,99,235,0.7);
      border-bottom: 2px solid rgba(37,99,235,0.7);
      border-radius: 2px;
      transform: rotate(180deg);
    }
    .chatbot-footer {
      margin: 0;
      padding: 0 24px 18px 24px;
      font-size: 0.78rem;
      color: var(--cc-muted);
      text-align: center;
    }
    @media (max-width: 640px) {
      .chatbot-launcher {
        right: 16px;
        bottom: 16px;
      }
      .chatbot-header {
        flex-direction: column;
        align-items: flex-start;
      }
      .chatbot-header-right {
        width: 100%;
        align-items: flex-start;
      }
    }
  `;

  function injectStyles() {
    if (document.getElementById("chatbot-widget-styles")) {
      return;
    }
    const styleTag = document.createElement("style");
    styleTag.id = "chatbot-widget-styles";
    styleTag.textContent = styles;
    document.head.appendChild(styleTag);
  }

  function clampSize(dimensions) {
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 1024;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 768;
    const horizontalPadding = viewportWidth < 600 ? 32 : 72;
    const verticalPadding = viewportHeight < 700 ? 96 : 180;
    const maxWidth = Math.max(260, viewportWidth - horizontalPadding);
    const maxHeight = Math.max(360, viewportHeight - verticalPadding);
    const minWidth = Math.min(360, maxWidth);
    const minHeight = Math.min(420, maxHeight);
    const width = Math.min(Math.max(dimensions.width || minWidth, minWidth), maxWidth);
    const height = Math.min(Math.max(dimensions.height || minHeight, minHeight), maxHeight);
    return { width, height };
  }

  function positionPanel(panel, overrides = {}) {
    if (!panel) {
      return;
    }
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 1024;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 768;
    const horizontalMargin = viewportWidth < 600 ? 16 : 32;
    const verticalMargin = viewportHeight < 700 ? 72 : 120;
    const width = overrides.width ?? parseFloat(panel.style.width) ?? panel.offsetWidth ?? 360;
    const height = overrides.height ?? parseFloat(panel.style.height) ?? panel.offsetHeight ?? 520;

    let left;
    if (typeof overrides.left === "number") {
      left = overrides.left;
    } else {
      left = viewportWidth - width - horizontalMargin;
    }
    let top;
    if (typeof overrides.top === "number") {
      top = overrides.top;
    } else {
      top = viewportHeight - height - verticalMargin;
    }

    left = Math.min(Math.max(left, 12), Math.max(12, viewportWidth - width - 12));
    top = Math.min(Math.max(top, 12), Math.max(12, viewportHeight - height - 12));

    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
  }

  function applyPanelSize(panel, dimensions, mode, options = {}) {
    const { reposition = true } = options;
    const { width, height } = clampSize(dimensions);
    panel.style.width = `${width}px`;
    panel.style.height = `${height}px`;
    panel.style.maxHeight = `${height}px`;
    state.panelSize = { width, height };
    if (mode === "custom") {
      state.customSize = { width, height };
      state.activeSize = "custom";
    }
    if (reposition) {
      positionPanel(panel, { width, height });
    }
    return { width, height };
  }

  function applyPresetSize(panel, presetId) {
    const preset = sizePresets.find((item) => item.id === presetId) || sizePresets[0];
    state.activeSize = preset.id;
    state.customSize = null;
    applyPanelSize(panel, preset);
  }

  function syncPanelSize(panel) {
    if (state.customSize) {
      applyPanelSize(panel, state.customSize, "custom");
      return;
    }
    applyPresetSize(panel, state.activeSize);
  }

  function updateSizeChipLabel(chip) {
    if (!chip) {
      return;
    }
    if (state.activeSize === "custom") {
      chip.textContent = "Custom";
      return;
    }
    const preset = sizePresets.find((item) => item.id === state.activeSize) || sizePresets[0];
    chip.textContent = preset.label;
  }

  function appendMessage(messagesEl, role, text) {
    const wrapper = document.createElement("div");
    wrapper.className = `chatbot-message ${role}`;
    const bubble = document.createElement("div");
    bubble.className = "chatbot-bubble";
    bubble.textContent = text;
    wrapper.appendChild(bubble);
    messagesEl.appendChild(wrapper);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return bubble;
  }

  function openPanel(panel, statusChip) {
    if (state.isOpen) {
      return;
    }
    state.isOpen = true;
    panel.classList.add("is-open");
    panel.setAttribute("aria-hidden", "false");
    markAutoOpened();
    syncPanelSize(panel);
    updateSizeChipLabel(statusChip);
    ensureSession().catch((err) => console.error("Chat widget", err));
  }

  function closePanel(panel) {
    if (!state.isOpen) {
      return;
    }
    panel.classList.remove("is-open");
    panel.setAttribute("aria-hidden", "true");
    state.isOpen = false;
  }

  function appendAutoWelcomeMessage(messagesEl) {
    if (state.autoWelcomeShown || !AUTO_WELCOME_MESSAGE || state.hasConversation) {
      return;
    }
    appendMessage(messagesEl, "assistant", AUTO_WELCOME_MESSAGE);
    state.autoWelcomeShown = true;
  }

  function runAutoWelcome(panel, messagesEl, statusChip) {
    openPanel(panel, statusChip);
    appendAutoWelcomeMessage(messagesEl);
  }

  function maybeScheduleAutoOpen(panel, messagesEl, statusChip) {
    /* Auto-open disabled: require explicit user click to launch chat */
    return;
  }

  async function closeSessionRequest(options = {}) {
    const {
      sessionId: explicitSessionId,
      useBeacon = false,
      preserveStorage = false,
      skipStateReset = false,
    } = options;
    const activeSessionId = explicitSessionId || state.sessionId;
    if (!activeSessionId) {
      return;
    }
    const payload = JSON.stringify({ bot_id: botId, session_id: activeSessionId });
    const target = `${apiBase}/api/public/close-session`;
    if (!explicitSessionId && !skipStateReset) {
      state.sessionId = null;
    }

    const finalizeStorage = () => {
      if (!preserveStorage) {
        clearPersistedSession();
      }
    };

    if (useBeacon && navigator.sendBeacon) {
      try {
        const blob = new Blob([payload], { type: "application/json" });
        navigator.sendBeacon(target, blob);
      } catch (err) {
        console.warn("Chat widget: close-session beacon failed", err);
      }
      finalizeStorage();
      return;
    }

    try {
      await fetch(target, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: payload,
        keepalive: true,
      });
    } catch (err) {
      console.warn("Chat widget: close-session failed", err);
      return;
    }
    finalizeStorage();
  }

  async function closeChat(panel, messagesEl, reason = "user_closed") {
    state.hasConversation = false;
    await closeSessionRequest();
    messagesEl.innerHTML = "";
    closePanel(panel);
    return reason;
  }

  async function ensureSession() {
    if (state.sessionId) {
      return state.sessionId;
    }
    const res = await fetch(`${apiBase}/api/public/start-session`, {
      method: "POST",
    });
    if (!res.ok) {
      throw new Error("Unable to start chat session");
    }
    const data = await res.json();
    state.sessionId = data.session_id;
    persistSessionState();
    return state.sessionId;
  }

  async function sendMessage(messagesEl, inputEl, text) {
    if (!text.trim() || state.isSending) {
      return;
    }
    state.isSending = true;
    inputEl.value = "";
    appendMessage(messagesEl, "user", text);
    const assistantBubble = appendMessage(messagesEl, "assistant", "...");
    let responseText = "";
    state.hasConversation = true;
    persistSessionState();

    try {
      const sessionId = await ensureSession();
      const res = await fetch(`${apiBase}/api/public/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          bot_id: botId,
          session_id: sessionId,
          message: text,
          page_url: window.location.href,
        }),
      });
      if (!res.ok || !res.body) {
        throw new Error("Chat request failed");
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          break;
        }
        responseText += decoder.decode(value, { stream: true });
        assistantBubble.textContent = responseText;
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }

      responseText = responseText || "(no response)";
      assistantBubble.textContent = responseText;
    } catch (err) {
      console.error("Chat widget error", err);
      responseText = "Sorry, something went wrong.";
      assistantBubble.textContent = responseText;
    } finally {
      state.hasConversation = true;
      persistSessionState();
      state.isSending = false;
    }
  }

  function registerResizer(panel, resizer, sizeChip) {
    resizer.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      state.isResizing = true;
      const startX = event.clientX;
      const startY = event.clientY;
      const startWidth = panel.offsetWidth;
      const startHeight = panel.offsetHeight;
      const startLeft = parseFloat(panel.style.left) || 0;
      const startTop = parseFloat(panel.style.top) || 0;

      function handlePointerMove(moveEvent) {
        if (!state.isResizing) {
          return;
        }
        const deltaX = moveEvent.clientX - startX;
        const deltaY = moveEvent.clientY - startY;
        const targetWidth = startWidth - deltaX;
        const targetHeight = startHeight - deltaY;
        const { width, height } = applyPanelSize(
          panel,
          { width: targetWidth, height: targetHeight },
          "custom",
          { reposition: false }
        );
        const newLeft = startLeft + (startWidth - width);
        const newTop = startTop + (startHeight - height);
        positionPanel(panel, { left: newLeft, top: newTop, width, height });
        updateSizeChipLabel(sizeChip);
      }

      function handlePointerUp() {
        state.isResizing = false;
        window.removeEventListener("pointermove", handlePointerMove);
        window.removeEventListener("pointerup", handlePointerUp);
      }

      window.addEventListener("pointermove", handlePointerMove);
      window.addEventListener("pointerup", handlePointerUp);
    });
  }

  function createWidget() {
    applyAutoContrastTheme();
    injectStyles();

    const launcher = document.createElement("button");
    launcher.className = "chatbot-launcher";
    launcher.type = "button";
    launcher.setAttribute("aria-label", "Open chat");
    launcher.textContent = "🤖";

    const panel = document.createElement("div");
    panel.className = "chatbot-panel";
    panel.setAttribute("aria-hidden", "true");
    applyPresetSize(panel, state.activeSize);
    state.panelEl = panel;

    const header = document.createElement("div");
    header.className = "chatbot-header";

    const heading = document.createElement("p");
    heading.className = "chatbot-heading";
    heading.textContent = "Smartbot AI Assistant";

    const headerRight = document.createElement("div");
    headerRight.className = "chatbot-header-right";

    const statusChip = document.createElement("button");
    statusChip.type = "button";
    statusChip.className = "chatbot-chip";
    statusChip.setAttribute("aria-label", "Cycle widget size");
    updateSizeChipLabel(statusChip);

    const closeBtn = document.createElement("button");
    closeBtn.className = "chatbot-close";
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "Close chat");
    closeBtn.textContent = "×";

    const statusPill = document.createElement("span");
    statusPill.className = "chatbot-status-pill";
    statusPill.textContent = "Online";

    headerRight.appendChild(statusChip);
    headerRight.appendChild(statusPill);
    headerRight.appendChild(closeBtn);

    header.appendChild(heading);
    header.appendChild(headerRight);

    const messagesEl = document.createElement("div");
    messagesEl.className = "chatbot-messages";
    state.messagesEl = messagesEl;

    const inputWrap = document.createElement("form");
    inputWrap.className = "chatbot-input";
    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = "Ask us anything…";
    const sendBtn = document.createElement("button");
    sendBtn.type = "submit";
    sendBtn.textContent = "Send";
    inputWrap.appendChild(input);
    inputWrap.appendChild(sendBtn);

    inputWrap.addEventListener("submit", function (event) {
      event.preventDefault();
      sendMessage(messagesEl, input, input.value);
    });

    const footerNote = document.createElement("p");
    footerNote.className = "chatbot-footer";
    footerNote.textContent = "Powered by LeadGenSource.com";

    const resizer = document.createElement("div");
    resizer.className = "chatbot-resizer";
    resizer.setAttribute("aria-hidden", "true");
    resizer.setAttribute("title", "Drag to resize");
    registerResizer(panel, resizer, statusChip);

    launcher.addEventListener("click", function () {
      if (state.isOpen) {
        closePanel(panel);
        return;
      }
      openPanel(panel, statusChip);
    });

    closeBtn.addEventListener("click", function () {
      closeChat(panel, messagesEl).catch((err) => console.error("Chat widget", err));
    });

    statusChip.addEventListener("click", function () {
      const currentIndex = sizePresets.findIndex((preset) => preset.id === state.activeSize);
      const nextPreset = sizePresets[(currentIndex + 1) % sizePresets.length];
      applyPresetSize(panel, nextPreset.id);
      updateSizeChipLabel(statusChip);
    });
    panel.appendChild(header);
    panel.appendChild(messagesEl);
    panel.appendChild(inputWrap);
    panel.appendChild(footerNote);
    panel.appendChild(resizer);

    document.body.appendChild(launcher);
    document.body.appendChild(panel);

    maybeScheduleAutoOpen(panel, messagesEl, statusChip);

    window.addEventListener("resize", function () {
      if (!document.body.contains(panel)) {
        return;
      }
      syncPanelSize(panel);
      updateSizeChipLabel(statusChip);
      scheduleAutoContrastUpdate();
    });

    if (!autoContrastListenersBound) {
      autoContrastListenersBound = true;
      window.addEventListener("scroll", scheduleAutoContrastUpdate, { passive: true });
    }

    window.addEventListener("beforeunload", function () {
      if (!state.sessionId || !state.hasConversation) {
        return;
      }
      persistSessionState();
      closeSessionRequest({ useBeacon: true, preserveStorage: true }).catch(() => {});
    });
  }

  closePersistedSessionIfNeeded().catch(() => {});

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", createWidget);
  } else {
    createWidget();
  }
})();
