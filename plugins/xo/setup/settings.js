'use strict';
// settings.json register / unregister / legacy cleanup. Strips a UTF-8 BOM,
// writes atomically, and backs up an existing file before its first change.
const fs = require('fs');
const path = require('path');
const { atomicWrite } = require('../hooks/_common.js');
const io = require('./io');

class SettingsError extends Error {
    constructor(file) { super(`invalid JSON in ${file}`); this.name = 'SettingsError'; this.file = file; }
}

// win32: normalised, case-insensitive; elsewhere: exact.
function samePath(a, b) {
    if (typeof a !== 'string' || typeof b !== 'string') return false;
    if (!io.IS_WIN) return a === b;
    const norm = (p) => path.resolve(p).replace(/[\\/]+$/, '').toLowerCase();
    return norm(a) === norm(b);
}

// Returns null when the file does not exist; throws SettingsError on invalid JSON.
function load(file) {
    if (!fs.existsSync(file)) return null;
    const raw = fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '');
    if (!raw.trim()) return {};
    let data;
    try { data = JSON.parse(raw); } catch (_) { throw new SettingsError(file); }
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new SettingsError(file);
    return data;
}

const backedUp = new Set();

function save(file, data) {
    if (fs.existsSync(file) && !backedUp.has(file)) {
        const bak = file + '.xo-backup';
        fs.copyFileSync(file, bak);
        backedUp.add(file);
        io.info(`Backed up settings.json to ${bak}`);
    }
    atomicWrite(file, JSON.stringify(data, null, 2) + '\n');
}

function hasPlugin(data, plugin) {
    return Array.isArray(data.plugins) && data.plugins.some((p) => samePath(p, plugin));
}

// -> 'created' | 'already' | 'added'
function register(file, plugin) {
    const data = load(file);
    if (data === null) {
        save(file, { plugins: [plugin] });
        return 'created';
    }
    if (hasPlugin(data, plugin)) return 'already';
    data.plugins = (Array.isArray(data.plugins) ? data.plugins : []).concat([plugin]);
    save(file, data);
    return 'added';
}

// -> 'no-file' | 'absent' | 'removed'
function unregister(file, plugin) {
    const data = load(file);
    if (data === null) return 'no-file';
    if (!hasPlugin(data, plugin)) return 'absent';
    data.plugins = data.plugins.filter((p) => !samePath(p, plugin));
    if (!data.plugins.length) delete data.plugins;
    save(file, data);
    return 'removed';
}

// Removes a legacy env.XOCORTEX_HOME entry (it never reached hooks). -> boolean
function removeLegacyEnv(file) {
    const data = load(file);
    if (!data || !data.env || typeof data.env !== 'object' || data.env.XOCORTEX_HOME == null) return false;
    delete data.env.XOCORTEX_HOME;
    if (!Object.keys(data.env).length) delete data.env;
    save(file, data);
    return true;
}

// -> { state: 'no-file' | 'invalid' | 'registered' | 'not-registered', data }
function status(file, plugin) {
    let data;
    try { data = load(file); } catch (_) { return { state: 'invalid', data: null }; }
    if (data === null) return { state: 'no-file', data: null };
    return { state: hasPlugin(data, plugin) ? 'registered' : 'not-registered', data };
}

// True when dir holds a plugin manifest named "xo". Unreadable -> false.
function isXoPluginDir(dir) {
    try {
        const m = JSON.parse(fs.readFileSync(path.join(dir, '.cortex-plugin', 'plugin.json'), 'utf8').replace(/^\uFEFF/, ''));
        return !!m && m.name === 'xo';
    } catch (_) { return false; }
}

// XO plugin folders in data.plugins other than `keep`.
function otherXoEntries(data, keep) {
    if (!data || !Array.isArray(data.plugins)) return [];
    return data.plugins.filter((p) => typeof p === 'string' && !samePath(p, keep) && isXoPluginDir(p));
}

// Removes XO entries other than `keep` from settings.plugins. -> removed paths
function removeOtherXo(file, keep) {
    const data = load(file);
    const stale = otherXoEntries(data, keep);
    if (!stale.length) return [];
    data.plugins = data.plugins.filter((p) => !stale.includes(p));
    if (!data.plugins.length) delete data.plugins;
    save(file, data);
    return stale;
}

module.exports = {
    SettingsError,
    samePath,
    load,
    save,
    register,
    unregister,
    removeLegacyEnv,
    status,
    otherXoEntries,
    removeOtherXo,
};
