/* نافذة تأكيد مخصصة — تظهر في منتصف الشاشة بدلاً من تحذير المتصفح */
(function () {
    if (window.__aqModal) return;
    window.__aqModal = true;

    var css = [
        '#aq_confirm_overlay{position:fixed;inset:0;background:rgba(5,8,18,.6);z-index:99999;display:none;align-items:center;justify-content:center;backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px);}',
        '#aq_confirm_overlay.open{display:flex;}',
        '#aq_confirm_box{background:#1c1f2e;border:1px solid rgba(255,255,255,.12);border-radius:18px;padding:1.6rem 1.8rem;max-width:400px;width:calc(100% - 2rem);box-shadow:0 20px 60px rgba(0,0,0,.55);color:#edeff7;text-align:center;font-family:inherit;direction:rtl;}',
        '#aq_confirm_msg{font-size:.95rem;line-height:1.8;margin-bottom:1.4rem;white-space:pre-line;color:#e7e9f4;}',
        '#aq_confirm_actions{display:flex;gap:.7rem;justify-content:center;flex-wrap:wrap;}',
        '#aq_confirm_box button{border:none;border-radius:11px;padding:.55rem 1.5rem;font-weight:800;font-size:.85rem;cursor:pointer;font-family:inherit;transition:transform .15s,filter .15s;}',
        '#aq_confirm_box button:hover{transform:translateY(-2px);filter:brightness(1.08);}',
        '#aq_ok_btn{background:linear-gradient(135deg,#ff6b6b,#ee5a24);color:#fff;}',
        '#aq_cancel_btn{background:#292e42;color:#c9cee4;border:1px solid rgba(255,255,255,.14);}'
    ].join('');

    var styleEl = document.createElement('style');
    styleEl.textContent = css;
    document.head.appendChild(styleEl);

    var overlay = document.createElement('div');
    overlay.id = 'aq_confirm_overlay';
    overlay.innerHTML =
        '<div id="aq_confirm_box">' +
            '<div id="aq_confirm_msg"></div>' +
            '<div id="aq_confirm_actions">' +
                '<button type="button" id="aq_ok_btn">متابعة</button>' +
                '<button type="button" id="aq_cancel_btn">تراجع</button>' +
            '</div>' +
        '</div>';
    document.body.appendChild(overlay);

    var msgEl = document.getElementById('aq_confirm_msg');
    var okBtn = document.getElementById('aq_ok_btn');
    var cancelBtn = document.getElementById('aq_cancel_btn');
    var pending = null;

    function show(message, onResult, mode) {
        msgEl.textContent = String(message).replace(/\\n/g, '\n');
        okBtn.textContent = (mode === 'alert') ? 'حسناً' : 'متابعة';
        cancelBtn.style.display = (mode === 'alert') ? 'none' : '';
        pending = onResult;
        overlay.classList.add('open');
    }

    function close(result) {
        overlay.classList.remove('open');
        if (pending) { var cb = pending; pending = null; cb(result); }
    }

    okBtn.addEventListener('click', function () { close(true); });
    cancelBtn.addEventListener('click', function () { close(false); });
    overlay.addEventListener('click', function (e) { if (e.target === overlay) close(false); });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') close(false);
    });

    window.askConfirm = function (message) {
        return new Promise(function (resolve) { show(message, resolve, 'confirm'); });
    };
    window.askAlert = function (message) {
        return new Promise(function (resolve) { show(message, resolve, 'alert'); });
    };

    /* تفويض عام: روابط تحمل data-confirm */
    document.addEventListener('click', function (e) {
        var el = e.target && e.target.closest ? e.target.closest('[data-confirm]') : null;
        if (!el) return;
        if (el.tagName === 'FORM') return; /* النماذج تُعالج عند الإرسال */
        e.preventDefault();
        e.stopPropagation();
        var href = el.getAttribute('href') || '';
        var target = el.getAttribute('target');
        askConfirm(el.getAttribute('data-confirm')).then(function (ok) {
            if (!ok) return;
            if (target === '_blank') {
                window.open(href, '_blank');
            } else if (window.__aqGo) {
                // __aqGo يحوّل المسارات المدمِّرة إلى POST مع رمز CSRF.
                window.__aqGo(href);
            } else {
                window.location.href = href;
            }
        });
    }, true);

    /* تفويض عام: نماذج تحمل data-confirm */
    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (!form || !form.getAttribute) return;
        if (form.getAttribute('data-confirm') === null) return;
        if (form.__aqConfirmed) { delete form.__aqConfirmed; return; }
        e.preventDefault();
        askConfirm(form.getAttribute('data-confirm')).then(function (ok) {
            if (!ok) return;
            form.__aqConfirmed = true;
            form.submit();
        });
    }, true);
})();
