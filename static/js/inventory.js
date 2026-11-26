let currentUploadTab = 'gdrive';
let selectedFile = null;
let isUploading = false;

function switchUploadTab(tab) {
    currentUploadTab = tab;
    const gdriveBtn = document.getElementById('tabBtnGdrive');
    const fileBtn = document.getElementById('tabBtnFile');
    const gdrivePanel = document.getElementById('gdriveTabPanel');
    const filePanel = document.getElementById('fileTabPanel');

    if (tab === 'gdrive') {
        gdriveBtn.classList.add('border-[#10B981]', 'text-[#10B981]');
        gdriveBtn.classList.remove('border-transparent', 'text-[#94A3B8]');
        fileBtn.classList.remove('border-[#10B981]', 'text-[#10B981]');
        fileBtn.classList.add('border-transparent', 'text-[#94A3B8]');
        gdrivePanel.classList.remove('hidden');
        filePanel.classList.add('hidden');
        handleGdriveInputChanged();
    } else {
        fileBtn.classList.add('border-[#10B981]', 'text-[#10B981]');
        fileBtn.classList.remove('border-transparent', 'text-[#94A3B8]');
        gdriveBtn.classList.remove('border-[#10B981]', 'text-[#10B981]');
        gdriveBtn.classList.add('border-transparent', 'text-[#94A3B8]');
        filePanel.classList.remove('hidden');
        gdrivePanel.classList.add('hidden');
        if (selectedFile) {
            document.getElementById('btnStartUpload').removeAttribute('disabled');
        } else {
            document.getElementById('btnStartUpload').setAttribute('disabled', 'true');
        }
    }
}

function handleGdriveInputChanged() {
    if (isUploading) return;
    const val = (document.getElementById('gdriveUrlInput') ? document.getElementById('gdriveUrlInput').value : '').trim();
    const btn = document.getElementById('btnStartUpload');
    if (currentUploadTab === 'gdrive') {
        if (val.length > 0) {
            btn.removeAttribute('disabled');
        } else {
            btn.setAttribute('disabled', 'true');
        }
    }
}

function openUploadModal() {
    document.getElementById('uploadModal').classList.remove('hidden');
    switchUploadTab(currentUploadTab);
}

function closeUploadModal() {
    document.getElementById('uploadModal').classList.add('hidden');
    clearSelectedFile();
    document.getElementById('uploadProgressContainer').classList.add('hidden');
}

// Quick Sync Drive
async function triggerQuickGdriveSync() {
    if (isUploading) return;
    const btn = document.getElementById('btnQuickGdriveSync');
    const origHtml = btn ? btn.innerHTML : '';
    if (btn) {
        btn.setAttribute('disabled', 'true');
        btn.innerHTML = '<span class="inline-block animate-spin mr-1">⟳</span> Syncing Drive...';
    }

    try {
        openUploadModal();
        switchUploadTab('gdrive');
        await submitUpload();
    } finally {
        if (btn) {
            btn.removeAttribute('disabled');
            btn.innerHTML = origHtml;
        }
    }
}

function openErrorModal(errors, summaryText) {
    const modal = document.getElementById('errorModal');
    const tbody = document.getElementById('errorTableBody');
    const summary = document.getElementById('errorModalSummary');

    if (summaryText) summary.innerText = summaryText;

    tbody.innerHTML = (errors || []).map(err => `
        <tr class="hover:bg-[#1B2230]/40">
            <td class="p-2 text-center font-bold text-[#FF3358]">${err.row || '?'}</td>
            <td class="p-2 text-white font-sans">${escapeHtml(err.error || 'Parsing error')}</td>
            <td class="p-2 text-[10px] text-[#64748B] font-mono break-all">${escapeHtml(err.snippet || '')}</td>
        </tr>
    `).join('');

    modal.classList.remove('hidden');
}

function closeErrorModal() {
    document.getElementById('errorModal').classList.add('hidden');
    if (window.needsReloadOnModalClose) {
        window.needsReloadOnModalClose = false;
        window.location.reload();
    }
}

