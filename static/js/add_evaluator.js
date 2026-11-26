// State
const _addEvalInit = window.__ADD_EVAL_INIT__ || {};
let currentInputMode = _addEvalInit.initialMode || 'batch';
let currentResultTab = 'card-matrix';
let activeSuiteData = null;
let lastEvaluationPayload = null;
let activeBreakdownDeckId = null;
let autocompleteDebounce = null;

document.addEventListener('DOMContentLoaded', () => {
    switchInputMode(currentInputMode === 'secret_lair' ? 'secret_lair' : (currentInputMode === 'single' ? 'single' : 'batch'));
    initSingleCardAutocomplete();
});

function switchInputMode(mode) {
    currentInputMode = mode;
    ['single', 'batch', 'secret_lair'].forEach(m => {
        const tab = document.getElementById(`tab-input-${m}`);
        const sec = document.getElementById(`section-input-${m}`);
        if (m === mode) {
            tab.className = mode === 'secret_lair' 
                ? 'px-3 py-1 bg-[#A855F7] text-white font-bold transition'
                : 'px-3 py-1 bg-[#00CED1] text-slate-950 font-bold transition';
            sec.classList.remove('hidden');
        } else {
            tab.className = 'px-3 py-1 text-[#94A3B8] hover:text-white transition';
            sec.classList.add('hidden');
        }
    });

    const deployText = document.getElementById('btn-deploy-text');
    if (deployText) {
        if (mode === 'secret_lair') {
            deployText.innerText = 'Deploy Secret Lair Intel';
        } else if (mode === 'single') {
            deployText.innerText = 'Evaluate Single Card';
        } else {
            deployText.innerText = 'Deploy Batch Evaluation';
        }
    }
}

function updateDeckCounter() {
    const checked = document.querySelectorAll('input[name="target_deck_ids"]:checked');
    const counter = document.getElementById('selected-decks-counter');
    if (counter) counter.innerText = `(${checked.length} Selected)`;
}

function toggleAllDecks(select) {
    document.querySelectorAll('input[name="target_deck_ids"]').forEach(cb => cb.checked = select);
    updateDeckCounter();
}

function clearBatchInput() {
    const el = document.getElementById('input-batch-cards');
    if (el) el.value = '';
}

function fillExampleStaples() {
    const el = document.getElementById('input-batch-cards');
    if (el) {
        el.value = [
            '1 Cyclonic Rift',
            '1 Esper Sentinel',
            '1 Smothering Tithe',
            '1 Deflecting Swat',
            '1 Heroic Intervention',
            '1 Roaming Throne',
            '1 Trouble in Pairs',
            '1 Toxic Deluge',
            '1 Jeska\'s Will',
            '1 Demonic Tutor'
        ].join('\n');
    }
}

function fillExampleSecretLairUrl() {
    const el = document.getElementById('input-sl-url');
    if (el) {
        el.value = 'https://magic.wizards.com/en/news/announcements/secret-lair-masters-of-the-universe-superdrop';
    }
}

