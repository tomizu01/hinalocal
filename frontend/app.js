(() => {
  const cfg = window.HINALIVE_CONFIG;

  const bubbleEl = document.getElementById("bubble-text");
  const playerForm = document.getElementById("player-form");
  const playerInput = document.getElementById("player-input");
  const micBtn = document.getElementById("mic-btn");
  const autoBtn = document.getElementById("auto-btn");
  const statusEl = document.getElementById("status-text");
  const lastPlayerEl = document.getElementById("last-player");
  const characterImg = document.getElementById("character-img");
  const missionTextEl = document.getElementById("mission-text");
  const missionInput = document.getElementById("mission-input");
  const missionEditBtn = document.getElementById("mission-edit-btn");
  const missionSaveBtn = document.getElementById("mission-save-btn");
  const missionCancelBtn = document.getElementById("mission-cancel-btn");
  const missionEdit = document.getElementById("mission-edit");
  const shutdownBtn = document.getElementById("shutdown-btn");

  let characterInfo = null;

  function setCharacterState(state) {
    if (!characterInfo) return;
    const file = state === "talk" ? "talk.png" : "stand.png";
    const src = `images/${characterInfo.id}/${file}`;
    if (!characterImg.src.endsWith(src)) {
      characterImg.src = src;
    }
  }

  async function fetchCharacter() {
    const res = await fetch("/api/character");
    if (!res.ok) throw new Error(`status ${res.status}`);
    return res.json();
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

  async function tryMissionCommand(text) {
    const prefix = "ミッション";
    if (!text.startsWith(prefix)) return false;
    const rest = text
      .slice(prefix.length)
      .replace(/^[\s、。：:,.]+/, "")
      .trim();
    if (!rest) return false;
    try {
      const res = await fetch("/api/mission", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: rest }),
      });
      if (!res.ok) throw new Error(`status ${res.status}`);
      const data = await res.json();
      applyMission(data);
      setStatus(`ミッション更新: ${rest.slice(0, 30)}`);
    } catch (e) {
      console.error("ミッション音声更新失敗", e);
      setStatus("ミッション更新失敗");
    }
    return true;
  }

  async function postPlayer(text) {
    if (await tryMissionCommand(text)) return;
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
    const voiceId = characterInfo && characterInfo.voice_id;
    if (!voiceId) throw new Error("voice_id 未取得");
    const url = `https://api.elevenlabs.io/v1/text-to-speech/${encodeURIComponent(
      voiceId
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

  function applyMission(data) {
    missionTextEl.textContent = data.content || "";
  }

  function openMissionEdit() {
    missionInput.value = missionTextEl.textContent || "";
    missionTextEl.classList.add("hidden");
    missionEditBtn.classList.add("hidden");
    missionEdit.classList.remove("hidden");
    missionInput.focus();
    missionInput.select();
  }

  function closeMissionEdit() {
    missionEdit.classList.add("hidden");
    missionTextEl.classList.remove("hidden");
    missionEditBtn.classList.remove("hidden");
  }

  async function fetchMission() {
    try {
      const res = await fetch("/api/mission");
      if (!res.ok) throw new Error(`status ${res.status}`);
      const data = await res.json();
      applyMission(data);
    } catch (e) {
      console.warn("ミッション取得失敗", e);
    }
  }

  async function saveMission(text) {
    try {
      const res = await fetch("/api/mission", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: text }),
      });
      if (!res.ok) throw new Error(`status ${res.status}`);
      const data = await res.json();
      applyMission(data);
      closeMissionEdit();
    } catch (e) {
      console.error("ミッション保存失敗", e);
      setStatus("ミッション保存失敗");
    }
  }

  missionEditBtn.addEventListener("click", openMissionEdit);
  missionCancelBtn.addEventListener("click", closeMissionEdit);
  missionSaveBtn.addEventListener("click", () => {
    saveMission(missionInput.value.trim());
  });
  missionInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      saveMission(missionInput.value.trim());
    } else if (e.key === "Escape") {
      e.preventDefault();
      closeMissionEdit();
    }
  });

  async function shutdown() {
    if (!window.confirm("中期記憶を保存してプログラムを終了します。よろしいですか？")) {
      return;
    }
    shutdownBtn.disabled = true;
    setStatus("終了処理中（中期記憶を保存しています）...");
    try {
      const res = await fetch("/api/shutdown", { method: "POST" });
      if (!res.ok) throw new Error(`status ${res.status}`);
      setStatus("終了しました。タブを閉じてください。");
    } catch (e) {
      console.error("終了処理失敗", e);
      setStatus("終了処理失敗");
      shutdownBtn.disabled = false;
    }
  }

  shutdownBtn.addEventListener("click", shutdown);

  fetchMission();

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

  (async () => {
    try {
      characterInfo = await fetchCharacter();
      setCharacterState("stand");
    } catch (e) {
      console.error("キャラ情報取得失敗", e);
      setStatus("キャラ情報の取得に失敗しました");
    }
  })();

  startBtn.addEventListener("click", async () => {
    if (!characterInfo) {
      try {
        characterInfo = await fetchCharacter();
        setCharacterState("stand");
      } catch (e) {
        console.error("キャラ情報取得失敗", e);
        setStatus("キャラ情報の取得に失敗しました");
        return;
      }
    }
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
