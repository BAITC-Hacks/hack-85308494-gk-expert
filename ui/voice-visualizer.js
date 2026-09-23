/* Blue/violet rounded bars adapted from h/desktop_app/voice-visualizer.js.
   Rendering only: players, capture, job selection and audio routing stay in workspace.js. */
'use strict';
class VoiceVisualizer {
  constructor(canvas) {
    this.canvas = canvas;
    this.values = new Float32Array(40);
    this.reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    this.lastFrame = 0;
  }
  static readBands(state, sampleRate) {
    const {analyser, bins} = state;
    analyser.getByteFrequencyData(bins);
    const bands = new Float32Array(40);
    const step = sampleRate / analyser.fftSize;
    const upper = Math.min(7600, sampleRate / 2);
    for (let i = 0; i < bands.length; i++) {
      const low = 80 * (upper / 80) ** (i / 40);
      const high = 80 * (upper / 80) ** ((i + 1) / 40);
      const first = Math.max(1, Math.floor(low / step));
      const end = Math.min(bins.length, Math.max(first + 1, Math.ceil(high / step)));
      let peak = 0;
      for (let j = first; j < end; j++) peak = Math.max(peak, bins[j]);
      bands[i] = peak / 255;
    }
    return bands;
  }
  paint(target, now = performance.now()) {
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) { this.lastFrame = now; return; }
    const scale = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.round(rect.width * scale), height = Math.round(rect.height * scale);
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width; this.canvas.height = height;
    }
    const context = this.canvas.getContext('2d');
    context.setTransform(scale, 0, 0, scale, 0, 0);
    context.clearRect(0, 0, rect.width, rect.height);
    const dt = Math.min(80, now - (this.lastFrame || now)); this.lastFrame = now;
    const quiet = !target.some(value => value > .008);
    const spacing = Math.min(12, Math.max(5, (rect.width - 48) / 40));
    const barWidth = Math.min(5, spacing * .43);
    const left = (rect.width - spacing * 39 - barWidth) / 2;
    const maximum = Math.min(88, rect.height * .88 - 4);
    for (let i = 0; i < 40; i++) {
      const next = target[i] || 0;
      const smoothing = this.reduced.matches ? 1 : 1 - Math.exp(-dt / (next > this.values[i] ? 75 : 190));
      // Silence and pause return immediately to a fixed baseline: no idle sine animation.
      this.values[i] = quiet ? 0 : this.values[i] + (next - this.values[i]) * smoothing;
      const level = this.values[i];
      const size = 3 + level * maximum;
      const x = left + i * spacing, y = (rect.height - size) / 2;
      const hue = 218 + i / 39 * 71;
      const gradient = context.createLinearGradient(0, y + size, 0, y);
      gradient.addColorStop(0, `hsl(${hue} 42% 53%)`);
      gradient.addColorStop(1, `hsl(${hue + 8} 56% 73%)`);
      context.fillStyle = gradient;
      context.globalAlpha = .4 + level * .57;
      context.shadowColor = 'rgba(164,125,213,.2)';
      context.shadowBlur = level > .25 ? 7 : 0;
      context.beginPath();
      context.roundRect(x, y, barWidth, size, barWidth / 2);
      context.fill();
    }
    context.globalAlpha = 1; context.shadowBlur = 0;
    this.canvas.dataset.signal = quiet ? 'idle' : 'audio';
  }
}