// Drag and drop handlers
function handleDragOver(e) {
    e.preventDefault();
    document.getElementById('dropZone').classList.add('border-[#10B981]', 'bg-[#10B981]/10');
}

function handleDragLeave(e) {
    e.preventDefault();
    document.getElementById('dropZone').classList.remove('border-[#10B981]', 'bg-[#10B981]/10');
}

function handleFileDrop(e) {
    e.preventDefault();
    document.getElementById('dropZone').classList.remove('border-[#10B981]', 'bg-[#10B981]/10');
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        processSelectedFile(e.dataTransfer.files[0]);
    }
}

function handleFileSelect(e) {
    if (e.target.files && e.target.files.length > 0) {
        processSelectedFile(e.target.files[0]);
    }
}

function processSelectedFile(file) {
    if (!file.name.toLowerCase().endsWith('.csv')) {
        showToast("Please select a valid .CSV file.", "error");
        return;
    }
    selectedFile = file;
    document.getElementById('selectedFileName').innerText = file.name;
    document.getElementById('selectedFileSize').innerText = `(${(file.size / 1024).toFixed(1)} KB)`;
    document.getElementById('selectedFileInfo').classList.remove('hidden');
    document.getElementById('btnStartUpload').removeAttribute('disabled');
}

function clearSelectedFile(e) {
    if (e) e.stopPropagation();
    selectedFile = null;
    const finput = document.getElementById('csvFileInput');
    if (finput) finput.value = "";
    document.getElementById('selectedFileInfo').classList.add('hidden');
    if (currentUploadTab === 'file') {
        document.getElementById('btnStartUpload').setAttribute('disabled', 'true');
    }
}

// Ingestion Submission (Google Drive or Local File)
async function submitUpload() {
    if (isUploading) return;
    isUploading = true;
    const btn = document.getElementById('btnStartUpload');
    const mode = document.querySelector('input[name="uploadMode"]:checked').value;
    const progressContainer = document.getElementById('uploadProgressContainer');
    const progressBar = document.getElementById('uploadProgressBar');
    const percentText = document.getElementById('uploadPercentText');
    const statusText = document.getElementById('uploadStatusText');

    btn.setAttribute('disabled', 'true');
    progressContainer.classList.remove('hidden');
    progressBar.style.width = "20%";
    percentText.innerText = "20%";

    try {
        let resp;
        if (currentUploadTab === 'gdrive') {
            const gdriveUrl = document.getElementById('gdriveUrlInput').value.trim();
            if (!gdriveUrl) {
                showToast("Please provide a Google Drive share link.", "error");
                btn.removeAttribute('disabled');
                progressContainer.classList.add('hidden');
                return;
            }

            statusText.innerText = "Connecting & downloading CSV from Google Drive...";
            progressBar.style.width = "40%";
            percentText.innerText = "40%";

            resp = await fetch("/api/inventory/upload", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ gdrive_url: gdriveUrl, mode: mode })
            });

        } else {
            if (!selectedFile) {
                showToast("Please select a .CSV file to upload.", "error");
                btn.removeAttribute('disabled');
                progressContainer.classList.add('hidden');
                return;
            }

            statusText.innerText = "Reading CSV & resolving Scryfall metadata...";
            progressBar.style.width = "50%";
            percentText.innerText = "50%";

            const formData = new FormData();
            formData.append("file", selectedFile);
            formData.append("mode", mode);

            resp = await fetch("/api/inventory/upload", {
                method: "POST",
                body: formData,
            });
        }

        progressBar.style.width = "90%";
        percentText.innerText = "90%";
        statusText.innerText = "Finalizing collection database records...";

        // Parse response safely - handle both JSON and potential HTML error pages
        let data = null;
        const contentType = resp.headers.get("content-type") || "";
        if (contentType.includes("application/json")) {
            try {
                data = await resp.json();
            } catch (jsonErr) {
                data = null;
            }
        }

        if (!data) {
            const rawText = await resp.text();
            let errorMsg = `Server returned HTTP ${resp.status}`;
            if (rawText.includes("<title>") && rawText.includes("</title>")) {
                const match = rawText.match(/<title>([^<]+)<\/title>/i);
                if (match) errorMsg = match[1].trim();
            } else if (rawText && !rawText.trim().startsWith("<")) {
                errorMsg = rawText.trim().slice(0, 160);
            }
            closeUploadModal();
            showToast(`Sync Failed: ${errorMsg}`, "error");
            return;
        }

        if (!resp.ok || data.error) {
            closeUploadModal();
            if (data.errors && data.errors.length > 0) {
                openErrorModal(data.errors, `Ingestion Warning: ${data.error}`);
            } else {
                showToast(`Sync Failed: ${data.error || 'Server error'}`, "error");
            }
            return;
        }

        progressBar.style.width = "100%";
        percentText.innerText = "100%";
        statusText.innerText = "Processing complete!";

        closeUploadModal();
        showToast(`Synced ${data.added_count + data.updated_count} cards into collection vault.`, "success");

        if (data.errors && data.errors.length > 0) {
            window.needsReloadOnModalClose = true;
            openErrorModal(data.errors, `Imported ${data.added_count + data.updated_count} cards with ${data.errors.length} malformed rows skipped. Review the skipped rows below, then dismiss to view your updated vault:`);
        } else {
            // Reload page to display new collection state
            setTimeout(() => {
                window.location.reload();
            }, 500);
        }

    } catch (err) {
        closeUploadModal();
        showToast(`Ingestion error: ${err.message}`, "error");
    } finally {
        isUploading = false;
        btn.removeAttribute('disabled');
        progressContainer.classList.add('hidden');
    }
}

