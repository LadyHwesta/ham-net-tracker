// ============================================================
// OFFLINE CHECK-IN QUEUE
// ============================================================
// Plain IndexedDB (no library) so a check-in submitted with no connection
// can be queued locally and replayed once back online, instead of silently
// failing during a live net. Shared between the page (foreground retry —
// the cross-browser guarantee, works on iOS Safari too) and the service
// worker's Background Sync handler (best-effort extra chance to flush if
// the tab is backgrounded/closed — not supported on iOS, so never the only
// mechanism). No DOM dependency, so the service worker can importScripts()
// this file directly.

const OFFLINE_DB_NAME = 'net_tracker_offline';
const OFFLINE_DB_VERSION = 1;
const OFFLINE_STORE = 'pending_checkins';

function _openOfflineDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(OFFLINE_DB_NAME, OFFLINE_DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(OFFLINE_STORE)) {
        db.createObjectStore(OFFLINE_STORE, { keyPath: 'id' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function queueCheckin(sessionId, payload, token) {
  const db = await _openOfflineDb();
  const record = {
    id: (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`),
    session_id: sessionId,
    payload,
    token,
    queued_at: new Date().toISOString(),
    status: 'pending',
  };
  await new Promise((resolve, reject) => {
    const tx = db.transaction(OFFLINE_STORE, 'readwrite');
    tx.objectStore(OFFLINE_STORE).add(record);
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
  db.close();
  return record;
}

async function getAllPendingCheckins() {
  const db = await _openOfflineDb();
  const items = await new Promise((resolve, reject) => {
    const tx = db.transaction(OFFLINE_STORE, 'readonly');
    const req = tx.objectStore(OFFLINE_STORE).getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
  db.close();
  return items;
}

async function getPendingCheckins(sessionId) {
  const all = await getAllPendingCheckins();
  return all.filter(r => r.session_id === sessionId);
}

async function _removeQueuedCheckin(id) {
  const db = await _openOfflineDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction(OFFLINE_STORE, 'readwrite');
    tx.objectStore(OFFLINE_STORE).delete(id);
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

async function _markQueuedCheckinFailed(id, message) {
  const db = await _openOfflineDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction(OFFLINE_STORE, 'readwrite');
    const store = tx.objectStore(OFFLINE_STORE);
    const getReq = store.get(id);
    getReq.onsuccess = () => {
      const record = getReq.result;
      if (record) {
        record.status = 'failed';
        record.last_error = message;
        store.put(record);
      }
    };
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

// Atomically transitions one item 'pending' -> 'sending' and returns
// whether *this* caller won the claim. The online listener, the 15s poll, a
// manual "Retry Now" click, the browser's own native `online` event, and the
// service worker's independent Background Sync handler can all try to flush
// within milliseconds of each other — including from a different execution
// context (page vs. service worker) that an in-memory flag can't coordinate
// with. A single IndexedDB readwrite transaction is atomic and serialized
// against every other transaction on the same store, in every context, so
// this is the actual guarantee against double-submitting a check-in.
function _claimItem(id) {
  return new Promise((resolve, reject) => {
    _openOfflineDb().then(db => {
      const tx = db.transaction(OFFLINE_STORE, 'readwrite');
      const store = tx.objectStore(OFFLINE_STORE);
      const getReq = store.get(id);
      let claimed = false;
      getReq.onsuccess = () => {
        const record = getReq.result;
        if (record && record.status === 'pending') {
          record.status = 'sending';
          store.put(record);
          claimed = true;
        }
      };
      tx.oncomplete = () => { db.close(); resolve(claimed); };
      tx.onerror = () => { db.close(); reject(tx.error); };
    }, reject);
  });
}

async function _resetToPending(id) {
  const db = await _openOfflineDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction(OFFLINE_STORE, 'readwrite');
    const store = tx.objectStore(OFFLINE_STORE);
    const getReq = store.get(id);
    getReq.onsuccess = () => {
      const record = getReq.result;
      if (record && record.status === 'sending') {
        record.status = 'pending';
        store.put(record);
      }
    };
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

// Replays every queued check-in against the real API. Returns true if at
// least one item's status changed (so callers know whether to re-render).
// Never throws — a still-offline retry is expected, not exceptional.
async function flushCheckinQueue() {
  const items = await getAllPendingCheckins();
  let changed = false;
  for (const item of items) {
    if (item.status !== 'pending') continue;  // already 'sending' elsewhere, or 'failed'
    if (!(await _claimItem(item.id))) continue;  // lost the race — someone else is sending it
    try {
      const res = await fetch(`/sessions/${item.session_id}/checkins`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + item.token,
        },
        body: JSON.stringify(item.payload),
      });
      if (res.ok) {
        await _removeQueuedCheckin(item.id);
        changed = true;
      } else if (res.status >= 400 && res.status < 500) {
        // Unrecoverable by retrying (expired token, session gone, bad payload) — stop.
        const data = await res.json().catch(() => ({}));
        await _markQueuedCheckinFailed(item.id, data.detail || res.statusText);
        changed = true;
      } else {
        await _resetToPending(item.id);  // 5xx — leave pending, try again next pass
      }
    } catch {
      await _resetToPending(item.id);  // still offline — leave pending, try again next pass
    }
  }
  return changed;
}

async function removeFailedCheckin(id) {
  await _removeQueuedCheckin(id);
}
