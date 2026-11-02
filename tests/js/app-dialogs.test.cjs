const test = require('node:test');
const assert = require('node:assert/strict');
const { startApp, settle, deferred } = require('./fake-dom.cjs');

const SERVER = {
  id: 'a'.repeat(32), name: 'WEB-01', nickname: '', host: '192.0.2.21', group: '', username: 'Administrator', memo: '',
  has_password: false,
};
const PLAINTEXT_WARNING = '엑셀 파일에 비밀번호가 평문으로 들어 있습니다. 지금 지우세요.';

/* 가짜 hq. 부른 횟수를 세고, 시험이 바꿔 끼울 수 있다 */
function fakeApi(overrides = {}) {
  const calls = {};
  const servers = [{ ...SERVER }];
  const api = {
    list_servers: async () => ({ ok: true, servers: servers.map((s) => ({ ...s })) }),
    probe: async () => ({ ok: true, status: {} }),
    missing_trusted_hosts: async () => ({ ok: true, readable: true, missing: [] }),
    set_password: async (id) => {
      servers.find((s) => s.id === id).has_password = true;
      return { ok: true };
    },
    ...overrides,
  };
  for (const [name, call] of Object.entries(api)) {
    api[name] = (...args) => {
      calls[name] = (calls[name] || 0) + 1;
      return call(...args);
    };
  }
  return { api, calls, servers };
}

test('an excel apply that finishes behind another dialog still reloads the list and shows the plaintext warning', async () => {
  // 등록하는 사이에 다른 창(여기서는 X의 끝내기 확인)이 미리보기를 밀어내면, 미리보기를 기다리던 쪽이 undefined를 받아
  // 목록을 다시 읽지 않고 spec 5.4의 평문 경고도 띄우지 않았다(최종 검토 M-3)
  const applying = deferred();
  const { api, calls } = fakeApi({
    preview_excel: async () => ({ ok: true, preview_id: 'p'.repeat(32), rows: [], counts: { new: 1, existing: 0, error: 0 } }),
    apply_excel: () => applying.promise,
  });
  const page = startApp(api);
  await settle();
  const modal = page.$('#modal');
  page.$('#excel').click();
  await settle();
  page.button(modal, '파일 고르기').click();
  await settle();
  page.button(modal, '등록').click();
  await settle();
  assert.equal(calls.apply_excel, 1);
  page.window.hq.onCloseRequested(1); // 미리보기를 밀어낸다
  assert.match(modal.textContent, /열린 탭 1개를 닫고 끝낼까요\?/);
  const listed = calls.list_servers;
  applying.resolve({ ok: true, added: 1, updated: 0, skipped: 0, errors: 0 });
  await settle();
  assert.equal(calls.list_servers, listed + 1, '등록한 서버를 보이도록 목록을 다시 읽는다');
  // 알림이 지금 떠 있는 창을 밀어내지 않는다. 밀어내면 그 창의 결과(여기서는 끝내기 확인)가 사라진다
  assert.match(modal.textContent, /열린 탭 1개를 닫고 끝낼까요\?/);
  assert.ok(!modal.textContent.includes(PLAINTEXT_WARNING));
  page.button(modal, '취소').click();
  await settle();
  assert.equal(modal.hidden, false);
  assert.ok(modal.textContent.includes(PLAINTEXT_WARNING), modal.textContent);
  assert.match(modal.textContent, /추가 1/);
});

test('an excel apply that fails behind another dialog says so once that dialog closes', async () => {
  const applying = deferred();
  const { api } = fakeApi({
    preview_excel: async () => ({ ok: true, preview_id: 'p'.repeat(32), rows: [], counts: { new: 1, existing: 0, error: 0 } }),
    apply_excel: () => applying.promise,
  });
  const page = startApp(api);
  await settle();
  const modal = page.$('#modal');
  page.$('#excel').click();
  await settle();
  page.button(modal, '파일 고르기').click();
  await settle();
  page.button(modal, '등록').click();
  await settle();
  page.window.hq.onCloseRequested(1);
  applying.resolve({ ok: false, error: '등록하지 못했습니다: 디스크 공간이 부족합니다' });
  await settle();
  assert.match(modal.textContent, /열린 탭 1개를 닫고 끝낼까요\?/);
  page.button(modal, '취소').click();
  await settle();
  assert.match(modal.textContent, /디스크 공간이 부족합니다/);
});

test('saving a password from the info panel redraws the panel', async () => {
  // 정보 칸에서 [비밀번호 변경]으로 저장해도 칸이 "비밀번호: 없음"으로 남았다(최종 검토 M-4)
  const { api } = fakeApi();
  const page = startApp(api);
  await settle();
  page.$('#server-list').querySelector('.srv').click();
  const info = page.$('#info');
  assert.equal(info.hidden, false);
  assert.match(info.textContent, /비밀번호없음/);
  page.button(info, '비밀번호 변경').click();
  const modal = page.$('#modal');
  modal.querySelector('#f-pw').value = 'new-pw';
  page.button(modal, '저장').click();
  await settle();
  assert.equal(modal.hidden, true);
  assert.equal(info.hidden, false);
  assert.match(info.textContent, /비밀번호저장됨 \(다시 볼 수 없음\)/);
});

test('reloading a list without the shown server closes the info panel', async () => {
  const { api, servers } = fakeApi();
  const page = startApp(api);
  await settle();
  page.$('#server-list').querySelector('.srv').click();
  const info = page.$('#info');
  assert.equal(info.hidden, false);
  servers.length = 0; // 다른 곳(엑셀 등록 실패 뒤 다시 읽기 등)에서 서버가 사라졌다
  page.button(info, '비밀번호 변경').click();
  const modal = page.$('#modal');
  modal.querySelector('#f-pw').value = 'new-pw';
  api.set_password = async () => ({ ok: true });
  page.button(modal, '저장').click();
  await settle();
  assert.equal(info.hidden, true);
});
