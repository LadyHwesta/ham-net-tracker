// ============================================================
// CONFIG
// ============================================================
const API = '';

// ============================================================
// STATE
// ============================================================
let token = localStorage.getItem('nt_token') || null;
let currentUser = null;
let nets = [];
let currentNetId = null;
let currentNetOwnerId = null;
let currentNetIsAres = false;
let currentNetIsGmrs = false;
let currentSessionId = null;
let activeView = 'nets';
let historyData = [];
let editNetId = null;
let allUsers = [];   // for sharing UI — populated when opening edit form
let shareState = { share_with_all: false, user_ids: [] };
let evacZones = {};   // callsign → zone, loaded from /nets/{id}/evac-zones
let currentNetScript = null;   // raw (unrendered) net.script text for the open net
