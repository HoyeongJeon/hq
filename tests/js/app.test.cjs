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

test('drops anywhere on the window never navigate away from the hq page', () => {
  // 끌어다 놓은 파일이나 링크로 WebView2가 다른 페이지로 넘어가면 탭이 사라지고 세션은 숨은 채 남는다(spec 8.2)
  const window = loadApp();
  for (const type of ['dragover', 'drop']) {
    const event = new Event(type, { cancelable: true });
    window.dispatchEvent(event);
    assert.equal(event.defaultPrevented, true, type);
  }
});
