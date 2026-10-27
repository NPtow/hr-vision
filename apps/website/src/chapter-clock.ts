const clamp = (value: number) => Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;

// Scroll owns the scene until an explicit action starts playback. During playback
// the page follows this clock; delayed scroll observations must not rewind it.
export class ChapterClock {
  position = 0;
  playing = false;
  private scrolling = true;
  scroll(value: number) {
    if (this.scrolling && !this.playing) this.position = clamp(value);
    return this.position;
  }
  seek(value: number) {
    this.playing = false;
    this.scrolling = false;
    this.position = clamp(value);
    return this.position;
  }
  play() { this.scrolling = false; this.playing = this.position < 1; }
  pause() { this.playing = false; }
  takeScrollControl() { this.pause(); this.scrolling = true; }
  advance(delta: number, duration: number) {
    if (!this.playing) return this.position;
    // A background tab must not skip the explanation when it becomes visible.
    this.position = clamp(this.position + Math.min(64, Math.max(0, delta)) / Math.max(1, duration));
    if (this.position === 1) this.playing = false;
    return this.position;
  }
}