// Single Card Autocomplete
function initSingleCardAutocomplete() {
    const input = document.getElementById('input-single-card');
    const dropdown = document.getElementById('single-card-autocomplete');
    if (!input || !dropdown) return;

    input.addEventListener('input', (e) => {
        const q = e.target.value.trim();
        clearTimeout(autocompleteDebounce);
        if (q.length < 2) {
            dropdown.classList.add('hidden');
            dropdown.innerHTML = '';
            return;
        }

        autocompleteDebounce = setTimeout(async () => {
            try {
                const res = await fetch(`/api/scryfall/autocomplete?q=${encodeURIComponent(q)}`);
                const data = await res.json();
                const suggestions = Array.isArray(data) ? data : (data.suggestions || []);
                if (suggestions && suggestions.length > 0) {
                    dropdown.innerHTML = suggestions.slice(0, 8).map(name => {
                        const safeName = (name || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
                        return `
                            <div data-card-name="${safeName}" class="p-2 hover:bg-[#10141D] cursor-pointer text-white flex items-center justify-between single-card-item">
                                <span>${safeName}</span>
                                <span class="text-[10px] text-[#00CED1] font-bold">&rarr; Select</span>
                            </div>
                        `;
                    }).join('');
                    dropdown.classList.remove('hidden');
                } else {
                    dropdown.classList.add('hidden');
                }
            } catch (err) {
                dropdown.classList.add('hidden');
            }
        }, 220);
    });

    dropdown.addEventListener('click', (e) => {
        const item = e.target.closest('.single-card-item');
        if (item) {
            const cardName = item.getAttribute('data-card-name');
            if (cardName) selectSingleCard(cardName);
        }
    });

    let singleCardHighlightIdx = -1;

    function updateSingleCardHighlight() {
        const items = Array.from(dropdown.querySelectorAll('.single-card-item'));
        items.forEach((el, idx) => {
            if (idx === singleCardHighlightIdx) {
                el.classList.add('bg-[#10141D]', 'text-[#00CED1]');
                el.scrollIntoView({ block: 'nearest' });
            } else {
                el.classList.remove('bg-[#10141D]', 'text-[#00CED1]');
            }
        });
    }

    input.addEventListener('input', () => {
        singleCardHighlightIdx = -1;
    });

    input.addEventListener('keydown', (e) => {
        const isDropdownVisible = !dropdown.classList.contains('hidden');
        const items = isDropdownVisible ? Array.from(dropdown.querySelectorAll('.single-card-item')) : [];

        if (e.key === 'ArrowDown' && items.length > 0) {
            e.preventDefault();
            singleCardHighlightIdx = (singleCardHighlightIdx + 1) % items.length;
            updateSingleCardHighlight();
            return;
        }
        if (e.key === 'ArrowUp' && items.length > 0) {
            e.preventDefault();
            singleCardHighlightIdx = (singleCardHighlightIdx - 1 + items.length) % items.length;
            updateSingleCardHighlight();
            return;
        }
        if (e.key === 'Enter') {
            e.preventDefault();
            if (isDropdownVisible && singleCardHighlightIdx >= 0 && singleCardHighlightIdx < items.length) {
                const cardName = items[singleCardHighlightIdx].getAttribute('data-card-name');
                if (cardName) selectSingleCard(cardName);
                return;
            }
            const first = dropdown.querySelector('.single-card-item');
            if (first && isDropdownVisible) {
                const cardName = first.getAttribute('data-card-name');
                if (cardName) selectSingleCard(cardName);
            } else if (input.value.trim().length >= 2) {
                selectSingleCard(input.value.trim());
            }
        }
    });

    document.addEventListener('click', (e) => {
        if (!input.contains(e.target) && !dropdown.contains(e.target)) {
            dropdown.classList.add('hidden');
        }
    });
}

async function selectSingleCard(cardName) {
    const input = document.getElementById('input-single-card');
    const dropdown = document.getElementById('single-card-autocomplete');
    if (input) input.value = cardName;
    if (dropdown) dropdown.classList.add('hidden');

    // Fetch brief metadata for preview badge
    try {
        const res = await fetch(`/api/scryfall/prints?name=${encodeURIComponent(cardName)}`);
        const data = await res.json();
        if (data && data.prints && data.prints.length > 0) {
            const p = data.prints[0];
            const badge = document.getElementById('single-card-preview-badge');
            document.getElementById('single-card-name').innerText = p.name || cardName;
            document.getElementById('single-card-mana').innerText = p.mana_cost || '';
            document.getElementById('single-card-type').innerText = p.type_line || '';
            document.getElementById('single-card-price').innerText = p.prices && p.prices.usd ? `$${p.prices.usd}` : 'N/A';
            const img = document.getElementById('single-card-img');
            if (img && (p.small_image_uri || p.image_uri)) {
                img.src = p.small_image_uri || p.image_uri;
            }
            if (badge) badge.classList.remove('hidden');
        }
    } catch (e) {}
}

// Execution Controller
async function runAddEvaluation() {
    const btn = document.getElementById('btn-run-evaluation');
    const btnText = document.getElementById('btn-deploy-text');
    const telemetry = document.getElementById('telemetry-banner');
    const resultsContainer = document.getElementById('results-container');

    const selectedDeckIds = Array.from(document.querySelectorAll('input[name="target_deck_ids"]:checked')).map(cb => cb.value);
    if (selectedDeckIds.length === 0) {
        showToast('Please select at least one Commander deck to evaluate against.', 'error');
        return;
    }

    const offline = Boolean(document.getElementById('chk-tw-offline')?.checked);

    let payload = {
        deck_ids: selectedDeckIds,
        offline: offline,
    };

    if (currentInputMode === 'secret_lair') {
        payload.mode = 'secret_lair';
        payload.url = document.getElementById('input-sl-url').value.trim();
        payload.raw_text = document.getElementById('input-sl-text').value.trim();
        if (!payload.url && !payload.raw_text) {
            showToast('Please enter a Secret Lair announcement URL or paste text.', 'error');
            return;
        }
    } else if (currentInputMode === 'single') {
        payload.mode = 'cards';
        const singleCard = document.getElementById('input-single-card').value.trim();
        if (!singleCard) {
            showToast('Please enter a Magic card name to evaluate.', 'error');
            return;
        }
        payload.cards = [singleCard];
    } else {
        payload.mode = 'cards';
        const batchText = document.getElementById('input-batch-cards').value.trim();
        if (!batchText) {
            showToast('Please paste at least one card name.', 'error');
            return;
        }
        payload.raw_text = batchText;
    }

    // Telemetry UI activation
    btn.disabled = true;
    btnText.innerText = 'Evaluating Fleet...';
    telemetry.classList.remove('hidden');
    resultsContainer.classList.add('hidden');

    let seconds = 0;
    const timerInterval = setInterval(() => {
        seconds++;
        const t = document.getElementById('telemetry-timer');
        if (t) t.innerText = `${seconds}s elapsed`;
    }, 1000);

    const updateStep = (num, text) => {
        const st = document.getElementById('telemetry-status-text');
        if (st) st.innerText = `SURVEILLANCE: ${text.toUpperCase()}`;
        for (let i = 1; i <= 4; i++) {
            const el = document.getElementById(`step-${i}`);
            if (i < num) {
                el.className = 'p-2 bg-[#10141D] border border-[#10B981] text-[#10B981] flex items-center space-x-2';
            } else if (i === num) {
                el.className = 'p-2 bg-[#10141D] border border-[#00CED1] text-white flex items-center space-x-2 animate-pulse';
            } else {
                el.className = 'p-2 bg-[#10141D] border border-[#263245] text-[#64748B] flex items-center space-x-2';
            }
        }
    };

    updateStep(1, 'Normalizing card targets...');
    setTimeout(() => updateStep(2, 'Querying Scryfall market & color telemetry...'), 600);
    setTimeout(() => updateStep(3, 'Cross-referencing EDHREC inclusion & Spellbook combos...'), 1200);
    setTimeout(() => updateStep(4, 'Computing Timewalked ratios & data-driven cuts...'), 2000);

    try {
        const resp = await fetch('/api/add-evaluator/evaluate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });

        const data = await resp.json();
        clearInterval(timerInterval);
        telemetry.classList.add('hidden');
        btn.disabled = false;
        btnText.innerText = currentInputMode === 'secret_lair' ? 'Deploy Secret Lair Intel' : 'Deploy Add Evaluation';

        if (!resp.ok || data.error) {
            alert(`Evaluation Error: ${data.error || 'Failed to analyze additions.'}`);
            return;
        }

        activeSuiteData = data;
        lastEvaluationPayload = payload;
        renderResults(data);

    } catch (err) {
        clearInterval(timerInterval);
        telemetry.classList.add('hidden');
        btn.disabled = false;
        btnText.innerText = 'Deploy Add Evaluation';
        alert(`Communication error: ${err.message}`);
    }
}

// Explicit User Action: Run Gemini Tactical Deep-Dive on Current Results
async function triggerGeminiDeepDiveOnResults() {
    if (!activeSuiteData) return;
    const rerunBtn = document.getElementById('btn-rerun-gemini');
    const rerunText = document.getElementById('btn-rerun-gemini-text');
    const origText = rerunText ? rerunText.innerText : 'Run Gemini AI Deep-Dive';

    if (rerunBtn) {
        rerunBtn.disabled = true;
        rerunText.innerText = 'Analyzing with Gemini...';
    }

    try {
        let payload = null;
        if (lastEvaluationPayload) {
            payload = Object.assign({}, lastEvaluationPayload);
        } else {
            const selectedDeckIds = Array.from(document.querySelectorAll('input[name="target_deck_ids"]:checked')).map(cb => cb.value);
            payload = {
                deck_ids: selectedDeckIds.length > 0 ? selectedDeckIds : undefined,
                mode: activeSuiteData.mode || 'cards',
            };
            if (activeSuiteData.mode === 'secret_lair') {
                payload.raw_text = activeSuiteData.title || '';
            } else if (activeSuiteData.cards && activeSuiteData.cards.length > 0) {
                payload.cards = activeSuiteData.cards.map(c => c.canonical_name || c.name);
            }
        }

        const modelSelect = document.getElementById('select-gemini-model');
        payload.model = modelSelect ? modelSelect.value : undefined;
        payload.use_gemini = true;
        payload.save_to_history = true;

        const resp = await fetch('/api/add-evaluator/evaluate', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });

        const data = await resp.json();
        if (!resp.ok || data.error) {
            alert(`Gemini Analysis Error: ${data.error || 'Failed to complete AI deep-dive.'}`);
            return;
        }

        activeSuiteData = data;
        lastEvaluationPayload = payload;
        renderResults(data);
    } catch (err) {
        alert(`Communication error during Gemini analysis: ${err.message}`);
    } finally {
        if (rerunBtn) {
            rerunBtn.disabled = false;
            rerunText.innerText = origText;
        }
    }
}

// Results Renderer
function renderResults(data) {
    const container = document.getElementById('results-container');
    container.classList.remove('hidden');

    document.getElementById('result-title').innerText = data.title || (data.mode === 'secret_lair' ? 'Secret Lair Superdrop Intelligence' : 'Card Add Fleet Evaluation');
    document.getElementById('result-exec-summary').innerText = data.executive_summary || 'Tactical analysis completed.';
    const modelBadge = document.getElementById('badge-model-used');
    if (modelBadge) {
        modelBadge.innerText = 'TIMEWALKED METHODOLOGY';
        modelBadge.className = 'bg-[#10B981]/20 border border-[#10B981] text-[#10B981] text-[10px] font-mono px-2 py-0.5 font-bold uppercase';
    }

    const savedBadge = document.getElementById('badge-saved-indicator');
    if (savedBadge) {
        if (data.id) {
            savedBadge.classList.remove('hidden');
        } else {
            savedBadge.classList.add('hidden');
        }
    }

    document.getElementById('badge-mode').innerText = `MODE: ${(data.mode || 'CARDS').toUpperCase()}`;

    const evaluatedCount = data.evaluated_cards_count || (data.cards ? data.cards.length : (data.drops ? data.drops.length : 0));
    document.getElementById('badge-cards-count').innerText = `${evaluatedCount} ${data.mode === 'secret_lair' ? 'DROPS' : 'CARDS'} EVALUATED`;

    // Banner image
    const bImg = document.getElementById('result-banner-img');
    const bFall = document.getElementById('result-banner-fallback');
    if (data.banner_image) {
        bImg.src = data.banner_image;
        bImg.classList.remove('hidden');
        bFall.classList.add('hidden');
    } else {
        bImg.classList.add('hidden');
        bFall.classList.remove('hidden');
    }

    // Secret Lair Bundle Card
    const bundleCard = document.getElementById('result-bundle-card');
    const slTabBtn = document.getElementById('tab-btn-sl-ranked');
    if (data.mode === 'secret_lair' && data.bundle_analysis) {
        bundleCard.classList.remove('hidden');
        document.getElementById('bundle-verdict-title').innerText = data.bundle_analysis.verdict || 'Bundle Analyzed';
        document.getElementById('bundle-verdict-rationale').innerText = data.bundle_analysis.rationale || '';
        slTabBtn.classList.remove('hidden');
        document.getElementById('tab-sl-drop-count').innerText = (data.drops ? data.drops.length : 0);
        renderSecretLairDrops(data.best_drops_to_buy || [], data.drops || []);
    } else {
        bundleCard.classList.add('hidden');
        slTabBtn.classList.add('hidden');
    }

    // Render TAB 1: Card-to-Decks Matrix
    const cardMatrix = data.card_matrix || [];
    document.getElementById('tab-matrix-card-count').innerText = cardMatrix.length;
    renderCardMatrix(cardMatrix);

    // Render TAB 2: Deck-by-Deck Breakdown
    const deckBreakdowns = data.deck_breakdowns || [];
    document.getElementById('tab-deck-count').innerText = deckBreakdowns.length;
    renderDeckBreakdowns(deckBreakdowns);

    // Render TAB 4: New Commander Opportunities
    const cmdrList = data.new_commander_opportunities || [];
    document.getElementById('tab-cmdr-count').innerText = cmdrList.length;
    renderNewCommanders(cmdrList);

    // Default view tab
    if (data.mode === 'secret_lair') {
        switchResultTab('sl-ranked');
    } else {
        switchResultTab('card-matrix');
    }

    // Scroll smoothly to results
    container.scrollIntoView({behavior: 'smooth'});
}

function switchResultTab(tabName) {
    currentResultTab = tabName;
    const tabs = ['card-matrix', 'deck-breakdown', 'sl-ranked', 'new-commanders'];
    tabs.forEach(t => {
        const btn = document.getElementById(`tab-btn-${t}`);
        const view = document.getElementById(`view-tab-${t}`);
        if (!btn || !view) return;

        if (t === tabName) {
            btn.className = 'px-4 py-2 bg-[#00CED1] text-slate-950 font-bold transition flex items-center space-x-1.5 whitespace-nowrap';
            view.classList.remove('hidden');
        } else {
            btn.className = 'px-4 py-2 text-[#94A3B8] hover:text-white transition flex items-center space-x-1.5 whitespace-nowrap';
            view.classList.add('hidden');
        }
    });
}

// -----------------------------------------------------------------------------
// TAB 1: Card-to-Decks Matrix View
// -----------------------------------------------------------------------------
function renderCardMatrix(cardMatrix) {
    const list = document.getElementById('card-matrix-list');
    if (!list) return;

    if (!cardMatrix || cardMatrix.length === 0) {
        list.innerHTML = '<div class="p-6 bg-[#1B2230] border border-[#263245] text-center text-xs text-[#64748B] font-mono">No card matrix records to display.</div>';
        return;
    }

    list.innerHTML = cardMatrix.map((item, idx) => {
        const cardName = item.card_name;
        const price = item.price_usd ? `$${item.price_usd.toFixed(2)}` : 'Market Price N/A';
        const typeLine = item.type_line || 'Card';
        const manaCost = item.mana_cost || '';
        const img = item.image_uri || item.art_crop_uri || '';
        const recs = item.deck_recommendations || [];
        const incomp = item.incompatible_decks || [];

        return `
            <div class="bg-[#1B2230] border border-[#263245] p-4 sm:p-5 shadow-xl space-y-4">
                <!-- Card Header -->
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-[#263245] pb-3">
                    <div class="flex items-center space-x-3">
                        <div class="w-10 h-10 rounded bg-[#10141D] border border-[#263245] overflow-hidden flex-shrink-0 flex items-center justify-center">
                            ${img ? `<img src="${typeof toScryfallThumb === 'function' ? toScryfallThumb(img) : img}" alt="${cardName}" loading="lazy" decoding="async" class="w-full h-full object-cover">` : `<span class="text-[10px] font-mono font-bold text-[#00CED1]">MTG</span>`}
                        </div>
                        <div>
                            <div class="text-sm font-bold text-white flex items-center gap-2 flex-wrap">
                                <span>${cardName}</span>
                                ${manaCost ? `<span class="text-xs font-mono text-[#94A3B8]">${manaCost}</span>` : ''}
                                ${item.from_drop ? `<span class="text-[10px] font-mono text-[#C084FC] px-1.5 py-0.2 bg-[#A855F7]/20 border border-[#A855F7]/50">[From: ${item.from_drop}]</span>` : ''}
                            </div>
                            <div class="text-[11px] text-[#94A3B8] flex items-center gap-2 flex-wrap mt-0.5">
                                <span>${typeLine}</span>
                                <span>&bull;</span>
                                <span class="text-[#00CED1] font-mono font-bold">${price}</span>
                                ${item.is_game_changer ? `<span class="text-[9px] font-mono px-1.5 py-0.2 bg-[#A855F7]/20 text-[#C084FC] border border-[#A855F7]/40 font-bold uppercase">GAME CHANGER</span>` : ''}
                                ${item.is_owned ? `<span class="text-[9px] font-mono px-1.5 py-0.2 bg-[#10B981]/20 text-[#10B981] border border-[#10B981]/40 font-bold uppercase">IN BINDER (${item.owned_qty}x)</span>` : ''}
                            </div>
                        </div>
                    </div>
                    <div class="flex items-center space-x-2">
                        <button data-card-name="${cardName.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;')}" onclick="quickAddToWatchlist(this.getAttribute('data-card-name'))" class="btn-bridge px-3 py-1.5 text-xs font-mono text-[#00CED1] flex items-center space-x-1" title="Track price on Watchlist">
                            <span>+ Watchlist</span>
                        </button>
                    </div>
                </div>

                <!-- Compatible Decks Leaderboard -->
                <div>
                    <div class="text-[10px] font-mono uppercase text-[#64748B] font-bold tracking-wider mb-2 flex items-center justify-between">
                        <span>FLEET FIT RANKING (${recs.length} COMPATIBLE DECKS):</span>
                        <span class="text-[#00CED1]">Ranked by Tactical Synergy</span>
                    </div>

                    ${recs.length > 0 ? `
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                            ${recs.map((rec, rIdx) => {
                                const verdictClass = rec.fit_verdict.includes('Essential') ? 'bg-[#10B981]/20 border-[#10B981] text-[#10B981]' :
                                                     rec.fit_verdict.includes('High') ? 'bg-[#00CED1]/20 border-[#00CED1] text-[#00CED1]' :
                                                     rec.fit_verdict.includes('Win-Con') ? 'bg-[#F59E0B]/20 border-[#F59E0B] text-[#F59E0B]' :
                                                     'bg-[#64748B]/20 border-[#64748B] text-[#94A3B8]';
                                return `
                                    <div class="p-3 bg-[#10141D] border border-[#263245] hover:border-[#00CED1] transition space-y-2 flex flex-col justify-between">
                                        <div>
                                            <div class="flex items-center justify-between gap-2">
                                                <div class="text-xs font-bold text-white truncate flex items-center space-x-1.5">
                                                    <span class="text-[10px] font-mono text-[#64748B]">#${rIdx + 1}</span>
                                                    <span>${rec.deck_name}</span>
                                                </div>
                                                <div class="flex items-center space-x-1 flex-shrink-0">
                                                    <span class="px-1.5 py-0.2 text-[9px] font-mono font-bold uppercase border ${verdictClass}">${rec.fit_verdict}</span>
                                                    <span class="px-1.5 py-0.2 text-[9px] font-mono font-bold bg-[#263245] text-white">${rec.synergy_rating.toFixed(1)}/10</span>
                                                </div>
                                            </div>
                                            <div class="text-[10px] text-[#94A3B8] mt-1">${rec.commander_name ? `Commander: ${rec.commander_name}` : ''}</div>
                                            
                                            ${rec.bracket_warning ? `
                                                <div class="text-[10px] font-mono text-[#F59E0B] bg-[#F59E0B]/10 border border-[#F59E0B]/40 px-2 py-0.5 mt-1.5 flex items-center gap-1">
                                                    <span>⚠️</span>
                                                    <span>${rec.bracket_warning}</span>
                                                </div>
                                            ` : ''}

                                            ${rec.reasons && rec.reasons.length > 0 ? `
                                                <div class="flex flex-wrap gap-1 mt-1.5">
                                                    ${rec.reasons.map(r => `
                                                        <span class="text-[9px] font-mono px-1.5 py-0.2 bg-[#10141D] border border-[#263245] text-[#94A3B8]">${r}</span>
                                                    `).join('')}
                                                </div>
                                            ` : ''}

                                            <p class="text-xs text-[#94A3B8] mt-1.5 leading-relaxed">${rec.rationale || 'Synergy fit for deck game plan.'}</p>
                                        </div>

                                        <div class="pt-2 border-t border-[#263245] flex items-center justify-between text-xs font-mono">
                                            <div class="text-[11px] text-[#FF3358] truncate max-w-[200px]" title="Recommended Cut: ${rec.suggested_cut} (${rec.cut_reason || ''})">
                                                Cut: <strong>${rec.suggested_cut}</strong>
                                                ${rec.cut_reason ? `<span class="text-[9px] text-[#64748B] block truncate">${rec.cut_reason}</span>` : ''}
                                            </div>
                                            <div class="flex items-center space-x-1.5 flex-shrink-0">
                                                <button onclick="applyEvaluatorSwap(${rec.deck_id}, '${rec.suggested_cut.replace(/'/g, "\\'")}', '${cardName.replace(/'/g, "\\'")}')" class="px-2 py-1 bg-[#00CED1]/20 hover:bg-[#00CED1] text-[#00CED1] hover:text-slate-950 font-bold border border-[#00CED1]/40 text-[10px] transition uppercase">
                                                    Apply Swap
                                                </button>
                                                <button onclick="directAddCardToDeck(${rec.deck_id}, '${cardName.replace(/'/g, "\\'")}')" class="px-2 py-1 bg-[#10141D] hover:bg-[#263245] text-[#94A3B8] hover:text-white border border-[#263245] text-[10px] transition uppercase" title="Add directly to deck">
                                                    + Add
                                                </button>
                                            </div>
                                        </div>
                                    </div>
                                `;
                            }).join('')}
                        </div>
                    ` : `
                        <div class="p-3 bg-[#10141D] border border-[#263245] text-xs text-[#64748B] font-mono">
                            No active decks in your fleet are compatible with this card's color identity or format legality.
                        </div>
                    `}
                </div>

                <!-- Incompatible Decks Drawer (Collapsible) -->
                ${incomp.length > 0 ? `
                    <details class="text-xs font-mono text-[#64748B] cursor-pointer pt-1">
                        <summary class="hover:text-white">${incomp.length} Ineligible Decks (Off-Color / Banned / Already Slotted)</summary>
                        <div class="mt-2 pl-3 space-y-1 text-[11px] border-l border-[#263245]">
                            ${incomp.map(inc => `
                                <div class="flex items-center justify-between text-[#94A3B8]">
                                    <span>${inc.deck_name}</span>
                                    <span class="text-[#DC143C]">${inc.reason}</span>
                                </div>
                            `).join('')}
                        </div>
                    </details>
                ` : ''}
            </div>
        `;
    }).join('');
}

// -----------------------------------------------------------------------------
// TAB 2: Deck-by-Deck Breakdown View
// -----------------------------------------------------------------------------
function renderDeckBreakdowns(deckBreakdowns) {
    const pills = document.getElementById('deck-breakdown-pills');
    if (!pills) return;

    if (!deckBreakdowns || deckBreakdowns.length === 0) {
        pills.innerHTML = '';
        document.getElementById('deck-breakdown-content').innerHTML = '<div class="p-6 bg-[#1B2230] border border-[#263245] text-center text-xs text-[#64748B] font-mono">No deck records found.</div>';
        return;
    }

    if (!activeBreakdownDeckId && deckBreakdowns.length > 0) {
        activeBreakdownDeckId = deckBreakdowns[0].deck_id;
    }

    pills.innerHTML = deckBreakdowns.map(d => {
        const isActive = d.deck_id === activeBreakdownDeckId;
        const count = d.applicable_cards ? d.applicable_cards.length : 0;
        return `
            <button onclick="selectBreakdownDeck(${d.deck_id})" class="px-3 py-1.5 text-xs font-mono font-bold whitespace-nowrap border flex items-center space-x-2 transition ${
                isActive ? 'bg-[#00CED1] text-slate-950 border-[#00CED1]' : 'bg-[#10141D] text-[#94A3B8] border-[#263245] hover:border-white'
            }">
                <span>${d.deck_name}</span>
                <span class="px-1 py-0.2 text-[9px] rounded ${isActive ? 'bg-black/30 text-white' : 'bg-[#263245] text-[#00CED1]'}">${count}</span>
            </button>
        `;
    }).join('');

    renderActiveBreakdownContent();
}

function selectBreakdownDeck(deckId) {
    activeBreakdownDeckId = deckId;
    renderDeckBreakdowns(activeSuiteData.deck_breakdowns || []);
}

function renderActiveBreakdownContent() {
    const container = document.getElementById('deck-breakdown-content');
    if (!container || !activeSuiteData || !activeSuiteData.deck_breakdowns) return;

    const deck = activeSuiteData.deck_breakdowns.find(d => d.deck_id === activeBreakdownDeckId);
    if (!deck) {
        container.innerHTML = '<div class="p-4 bg-[#10141D] border border-[#263245] text-xs text-[#64748B]">Select a deck above to inspect tactical additions.</div>';
        return;
    }

    const cards = deck.applicable_cards || [];
    container.innerHTML = `
        <div class="bg-[#1B2230] border border-[#263245] p-5 shadow-xl space-y-4">
            <div class="flex items-center justify-between border-b border-[#263245] pb-3">
                <div>
                    <div class="flex items-center gap-2 flex-wrap">
                        <h3 class="text-sm font-heading font-black text-white uppercase">${deck.deck_name}</h3>
                        ${deck.on_paper_bracket ? `
                            <span class="text-[10px] font-mono px-2 py-0.5 bg-[#00CED1]/20 border border-[#00CED1]/50 text-[#00CED1] font-bold">
                                Bracket ${deck.on_paper_bracket} (${deck.on_paper_label || 'Core'}) · Feels Like ${Number(deck.feels_like_score || 2.0).toFixed(1)}
                            </span>
                        ` : ''}
                    </div>
                    <div class="text-xs font-mono text-[#94A3B8] mt-1">
                        Commander: <span class="text-white">${deck.commander_name || 'Unspecified'}</span> &bull; 
                        CI: <span class="text-[#00CED1]">[${deck.color_identity.join('')}]</span> &bull; 
                        ${cards.length} Beneficial Additions Found
                    </div>
                </div>
            </div>

            <!-- Timewalked Deck Ratio Strip -->
            ${deck.ratios && Object.keys(deck.ratios).length > 0 ? `
                <div class="bg-[#10141D] p-3 border border-[#263245]">
                    <div class="text-[10px] font-mono text-[#94A3B8] uppercase font-bold tracking-wider mb-1.5">// TIMEWALKED CARD RATIOS</div>
                    <div class="grid grid-cols-3 sm:grid-cols-5 lg:grid-cols-9 gap-1.5">
                        ${Object.values(deck.ratios).map(r => {
                            let badgeCls = 'bg-[#10B981]/15 text-[#10B981] border-[#10B981]/40';
                            if (r.state === 'close') badgeCls = 'bg-[#F59E0B]/15 text-[#F59E0B] border-[#F59E0B]/40';
                            else if (r.state === 'short') badgeCls = 'bg-[#DC143C]/15 text-[#FF3358] border-[#DC143C]/40';
                            return `
                                <div class="p-1.5 bg-[#1B2230] border border-[#263245] text-center font-mono">
                                    <div class="text-[9px] text-[#94A3B8] truncate" title="${r.label}">${r.label}</div>
                                    <div class="text-[11px] font-bold text-white mt-0.5">${r.count}/${r.target}</div>
                                    <div class="text-[8px] font-bold uppercase px-1 py-0.2 border mt-1 ${badgeCls}">${r.state ? r.state.toUpperCase() : 'OK'}</div>
                                </div>
                            `;
                        }).join('')}
                    </div>
                </div>
            ` : ''}

            ${cards.length > 0 ? `
                <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                    ${cards.map(c => {
                        const verdictClass = c.fit_verdict.includes('Essential') ? 'bg-[#10B981]/20 border-[#10B981] text-[#10B981]' :
                                             c.fit_verdict.includes('High') ? 'bg-[#00CED1]/20 border-[#00CED1] text-[#00CED1]' :
                                             c.fit_verdict.includes('Win-Con') ? 'bg-[#F59E0B]/20 border-[#F59E0B] text-[#F59E0B]' :
                                             'bg-[#64748B]/20 border-[#64748B] text-[#94A3B8]';
                        return `
                            <div class="p-3 bg-[#10141D] border border-[#263245] hover:border-[#00CED1] transition space-y-2 flex flex-col justify-between">
                                <div>
                                    <div class="flex items-center justify-between gap-2">
                                        <div class="text-xs font-bold text-white flex items-center space-x-1.5">
                                            <span>${c.card_name}</span>
                                            ${c.mana_cost ? `<span class="text-[10px] font-mono text-[#94A3B8]">${c.mana_cost}</span>` : ''}
                                        </div>
                                        <div class="flex items-center space-x-1 flex-shrink-0">
                                            <span class="px-1.5 py-0.2 text-[9px] font-mono font-bold uppercase border ${verdictClass}">${c.fit_verdict}</span>
                                            <span class="px-1.5 py-0.2 text-[9px] font-mono font-bold bg-[#263245] text-white">${c.synergy_rating.toFixed(1)}/10</span>
                                        </div>
                                    </div>
                                    <div class="text-[10px] text-[#64748B] mt-0.5">${c.type_line || ''} ${c.price_usd ? `&bull; $${c.price_usd.toFixed(2)}` : ''}</div>
                                    ${c.bracket_warning ? `
                                        <div class="text-[10px] font-mono text-[#F59E0B] bg-[#F59E0B]/10 border border-[#F59E0B]/40 px-2 py-0.5 mt-1 flex items-center gap-1">
                                            <span>⚠️</span>
                                            <span>${c.bracket_warning}</span>
                                        </div>
                                    ` : ''}
                                    ${c.reasons && c.reasons.length > 0 ? `
                                        <div class="flex flex-wrap gap-1 mt-1">
                                            ${c.reasons.map(r => `
                                                <span class="text-[9px] font-mono px-1.5 py-0.2 bg-[#10141D] border border-[#263245] text-[#94A3B8]">${r}</span>
                                            `).join('')}
                                        </div>
                                    ` : ''}
                                    <p class="text-xs text-[#94A3B8] mt-1 leading-relaxed">${c.rationale || 'Tactical upgrade.'}</p>
                                </div>

                                <div class="pt-2 border-t border-[#263245] flex items-center justify-between text-xs font-mono">
                                    <div class="text-[11px] text-[#FF3358] truncate max-w-[200px]" title="Recommended Cut: ${c.suggested_cut} (${c.cut_reason || ''})">
                                        Cut: <strong>${c.suggested_cut}</strong>
                                        ${c.cut_reason ? `<span class="text-[9px] text-[#64748B] block truncate">${c.cut_reason}</span>` : ''}
                                    </div>
                                    <div class="flex items-center space-x-1.5">
                                        <button onclick="applyEvaluatorSwap(${deck.deck_id}, '${c.suggested_cut.replace(/'/g, "\\'")}', '${c.card_name.replace(/'/g, "\\'")}')" class="px-2 py-1 bg-[#00CED1]/20 hover:bg-[#00CED1] text-[#00CED1] hover:text-slate-950 font-bold border border-[#00CED1]/40 text-[10px] transition uppercase">
                                            Apply Swap
                                        </button>
                                        <button onclick="directAddCardToDeck(${deck.deck_id}, '${c.card_name.replace(/'/g, "\\'")}')" class="px-2 py-1 bg-[#10141D] hover:bg-[#263245] text-[#94A3B8] hover:text-white border border-[#263245] text-[10px] transition uppercase" title="Add directly to deck">
                                            + Add
                                        </button>
                                    </div>
                                </div>
                            </div>
                        `;
                    }).join('')}
                </div>
            ` : `
                <div class="p-6 text-center text-xs text-[#64748B] font-mono">
                    None of the evaluated cards are legal additions for this commander.
                </div>
            `}
        </div>
    `;
}

// -----------------------------------------------------------------------------
// TAB 3: Secret Lair Drops & EV
// -----------------------------------------------------------------------------
function renderSecretLairDrops(bestDrops, drops) {
    const container = document.getElementById('sl-ranked-drops-list');
    if (!container) return;

    if (!drops || drops.length === 0) {
        container.innerHTML = '<div class="p-6 text-center text-xs text-[#64748B] font-mono">No drop data available.</div>';
        return;
    }

    container.innerHTML = drops.map((d, idx) => {
        const rankInfo = bestDrops.find(b => b.drop_name.toLowerCase() === d.drop_name.toLowerCase()) || {};
        const tier = rankInfo.recommendation_tier || d.ev_status || 'Analyzed';
        const rank = rankInfo.rank || (idx + 1);
        const singlesVal = d.singles_value_nonfoil ? `$${d.singles_value_nonfoil.toFixed(2)}` : 'N/A';
        const priceNF = d.price_nonfoil ? `$${d.price_nonfoil.toFixed(2)}` : '$29.99';

        return `
            <div class="bg-[#1B2230] border border-[#263245] p-5 shadow-xl space-y-3">
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-[#263245] pb-3">
                    <div class="flex items-center space-x-3">
                        <span class="text-sm font-mono font-black text-[#A855F7]">#${rank}</span>
                        <div>
                            <h4 class="text-sm font-bold text-white uppercase">${d.drop_name}</h4>
                            <div class="text-[10px] font-mono text-[#94A3B8]">Retail: ${priceNF} &bull; TCG Singles Market: <strong class="text-white">${singlesVal}</strong></div>
                        </div>
                    </div>
                    <span class="px-2 py-0.5 text-xs font-mono font-bold uppercase bg-[#A855F7]/20 border border-[#A855F7] text-[#C084FC]">${tier}</span>
                </div>
                ${rankInfo.summary_rationale ? `<p class="text-xs text-[#94A3B8] leading-relaxed">${rankInfo.summary_rationale}</p>` : ''}
                <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2 pt-2">
                    ${(d.cards || []).map(c => `
                        <div class="p-2 bg-[#10141D] border border-[#263245] flex items-center space-x-2">
                            <div class="w-7 h-7 rounded bg-[#263245] overflow-hidden flex-shrink-0">
                                ${c.image_uri ? `<img src="${typeof toScryfallThumb === 'function' ? toScryfallThumb(c.image_uri) : c.image_uri}" alt="${c.canonical_name}" loading="lazy" decoding="async" class="w-full h-full object-cover">` : ''}
                            </div>
                            <div class="min-w-0 flex-1">
                                <div class="text-[11px] font-bold text-white truncate">${c.canonical_name}</div>
                                <div class="text-[9px] text-[#00CED1] font-mono">${c.price_usd ? `$${c.price_usd.toFixed(2)}` : 'N/A'}</div>
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }).join('');
}

// -----------------------------------------------------------------------------
// TAB 4: New Commander Opportunities
// -----------------------------------------------------------------------------
function renderNewCommanders(cmdrs) {
    const container = document.getElementById('new-commanders-grid');
    if (!container) return;

    if (!cmdrs || cmdrs.length === 0) {
        container.innerHTML = '<div class="col-span-full p-6 text-center text-xs text-[#64748B] font-mono">No new legendary commander helms detected in this card set.</div>';
        return;
    }

    container.innerHTML = cmdrs.map(c => `
        <div class="p-4 bg-[#1B2230] border border-[#263245] shadow-xl space-y-2">
            <div class="flex items-center space-x-3">
                <div class="w-10 h-10 rounded bg-[#10141D] border border-[#263245] overflow-hidden flex-shrink-0 flex items-center justify-center">
                    ${c.image_uri ? `<img src="${typeof toScryfallThumb === 'function' ? toScryfallThumb(c.image_uri) : c.image_uri}" alt="${c.card_name}" loading="lazy" decoding="async" class="w-full h-full object-cover">` : `<span class="text-[10px] font-mono font-bold text-[#A855F7]">EDH</span>`}
                </div>
                <div>
                    <h4 class="text-xs font-bold text-white uppercase">${c.card_name}</h4>
                    <div class="text-[10px] font-mono text-[#00CED1]">[${(c.colors || []).join('')}] &bull; ${c.archetype || 'Commander'}</div>
                </div>
            </div>
            <p class="text-xs text-[#94A3B8] leading-relaxed pt-1 border-t border-[#263245]">${c.rationale || 'Premier commander potential.'}</p>
        </div>
    `).join('');
}

// -----------------------------------------------------------------------------
// Actions: Apply Swap, Add Card, Add to Watchlist
// -----------------------------------------------------------------------------
async function applyEvaluatorSwap(deckId, cardOut, cardIn) {
    if (!confirm(`Apply tactical swap in deck?\n\nCUT: ${cardOut}\nADD: ${cardIn}\n\nThis will update your deck list and recalculate telemetry.`)) {
        return;
    }

    try {
        const resp = await fetch(`/api/deck/${deckId}/apply-swap`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({card_out: cardOut, card_in: cardIn})
        });

        const res = await resp.json();
        if (res.success) {
            alert(`✓ Swap Applied!\nReplaced '${cardOut}' with '${cardIn}' in deck.`);
        } else {
            alert(`Swap Error: ${res.error || 'Failed to apply swap.'}`);
        }
    } catch (e) {
        alert(`Communication error: ${e.message}`);
    }
}

