// Punch screen: GPS + camera + liveness challenge -> /api/punch
(() => {
  const root = document.getElementById('punch');
  const kind = root.dataset.mode;                       // "in" | "out"
  const $ = id => document.getElementById(id);
  const video = $('video'), go = $('go'), promptEl = $('prompt'), countEl = $('count'), result = $('result');
  let geo = null, camReady = false, busy = false;

  const setStep = (id, state, text, txtId) => { $(id).className = 'step ' + state; $(txtId).textContent = text; };
  const refresh = () => { go.disabled = busy || !(geo && geo.ok && geo.allowed && camReady); };
  const sleep = ms => new Promise(r => setTimeout(r, ms));

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
      }
      else if (geo.inside) setStep('st-gps', 'ok', `Inside ${geo.office} · ${geo.distance} m (±${Math.round(accuracy)} m)`, 'gps-text');
      else if (geo.od) setStep('st-gps', 'ok', `On-duty approved (${geo.od_location || 'field'}) · ${geo.distance} m from office`, 'gps-text');
      else {
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
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user', width: { ideal: 640 }, height: { ideal: 480 } }, audio: false });
      video.srcObject = stream;
      await video.play();
      camReady = true;
      setStep('st-cam', 'ok', 'Front camera ready', 'cam-text');
    } catch (e) {
      setStep('st-cam', 'bad', 'Camera permission denied or unavailable', 'cam-text');
    }
    refresh();
  }

  function grab(w, h, q) {
    const c = document.createElement('canvas'); c.width = w; c.height = h;
    const ctx = c.getContext('2d');
    ctx.drawImage(video, 0, 0, w, h);
    return c.toDataURL('image/jpeg', q);
  }

  async function faceCheck() {
    if (!('FaceDetector' in window)) return true;     // optional: Chrome/Android shape-detection API
    try {
      const faces = await new window.FaceDetector({ fastMode: true, maxDetectedFaces: 2 }).detect(video);
      return faces.length === 1;
    } catch (e) { return true; }
  }

  async function countdown(sec) {
    for (let i = sec; i > 0; i--) { countEl.textContent = i; await sleep(1000); }
    countEl.textContent = '';
  }

  // ---------- Verify + punch
  go.addEventListener('click', async () => {
    busy = true; refresh(); result.hidden = true;
    try {
      if (!(await faceCheck())) throw new Error('Exactly one face must be visible in the oval.');
      const ch = await postJSON('/api/challenge', {});
      if (!ch.ok) throw new Error('Could not start liveness check.');
      const frames = [grab(160, 120, 0.6)];            // baseline
      await sleep(400);
      for (const p of ch.prompts) {
        promptEl.textContent = p.text;
        await countdown(2);
        frames.push(grab(160, 120, 0.6));
      }
      promptEl.textContent = 'Hold still…';
      await sleep(500);
      const selfie = grab(480, 360, 0.8);
      promptEl.textContent = '';
      const loc = geo ? { lat: geo.lat, lng: geo.lng, accuracy: geo.accuracy } : {};
      const res = await postJSON('/api/punch', { kind, token: ch.token, frames, selfie, ...loc });
      if (!res.ok) throw new Error(res.error || 'Something went wrong.');
      result.className = 'result ok'; result.hidden = false;
      result.innerHTML = `<b>${res.message}</b> at ${res.time}` + (res.hours !== undefined ? `<br>Worked ${res.hours} h · Status <b>${res.status}</b>` : '');
      go.textContent = 'Done'; 
      setTimeout(() => location.href = '/dashboard', 2200);
      return;
    } catch (e) {
      promptEl.textContent = ''; countEl.textContent = '';
      result.className = 'result bad'; result.hidden = false; result.textContent = e.message;
      locate();                                         // refresh GPS for next attempt
    }
    busy = false; refresh();
  });

  locate(); startCamera();
})();