// Tactical Confirmation Modal Helper
let pendingInventoryConfirmAction = null;

function openInventoryConfirmModal(title, message, onConfirm) {
    const modal = document.getElementById('inventoryConfirmModal');
    const titleEl = document.getElementById('inventoryConfirmTitle');
    const msgEl = document.getElementById('inventoryConfirmMessage');
    const execBtn = document.getElementById('btnInventoryConfirmExecute');
    if (!modal) return;

    if (titleEl) titleEl.innerText = title;
    if (msgEl) msgEl.innerText = message;
    pendingInventoryConfirmAction = onConfirm;

    if (execBtn) {
        execBtn.onclick = async () => {
            const fn = pendingInventoryConfirmAction;
            closeInventoryConfirmModal();
            if (typeof fn === 'function') await fn();
        };
    }

    modal.classList.remove('hidden');
}

function closeInventoryConfirmModal() {
    const modal = document.getElementById('inventoryConfirmModal');
    if (modal) modal.classList.add('hidden');
    pendingInventoryConfirmAction = null;
}

// Card Deletion
function deleteCard(cardId, cardName) {
    openInventoryConfirmModal(
        "Remove Card From Collection",
        `Permanently remove '${cardName}' from your collection binder?`,
        async () => {
            try {
                const resp = await fetch(`/api/inventory/card/${cardId}`, { method: "DELETE" });
                const contentType = resp.headers.get("content-type") || "";
                let data = null;
                if (contentType.includes("application/json")) {
                    data = await resp.json();
                }
                if (data && data.success) {
                    showToast(`Removed '${cardName}' from collection.`, "success");
                    loadCardsPage(currentPage);
                } else {
                    showToast((data && data.error) || `Failed to remove card (HTTP ${resp.status}).`, "error");
                }
            } catch (err) {
                showToast(`Error deleting card: ${err.message}`, "error");
            }
        }
    );
}

// Clear Inventory
function confirmClearInventory() {
    openInventoryConfirmModal(
        "Purge Entire Collection Vault",
        "WARNING: This will permanently delete ALL cards from your collection inventory. Do you wish to proceed?",
        async () => {
            try {
                const resp = await fetch("/api/inventory/clear", { method: "POST" });
                const contentType = resp.headers.get("content-type") || "";
                let data = null;
                if (contentType.includes("application/json")) {
                    data = await resp.json();
                }
                if (data && data.success) {
                    showToast("Collection vault purged.", "success");
                    setTimeout(() => window.location.reload(), 400);
                } else {
                    showToast((data && data.error) || `Failed to clear collection (HTTP ${resp.status}).`, "error");
                }
            } catch (err) {
                showToast(`Error clearing inventory: ${err.message}`, "error");
            }
        }
    );
}

