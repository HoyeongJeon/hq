const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP_JS = fs.readFileSync(path.join(__dirname, '../../src/hq/static/js/app.js'), 'utf8');

// app.js를 브라우저 대신 빈 window·document(EventTarget)에서 읽는다. pywebview가 아직 없으므로 화면은 시작하지 않는다
function loadApp() {
  const window = new EventTarget();
  const document = new EventTarget();
  vm.runInNewContext(APP_JS, { window, document });
  return window;
}

test('the ID hint shows how to write a local and a domain account', () => {
  // 도메인에 가입된 서버에서 접두 없는 계정은 서버의 로컬 계정으로 확인되어 거부된다(체크포인트 D)
  const literal = APP_JS.match(/const USERNAME_HINT = ('(?:[^'\\]|\\.)*');/);
  assert.ok(literal, 'USERNAME_HINT를 찾지 못했다');
  const hint = vm.runInNewContext(literal[1]);
  for (const part of ['로컬 계정', 'Administrator', '도메인 계정', '도메인\\계정', 'CONTOSO\\opsadmin']) {
    assert.ok(hint.includes(part), `${part} / ${hint}`);
  }
});

test('drops anywhere on the window never navigate away from the hq page', () => {
  // 끌어다 놓은 파일이나 링크로 WebView2가 다른 페이지로 넘어가면 탭이 사라지고 세션은 숨은 채 남는다(spec 8.2)
  const window = loadApp();
  for (const type of ['dragover', 'drop']) {
    const event = new Event(type, { cancelable: true });
    window.dispatchEvent(event);
    assert.equal(event.defaultPrevented, true, type);
  }
});
