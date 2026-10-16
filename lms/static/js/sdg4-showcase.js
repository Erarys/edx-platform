/* Progressive enhancement: content and links work without JavaScript. */
(function () {
    'use strict';
    var root = document.querySelector('.sdg4');
    if (!root) { return; }
    var reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    if ('IntersectionObserver' in window && !reducedMotion.matches) {
        var observer = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting) {
                    entry.target.classList.add('sdg4-visible');
                    observer.unobserve(entry.target);
                }
            });
        }, {threshold: 0.08});
        root.querySelectorAll('.sdg4-reveal').forEach(function (element) {
            element.classList.add('sdg4-animate');
            observer.observe(element);
        });
    }
    var filters = root.querySelector('.sdg4-filters');
    if (!filters) { return; }
    var search = root.querySelector('#sdg4-search');
    var faculty = root.querySelector('#sdg4-faculty-filter');
    var cards = Array.from(root.querySelectorAll('[data-resource-card]'));
    var count = root.querySelector('.sdg4-result-count');
    function filter() {
        var term = search.value.trim().toLocaleLowerCase();
        var visible = 0;
        cards.forEach(function (card) {
            var match = (!faculty.value || card.dataset.faculty === faculty.value)
                && card.textContent.toLocaleLowerCase().indexOf(term) !== -1;
            card.hidden = !match;
            if (match) { visible += 1; }
        });
        count.textContent = count.dataset.resultLabel.replace('{count}', String(visible));
        root.querySelector('#sdg4-no-results').hidden = visible !== 0;
    }
    filters.hidden = false;
    filters.addEventListener('submit', function (event) { event.preventDefault(); });
    search.addEventListener('input', filter);
    faculty.addEventListener('change', filter);
    filter();
}());
