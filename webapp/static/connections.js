/* Remote input is enabled only inside this explicit sign-in dialog. */
(() => {
  const dialog = document.getElementById('connectionDialog');
  const canvas = document.getElementById('connectionScreen');
  const ctx = canvas.getContext('2d');
  const status = document.getElementById('connectionStatus');
  const entry = document.getElementById('connectionText');
  let socket = null, generation = 0, ready = false, dragging = false, lastMove = 0;
  const names = {youtube: 'YouTube', instagram: 'Instagram / Meta', facebook: 'Facebook / Meta', tiktok: 'TikTok'};
  const send = data => {
    if (ready && socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(data));
  };
  function clear() {
    generation++;
    ready = false;
    dragging = false;
    entry.value = '';
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    socket?.close();
    socket = null;
    refreshAccounts();
  }
  dialog.addEventListener('close', clear);
  document.getElementById('connectionDone').onclick = () => dialog.close();
  document.querySelectorAll('[data-connect]').forEach(button => {
    button.onclick = async () => {
      if (!await requireSignIn()) return;
      clear();
      const current = generation, platform = button.dataset.connect;
      document.getElementById('connectionTitle').textContent = 'Connect ' + names[platform];
      status.textContent = 'Opening your private browser…';
      dialog.showModal();
      try {
        const response = await fetch('/api/start', {method: 'POST'});
        const result = await response.json();
        if (!response.ok) throw Error(result.error || 'Could not start your browser.');
        if (current !== generation || !dialog.open) return;
        socket = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws/connect/' + platform);
        socket.onmessage = event => {
          if (current !== generation) return;
          const message = JSON.parse(event.data);
          if (message.t === 'error' || message.t === 'notice') {
            status.textContent = message.message;
          } else if (message.t === 'frame') {
            const image = new Image();
            image.onload = () => {
              if (current !== generation || !dialog.open) return;
              canvas.width = image.width; canvas.height = image.height;
              ctx.drawImage(image, 0, 0);
              ready = true;
              status.textContent = 'Click the page to sign in. Complete any verification here, then select Done.';
            };
            image.src = 'data:image/jpeg;base64,' + message.d;
          }
        };
        socket.onclose = event => {
          if (current !== generation) return;
          ready = false;
          entry.value = '';
          ctx.clearRect(0, 0, canvas.width, canvas.height);
          status.textContent = event.reason || 'Sign-in view closed. Reconnect to continue. Your saved platform sessions are kept.';
          refreshAccounts();
        };
        socket.onerror = () => { if (current === generation) status.textContent = 'Could not connect. Close this view and try again.'; };
      } catch (error) {
        if (current === generation) status.textContent = error.message;
      }
    };
  });
  function point(event) {
    const rect = canvas.getBoundingClientRect();
    return {x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
            y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height))};
  }
  canvas.addEventListener('pointerdown', event => {
    if (!ready) return;
    event.preventDefault(); canvas.focus({preventScroll: true}); canvas.setPointerCapture(event.pointerId);
    dragging = true; send({type: 'down', ...point(event)});
  });
  canvas.addEventListener('pointermove', event => {
    if (!dragging || performance.now() - lastMove < 40) return;
    lastMove = performance.now(); send({type: 'move', ...point(event)});
  });
  const release = event => { if (dragging) send({type: 'up', ...point(event)}); dragging = false; };
  canvas.addEventListener('pointerup', release);
  canvas.addEventListener('pointercancel', release);
  canvas.addEventListener('wheel', event => {
    if (!ready) return;
    event.preventDefault();
    send({type: 'scroll', dx: Math.max(-1500, Math.min(1500, event.deltaX)), dy: Math.max(-1500, Math.min(1500, event.deltaY))});
  }, {passive: false});
  canvas.addEventListener('keydown', event => {
    if (!ready) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'v') return;
    const allowed = ['Enter', 'Tab', 'Backspace', 'Delete', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'];
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
      event.preventDefault(); send({type: 'key', key: 'ControlOrMeta+A'});
    } else if (allowed.includes(event.key)) {
      event.preventDefault(); send({type: 'key', key: event.shiftKey && event.key === 'Tab' ? 'Shift+Tab' : event.key});
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault(); send({type: 'text', text: event.key});
    }
  });
  canvas.addEventListener('paste', event => {
    event.preventDefault(); const text = event.clipboardData.getData('text');
    if (text && text.length <= 4096) send({type: 'text', text});
  });
  document.getElementById('connectionType').onsubmit = event => {
    event.preventDefault();
    if (entry.value && ready) { send({type: 'text', text: entry.value}); entry.value = ''; canvas.focus({preventScroll: true}); }
  };
  document.querySelectorAll('[data-remote-key]').forEach(button => {
    button.onclick = () => { send({type: 'key', key: button.dataset.remoteKey}); canvas.focus({preventScroll: true}); };
  });
})();

// Direct local sign-in deliberately has no streamed page or remote input.
for (const [id, action] of [['manualStart', 'start?platform=youtube'], ['manualFinish', 'finish']]) {
  document.getElementById(id).onclick = async event => {
    const button = event.currentTarget;
    if (!await requireSignIn()) return;
    button.disabled = true;
    const message = document.getElementById('manualStatus');
    message.textContent = id === 'manualStart' ? 'Opening Chrome for direct sign-in…' : 'Reopening your saved profile for posting…';
    try {
      const response = await fetch('/api/manual-login/' + action, {method: 'POST'});
      const result = await response.json();
      if (!response.ok) throw Error(result.error || 'Could not change sign-in mode.');
      await refreshAccounts();
      if (id === 'manualFinish') {
        message.textContent = 'Profile reopened. Check the account status above; saved sessions may still require verification.';
        connectWs();
      }
    } catch (error) { message.textContent = error.message; }
    finally { button.disabled = false; }
  };
}
