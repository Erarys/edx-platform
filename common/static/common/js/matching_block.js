/* global window, document */
window.MatchingBlock = function MatchingBlock(runtime, element, config) {
  'use strict';

  var options = config || {};
  var rootElement = element && element.jquery ? element[0] : element;
  var root = rootElement.querySelector('[data-matching-block]');
  if (!root) {
    return {};
  }

  var board = root.querySelector('[data-matching-board]');
  var svg = root.querySelector('[data-matching-lines]');
  var submitButton = root.querySelector('[data-matching-submit]');
  var clearButton = root.querySelector('[data-matching-clear]');
  var feedback = root.querySelector('[data-matching-feedback]');
  var progress = root.querySelector('[data-matching-progress]');
  var attemptsLabel = root.querySelector('[data-matching-attempts]');
  var leftItems = Array.prototype.slice.call(root.querySelectorAll('[data-matching-left]'));
  var rightItems = Array.prototype.slice.call(root.querySelectorAll('[data-matching-right]'));
  var matches = Object.assign({}, options.matches || {});
  var correctPairIds = options.correctPairIds || [];
  var locked = Boolean(options.locked);
  var attempts = options.attempts || 0;
  var selectedLeft = null;
  var dragging = null;
  var pointerPosition = null;
  var colors = ['#2563eb', '#7c3aed', '#db2777', '#dc2626', '#ea580c', '#0f766e', '#4d7c0f', '#0369a1'];

  function message(key, values) {
    var text = (options.messages && options.messages[key]) || '';
    Object.keys(values || {}).forEach(function replaceValue(name) {
      text = text.replace('{' + name + '}', values[name]);
    });
    return text;
  }

  function itemText(item) {
    var textNode = item.querySelector('.matching-item__text');
    return textNode ? textNode.textContent.trim() : '';
  }

  function findLeft(leftId) {
    return leftItems.find(function matchesLeft(item) {
      return item.getAttribute('data-matching-left') === leftId;
    });
  }

  function findRight(rightId) {
    return rightItems.find(function matchesRight(item) {
      return item.getAttribute('data-matching-right') === rightId;
    });
  }

  function setFeedback(text, status) {
    feedback.textContent = text || '';
    feedback.classList.remove(
      'matching-feedback--correct',
      'matching-feedback--incorrect',
      'matching-feedback--error'
    );
    if (status) {
      feedback.classList.add('matching-feedback--' + status);
    }
  }

  function setProgress(score, maximum) {
    var earned = Number(score || 0).toFixed(1);
    var possible = Number(maximum || 0).toFixed(1);
    progress.textContent = message('progress', { earned: earned, maximum: possible });
  }

  function setAttempts(attempts) {
    if (options.maxAttempts) {
      attemptsLabel.textContent = message('attemptsLimited', {
        attempts: attempts,
        max_attempts: options.maxAttempts,
      });
    } else if (attempts) {
      attemptsLabel.textContent = message('attempts', { attempts: attempts });
    } else {
      attemptsLabel.textContent = '';
    }
  }

  function endpoint(item) {
    return item.querySelector('.matching-endpoint');
  }

  function coordinates(item, side, boardRect) {
    var rect = endpoint(item).getBoundingClientRect();
    return {
      x: (side === 'left' ? rect.right : rect.left) - boardRect.left,
      y: rect.top + (rect.height / 2) - boardRect.top,
    };
  }

  function svgNode(name, attributes) {
    var node = document.createElementNS('http://www.w3.org/2000/svg', name);
    Object.keys(attributes).forEach(function setAttribute(attribute) {
      node.setAttribute(attribute, attributes[attribute]);
    });
    return node;
  }

  function pathData(start, end) {
    var bend = Math.max(35, Math.abs(end.x - start.x) * 0.45);
    return 'M ' + start.x + ' ' + start.y
      + ' C ' + (start.x + bend) + ' ' + start.y
      + ', ' + (end.x - bend) + ' ' + end.y
      + ', ' + end.x + ' ' + end.y;
  }

  function clearSvg() {
    while (svg.firstChild) {
      svg.removeChild(svg.firstChild);
    }
  }

  function drawLines() {
    var boardRect = board.getBoundingClientRect();
    clearSvg();
    svg.setAttribute('viewBox', '0 0 ' + boardRect.width + ' ' + boardRect.height);

    Object.keys(matches).forEach(function drawMatch(leftId) {
      var rightId = matches[leftId];
      var left = findLeft(leftId);
      var right = findRight(rightId);
      if (!left || !right) {
        return;
      }
      var color = colors[leftItems.indexOf(left) % colors.length];
      var start = coordinates(left, 'left', boardRect);
      var end = coordinates(right, 'right', boardRect);
      svg.appendChild(svgNode('path', {
        d: pathData(start, end),
        class: 'matching-line',
        stroke: color,
      }));
      svg.appendChild(svgNode('circle', {
        cx: start.x,
        cy: start.y,
        r: 5,
        class: 'matching-line-end',
        fill: color,
      }));
      svg.appendChild(svgNode('circle', {
        cx: end.x,
        cy: end.y,
        r: 5,
        class: 'matching-line-end',
        fill: color,
      }));
    });

    if (dragging && pointerPosition) {
      var dragItem = findLeft(dragging);
      if (dragItem) {
        var start = coordinates(dragItem, 'left', boardRect);
        svg.appendChild(svgNode('path', {
          d: pathData(start, pointerPosition),
          class: 'matching-line matching-line--temporary',
        }));
      }
    }
  }

  function clearResultStyles() {
    leftItems.concat(rightItems).forEach(function clearItem(item) {
      item.classList.remove('is-correct', 'is-incorrect');
    });
    correctPairIds = [];
  }

  function updateItems() {
    leftItems.forEach(function updateLeft(item) {
      var id = item.getAttribute('data-matching-left');
      var connected = Boolean(matches[id]);
      item.classList.toggle('is-connected', connected);
      item.classList.toggle('is-selected', selectedLeft === id);
      item.setAttribute('aria-pressed', selectedLeft === id ? 'true' : 'false');
      item.disabled = locked;
    });
    rightItems.forEach(function updateRight(item) {
      var id = item.getAttribute('data-matching-right');
      var connected = Object.keys(matches).some(function isMatch(leftId) {
        return matches[leftId] === id;
      });
      item.classList.toggle('is-connected', connected);
      item.setAttribute('aria-pressed', connected ? 'true' : 'false');
      item.disabled = locked;
    });
    submitButton.disabled = locked || Object.keys(matches).length !== options.total;
    clearButton.disabled = locked || Object.keys(matches).length === 0;
    drawLines();
  }

  function showCorrectness() {
    if (!correctPairIds.length && !attempts) {
      return;
    }
    leftItems.forEach(function markLeft(left) {
      var leftId = left.getAttribute('data-matching-left');
      if (!matches[leftId]) {
        return;
      }
      var isCorrect = correctPairIds.indexOf(leftId) !== -1;
      var right = findRight(matches[leftId]);
      left.classList.add(isCorrect ? 'is-correct' : 'is-incorrect');
      if (right) {
        right.classList.add(isCorrect ? 'is-correct' : 'is-incorrect');
      }
    });
  }

  function selectLeft(item) {
    if (locked) {
      return;
    }
    selectedLeft = item.getAttribute('data-matching-left');
    clearResultStyles();
    setFeedback(message('selectLeft'));
    updateItems();
  }

  function connect(right) {
    if (locked || !selectedLeft) {
      return;
    }
    var rightId = right.getAttribute('data-matching-right');
    Object.keys(matches).forEach(function enforceOneToOne(leftId) {
      if (matches[leftId] === rightId) {
        delete matches[leftId];
      }
    });
    matches[selectedLeft] = rightId;
    var left = findLeft(selectedLeft);
    setFeedback(message('connected', { left: itemText(left), right: itemText(right) }));
    selectedLeft = null;
    clearResultStyles();
    updateItems();
  }

  function postSubmission() {
    if (Object.keys(matches).length !== options.total) {
      setFeedback(message('completeAll'));
      return;
    }
    submitButton.disabled = true;
    var request = window.jQuery.ajax({
      type: 'POST',
      url: runtime.handlerUrl(element, 'submit'),
      data: JSON.stringify({ matches: matches }),
      contentType: 'application/json',
      dataType: 'json',
    });
    request.done(function submitted(response) {
      if (response.result !== 'success') {
        setFeedback(response.message || message('requestFailed'), 'error');
        updateItems();
        return;
      }
      correctPairIds = response.correctPairIds || [];
      attempts = response.attempts;
      locked = Boolean(response.locked);
      showCorrectness();
      setAttempts(response.attempts);
      setProgress(response.score, response.maxScore);
      setFeedback(response.status === 'correct'
        ? message('correct')
        : message('incorrect', { correct: response.correct, total: response.total }), response.status);
      updateItems();
    });
    request.fail(function submissionFailed() {
      setFeedback(message('requestFailed'), 'error');
      updateItems();
    });
  }

  leftItems.forEach(function bindLeft(item) {
    item.addEventListener('click', function leftClicked() {
      selectLeft(item);
    });
    endpoint(item).addEventListener('pointerdown', function dragStarted(event) {
      if (locked) {
        return;
      }
      event.preventDefault();
      selectLeft(item);
      dragging = item.getAttribute('data-matching-left');
      var boardRect = board.getBoundingClientRect();
      pointerPosition = { x: event.clientX - boardRect.left, y: event.clientY - boardRect.top };
      drawLines();
    });
  });

  rightItems.forEach(function bindRight(item) {
    item.addEventListener('click', function rightClicked() {
      connect(item);
    });
  });

  document.addEventListener('pointermove', function draggingPointer(event) {
    if (!dragging) {
      return;
    }
    var boardRect = board.getBoundingClientRect();
    pointerPosition = { x: event.clientX - boardRect.left, y: event.clientY - boardRect.top };
    drawLines();
  });

  document.addEventListener('pointerup', function dragFinished(event) {
    if (!dragging) {
      return;
    }
    var target = document.elementFromPoint(event.clientX, event.clientY);
    var right = target && target.closest ? target.closest('[data-matching-right]') : null;
    dragging = null;
    pointerPosition = null;
    if (right && root.contains(right)) {
      connect(right);
    } else {
      drawLines();
    }
  });

  submitButton.addEventListener('click', postSubmission);
  clearButton.addEventListener('click', function clearConnections() {
    if (locked) {
      return;
    }
    matches = {};
    selectedLeft = null;
    clearResultStyles();
    setFeedback('');
    updateItems();
  });

  if (window.ResizeObserver) {
    var resizeObserver = new window.ResizeObserver(drawLines);
    resizeObserver.observe(board);
  } else {
    window.addEventListener('resize', drawLines);
  }

  setAttempts(attempts);
  setProgress(options.score, options.maxScore);
  updateItems();
  showCorrectness();
  if (locked && correctPairIds.length !== options.total) {
    setFeedback(message('locked'), 'error');
  }
  return {};
};

