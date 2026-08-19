(() => {
  const cfg = window.HINALOCAL_CONFIG;

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

  function applyRoomBackground() {
    if (!characterInfo) return;
    const src = `images/${characterInfo.id}/room.png`;
    const img = new Image();
    img.onload = () => {
      document.querySelector(".app").style.backgroundImage = `url("${src}")`;
    };
    // 読み込み失敗時は CSS デフォルト (images/parts/room.png) のまま
    img.src = src;
  }

  function setLastPlayer(text) {
    lastPlayerEl.textContent = text ? `プレーヤー：${text}` : "";
  }

  let autoMicEnabled = false;
  let micActive = false;
  let sttEnabled = true;
  let sttReady = false;

  function setStatus(text) {
    statusEl.textContent = text || "";
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
    // TTS はバックエンド経由で AivisSpeech Engine（別PC可）に中継される。
    // スタイルIDはバックエンド側でキャラ設定から解決する。
    const res = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(`TTS ${res.status}: ${body.slice(0, 200)}`);
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
        playAudio(audioUrl, cfg.tts.audioMaxMs),
      ]);
    } finally {
      setCharacterState("stand");
    }
  }

  // ---- 音声入力（ローカルSTT）--------------------------------------------
  // Web Speech API は音声を外部サーバ（Google）へ送るためオフラインでは使えない。
  // 代わりに MediaRecorder で録音し、バックエンドの /api/stt へ送って
  // このマシンの GPU 上の faster-whisper で文字起こしする。
  // 録音の切り上げ（発話の終わり検出）はブラウザ側の簡易VAD（音量ベース）で行う。
  //
  // VAD は **AudioWorklet（音声スレッド）** で動かす。setInterval で音量を見る作りだと、
  // ゲームを前面にしてブラウザがバックグラウンドになった瞬間 Chrome のタイマー間引き
  // （1秒に1回）が効いて、無音検出が数十倍遅くなり録音が終わらなくなる。
  // 音声スレッドは間引かれず、経過時間もサンプル数から正確に出せる。

  // AudioWorklet に読み込ませる VAD 本体（Blob URL 経由で addModule する）。
  const VAD_WORKLET_SRC = `
class HinalocalVad extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = options.processorOptions;
    const toSamples = (ms) => (ms / 1000) * sampleRate;
    this.speechStartTimeout = toSamples(o.speechStartTimeoutMs);
    this.silenceEnd = toSamples(o.silenceEndMs);
    this.maxRecord = toSamples(o.maxRecordMs);
    this.minSpeech = toSamples(o.minSpeechMs);
    this.multiplier = o.vadNoiseMultiplier;
    this.minRms = o.vadMinRms;
    this.total = 0;
    this.speech = 0;
    this.silence = 0;
    this.speaking = false;
    this.floor = null;
    this.done = false;
  }

  process(inputs) {
    if (this.done) return false;
    const ch = inputs[0] && inputs[0][0];
    const n = ch ? ch.length : 128;
    let rms = 0;
    if (ch) {
      let sum = 0;
      for (let i = 0; i < ch.length; i += 1) sum += ch[i] * ch[i];
      rms = Math.sqrt(sum / ch.length);
    }
    if (this.floor === null) this.floor = rms;
    const threshold = Math.max(this.floor * this.multiplier, this.minRms);
    this.total += n;

    if (rms > threshold) {
      this.speaking = true;
      this.speech += n;
      this.silence = 0;
    } else {
      // 喋っていない間だけ暗騒音の推定値をゆっくり更新する（時定数 約0.5秒）
      this.floor = this.floor * 0.994 + rms * 0.006;
      if (this.speaking) this.silence += n;
    }

    let reason = null;
    if (!this.speaking && this.total >= this.speechStartTimeout) reason = "timeout";
    else if (this.speaking && this.silence >= this.silenceEnd) reason = "silence";
    else if (this.total >= this.maxRecord) reason = "max";
    if (reason) {
      this.done = true;
      this.port.postMessage({
        reason,
        accepted: this.speaking && this.speech >= this.minSpeech,
        speechMs: (this.speech / sampleRate) * 1000,
      });
      return false;
    }
    return true;
  }
}
registerProcessor("hinalocal-vad", HinalocalVad);
`;

  let mediaStream = null;
  let audioCtx = null;
  let micSource = null;
  let silentGain = null;
  let vadModuleReady = false;
  let activeRecording = null;

  function pickMimeType() {
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
    ];
    for (const t of candidates) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported(t)) return t;
    }
    return "";
  }

  // マイクは一度掴んだら開いたままにする（毎回開き直すと録音開始が遅れるため）。
  async function ensureMicStream() {
    if (mediaStream && mediaStream.active) {
      if (audioCtx && audioCtx.state === "suspended") await audioCtx.resume();
      return mediaStream;
    }
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") await audioCtx.resume();
    micSource = audioCtx.createMediaStreamSource(mediaStream);
    // VAD ノードの出力先。音量0で destination に繋いでおくことで、
    // グラフが確実に処理され続ける（どこにも繋がないと処理されない場合がある）。
    silentGain = audioCtx.createGain();
    silentGain.gain.value = 0;
    silentGain.connect(audioCtx.destination);

    if (!vadModuleReady) {
      const url = URL.createObjectURL(
        new Blob([VAD_WORKLET_SRC], { type: "application/javascript" })
      );
      try {
        await audioCtx.audioWorklet.addModule(url);
        vadModuleReady = true;
      } finally {
        URL.revokeObjectURL(url);
      }
    }
    return mediaStream;
  }

  /**
   * 1発話ぶんを録音して Blob を返す。発話が無かった場合は null。
   * 発話の開始・終了判定は AudioWorklet 側（VAD_WORKLET_SRC）が行い、
   * 判定結果が1回だけ postMessage で返ってくる。
   * しきい値は暗騒音（無音時の音量）に追従させ、環境ごとのマイク感度差を吸収する。
   */
  function recordUtterance(stream) {
    const s = cfg.stt;
    const mimeType = pickMimeType();
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks = [];
    recorder.ondataavailable = (e) => {
      if (e.data && e.data.size) chunks.push(e.data);
    };

    const vad = new AudioWorkletNode(audioCtx, "hinalocal-vad", {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      processorOptions: {
        speechStartTimeoutMs: s.speechStartTimeoutMs,
        silenceEndMs: s.silenceEndMs,
        maxRecordMs: s.maxRecordMs,
        minSpeechMs: s.minSpeechMs,
        vadNoiseMultiplier: s.vadNoiseMultiplier,
        vadMinRms: s.vadMinRms,
      },
    });
    micSource.connect(vad);
    vad.connect(silentGain);

    return new Promise((resolve) => {
      let settled = false;

      const finish = (accepted) => {
        if (settled) return;
        settled = true;
        activeRecording = null;
        clearTimeout(guard);
        try {
          micSource.disconnect(vad);
          vad.disconnect();
        } catch (e) {
          console.warn("VADノードの切断に失敗", e);
        }
        recorder.onstop = () => {
          resolve(
            accepted
              ? new Blob(chunks, { type: recorder.mimeType || mimeType || "audio/webm" })
              : null
          );
        };
        try {
          recorder.stop();
        } catch (e) {
          console.warn("recorder.stop() 失敗", e);
          resolve(null);
        }
      };

      vad.port.onmessage = (e) => {
        const d = e.data || {};
        console.log("VAD判定", d);
        finish(Boolean(d.accepted));
      };

      // AudioWorklet からの通知が来なかったときの保険（タイマーは間引かれ得るので余裕を持つ）
      const guard = setTimeout(() => {
        console.warn("VADからの通知が無かったため録音を打ち切ります");
        finish(true);
      }, s.maxRecordMs + 10000);

      // マイクボタンの押し直しで即座に締めるためのハンドル
      activeRecording = { stop: () => finish(true) };
      recorder.start(200);
    });
  }

  async function transcribe(blob) {
    const res = await fetch("/api/stt", {
      method: "POST",
      headers: { "Content-Type": blob.type || "application/octet-stream" },
      body: blob,
    });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(`STT ${res.status}: ${body.slice(0, 200)}`);
    }
    const data = await res.json();
    return (data.text || "").trim();
  }

  async function startMic() {
    if (micActive) return;
    if (!sttEnabled) {
      setStatus("音声入力は無効です");
      return;
    }
    micActive = true;
    try {
      let stream;
      try {
        stream = await ensureMicStream();
      } catch (e) {
        console.error("マイクの取得に失敗", e);
        setStatus("マイクを使用できません");
        return;
      }

      setStatus("🎤 録音中...");
      const blob = await recordUtterance(stream);
      if (!blob) {
        setStatus("");
        return;
      }

      setStatus("認識中...");
      let text = "";
      try {
        text = await transcribe(blob);
      } catch (e) {
        console.error("音声認識に失敗", e);
        setStatus("音声認識に失敗しました");
        return;
      }
      setStatus("");
      if (!text) return;
      playerInput.value = "";
      setLastPlayer(text);
      await postPlayer(text);
    } finally {
      micActive = false;
    }
  }

  // モデルのロードが終わるまでマイクを押せないようにする（ロード中は数秒〜十数秒）
  async function pollSttStatus() {
    while (true) {
      try {
        const res = await fetch("/api/stt/status");
        if (res.ok) {
          const data = await res.json();
          sttEnabled = Boolean(data.enabled);
          sttReady = Boolean(data.enabled && data.ready);
          if (!sttEnabled) {
            micBtn.disabled = true;
            autoBtn.disabled = true;
            micBtn.textContent = "🎤 音声入力（無効）";
            return;
          }
          micBtn.disabled = !sttReady;
          autoBtn.disabled = !sttReady;
          micBtn.textContent = sttReady ? "🎤 音声入力" : "🎤 準備中...";
          if (sttReady) return;
        }
      } catch (e) {
        console.warn("STT状態の取得に失敗", e);
      }
      await sleep(2000);
    }
  }

  micBtn.addEventListener("click", () => {
    if (micActive) {
      // 録音中にもう一度押したら、無音を待たずにその場で確定する
      if (activeRecording) activeRecording.stop();
      return;
    }
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
  // STT モデルのロード完了までマイク系ボタンを無効にしておく
  micBtn.disabled = true;
  autoBtn.disabled = true;
  micBtn.textContent = "🎤 準備中...";
  pollSttStatus();

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
        if (autoMicEnabled && sttReady) {
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
      applyRoomBackground();
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
        applyRoomBackground();
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
