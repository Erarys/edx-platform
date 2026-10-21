(function () {
    'use strict';
    var input = document.querySelector('.sdg4-edit input[name="presentations"]');
    var list = document.getElementById('sdg4-file-list');
    if (input && list) {
        input.addEventListener('change', function () {
            list.textContent = Array.from(input.files).map(function (file) { return file.name; }).join(' · ');
        });
    }

    var blocks = document.getElementById('sdg4-article-blocks');
    var emptyBlock = document.getElementById('sdg4-empty-article-block');
    var total = document.getElementById('id_article-TOTAL_FORMS');
    var accept = {
        image: '.gif,.jpeg,.jpg,.png,.webp',
        presentation: '.pdf,.ppt,.pptx',
        audio: '.m4a,.mp3,.ogg,.wav',
        video: '.mp4,.ogv,.webm'
    };

    function visibleBlocks() {
        return Array.from(blocks.querySelectorAll('[data-article-block]')).filter(function (block) {
            var deleted = block.querySelector('input[name$="-DELETE"]');
            return !deleted || !deleted.checked;
        });
    }

    function updateOrder() {
        if (!blocks) { return; }
        visibleBlocks().forEach(function (block, index) {
            var order = block.querySelector('input[name$="-ORDER"]');
            if (order) { order.value = index + 1; }
        });
    }

    function updateBlock(block) {
        var kind = block.querySelector('select[name$="-kind"]');
        if (!kind) { return; }
        var textFields = block.querySelector('[data-fields="text"]');
        var fileFields = block.querySelector('[data-fields="file"]');
        var file = block.querySelector('input[type="file"]');
        textFields.hidden = kind.value !== 'text';
        fileFields.hidden = kind.value === 'text';
        if (file) { file.accept = accept[kind.value] || ''; }
        var label = block.querySelector('[data-block-label]');
        if (label) { label.textContent = kind.options[kind.selectedIndex].text; }
    }

    function prepareBlock(block) {
        var kind = block.querySelector('select[name$="-kind"]');
        if (kind) { kind.addEventListener('change', function () { updateBlock(block); }); }
        block.querySelectorAll('[data-move]').forEach(function (button) {
            button.addEventListener('click', function () {
                var active = visibleBlocks();
                var index = active.indexOf(block);
                var other = button.dataset.move === 'up' ? active[index - 1] : active[index + 1];
                if (!other) { return; }
                if (button.dataset.move === 'up') {
                    blocks.insertBefore(block, other);
                } else {
                    blocks.insertBefore(other, block);
                }
                updateOrder();
                block.focus({preventScroll: true});
            });
        });
        var remove = block.querySelector('[data-remove]');
        if (remove) {
            remove.addEventListener('click', function () {
                var deleted = block.querySelector('input[name$="-DELETE"]');
                if (deleted) { deleted.checked = true; }
                block.hidden = true;
                updateOrder();
            });
        }
        block.tabIndex = -1;
        updateBlock(block);
    }

    if (blocks && emptyBlock && total) {
        blocks.querySelectorAll('[data-article-block]').forEach(prepareBlock);
        document.querySelectorAll('[data-add-kind]').forEach(function (button) {
            button.addEventListener('click', function () {
                var index = parseInt(total.value, 10);
                var wrapper = document.createElement('div');
                wrapper.innerHTML = emptyBlock.innerHTML.replace(/__prefix__/g, String(index)).trim();
                var block = wrapper.firstElementChild;
                var kind = block.querySelector('select[name$="-kind"]');
                kind.value = button.dataset.addKind;
                blocks.appendChild(block);
                total.value = index + 1;
                prepareBlock(block);
                updateOrder();
                block.focus();
            });
        });
        var form = blocks.closest('form');
        if (form) { form.addEventListener('submit', updateOrder); }
        updateOrder();
    }

    // Keep server-side errors visible even inside optional details sections.
    document.querySelectorAll('.sdg4-edit details').forEach(function (section) {
        if (section.querySelector('.errorlist')) { section.open = true; }
    });
}());
