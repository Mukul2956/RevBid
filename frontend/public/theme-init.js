// Applies the saved/system theme before first paint to avoid a light-mode flash.
// Kept as an external file (not inline) so the Content-Security-Policy can stay `script-src 'self'`.
try {
  var t = localStorage.getItem('theme')
  if (t === 'dark' || (!t && matchMedia('(prefers-color-scheme: dark)').matches)) {
    document.documentElement.classList.add('dark')
  }
} catch (e) {
  /* storage blocked: fall back to the light theme */
}
