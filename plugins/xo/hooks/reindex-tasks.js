'use strict';
const fs = require('fs');
const path = require('path');
const { isoUtc, atomicWrite, appendLog } = require('./_common.js');

function reindexTasks(tasksDir, logFile, shortSid) {
    const currentDir = path.join(tasksDir, 'current');
    const archiveDir = path.join(tasksDir, 'archive');
    const indexFile = path.join(tasksDir, 'index.md');

    if (!fs.existsSync(currentDir)) return;

    const ts = isoUtc();

    const stateOrd = { Now: 1, Next: 2, Later: 3 };
    const statusOrd = s => (s && s.startsWith('InProgress') ? 1 : s && s.startsWith('Shipped') ? 2 : s === 'Ready' ? 3 : 4);

    const entries = [];
    const wiGlob = fs.readdirSync(currentDir).filter(n => /(?:^|-)wi\d+.*\.md$/.test(n));
    for (const fname of wiGlob) {
        const fpath = path.join(currentDir, fname);
        if (!fs.statSync(fpath).isFile()) continue;
        const base = path.basename(fname, '.md');
        const wiNumMatch = base.match(/(?:^|-)wi0*(\d+)/);
        const wiNum = wiNumMatch ? parseInt(wiNumMatch[1], 10) : 0;

        const lines = fs.readFileSync(fpath, 'utf8').split('\n');
        let title = '', state = '', wi_status = '', status_note = '';
        for (const line of lines) {
            if (!title && /^# WI-/.test(line)) {
                title = line.replace(/^# WI-\d+: /, '');
            } else if (/^- Priority: /.test(line)) {
                state = line.replace(/^- Priority: /, '');
            } else if (/^- Status: /.test(line)) {
                wi_status = line.replace(/^- Status: /, '');
            } else if (/^- StatusNote: /.test(line)) {
                status_note = line.replace(/^- StatusNote: */, '');
                break;
            } else if (/^- Blocker: /.test(line)) {
                break;
            }
        }

        const so = stateOrd[state] ?? 3;
        const sto = statusOrd(wi_status);
        const wiPad = String(wiNum).padStart(4, '0');
        entries.push({ so, sto, wiPad, wiNum, state, wi_status, status_note, title });
    }

    entries.sort((a, b) => a.so - b.so || a.sto - b.sto || a.wiPad.localeCompare(b.wiPad));

    const archiveEntries = [];
    if (fs.existsSync(archiveDir)) {
        const archiveDirs = fs.readdirSync(archiveDir).filter(n => {
            return fs.statSync(path.join(archiveDir, n)).isDirectory();
        });
        for (const subdir of archiveDirs) {
            const subdirPath = path.join(archiveDir, subdir);
            const files = fs.readdirSync(subdirPath).filter(n => /(?:^|-)wi\d+.*\.md$/.test(n));
            for (const fname of files) {
                const fpath = path.join(subdirPath, fname);
                if (!fs.statSync(fpath).isFile()) continue;
                const base = path.basename(fname, '.md');
                const wiNumMatch = base.match(/(?:^|-)wi0*(\d+)/);
                const wiNum = wiNumMatch ? parseInt(wiNumMatch[1], 10) : 0;
                const wiPad = String(wiNum).padStart(4, '0');
                const lines = fs.readFileSync(fpath, 'utf8').split('\n');
                let title = '';
                for (const line of lines) {
                    if (/^# WI-/.test(line)) {
                        title = line.replace(/^# WI-\d+: /, '');
                        break;
                    }
                }
                archiveEntries.push({ wiPad, wiNum, title });
            }
        }
    }
    archiveEntries.sort((a, b) => a.wiPad.localeCompare(b.wiPad));

    const header = [
        '---',
        `generated: ${ts}`,
        `active_count: ${entries.length}`,
        `archived_count: ${archiveEntries.length}`,
        '---',
        '',
        '# Work Items Index',
        '',
        '| WI | Priority | Status | StatusNote | Description |',
        '|-----|-------|--------|------------|-------------|',
    ].join('\n');

    const activeRows = entries
        .map(e => `| ${e.wiNum} | ${e.state} | ${e.wi_status} | ${e.status_note} | ${e.title} |`)
        .join('\n');

    let content = header + '\n' + activeRows + '\n';

    if (archiveEntries.length > 0) {
        const archiveRows = archiveEntries
            .map(e => `| ${e.wiNum} | - | Done | | ${e.title} |`)
            .join('\n');
        content += '\n### Archived\n| WI | Priority | Status | StatusNote | Description |\n|-----|-------|--------|------------|-------------|\n' + archiveRows + '\n';
    }

    atomicWrite(indexFile, content);

    if (logFile && shortSid) {
        appendLog(logFile, `[${isoUtc()}] [${shortSid}] reindexed ${entries.length} active + ${archiveEntries.length} archived work items -> ${indexFile}`);
    }
}

module.exports = { reindexTasks };