// =========================================================================
// Server-Side Paginated Table & Real-Time Filtering
// =========================================================================
let currentPage = 1;
let currentPerPage = 50;
let currentTotalPages = 1;
let currentTotalCount = 0;
let searchDebounceTimer = null;
let isLoadingCards = false;

function handleSearchInput() {
    const val = document.getElementById('inventorySearch')?.value || '';
    const clearBtn = document.getElementById('btnClearInventorySearch');
    if (clearBtn) {
        if (val.length > 0) clearBtn.classList.remove('hidden');
        else clearBtn.classList.add('hidden');
    }
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(() => {
        currentPage = 1;
        loadCardsPage(1);
    }, 300);
}

function clearInventorySearch() {
    const input = document.getElementById('inventorySearch');
    if (input) {
        input.value = '';
        handleSearchInput();
        input.focus();
    }
}

function onFilterChanged() {
    currentPage = 1;
    loadCardsPage(1);
}

function changePerPage(val) {
    currentPerPage = parseInt(val, 10) || 50;
    currentPage = 1;
    loadCardsPage(1);
}

function goToPage(page) {
    if (page < 1 || page > currentTotalPages || page === currentPage || isLoadingCards) return;
    loadCardsPage(page);
}

function resetInventoryFilters() {
    document.getElementById('inventorySearch').value = '';
    document.getElementById('filterAllocation').value = 'all';
    document.getElementById('filterFoil').value = 'all';
    document.getElementById('filterSort').value = 'name_asc';
    currentPage = 1;
    loadCardsPage(1);
}

function toScryfallThumb(url) {
    if (!url || typeof url !== 'string') return url || '';
    if (url.includes('cards.scryfall.io/normal/')) return url.replace('cards.scryfall.io/normal/', 'cards.scryfall.io/small/');
    if (url.includes('cards.scryfall.io/large/')) return url.replace('cards.scryfall.io/large/', 'cards.scryfall.io/small/');
    return url;
}

