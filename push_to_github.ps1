# =====================================================================
#  push_to_github.ps1 — رفع هذه النسخة إلى مستودع GitHub
# =====================================================================
#  شغّله من داخل مجلد المشروع:
#      powershell -ExecutionPolicy Bypass -File .\push_to_github.ps1
#
#  سيطلب منك رابط المستودع إن لم يكن مضبوطاً، ثم يرفع الملفات.
#  لا يحذف أي شيء على GitHub إلا إن اخترت "force" صراحةً.
# =====================================================================
param(
    [string]$RepoUrl = "",
    [string]$Branch  = "main",
    [string]$Message = "نشر على Vercel: إصلاح البنية، تفعيل المزامنة، وتحسينات السرعة",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "==> التحقق من Git" -ForegroundColor Cyan
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git غير مثبّت. ثبّته من https://git-scm.com/downloads"
}

# --- اسم/بريد افتراضي إن لم يكن مضبوطاً ---
if (-not (git config user.name))  { git config user.name  "ashbal-quran" }
if (-not (git config user.email)) { git config user.email "ashbal-quran@users.noreply.github.com" }

# --- تهيئة المستودع إن لم يوجد ---
if (-not (Test-Path ".git")) {
    Write-Host "==> تهيئة مستودع Git جديد" -ForegroundColor Cyan
    git init | Out-Null
    git branch -M $Branch 2>$null
}

# --- ضبط الـ remote ---
if ($RepoUrl -ne "") {
    if (git remote get-url origin 2>$null) {
        git remote set-url origin $RepoUrl
    } else {
        git remote add origin $RepoUrl
    }
} elseif (-not (git remote get-url origin 2>$null)) {
    $RepoUrl = Read-Host "الصق رابط مستودع GitHub (مثال: https://github.com/user/repo.git)"
    if ($RepoUrl -eq "") { throw "لم تُدخل رابط المستودع." }
    git remote add origin $RepoUrl
}

Write-Host "==> المستودع البعيد: $(git remote get-url origin)" -ForegroundColor Green

# --- تجاهل ملفات غير مرغوبة ---
git rm -r --cached . 2>$null | Out-Null   # إعادة تطبيق .gitignore على الملفات المتعقّبة
git add -A

Write-Host "==> الملفات التي ستُرفع:" -ForegroundColor Cyan
git status --short

if ($Force) {
    Write-Host "==> رفع قسري (force-with-lease)" -ForegroundColor Yellow
    git commit -m $Message 2>$null
    git push -u origin $Branch --force-with-lease
} else {
    git commit -m $Message 2>$null
    git push -u origin $Branch
}

Write-Host "==> تم الرفع بنجاح إلى الفرع $Branch" -ForegroundColor Green
Write-Host "افتح https://vercel.com/new واستورد المستودع." -ForegroundColor Green
