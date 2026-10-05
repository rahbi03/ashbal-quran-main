/**
 * =====================================================
 * pwa-db.js — إدارة قاعدة بيانات IndexedDB المحلية
 * =====================================================
 * يُضاف هذا الملف في: static/pwa-db.js
 * يُستدعى قبل pwa-sync.js في base.html
 */

const QuranDB = (() => {
  const DB_NAME    = 'QuranTrackingSync';
  const DB_VERSION = 1;

  // أسماء مخازن البيانات
  const STORES = {
    REPORTS      : 'pending_reports',
    PLANS        : 'pending_plans',
    EVALUATIONS  : 'pending_evaluations',
    CACHE_DATA   : 'cached_data'       // بيانات مخزنة للعرض Offline
  };

  let _db = null;

  // ─── فتح / إنشاء قاعدة البيانات ──────────────────────────────────────────
  function open() {
    if (_db) return Promise.resolve(_db);

    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);

      req.onupgradeneeded = e => {
        const db = e.target.result;

        // مخازن البيانات المعلّقة للمزامنة
        Object.values(STORES).forEach(storeName => {
          if (!db.objectStoreNames.contains(storeName)) {
            const store = db.createObjectStore(storeName, {
              keyPath: 'id',
              autoIncrement: true
            });
            // فهرس للبحث السريع بالـ sync_id
            if (storeName !== STORES.CACHE_DATA) {
              store.createIndex('sync_id', 'sync_id', { unique: false });
              store.createIndex('timestamp', 'timestamp', { unique: false });
              store.createIndex('status', 'status', { unique: false });
            } else {
              store.createIndex('key', 'key', { unique: true });
            }
          }
        });
      };

      req.onsuccess = e => {
        _db = e.target.result;

        // إعادة الفتح عند الإغلاق المفاجئ
        _db.onversionchange = () => {
          _db.close();
          _db = null;
        };

        resolve(_db);
      };

      req.onerror = () => reject(req.error);
      req.onblocked = () => {
        console.warn('[QuranDB] قاعدة البيانات محجوبة — أغلق تبويبات أخرى');
      };
    });
  }

  // ─── إضافة سجل إلى مخزن ──────────────────────────────────────────────────
  async function add(storeName, data) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, 'readwrite');
      const record = {
        ...data,
        sync_id   : generateUUID(),
        timestamp : Date.now(),
        status    : 'pending',   // pending | syncing | synced | failed
        retries   : 0
      };
      const req = tx.objectStore(storeName).add(record);
      req.onsuccess = () => resolve({ ...record, id: req.result });
      req.onerror   = () => reject(req.error);
    });
  }

  // ─── جلب جميع السجلات المعلّقة ───────────────────────────────────────────
  async function getPending(storeName) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, 'readonly');
      const index = tx.objectStore(storeName).index('status');
      const req = index.getAll('pending');
      req.onsuccess = () => resolve(req.result || []);
      req.onerror   = () => reject(req.error);
    });
  }

  // ─── جلب جميع السجلات ────────────────────────────────────────────────────
  async function getAll(storeName) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, 'readonly');
      const req = tx.objectStore(storeName).getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror   = () => reject(req.error);
    });
  }

  // ─── تحديث حالة سجل ──────────────────────────────────────────────────────
  async function updateStatus(storeName, id, status, serverData = null) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, 'readwrite');
      const store = tx.objectStore(storeName);
      const getReq = store.get(id);
      getReq.onsuccess = () => {
        const record = getReq.result;
        if (!record) { resolve(null); return; }
        record.status     = status;
        record.syncedAt   = status === 'synced' ? Date.now() : null;
        record.serverData = serverData;
        const putReq = store.put(record);
        putReq.onsuccess = () => resolve(record);
        putReq.onerror   = () => reject(putReq.error);
      };
      getReq.onerror = () => reject(getReq.error);
    });
  }

  // ─── زيادة عداد إعادة المحاولات ──────────────────────────────────────────
  async function incrementRetry(storeName, id) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, 'readwrite');
      const store = tx.objectStore(storeName);
      const getReq = store.get(id);
      getReq.onsuccess = () => {
        const record = getReq.result;
        if (!record) { resolve(null); return; }
        record.retries = (record.retries || 0) + 1;
        // بعد 5 محاولات نضع العنصر كـ failed
        if (record.retries >= 5) record.status = 'failed';
        const putReq = store.put(record);
        putReq.onsuccess = () => resolve(record);
        putReq.onerror   = () => reject(putReq.error);
      };
    });
  }

  // ─── حذف سجل ─────────────────────────────────────────────────────────────
  async function remove(storeName, id) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, 'readwrite');
      const req = tx.objectStore(storeName).delete(id);
      req.onsuccess = () => resolve(true);
      req.onerror   = () => reject(req.error);
    });
  }

  // ─── حذف السجلات المتزامنة (تنظيف دوري) ─────────────────────────────────
  async function clearSynced(storeName) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, 'readwrite');
      const store = tx.objectStore(storeName);
      const index = store.index('status');
      const req = index.openCursor(IDBKeyRange.only('synced'));
      let count = 0;
      req.onsuccess = e => {
        const cursor = e.target.result;
        if (cursor) {
          cursor.delete();
          count++;
          cursor.continue();
        } else {
          resolve(count);
        }
      };
      req.onerror = () => reject(req.error);
    });
  }

  // ─── عدد السجلات المعلّقة ─────────────────────────────────────────────────
  async function countPending() {
    const counts = {};
    for (const [key, storeName] of Object.entries(STORES)) {
      if (storeName === STORES.CACHE_DATA) continue;
      try {
        const pending = await getPending(storeName);
        counts[key] = pending.length;
      } catch { counts[key] = 0; }
    }
    counts.total = Object.values(counts).reduce((a, b) => a + b, 0);
    return counts;
  }

  // ─── حفظ بيانات للعرض Offline ────────────────────────────────────────────
  async function setCachedData(key, data) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORES.CACHE_DATA, 'readwrite');
      const store = tx.objectStore(STORES.CACHE_DATA);
      // نحذف القديم أولاً ثم نضيف الجديد
      const index = store.index('key');
      const getReq = index.getKey(key);
      getReq.onsuccess = () => {
        const existingId = getReq.result;
        const record = { key, data, updatedAt: Date.now() };
        let req;
        if (existingId !== undefined) {
          record.id = existingId;
          req = store.put(record);
        } else {
          req = store.add(record);
        }
        req.onsuccess = () => resolve(true);
        req.onerror   = () => reject(req.error);
      };
      getReq.onerror = () => reject(getReq.error);
    });
  }

  // ─── جلب بيانات مخزنة للعرض Offline ─────────────────────────────────────
  async function getCachedData(key) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORES.CACHE_DATA, 'readonly');
      const index = tx.objectStore(STORES.CACHE_DATA).index('key');
      const req = index.get(key);
      req.onsuccess = () => resolve(req.result?.data || null);
      req.onerror   = () => reject(req.error);
    });
  }

  // ─── حفظ بيانات الطالب كاملة (للعرض Offline) ──────────────────────────────
  async function cacheStudentData(data) {
    await setCachedData('student_dashboard', data);
  }

  // ─── جلب بيانات الطالب المخزنة ─────────────────────────────────────────────
  async function getCachedStudentData() {
    return getCachedData('student_dashboard');
  }

  // ─── حفظ خطة محددة ─────────────────────────────────────────────────────────
  async function cachePlanDetails(planId, data) {
    await setCachedData('plan_' + planId, data);
  }

  // ─── جلب خطة محددة ─────────────────────────────────────────────────────────
  async function getCachedPlanDetails(planId) {
    return getCachedData('plan_' + planId);
  }

  // ─── حفظ جلسة المستخدم (تذكرني) ────────────────────────────────────────────
  async function saveSession(sessionData) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORES.CACHE_DATA, 'readwrite');
      const store = tx.objectStore(STORES.CACHE_DATA);
      const index = store.index('key');
      const getReq = index.getKey('user_session');
      getReq.onsuccess = () => {
        const record = {
          key: 'user_session',
          data: {
            ...sessionData,
            savedAt: Date.now()
          },
          updatedAt: Date.now()
        };
        const existingId = getReq.result;
        if (existingId !== undefined) {
          record.id = existingId;
          store.put(record).onsuccess = () => resolve(true);
        } else {
          store.add(record).onsuccess = () => resolve(true);
        }
      };
      getReq.onerror = () => reject(getReq.error);
    });
  }

  // ─── جلب جلسة المستخدم المحفوظة ────────────────────────────────────────────
  async function getSession() {
    return getCachedData('user_session');
  }

  // ─── حذف الجلسة (تسجيل خروج) ───────────────────────────────────────────────
  async function clearSession() {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORES.CACHE_DATA, 'readwrite');
      const index = tx.objectStore(STORES.CACHE_DATA).index('key');
      const req = index.getKey('user_session');
      req.onsuccess = () => {
        if (req.result !== undefined) {
          tx.objectStore(STORES.CACHE_DATA).delete(req.result);
        }
        resolve(true);
      };
      req.onerror = () => reject(req.error);
    });
  }

  // ─── توليد UUID فريد ──────────────────────────────────────────────────────
  function generateUUID() {
    if (crypto?.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
      const r = Math.random() * 16 | 0;
      return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
    });
  }

  // ─── الواجهة العامة ───────────────────────────────────────────────────────
  return {
    STORES,
    open,
    add,
    getAll,
    getPending,
    updateStatus,
    incrementRetry,
    remove,
    clearSynced,
    countPending,
    setCachedData,
    getCachedData,
    generateUUID,
    cacheStudentData,
    getCachedStudentData,
    cachePlanDetails,
    getCachedPlanDetails,
    saveSession,
    getSession,
    clearSession
  };
})();

// تصدير للاستخدام في Node.js / اختبارات (اختياري)
if (typeof module !== 'undefined') module.exports = QuranDB;
