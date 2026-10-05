/**
 * =====================================================
 * pwa-sync.js — منطق المزامنة والعمل Offline
 * =====================================================
 * يُضاف في: static/pwa-sync.js
 * يُحمَّل في base.html بعد pwa-db.js
 *
 * المسؤوليات:
 *   1. تسجيل Service Worker
 *   2. مراقبة حالة الاتصال (Online/Offline)
 *   3. اعتراض نماذج HTML وحفظها Offline
 *   4. مزامنة البيانات عند عودة الاتصال
 *   5. عرض مؤشر الحالة للمستخدم
 * =====================================================
 */

const QuranSync = (() => {

  // ─── إعدادات ──────────────────────────────────────────────────────────────
  const CONFIG = {
    SW_PATH         : '/static/service-worker.js',
    SW_SCOPE        : '/',
    SYNC_INTERVAL_MS: 30_000,   // كل 30 ثانية نتحقق من المزامنة
    DATA_REFRESH_MS : 300_000,  // كل 5 دقائق نحدّث البيانات المخزنة
    API: {
      reports     : '/api/sync/reports',
      plans       : '/api/sync/plans',
      evaluations : '/api/sync/evaluations',
      studentData : '/api/student/dashboard'
    }
  };

  // ─── حالة داخلية ──────────────────────────────────────────────────────────
  let _isOnline        = navigator.onLine;
  let _syncInterval    = null;
  let _swRegistration  = null;
  let _isSyncing       = false;

  // ─── 1. التهيئة الرئيسية ─────────────────────────────────────────────────
  async function init() {
    // إزالة بيانات دخول قديمة خُزنت في المتصفح؛ الاعتماد على ملفات تعريف الارتباط الآمنة فقط.
    await QuranDB.clearSession().catch(() => {});
    await registerServiceWorker();
    setupNetworkListeners();
    setupMessageListener();
    interceptForms();
    updateStatusBar();
    scheduleAutoSync();
    scheduleDataRefresh();
    await showPendingBadge();
    // جلب البيانات للطالب فقط؛ لا تحاول إعادة الدخول بعد تسجيل الخروج.
    if (_isOnline && window.location.pathname === '/student') {
      await fetchAndCacheStudentData();
    }
    console.log('[QuranSync] جاهز');
  }

  // ─── 2. تسجيل Service Worker ──────────────────────────────────────────────
  async function registerServiceWorker() {
    if (!('serviceWorker' in navigator)) {
      console.warn('[QuranSync] المتصفح لا يدعم Service Worker');
      return;
    }

    try {
      _swRegistration = await navigator.serviceWorker.register(
        CONFIG.SW_PATH,
        { scope: CONFIG.SW_SCOPE, updateViaCache: 'none' }
      );

      console.log('[QuranSync] Service Worker مسجّل:', _swRegistration.scope);

      // التحقق من وجود تحديث
      _swRegistration.addEventListener('updatefound', () => {
        const newSW = _swRegistration.installing;
        newSW.addEventListener('statechange', () => {
          if (newSW.state === 'installed' && navigator.serviceWorker.controller) {
            showUpdateBanner();
          }
        });
      });

    } catch (err) {
      console.error('[QuranSync] فشل تسجيل Service Worker:', err);
    }
  }

  // ─── 3. مراقبة حالة الاتصال ──────────────────────────────────────────────
  function setupNetworkListeners() {
    window.addEventListener('online', async () => {
      _isOnline = true;
      updateStatusBar();
      showToast('✅ عاد الاتصال بالإنترنت — جارٍ المزامنة...', 'success');
      await triggerSync();
    });

    window.addEventListener('offline', () => {
      _isOnline = false;
      updateStatusBar();
      showToast('📵 انقطع الإنترنت — سيتم حفظ بياناتك محلياً', 'warning');
    });
  }

  // ─── 4. استقبال رسائل Service Worker ─────────────────────────────────────
  function setupMessageListener() {
    navigator.serviceWorker.addEventListener('message', async event => {
      const { type, storeKey, apiEndpoint, url, body } = event.data || {};

      if (type === 'TRIGGER_SYNC') {
        // SW طلب منّا المزامنة
        await syncStore(storeKey, apiEndpoint);
      }

      if (type === 'SAVE_OFFLINE') {
        // SW التقط طلب POST وطلب منّا حفظه
        await savePostOffline(url, body);
      }
    });
  }

  // ─── 5. جلب وتخزين بيانات الطالب (للعرض Offline) ─────────────────────────
  async function fetchAndCacheStudentData() {
    if (!_isOnline || window.location.pathname !== '/student') return false;
    try {
      const resp = await fetch(CONFIG.API.studentData, {
        headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' }
      });
      if (!resp.ok) return false;
      const result = await resp.json();
      if (result.success) {
        await QuranDB.cacheStudentData(result);
        // تخزين كل خطة على حدة
        if (result.plans) {
          for (const plan of result.plans) {
            await QuranDB.cachePlanDetails(plan.id, plan);
          }
        }
        return true;
      }
      return false;
    } catch {
      return false;
    }
  }

  // ─── اعتراض نماذج HTML ────────────────────────────────────────────────────
  /**
   * نعترض كل نماذج POST في الصفحة.
   * إذا كان المستخدم Offline نحفظ البيانات محلياً بدلاً من إرسالها.
   */
  function interceptForms() {
    document.addEventListener('submit', async e => {
      const form = e.target;
      if (form.tagName !== 'FORM' || form.method?.toLowerCase() !== 'post') return;

      // إذا كان متصلاً → إرسال طبيعي
      if (_isOnline) return;

      // إذا كان Offline → نعترض
      e.preventDefault();
      const action = form.action || window.location.href;
      const url    = new URL(action);
      const data   = Object.fromEntries(new FormData(form));

      const saved = await savePostOffline(url.pathname, data);

      // إن تعذّر تخزين النموذج (نوع غير مدعوم محليًا) نُظهر تحذيرًا واضحًا
      // ونُبقي المستخدم في الصفحة، بدل إيهامه بالنجاح ثم ضياع البيانات صامتًا.
      if (!saved) {
        showToast('⚠️ تعذّر حفظ هذا النموذج دون اتصال. أعد المحاولة عند توفر الشبكة.', 'warning');
        return;
      }

      showToast('💾 تم حفظ البيانات محلياً — ستُرسل عند عودة الاتصال', 'info');
      showPendingBadge();

      // في صفحات التقارير، إعادة التوجيه للصفحة السابقة
      if (window.history.length > 1) {
        setTimeout(() => window.history.back(), 1500);
      }
    });
  }

  // ─── 10. تحديث بيانات الطالب بشكل دوري ───────────────────────────────────
  function scheduleDataRefresh() {
    setInterval(async () => {
      if (_isOnline) {
        await fetchAndCacheStudentData();
      }
    }, CONFIG.DATA_REFRESH_MS);
  }

  // ─── 11. حفظ طلب POST في IndexedDB ────────────────────────────────────────
  async function savePostOffline(urlPath, data) {
    const storeName = detectStore(urlPath);
    if (!storeName) {
      console.warn('[QuranSync] لا يوجد مخزن مناسب لـ:', urlPath);
      return null;
    }

    try {
      const record = await QuranDB.add(storeName, {
        url    : urlPath,
        data   : typeof data === 'string' ? parseFormBody(data) : data,
        pageUrl: window.location.href
      });
      console.log('[QuranSync] حُفظ محلياً:', storeName, record.sync_id);
      return record;
    } catch (err) {
      console.error('[QuranSync] تعذّر الحفظ المحلي:', err);
      return null;
    }
  }

  // ─── 12. تحديد المخزن المناسب بناءً على URL ──────────────────────────────
  function detectStore(urlPath) {
    if (urlPath.includes('add_report') || urlPath.includes('daily_report') || urlPath.includes('report')) {
      return QuranDB.STORES.REPORTS;
    }
    if (urlPath.includes('add_plan') || urlPath.includes('edit_plan')) {
      return QuranDB.STORES.PLANS;
    }
    if (urlPath.includes('evaluate') || urlPath.includes('evaluation')) {
      return QuranDB.STORES.EVALUATIONS;
    }
    return null;
  }

  // ─── 13. تشغيل المزامنة الكاملة ──────────────────────────────────────────
  async function triggerSync() {
    if (_isSyncing || !_isOnline) return;
    _isSyncing = true;

    try {
      // محاولة Background Sync أولاً (إن دعمه المتصفح)
      if (_swRegistration?.sync) {
        await Promise.all([
          _swRegistration.sync.register('sync-daily-reports').catch(() => {}),
          _swRegistration.sync.register('sync-plans').catch(() => {}),
          _swRegistration.sync.register('sync-evaluations').catch(() => {})
        ]);
      }

      // مزامنة مباشرة من الصفحة
      await Promise.all([
        syncStore(QuranDB.STORES.REPORTS,     CONFIG.API.reports),
        syncStore(QuranDB.STORES.PLANS,       CONFIG.API.plans),
        syncStore(QuranDB.STORES.EVALUATIONS, CONFIG.API.evaluations)
      ]);

      await showPendingBadge();

    } catch (err) {
      console.error('[QuranSync] خطأ في المزامنة:', err);
    } finally {
      _isSyncing = false;
    }
  }

  // ─── 14. مزامنة مخزن واحد ────────────────────────────────────────────────
  async function syncStore(storeName, apiEndpoint) {
    let pending;
    try {
      pending = await QuranDB.getPending(storeName);
    } catch { return; }

    if (pending.length === 0) return;
    console.log(`[QuranSync] مزامنة ${pending.length} عناصر من ${storeName}`);

    let syncedCount = 0;

    for (const item of pending) {
      try {
        await QuranDB.updateStatus(storeName, item.id, 'syncing');

        const response = await fetch(apiEndpoint, {
          method  : 'POST',
          headers : {
            'Content-Type'  : 'application/json',
            'X-Sync-Request': 'true',
            'X-Sync-Id'     : item.sync_id
          },
          body: JSON.stringify({
            sync_id   : item.sync_id,
            url       : item.url,
            data      : item.data,
            timestamp : item.timestamp
          })
        });

        if (response.ok) {
          const serverData = await response.json().catch(() => ({}));
          await QuranDB.updateStatus(storeName, item.id, 'synced', serverData);
          syncedCount++;
          console.log('[QuranSync] ✅ تمت مزامنة:', item.sync_id);
        } else {
          await QuranDB.updateStatus(storeName, item.id, 'pending');
          await QuranDB.incrementRetry(storeName, item.id);
          console.warn('[QuranSync] ❌ فشل:', item.sync_id, response.status);
        }

      } catch (err) {
        await QuranDB.updateStatus(storeName, item.id, 'pending').catch(() => {});
        await QuranDB.incrementRetry(storeName, item.id).catch(() => {});
        console.error('[QuranSync] خطأ في مزامنة:', item.sync_id, err);
      }
    }

    if (syncedCount > 0) {
      showToast(`✅ تمت مزامنة ${syncedCount} عنصر بنجاح`, 'success');
      // تنظيف المتزامنة بعد 5 دقائق
      setTimeout(() => QuranDB.clearSynced(storeName).catch(() => {}), 5 * 60_000);
    }
  }

  // ─── 15. المزامنة التلقائية الدورية ──────────────────────────────────────
  function scheduleAutoSync() {
    _syncInterval = setInterval(async () => {
      if (_isOnline) {
        const counts = await QuranDB.countPending().catch(() => ({ total: 0 }));
        if (counts.total > 0) {
          console.log('[QuranSync] مزامنة تلقائية — معلق:', counts.total);
          await triggerSync();
        }
      }
    }, CONFIG.SYNC_INTERVAL_MS);
  }

  // ─── 16. شريط حالة الاتصال ───────────────────────────────────────────────
  function updateStatusBar() {
    let bar = document.getElementById('pwa-status-bar');

    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'pwa-status-bar';
      bar.style.cssText = `
        position: fixed; bottom: 0; left: 0; right: 0;
        z-index: 99999; padding: .45rem 1rem;
        font-family: 'Tajawal','Cairo',sans-serif;
        font-size: .88rem; font-weight: 700;
        text-align: center; direction: rtl;
        transition: all .3s ease;
        display: flex; align-items: center; justify-content: center; gap: .5rem;
      `;
      document.body.appendChild(bar);
    }

    if (_isOnline) {
      bar.style.cssText += 'background:#d4edda; color:#155724; border-top:2px solid #c3e6cb;';
      bar.innerHTML = '<span>🟢</span><span>متصل بالإنترنت</span>';
      // إخفاء الشريط بعد 4 ثوانٍ عند الاتصال
      setTimeout(() => { bar.style.opacity = '0'; }, 4000);
      setTimeout(() => { bar.style.display = 'none'; }, 4400);
    } else {
      bar.style.display = 'flex';
      bar.style.opacity = '1';
      bar.style.cssText += 'background:#fff3cd; color:#856404; border-top:2px solid #ffeeba;';
      bar.innerHTML = `
        <span>🔴</span>
        <span>غير متصل — البيانات تُحفظ محلياً</span>
        <span id="pwa-pending-count" style="background:#856404;color:white;border-radius:1rem;padding:.1rem .5rem;font-size:.78rem;"></span>
      `;
    }
  }

  // ─── 17. شارة عدد السجلات المعلّقة ──────────────────────────────────────
  async function showPendingBadge() {
    const counts = await QuranDB.countPending().catch(() => ({ total: 0 }));

    // شارة في شريط الحالة
    const countEl = document.getElementById('pwa-pending-count');
    if (countEl) {
      countEl.textContent = counts.total > 0 ? `${counts.total} معلّق` : '';
    }

    // شارة في عنصر التنقل (إن وُجد)
    const badge = document.getElementById('pwa-sync-badge');
    if (badge) {
      badge.textContent   = counts.total > 0 ? counts.total : '';
      badge.style.display = counts.total > 0 ? 'inline-flex' : 'none';
    }

    return counts;
  }

  // ─── 18. رسائل Toast ──────────────────────────────────────────────────────
  function showToast(message, type = 'info') {
    // استخدام نظام Toast الموجود في base.html إن وُجد
    const wrap = document.querySelector('.toast-wrap');
    if (wrap) {
      const colorMap = {
        success: 't-success',
        error  : 't-error',
        warning: 't-warning',
        info   : 't-info'
      };
      const iconMap = {
        success: 'check-circle',
        error  : 'times-circle',
        warning: 'exclamation-triangle',
        info   : 'info-circle'
      };
      const toast = document.createElement('div');
      toast.className = `toast-item ${colorMap[type] || 't-info'}`;
      toast.innerHTML = `
        <i class="fas fa-${iconMap[type] || 'info-circle'}"></i>
        <span>${message}</span>
      `;
      wrap.appendChild(toast);
      setTimeout(() => {
        toast.classList.add('toast-out');
        setTimeout(() => toast.remove(), 400);
      }, 4000);
      return;
    }

    // fallback: console فقط
    console.log(`[QuranSync Toast] ${type}: ${message}`);
  }

  // ─── 19. لافتة تحديث التطبيق ─────────────────────────────────────────────
  function showUpdateBanner() {
    const banner = document.createElement('div');
    banner.style.cssText = `
      position: fixed; top: 0; left: 0; right: 0; z-index: 99998;
      background: #28a745; color: white;
      padding: .75rem 1rem; text-align: center; direction: rtl;
      font-family: 'Tajawal','Cairo',sans-serif; font-weight: 700;
      display: flex; align-items: center; justify-content: center; gap: 1rem;
    `;
    banner.innerHTML = `
      <span>🆕 يوجد تحديث للتطبيق</span>
      <button onclick="window.location.reload()" style="
        background:white; color:#28a745; border:none;
        padding:.3rem .9rem; border-radius:1rem; cursor:pointer; font-weight:700;
      ">تحديث الآن</button>
      <button onclick="this.parentElement.remove()" style="
        background:transparent; color:white; border:none; cursor:pointer; font-size:1.2rem;
      ">✕</button>
    `;
    document.body.prepend(banner);
  }

  // ─── 20. تحويل نص form-body إلى كائن ─────────────────────────────────────
  function parseFormBody(bodyStr) {
    try {
      return Object.fromEntries(new URLSearchParams(bodyStr));
    } catch {
      return { raw: bodyStr };
    }
  }

  // ─── واجهة عامة ───────────────────────────────────────────────────────────
  return {
    init,
    triggerSync,
    showPendingBadge,
    savePostOffline,
    showToast,
    fetchAndCacheStudentData,
    isOnline : () => _isOnline
  };

})();

// ─── تشغيل تلقائي عند تحميل الصفحة ─────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  QuranSync.init().catch(err => {
    console.error('[QuranSync] خطأ في التهيئة:', err);
  });
});
