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

  function isCanvasBlackOrTooDark(canvas, minBrightness = 8) {
    try {
      const ctx = canvas.getContext('2d');
      const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const d = imgData.data;
      let sum = 0, samples = 0;
      for (let i = 0; i < d.length; i += 16) {
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
    if (video && video.videoWidth > 0 && video.videoHeight > 0) {
      ctx.drawImage(video, 0, 0, w, h);
    }
    return { dataUrl: c.toDataURL('image/jpeg', q), isDark: isCanvasBlackOrTooDark(c, 8) };
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

      // 2. CRITICAL FOR iOS (iPhone Safari/Chrome) & ANDROID:
      // Capture live attendance selfie snapshot FIRST before triggering native Phone Face ID.
      // On Apple iOS and Android, the OS WebKit/browser pauses or suspends video streaming
      // while system Passkey / Face ID dialogs are open on screen.
      // Capturing the selfie photo now guarantees a bright, crystal-clear face photo
      // while the user is actively looking directly at the camera oval.
      promptEl.textContent = 'Scanning face…';
      await sleep(100);
      let selfieObj = grab(480, 360, 0.85);

      // If camera frame was momentarily settling or auto-exposing, retry once after 120ms
      if (selfieObj.isDark) {
        if (video && video.paused) {
          try { await video.play(); } catch(e){}
        }
        await sleep(120);
        selfieObj = grab(480, 360, 0.85);
      }

      if (selfieObj.isDark) {
        promptEl.textContent = '⚠️ Camera screen is black or dark! No face detected.';
        throw new Error('Camera screen is black or too dark. No face detected! Please ensure camera lens is uncovered and lighting is adequate.');
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
