(function () {
    'use strict';
    var form = document.getElementById('sdg4-course-form');
    if (!form) { return; }

    function activeMaterials(container) {
        return Array.from(container.querySelectorAll('[data-material-form]')).filter(function (material) {
            var deleted = material.querySelector('input[name$="-DELETE"]');
            return !deleted || !deleted.checked;
        });
    }

    function updateCourse(editor) {
        var type = editor.querySelector('select[name$="-type"]');
        var isVideo = type && type.value === 'video';
        editor.querySelectorAll('[data-presentation-only]').forEach(function (section) {
            section.hidden = isVideo;
        });
        editor.querySelectorAll('[data-video-only]').forEach(function (section) {
            section.hidden = !isVideo;
        });
        activeMaterials(editor).forEach(function (material) {
            var input = material.querySelector('input[type="file"][name$="-file"]');
            if (input) { input.accept = isVideo ? '.mp4,.ogv,.webm' : '.pdf,.ppt,.pptx'; }
        });
    }

    function prepareMaterial(material, editor) {
        var remove = material.querySelector('[data-remove-material]');
        if (remove) {
            remove.addEventListener('click', function () {
                var deleted = material.querySelector('input[name$="-DELETE"]');
                if (deleted) { deleted.checked = true; }
                material.hidden = true;
            });
        }
        updateCourse(editor);
    }

    form.querySelectorAll('[data-course-editor]').forEach(function (editor) {
        var type = editor.querySelector('select[name$="-type"]');
        if (type) { type.addEventListener('change', function () { updateCourse(editor); }); }
        editor.querySelectorAll('[data-material-form]').forEach(function (material) {
            prepareMaterial(material, editor);
        });
        var formset = editor.querySelector('.sdg4-formset');
        var add = formset && formset.querySelector('.sdg4-add-material');
        var template = formset && formset.querySelector('.sdg4-empty-form');
        var prefix = formset && formset.dataset.prefix;
        var total = prefix && document.getElementById('id_' + prefix + '-TOTAL_FORMS');
        if (add && template && total) {
            add.addEventListener('click', function () {
                var index = parseInt(total.value, 10);
                var wrapper = document.createElement('div');
                wrapper.innerHTML = template.innerHTML.replace(/__prefix__/g, String(index)).trim();
                var material = wrapper.firstElementChild;
                formset.querySelector('.sdg4-material-forms').appendChild(material);
                total.value = index + 1;
                prepareMaterial(material, editor);
                material.scrollIntoView({behavior: 'smooth', block: 'center'});
            });
        }
        updateCourse(editor);
    });

    document.querySelectorAll('.sdg4-edit details').forEach(function (section) {
        if (section.querySelector('.errorlist')) { section.open = true; }
    });

    function selectedFiles() {
        return Array.from(form.querySelectorAll('input[type="file"]')).reduce(function (files, input) {
            return files.concat(Array.from(input.files || []));
        }, []);
    }

    form.addEventListener('submit', function (event) {
        var files = selectedFiles();
        var limit = parseInt(form.dataset.uploadLimit, 10);
        if (files.some(function (file) { return file.size > limit; })) {
            event.preventDefault();
            window.alert(form.dataset.sizeError);
            return;
        }
        if (!files.length || !window.XMLHttpRequest || !window.FormData) { return; }
        event.preventDefault();
        var submitter = event.submitter;
        var data = new FormData(form);
        if (submitter && submitter.name) { data.set(submitter.name, submitter.value); }
        var progressBox = form.querySelector('[data-upload-progress]');
        var progress = progressBox.querySelector('progress');
        var percent = progressBox.querySelector('[data-upload-percent]');
        var status = progressBox.querySelector('[data-upload-status]');
        var buttons = Array.from(form.querySelectorAll('button[type="submit"]'));
        progressBox.hidden = false;
        progressBox.scrollIntoView({behavior: 'smooth', block: 'center'});
        buttons.forEach(function (button) { button.disabled = true; });

        var request = new XMLHttpRequest();
        request.open('POST', form.action || window.location.href);
        request.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
        request.upload.addEventListener('progress', function (uploadEvent) {
            if (!uploadEvent.lengthComputable) { return; }
            var value = Math.min(100, Math.round((uploadEvent.loaded / uploadEvent.total) * 100));
            progress.value = value;
            progress.textContent = value + '%';
            percent.textContent = value + '%';
        });
        request.addEventListener('load', function () {
            var type = request.getResponseHeader('Content-Type') || '';
            if (request.status >= 200 && request.status < 300 && type.indexOf('application/json') !== -1) {
                var response = JSON.parse(request.responseText);
                window.location.assign(response.redirect);
                return;
            }
            document.open();
            document.write(request.responseText);
            document.close();
        });
        request.addEventListener('error', function () {
            buttons.forEach(function (button) { button.disabled = false; });
            status.textContent = form.dataset.uploadError;
            progressBox.classList.add('sdg4-upload-failed');
        });
        request.send(data);
    });
}());
