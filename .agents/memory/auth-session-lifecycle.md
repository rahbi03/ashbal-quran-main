---
name: Persistent login and logout lifecycle
description: Why persistent login must use server-issued cookies, and why logout order matters.
---

Use server-issued persistent login cookies rather than retaining usernames/passwords in browser storage. Keep protected HTML out of offline page caches.

**Why:** A cached authenticated page can reappear after logout; an old client-side auto-login can silently undo logout. Flask-Login also marks the remember cookie for deletion in the session during logout; clearing the session afterward removes that deletion marker.

**How to apply:** For authentication changes, test returning in a new browser session with only the remember cookie, then logout and verify that both session and remember cookies are gone. If clearing the session explicitly, do it before Flask-Login's logout operation.