async function loadCardsPage(page = 1) {
    if (isLoadingCards) return;
    isLoadingCards = true;
    currentPage = page;

    const tbody = document.getElementById('inventoryTableBody');
    const mobileList = document.getElementById('inventoryMobileList');
    const resultsText = document.getElementById('resultsCountText');
    const prevBtn = document.getElementById('btnPrevPage');
    const nextBtn = document.getElementById('btnNextPage');
    const pageContainer = document.getElementById('pageNumbersContainer');

    const q = (document.getElementById('inventorySearch').value || '').trim();
    const allocation = document.getElementById('filterAllocation').value;
    const foil = document.getElementById('filterFoil').value;
    const sort = document.getElementById('filterSort').value;

    // Show loading state
    tbody.innerHTML = `
        <tr id="loadingTablePlaceholder">
            <td colspan="9" class="py-12 text-center text-[#94A3B8]">
                <div class="flex items-center justify-center space-x-2 text-xs font-mono text-[#10B981]">
                    <span class="inline-block animate-spin text-base">⟳</span>
                    <span>Accessing collection vault...</span>
                </div>
            </td>
        </tr>
    `;
    if (mobileList) {
        mobileList.innerHTML = `
            <div class="py-10 text-center text-[#94A3B8]">
                <div class="flex items-center justify-center space-x-2 text-xs font-mono text-[#10B981]">
                    <span class="inline-block animate-spin text-base">⟳</span>
                    <span>Accessing collection vault...</span>
                </div>
            </div>
        `;
    }

    try {
        const params = new URLSearchParams({
            page: currentPage,
            per_page: currentPerPage,
            sort: sort,
            allocation: allocation,
            foil: foil,
        });
        if (q) params.append('q', q);

        const resp = await fetch(`/api/inventory/cards?${params.toString()}`);
        if (!resp.ok) {
            throw new Error(`Server returned HTTP ${resp.status}`);
        }
        const data = await resp.json();

        if (!data.success) {
            throw new Error(data.error || 'Failed to load cards');
        }

        currentTotalCount = data.total_count || 0;
        currentTotalPages = data.total_pages || 1;
        currentPage = data.page || 1;

        if (!data.cards || data.cards.length === 0) {
            const emptyHtml = `
                <div class="max-w-md mx-auto space-y-3 py-10 px-4 text-center text-[#94A3B8]">
                    <div class="text-3xl">📦</div>
                    <div class="font-heading font-bold text-white text-base">${q || allocation !== 'all' || foil !== 'all' ? 'No Matching Cards' : 'Your Inventory Vault is Empty'}</div>
                    <p class="text-xs text-[#64748B]">${q || allocation !== 'all' || foil !== 'all' ? 'No cards match your filter criteria.' : 'Upload your ManaBox collection CSV export to track physical inventory and power zero-cost swaps.'}</p>
                    ${q || allocation !== 'all' || foil !== 'all' 
                        ? '<button onclick="resetInventoryFilters()" class="mt-2 px-4 py-2 bg-[#1B2230] hover:bg-[#222B3D] border border-[#263245] text-white text-xs font-mono font-bold uppercase transition">Reset Filters</button>' 
                        : '<button onclick="openUploadModal()" class="mt-2 px-4 py-2 bg-[#10B981] hover:bg-[#059669] text-slate-950 text-xs font-mono font-bold uppercase transition">Upload ManaBox CSV</button>'
                    }
                </div>
            `;
            tbody.innerHTML = `<tr id="emptyTablePlaceholder"><td colspan="9">${emptyHtml}</td></tr>`;
            if (mobileList) mobileList.innerHTML = emptyHtml;
            resultsText.innerText = 'Showing 0 of 0 cards';
            prevBtn.disabled = true;
            nextBtn.disabled = true;
            pageContainer.innerHTML = '';
            return;
        }

        // Render card rows for Desktop Table and Mobile Compact Card List
        tbody.innerHTML = data.cards.map(card => renderCardRow(card)).join('');
        if (mobileList) {
            mobileList.innerHTML = data.cards.map(card => renderMobileCardRow(card)).join('');
        }

        // Update result text & buttons
        const startIdx = (currentPage - 1) * currentPerPage + 1;
        const endIdx = Math.min(currentPage * currentPerPage, currentTotalCount);
        resultsText.innerText = `Showing ${startIdx.toLocaleString()}–${endIdx.toLocaleString()} of ${currentTotalCount.toLocaleString()} cards`;

        prevBtn.disabled = (currentPage <= 1);
        nextBtn.disabled = (currentPage >= currentTotalPages);

        renderPaginationButtons(currentPage, currentTotalPages);

    } catch (err) {
        const errMsg = `
            <div class="py-8 text-center text-[#FF3358] font-mono text-xs">
                Failed to load cards: ${escapeHtml(err.message)}
                <button onclick="loadCardsPage(currentPage)" class="ml-2 underline text-[#10B981]">Retry</button>
            </div>
        `;
        tbody.innerHTML = `<tr><td colspan="9">${errMsg}</td></tr>`;
        if (mobileList) mobileList.innerHTML = errMsg;
    } finally {
        isLoadingCards = false;
    }
}

function renderPaginationButtons(current, total) {
    const container = document.getElementById('pageNumbersContainer');
    if (!container) return;

    if (total <= 1) {
        container.innerHTML = '';
        return;
    }

    const pages = [];
    const delta = 2;

    for (let i = 1; i <= total; i++) {
        if (i === 1 || i === total || (i >= current - delta && i <= current + delta)) {
            pages.push(i);
        } else if (pages[pages.length - 1] !== '...') {
            pages.push('...');
        }
    }

    container.innerHTML = pages.map(p => {
        if (p === '...') {
            return `<span class="px-2 text-[#64748B]">...</span>`;
        }
        if (p === current) {
            return `<button class="min-w-[38px] min-h-[38px] px-2.5 py-1 inline-flex items-center justify-center bg-[#10B981] text-slate-950 font-bold border border-[#10B981]">${p}</button>`;
        }
        return `<button onclick="goToPage(${p})" class="min-w-[38px] min-h-[38px] px-2.5 py-1 inline-flex items-center justify-center bg-[#1B2230] hover:bg-[#222B3D] border border-[#263245] text-[#94A3B8] hover:text-white transition">${p}</button>`;
    }).join('');
}

