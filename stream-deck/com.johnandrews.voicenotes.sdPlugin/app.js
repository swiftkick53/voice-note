/* Voice Notes — Stream Deck plugin
 *
 * Talks to the app's loopback control API (see stream-deck/README.md).
 * Dictate/Note keys: tap = toggle, hold ≥500ms = push-to-talk (stops on
 * release). Keys show live state: red blob + elapsed while recording,
 * cyan while transcribing, green while saving.
 */

var ws = null;
var pluginUUID = null;
var settings = { port: 48752, token: '' };   // global settings
var contexts = {};      // context → { action: short name }
var lastStatus = null;  // last /status payload
var pollTimer = null;
var keyState = {};      // context → { downAt, stoppedOnDown }

var HOLD_MS = 500;

function api(path, cb) {
  if (!settings.token) return;
  var xhr = new XMLHttpRequest();
  var method = path === '/status' ? 'GET' : 'POST';
  xhr.open(method, 'http://127.0.0.1:' + settings.port + path +
           (path.indexOf('?') < 0 ? '?' : '&') + 'token=' + encodeURIComponent(settings.token));
  xhr.timeout = 3000;
  xhr.onload = function () {
    if (cb) {
      try { cb(xhr.status, JSON.parse(xhr.responseText)); }
      catch (e) { cb(xhr.status, null); }
    }
  };
  xhr.onerror = xhr.ontimeout = function () { if (cb) cb(0, null); };
  xhr.send();
}

function sdSend(obj) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); }
function setImage(context, key) {
  // ICONS comes from icons.js (base64 data URLs); null resets to manifest image
  sdSend({ event: 'setImage', context: context,
           payload: { image: key ? ICONS[key] : '', target: 0 } });
}
function setTitle(context, title) {
  sdSend({ event: 'setTitle', context: context, payload: { title: title || '', target: 0 } });
}
function showOk(context) { sdSend({ event: 'showOk', context: context }); }
function showAlert(context) { sdSend({ event: 'showAlert', context: context }); }

function fmt(s) { return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); }

function paintAll() {
  var st = lastStatus;
  Object.keys(contexts).forEach(function (ctx) {
    var action = contexts[ctx].action;
    if (action !== 'dictate' && action !== 'note') return;
    if (!st) { setImage(ctx, null); setTitle(ctx, ''); return; }
    var mine = (st.mode === (action === 'dictate' ? 'dictation' : 'note'));
    if (st.state === 'recording' && mine) {
      setImage(ctx, 'recording');
      setTitle(ctx, fmt(st.recording_elapsed || 0));
    } else if (st.state === 'transcribing' && mine) {
      setImage(ctx, 'transcribing');
      setTitle(ctx, '');
    } else if (st.state === 'saving' && action === 'note') {
      setImage(ctx, 'saving');
      setTitle(ctx, '');
    } else {
      setImage(ctx, null);
      setTitle(ctx, '');
    }
  });
}

function poll() {
  api('/status', function (code, body) {
    lastStatus = (code === 200) ? body : null;
    paintAll();
  });
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(poll, 1000);
  poll();
}
function stopPollingIfIdle() {
  if (Object.keys(contexts).length === 0 && pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function onKeyDown(context, action) {
  var st = lastStatus || {};
  if (action === 'cancel') { api('/cancel', ack(context)); return; }
  if (action === 'save') { api('/save', ack(context)); return; }

  // dictate / note — toggle or push-to-talk
  var path = action === 'dictate' ? '/dictate' : '/note';
  var recording = st.state === 'recording';
  keyState[context] = { downAt: Date.now(), stoppedOnDown: recording };
  api(path, ack(context));   // starts, or stops if already recording
}

function onKeyUp(context, action) {
  if (action !== 'dictate' && action !== 'note') return;
  var ks = keyState[context];
  delete keyState[context];
  if (!ks || ks.stoppedOnDown) return;
  if (Date.now() - ks.downAt >= HOLD_MS) {
    // push-to-talk: held while speaking → stop on release
    api(action === 'dictate' ? '/dictate' : '/note', null);
  }
}

function ack(context) {
  return function (code) {
    if (code === 200) { showOk(context); poll(); }
    else showAlert(context);
  };
}

/* Stream Deck runtime entry point */
function connectElgatoStreamDeckSocket(inPort, inPluginUUID, inRegisterEvent, inInfo) {
  pluginUUID = inPluginUUID;
  ws = new WebSocket('ws://127.0.0.1:' + inPort);

  ws.onopen = function () {
    sdSend({ event: inRegisterEvent, uuid: inPluginUUID });
    sdSend({ event: 'getGlobalSettings', context: inPluginUUID });
  };

  ws.onmessage = function (msg) {
    var data = JSON.parse(msg.data);
    var event = data.event;
    var context = data.context;
    var short = data.action ? data.action.split('.').pop() : null;

    if (event === 'didReceiveGlobalSettings') {
      var s = (data.payload && data.payload.settings) || {};
      if (s.token) settings.token = s.token;
      if (s.port) settings.port = Number(s.port) || 48752;
      poll();
    } else if (event === 'willAppear') {
      contexts[context] = { action: short };
      startPolling();
    } else if (event === 'willDisappear') {
      delete contexts[context];
      stopPollingIfIdle();
    } else if (event === 'keyDown') {
      onKeyDown(context, short);
    } else if (event === 'keyUp') {
      onKeyUp(context, short);
    }
  };
}
