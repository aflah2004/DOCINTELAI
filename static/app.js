(() => {
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const escapeHtml = (value = '') => { const d = document.createElement('div'); d.textContent = value; return d.innerHTML; };

  // Icons
  const refreshIcons = () => { if (window.lucide) lucide.createIcons(); };
  refreshIcons();

  // Theme persistence
  const savedTheme = localStorage.getItem('docintel-theme') || 'dark';
  document.documentElement.dataset.theme = savedTheme;
  const markTheme = () => $$('.theme-option').forEach(b => b.classList.toggle('active', b.dataset.setTheme === document.documentElement.dataset.theme));
  markTheme();
  const themeToggle = $('#theme-toggle');
  themeToggle?.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next; localStorage.setItem('docintel-theme', next); markTheme();
  });
  $$('[data-set-theme]').forEach(btn => btn.addEventListener('click', () => {
    const next = btn.dataset.setTheme; document.documentElement.dataset.theme = next; localStorage.setItem('docintel-theme', next); markTheme();
  }));

  // Mobile navigation
  const sidebar = $('#sidebar'), overlay = $('#mobile-overlay');
  const closeMenu = () => { sidebar?.classList.remove('open'); overlay?.classList.remove('open'); };
  $('#mobile-menu')?.addEventListener('click', () => { sidebar?.classList.add('open'); overlay?.classList.add('open'); });
  overlay?.addEventListener('click', closeMenu);
  $$('.nav-link').forEach(a => a.addEventListener('click', closeMenu));

  // Global document search: useful on Documents page, otherwise route there with a query.
  const globalSearch = $('#global-search');
  globalSearch?.addEventListener('keydown', e => {
    if (e.key === 'Enter' && globalSearch.value.trim()) {
      window.location.href = `/documents?q=${encodeURIComponent(globalSearch.value.trim())}`;
    }
  });

  // -------------------------------------------------------------------------
  // Ask AI
  // -------------------------------------------------------------------------
  const form = $('#ask-form'), input = $('#question-input'), clear = $('#clear-input'), log = $('#chat-log'), button = $('#ask-button');
  let latestSources = [];

  const markdownLite = (text = '') => {
    let safe = escapeHtml(text).replace(/\r/g, '');
    const lines = safe.split('\n'); let out = [], inList = false;
    const closeList = () => { if (inList) { out.push('</ul>'); inList = false; } };
    lines.forEach(line => {
      if (/^\s*[-*]\s+/.test(line)) {
        if (!inList) { out.push('<ul>'); inList = true; }
        out.push(`<li>${line.replace(/^\s*[-*]\s+/, '')}</li>`); return;
      }
      closeList();
      if (!line.trim()) { out.push('<div class="answer-spacer"></div>'); return; }
      let formatted = line.replace(/^#{1,4}\s+/, '<strong>').replace(/\s*$/, '</strong>');
      formatted = formatted.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      if (/^<strong>/.test(formatted) && !formatted.endsWith('</strong>')) formatted += '</strong>';
      out.push(`<p>${formatted}</p>`);
    });
    closeList(); return out.join('');
  };

  const renderSources = sources => {
    latestSources = Array.isArray(sources) ? sources : [];
    const body = $('#sources-body'), count = $('#source-count'); if (!body) return;
    count.textContent = `(${latestSources.length})`;
    if (!latestSources.length) {
      body.innerHTML = `<div class="sources-empty"><div class="empty-source-icon"><i data-lucide="file-search"></i></div><h3>No relevant sources</h3><p>Ask another question or try wording that matches the uploaded documents.</p></div>`;
      refreshIcons(); return;
    }
    body.innerHTML = latestSources.map((s, i) => {
      const pct = Math.max(0, Math.min(100, Math.round((Number(s.score) || 0) * 100)));
      return `<article class="source-card"><div class="source-card-head"><span class="source-file-icon"><i data-lucide="file-text"></i></span><div class="source-main"><strong title="${escapeHtml(s.filename)}">${escapeHtml(s.filename)}</strong><small>Page ${escapeHtml(String(s.page ?? '—'))}</small></div><span class="score">${pct}%</span></div><p class="source-excerpt">${escapeHtml(s.excerpt || s.text || '')}${(s.excerpt || s.text || '').length >= 280 ? '…' : ''}</p><button type="button" class="source-view" data-source-index="${i}">View relevant excerpt <i data-lucide="arrow-up-right"></i></button></article>`;
    }).join('');
    refreshIcons();
    $$('.source-view', body).forEach(btn => btn.addEventListener('click', () => openExcerpt(latestSources[Number(btn.dataset.sourceIndex)])));
  };

  $('#clear-sources')?.addEventListener('click', () => renderSources([]));

  const modal = $('#excerpt-modal');
  const openExcerpt = source => {
    if (!modal || !source) return;
    $('#excerpt-title').textContent = source.filename || 'Source excerpt';
    $('#excerpt-meta').textContent = `Page ${source.page ?? '—'} · Relevance ${Math.round((Number(source.score) || 0) * 100)}%`;
    $('#excerpt-text').textContent = source.text || source.excerpt || 'No excerpt available.';
    modal.classList.add('open'); modal.setAttribute('aria-hidden', 'false');
  };
  const closeExcerpt = () => { modal?.classList.remove('open'); modal?.setAttribute('aria-hidden', 'true'); };
  $$('[data-close-excerpt]').forEach(el => el.addEventListener('click', closeExcerpt));
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeExcerpt(); });

  clear?.addEventListener('click', () => { input.value = ''; input.focus(); });
  $$('.suggestions button').forEach(btn => btn.addEventListener('click', () => { input.value = btn.textContent.trim(); input.focus(); }));
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  async function typeAnswer(node, text) {
    // Type by small word groups for a smooth but fast effect.
    const words = String(text || '').split(/(\s+)/);
    let built = '';
    for (let i = 0; i < words.length; i++) {
      built += words[i];
      node.innerHTML = markdownLite(built) + '<span class="type-cursor"></span>';
      if (words[i].trim()) await sleep(12);
    }
    node.innerHTML = markdownLite(text);
  }

  if (form) form.addEventListener('submit', async e => {
    e.preventDefault();
    const question = input.value.trim(); if (!question || button.disabled) return;
    const welcome = $('#welcome-message'); welcome?.remove();
    const turn = document.createElement('div'); turn.className = 'chat-turn';
    turn.innerHTML = `<div class="user-bubble">${escapeHtml(question)}</div><div class="ai-bubble loading"><div class="ai-avatar small"><i data-lucide="sparkles"></i></div><div class="answer-content"><div class="answer-label">SEARCHING YOUR DOCUMENTS</div><div class="loading-line"><span class="typing"><i></i><i></i><i></i></span><span>Finding the most relevant passages…</span></div></div></div>`;
    log.appendChild(turn); refreshIcons(); log.scrollTop = log.scrollHeight;
    input.value = ''; input.disabled = true; button.disabled = true; button.innerHTML = '<span>Working…</span>';
    renderSources([]);
    try {
      const res = await fetch('/api/ask', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({question}) });
      const data = await res.json(); const answerBox = $('.ai-bubble', turn);
      answerBox.classList.remove('loading');
      renderSources(data.sources || []);
      if (!res.ok) {
        answerBox.classList.add('error-bubble'); answerBox.innerHTML = `<div class="ai-avatar small"><i data-lucide="triangle-alert"></i></div><div class="answer-content"><div class="answer-label">REQUEST ERROR</div><div class="answer-text"><p>${escapeHtml(data.error || 'Something went wrong. Please try again.')}</p></div></div>`;
      } else if (data.mode === 'llm_unavailable') {
        answerBox.classList.add('llm-warning'); answerBox.innerHTML = `<div class="ai-avatar small"><i data-lucide="cpu"></i></div><div class="answer-content"><div class="answer-label">DOCUMENTS FOUND · AI UNAVAILABLE</div><div class="answer-text"><p>${escapeHtml(data.answer)}</p></div><div class="answer-note"><strong>Quick fix:</strong> open Terminal and run <code>ollama list</code>. If ${escapeHtml(window.DOCINTEL_MODEL || 'llama3.2')} is missing, run <code>ollama pull ${escapeHtml(window.DOCINTEL_MODEL || 'llama3.2')}</code>. Then start Ollama with <code>ollama serve</code> and ask again.</div><a class="inline-help" href="/help">Open troubleshooting guide →</a></div>`;
      } else if (data.mode === 'no_match') {
        answerBox.classList.add('no-match'); answerBox.innerHTML = `<div class="ai-avatar small"><i data-lucide="search-x"></i></div><div class="answer-content"><div class="answer-label">NO RELEVANT EVIDENCE</div><div class="answer-text"><p>${escapeHtml(data.answer)}</p></div></div>`;
      } else {
        answerBox.innerHTML = `<div class="ai-avatar small"><i data-lucide="sparkles"></i></div><div class="answer-content"><div class="answer-label">DOCINTEL AI · GROUNDED ANSWER</div><div class="answer-text"></div></div>`;
        await typeAnswer($('.answer-text', answerBox), data.answer || 'No answer was returned.');
      }
      refreshIcons();
    } catch (err) {
      const answerBox = $('.ai-bubble', turn); answerBox.classList.remove('loading');
      answerBox.innerHTML = `<div class="ai-avatar small"><i data-lucide="wifi-off"></i></div><div class="answer-content"><div class="answer-label">CONNECTION ERROR</div><div class="answer-text"><p>DocIntel could not reach the application server. Make sure Flask is running and try again.</p></div></div>`; refreshIcons();
    } finally {
      input.disabled = false; button.disabled = false; button.innerHTML = 'Ask AI <i data-lucide="arrow-up-right"></i>'; refreshIcons(); input.focus(); log.scrollTop = log.scrollHeight;
    }
  });

  // -------------------------------------------------------------------------
  // Upload: show exactly which files were selected/dropped before submit.
  // -------------------------------------------------------------------------
  const dropzone = $('#dropzone'), fileInput = $('#files'), fileList = $('#file-list'), processButton = $('#process-button');
  if (dropzone && fileInput) {
    let selected = [];
    const formatSize = bytes => bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(0)} KB` : `${(bytes / 1024 / 1024).toFixed(2)} MB`;
    const syncInput = () => {
      const dt = new DataTransfer(); selected.forEach(f => dt.items.add(f)); fileInput.files = dt.files;
    };
    const renderFiles = () => {
      const head = $('#selection-head'), count = $('#selection-count'), title = $('#drop-title'), subtitle = $('#drop-subtitle');
      head.hidden = selected.length === 0; count.textContent = `${selected.length} file${selected.length === 1 ? '' : 's'} selected`;
      title.textContent = selected.length ? `${selected.length} PDF${selected.length === 1 ? '' : 's'} ready to upload` : 'Drop your PDFs here';
      subtitle.textContent = selected.length ? 'Your selected files are listed below. You can remove any file before processing.' : 'Drag and drop files into this area, or browse your computer.';
      fileList.innerHTML = selected.map((f,i) => `<div class="selected-file"><span class="pdf-icon"><i data-lucide="file-text"></i></span><div><strong title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</strong><small>${formatSize(f.size)}</small></div><span class="file-check"><i data-lucide="check"></i></span><button type="button" class="file-remove" data-file-index="${i}" aria-label="Remove ${escapeHtml(f.name)}"><i data-lucide="x"></i></button></div>`).join('');
      processButton.disabled = selected.length === 0;
      $('#process-label').textContent = selected.length ? `Process ${selected.length} document${selected.length === 1 ? '' : 's'}` : 'Process documents';
      refreshIcons();
      $$('.file-remove', fileList).forEach(btn => btn.addEventListener('click', () => { selected.splice(Number(btn.dataset.fileIndex), 1); syncInput(); renderFiles(); }));
    };
    const addFiles = files => {
      const valid = [...files].filter(f => f.type === 'application/pdf' || f.name.toLowerCase().endsWith('.pdf'));
      const invalid = [...files].filter(f => !(f.type === 'application/pdf' || f.name.toLowerCase().endsWith('.pdf')));
      valid.forEach(f => { if (!selected.some(x => x.name === f.name && x.size === f.size && x.lastModified === f.lastModified)) selected.push(f); });
      if (invalid.length) alert(`${invalid.length} file${invalid.length === 1 ? '' : 's'} skipped. DocIntel accepts PDF files only.`);
      const total = selected.reduce((n,f) => n + f.size, 0);
      if (total > 25 * 1024 * 1024) alert('The selected files exceed the 25 MB request limit. Remove a file before processing.');
      syncInput(); renderFiles();
    };
    fileInput.addEventListener('change', () => addFiles(fileInput.files));
    ['dragenter','dragover'].forEach(ev => dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.add('drag-over'); }));
    ['dragleave','drop'].forEach(ev => dropzone.addEventListener(ev, e => { e.preventDefault(); dropzone.classList.remove('drag-over'); }));
    dropzone.addEventListener('drop', e => addFiles(e.dataTransfer.files));
    $('#clear-files')?.addEventListener('click', () => { selected = []; syncInput(); renderFiles(); });
    $('#upload-form')?.addEventListener('submit', () => { if (!selected.length) return; processButton.disabled = true; $('#upload-progress').hidden = false; $('#progress-label').textContent = `Processing ${selected.length} document${selected.length === 1 ? '' : 's'}…`; });
  }

  // Help Center filtering
  const helpSearch = $('#help-search');
  helpSearch?.addEventListener('input', () => {
    const term = helpSearch.value.toLowerCase().trim();
    $$('.faq').forEach(faq => { faq.hidden = !!term && !faq.textContent.toLowerCase().includes(term); });
  });

  // Health status: clearly show if the local model is actually reachable.
  const status = $('#model-status');
  if (status) fetch('/api/health').then(r => r.json()).then(h => {
    status.innerHTML = `<span class="online-dot" style="background:${h.llm_available ? 'var(--accent)' : 'var(--warning)'}"></span> Local LLM · ${escapeHtml(h.model)} · ${h.llm_available ? 'Ready' : 'Offline'}`;
  }).catch(() => {});
})();
