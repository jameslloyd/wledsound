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
  const activePaletteLabelEl = document.getElementById('activePaletteLabel');
  const paletteSourceLabelEl = document.getElementById('paletteSourceLabel');
  const devicesListEl = document.getElementById('devicesList');
  const btnSyncWledSegments = document.getElementById('btnSyncWledSegments');

  const gainSlider = document.getElementById('gainSlider');
  const gainVal = document.getElementById('gainVal');
  const smoothSlider = document.getElementById('smoothSlider');
  const smoothVal = document.getElementById('smoothVal');
  const squelchSlider = document.getElementById('squelchSlider');
  const squelchVal = document.getElementById('squelchVal');

  const snapcastBadge = document.getElementById('snapcastBadge');
  const massBadge = document.getElementById('massBadge');
  const modeBadge = document.getElementById('modeBadge');
  const btnSyncToggle = document.getElementById('btnSyncToggle');
  const syncStatusText = document.getElementById('syncStatusText');
  const btnActionToggleSync = document.getElementById('btnActionToggleSync');
  const actionSyncText = document.getElementById('actionSyncText');

  // Master LED Sync state UI updater
  function updateSyncUI(enabled) {
    if (btnSyncToggle) {
      btnSyncToggle.classList.toggle('active', !!enabled);
      btnSyncToggle.classList.toggle('disabled', !enabled);
      btnSyncToggle.setAttribute('aria-checked', enabled ? 'true' : 'false');
    }
    if (syncStatusText) {
      syncStatusText.textContent = enabled ? 'ON' : 'OFF';
      syncStatusText.style.color = enabled ? 'var(--color-success)' : 'var(--color-danger)';
    }
    if (actionSyncText) {
      actionSyncText.textContent = enabled ? 'Pause Sync' : 'Resume Sync';
    }
    if (btnActionToggleSync) {
      btnActionToggleSync.classList.toggle('btn-primary', !enabled);
      btnActionToggleSync.classList.toggle('btn-outline', !!enabled);
    }
    if (modeBadge && !enabled) {
      modeBadge.textContent = 'Mode: SYNC OFF';
      modeBadge.style.borderColor = 'rgba(255, 77, 109, 0.4)';
      modeBadge.style.color = 'var(--color-danger)';
    }
  }

  // Toggle master LED sync via API
  async function toggleLedSync(explicitVal = null) {
    try {
      const payload = explicitVal !== null ? { enabled: explicitVal } : {};
      const resp = await fetch('/api/wled/sync-toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (resp.ok) {
        const data = await resp.json();
        updateSyncUI(data.sync_enabled);
      }
    } catch (e) {
      console.error('Failed to toggle LED sync:', e);
    }
  }

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

    // LED Sync Master Toggle
    if (data.sync_enabled !== undefined) {
      updateSyncUI(data.sync_enabled);
    }

    // Palette selection & active swatches
    if (data.palette_mode !== undefined) {
      updateActivePaletteUI(data.palette_mode, data.active_palette);
    }

    // WLED Devices & Segments
    if (data.devices) {
      renderDevicesList(data.devices, data.available_effects);
    }

    // Metadata & track updates
    if (data.track) {
      updateNowPlaying(data.track, data.palette_mode);
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

  // Active palette UI state
  let currentPaletteMode = 'album_art';
  function updateActivePaletteUI(paletteMode, activePalette) {
    currentPaletteMode = paletteMode;
    const paletteCards = document.querySelectorAll('.palette-card');
    paletteCards.forEach(c => {
      const isCardActive = (c.dataset.palette === paletteMode);
      c.classList.toggle('active', isCardActive);
      if (isCardActive && activePaletteLabelEl) {
        const nameEl = c.querySelector('.palette-card-name');
        activePaletteLabelEl.textContent = nameEl ? nameEl.textContent : paletteMode;
      }
    });

    if (paletteSourceLabelEl) {
      if (paletteMode === 'album_art') {
        paletteSourceLabelEl.textContent = 'Active Artwork Palette (Auto)';
      } else {
        const matched = Array.from(paletteCards).find(c => c.dataset.palette === paletteMode);
        const name = matched ? (matched.querySelector('.palette-card-name')?.textContent || paletteMode) : paletteMode;
        paletteSourceLabelEl.textContent = `Preset Palette: ${name}`;
      }
    }

    if (activePalette && activePalette.length > 0) {
      updatePalette(activePalette);
    }
  }

  // Update Now Playing UI & Palette
  let lastTrackHash = '';
  function updateNowPlaying(track, paletteMode) {
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

    // Update Album Cover preview card bar in palette selector
    const albumBar = document.querySelector('#btnPaletteAlbumArt .palette-card-bar');
    if (albumBar && track.palette && track.palette.length >= 2) {
      const stops = track.palette.map(c => rgbToHex(c[0], c[1], c[2])).join(', ');
      albumBar.style.background = `linear-gradient(90deg, ${stops})`;
    }

    // Connection badge for MA
    if (massBadge) {
      if (track.state && track.state !== 'offline') {
        massBadge.classList.add('online');
      } else {
        massBadge.classList.remove('online');
      }
    }

    // Dynamic color palette swatches & CSS variables (if on album_art mode)
    const mode = paletteMode || currentPaletteMode;
    if (mode === 'album_art' && track.palette && track.palette.length > 0) {
      updatePalette(track.palette);
    }
  }

  let lastPaletteJson = '';
  function updatePalette(palette) {
    if (!paletteSwatchesEl || !palette || palette.length === 0) return;
    const pJson = JSON.stringify(palette);
    if (pJson === lastPaletteJson) return;
    lastPaletteJson = pJson;

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
      swatch.title = `Color ${i + 1}: ${hex} (click to copy)`;
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

    // LED Sync Master Toggle Buttons
    if (btnSyncToggle) {
      btnSyncToggle.addEventListener('click', () => toggleLedSync());
    }
    if (btnActionToggleSync) {
      btnActionToggleSync.addEventListener('click', () => toggleLedSync());
    }

    // Output Mode Segmented Control
    const modeBtns = document.querySelectorAll('.mode-btn');
    modeBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        modeBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const mode = btn.dataset.mode;
        if (mode === 'off') {
          updateConfig({ wled: { mode: 'off', sync_enabled: false } });
          updateSyncUI(false);
        } else {
          updateConfig({ wled: { mode: mode, sync_enabled: true } });
          updateSyncUI(true);
          if (modeBadge) {
            modeBadge.textContent = `Mode: ${mode.toUpperCase()}`;
            modeBadge.style.borderColor = 'rgba(0, 229, 255, 0.3)';
            modeBadge.style.color = 'var(--color-primary)';
          }
        }
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

    // Palette Card Selection Buttons
    const paletteCards = document.querySelectorAll('.palette-card');
    paletteCards.forEach(card => {
      card.addEventListener('click', async () => {
        const paletteId = card.dataset.palette;
        paletteCards.forEach(c => c.classList.remove('active'));
        card.classList.add('active');
        if (activePaletteLabelEl) {
          const nameEl = card.querySelector('.palette-card-name');
          activePaletteLabelEl.textContent = nameEl ? nameEl.textContent : paletteId;
        }
        try {
          await fetch(`/api/wled/palette/${paletteId}`, { method: 'POST' });
        } catch (e) {
          console.error('Failed to set palette:', e);
        }
      });
    });

    // Sync Segments from WLED Hardware
    if (btnSyncWledSegments) {
      btnSyncWledSegments.addEventListener('click', async () => {
        btnSyncWledSegments.disabled = true;
        const origHtml = btnSyncWledSegments.innerHTML;
        btnSyncWledSegments.innerHTML = `Scanning...`;
        try {
          const resp = await fetch('/api/wled/devices/discover', { method: 'POST' });
          if (resp.ok) {
            const result = await resp.json();
            if (result.devices) {
              lastDevicesJson = '';
              renderDevicesList(result.devices);
            }
          }
        } catch (e) {
          console.error('Failed to discover WLED devices:', e);
        } finally {
          btnSyncWledSegments.disabled = false;
          btnSyncWledSegments.innerHTML = origHtml;
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

  // WLED Devices & Segments Rendering
  let lastDevicesJson = '';
  let availableEffects = [
    { id: 'album_pulse', name: 'Album Pulse' },
    { id: 'geq_spectrum', name: '16-Band GEQ' },
    { id: 'energy_wave', name: 'Energy Wave' },
    { id: 'vu_meter', name: 'VU Meter' },
    { id: 'beat_flash', name: 'Beat Drop Flash' },
    { id: 'solid', name: 'Solid Ambient' },
    { id: 'off', name: 'Off (Dark)' }
  ];

  function renderDevicesList(devices, effects) {
    if (!devicesListEl || !devices) return;
    if (effects && effects.length > 0) {
      availableEffects = effects;
    }
    const currentJson = JSON.stringify(devices);
    if (currentJson === lastDevicesJson && devicesListEl.children.length > 0) return;
    lastDevicesJson = currentJson;

    devicesListEl.innerHTML = '';

    if (devices.length === 0) {
      devicesListEl.innerHTML = '<div style="font-size: 12px; color: var(--color-text-dim); padding: 10px 0;">No devices detected yet. Click "Sync from WLED" above to discover.</div>';
      return;
    }

    devices.forEach(dev => {
      const card = document.createElement('div');
      card.className = 'device-card';
      card.dataset.ip = dev.ip;

      const header = document.createElement('div');
      header.className = 'device-header';
      header.innerHTML = `
        <div class="device-info">
          <span class="device-name">${dev.name || 'WLED Device'}</span>
          <span class="device-badge">${dev.ip}</span>
          <span class="device-badge leds">${dev.led_count} LEDs</span>
        </div>
        <div class="device-toggles">
          <label class="toggle-label" title="Enable or disable real-time DDP streaming to this device">
            <input type="checkbox" class="dev-ddp-toggle" data-ip="${dev.ip}" ${dev.ddp_enabled ? 'checked' : ''}>
            <span>DDP Stream</span>
          </label>
        </div>
      `;

      const ddpToggle = header.querySelector('.dev-ddp-toggle');
      ddpToggle.addEventListener('change', async (e) => {
        dev.ddp_enabled = e.target.checked;
        await saveDevicesConfig(devices);
      });

      card.appendChild(header);

      const segmentsContainer = document.createElement('div');
      segmentsContainer.className = 'device-segments';

      (dev.segments || []).forEach(seg => {
        const segLen = (seg.stop || dev.led_count) - (seg.start || 0);
        const row = document.createElement('div');
        row.className = 'segment-row';
        row.dataset.segId = seg.id;

        const meta = document.createElement('div');
        meta.className = 'seg-meta';
        meta.innerHTML = `
          <div class="seg-title">
            <span class="seg-num">#${seg.id}</span>
            <span class="seg-name">${seg.name || `Segment ${seg.id}`}</span>
          </div>
          <span class="seg-range">LEDs ${seg.start} – ${seg.stop} (${segLen} px)</span>
        `;
        row.appendChild(meta);

        const controls = document.createElement('div');
        controls.className = 'seg-controls';

        const selectWrap = document.createElement('div');
        selectWrap.className = 'seg-effect-select-wrapper';
        selectWrap.innerHTML = `<label class="seg-field-label">Effect:</label>`;

        const select = document.createElement('select');
        select.className = 'seg-effect-select';
        select.dataset.ip = dev.ip;
        select.dataset.segId = seg.id;

        availableEffects.forEach(eff => {
          const opt = document.createElement('option');
          opt.value = eff.id;
          opt.textContent = eff.name;
          if (seg.effect === eff.id) {
            opt.selected = true;
          }
          select.appendChild(opt);
        });

        select.addEventListener('change', async (e) => {
          const newEffect = e.target.value;
          seg.effect = newEffect;
          await updateSegmentSetting(dev.ip, seg.id, { effect: newEffect });
        });
        selectWrap.appendChild(select);
        controls.appendChild(selectWrap);

        // Palette Selector
        const paletteWrap = document.createElement('div');
        paletteWrap.className = 'seg-select-wrapper';
        paletteWrap.innerHTML = `<label class="seg-field-label">Palette:</label>`;

        const paletteSelect = document.createElement('select');
        paletteSelect.className = 'seg-select seg-palette-select';
        paletteSelect.dataset.ip = dev.ip;
        paletteSelect.dataset.segId = seg.id;

        const paletteOptions = [
          { id: '', name: '🌐 Global Palette' },
          { id: 'album_art', name: '🎨 Album Cover (Auto)' },
          { id: 'cyberpunk', name: 'Cyberpunk Neon' },
          { id: 'sunset', name: 'Sunset Fire' },
          { id: 'vaporwave', name: 'Vaporwave Retro' },
          { id: 'aurora', name: 'Aurora Borealis' },
          { id: 'magma', name: 'Molten Magma' },
          { id: 'forest', name: 'Emerald Forest' },
          { id: 'glacial', name: 'Glacial Frost' },
          { id: 'rainbow', name: 'Rainbow Prism' },
          { id: 'candle', name: 'Warm Candle' }
        ];

        paletteOptions.forEach(pOpt => {
          const opt = document.createElement('option');
          opt.value = pOpt.id;
          opt.textContent = pOpt.name;
          const currentPal = seg.palette || '';
          if (currentPal === pOpt.id || (pOpt.id === '' && (!seg.palette || seg.palette === 'inherit'))) {
            opt.selected = true;
          }
          paletteSelect.appendChild(opt);
        });

        paletteSelect.addEventListener('change', async (e) => {
          const newPal = e.target.value || null;
          seg.palette = newPal;
          await updateSegmentSetting(dev.ip, seg.id, { palette: newPal });
        });
        paletteWrap.appendChild(paletteSelect);
        controls.appendChild(paletteWrap);

        const toggles = document.createElement('div');
        toggles.className = 'seg-toggles';

        const revBtn = document.createElement('button');
        revBtn.type = 'button';
        revBtn.className = `chip-toggle ${seg.reverse ? 'active' : ''}`;
        revBtn.title = 'Reverse animation direction';
        revBtn.textContent = '⇄ Reverse';
        revBtn.addEventListener('click', async () => {
          seg.reverse = !seg.reverse;
          revBtn.classList.toggle('active', seg.reverse);
          await updateSegmentSetting(dev.ip, seg.id, { reverse: seg.reverse });
        });

        const mirrorBtn = document.createElement('button');
        mirrorBtn.type = 'button';
        mirrorBtn.className = `chip-toggle ${seg.mirror ? 'active' : ''}`;
        mirrorBtn.title = 'Mirror animation from center';
        mirrorBtn.textContent = '⇋ Mirror';
        mirrorBtn.addEventListener('click', async () => {
          seg.mirror = !seg.mirror;
          mirrorBtn.classList.toggle('active', seg.mirror);
          await updateSegmentSetting(dev.ip, seg.id, { mirror: seg.mirror });
        });

        toggles.appendChild(revBtn);
        toggles.appendChild(mirrorBtn);
        controls.appendChild(toggles);

        row.appendChild(controls);
        segmentsContainer.appendChild(row);
      });

      card.appendChild(segmentsContainer);
      devicesListEl.appendChild(card);
    });
  }

  async function updateSegmentSetting(ip, segId, patch) {
    try {
      await fetch(`/api/wled/devices/${encodeURIComponent(ip)}/segments/${segId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch)
      });
    } catch (e) {
      console.error('Failed to update segment:', e);
    }
  }

  async function saveDevicesConfig(devices) {
    try {
      await fetch('/api/wled/devices', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ devices })
      });
    } catch (e) {
      console.error('Failed to save devices:', e);
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
          if (data.audio.mode) {
            const sourceBtns = document.querySelectorAll('.source-btn');
            sourceBtns.forEach(b => b.classList.toggle('active', b.dataset.source === data.audio.mode));
          }
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
        if (data.wled) {
          if (data.wled.sync_enabled !== undefined) {
            updateSyncUI(data.wled.sync_enabled);
          }
          if (data.wled.mode) {
            const modeBtns = document.querySelectorAll('.mode-btn');
            modeBtns.forEach(b => b.classList.toggle('active', b.dataset.mode === data.wled.mode));
            if (data.wled.sync_enabled !== false && modeBadge) {
              modeBadge.textContent = `Mode: ${data.wled.mode.toUpperCase()}`;
            }
          }
          if (data.wled.effect) {
            const effectBtns = document.querySelectorAll('.effect-btn');
            effectBtns.forEach(b => b.classList.toggle('active', b.dataset.effect === data.wled.effect));
          }
          if (data.wled.palette) {
            updateActivePaletteUI(data.wled.palette);
          }
          if (data.wled.devices) {
            renderDevicesList(data.wled.devices, data.wled.available_effects);
          }
        }
      }
    } catch (e) {
      console.warn('Could not fetch initial status:', e);
    }
  }

  // Initialize
  document.addEventListener('DOMContentLoaded', () => {
    resizeCanvas();
    setupControls();
    fetchInitialStatus();
    connectWebSocket();
    animationFrameId = requestAnimationFrame(render);
  });

})();
