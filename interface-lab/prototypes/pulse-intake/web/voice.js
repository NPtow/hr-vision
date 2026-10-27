"use strict";
// Audio stays in memory. Permission is requested only by start(), after a click.
class VoiceInput {
  constructor({ upload, onState, onResult, onError, mediaDevices = navigator.mediaDevices, Recorder = window.MediaRecorder }) {
    Object.assign(this, { upload, onState, onResult, onError, mediaDevices, Recorder });
    this.state = "idle";
    this.generation = 0;
    this.stream = null;
    this.recorder = null;
    this.timer = null;
  }
  setState(value, seconds = 0) { this.state = value; this.onState(value, seconds); }
  release() {
    clearInterval(this.timer);
    this.timer = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
  }
  async start() {
    if (this.state !== "idle") return;
    if (!this.mediaDevices?.getUserMedia || !this.Recorder) {
      this.onError("Этот браузер не поддерживает запись. Откройте анкету в Safari или Chrome.");
      return;
    }
    const generation = ++this.generation;
    this.setState("requesting");
    try {
      const stream = await this.mediaDevices.getUserMedia({ audio: true });
      if (generation !== this.generation) { stream.getTracks().forEach((track) => track.stop()); return; }
      this.stream = stream;
      const mime = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus"].find((type) => this.Recorder.isTypeSupported(type));
      const recorder = new this.Recorder(stream, { ...(mime ? { mimeType: mime } : {}), audioBitsPerSecond: 32000 });
      this.recorder = recorder;
      const chunks = [];
      let bytes = 0;
      recorder.ondataavailable = (event) => {
        if (event.data.size && generation === this.generation) {
          chunks.push(event.data);
          bytes += event.data.size;
          if (bytes > 5_000_000) { this.cancel(); this.onError("Запись слишком большая. Попробуйте ответить короче."); }
        }
      };
      recorder.onerror = () => {
        if (generation !== this.generation) return;
        this.cancel();
        this.onError("Запись прервалась. Попробуйте включить микрофон ещё раз.");
      };
      recorder.onstop = async () => {
        if (generation !== this.generation) return;
        this.release();
        const blob = new Blob(chunks, { type: recorder.mimeType || mime || "audio/webm" });
        if (blob.size < 100) { this.setState("idle"); this.onError("Запись получилась пустой. Попробуйте ещё раз."); return; }
        this.setState("transcribing");
        this.abort = new AbortController();
        const timeout = setTimeout(() => this.abort?.abort(), 90000);
        try {
          const text = await this.upload(blob, this.abort.signal);
          if (generation === this.generation) this.onResult(text);
        } catch (error) {
          if (generation === this.generation) this.onError(error.name === "AbortError" ? "Распознавание заняло слишком много времени. Попробуйте ещё раз." : error.message);
        } finally {
          clearTimeout(timeout);
          if (generation === this.generation) { this.abort = null; this.setState("idle"); }
        }
      };
      recorder.start(500);
      this.started = Date.now();
      this.setState("recording");
      this.timer = setInterval(() => {
        const seconds = Math.floor((Date.now() - this.started) / 1000);
        this.onState("recording", seconds);
        if (seconds >= 120) this.stop();
      }, 250);
    } catch (error) {
      this.release();
      if (generation !== this.generation) return;
      this.setState("idle");
      this.onError(error.name === "NotAllowedError" ? "Разрешите доступ к микрофону в настройках браузера и попробуйте снова." : "Не удалось включить микрофон. Проверьте, подключён ли он.");
    }
  }
  stop() {
    if (this.state !== "recording") return;
    this.setState("transcribing");
    if (this.recorder?.state !== "inactive") this.recorder.stop();
    this.release();
  }
  cancel() {
    ++this.generation;
    this.abort?.abort();
    this.abort = null;
    if (this.recorder && this.recorder.state !== "inactive") this.recorder.stop();
    this.release();
    this.setState("idle");
  }
}
if (typeof window !== "undefined") window.VoiceInput = VoiceInput;
if (typeof module !== "undefined") module.exports = VoiceInput;
