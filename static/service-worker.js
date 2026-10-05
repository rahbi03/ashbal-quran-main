/**
 * =====================================================
 * service-worker.js — أشبال القرآن PWA
 * يدعم: Offline كامل + Background Sync + Cache Strategy
 * =====================================================
 */

const SW_VERSION = 'v5';
const CACHE_STATIC  = `quran-static-${SW_VERSION}`;   // أصول لا تتغير
const CACHE_PAGES   = `quran-pages-${SW_VERSION}`;    // صفحات HTML
const CACHE_API     = `quran-api-${SW_VERSION}`;      // استجابات API
const SYNC_TAG_REPORTS = 'sync-daily-reports';
const SYNC_TAG_PLANS   = 'sync-plans';
const SYNC_TAG_EVALS   = 'sync-evaluations';

// ─── الأصول الثابتة التي تُخزَّن فور التثبيت ───────────────────────────────
const STATIC_ASSETS = [
  '/static/manifest.json',
  '/static/images/logo.png',
  '/static/pwa-db.js',
  '/static/pwa-sync.js',
  // Bootstrap RTL من CDN
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.rtl.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js',
  // Font Awesome
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css',
  // الخطوط
  'https://fonts.googleapis.com/css2?family=Cairo:wght@400;500;600;700&family=Tajawal:wght@400;500;700&display=swap'
];

// ─── الصفحات التي تُخزَّن للعمل Offline ─────────────────────────────────────
const OFFLINE_PAGES = [];

// ─── صفحة الـ Offline الاحتياطية ─────────────────────────────────────────────
const OFFLINE_FALLBACK = '/offline';

// =============================================================================
// 1. التثبيت — تخزين الأصول الثابتة
// =============================================================================
self.addEventListener('install', event => {
  console.log('[SW] تثبيت الإصدار:', SW_VERSION);
  event.waitUntil(
    Promise.all([
      // تخزين الأصول الثابتة
      caches.open(CACHE_STATIC).then(cache => {
        return cache.addAll(STATIC_ASSETS).catch(err => {
          console.warn('[SW] تعذّر تخزين بعض الأصول الثابتة:', err);
        });
      }),
      // تخزين الصفحات الأساسية
      caches.open(CACHE_PAGES).then(cache => {
        return cache.addAll(OFFLINE_PAGES).catch(err => {
          console.warn('[SW] تعذّر تخزين بعض الصفحات:', err);
        });
      })
    ]).then(() => {
      console.log('[SW] اكتمل التثبيت');
      // تفعيل فوري دون انتظار إغلاق التبويبات القديمة
      return self.skipWaiting();
    })
  );
});

// =============================================================================
// 2. التفعيل — حذف الكاشات القديمة
// =============================================================================
self.addEventListener('activate', event => {
  console.log('[SW] تفعيل الإصدار:', SW_VERSION);
  const validCaches = [CACHE_STATIC, CACHE_PAGES, CACHE_API];

  event.waitUntil(
    caches.keys().then(keys => {
      return Promise.all(
        keys
          .filter(key => !validCaches.includes(key))
          .map(key => {
            console.log('[SW] حذف كاش قديم:', key);
            return caches.delete(key);
          })
      );
    }).then(() => {
      console.log('[SW] السيطرة على جميع العملاء');
      return self.clients.claim();
    })
  );
});

// =============================================================================
// 3. اعتراض الطلبات — استراتيجيات التخزين المؤقت
// =============================================================================
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // تجاهل الطلبات غير HTTP/HTTPS
  if (!request.url.startsWith('http')) return;

  // تجاهل طلبات Chrome DevTools
  if (url.pathname.startsWith('/chrome-extension')) return;

  // ── استراتيجية حسب نوع الطلب ───────────────────────────────────────────────

  // 1) طلبات API (POST) → Network Only مع حفظ في قائمة الانتظار عند Offline
  if (request.method === 'POST') {
    event.respondWith(handlePostRequest(request));
    return;
  }

  // 2) الأصول الثابتة (JS, CSS, صور) → Cache First
  if (isStaticAsset(url)) {
    event.respondWith(cacheFirst(request, CACHE_STATIC));
    return;
  }

  // 3) واجهات البيانات الشخصية لا تُخزّن بين المستخدمين أو بعد تسجيل الخروج
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(request).catch(() =>
      new Response(JSON.stringify({ error: 'offline' }), {
        status: 503, headers: { 'Content-Type': 'application/json' }
      })
    ));
    return;
  }

  // 4) صفحات HTML → Network First مع Cache احتياطي + Offline fallback
  if (request.headers.get('accept')?.includes('text/html')) {
    event.respondWith(handlePageRequest(request));
    return;
  }

  // 5) باقي الطلبات → Network مع Cache احتياطي
  event.respondWith(networkFirst(request, CACHE_STATIC, 3000));
});

