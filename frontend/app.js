(() => {
  const cfg = window.HINAFT_CONFIG;

  const bubbleEl = document.getElementById("bubble-text");
  const playerForm = document.getElementById("player-form");
  const playerInput = document.getElementById("player-input");
  const micBtn = document.getElementById("mic-btn");
  const autoBtn = document.getElementById("auto-btn");
  const modelBtn = document.getElementById("model-btn");
  const statusEl = document.getElementById("status-text");
  const lastPlayerEl = document.getElementById("last-player");
  const characterImg = document.getElementById("character-img");

  function setCharacterState(state) {
    const src = state === "talk" ? "images/talk.png" : "images/stand.png";
    if (!characterImg.src.endsWith(src)) {
      characterImg.src = src;
    }
  }

  function setLastPlayer(text) {
    lastPlayerEl.textContent = text ? `あなた: ${text}` : "";
  }

  let autoMicEnabled = false;
  let micActive = false;
  let recognition = null;

  function setStatus(text) {
    statusEl.textContent = text || "";
  }

  function buildSpeechRecognition() {
    const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Ctor) return null;
    const r = new Ctor();
    r.lang = "ja-JP";
    r.interimResults = true;
    r.maxAlternatives = 1;
    r.continuous = false;
    return r;
  }

  async function postPlayer(text) {
    try {
      await fetch("/api/messages/player", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: text }),
      });
    } catch (e) {
      console.error("プレイヤー送信失敗", e);
    }
  }

  async function fetchNextMessage() {
    const res = await fetch("/api/messages/next");
    if (!res.ok) throw new Error(`status ${res.status}`);
    const data = await res.json();
    if (!data || !data.content) return null;
    return data;
  }

  async function synthesizeSpeech(text) {
    const e = cfg.elevenlabs;
    const url = `https://api.elevenlabs.io/v1/text-to-speech/${encodeURIComponent(
      e.voiceId
    )}`;
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "xi-api-key": e.apiKey,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        text,
        model_id: e.modelId,
        language_code: e.languageCode,
        output_format: e.outputFormat,
        voice_settings: e.voiceSettings,
      }),
    });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(`ElevenLabs ${res.status}: ${body.slice(0, 200)}`);
    }
    const blob = await res.blob();
    return URL.createObjectURL(blob);
  }

  function typewriter(text, charsPerSecond) {
    bubbleEl.textContent = "";
    const intervalMs = Math.max(1, Math.round(1000 / charsPerSecond));
    return new Promise((resolve) => {
      let i = 0;
      const timer = setInterval(() => {
        if (i >= text.length) {
          clearInterval(timer);
          resolve();
          return;
        }
        bubbleEl.textContent += text[i];
        i += 1;
      }, intervalMs);
    });
  }

  function playAudio(url, timeoutMs) {
    return new Promise((resolve) => {
      const audio = new Audio(url);
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        audio.onended = null;
        audio.onerror = null;
        try {
          audio.pause();
        } catch (_) {}
        URL.revokeObjectURL(url);
        resolve();
      };
      const timer = setTimeout(finish, timeoutMs);
      audio.onended = finish;
      audio.onerror = (e) => {
        console.warn("audio error", e);
        finish();
      };
      audio.play().catch((e) => {
        console.warn("audio.play() 失敗", e);
        finish();
      });
    });
  }

  async function speakAndShow(text) {
    setCharacterState("talk");
    try {
      let audioUrl = null;
      try {
        audioUrl = await synthesizeSpeech(text);
      } catch (e) {
        console.error(e);
        setStatus("TTS失敗、テキストのみ表示");
        await typewriter(text, cfg.typewriterCharsPerSecond);
        setStatus("");
        return;
      }

      await Promise.all([
        typewriter(text, cfg.typewriterCharsPerSecond),
        playAudio(audioUrl, cfg.elevenlabs.audioMaxMs),
      ]);
    } finally {
      setCharacterState("stand");
    }
  }

  function startMic() {
    if (micActive) return;
    if (!recognition) {
      recognition = buildSpeechRecognition();
      if (!recognition) {
        setStatus("このブラウザは音声認識に未対応");
        return;
      }
    }
    micActive = true;
    setStatus("🎤 録音中...");
    let resultText = "";
    recognition.onresult = (event) => {
      let combined = "";
      for (let i = 0; i < event.results.length; i += 1) {
        const r = event.results[i];
        if (r && r[0]) combined += r[0].transcript || "";
      }
      resultText = combined;
      console.log("speech onresult", { combined, isFinal: event.results[event.results.length - 1]?.isFinal });
      if (combined) setLastPlayer(combined);
    };
    recognition.onerror = (event) => {
      console.warn("speech error", event.error);
      setStatus(`音声認識エラー: ${event.error}`);
    };
    recognition.onend = async () => {
      micActive = false;
      setStatus("");
      const t = resultText.trim();
      console.log("speech onend", { resultText: t });
      if (t) {
        playerInput.value = "";
        setLastPlayer(t);
        await postPlayer(t);
      }
    };
    try {
      recognition.start();
    } catch (e) {
      micActive = false;
      setStatus("");
      console.warn("recognition.start() 失敗", e);
    }
  }

  micBtn.addEventListener("click", () => {
    startMic();
  });

  autoBtn.addEventListener("click", () => {
    autoMicEnabled = !autoMicEnabled;
    autoBtn.setAttribute("aria-pressed", String(autoMicEnabled));
    autoBtn.textContent = `AUTO: ${autoMicEnabled ? "ON" : "OFF"}`;
  });

  function applyModelTier(tier) {
    modelBtn.dataset.tier = tier;
    modelBtn.textContent = `モデル: ${tier.toUpperCase()}`;
    modelBtn.setAttribute("aria-pressed", String(tier === "pro"));
  }

  async function fetchModelTier() {
    try {
      const res = await fetch("/api/model");
      if (!res.ok) return;
      const data = await res.json();
      if (data && data.tier) applyModelTier(data.tier);
    } catch (e) {
      console.warn("モデル状態取得失敗", e);
    }
  }

  async function setModelTier(tier) {
    const prev = modelBtn.dataset.tier;
    applyModelTier(tier);
    try {
      const res = await fetch("/api/model", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tier }),
      });
      if (!res.ok) throw new Error(`status ${res.status}`);
    } catch (e) {
      console.error("モデル切替失敗", e);
      setStatus("モデル切替失敗");
      applyModelTier(prev);
    }
  }

  modelBtn.addEventListener("click", () => {
    const next = modelBtn.dataset.tier === "flash" ? "pro" : "flash";
    setModelTier(next);
  });

  fetchModelTier();

  playerForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = playerInput.value.trim();
    if (!text) return;
    playerInput.value = "";
    setLastPlayer(text);
    await postPlayer(text);
  });

  async function messageLoop() {
    while (true) {
      try {
        const data = await fetchNextMessage();
        if (!data) {
          await sleep(cfg.pollIntervalMs);
          continue;
        }
        await speakAndShow(data.content);
        if (autoMicEnabled) {
          await sleep(cfg.autoMicGuardMs);
          startMic();
        }
      } catch (e) {
        console.error("messageLoop error", e);
        await sleep(cfg.pollIntervalMs);
      }
    }
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  const overlay = document.getElementById("start-overlay");
  const startBtn = document.getElementById("start-btn");
  startBtn.addEventListener("click", async () => {
    try {
      const a = new Audio(
        "data:audio/mp3;base64,SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4Ljc2LjEwMAAAAAAAAAAAAAAA//tQAAAAAAAAAAAAAAAAAAAAAAAA"
      );
      a.volume = 0;
      await a.play().catch(() => {});
    } catch (_) {}
    overlay.classList.add("hidden");
    messageLoop();
  });
})();
