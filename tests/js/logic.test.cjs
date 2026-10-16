const test = require('node:test');
const assert = require('node:assert/strict');
const L = require('../../src/hq/static/js/logic.js');

const flush = () => new Promise((resolve) => setImmediate(resolve));
const servers = [
  { id: '1', name: 'WEB-PRD-01', nickname: '웹01', host: '10.0.1.21', group: '운영', memo: 'IIS 8.5' },
  { id: '2', name: 'DB-01', nickname: '', host: '10.0.1.31', group: '', memo: '백업 서버' },
  { id: '3', name: 'APP-01', nickname: '앱', host: '10.0.2.5', group: '개발', memo: '' },
];
const DA1 = '\x1b[?1;2c';

test('displayName falls back to the name', () => {
  assert.equal(L.displayName(servers[0]), '웹01');
  assert.equal(L.displayName(servers[1]), 'DB-01');
});

test('filterServers matches name, nickname, host and memo, ignoring case', () => {
  const ids = (q) => L.filterServers(servers, q).map((s) => s.id);
  assert.deepEqual(ids('  웹 '), ['1']);
  assert.deepEqual(ids('db'), ['2']);
  assert.deepEqual(ids('10.0.2'), ['3']);
  assert.deepEqual(ids('백업'), ['2']);
  assert.deepEqual(ids(''), ['1', '2', '3']);
});

test('groupServers sorts groups and puts 미분류 last', () => {
  const groups = L.groupServers(servers);
  assert.deepEqual(groups.map((g) => g.name), ['개발', '운영', L.UNGROUPED]);
  assert.deepEqual(groups[2].servers.map((s) => s.id), ['2']);
});

test('tab titles use the smallest free ordinal', () => {
  assert.equal(L.nextOrdinal([]), 1);
  assert.equal(L.nextOrdinal([1]), 2);
  assert.equal(L.nextOrdinal([2]), 1);
  assert.equal(L.nextOrdinal([1, 3]), 2);
  assert.equal(L.tabTitle(servers[0], 1), '웹01');
  assert.equal(L.tabTitle(servers[0], 2), '웹01 (2)');
});

test('paste needs confirmation only for more than one non-empty line', () => {
  assert.equal(L.needsPasteConfirm('Get-Process'), false);
  assert.equal(L.needsPasteConfirm('Get-Process\r\n'), false);
  assert.equal(L.needsPasteConfirm('a\nb'), true);
  assert.equal(L.pastedLineCount('a\r\n\r\nb\rc'), 3);
});

test('shortcuts', () => {
  const key = (props) => L.shortcutFor({ key: '', code: '', ctrlKey: false, shiftKey: false, altKey: false, ...props });
  assert.deepEqual(key({ key: 'Tab', ctrlKey: true }), { type: 'next' });
  assert.deepEqual(key({ key: 'Tab', ctrlKey: true, shiftKey: true }), { type: 'prev' });
  assert.deepEqual(key({ key: '3', code: 'Digit3', altKey: true }), { type: 'index', index: 2 });
  assert.deepEqual(key({ key: 'k', ctrlKey: true }), { type: 'search' });
  assert.deepEqual(key({ key: 'F5' }), { type: 'block' });
  assert.deepEqual(key({ key: 'r', ctrlKey: true }), { type: 'block' });
  assert.equal(key({ key: 'c', ctrlKey: true }), null);
  assert.equal(key({ key: 'a' }), null);
});

test('statusView', () => {
  assert.deepEqual(L.statusView(true), { label: '온라인', cls: 'on' });
  assert.deepEqual(L.statusView(false), { label: '응답 없음', cls: 'off' });
  assert.deepEqual(L.statusView(undefined), { label: '확인 전', cls: 'unknown' });
});