// =============================================================================
// 4. Background Sync — إرسال البيانات المعلّقة عند عودة الاتصال
// =============================================================================
self.addEventListener('sync', event => {
  console.log('[SW] Background Sync:', event.tag);

  if (event.tag === SYNC_TAG_REPORTS) {
    event.waitUntil(syncPendingData('pending_reports', '/api/sync/reports'));
  }
  if (event.tag === SYNC_TAG_PLANS) {
    event.waitUntil(syncPendingData('pending_plans', '/api/sync/plans'));
  }
  if (event.tag === SYNC_TAG_EVALS) {
    event.waitUntil(syncPendingData('pending_evaluations', '/api/sync/evaluations'));
  }
});

// =============================================================================
// 5. Push Notifications (اختياري — للإشعارات المستقبلية)
// =============================================================================
self.addEventListener('push', event => {
  if (!event.data) return;
  const data = event.data.json();
  event.waitUntil(
    self.registration.showNotification(data.title || 'أشبال القرآن', {
      body: data.body || '',
      icon: '/static/images/logo.png',
      badge: '/static/images/logo.png',
      dir: 'rtl',
      lang: 'ar',
      tag: data.tag || 'quran-notification',
      data: data.url ? { url: data.url } : {}
    })
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  if (event.notification.data?.url) {
    event.waitUntil(clients.openWindow(event.notification.data.url));
  }
});

// =============================================================================
// دوال مساعدة
// =============================================================================

/**
 * Cache First: ابحث في الكاش أولاً، إن لم يوجد اجلب من الشبكة واحفظ
 */
async function cacheFirst(request, cacheName) {
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(cacheName);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    return new Response('المورد غير متاح offline', { status: 503 });
  }
}

/**
 * Network First: اجلب من الشبكة مع timeout، عند الفشل ارجع للكاش
 */
async function networkFirst(request, cacheName, timeoutMs = 5000) {
  const cache = await caches.open(cacheName);

  try {
    const networkPromise = fetch(request.clone());
    const timeoutPromise = new Promise((_, reject) =>
      setTimeout(() => reject(new Error('timeout')), timeoutMs)
    );

    const response = await Promise.race([networkPromise, timeoutPromise]);

    if (response.ok) {
      cache.put(request, response.clone()); // تحديث الكاش في الخلفية
    }
    return response;
  } catch {
    const cached = await cache.match(request);
    if (cached) {
      console.log('[SW] Offline — إرجاع من الكاش:', request.url);
      return cached;
    }
    return new Response(
      JSON.stringify({ error: 'offline', message: 'لا يوجد اتصال بالإنترنت' }),
      { status: 503, headers: { 'Content-Type': 'application/json' } }
    );
  }
}

/**
 * معالجة طلبات الصفحات مع صفحة Offline احتياطية
 */
async function handlePageRequest(request) {
  try {
    return await fetch(request);
  } catch {
    return new Response(getOfflineHTML(), {
      headers: { 'Content-Type': 'text/html; charset=utf-8' }
    });
  }
}

/**
 * معالجة طلبات POST — حفظ في IndexedDB عند Offline
 */
async function handlePostRequest(request) {
  try {
    const response = await fetch(request.clone());
    return response;
  } catch {
    // الشبكة غير متاحة — حفظ الطلب للمزامنة لاحقاً
    const url = new URL(request.url);
    const body = await request.clone().text();

    // إرسال رسالة للصفحة لحفظ البيانات في IndexedDB
    const clients = await self.clients.matchAll({ type: 'window' });
    clients.forEach(client => {
      client.postMessage({
        type: 'SAVE_OFFLINE',
        url: url.pathname,
        body: body,
        timestamp: Date.now()
      });
    });

    // إجدولة مزامنة
    if (url.pathname.includes('add_report') || url.pathname.includes('daily_report')) {
      await self.registration.sync.register(SYNC_TAG_REPORTS).catch(() => {});
    } else if (url.pathname.includes('add_plan') || url.pathname.includes('plan')) {
      await self.registration.sync.register(SYNC_TAG_PLANS).catch(() => {});
    } else if (url.pathname.includes('evaluate')) {
      await self.registration.sync.register(SYNC_TAG_EVALS).catch(() => {});
    }

    // رد وهمي بالنجاح حتى لا تظهر رسالة خطأ للمستخدم
    return new Response(
      JSON.stringify({ success: true, offline: true, message: 'تم الحفظ محلياً وسيُزامَن عند عودة الاتصال' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    );
  }
}

/**
 * مزامنة البيانات المعلّقة مع السيرفر
 */
async function syncPendingData(storeKey, apiEndpoint) {
  // نرسل رسالة للصفحة لتنفيذ المزامنة من خلال pwa-sync.js
  const allClients = await self.clients.matchAll({ type: 'window' });

  if (allClients.length > 0) {
    // الصفحة مفتوحة — اطلب منها المزامنة
    allClients.forEach(client => {
      client.postMessage({
        type: 'TRIGGER_SYNC',
        storeKey,
        apiEndpoint
      });
    });
  } else {
    // الصفحة مغلقة — نزامن مباشرة من Service Worker
    try {
      const db = await openSyncDB();
      const pending = await getAllFromStore(db, storeKey);

      for (const item of pending) {
        try {
          const response = await fetch(apiEndpoint, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-Sync-Request': 'true'
            },
            body: JSON.stringify(item.data)
          });

          if (response.ok) {
            await deleteFromStore(db, storeKey, item.id);
            console.log('[SW] تمت مزامنة:', item.id);
          }
        } catch (err) {
          console.error('[SW] فشل مزامنة العنصر:', item.id, err);
        }
      }
    } catch (err) {
      console.error('[SW] فشل فتح قاعدة البيانات:', err);
      throw err; // إعادة الرمي لإعادة المحاولة لاحقاً
    }
  }
}

