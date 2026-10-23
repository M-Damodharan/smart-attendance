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
          setStep('st-faceid', 'ok', `Biometrics bound to ${data.device_name || 'your phone'}`, 'faceid-text');
        } else {
          setStep('st-faceid', 'warn', 'Phone Biometrics not registered yet', 'faceid-text');
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

  // Face Quality & Clarity Inspection (Darkness, Blur, Blank Screen)
  function analyzeCanvasClarity(canvas) {
    try {
      const ctx = canvas.getContext('2d');
      const w = canvas.width, h = canvas.height;
      const imgData = ctx.getImageData(0, 0, w, h);
      const d = imgData.data;

      let sum = 0, sumSq = 0, count = 0;
      for (let i = 0; i < d.length; i += 16) {
        const lum = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
        sum += lum;
        sumSq += lum * lum;
        count++;
      }
      const mean = count > 0 ? (sum / count) : 0;
      const variance = count > 0 ? (sumSq / count - mean * mean) : 0;
      const stdDev = Math.sqrt(Math.max(0, variance));

      // Calculate edge gradient sharpness
      let edgeSum = 0, edgeCount = 0;
      const stride = w * 4;
      for (let y = 1; y < h - 1; y += 4) {
        for (let x = 1; x < w - 1; x += 4) {
          const idx = (y * w + x) * 4;
          const center = 0.299 * d[idx] + 0.587 * d[idx + 1] + 0.114 * d[idx + 2];
          const right = 0.299 * d[idx + 4] + 0.587 * d[idx + 5] + 0.114 * d[idx + 6];
          const down = 0.299 * d[idx + stride] + 0.587 * d[idx + stride + 1] + 0.114 * d[idx + stride + 2];
          edgeSum += Math.abs(center - right) + Math.abs(center - down);
          edgeCount++;
        }
      }
      const sharpness = edgeCount > 0 ? (edgeSum / edgeCount) : 0;

      // Center face region vs background flanks (Backlight & Shadow Detection)
      let faceSum = 0, faceCount = 0;
      let bgSum = 0, bgCount = 0;
      const xFaceMin = Math.floor(w * 0.38), xFaceMax = Math.floor(w * 0.62);
      const yFaceMin = Math.floor(h * 0.25), yFaceMax = Math.floor(h * 0.65);
      const xBgLeftMax = Math.floor(w * 0.30);
      const xBgRightMin = Math.floor(w * 0.70);

      for (let y = yFaceMin; y < yFaceMax; y += 4) {
        for (let x = xFaceMin; x < xFaceMax; x += 4) {
          const idx = (y * w + x) * 4;
          faceSum += 0.299 * d[idx] + 0.587 * d[idx + 1] + 0.114 * d[idx + 2];
          faceCount++;
        }
      }
      for (let y = yFaceMin; y < yFaceMax; y += 4) {
        for (let x = 0; x < xBgLeftMax; x += 4) {
          const idx = (y * w + x) * 4;
          bgSum += 0.299 * d[idx] + 0.587 * d[idx + 1] + 0.114 * d[idx + 2];
          bgCount++;
        }
        for (let x = xBgRightMin; x < w; x += 4) {
          const idx = (y * w + x) * 4;
          bgSum += 0.299 * d[idx] + 0.587 * d[idx + 1] + 0.114 * d[idx + 2];
          bgCount++;
        }
      }
      const faceMean = faceCount > 0 ? (faceSum / faceCount) : mean;
      const bgMean = bgCount > 0 ? (bgSum / bgCount) : mean;

      const isShadowed = faceMean < 22.0;
      const isBacklit = (faceMean < 36.0 && bgMean > 65.0 && (faceMean / (bgMean + 0.001) < 0.40));
      const isTooDark = mean < 16.0 || isShadowed || isBacklit;
      const isBlank = stdDev < 6.0;
      const isBlurry = sharpness < 2.0;
      const isClear = !isTooDark && !isBlank && !isBlurry;

      let reason = 'OK';
      if (isBacklit) {
        reason = 'Face not clear: Face is in deep shadow or backlit. Facial features are not visible. Please face the light and try again.';
      } else if (isShadowed || isTooDark) {
        reason = 'Face not clear: Face is too dark or in shadow (brightness: ' + Math.round(faceMean) + '/255). Please ensure good lighting and try again.';
      } else if (isBlank) {
        reason = 'Face not clear: Camera screen is blank. No facial features visible.';
      } else if (isBlurry) {
        reason = 'Face not clear: Photo is blurry or out of focus. Please hold phone steady.';
      }

      return { isClear, isTooDark, isBlank, isBlurry, mean, faceMean, bgMean, stdDev, sharpness, reason };
    } catch (e) {
      return { isClear: true, reason: 'OK' };
    }
  }

  function showFaceNotClearModal(msg) {
    const modal = $('face-not-clear-modal');
    const msgEl = $('face-not-clear-msg');
    if (modal) {
      if (msgEl && msg) {
        msgEl.innerHTML = `<b>${msg}</b><br><span style="font-size:0.84rem;color:var(--text-muted);display:block;margin-top:6px">Attendance punch was NOT recorded. Please position your face clearly in bright light and try again.</span>`;
      }
      modal.style.display = 'flex';
    }
  }

  function hideFaceNotClearModal() {
    const modal = $('face-not-clear-modal');
    if (modal) modal.style.display = 'none';
    if (video && video.paused) {
      try { video.play(); } catch(e){}
    }
    busy = false;
    refresh();
  }

  const btnTryAgain = $('btn-face-try-again');
  if (btnTryAgain) {
    btnTryAgain.addEventListener('click', hideFaceNotClearModal);
  }

  function grab(w, h, q) {
    const c = document.createElement('canvas'); c.width = w; c.height = h;
    const ctx = c.getContext('2d');
    if (video && video.videoWidth > 0 && video.videoHeight > 0) {
      ctx.drawImage(video, 0, 0, w, h);
    }
    const clarity = analyzeCanvasClarity(c);
    return { dataUrl: c.toDataURL('image/jpeg', q), clarity };
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
      // 1. Ensure live camera video stream is active and awake
      if (video && video.paused) {
        try { await video.play(); } catch(e){}
      }

      // 2. Capture live attendance selfie snapshot with face clarity check
      promptEl.textContent = 'Checking face clarity & lighting…';
      await sleep(100);
      let selfieObj = grab(480, 360, 0.85);

      // If camera frame was settling or auto-exposing, retry once after 150ms
      if (!selfieObj.clarity.isClear) {
        if (video && video.paused) {
          try { await video.play(); } catch(e){}
        }
        await sleep(150);
        selfieObj = grab(480, 360, 0.85);
      }

      // Face Not Clear / Dark -> POP-UP modal and DO NOT TAKE PUNCH!
      if (!selfieObj.clarity.isClear) {
        showFaceNotClearModal(selfieObj.clarity.reason);
        const err = new Error(selfieObj.clarity.reason);
        err.face_not_clear = true;
        throw err;
      }
      const selfie = selfieObj.dataUrl;

      // 3. Phone Biometric Authentication (Android BiometricPrompt / Apple Face ID & Touch ID)
      let webauthnAssertion = null;
      if (!window.WebAuthnHelper || !window.WebAuthnHelper.isSupported()) {
        throw new Error('Biometric authentication required: Attendance punching requires a phone with supported fingerprint or face biometrics. Passwords, PINs, and patterns are not accepted.');
      }

      if (!isWebAuthnEnrolled) {
        promptEl.textContent = 'Setting up Phone Biometrics… Please authenticate with fingerprint or face.';
        try {
          await window.WebAuthnHelper.enrollDevice();
          isWebAuthnEnrolled = true;
          checkDeviceStatus();
          promptEl.textContent = 'Phone bound! Verifying attendance punch…';
          await sleep(400);
        } catch (enrollErr) {
          console.warn('Auto-enroll cancelled or failed:', enrollErr);
          throw new Error('Phone Biometric setup cancelled: ' + (enrollErr.message || 'Please tap to bind Biometrics'));
        }
      }

      promptEl.textContent = 'Authenticating Phone Biometrics (Fingerprint / Face ID)…';
      try {
        webauthnAssertion = await window.WebAuthnHelper.getPunchAssertion();
        promptEl.textContent = 'Biometric verified! Processing attendance…';
        // Resume video stream if iOS or Android suspended it during the system dialog
        if (video && video.paused) {
          try { await video.play(); } catch(e){}
        }
        await sleep(200);
      } catch (authErr) {
        console.warn('Biometric assertion failed or cancelled:', authErr);
        if (video && video.paused) {
          try { await video.play(); } catch(e){}
        }
        throw new Error('Biometric verification failed: ' + (authErr.message || 'Cancelled by user'));
      }

      if (!webauthnAssertion) {
        throw new Error('Biometric authentication required: Attendance punching must be verified via phone fingerprint or face unlock. Passwords, PINs, and patterns are strictly disabled.');
      }

      let payload = {
        kind,
        selfie,
        webauthn_assertion: webauthnAssertion,
        ...loc
      };

      promptEl.textContent = 'Recording attendance…';
      const res = await postJSON('/api/punch', payload);
      promptEl.textContent = '';

      if (!res.ok) {
        const err = new Error(res.error || 'Something went wrong.');
        err.shield_status = res.shield_status;
        err.face_not_clear = res.face_not_clear;
        if (res.face_not_clear) {
          showFaceNotClearModal(res.error);
        }
        throw err;
      }

      result.className = 'result ok';
      result.hidden = false;
      const durStr = res.duration || res.duration_text || (res.hours !== undefined ? res.hours + ' h' : '');
      const otStr = res.overtime_text || (res.ot_hours ? res.ot_hours + ' h' : '');
      let details = '';

      if (res.auth_method === 'webauthn_face_id' || res.auth_method === 'android_biometric' || res.face_verified) {
        details += `<br><span style="color:#10b981">🛡️ <b>Phone Biometrics Verified</b> (Hardware Cryptographic Signature Valid)</span>`;
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
      if (e.face_not_clear || (e.message && (e.message.toLowerCase().includes('face not clear') || e.message.toLowerCase().includes('black or too dark') || e.message.toLowerCase().includes('blurry')))) {
        showFaceNotClearModal(e.message);
      }
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

  // Quick Bind Phone Biometrics Button
  const quickBindBtn = $('btn-quick-bind-faceid');
  const quickBindStatus = $('quick-bind-status');
  if (quickBindBtn) {
    quickBindBtn.addEventListener('click', async () => {
      quickBindBtn.disabled = true;
      quickBindBtn.textContent = 'Authenticating Phone Biometrics…';
      if (quickBindStatus) {
        quickBindStatus.style.display = 'block';
        quickBindStatus.style.color = 'var(--text-muted)';
        quickBindStatus.textContent = 'Please confirm with Fingerprint or Face ID on your phone…';
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
        quickBindBtn.textContent = '📱 Retry Binding Phone Biometrics';
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