function renderMobileCardRow(card) {
    const effectivePrice = card.effective_price ? `$${card.effective_price.toFixed(2)}` : '--';
    const totalVal = card.total_value ? `$${card.total_value.toFixed(2)}` : '$0.00';
    const isAllocated = !!card.is_allocated;
    const allocatedDecks = card.allocated_decks || [];
    const thumbUri = toScryfallThumb(card.image_uri);

    let finishBadge = '';
    if (card.foil && (card.foil.toLowerCase() === 'foil' || card.foil.toLowerCase() === 'etched')) {
        finishBadge = `<span class="px-1 py-0.2 bg-[#F59E0B]/20 text-[#F59E0B] border border-[#F59E0B]/40 text-[9px] font-bold uppercase">★ ${escapeHtml(card.foil)}</span>`;
    }

    const isOrderedBinder = (card.binder_name || '').toLowerCase() === 'ordered';
    const allocBadge = isOrderedBinder
        ? `<span class="text-[9px] px-1.5 py-0.2 bg-[#F59E0B]/20 text-[#F59E0B] border border-[#F59E0B]/50 uppercase font-bold">🚚 Ordered</span>`
        : (isAllocated
            ? `<span class="text-[9px] px-1.5 py-0.2 bg-[#C084FC]/20 text-[#C084FC] border border-[#C084FC]/40 uppercase font-bold">In ${allocatedDecks.length} Deck${allocatedDecks.length > 1 ? 's' : ''}</span>`
            : `<span class="text-[9px] px-1.5 py-0.2 bg-[#10B981]/15 text-[#10B981] border border-[#10B981]/30 font-bold uppercase">Free</span>`);

    const previewAttrs = card.image_uri ? `
        onclick="openCardImageOverlay(this)"
        data-card-image="${escapeHtml(card.image_uri)}"
        data-card-name="${escapeHtml(card.name)}"
        data-card-set="${escapeHtml((card.set_code || '').toUpperCase())}"
        data-card-number="${escapeHtml(card.collector_number || '')}"
        data-card-finish="${escapeHtml(card.foil || 'nonfoil')}"
    ` : '';

    return `
        <div class="p-3 flex items-center justify-between gap-2.5 hover:bg-[#222B3D]/60 transition compact-card">
            <div class="flex items-center space-x-2.5 min-w-0 flex-1">
                ${card.image_uri ? `
                    <div class="w-10 h-14 bg-black border border-[#263245] overflow-hidden flex-shrink-0 cursor-pointer" ${previewAttrs}>
                        <img src="${escapeHtml(thumbUri)}" alt="${escapeHtml(card.name)}" loading="lazy" decoding="async" class="w-full h-full object-cover card-thumbnail-interactive">
                    </div>
                ` : `
                    <div class="w-10 h-14 bg-[#10141D] border border-[#263245] flex items-center justify-center text-[9px] text-[#64748B] flex-shrink-0">NO ART</div>
                `}
                <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-1.5">
                        <span class="bg-[#10141D] border border-[#263245] px-1.5 py-0.2 text-[10px] font-bold text-[#10B981] flex-shrink-0">${card.quantity}x</span>
                        <span class="font-bold text-white text-xs truncate ${card.image_uri ? 'cursor-pointer hover:text-[#00CED1]' : ''}" ${previewAttrs}>${escapeHtml(card.name)}</span>
                    </div>
                    <div class="flex flex-wrap items-center gap-1.5 mt-1 text-[10px]">
                        <span class="px-1 py-0.2 bg-[#10141D] border border-[#263245] text-[#00CED1] font-bold uppercase">${escapeHtml(card.set_code || '---')}</span>
                        <span class="text-[#64748B]">#${escapeHtml(card.collector_number || '?')}</span>
                        ${finishBadge}
                        ${allocBadge}
                    </div>
                </div>
            </div>
            <div class="flex items-center gap-2 flex-shrink-0">
                <div class="text-right">
                    <div class="text-xs font-bold text-[#10B981]">${totalVal}</div>
                    <div class="text-[10px] text-[#64748B]">${effectivePrice} ea</div>
                </div>
                <button onclick="deleteCard(${card.id}, '${escapeHtml(card.name)}')" 
                        class="p-1.5 hover:bg-[#DC143C]/20 text-[#64748B] hover:text-[#FF3358] border border-transparent hover:border-[#DC143C]/50 transition" title="Delete card">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="square" stroke-linejoin="miter" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path>
                    </svg>
                </button>
            </div>
        </div>
    `;
}