async function directAddCardToDeck(deckId, cardName) {
    if (!confirm(`Directly add '${cardName}' to deck?`)) return;

    try {
        const resp = await fetch(`/api/deck/${deckId}/add-card`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({card_name: cardName})
        });

        const res = await resp.json();
        if (res.success) {
            alert(`✓ Added '${cardName}' to deck.`);
        } else {
            alert(`Add Error: ${res.error || 'Failed to add card.'}`);
        }
    } catch (e) {
        alert(`Communication error: ${e.message}`);
    }
}

async function quickAddToWatchlist(cardName) {
    const notify = (msg, type = 'info') => {
        if (typeof showToast === 'function') showToast(msg, type);
        else alert(msg);
    };
    try {
        const resp = await fetch('/api/watchlist/add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: cardName})
        });
        const text = await resp.text();
        let res = {};
        if (text) {
            try {
                res = JSON.parse(text);
            } catch (_) {
                res = {error: `Unexpected server response (HTTP ${resp.status})`};
            }
        }
        if (resp.ok || res.success) {
            notify(res.message || `✓ '${cardName}' added to your surveillance Watchlist.`, 'success');
        } else if (resp.status === 409) {
            notify(res.error || `'${cardName}' is already on your watchlist.`, 'info');
        } else {
            notify(res.error || 'Failed to add to watchlist.', 'error');
        }
    } catch (e) {
        notify(`Error: ${e.message}`, 'error');
    }
}

