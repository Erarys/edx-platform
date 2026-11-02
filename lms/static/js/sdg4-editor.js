/* SDG 4 publication editor.
 *
 * The form works without JavaScript; this script only makes it easier:
 * drag-and-drop file zones, a visible list of chosen files, file type checks
 * before upload, "add / remove course" controls, protection against losing
 * unsaved work, and chunked uploads for large files.
 */
(function () {
    'use strict';
    var form = document.getElementById('sdg4-course-form');
    if (!form) { return; }
    var courseList = form.querySelector('[data-course-list]');
    var addCourse = form.querySelector('[data-add-course]');
    var courseTemplate = document.getElementById('sdg4-empty-course');
    var courseTotal = document.getElementById('id_course-TOTAL_FORMS');
    var EXTENSIONS = {lecture: ['.pdf'], video: ['.mp4', '.webm', '.ogv']};
    var dirty = false;
    var submitting = false;

    function message(name, names) {
        return (form.dataset[name] || '').replace('{names}', names || '');
    }

    function visibleCourses() {
        return Array.from(courseList ? courseList.querySelectorAll('[data-course-editor]') : [])
            .filter(function (editor) { return !editor.hidden; });
    }

    function renumberCourses() {
        if (!courseList) { return; }
        var number = 0;
        courseList.querySelectorAll('[data-course-editor]').forEach(function (editor) {
            if (editor.hidden) { return; }
            number += 1;
            var heading = editor.querySelector('[data-course-number]');
            if (heading) {
                heading.textContent = courseList.dataset.courseLabel.replace('{number}', String(number));
            }
        });
        // The only remaining course cannot be removed: an empty form is not useful.
        var visible = visibleCourses();
        visible.forEach(function (editor) {
            var remove = editor.querySelector('[data-remove-course]');
            if (remove) { remove.hidden = visible.length < 2; }
        });
        if (addCourse && courseTotal) {
            addCourse.disabled = parseInt(courseTotal.value, 10) >= parseInt(addCourse.dataset.maxTabs, 10);
        }
    }

    function courseType(editor) {
        var checked = editor.querySelector('input[name$="-type"]:checked');
        return checked && checked.value === 'video' ? 'video' : 'lecture';
    }

    function extensionOf(name) {
        var dot = name.lastIndexOf('.');
        return dot === -1 ? '' : name.slice(dot).toLowerCase();
    }

    function activeMaterials(container) {
        return Array.from(container.querySelectorAll('[data-material-form]')).filter(function (material) {
            var deleted = material.querySelector('input[name$="-DELETE"]');
            return !material.hidden && (!deleted || !deleted.checked);
        });
    }

    function formatSize(bytes) {
        if (bytes >= 1024 * 1024 * 1024) { return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB'; }
        if (bytes >= 1024 * 1024) { return (bytes / (1024 * 1024)).toFixed(1) + ' MB'; }
        return Math.max(1, Math.round(bytes / 1024)) + ' KB';
    }

    /* ---- Drop zone: one place for all files of a course ---- */

    function setFiles(input, files) {
        if (!window.DataTransfer) { return false; }
        var transfer = new DataTransfer();
        files.forEach(function (file) { transfer.items.add(file); });
        input.files = transfer.files;
        return true;
    }

    function showZoneError(zone, text) {
        var error = zone.querySelector('[data-dropzone-error]');
        if (!error) { return; }
        error.textContent = text;
        error.hidden = !text;
        zone.classList.toggle('sdg4-field-invalid', Boolean(text));
    }

    function renderFileList(zone, editor) {
        var input = zone.querySelector('input[type="file"]');
        var list = zone.querySelector('[data-file-list]');
        if (!input || !list) { return; }
        list.innerHTML = '';
        var files = Array.from(input.files || []);
        var allowed = EXTENSIONS[courseType(editor)];
        var wrong = files.filter(function (file) { return allowed.indexOf(extensionOf(file.name)) === -1; });
        var limit = parseInt(form.dataset.uploadLimit, 10);
        var tooLarge = files.filter(function (file) { return file.size > limit; });
        files.forEach(function (file, index) {
            var item = document.createElement('li');
            if (wrong.indexOf(file) !== -1 || tooLarge.indexOf(file) !== -1) { item.className = 'sdg4-file-bad'; }
            var name = document.createElement('span');
            name.className = 'sdg4-file-name';
            name.textContent = file.name;
            var size = document.createElement('span');
            size.className = 'sdg4-file-size';
            size.textContent = formatSize(file.size);
            item.appendChild(name);
            item.appendChild(size);
            if (window.DataTransfer) {
                var remove = document.createElement('button');
                remove.type = 'button';
                remove.className = 'sdg4-icon-button';
                remove.setAttribute('aria-label', message('msgRemoveFile') + ': ' + file.name);
                remove.title = message('msgRemoveFile');
                remove.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>';
                remove.addEventListener('click', function () {
                    var rest = Array.from(input.files).filter(function (_file, position) { return position !== index; });
                    setFiles(input, rest);
                    renderFileList(zone, editor);
                });
                item.appendChild(remove);
            }
            list.appendChild(item);
        });
        zone.classList.toggle('sdg4-dropzone-filled', files.length > 0);
        if (wrong.length) {
            showZoneError(zone, message(courseType(editor) === 'video' ? 'msgWrongVideo' : 'msgWrongPdf',
                wrong.map(function (file) { return file.name; }).join(', ')));
        } else if (tooLarge.length) {
            showZoneError(zone, form.dataset.sizeError);
        } else {
            showZoneError(zone, '');
        }
    }

    function prepareDropzone(zone, editor) {
        var input = zone.querySelector('input[type="file"]');
        if (!input) { return; }
        input.addEventListener('change', function () { dirty = true; renderFileList(zone, editor); });
        ['dragenter', 'dragover'].forEach(function (name) {
            zone.addEventListener(name, function (event) {
                event.preventDefault();
                zone.classList.add('sdg4-dropzone-over');
            });
        });
        ['dragleave', 'dragend', 'drop'].forEach(function (name) {
            zone.addEventListener(name, function () { zone.classList.remove('sdg4-dropzone-over'); });
        });
        zone.addEventListener('drop', function (event) {
            event.preventDefault();
            var dropped = Array.from((event.dataTransfer && event.dataTransfer.files) || []);
            if (!dropped.length) { return; }
            // Dropping adds to the files already chosen instead of replacing them.
            if (!setFiles(input, Array.from(input.files || []).concat(dropped))) { return; }
            dirty = true;
            renderFileList(zone, editor);
        });
    }

    /* ---- Course type: PDF slides or video ---- */

    function updateCourse(editor) {
        var type = courseType(editor);
        var isVideo = type === 'video';
        editor.querySelectorAll('[data-video-only]').forEach(function (element) { element.hidden = !isVideo; });
        editor.querySelectorAll('[data-lecture-only]').forEach(function (element) { element.hidden = isVideo; });
        editor.querySelectorAll('input[type="file"]').forEach(function (input) {
            input.accept = EXTENSIONS[type].join(',');
        });
        var zone = editor.querySelector('[data-dropzone]');
        if (zone) { renderFileList(zone, editor); }
    }

    function updateLessonsHeading(editor) {
        var heading = editor.querySelector('[data-lessons-heading]');
        if (heading) { heading.hidden = activeMaterials(editor).length === 0; }
    }

    function prepareMaterial(material, editor) {
        var remove = material.querySelector('[data-remove-material]');
        var file = material.querySelector('input[type="file"][name$="-file"]');
        var url = material.querySelector('input[name$="-file_url"]');
        if (file && url) {
            file.addEventListener('change', function () {
                if (file.files && file.files.length) { url.value = ''; }
            });
            url.addEventListener('input', function () {
                if (url.value.trim()) { file.value = ''; }
            });
        }
        if (remove) {
            remove.addEventListener('click', function () {
                var deleted = material.querySelector('input[name$="-DELETE"]');
                if (deleted) { deleted.checked = true; }
                material.hidden = true;
                dirty = true;
                updateLessonsHeading(editor);
            });
        }
        updateCourse(editor);
    }

    function prepareCourse(editor) {
        if (editor.dataset.courseReady === 'true') { return; }
        editor.dataset.courseReady = 'true';
        editor.querySelectorAll('input[name$="-type"]').forEach(function (radio) {
            radio.addEventListener('change', function () { updateCourse(editor); });
        });
        var removeCourse = editor.querySelector('[data-remove-course]');
        var deletedCourse = editor.querySelector('input[name^="course-"][name$="-DELETE"]');
        if (deletedCourse && ['on', 'true', 'True', '1'].indexOf(deletedCourse.value) !== -1) {
            editor.hidden = true;
        }
        if (removeCourse) {
            removeCourse.addEventListener('click', function () {
                if (visibleCourses().length < 2) { return; }
                if (!window.confirm(message('msgRemoveCourse'))) { return; }
                if (deletedCourse) { deletedCourse.value = 'on'; }
                editor.querySelectorAll('input[type="file"]').forEach(function (input) { input.value = ''; });
                // A removed course must not block the browser's required-field check.
                editor.querySelectorAll('[required]').forEach(function (input) { input.required = false; });
                editor.hidden = true;
                dirty = true;
                renumberCourses();
            });
        }
        var zone = editor.querySelector('[data-dropzone]');
        if (zone) { prepareDropzone(zone, editor); }
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
                updateLessonsHeading(editor);
                var first = material.querySelector('input[name$="-file_url"]');
                if (first) { first.focus(); }
            });
        }
        updateCourse(editor);
        updateLessonsHeading(editor);
    }

    form.querySelectorAll('[data-course-editor]').forEach(function (editor) {
        prepareCourse(editor);
    });
    renumberCourses();

    if (courseList && addCourse && courseTemplate && courseTotal) {
        addCourse.addEventListener('click', function () {
            var current = parseInt(courseTotal.value, 10);
            var maximum = parseInt(addCourse.dataset.maxTabs, 10);
            if (current >= maximum) { return; }
            var slot = current + 1;
            var wrapper = document.createElement('div');
            wrapper.innerHTML = courseTemplate.innerHTML.replace(/__tab__/g, String(slot)).trim();
            var editor = wrapper.firstElementChild;
            courseList.appendChild(editor);
            courseTotal.value = slot;
            prepareCourse(editor);
            renumberCourses();
            dirty = true;
            editor.scrollIntoView({behavior: 'smooth', block: 'start'});
            var title = editor.querySelector('input[name$="-topic_ru"]');
            if (title) { title.focus({preventScroll: true}); }
        });
    }

    form.querySelectorAll('details').forEach(function (section) {
        if (section.querySelector('.errorlist')) { section.open = true; }
    });
    var summary = form.querySelector('[data-error-summary]');
    if (summary) { summary.focus(); }

    form.addEventListener('input', function () { dirty = true; });
    form.addEventListener('change', function () { dirty = true; });
    window.addEventListener('beforeunload', function (event) {
        if (!dirty || submitting) { return undefined; }
        event.preventDefault();
        event.returnValue = message('msgLeave');
        return event.returnValue;
    });

    /* ---- Checks before sending ---- */

    function courseHasLessons(editor) {
        var zone = editor.querySelector('[data-dropzone] input[type="file"]');
        if (zone && zone.files && zone.files.length) { return true; }
        return activeMaterials(editor).some(function (material) {
            if (material.dataset.hasSource === 'true') { return true; }
            var file = material.querySelector('input[type="file"]');
            var url = material.querySelector('input[name$="-file_url"]');
            return (file && file.files && file.files.length) || (url && url.value.trim());
        });
    }

    function checkBeforeSubmit(isDraft) {
        var problem = null;
        visibleCourses().forEach(function (editor) {
            var zone = editor.querySelector('[data-dropzone]');
            if (!zone) { return; }
            renderFileList(zone, editor);
            if (zone.classList.contains('sdg4-field-invalid') && zone.querySelector('[data-dropzone-error]:not([hidden])')) {
                problem = problem || zone;
                return;
            }
            if (!isDraft && !courseHasLessons(editor)) {
                showZoneError(zone, form.dataset.msgNoFiles);
                problem = problem || zone;
            }
        });
        if (problem) {
            problem.scrollIntoView({behavior: 'smooth', block: 'center'});
            var input = problem.querySelector('input[type="file"]');
            if (input) { input.focus({preventScroll: true}); }
            return false;
        }
        return true;
    }

    function selectedFiles() {
        return Array.from(form.querySelectorAll('input[type="file"]')).reduce(function (files, input) {
            if (input.closest('[hidden]')) { return files; }
            var editor = input.closest('[data-course-editor]');
            var kind = 'image';
            if (input.name !== 'photo') {
                kind = editor && courseType(editor) === 'video' ? 'video' : 'presentation';
            }
            return files.concat(Array.from(input.files || []).map(function (file) {
                return {input: input, file: file, kind: kind};
            }));
        }, []);
    }

    function csrfToken() {
        var input = form.querySelector('input[name="csrfmiddlewaretoken"]');
        return input ? input.value : '';
    }

    function parseJsonResponse(request) {
        var type = request.getResponseHeader('Content-Type') || '';
        if (type.indexOf('application/json') === -1) { return null; }
        try { return JSON.parse(request.responseText); } catch (error) { return null; }
    }

    function jsonPost(url, payload, retries) {
        return new Promise(function (resolve, reject) {
            var request = new XMLHttpRequest();
            request.open('POST', url);
            request.setRequestHeader('Content-Type', 'application/json');
            request.setRequestHeader('X-CSRFToken', csrfToken());
            request.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
            request.addEventListener('load', function () {
                var response = parseJsonResponse(request);
                if (request.status >= 200 && request.status < 300 && response) { resolve(response); return; }
                reject(new Error(response && response.error ? response.error : form.dataset.uploadError));
            });
            request.addEventListener('error', function () {
                if (retries > 0) {
                    window.setTimeout(function () {
                        jsonPost(url, payload, retries - 1).then(resolve, reject);
                    }, 750);
                    return;
                }
                reject(new Error(form.dataset.uploadError));
            });
            request.send(JSON.stringify(payload));
        });
    }

    function sendChunk(token, index, chunk, onProgress, retries) {
        return new Promise(function (resolve, reject) {
            var request = new XMLHttpRequest();
            request.open('POST', form.dataset.uploadChunkUrl);
            request.setRequestHeader('Content-Type', 'application/octet-stream');
            request.setRequestHeader('X-CSRFToken', csrfToken());
            request.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
            request.setRequestHeader('X-SDG4-Upload', token);
            request.setRequestHeader('X-SDG4-Chunk-Index', String(index));
            request.upload.addEventListener('progress', function (event) {
                if (event.lengthComputable) { onProgress(event.loaded); }
            });
            request.addEventListener('load', function () {
                var response = parseJsonResponse(request);
                if (request.status >= 200 && request.status < 300 && response) { resolve(response); return; }
                reject(new Error(response && response.error ? response.error : form.dataset.uploadError));
            });
            request.addEventListener('error', function () {
                if (retries > 0) {
                    window.setTimeout(function () {
                        sendChunk(token, index, chunk, onProgress, retries - 1).then(resolve, reject);
                    }, 750);
                    return;
                }
                reject(new Error(form.dataset.uploadError));
            });
            request.send(chunk);
        });
    }

    function uploadFile(entry, completedBytes, totalBytes, updateProgress) {
        return jsonPost(form.dataset.uploadStartUrl, {
            field: entry.input.name,
            kind: entry.kind,
            name: entry.file.name,
            size: entry.file.size
        }, 2).then(function (started) {
            var index = 0;
            function nextChunk() {
                if (index >= started.chunks) {
                    return jsonPost(form.dataset.uploadCompleteUrl, {token: started.token}, 2).then(function () {
                        updateProgress(completedBytes + entry.file.size, totalBytes, true);
                        return started.token;
                    });
                }
                var offset = index * started.chunk_size;
                var chunk = entry.file.slice(offset, Math.min(offset + started.chunk_size, entry.file.size));
                return sendChunk(started.token, index, chunk, function (loaded) {
                    updateProgress(completedBytes + offset + loaded, totalBytes, false);
                }, 3).then(function () {
                    index += 1;
                    return nextChunk();
                });
            }
            return nextChunk();
        });
    }

    form.addEventListener('submit', function (event) {
        var submitter = event.submitter;
        var isDraft = Boolean(submitter && submitter.value === 'draft');
        if (submitting) { event.preventDefault(); return; }
        if (!checkBeforeSubmit(isDraft)) { event.preventDefault(); return; }
        var buttons = Array.from(form.querySelectorAll('button[type="submit"]'));
        var files = selectedFiles();
        submitting = true;
        if (!files.length || !window.XMLHttpRequest || !window.FormData) {
            // Ordinary submission: only block a second click, which used to
            // create the same publication twice.
            window.setTimeout(function () { buttons.forEach(function (button) { button.disabled = true; }); }, 0);
            return;
        }
        event.preventDefault();
        var progressBox = form.querySelector('[data-upload-progress]');
        var progress = progressBox.querySelector('progress');
        var percent = progressBox.querySelector('[data-upload-percent]');
        var status = progressBox.querySelector('[data-upload-status]');
        progressBox.hidden = false;
        progressBox.classList.remove('sdg4-upload-failed');
        progressBox.scrollIntoView({behavior: 'smooth', block: 'center'});
        buttons.forEach(function (button) { button.disabled = true; });
        var totalBytes = files.reduce(function (total, entry) { return total + entry.file.size; }, 0);
        function updateProgress(loaded, total, complete) {
            var value = Math.round((loaded / total) * 100);
            value = complete && loaded >= total ? 99 : Math.min(98, value);
            progress.value = value;
            progress.textContent = value + '%';
            percent.textContent = value + '%';
        }
        function failed(text) {
            submitting = false;
            buttons.forEach(function (button) { button.disabled = false; });
            status.textContent = text || form.dataset.uploadError;
            progressBox.classList.add('sdg4-upload-failed');
        }
        var completedBytes = 0;
        var tokens = [];
        files.reduce(function (promise, entry) {
            return promise.then(function () {
                return uploadFile(entry, completedBytes, totalBytes, updateProgress).then(function (token) {
                    tokens.push(token);
                    completedBytes += entry.file.size;
                });
            });
        }, Promise.resolve()).then(function () {
            var data = new FormData(form);
            files.forEach(function (entry) { data.delete(entry.input.name); });
            tokens.forEach(function (token) { data.append('_sdg4_staged_upload', token); });
            if (submitter && submitter.name) { data.set(submitter.name, submitter.value); }
            var request = new XMLHttpRequest();
            request.open('POST', form.getAttribute('action') || window.location.href);
            request.setRequestHeader('X-CSRFToken', csrfToken());
            request.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
            request.addEventListener('load', function () {
                var response = parseJsonResponse(request);
                if (request.status >= 200 && request.status < 300 && response && response.redirect) {
                    progress.value = 100;
                    progress.textContent = '100%';
                    percent.textContent = '100%';
                    window.location.assign(response.redirect);
                    return;
                }
                // The server returned the form with error messages; files that
                // were already uploaded are kept by their hidden tokens.
                document.open();
                document.write(request.responseText);
                document.close();
            });
            request.addEventListener('error', function () { failed(form.dataset.uploadError); });
            request.send(data);
        }).catch(function (error) {
            failed(error.message);
        });
    });
}());
