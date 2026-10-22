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

    function prepareCourse(editor) {
        if (editor.dataset.courseReady === 'true') { return; }
        editor.dataset.courseReady = 'true';
        var type = editor.querySelector('select[name$="-type"]');
        if (type) { type.addEventListener('change', function () { updateCourse(editor); }); }
        var removeCourse = editor.querySelector('[data-remove-course]');
        var deletedCourse = editor.querySelector('input[name^="course-"][name$="-DELETE"]');
        if (deletedCourse && ['on', 'true', 'True', '1'].indexOf(deletedCourse.value) !== -1) {
            editor.hidden = true;
        }
        if (removeCourse) {
            removeCourse.addEventListener('click', function () {
                if (deletedCourse) { deletedCourse.value = 'on'; }
                editor.querySelectorAll('input[type="file"]').forEach(function (input) { input.value = ''; });
                editor.hidden = true;
            });
        }
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
    }

    form.querySelectorAll('[data-course-editor]').forEach(function (editor) {
        prepareCourse(editor);
    });

    var courseList = form.querySelector('[data-course-list]');
    var addCourse = form.querySelector('[data-add-course]');
    var courseTemplate = document.getElementById('sdg4-empty-course');
    var courseTotal = document.getElementById('id_course-TOTAL_FORMS');
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
            editor.scrollIntoView({behavior: 'smooth', block: 'start'});
            if (slot >= maximum) { addCourse.disabled = true; }
        });
    }

    document.querySelectorAll('.sdg4-edit details').forEach(function (section) {
        if (section.querySelector('.errorlist')) { section.open = true; }
    });

    function selectedFiles() {
        return Array.from(form.querySelectorAll('input[type="file"]')).reduce(function (files, input) {
            if (input.closest('[hidden]')) { return files; }
            var editor = input.closest('[data-course-editor]');
            var type = editor && editor.querySelector('select[name$="-type"]');
            var kind = input.name === 'photo' ? 'image' : 'presentation';
            if (input.name.indexOf('materials-') === 0 && type && type.value === 'video') { kind = 'video'; }
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
        var files = selectedFiles();
        var limit = parseInt(form.dataset.uploadLimit, 10);
        if (files.some(function (entry) { return entry.file.size > limit; })) {
            event.preventDefault();
            window.alert(form.dataset.sizeError);
            return;
        }
        if (!files.length || !window.XMLHttpRequest || !window.FormData) { return; }
        event.preventDefault();
        var submitter = event.submitter;
        var progressBox = form.querySelector('[data-upload-progress]');
        var progress = progressBox.querySelector('progress');
        var percent = progressBox.querySelector('[data-upload-percent]');
        var status = progressBox.querySelector('[data-upload-status]');
        var buttons = Array.from(form.querySelectorAll('button[type="submit"]'));
        progressBox.hidden = false;
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
        }).catch(function (error) {
            buttons.forEach(function (button) { button.disabled = false; });
            status.textContent = error.message || form.dataset.uploadError;
            progressBox.classList.add('sdg4-upload-failed');
        });
    });
}());