function renderCardRow(card) {
    const effectivePrice = card.effective_price ? `$${card.effective_price.toFixed(2)}` : '--';
    const totalVal = card.total_value ? `$${card.total_value.toFixed(2)}` : '$0.00';
    const isAllocated = !!card.is_allocated;
    const allocatedDecks = card.allocated_decks || [];
    const thumbUri = toScryfallThumb(card.image_uri);

    let finishHtml = '<span class="text-[#64748B] text-[10px]">Nonfoil</span>';
    if (card.foil && (card.foil.toLowerCase() === 'foil' || card.foil.toLowerCase() === 'etched')) {
        finishHtml = `<span class="px-1.5 py-0.5 bg-[#F59E0B]/20 text-[#F59E0B] border border-[#F59E0B]/40 text-[9px] font-bold uppercase">★ ${escapeHtml(card.foil)}</span>`;
    }

    let allocHtml = '';
    const isOrderedBinder = (card.binder_name || '').toLowerCase() === 'ordered';
    if (isOrderedBinder) {
        allocHtml = `
            <span class="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 bg-[#F59E0B]/20 text-[#F59E0B] border border-[#F59E0B]/50 uppercase font-bold">
                <span>🚚 Ordered (In Transit)</span>
            </span>
        `;
    } else if (isAllocated) {
        const deckNames = allocatedDecks.map(d => escapeHtml(d.deck_name)).join(', ');
        allocHtml = `
            <div class="space-y-0.5">
                <span class="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 bg-[#C084FC]/20 text-[#C084FC] border border-[#C084FC]/40 uppercase font-bold">
                    <span>In ${allocatedDecks.length} Deck${allocatedDecks.length > 1 ? 's' : ''}</span>
                </span>
                <div class="text-[9px] text-[#64748B] truncate max-w-[200px]" title="${deckNames}">
                    ${deckNames}
                </div>
            </div>
        `;
    } else {
        allocHtml = `
            <span class="text-[10px] px-1.5 py-0.5 bg-[#10B981]/15 text-[#10B981] border border-[#10B981]/30 font-bold uppercase">
                Free in Binder
            </span>
        `;
    }

    const previewAttrs = card.image_uri ? `
        onclick="openCardImageOverlay(this)"
        data-card-image="${escapeHtml(card.image_uri)}"
        data-card-name="${escapeHtml(card.name)}"
        data-card-set="${escapeHtml((card.set_code || '').toUpperCase())}"
        data-card-number="${escapeHtml(card.collector_number || '')}"
        data-card-finish="${escapeHtml(card.foil || 'nonfoil')}"
    ` : '';

    const imgHtml = card.image_uri ? `
        <div class="w-9 h-12 bg-black border border-[#263245] overflow-hidden flex-shrink-0 group relative cursor-pointer"
             ${previewAttrs}
             title="Click to inspect ${escapeHtml(card.name)}">
            <img src="${escapeHtml(thumbUri)}" alt="${escapeHtml(card.name)}" class="w-full h-full object-cover card-thumbnail-interactive" loading="lazy" decoding="async">
            <div class="absolute inset-0 bg-[#00CED1]/20 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center pointer-events-none">
                <svg class="w-3.5 h-3.5 text-white drop-shadow" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="square" stroke-linejoin="miter" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0zM10 7v6m3-3H7"></path>
                </svg>
            </div>
        </div>
    ` : '';

    const binderMeta = card.binder_name ? `
        <span>&bull;</span>
        <span class="text-slate-400">📁 ${escapeHtml(card.binder_name)}</span>
    ` : '';

    return `
        <tr class="hover:bg-[#222B3D] transition inventory-row">
            <td class="py-3 px-4 text-center font-black text-white text-sm">
                <span class="bg-[#10141D] border border-[#263245] px-2 py-0.5 text-xs font-bold text-[#10B981]">
                    ${card.quantity}x
                </span>
            </td>
            <td class="py-3 px-4">
                <div class="flex items-center space-x-3">
                    ${imgHtml}
                    <div>
                        <div class="font-bold text-white hover:text-[#00CED1] transition ${card.image_uri ? 'cursor-pointer' : ''}" ${previewAttrs}>
                            ${escapeHtml(card.name)}
                        </div>
                        <div class="text-[10px] text-[#64748B] flex items-center gap-2 mt-0.5">
                            <span>#${escapeHtml(card.collector_number || '?')}</span>
                            <span>&bull;</span>
                            <span>${escapeHtml(card.condition || 'NM')}</span>
                            ${binderMeta}
                        </div>
                    </div>
                </div>
            </td>
            <td class="py-3 px-3">
                <span class="px-1.5 py-0.5 bg-[#10141D] border border-[#263245] text-[#00CED1] font-bold text-[10px] uppercase">
                    ${escapeHtml(card.set_code || '---')}
                </span>
            </td>
            <td class="py-3 px-3">
                ${finishHtml}
            </td>
            <td class="py-3 px-3 text-[#94A3B8] text-xs">
                ${escapeHtml(card.type_line || 'Card')}
            </td>
            <td class="py-3 px-3">
                ${allocHtml}
            </td>
            <td class="py-3 px-3 text-right text-slate-300">
                ${effectivePrice}
            </td>
            <td class="py-3 px-3 text-right font-bold text-[#10B981]">
                ${totalVal}
            </td>
            <td class="py-3 px-4 text-center">
                <button onclick="deleteCard(${card.id}, '${escapeHtml(card.name)}')" 
                        class="p-1 hover:bg-[#DC143C]/20 text-[#64748B] hover:text-[#FF3358] border border-transparent hover:border-[#DC143C]/50 transition" title="Delete card">
                    <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="square" stroke-linejoin="miter" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path>
                    </svg>
                </button>
            </td>
        </tr>
    `;
}