/**
 * فتح قاعدة بيانات المزامنة مباشرة من SW
 */
function openSyncDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('QuranTrackingSync', 1);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
    req.onupgradeneeded = e => {
      const db = e.target.result;
      ['pending_reports', 'pending_plans', 'pending_evaluations'].forEach(name => {
        if (!db.objectStoreNames.contains(name)) {
          db.createObjectStore(name, { keyPath: 'id', autoIncrement: true });
        }
      });
    };
  });
}

function getAllFromStore(db, storeName) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, 'readonly');
    const req = tx.objectStore(storeName).getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error);
  });
}

function deleteFromStore(db, storeName, id) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, 'readwrite');
    const req = tx.objectStore(storeName).delete(id);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}

/**
 * هل هذا أصل ثابت (JS/CSS/صورة/خط)؟
 */
function isStaticAsset(url) {
  const staticExtensions = ['.js', '.css', '.png', '.jpg', '.jpeg', '.svg', '.ico', '.woff', '.woff2', '.ttf'];
  return (
    staticExtensions.some(ext => url.pathname.endsWith(ext)) ||
    url.hostname.includes('cdn.jsdelivr.net') ||
    url.hostname.includes('cdnjs.cloudflare.com') ||
    url.hostname.includes('fonts.googleapis.com') ||
    url.hostname.includes('fonts.gstatic.com')
  );
}

/**
 * صفحة HTML احتياطية عند انقطاع الإنترنت
 */
function getOfflineHTML() {
  return `<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>أشبال القرآن — غير متصل</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Cairo', 'Tajawal', sans-serif;
      background: linear-gradient(135deg, #f5f7fa, #c3cfe2);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 1rem;
    }
    .card {
      background: white;
      border-radius: 1.5rem;
      padding: 2.5rem 2rem;
      text-align: center;
      max-width: 360px;
      width: 100%;
      box-shadow: 0 10px 40px rgba(0,0,0,0.12);
    }
    .icon { font-size: 4rem; margin-bottom: 1rem; }
    h1 { font-size: 1.4rem; color: #1a3a2a; margin-bottom: .5rem; }
    p  { color: #666; font-size: .95rem; line-height: 1.7; margin-bottom: 1.5rem; }
    .btn {
      background: #28a745;
      color: white;
      border: none;
      padding: .75rem 2rem;
      border-radius: 2rem;
      font-size: 1rem;
      cursor: pointer;
      width: 100%;
    }
    .pending-info {
      margin-top: 1rem;
      padding: .75rem;
      background: #fff8e1;
      border-radius: .75rem;
      font-size: .85rem;
      color: #7a5000;
      display: none;
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">📵</div>
    <h1>لا يوجد اتصال بالإنترنت</h1>
    <p>
      لا تقلق! أي بيانات أدخلتها ستُحفظ تلقائياً<br>
      وتُزامَن فور عودة الاتصال.
    </p>
    <button class="btn" onclick="window.location.reload()">
      🔄 إعادة المحاولة
    </button>
    <div class="pending-info" id="pendingInfo">
      ⏳ يوجد بيانات محلية في انتظار المزامنة
    </div>
  </div>
  <script>
    // فحص وجود بيانات معلّقة
    if ('indexedDB' in window) {
      const req = indexedDB.open('QuranTrackingSync', 1);
      req.onsuccess = function() {
        const db = req.result;
        const stores = ['pending_reports', 'pending_plans', 'pending_evaluations'];
        let total = 0;
        let checked = 0;
        stores.forEach(s => {
          if (db.objectStoreNames.contains(s)) {
            const tx = db.transaction(s, 'readonly');
            const r = tx.objectStore(s).count();
            r.onsuccess = function() {
              total += r.result;
              checked++;
              if (checked === stores.length && total > 0) {
                document.getElementById('pendingInfo').style.display = 'block';
              }
            };
          } else { checked++; }
        });
      };
    }
  </script>
</body>
</html>`;
}
