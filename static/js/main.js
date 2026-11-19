// Figure carousel: one active slide with its neighbours dimmed at the sides.
(function () {
  var root = document.getElementById('carousel');
  if (!root) return;
  var slides = Array.prototype.slice.call(root.querySelectorAll('.slide'));
  var dots = document.getElementById('carousel-dots');
  var current = 0;
  var timer = null;

  function show(index) {
    var n = slides.length;
    current = (index + n) % n;
    slides.forEach(function (slide, i) {
      slide.classList.remove('active', 'prev', 'next');
      if (i === current) slide.classList.add('active');
      else if (i === (current - 1 + n) % n) slide.classList.add('prev');
      else if (i === (current + 1) % n) slide.classList.add('next');
    });
    Array.prototype.forEach.call(dots.children, function (dot, i) {
      dot.classList.toggle('active', i === current);
    });
  }

  function restart() {
    if (timer) clearInterval(timer);
    timer = setInterval(function () { show(current + 1); }, 7000);
  }

  slides.forEach(function (slide, i) {
    var dot = document.createElement('button');
    dot.className = 'carousel-dot';
    dot.setAttribute('aria-label', 'Show figure ' + (i + 1));
    dot.addEventListener('click', function () { show(i); restart(); });
    dots.appendChild(dot);
    slide.addEventListener('click', function () { if (i !== current) { show(i); restart(); } });
  });
  root.querySelector('.carousel-nav.prev').addEventListener('click', function () { show(current - 1); restart(); });
  root.querySelector('.carousel-nav.next').addEventListener('click', function () { show(current + 1); restart(); });

  show(0);
  restart();
})();