function escapeHtml(str) {
    if (!str) return '';
    return str.toString()
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function filterToOrderedBinder() {
    const sel = document.getElementById('filterAllocation');
    if (sel) {
        sel.value = 'ordered';
        onFilterChanged();
    }
}

async function loadInventoryActiveOrdersBanner() {
    const banner = document.getElementById('inventoryActiveOrdersBanner');
    const textEl = document.getElementById('inventoryActiveOrdersText');
    if (!banner || !textEl) return;
    try {
        const res = await fetch('/api/orders/tcgplayer');
        if (!res.ok) return;
        const data = await res.json();
        const activeCount = data.active_orders_count || 0;
        const cardsCount = data.in_transit_cards_count || 0;
        if (activeCount > 0) {
            textEl.textContent = `${activeCount} Active TCGplayer Order${activeCount === 1 ? '' : 's'} (${cardsCount} card${cardsCount === 1 ? '' : 's'} in transit)`;
            banner.classList.remove('hidden');
        } else {
            banner.classList.add('hidden');
        }
    } catch (e) {
        console.debug('Could not load active TCGplayer orders banner:', e);
    }
}

// Initial load on page ready (default to 25 per page on mobile viewports for faster initial render)
document.addEventListener('DOMContentLoaded', () => {
    if (window.innerWidth < 640) {
        currentPerPage = 25;
        const sel = document.getElementById('selectPerPage');
        if (sel) sel.value = '25';
    }
    const urlParams = new URLSearchParams(window.location.search);
    const initialQ = urlParams.get('q');
    if (initialQ) {
        const input = document.getElementById('inventorySearch');
        if (input) {
            input.value = initialQ;
            const clearBtn = document.getElementById('btnClearInventorySearch');
            if (clearBtn) clearBtn.classList.remove('hidden');
        }
    }
    loadCardsPage(1);
    loadInventoryActiveOrdersBanner();
});
