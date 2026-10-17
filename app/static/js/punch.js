// Punch screen: GPS + Camera Selfie + Phone Face ID (WebAuthn / Passkeys) -> /api/punch
(() => {
  const root = document.getElementById('punch');
  if (!root) return;
  const kind = root.dataset.mode; // "in" | "out"
  const $ = id => document.getElementById(id);
  const video = $('video'), go = $('go'), promptEl = $('prompt'), countEl = $('count'), result = $('result');
  let geo = null, camReady = false, busy = false, isWebAuthnEnrolled = true;

  const setStep = (id, state, text, txtId) => {
    const el = $(id);
    if (el) el.className = 'step ' + state;
    const txt = $(txtId);
    if (txt) txt.textContent = text;
  };

  const refresh = () => {
    go.disabled = busy || !(geo && geo.ok && geo.allowed && camReady);
  };
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // Check employee device enrollment status
  async function checkDeviceStatus() {
    try {
      const res = await fetch('/api/webauthn/status');
      const data = await res.json();
      if (data.ok) {
        isWebAuthnEnrolled = data.device_registered;
        const banner = $('device-unregistered-banner');
        if (banner) {
          banner.style.display = isWebAuthnEnrolled ? 'none' : 'block';
        }
        if (isWebAuthnEnrolled) {
          setStep('st-faceid', 'ok', `Face ID bound to ${data.device_name || 'your phone'}`, 'faceid-text');
        } else {
          setStep('st-faceid', 'warn', 'Phone Face ID not registered yet', 'faceid-text');
        }
      }
    } catch (e) {
      console.warn('Could not check WebAuthn device status:', e);
    }
  }

  // ---------- GPS
  function locate() {
    const retryBtn = $('gps-retry-btn');
    if (retryBtn) retryBtn.style.display = 'none';
    setStep('st-gps', '', 'Acquiring GPS location…', 'gps-text');
    if (!navigator.geolocation) {
      if (retryBtn) retryBtn.style.display = 'inline-flex';
      return setStep('st-gps', 'bad', 'GPS not supported on this device', 'gps-text');
    }
    navigator.geolocation.getCurrentPosition(async pos => {
      const { latitude: lat, longitude: lng, accuracy } = pos.coords;
      geo = await postJSON('/api/geo-check', { lat, lng, accuracy });
      geo.lat = lat; geo.lng = lng; geo.accuracy = accuracy;
      if (!geo.ok) {
        setStep('st-gps', 'bad', geo.error, 'gps-text');
        if (retryBtn) retryBtn.style.display = 'inline-flex';
      } else if (geo.inside) {
        setStep('st-gps', 'ok', `Inside ${geo.office} · ${geo.distance} m (±${Math.round(accuracy)} m)`, 'gps-text');
      } else if (geo.od) {
        setStep('st-gps', 'ok', `On-duty approved (${geo.od_location || 'field'}) · ${geo.distance} m from office`, 'gps-text');
      } else {
        setStep('st-gps', 'bad', `Outside geofence: ${geo.distance} m from ${geo.office} (allowed ${geo.radius} m). Apply for OD if you work off-site.`, 'gps-text');
        if (retryBtn) retryBtn.style.display = 'inline-flex';
      }
      refresh();
    }, err => {
      const msg = err.code === 1 ? 'Location permission denied — enable it in browser settings' : 'Could not get location. Move to an open area and retry.';
      setStep('st-gps', 'bad', msg, 'gps-text');
      if (retryBtn) retryBtn.style.display = 'inline-flex';
    }, { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 });
  }
  window.retryGPS = locate;

  // ---------- Camera
  async function startCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return setStep('st-cam', 'bad', 'Camera needs HTTPS (or localhost)', 'cam-text');
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'user', width: { ideal: 640 }, height: { ideal: 480 } },
        audio: false
      });
      video.srcObject = stream;
      await video.play();
      camReady = true;
      setStep('st-cam', 'ok', 'Front camera selfie ready', 'cam-text');
    } catch (e) {
      setStep('st-cam', 'bad', 'Camera permission denied or unavailable', 'cam-text');
    }
    refresh();
  }

  function isCanvasBlackOrTooDark(canvas, minBrightness = 20) {
    try {
      const ctx = canvas.getContext('2d');
      const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const d = imgData.data;
      let sum = 0, samples = 0;
      for (let i = 0; i < d.length; i += 32) {
        sum += (0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]);
        samples++;
      }
      const avg = samples > 0 ? (sum / samples) : 0;
      return avg < minBrightness;
    } catch (e) {
      return false;
    }
  }

  function grab(w, h, q) {
    const c = document.createElement('canvas'); c.width = w; c.height = h;
    const ctx = c.getContext('2d');
    ctx.drawImage(video, 0, 0, w, h);
    return { dataUrl: c.toDataURL('image/jpeg', q), isDark: isCanvasBlackOrTooDark(c) };
  }

  async function countdown(sec) {
    for (let i = sec; i > 0; i--) { countEl.textContent = i; await sleep(1000); }
    countEl.textContent = '';
  }

  // ---------- Verify + Punch Execution
  go.addEventListener('click', async () => {
    busy = true; refresh(); result.hidden = true;
    promptEl.textContent = '';
    const loc = geo ? { lat: geo.lat, lng: geo.lng, accuracy: geo.accuracy } : {};

    try {
      // 1. Try Phone Owner Face ID Biometric Authentication (WebAuthn / Passkeys)
      let webauthnAssertion = null;
      if (window.WebAuthnHelper && window.WebAuthnHelper.isSupported()) {
        if (!isWebAuthnEnrolled) {
          promptEl.textContent = 'Setting up Phone Face ID… Please verify with Face ID.';
          try {
            await window.WebAuthnHelper.enrollDevice();
            isWebAuthnEnrolled = true;
            checkDeviceStatus();
            promptEl.textContent = 'Phone bound! Verifying attendance punch…';
            await sleep(400);
          } catch (enrollErr) {
            console.warn('Auto-enroll cancelled or failed:', enrollErr);
            throw new Error('Phone Face ID setup cancelled: ' + (enrollErr.message || 'Please tap to bind Face ID'));
          }
        }

        promptEl.textContent = 'Authenticating Phone Face ID…';
        try {
          webauthnAssertion = await window.WebAuthnHelper.getPunchAssertion();
          promptEl.textContent = 'Face ID verified! Capturing attendance photo…';
          await sleep(300);
        } catch (authErr) {
          console.warn('WebAuthn assertion failed or cancelled:', authErr);
          throw new Error('Phone Face ID Biometric verification failed: ' + (authErr.message || 'Cancelled by user'));
        }
      }

      // 2. Capture high-quality live snapshot photo for attendance audit
      const selfieObj = grab(480, 360, 0.82);
      if (selfieObj.isDark) {
        promptEl.textContent = '⚠️ Camera screen is black or dark! No face detected.';
        throw new Error('Camera screen is black or too dark. No face detected! Please ensure camera lens is uncovered and lighting is adequate.');
      }
      const selfie = selfieObj.dataUrl;

      let payload = { kind, selfie, ...loc };

      if (webauthnAssertion) {
        // High-security WebAuthn Biometric Punch
        payload.webauthn_assertion = webauthnAssertion;
      } else {
        // Fallback Liveness Flow if device not yet registered or fallback requested
        promptEl.textContent = 'Running liveness check…';
        const ch = await postJSON('/api/challenge', {});
        if (!ch.ok) throw new Error('Could not start liveness check.');
        const frames = [grab(160, 120, 0.6).dataUrl];
        await sleep(400);
        for (const p of ch.prompts) {
          promptEl.textContent = p.text;
          await countdown(2);
          frames.push(grab(160, 120, 0.6).dataUrl);
        }
        promptEl.textContent = 'Hold still…';
        await sleep(400);
        payload.token = ch.token;
        payload.frames = frames;
      }

      promptEl.textContent = 'Recording attendance…';
      const res = await postJSON('/api/punch', payload);
      promptEl.textContent = '';

      if (!res.ok) {
        const err = new Error(res.error || 'Something went wrong.');
        err.shield_status = res.shield_status;
        throw err;
      }

      result.className = 'result ok';
      result.hidden = false;
      const durStr = res.duration || res.duration_text || (res.hours !== undefined ? res.hours + ' h' : '');
      const otStr = res.overtime_text || (res.ot_hours ? res.ot_hours + ' h' : '');
      let details = '';

      if (res.auth_method === 'webauthn_face_id' || res.face_verified) {
        details += `<br><span style="color:#10b981">🛡️ <b>Phone Face ID Verified</b> (Hardware Cryptographic Signature Valid)</span>`;
      }
      if (durStr) details += `<br>Worked: <strong>${durStr}</strong>`;
      if (otStr && otStr !== '0m' && otStr !== '0 h') details += ` · Overtime: <strong>${otStr}</strong>`;
      if (res.status) details += ` · Status: <strong>${res.status}</strong>`;

      result.innerHTML = `<b>${res.message}</b>` + details;
      go.textContent = '✓ Attendance Marked';
      go.disabled = true;
      setTimeout(() => location.href = '/dashboard', 2200);
      return;

    } catch (e) {
      promptEl.textContent = '';
      countEl.textContent = '';
      result.className = 'result bad';
      result.hidden = false;
      if (e.shield_status === 'BLOCKED' || (e.message && (e.message.includes('Blocked') || e.message.includes('locked')))) {
        result.innerHTML = `🚨 <b>AI Biometric Defense:</b> ${e.message}<br><small style="margin-top:6px;display:block;opacity:0.9">Your employee ID has been locked for security. Please contact HR Administration to review the biometric audit log and unlock your ID.</small>`;
        go.disabled = true;
        go.textContent = '🔒 Account Locked by AI';
        return;
      }
      result.textContent = e.message;
      locate(); // refresh GPS for retry
    }

    busy = false;
    refresh();
  });

  // Quick Bind Phone Face ID Button
  const quickBindBtn = $('btn-quick-bind-faceid');
  const quickBindStatus = $('quick-bind-status');
  if (quickBindBtn) {
    quickBindBtn.addEventListener('click', async () => {
      quickBindBtn.disabled = true;
      quickBindBtn.textContent = 'Authenticating Phone Face ID…';
      if (quickBindStatus) {
        quickBindStatus.style.display = 'block';
        quickBindStatus.style.color = 'var(--text-muted)';
        quickBindStatus.textContent = 'Please confirm with Face ID / Biometrics on your phone…';
      }
      try {
        const res = await window.WebAuthnHelper.enrollDevice();
        isWebAuthnEnrolled = true;
        if (quickBindStatus) {
          quickBindStatus.style.color = '#10b981';
          quickBindStatus.innerHTML = '<b>✓ ' + res.message + ' You can now tap Verify &amp; Check In below.</b>';
        }
        quickBindBtn.style.display = 'none';
        checkDeviceStatus();
      } catch (err) {
        quickBindBtn.disabled = false;
        quickBindBtn.textContent = '📱 Retry Binding Phone Face ID';
        if (quickBindStatus) {
          quickBindStatus.style.color = '#ef4444';
          quickBindStatus.textContent = 'Setup failed: ' + (err.message || 'Cancelled');
        }
      }
    });
  }

  locate();
  startCamera();
  checkDeviceStatus();
})();
