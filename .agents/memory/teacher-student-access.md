---
name: Teacher access to shared student work
description: Role policy for linked teachers editing another teacher's student work.
---

The administration's teacher–student assignment controls a teacher's visibility and ability to view or edit that student's plans and evaluations. The author of a plan or evaluation does not need to be the teacher making an edit.

**Why:** Multiple teachers may be assigned to the same student and need to correct each other's plans and evaluations. Restricting edits to the creator would break this collaboration, while showing all students would expose unassigned students.

**How to apply:** When adding student-facing teacher views or edit endpoints, check the current assignment for both GET and POST. Do not use the plan/evaluation creator ID to authorize edits. Preserve any intentionally stricter deletion rules unless the user changes that policy.