// History Modal
function openHistoryModal() {
    const m = document.getElementById('modal-history');
    if (m) m.classList.remove('hidden');
}

function closeHistoryModal() {
    const m = document.getElementById('modal-history');
    if (m) m.classList.add('hidden');
}

function switchHistoryTab(tab) {
    const htabCards = document.getElementById('htab-cards');
    const htabSl = document.getElementById('htab-sl');
    const hlistCards = document.getElementById('hlist-cards');
    const hlistSl = document.getElementById('hlist-sl');

    if (tab === 'cards') {
        htabCards.className = 'px-3 py-1 bg-[#00CED1] text-slate-950 font-bold';
        htabSl.className = 'px-3 py-1 text-[#94A3B8] hover:text-white';
        hlistCards.classList.remove('hidden');
        hlistSl.classList.add('hidden');
    } else {
        htabCards.className = 'px-3 py-1 text-[#94A3B8] hover:text-white';
        htabSl.className = 'px-3 py-1 bg-[#A855F7] text-white font-bold';
        hlistCards.classList.add('hidden');
        hlistSl.classList.remove('hidden');
    }
}

async function loadSavedAnalysis(type, id) {
    closeHistoryModal();
    const endpoint = type === 'cards' ? `/api/add-evaluator/history/${id}` : `/api/secret-lair/history/${id}`;
    try {
        const resp = await fetch(endpoint);
        const data = await resp.json();
        if (data.analysis) {
            const suitePayload = data.analysis;
            suitePayload.id = data.id;
            suitePayload.title = data.title;
            suitePayload.banner_image = data.banner_image;
            suitePayload.drops = data.drops;
            suitePayload.mode = data.source_type === 'secret_lair' ? 'secret_lair' : (type === 'sl' ? 'secret_lair' : 'cards');
            activeSuiteData = suitePayload;
            
            // Reconstruct lastEvaluationPayload from saved metadata
            const deckIds = data.target_deck_ids ? data.target_deck_ids.split(',').map(s => s.trim()).filter(Boolean) : [];
            lastEvaluationPayload = {
                deck_ids: deckIds,
                mode: suitePayload.mode,
                model: data.model_used,
            };
            if (suitePayload.mode === 'secret_lair') {
                lastEvaluationPayload.raw_text = data.input_text || suitePayload.title || '';
            } else if (data.cards && data.cards.length > 0) {
                lastEvaluationPayload.cards = data.cards.map(c => c.canonical_name || c.name);
            } else if (data.input_text) {
                lastEvaluationPayload.raw_text = data.input_text;
            }

            renderResults(suitePayload);
        }
    } catch (e) {
        alert(`Error loading analysis: ${e.message}`);
    }
}

async function deleteSavedAnalysis(type, id, btnEl) {
    if (!confirm('Permanently delete this saved analysis?')) return;
    const endpoint = type === 'cards' ? `/api/add-evaluator/history/${id}` : `/api/secret-lair/history/${id}`;
    try {
        const resp = await fetch(endpoint, {method: 'DELETE'});
        const res = await resp.json();
        if (res.success) {
            const row = document.getElementById(`history-row-${type}-${id}`) || (btnEl && btnEl.closest('[data-history-row]'));
            if (row) row.remove();
            const cntEl = document.getElementById(type === 'cards' ? 'hcount-cards' : 'hcount-sl');
            if (cntEl) {
                const cur = parseInt(cntEl.textContent, 10) || 0;
                cntEl.textContent = String(Math.max(0, cur - 1));
            }
            if (typeof showToast === 'function') showToast('Analysis removed from history.', 'success');
        } else if (typeof showToast === 'function') {
            showToast(res.error || 'Failed to delete analysis.', 'error');
        }
    } catch (e) {
        if (typeof showToast === 'function') {
            showToast(`Error: ${e.message}`, 'error');
        } else {
            alert(`Error: ${e.message}`);
        }
    }
}
