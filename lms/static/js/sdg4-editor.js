(function () {
    'use strict';
    var input = document.querySelector('.sdg4-edit input[name="presentations"]');
    var list = document.getElementById('sdg4-file-list');
    if (input && list) {
        input.addEventListener('change', function () {
            list.textContent = Array.from(input.files).map(function (file) { return file.name; }).join(' · ');
        });
    }
    // Keep server-side errors visible even inside optional details sections.
    document.querySelectorAll('.sdg4-edit details').forEach(function (section) {
        if (section.querySelector('.errorlist')) { section.open = true; }
    });
}());
