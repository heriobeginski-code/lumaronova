// LUMARONOVA — static catalog interactions
// (1) Copy-image-URL buttons (2) category filtering
(function () {
  'use strict';

  // --- Copy image URL ---
  // data-copy holds the relative image path; we resolve it to an absolute URL
  // against the current origin, so the copied value is a public HTTPS link.
  function absoluteUrl(rel) {
    var a = document.createElement('a');
    a.href = rel;
    return a.href; // resolves against document base -> absolute URL
  }

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    // fallback for local (file://) preview and older browsers
    return new Promise(function (resolve, reject) {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      document.body.removeChild(ta);
      ok ? resolve() : reject(new Error('copy failed'));
    });
  }

  document.querySelectorAll('.copy-btn[data-copy]').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      var rel = btn.getAttribute('data-copy');
      var url = absoluteUrl(rel);
      copyText(url).then(function () {
        var span = btn.querySelector('span');
        var original = span ? span.textContent : btn.textContent;
        if (span) span.textContent = 'Copied!';
        btn.classList.add('copied');
        setTimeout(function () {
          if (span) span.textContent = original;
          btn.classList.remove('copied');
        }, 1600);
      }).catch(function () {
        window.alert('Could not copy automatically.\nURL: ' + url);
      });
    });
  });

  // --- Category filter ---
  var filterBtns = document.querySelectorAll('.filter-btn[data-filter]');
  var grid = document.getElementById('grid');
  if (filterBtns.length && grid) {
    var cards = grid.querySelectorAll('.card');
    filterBtns.forEach(function (btn) {
      btn.addEventListener('click', function () {
        filterBtns.forEach(function (b) { b.classList.remove('active'); });
        btn.classList.add('active');
        var f = btn.getAttribute('data-filter');
        cards.forEach(function (card) {
          var cat = card.getAttribute('data-category');
          card.style.display = (f === 'all' || cat === f) ? '' : 'none';
        });
      });
    });
  }
})();
