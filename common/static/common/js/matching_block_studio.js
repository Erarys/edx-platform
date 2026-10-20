/* global window, document */
window.MatchingBlockStudio = function MatchingBlockStudio(runtime, element, config) {
  'use strict';

  var options = config || {};
  var rootElement = element && element.jquery ? element[0] : element;
  var root = rootElement.querySelector('[data-matching-editor]');
  if (!root) {
    return {};
  }

  var pairsContainer = root.querySelector('[data-matching-pairs]');
  var errorBox = root.querySelector('[data-matching-editor-error]');
  var weightInput = root.querySelector('[data-matching-weight]');
  var attemptsInput = root.querySelector('[data-matching-max-attempts]');
  var shuffleInput = root.querySelector('[data-matching-shuffle]');
  var scoringSummary = root.querySelector('[data-scoring-summary]');
  var optionsSummary = root.querySelector('[data-options-summary]');

  function message(key, values) {
    var text = (options.messages && options.messages[key]) || '';
    Object.keys(values || {}).forEach(function replaceValue(name) {
      text = text.replace('{' + name + '}', values[name]);
    });
    return text;
  }

  function pairId() {
    return 'pair-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  }

  function rightId() {
    return 'answer-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  }

  function showError(message) {
    errorBox.textContent = message || '';
    errorBox.hidden = !message;
  }

  function createInput(attribute, label) {
    var input = document.createElement('input');
    input.type = 'text';
    input.maxLength = 500;
    input.setAttribute(attribute, '');
    input.setAttribute('aria-label', label);
    return input;
  }

  function updatePairIndexes() {
    Array.prototype.forEach.call(
      root.querySelectorAll('[data-matching-pair]'),
      function updatePairIndex(row, index) {
        row.querySelector('[data-pair-index]').textContent = index + 1;
      }
    );
  }

  function updateSettingsSummaries() {
    var weight = weightInput.value || '0';
    var attempts = parseInt(attemptsInput.value, 10) || 0;
    var attemptsText = attempts > 0
      ? message('attemptsSummary', { attempts: attempts })
      : message('unlimitedAttempts');
    scoringSummary.textContent = message('points', { weight: weight }) + ' · ' + attemptsText;
    optionsSummary.textContent = shuffleInput.checked
      ? message('rightColumnShuffled')
      : message('fixedAnswerOrder');
  }

  function addPair() {
    var row = document.createElement('div');
    var index = document.createElement('span');
    var arrow = document.createElement('span');
    var remove = document.createElement('button');
    row.className = 'matching-editor__pair';
    row.setAttribute('data-matching-pair', '');
    row.setAttribute('data-pair-id', pairId());
    row.setAttribute('data-pair-right-id', rightId());
    index.className = 'matching-editor__pair-index';
    index.setAttribute('data-pair-index', '');
    arrow.className = 'matching-editor__arrow';
    arrow.setAttribute('aria-hidden', 'true');
    arrow.textContent = '↔';
    remove.type = 'button';
    remove.className = 'matching-editor__remove';
    remove.setAttribute('data-remove-pair', '');
    remove.setAttribute('aria-label', message('removePair'));
    remove.textContent = '×';
    row.appendChild(index);
    row.appendChild(createInput('data-pair-left', message('leftItem')));
    row.appendChild(arrow);
    row.appendChild(createInput('data-pair-right', message('rightMatch')));
    row.appendChild(remove);
    pairsContainer.appendChild(row);
    updatePairIndexes();
    row.querySelector('[data-pair-left]').focus();
  }

  function collectPairs() {
    return Array.prototype.map.call(root.querySelectorAll('[data-matching-pair]'), function collect(row) {
      return {
        id: row.getAttribute('data-pair-id'),
        right_id: row.getAttribute('data-pair-right-id'),
        left: row.querySelector('[data-pair-left]').value.trim(),
        right: row.querySelector('[data-pair-right]').value.trim(),
      };
    });
  }

  root.querySelector('[data-add-pair]').addEventListener('click', addPair);
  pairsContainer.addEventListener('click', function removePair(event) {
    var remove = event.target.closest('[data-remove-pair]');
    if (!remove) {
      return;
    }
    if (root.querySelectorAll('[data-matching-pair]').length <= 2) {
      showError(message('minimumPairs'));
      return;
    }
    remove.closest('[data-matching-pair]').remove();
    updatePairIndexes();
    showError('');
  });

  weightInput.addEventListener('input', updateSettingsSummaries);
  attemptsInput.addEventListener('input', updateSettingsSummaries);
  shuffleInput.addEventListener('change', updateSettingsSummaries);

  root.querySelector('.cancel-button').addEventListener('click', function cancel(event) {
    event.preventDefault();
    runtime.notify('cancel', {});
  });

  root.querySelector('.save-button').addEventListener('click', function save(event) {
    event.preventDefault();
    showError('');
    var payload = {
      display_name: root.querySelector('[data-matching-display-name]').value.trim(),
      question: root.querySelector('[data-matching-question]').value.trim(),
      pairs: collectPairs(),
      shuffle_answers: root.querySelector('[data-matching-shuffle]').checked,
      max_attempts: root.querySelector('[data-matching-max-attempts]').value,
      weight: root.querySelector('[data-matching-weight]').value,
    };
    if (!payload.display_name || !payload.question || payload.pairs.some(function incomplete(pair) {
      return !pair.left || !pair.right;
    })) {
      showError(message('completeFields'));
      return;
    }

    runtime.notify('save', { state: 'start' });
    window.jQuery.ajax({
      type: 'POST',
      url: runtime.handlerUrl(element, 'studio_submit'),
      data: JSON.stringify(payload),
      contentType: 'application/json',
      dataType: 'json',
    }).done(function saved(response) {
      if (response.result === 'success') {
        runtime.notify('save', { state: 'end' });
      } else {
        showError(response.message || message('saveFailed'));
        runtime.notify('error', {
          title: message('errorTitle'),
          message: response.message || message('saveFailed'),
        });
      }
    }).fail(function saveFailed() {
      showError(message('saveFailed'));
      runtime.notify('error', {
        title: message('errorTitle'),
        message: message('saveFailed'),
      });
    });
  });

  return {};
};
