/* =====================================================================
 * csrf.js — جسر حماية CSRF لصفحات أشبال القرآن
 * =====================================================================
 *  يُحقن تلقائيًا في كل صفحة HTML من الخادم (انظر inject_csrf_into_pages).
 *  مهامه:
 *    1) إضافة الحقل المخفي csrf_token إلى كل نموذج POST.
 *    2) إضافة الترويسة X-CSRFToken إلى طلبات fetch و XMLHttpRequest.
 *    3) تحويل روابط الحذف/التبديل (المسارات المدمِّرة) إلى طلبات POST.
 *
 *  سبب وجوده: القوالب (39) لا تشترك في base.html، فحقن سكربت واحد من الخادم
 *  يغطّيها كلها دون تعديل كل قالب على حدة.
 * ===================================================================== */
(function () {
    "use strict";
    if (window.__aqCsrfReady) return;
    window.__aqCsrfReady = true;

    /* ── رمز CSRF من الوسم الذي يحقنه الخادم ─────────────────────────── */
    function getToken() {
        var m = document.querySelector('meta[name="csrf-token"]');
        return m ? m.getAttribute("content") : "";
    }

    /* ── المسارات التي أصبحت POST فقط في الخادم ──────────────────────── */
    var POST_ONLY = [
        "/delete_teacher/",
        "/toggle_teacher_report_permission/",
        "/toggle_teacher_add_plan_permission/",
        "/toggle_teacher_add_poem_plan_permission/",
        "/delete_assignment/",
        "/delete_plan/",
        "/admin/toggle_reward_rule/",
        "/admin/delete_reward_rule/",
        "/admin/delete_admin/",
        "/admin/duplicates/delete/"
    ];

    function isPostOnlyUrl(rawUrl) {
        try {
            var u = new URL(rawUrl, window.location.origin);
            if (u.origin !== window.location.origin) return false;
            return POST_ONLY.some(function (p) {
                return u.pathname.indexOf(p) === 0;
            });
        } catch (e) {
            return false;
        }
    }

    /* ── إرسال طلب POST إلى مسار مع الحفاظ على معاملات الاستعلام ─────── */
    function postTo(url) {
        var u = new URL(url, window.location.origin);
        var form = document.createElement("form");
        form.method = "POST";
        form.action = u.pathname + u.search;   // نُبقي ?tab=... في الرابط
        form.style.display = "none";

        var input = document.createElement("input");
        input.type = "hidden";
        input.name = "csrf_token";
        input.value = getToken();
        form.appendChild(input);

        document.body.appendChild(form);
        form.submit();
    }

    /* ── دالة عامة للتنقّل: POST للمسارات المدمِّرة، وإلا انتقال عادي ── */
    window.__aqGo = function (url) {
        if (url && isPostOnlyUrl(url)) {
            postTo(url);
        } else if (url) {
            window.location.href = url;
        }
    };

    /* ── 1) حقن الحقل المخفي في النماذج ─────────────────────────────── */
    function injectToken(form) {
        if (!form || form.method.toLowerCase() !== "post") return;
        if (form.querySelector('input[name="csrf_token"]')) return;
        var t = getToken();
        if (!t) return;
        var input = document.createElement("input");
        input.type = "hidden";
        input.name = "csrf_token";
        input.value = t;
        form.appendChild(input);
    }

    function injectAll(scope) {
        var forms = (scope || document).querySelectorAll("form");
        for (var i = 0; i < forms.length; i++) injectToken(forms[i]);
    }

    /* ── 2) ترقيع fetch و XMLHttpRequest ────────────────────────────── */
    var _fetch = window.fetch;
    if (typeof _fetch === "function") {
        window.fetch = function (input, init) {
            init = init || {};
            var method = (init.method || (typeof input === "object" && input && input.method) || "GET").toUpperCase();
            if (method !== "GET" && method !== "HEAD" && method !== "OPTIONS") {
                try {
                    var target = (typeof input === "string") ? input : (input && input.url) || "";
                    var u = new URL(target, window.location.origin);
                    if (u.origin === window.location.origin) {
                        var headers = new Headers(init.headers || (typeof input === "object" && input && input.headers) || {});
                        if (!headers.has("X-CSRFToken")) headers.set("X-CSRFToken", getToken());
                        init.headers = headers;
                    }
                } catch (e) { /* تجاهل */ }
            }
            return _fetch.call(this, input, init);
        };
    }

    var _open = XMLHttpRequest.prototype.open;
    var _send = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url) {
        this.__aqMethod = (method || "GET").toUpperCase();
        try { this.__aqUrl = new URL(url, window.location.origin); } catch (e) { this.__aqUrl = null; }
        return _open.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function () {
        if (this.__aqMethod !== "GET" && this.__aqMethod !== "HEAD" && this.__aqMethod !== "OPTIONS" &&
            this.__aqUrl && this.__aqUrl.origin === window.location.origin) {
            try { this.setRequestHeader("X-CSRFToken", getToken()); } catch (e) { /* تجاهل */ }
        }
        return _send.apply(this, arguments);
    };

    /* ── 3) تحويل روابط المسارات المدمِّرة إلى POST ───────────────────── */
    document.addEventListener("click", function (e) {
        var a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
        if (!a) return;
        // الروابط التي لها onclick خاص (confirmDelete) يديرها القالب بنفسه.
        if (a.hasAttribute("onclick")) return;
        // الروابط ذات data-confirm يديرها confirm-modal ثم يستدعي __aqGo.
        if (a.hasAttribute("data-confirm")) return;
        var href = a.getAttribute("href");
        if (!href || href.charAt(0) === "#") return;
        if (!isPostOnlyUrl(href)) return;
        e.preventDefault();
        e.stopPropagation();
        window.__aqGo(a.href);
    }, true);

    /* ── التهيئة ────────────────────────────────────────────────────── */
    function boot() {
        injectAll(document);
        // حقن أي نموذج يُضاف لاحقًا ديناميكيًا.
        if (window.MutationObserver) {
            new MutationObserver(function (mutations) {
                for (var i = 0; i < mutations.length; i++) {
                    var nodes = mutations[i].addedNodes;
                    for (var j = 0; j < nodes.length; j++) {
                        var n = nodes[j];
                        if (n.nodeType === 1) {
                            if (n.tagName === "FORM") injectToken(n);
                            else if (n.querySelectorAll) injectAll(n);
                        }
                    }
                }
            }).observe(document.documentElement, { childList: true, subtree: true });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }

    // شبكة أمان: أي إرسال نموذج (حتى لو لم يُحقن بعد) نضمن وجود الرمز.
    document.addEventListener("submit", function (e) {
        injectToken(e.target);
    }, true);
})();
