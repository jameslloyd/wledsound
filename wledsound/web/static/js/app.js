/**
 * WLEDSOUND Dashboard Client
 * Real-time 60fps Canvas Audio Visualizer and WebSockets sync.
 */

(function () {
  'use strict';

  // State
  let ws = null;
  let animationFrameId = null;
  let lastFrameTime = performance.now();
  let frameCount = 0;
  let fps = 0;

  // Audio spectrum smoothing state
  const NUM_BANDS = 16;
  const currentBands = new Array(NUM_BANDS).fill(0);
  const targetBands = new Array(NUM_BANDS).fill(0);
  const peakBands = new Array(NUM_BANDS).fill(0);
  let currentWaveform = new Array(32).fill(0);
  let isPeakActive = false;

  // DOM Elements
  const canvas = document.getElementById('visualizerCanvas');
  const ctx = canvas.getContext('2d');
  const fpsEl = document.getElementById('statFps');
  const peakFreqEl = document.getElementById('statPeakFreq');
  const rmsEl = document.getElementById('statRms');
  const beatBulb = document.getElementById('beatBulb');
  const beatPill = document.getElementById('beatPill');

  const trackTitleEl = document.getElementById('trackTitle');
  const trackArtistEl = document.getElementById('trackArtist');
  const trackAlbumEl = document.getElementById('trackAlbum');
  const albumImgEl = document.getElementById('albumImg');
  const albumBackdropEl = document.getElementById('albumBackdrop');
  const statePillEl = document.getElementById('playbackStatePill');
  const paletteSwatchesEl = document.getElementById('paletteSwatches');

  const gainSlider = document.getElementById('gainSlider');
  const gainVal = document.getElementById('gainVal');
  const smoothSlider = document.getElementById('smoothSlider');
  const smoothVal = document.getElementById('smoothVal');
  const squelchSlider = document.getElementById('squelchSlider');
  const squelchVal = document.getElementById('squelchVal');

  const snapcastBadge = document.getElementById('snapcastBadge');
  const massBadge = document.getElementById('massBadge');
  const modeBadge = document.getElementById('modeBadge');

  // Resize canvas for sharp high-DPI rendering
  function resizeCanvas() {
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);
  }
  window.addEventListener('resize', resizeCanvas);
  resizeCanvas();

  // Connect WebSocket
  function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/visualizer`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log('Visualizer WebSocket connected');
      snapcastBadge.classList.add('online');
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleVisualizerData(data);
      } catch (err) {
        console.error('Error parsing WS frame:', err);
      }
    };

    ws.onclose = () => {
      console.log('Visualizer WebSocket closed. Reconnecting in 2s...');
      snapcastBadge.classList.remove('online');
      setTimeout(connectWebSocket, 2000);
    };

    ws.onerror = (err) => {
      console.error('WebSocket error:', err);
      ws.close();
    };
  }

  // Handle incoming audio & telemetry frame
  function handleVisualizerData(data) {
    if (data.bands && data.bands.length >= NUM_BANDS) {
      for (let i = 0; i < NUM_BANDS; i++) {
        targetBands[i] = data.bands[i] / 255.0;
      }
    }

    if (data.waveform) {
      currentWaveform = data.waveform;
    }

    // Peak detection trigger
    if (data.sample_peak) {
      triggerBeatEffect();
    }

    // Telemetry updates
    if (peakFreqEl && data.major_peak !== undefined) {
      peakFreqEl.textContent = `${Math.round(data.major_peak)} Hz`;
    }
    if (rmsEl && data.rms !== undefined) {
      const db = data.rms > 0 ? (20 * Math.log10(data.rms)).toFixed(1) : '-inf';
      rmsEl.textContent = `${db} dB`;
    }

    // Connection badges
    if (massBadge && data.mass_connected !== undefined) {
      massBadge.classList.toggle('online', !!data.mass_connected);
    }
    if (snapcastBadge && data.audio_active !== undefined) {
      snapcastBadge.classList.toggle('online', !!data.audio_active);
    }

    // Metadata & palette updates
    if (data.track) {
      updateNowPlaying(data.track);
    }
  }

  // Flash beat indicator
  function triggerBeatEffect() {
    if (!beatBulb) return;
    beatBulb.classList.add('flash');
    isPeakActive = true;
    setTimeout(() => {
      beatBulb.classList.remove('flash');
      isPeakActive = false;
    }, 120);
  }

  // Update Now Playing UI & Palette
  let lastTrackHash = '';
  function updateNowPlaying(track) {
    const hash = `${track.title}-${track.artist}-${track.state}-${track.image_url}`;
    if (hash === lastTrackHash) return;
    lastTrackHash = hash;

    if (trackTitleEl) trackTitleEl.textContent = track.title || 'Unknown Title';
    if (trackArtistEl) trackArtistEl.textContent = track.artist || 'Unknown Artist';
    if (trackAlbumEl) trackAlbumEl.textContent = track.album || '';

    // Playback state
    if (statePillEl) {
      statePillEl.className = `state-pill ${track.state || 'idle'}`;
      statePillEl.textContent = (track.state || 'idle').toUpperCase();
    }

    // Artwork image
    if (track.image_url && albumImgEl) {
      albumImgEl.src = track.image_url;
      albumImgEl.style.display = 'block';
      if (albumBackdropEl) {
        albumBackdropEl.style.backgroundImage = `url('${track.image_url}')`;
      }
    }

    // Connection badge for MA
    if (massBadge) {
      if (track.state && track.state !== 'offline') {
        massBadge.classList.add('online');
      } else {
        massBadge.classList.remove('online');
      }
    }

    // Dynamic color palette swatches & CSS variables
    if (track.palette && track.palette.length > 0) {
      updatePalette(track.palette);
    }
  }

  function updatePalette(palette) {
    if (!paletteSwatchesEl) return;
    paletteSwatchesEl.innerHTML = '';

    palette.forEach((color, i) => {
      const [r, g, b] = color;
      const hex = rgbToHex(r, g, b);

      // Update root CSS variables
      document.documentElement.style.setProperty(`--palette-c${i + 1}`, hex);

      // Create swatch
      const swatch = document.createElement('div');
      swatch.className = 'palette-swatch';
      swatch.style.backgroundColor = hex;
      swatch.title = `Color ${i + 1}: ${hex}`;
      swatch.addEventListener('click', () => {
        navigator.clipboard.writeText(hex);
        swatch.style.transform = 'scale(0.9)';
        setTimeout(() => swatch.style.transform = '', 150);
      });
      paletteSwatchesEl.appendChild(swatch);
    });
  }

  function rgbToHex(r, g, b) {
    return `#${[r, g, b].map(x => x.toString(16).padStart(2, '0')).join('')}`;
  }

  // 60 FPS Render Loop
  function render(time) {
    // Measure FPS
    frameCount++;
    if (time - lastFrameTime >= 1000) {
      fps = Math.round((frameCount * 1000) / (time - lastFrameTime));
      frameCount = 0;
      lastFrameTime = time;
      if (fpsEl) fpsEl.textContent = `${fps} FPS`;
    }

    const rect = canvas.getBoundingClientRect();
    const w = rect.width;
    const h = rect.height;

    ctx.clearRect(0, 0, w, h);

    // 1. Draw subtle background oscilloscope / waveform
    ctx.beginPath();
    ctx.strokeStyle = 'rgba(0, 229, 255, 0.12)';
    ctx.lineWidth = 2;
    const waveStep = w / (currentWaveform.length - 1);
    for (let i = 0; i < currentWaveform.length; i++) {
      const x = i * waveStep;
      const y = (h * 0.5) + (currentWaveform[i] * h * 0.35);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();

    // 2. Draw 16 GEQ Spectrum Bars
    const totalBars = NUM_BANDS;
    const padding = 6;
    const barWidth = (w - (padding * (totalBars + 1))) / totalBars;

    // Fetch dynamic palette colors from CSS
    const style = getComputedStyle(document.documentElement);
    const c1 = style.getPropertyValue('--palette-c1').trim() || '#ff7800';
    const c2 = style.getPropertyValue('--palette-c2').trim() || '#ff2864';
    const c3 = style.getPropertyValue('--palette-c3').trim() || '#00e5ff';

    for (let i = 0; i < totalBars; i++) {
      // Lerp smoothing towards target
      currentBands[i] += (targetBands[i] - currentBands[i]) * 0.35;

      // Peak decay
      if (currentBands[i] > peakBands[i]) {
        peakBands[i] = currentBands[i];
      } else {
        peakBands[i] = Math.max(0, peakBands[i] - 0.015);
      }

      const barHeight = Math.max(4, currentBands[i] * (h - 30));
      const x = padding + i * (barWidth + padding);
      const y = h - barHeight;

      // Gradient fill matching album art palette
      const grad = ctx.createLinearGradient(0, y, 0, h);
      grad.addColorStop(0, c3);
      grad.addColorStop(0.5, c2);
      grad.addColorStop(1, c1);

      ctx.fillStyle = grad;

      // Draw rounded bar
      drawRoundedRect(ctx, x, y, barWidth, barHeight, 4);

      // Draw floating peak dot
      if (peakBands[i] > 0.05) {
        const peakY = h - (peakBands[i] * (h - 30));
        ctx.fillStyle = '#ffffff';
        ctx.shadowColor = c3;
        ctx.shadowBlur = 8;
        drawRoundedRect(ctx, x, peakY - 3, barWidth, 3, 1.5);
        ctx.shadowBlur = 0;
      }
    }

    // 3. Subtle glow on beat peak
    if (isPeakActive) {
      ctx.fillStyle = 'rgba(255, 40, 100, 0.08)';
      ctx.fillRect(0, 0, w, h);
    }

    animationFrameId = requestAnimationFrame(render);
  }

  function drawRoundedRect(c, x, y, width, height, radius) {
    c.beginPath();
    c.moveTo(x + radius, y);
    c.lineTo(x + width - radius, y);
    c.quadraticCurveTo(x + width, y, x + width, y + radius);
    c.lineTo(x + width, y + height);
    c.lineTo(x, y + height);
    c.lineTo(x, y + radius);
    c.quadraticCurveTo(x, y, x + radius, y);
    c.closePath();
    c.fill();
  }

  // Config updating helper
  async function updateConfig(patch) {
    try {
      const resp = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch)
      });
      if (!resp.ok) console.warn('Failed to update config:', resp.statusText);
    } catch (e) {
      console.error('Config update error:', e);
    }
  }

  // Setup Event Listeners for Controls
  function setupControls() {
    // Gain Slider
    if (gainSlider) {
      gainSlider.addEventListener('input', (e) => {
        const val = parseFloat(e.target.value);
        if (gainVal) gainVal.textContent = `${val.toFixed(1)}x`;
        updateConfig({ audio: { gain: val } });
      });
    }

    // Smoothing Slider
    if (smoothSlider) {
      smoothSlider.addEventListener('input', (e) => {
        const val = parseFloat(e.target.value);
        if (smoothVal) smoothVal.textContent = val.toFixed(2);
        updateConfig({ audio: { smoothing: val } });
      });
    }

    // Squelch Slider
    if (squelchSlider) {
      squelchSlider.addEventListener('input', (e) => {
        const val = parseFloat(e.target.value);
        if (squelchVal) squelchVal.textContent = val.toFixed(3);
        updateConfig({ audio: { squelch: val } });
      });
    }

    // Output Mode Segmented Control
    const modeBtns = document.querySelectorAll('.mode-btn');
    modeBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        modeBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const mode = btn.dataset.mode;
        if (modeBadge) modeBadge.textContent = `Mode: ${mode.toUpperCase()}`;
        updateConfig({ wled: { mode: mode } });
      });
    });

    // Visualizer Effect Buttons
    const effectBtns = document.querySelectorAll('.effect-btn');
    effectBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        effectBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        updateConfig({ wled: { ddp_effect: btn.dataset.effect } });
      });
    });

    // Audio Source Buttons
    const sourceBtns = document.querySelectorAll('.source-btn');
    sourceBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        sourceBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        updateConfig({ audio: { mode: btn.dataset.source } });
      });
    });

    // Quick Action: Test Flash
    const btnFlash = document.getElementById('btnTestFlash');
    if (btnFlash) {
      btnFlash.addEventListener('click', async () => {
        triggerBeatEffect();
        try {
          await fetch('/api/wled/test-flash', { method: 'POST' });
        } catch (e) {
          console.error(e);
        }
      });
    }

    // Quick Action: Toggle WLED Power
    const btnPower = document.getElementById('btnTogglePower');
    if (btnPower) {
      btnPower.addEventListener('click', async () => {
        try {
          await fetch('/api/wled/power', { method: 'POST' });
        } catch (e) {
          console.error(e);
        }
      });
    }

    // Quick Action: Sync Palette Now
    const btnSyncPalette = document.getElementById('btnSyncPalette');
    if (btnSyncPalette) {
      btnSyncPalette.addEventListener('click', async () => {
        try {
          await fetch('/api/wled/sync-palette', { method: 'POST' });
        } catch (e) {
          console.error(e);
        }
      });
    }
  }

  // Fetch initial system status & track on load
  async function fetchInitialStatus() {
    try {
      const resp = await fetch('/api/status');
      if (resp.ok) {
        const data = await resp.json();
        if (data.music_assistant) {
          updateNowPlaying(data.music_assistant);
          if (massBadge) {
            const isOnline = data.music_assistant.enabled && data.music_assistant.state && data.music_assistant.state !== 'offline';
            massBadge.classList.toggle('online', !!isOnline);
          }
        }
        if (data.audio) {
          if (gainSlider && data.audio.gain !== undefined) {
            gainSlider.value = data.audio.gain;
            if (gainVal) gainVal.textContent = `${data.audio.gain.toFixed(1)}x`;
          }
          if (smoothSlider && data.audio.smoothing !== undefined) {
            smoothSlider.value = data.audio.smoothing;
            if (smoothVal) smoothVal.textContent = data.audio.smoothing.toFixed(2);
          }
          if (squelchSlider && data.audio.squelch !== undefined) {
            squelchSlider.value = data.audio.squelch;
            if (squelchVal) squelchVal.textContent = data.audio.squelch.toFixed(3);
          }
        }
      }
    } catch (e) {
      console.warn('Could not fetch initial status:', e);
    }
  }

  // Initialize
  document.addEventListener('DOMContentLoaded', () => {
    setupControls();
    fetchInitialStatus();
    connectWebSocket();
    animationFrameId = requestAnimationFrame(render);
  });

})();
