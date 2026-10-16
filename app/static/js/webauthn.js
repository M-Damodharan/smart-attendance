/**
 * Phone Owner Face ID / Biometric Authentication Helper (WebAuthn / Passkeys)
 * Supports Apple Face ID on iPhone and Biometric Face/Fingerprint on Android.
 */
window.WebAuthnHelper = (() => {
  // Convert ArrayBuffer to URL-safe base64 string
  function bufferToBase64Url(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary)
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '');
  }

  // Convert URL-safe base64 string to Uint8Array
  function base64UrlToBuffer(base64url) {
    let base64 = base64url.replace(/-/g, '+').replace(/_/g, '/');
    while (base64.length % 4) {
      base64 += '=';
    }
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
  }

  function isSupported() {
    return !!(window.PublicKeyCredential && navigator.credentials && navigator.credentials.create);
  }

  function getDeviceModelName() {
    const ua = navigator.userAgent;
    if (/iPhone/i.test(ua)) return 'iPhone (Face ID)';
    if (/iPad/i.test(ua)) return 'iPad (Face ID / Touch ID)';
    if (/Android/i.test(ua)) return 'Android Phone (Biometric)';
    if (/Macintosh/i.test(ua)) return 'Mac (Touch ID)';
    if (/Windows/i.test(ua)) return 'Windows PC (Windows Hello)';
    return 'Mobile Phone';
  }

  /**
   * Enroll phone biometric (Face ID / Passkey) with the server.
   */
  async function enrollDevice(customName) {
    if (!isSupported()) {
      throw new Error('Biometric authentication (WebAuthn/Face ID) is not supported in this browser.');
    }

    // 1. Get registration options from server
    const chalRes = await fetch('/api/webauthn/register/challenge', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': document.querySelector('input[name="csrf_token"]')?.value || ''
      }
    });
    const chalData = await chalRes.json();
    if (!chalData.ok) {
      throw new Error(chalData.error || 'Could not initiate biometric registration.');
    }

    const options = chalData.options;
    options.challenge = base64UrlToBuffer(options.challenge);
    options.user.id = base64UrlToBuffer(options.user.id);

    // 2. Trigger native Phone Face ID prompt
    const cred = await navigator.credentials.create({ publicKey: options });
    if (!cred) {
      throw new Error('Biometric registration was cancelled.');
    }

    // 3. Format payload and send to server for cryptographic verification and binding
    const payload = {
      id: cred.id,
      rawId: bufferToBase64Url(cred.rawId),
      type: cred.type,
      response: {
        clientDataJSON: bufferToBase64Url(cred.response.clientDataJSON),
        attestationObject: bufferToBase64Url(cred.response.attestationObject)
      },
      device_name: customName || getDeviceModelName()
    };

    const verifyRes = await fetch('/api/webauthn/register/verify', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': document.querySelector('input[name="csrf_token"]')?.value || ''
      },
      body: JSON.stringify(payload)
    });
    const verifyData = await verifyRes.json();
    if (!verifyData.ok) {
      throw new Error(verifyData.error || 'Failed to verify device registration.');
    }

    return verifyData;
  }

  /**
   * Request native Phone Face ID verification assertion for attendance punch.
   */
  async function getPunchAssertion() {
    if (!isSupported()) {
      throw new Error('Biometric authentication is not supported in this browser.');
    }

    // 1. Get punch challenge options from server
    const chalRes = await fetch('/api/webauthn/punch/challenge', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': document.querySelector('input[name="csrf_token"]')?.value || ''
      }
    });
    const chalData = await chalRes.json();
    if (!chalData.ok) {
      throw new Error(chalData.error || 'Could not initiate biometric punch challenge.');
    }

    const options = chalData.options;
    options.challenge = base64UrlToBuffer(options.challenge);
    if (options.allowCredentials) {
      options.allowCredentials = options.allowCredentials.map(c => ({
        ...c,
        id: base64UrlToBuffer(c.id)
      }));
    }

    // 2. Trigger native Phone Face ID prompt
    const assertion = await navigator.credentials.get({ publicKey: options });
    if (!assertion) {
      throw new Error('Biometric Face ID verification was cancelled.');
    }

    // 3. Package assertion payload
    return {
      id: assertion.id,
      rawId: bufferToBase64Url(assertion.rawId),
      type: assertion.type,
      response: {
        clientDataJSON: bufferToBase64Url(assertion.response.clientDataJSON),
        authenticatorData: bufferToBase64Url(assertion.response.authenticatorData),
        signature: bufferToBase64Url(assertion.response.signature),
        userHandle: assertion.response.userHandle ? bufferToBase64Url(assertion.response.userHandle) : null
      }
    };
  }

  return {
    isSupported,
    getDeviceModelName,
    enrollDevice,
    getPunchAssertion
  };
})();