test('endedView covers every reason and falls back to UNKNOWN', () => {
  const reasons = ['USER_EXIT', 'AUTH_FAILED', 'UNREACHABLE', 'NOT_TRUSTED', 'QUOTA', 'SECRET_UNREADABLE',
    'DISCONNECTED', 'NOT_READY', 'UNKNOWN'];
  for (const reason of reasons) {
    const view = L.endedView(reason);
    assert.ok(view.text && view.short && view.actions.length > 0, reason);
    if (reason !== 'UNKNOWN') assert.notEqual(view.text, L.endedView('UNKNOWN').text, reason);
  }
  assert.deepEqual(L.endedView('NOPE'), L.endedView('UNKNOWN'));
  assert.deepEqual(L.endedView('AUTH_FAILED').actions.map((a) => a.id), ['password', 'setup']);
  assert.equal(L.endedView('DISCONNECTED').actions[0].label, '다시 연결');
});

test('endedView explains NOT_READY', () => {
  assert.deepEqual(L.endedView('NOT_READY'), {
    short: '시작 안 됨',
    text: '접속 스크립트가 시작되지 않았습니다. 백신이나 보안 정책이 막았을 수 있습니다.',
    actions: [{ id: 'reconnect', label: '다시 연결' }],
  });
});

test('isTerminalReply accepts only whole chunks of terminal replies', () => {
  for (const reply of [DA1, '\x1b[?62;22c', '\x1b[?c', '\x1b[>0;276;0c', '\x1b[12;40R', '\x1b[0n', '\x1b[I', '\x1b[O',
    `${DA1}\x1b[I`, '\x1b[O\x1b[1;1R']) {
    assert.equal(L.isTerminalReply(reply), true, JSON.stringify(reply));
  }
  for (const other of ['', 'a', `${DA1}a`, `a${DA1}`, `${DA1}\r`, `${DA1}\n`, '\x1b[A', '\x1b[3n', '\x1b[1;R', '\r',
    undefined, null]) {
    assert.equal(L.isTerminalReply(other), false, JSON.stringify(other));
  }
});

test('input queue keeps order and batches ordinary keys while a call is in flight', async () => {
  const sent = [];
  const pending = [];
  const queue = L.createInputQueue((data) => {
    sent.push(data);
    return new Promise((resolve) => pending.push(resolve));
  });
  queue.push('a');
  await flush();
  assert.deepEqual(sent, ['a']);
  queue.push('b');
  queue.push('c');
  await flush();
  assert.deepEqual(sent, ['a']);
  assert.equal(queue.pending(), 'bc');
  pending.shift()();
  await flush();
  assert.deepEqual(sent, ['a', 'bc']);
  assert.equal(queue.pending(), '');
});

test('input queue sends a terminal reply alone and in order', async () => {
  const sent = [];
  const pending = [];
  const queue = L.createInputQueue((data) => {
    sent.push(data);
    return new Promise((resolve) => pending.push(resolve));
  });
  queue.push('a');
  queue.push('b');
  queue.push(DA1);
  queue.push('\x1b[I');
  queue.push('c');
  queue.push('d');
  await flush();
  assert.deepEqual(sent, ['a']);
  assert.equal(queue.pending(), `b${DA1}\x1b[Icd`);
  while (pending.length > 0) {
    pending.shift()();
    await flush();
  }
  assert.deepEqual(sent, ['a', 'b', DA1, '\x1b[I', 'cd']);
});

test('input queue sends a terminal reply right away when idle', async () => {
  const sent = [];
  const queue = L.createInputQueue((data) => {
    sent.push(data);
    return Promise.resolve();
  });
  queue.push(DA1);
  await flush();
  queue.push('x');
  await flush();
  assert.deepEqual(sent, [DA1, 'x']);
});

test('input queue logs a failed call and keeps going', async (t) => {
  const warn = t.mock.method(console, 'warn', () => {});
  const sent = [];
  const error = new Error('x');
  const queue = L.createInputQueue((data) => {
    sent.push(data);
    return Promise.reject(error);
  });
  queue.push('a');
  await flush();
  queue.push('b');
  await flush();
  assert.deepEqual(sent, ['a', 'b']);
  assert.equal(warn.mock.callCount(), 2);
  assert.ok(warn.mock.calls[0].arguments.includes(error));
});

test('newId is 32 lowercase hex digits', () => {
  assert.match(L.newId(), /^[0-9a-f]{32}$/);
  assert.notEqual(L.newId(), L.newId());
});
