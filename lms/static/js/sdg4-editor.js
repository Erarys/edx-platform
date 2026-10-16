/* Native formsets keep the ordering explicit and work with ordinary multipart POSTs. */
document.addEventListener('DOMContentLoaded', () => {
    const updateCourseFields = (editor) => {
        const isVideo = editor.querySelector('select[name$="-type"]').value === 'video';
        editor.querySelector('.sdg4-video-help').hidden = !isVideo;
        editor.querySelectorAll('input').forEach((input) => {
            if (/-captions$|-captions_url$|-youtube_captions$|-duration$/.test(input.name)) {
                input.closest('p').hidden = !isVideo;
            }
            if (input.type === 'file') {
                input.accept = input.name.endsWith('-captions') ? '.vtt' : isVideo ? '.mp4,.webm,.ogv' : '.pdf';
            }
        });
    };
    document.querySelectorAll('.sdg4-course-editor').forEach((editor) => {
        editor.querySelector('select[name$="-type"]').addEventListener('change', () => updateCourseFields(editor));
        updateCourseFields(editor);
    });
    document.querySelectorAll('.sdg4-formset').forEach((formset) => {
        const total = document.getElementById(`id_${formset.dataset.prefix}-TOTAL_FORMS`);
        const maximum = document.getElementById(`id_${formset.dataset.prefix}-MAX_NUM_FORMS`);
        const button = formset.querySelector('.sdg4-add-material');
        button.addEventListener('click', () => {
            const index = Number(total.value);
            if (index >= Number(maximum.value)) {
                button.disabled = true;
                return;
            }
            const content = formset.querySelector('template').content.cloneNode(true);
            content.querySelectorAll('[id], [name], [for]').forEach((element) => {
                ['id', 'name', 'for'].forEach((attribute) => {
                    if (element.hasAttribute(attribute)) {
                        element.setAttribute(attribute, element.getAttribute(attribute).replaceAll('__prefix__', String(index)));
                    }
                });
            });
            const firstInput = content.querySelector('input');
            formset.querySelector('.sdg4-material-forms').appendChild(content);
            total.value = String(index + 1);
            updateCourseFields(formset.closest('.sdg4-course-editor'));
            firstInput.focus();
        });
    });
});
