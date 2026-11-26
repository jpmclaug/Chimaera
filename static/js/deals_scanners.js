// =========================================================================
// TCGplayer Seller Lot Finder (Consolidation & Budget Optimizer)
// =========================================================================
let currentLotFinderMode = "lots";
let currentSellerLots = [];

function switchDealsSection(section) {
    const singleContainer = document.getElementById("deals-single-container");
    const lotsContainer = document.getElementById("deals-lot-finder-container");
    const sealedContainer = document.getElementById("deals-sealed-finder-container");
    const retailersContainer = document.getElementById("deals-retailers-container");
    const btnSingle = document.getElementById("btn-deals-sec-single");
    const btnLots = document.getElementById("btn-deals-sec-lots");
    const btnSealed = document.getElementById("btn-deals-sec-sealed");
    const btnRetailers = document.getElementById("btn-deals-sec-retailers");

    if (!singleContainer || !lotsContainer) return;

    const inactiveCls = "py-2 px-3 text-xs font-mono font-bold uppercase transition flex items-center justify-center space-x-2 text-[#94A3B8] hover:text-white hover:bg-[#10141D]";

    singleContainer.classList.add("hidden");
    lotsContainer.classList.add("hidden");
    if (sealedContainer) sealedContainer.classList.add("hidden");
    if (retailersContainer) retailersContainer.classList.add("hidden");
    if (btnSingle) btnSingle.className = inactiveCls;
    if (btnLots) btnLots.className = inactiveCls;
    if (btnSealed) btnSealed.className = inactiveCls;
    if (btnRetailers) btnRetailers.className = inactiveCls;

    if (section === "lots") {
        lotsContainer.classList.remove("hidden");
        if (btnLots) {
            btnLots.className = "py-2 px-3 text-xs font-mono font-bold uppercase transition flex items-center justify-center space-x-2 bg-[#00CED1] text-[#10141D]";
        }
        try { history.replaceState(null, "", "#lots"); } catch (e) {}
    } else if (section === "sealed" && sealedContainer) {
        sealedContainer.classList.remove("hidden");
        if (btnSealed) {
            btnSealed.className = "py-2 px-3 text-xs font-mono font-bold uppercase transition flex items-center justify-center space-x-2 bg-[#F59E0B] text-[#10141D]";
        }
        try { history.replaceState(null, "", "#sealed"); } catch (e) {}
        initSealedProductFinder();
    } else if (section === "retailers" && retailersContainer) {
        retailersContainer.classList.remove("hidden");
        if (btnRetailers) {
            btnRetailers.className = "py-2 px-3 text-xs font-mono font-bold uppercase transition flex items-center justify-center space-x-2 bg-[#8B5CF6] text-white";
        }
        try { history.replaceState(null, "", "#retailers"); } catch (e) {}
    } else {
        singleContainer.classList.remove("hidden");
        if (btnSingle) {
            btnSingle.className = "py-2 px-3 text-xs font-mono font-bold uppercase transition flex items-center justify-center space-x-2 bg-[#DC143C] text-white";
        }
        try { history.replaceState(null, "", "#single"); } catch (e) {}
    }
}

function setLotFinderMode(mode) {
    currentLotFinderMode = mode;
    const modes = ["lots", "anchor_card", "dollar_amount"];
    modes.forEach(m => {
        const btn = document.getElementById(`btn-mode-${m}`);
        if (btn) {
            if (m === mode) {
                btn.className = "lot-mode-btn active p-2.5 sm:p-3 text-left border border-[#00CED1] bg-[#00CED1]/10 text-white transition";
            } else {
                btn.className = "lot-mode-btn p-2.5 sm:p-3 text-left border border-[#263245] bg-[#10141D] text-[#94A3B8] hover:text-white transition";
            }
        }
    });

    const anchorBox = document.getElementById("lot-anchor-card-box");
    const dollarBox = document.getElementById("lot-dollar-amount-box");

    if (anchorBox) {
        if (mode === "anchor_card") anchorBox.classList.remove("hidden");
        else anchorBox.classList.add("hidden");
    }

    if (dollarBox) {
        if (mode === "dollar_amount") dollarBox.classList.remove("hidden");
        else dollarBox.classList.add("hidden");
    }
}

function applyDollarPreset(preset) {
    const maxPrice = document.getElementById("lot-max-card-price");
    const targetBundle = document.getElementById("lot-target-bundle-amount");
    const bundleType = document.getElementById("lot-bundle-amount-type");

    if (!maxPrice || !targetBundle || !bundleType) return;

    if (preset === "free_ship") {
        targetBundle.value = "5.00";
        bundleType.value = "min_threshold";
        showToast("Set $5.00 minimum spend goal (TCGplayer free shipping threshold).", "info");
    } else if (preset === "budget_2") {
        maxPrice.value = "2.00";
        showToast("Filtering watchlist to singles under $2.00.", "info");
    } else if (preset === "budget_5") {
        maxPrice.value = "5.00";
        showToast("Filtering watchlist to staples under $5.00.", "info");
    } else if (preset === "bundle_25") {
        targetBundle.value = "25.00";
        bundleType.value = "max_budget";
        showToast("Set $25.00 cart budget target.", "info");
    } else if (preset === "clear") {
        maxPrice.value = "";
        targetBundle.value = "";
        bundleType.value = "max_budget";
        showToast("Cleared dollar amount presets.", "info");
    }
}

async function runTcgplayerLotScan() {
    const btn = document.getElementById("btn-run-lot-scan");
    const btnLabel = document.getElementById("lot-scan-btn-label");
    const spinner = document.getElementById("lot-scan-spinner");
    const statusText = document.getElementById("lot-status-text");

    const minCards = parseInt(document.getElementById("lot-min-cards")?.value || "5", 10);
    const priceTolerance = document.getElementById("lot-price-tolerance")?.value || "decent_10";
    const tagFilter = document.getElementById("lot-tag-filter")?.value || "all";
    const allowMp = document.getElementById("lot-allow-mp")?.checked || false;

    const anchorSelect = document.getElementById("lot-anchor-card-select");
    const anchorCardId = anchorSelect?.value ? parseInt(anchorSelect.value, 10) : null;
    const anchorCardName = anchorSelect?.selectedOptions[0]?.dataset?.name || null;

    if (currentLotFinderMode === "anchor_card" && !anchorCardId) {
        showToast("Please choose an anchor card from your watchlist.", "warning");
        if (anchorSelect) anchorSelect.focus();
        return;
    }

    const maxCardPrice = document.getElementById("lot-max-card-price")?.value || null;
    const targetBundleAmount = document.getElementById("lot-target-bundle-amount")?.value || null;
    const bundleAmountType = document.getElementById("lot-bundle-amount-type")?.value || "max_budget";
    const englishOnly = document.getElementById("lot-english-only") ? document.getElementById("lot-english-only").checked : true;

    if (btn) btn.disabled = true;
    if (spinner) spinner.classList.remove("hidden");
    if (btnLabel) btnLabel.textContent = "SCANNING MARKET...";
    if (statusText) statusText.textContent = "Querying live TCGplayer seller inventories across watchlist...";

    showToast("Launching TCGplayer seller lot scan...", "info");

    const payload = {
        mode: currentLotFinderMode,
        min_cards: minCards,
        price_tolerance_mode: priceTolerance,
        tag_filter: tagFilter,
        allow_mp: allowMp,
        english_only: englishOnly,
        anchor_card_id: anchorCardId,
        anchor_card_name: anchorCardName,
        max_card_price: maxCardPrice,
        target_bundle_amount: targetBundleAmount,
        bundle_amount_type: bundleAmountType,
    };

    try {
        const resp = await fetch("/api/deals/tcgplayer-lots", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.error || "Failed to scan TCGplayer seller lots.");
        }

        currentSellerLots = data.sellers || [];
        renderSellerLotResults(data);

        if (statusText) {
            statusText.textContent = `Scan complete: Found ${data.total_sellers_found} qualifying sellers across ${data.scanned_cards_count} monitored cards (${data.duration_seconds}s).`;
        }

        if (data.total_sellers_found > 0) {
            showToast(`Found ${data.total_sellers_found} seller lots holding ${minCards}+ watchlist cards!`, "success");
        } else {
            showToast(`No sellers found matching ${minCards}+ cards with current filters. Try relaxing price tolerance or min cards.`, "info");
        }
    } catch (err) {
        console.error("Error during TCGplayer lot scan:", err);
        showToast(err.message || "Failed to complete seller lot scan.", "error");
        if (statusText) statusText.textContent = `Scan failed: ${err.message}`;
    } finally {
        if (btn) btn.disabled = false;
        if (spinner) spinner.classList.add("hidden");
        if (btnLabel) btnLabel.textContent = "🔍 SCAN TCGPLAYER SELLERS";
    }
}

function renderSellerLotResults(data) {
    const kpiRow = document.getElementById("lot-telemetry-kpis");
    const container = document.getElementById("lot-results-container");
    if (!container) return;

    const sellers = data.sellers || [];
    const minCards = data.min_cards || 5;

    // Update KPI summary cards
    if (kpiRow) {
        kpiRow.classList.remove("hidden");
        const countEl = document.getElementById("kpi-lot-sellers-count");
        const minLabel = document.getElementById("kpi-lot-min-label");
        const maxCardsEl = document.getElementById("kpi-lot-max-cards");
        const maxSavingsEl = document.getElementById("kpi-lot-max-savings");
        const durationEl = document.getElementById("kpi-lot-duration");
        const scannedCardsEl = document.getElementById("kpi-lot-scanned-cards");

        if (countEl) countEl.textContent = data.total_sellers_found || 0;
        if (minLabel) minLabel.textContent = minCards;
        if (maxCardsEl) {
            const maxCards = sellers.length > 0 ? Math.max(...sellers.map(s => s.qualifyingCount)) : 0;
            maxCardsEl.textContent = maxCards;
        }
        if (maxSavingsEl) {
            const maxSav = sellers.length > 0 ? Math.max(...sellers.map(s => s.netSavings)) : 0;
            maxSavingsEl.textContent = `$${maxSav.toFixed(2)}`;
        }
        if (durationEl) durationEl.textContent = `${data.duration_seconds || 0}s`;
        if (scannedCardsEl) scannedCardsEl.textContent = data.scanned_cards_count || 0;
    }

    if (sellers.length === 0) {
        container.innerHTML = `
            <div class="bg-[#1B2230] border border-[#263245] p-8 sm:p-10 text-center font-mono">
                <div class="text-xs text-[#F59E0B] uppercase font-bold tracking-widest">// NO SELLERS REACHED THRESHOLD</div>
                <h4 class="text-base font-heading font-black text-white uppercase tracking-wider mt-1">
                    0 Sellers Matched ${minCards}+ Cards at Selected Tolerance
                </h4>
                <p class="text-xs text-[#94A3B8] max-w-lg mx-auto font-sans mt-2 leading-relaxed">
                    No single TCGplayer store held ${minCards} or more cards at or below your chosen price tolerance.
                    Try lowering the <strong>Min Cards at Seller</strong> to 3+ or setting <strong>Decent Price Rule</strong> to Flexible (+20%).
                </p>
            </div>
        `;
        return;
    }

    let html = `<div class="space-y-4">`;

    sellers.forEach((seller, idx) => {
        const directBadge = seller.directSeller ? `<span class="bg-[#00CED1]/20 text-[#00CED1] border border-[#00CED1]/40 text-[10px] px-2 py-0.5 font-bold uppercase tracking-wider">TCG Direct</span>` : "";
        const goldBadge = seller.goldSeller ? `<span class="bg-[#F59E0B]/20 text-[#F59E0B] border border-[#F59E0B]/40 text-[10px] px-2 py-0.5 font-bold uppercase tracking-wider">★ Gold Star</span>` : "";
        
        let bundleSection = "";
        if (seller.bundleInfo) {
            const b = seller.bundleInfo;
            const bLabel = b.bundleType === "min_threshold" ? "Free Shipping Goal" : "Target Budget Cap";
            bundleSection = `
                <div class="mt-3 p-3 bg-[#10B981]/10 border border-[#10B981]/40 font-mono text-xs">
                    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-1 text-[#10B981] font-bold">
                        <span>🎯 OPTIMAL BUNDLE RECOMMENDATION (${bLabel}):</span>
                        <span>${b.count} Cards &bull; Bundle Total: $${seller.subtotal.toFixed(2)} (Cap: $${b.targetAmount.toFixed(2)})</span>
                    </div>
                </div>
            `;
        }

        const countBadge = seller.bundleInfo ? `
            <span class="bg-[#10B981] text-[#10141D] text-xs px-2.5 py-1.5 font-bold uppercase tracking-wider flex items-center space-x-1.5 flex-shrink-0" title="Target Bundle: ${seller.qualifyingCount} cards totaling $${seller.subtotal.toFixed(2)}">
                <span>🎯 ${seller.qualifyingCount} Bundle Cards ($${seller.subtotal.toFixed(2)})</span>
            </span>
        ` : `
            <span class="bg-[#00CED1] text-[#10141D] text-xs px-2.5 py-1.5 font-bold uppercase tracking-wider flex items-center space-x-1.5 flex-shrink-0">
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="square" stroke-linejoin="miter" stroke-width="2" d="M5 13l4 4L19 7"></path>
                </svg>
                <span>${seller.qualifyingCount} Watchlist Cards</span>
            </span>
        `;

        let cardsTableRows = "";
        (seller.cards || []).forEach(card => {
            const binderBadge = card.inBinder ? `
                <span class="bg-[#10B981]/20 text-[#10B981] border border-[#10B981]/40 text-[9px] px-1 py-0.2 uppercase font-bold" title="In Binder (${card.binderCount})">
                    💼 IN BINDER (${card.binderCount})
                </span>
            ` : "";

            const tagBadge = card.tag ? `<span class="bg-[#9333EA]/20 text-[#C084FC] border border-[#9333EA]/40 text-[9px] px-1 py-0.2 uppercase font-bold">🏷️ ${card.tag}</span>` : "";

            const baseMktText = card.baseMarketPrice ? `$${card.baseMarketPrice.toFixed(2)}` : "--";
            const deltaBadge = card.deltaPct !== null ? (
                card.deltaPct <= 0 
                ? `<span class="text-[#10B981] text-[10px] font-bold">${card.deltaPct}%</span>`
                : `<span class="text-[#F59E0B] text-[10px] font-bold">+${card.deltaPct}%</span>`
            ) : "";

            const fancyNotice = card.isFancyTreatment && card.marketPrice ? `
                <span class="text-[9px] text-[#F59E0B] block" title="Special treatment (Edition Market: $${card.marketPrice.toFixed(2)})">
                    ★ Premium Edition (Base: ${baseMktText})
                </span>
            ` : "";

            const imgHtml = card.imageUri ? `
                <img src="${toScryfallThumb(card.imageUri)}" alt="${card.name}" loading="lazy" decoding="async" class="w-8 h-11 object-cover border border-[#263245] bg-[#10141D] flex-shrink-0 cursor-pointer"
                    onclick="openCardImageOverlay(this)" data-card-image="${card.imageUri}" data-card-name="${card.name}">
            ` : `<div class="w-8 h-11 border border-[#263245] bg-[#10141D] flex items-center justify-center text-[8px] text-[#64748B] flex-shrink-0">NO ART</div>`;

            cardsTableRows += `
                <tr class="border-b border-[#263245]/60 hover:bg-[#161C28] transition font-mono text-xs">
                    <td class="p-2 w-10">${imgHtml}</td>
                    <td class="p-2 min-w-0">
                        <div class="font-bold text-white truncate" title="${card.name}">${card.name}</div>
                        <div class="text-[10px] text-[#94A3B8] truncate flex flex-wrap items-center gap-1 mt-0.5">
                            <span>${card.setName || "Magic: The Gathering"}</span>
                            <span class="text-[#00CED1]">&bull; ${card.condition}</span>
                            <span>&bull; ${card.printing}</span>
                            <span class="bg-[#00CED1]/15 text-[#00CED1] border border-[#00CED1]/30 text-[9px] px-1 py-0.2 uppercase font-bold" title="${card.language || 'English'}">
                                ${card.languageAbbreviation || "EN"}
                            </span>
                            ${tagBadge}
                            ${binderBadge}
                        </div>
                        ${fancyNotice}
                    </td>
                    <td class="p-2 text-right whitespace-nowrap">
                        <div class="font-bold text-[#00CED1] text-sm">$${card.price.toFixed(2)}</div>
                        <div class="text-[10px] text-[#64748B]" title="Compared to cheapest baseline regular version across printings">
                            Base Mkt: ${baseMktText} ${deltaBadge}
                        </div>
                    </td>
                    <td class="p-2 text-right whitespace-nowrap">
                        <div class="flex items-center justify-end space-x-1.5">
                            <a href="${card.storeSearchUrl || card.tcgplayerUrl}" target="_blank" rel="noopener"
                                class="btn-bridge inline-block px-2 py-1 text-[10px] text-[#00CED1] hover:text-white uppercase font-bold"
                                title="Search for this card in ${seller.sellerName}'s store">
                                🔍 In Store
                            </a>
                            <a href="${card.tcgplayerUrl}" target="_blank" rel="noopener"
                                class="btn-crimson inline-block px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider">
                                Buy &rarr;
                            </a>
                        </div>
                    </td>
                </tr>
            `;
        });

        let otherInStockHtml = "";
        if (seller.otherInStockCount > 0) {
            otherInStockHtml = `
                <div class="mt-3 pt-3 border-t border-[#263245]/60 text-xs font-mono">
                    <details class="cursor-pointer group">
                        <summary class="text-[#00CED1] hover:text-white flex items-center justify-between text-[11px] font-bold uppercase">
                            <span>+ ${seller.otherInStockCount} More In-Stock Watchlist Cards from this Store (Total Stocked: ${seller.allStockedCount} cards, $${seller.allStockedSubtotal ? seller.allStockedSubtotal.toFixed(2) : '--'})</span>
                            <span class="text-[#64748B] text-[10px] group-open:rotate-180 transition-transform">&darr;</span>
                        </summary>
                        <div class="mt-2 space-y-1.5 pl-2 border-l border-[#00CED1]/40">
                            ${(seller.otherInStockCards || []).map(c => `
                                <div class="flex items-center justify-between text-[11px] text-[#94A3B8]">
                                    <span class="truncate">${c.name} (${c.condition}, ${c.printing})</span>
                                    <div class="flex items-center space-x-2 whitespace-nowrap">
                                        <span class="text-white font-bold">$${c.price.toFixed(2)}</span>
                                        <span class="text-[#64748B] text-[10px]">(Base Mkt: $${c.baseMarketPrice ? c.baseMarketPrice.toFixed(2) : '--'})</span>
                                        <a href="${c.storeSearchUrl || c.tcgplayerUrl}" target="_blank" rel="noopener" class="text-[#00CED1] hover:underline text-[10px]">[In Store]</a>
                                        <a href="${c.tcgplayerUrl}" target="_blank" rel="noopener" class="text-[#FF3358] hover:underline text-[10px]">[Buy]</a>
                                    </div>
                                </div>
                            `).join("")}
                            ${seller.allCardsSearchUrl ? `
                                <div class="pt-2">
                                    <a href="${seller.allCardsSearchUrl}" target="_blank" rel="noopener" class="text-xs text-[#00CED1] hover:underline font-bold">
                                        🔍 Search All ${seller.allStockedCount} In-Stock Cards in ${seller.sellerName}'s Store &rarr;
                                    </a>
                                </div>
                            ` : ""}
                        </div>
                    </details>
                </div>
            `;
        }

        let aboveToleranceHtml = "";
        if (seller.aboveToleranceCount > 0) {
            aboveToleranceHtml = `
                <div class="mt-3 pt-3 border-t border-[#263245]/60 text-xs font-mono">
                    <details class="cursor-pointer group">
                        <summary class="text-[#94A3B8] hover:text-white flex items-center justify-between text-[11px] uppercase">
                            <span>+ ${seller.aboveToleranceCount} Other In-Stock Watchlist Cards (Above Selected Tolerance)</span>
                            <span class="text-[#64748B] text-[10px] group-open:rotate-180 transition-transform">&darr;</span>
                        </summary>
                        <div class="mt-2 space-y-1.5 pl-2 border-l border-[#263245]">
                            ${(seller.aboveToleranceCards || []).map(c => `
                                <div class="flex items-center justify-between text-[11px] text-[#94A3B8]">
                                    <span class="truncate">${c.name} (${c.condition})</span>
                                    <div class="flex items-center space-x-2">
                                        <span class="text-[#F59E0B] font-bold">$${c.price.toFixed(2)}</span>
                                        <span class="text-[#64748B] text-[10px]">(Base Mkt: $${c.baseMarketPrice ? c.baseMarketPrice.toFixed(2) : '--'})</span>
                                        <a href="${c.storeSearchUrl || c.tcgplayerUrl}" target="_blank" rel="noopener" class="text-[#00CED1] hover:underline text-[10px]">[In Store]</a>
                                        <a href="${c.tcgplayerUrl}" target="_blank" rel="noopener" class="text-[#FF3358] hover:underline text-[10px]">[Buy]</a>
                                    </div>
                                </div>
                            `).join("")}
                        </div>
                    </details>
                </div>
            `;
        }

        html += `
            <div class="bg-[#1B2230] border-2 border-[#263245] hover:border-[#00CED1] transition p-4 sm:p-5 font-mono space-y-3.5" id="seller-lot-${idx}">
                <!-- Seller Card Header -->
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-[#263245] pb-3">
                    <div>
                        <div class="flex items-center space-x-2">
                            <span class="text-xs font-mono font-bold text-[#00CED1] uppercase tracking-wider">#${idx + 1} TCGPLAYER STORE</span>
                            ${directBadge}
                            ${goldBadge}
                        </div>
                        <h4 class="text-base sm:text-lg font-heading font-black text-white uppercase tracking-wider mt-0.5">
                            ${seller.sellerName}
                        </h4>
                        <div class="text-xs text-[#94A3B8] mt-0.5 flex flex-wrap items-center gap-2">
                            <span>★ ${seller.sellerRating}% Rating</span>
                            <span>&bull;</span>
                            <span>${seller.sellerSales} Sales</span>
                            <span>&bull;</span>
                            <a href="${seller.storeUrl}" target="_blank" rel="noopener" class="text-[#00CED1] hover:underline flex items-center space-x-1 font-bold">
                                <span>🏪 Open Storefront &rarr;</span>
                            </a>
                        </div>
                    </div>

                    <div class="flex flex-wrap items-center gap-1.5 sm:justify-end">
                        ${countBadge}
                        <button onclick="openTcgMassEntry(${idx})" class="btn-bridge text-xs px-3 py-1.5 bg-[#10B981]/20 text-[#10B981] hover:bg-[#10B981] hover:text-[#10141D] flex items-center space-x-1.5 font-bold uppercase tracking-wider transition border border-[#10B981]/40"
                            title="Copy all ${seller.qualifyingCount} cards to clipboard and open TCGplayer Mass Entry tool to cart them together">
                            <span>🛒 Cart Lot (Mass Entry)</span>
                        </button>
                        <a href="${seller.storeUrl}" target="_blank" rel="noopener"
                            class="btn-bridge text-xs px-2.5 py-1.5 text-[#00CED1] hover:text-white flex items-center space-x-1 font-bold"
                            title="Browse ${seller.sellerName}'s live catalog on TCGplayer">
                            <span>🏪 Storefront</span>
                        </a>
                        <button onclick="copySellerLotList(${idx})" class="btn-bridge text-xs px-2.5 py-1.5 text-[#94A3B8] hover:text-white" title="Copy Card List to Clipboard">
                            📋 Copy List
                        </button>
                    </div>
                </div>

                <!-- Financial Telemetry Breakdown -->
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 bg-[#10141D] p-3 border border-[#263245] text-xs">
                    <div>
                        <div class="text-[#94A3B8] text-[10px] uppercase">${seller.bundleInfo ? 'Bundle Subtotal' : 'Cards Subtotal'}</div>
                        <div class="text-sm sm:text-base font-bold text-white mt-0.5">$${seller.subtotal.toFixed(2)}</div>
                    </div>
                    <div>
                        <div class="text-[#94A3B8] text-[10px] uppercase">Est. Shipping</div>
                        <div class="text-sm sm:text-base font-bold ${seller.estShipping === 0 ? 'text-[#10B981]' : 'text-white'} mt-0.5">
                            ${seller.estShipping === 0 ? 'FREE / DIRECT' : '$' + seller.estShipping.toFixed(2)}
                        </div>
                    </div>
                    <div>
                        <div class="text-[#94A3B8] text-[10px] uppercase">${seller.bundleInfo ? 'Bundle Landed Total' : 'Landed Total'}</div>
                        <div class="text-sm sm:text-base font-bold text-[#00CED1] mt-0.5">$${seller.landedTotal.toFixed(2)}</div>
                    </div>
                    <div>
                        <div class="text-[#94A3B8] text-[10px] uppercase" title="Savings compared to cheapest baseline regular version market price">Net Savings vs Base</div>
                        <div class="text-sm sm:text-base font-bold ${seller.netSavings > 0 ? 'text-[#10B981]' : 'text-[#94A3B8]'} mt-0.5">
                            ${seller.netSavings > 0 ? '+$' + seller.netSavings.toFixed(2) + ' (' + seller.savingsPct + '%)' : '$0.00'}
                        </div>
                    </div>
                </div>

                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-1 text-[11px] bg-[#10141D] px-3 py-1.5 border border-[#263245] text-[#94A3B8] font-mono">
                    <div class="flex items-center space-x-1.5">
                        <span class="text-[#00CED1] font-bold">💡 How to Buy:</span>
                        <span>Use <strong class="text-[#10B981]">🛒 Cart Lot (Mass Entry)</strong> to paste all cards into your cart at once, or use <strong class="text-[#00CED1]">🔍 In Store</strong> next to any card below to view listings in this shop.</span>
                    </div>
                </div>

                ${bundleSection}

                <!-- Cards Table -->
                <div class="border border-[#263245] bg-[#10141D] overflow-x-auto">
                    <table class="w-full text-left border-collapse">
                        <thead>
                            <tr class="border-b border-[#263245] bg-[#161C28] text-[10px] text-[#94A3B8] uppercase font-bold tracking-wider">
                                <th class="p-2 w-10">Art</th>
                                <th class="p-2">Card &amp; Printing</th>
                                <th class="p-2 text-right">Price</th>
                                <th class="p-2 text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${cardsTableRows}
                        </tbody>
                    </table>
                </div>

                ${otherInStockHtml}
                ${aboveToleranceHtml}
            </div>
        `;
    });

    html += `</div>`;
    container.innerHTML = html;
}

function copySellerLotList(sellerIndex) {
    const seller = currentSellerLots[sellerIndex];
    if (!seller) return;

    let text = `TCGplayer Seller Lot: ${seller.sellerName}\n`;
    text += `Cards In Stock: ${seller.qualifyingCount} | Subtotal: $${seller.subtotal.toFixed(2)} | Est. Shipping: $${seller.estShipping.toFixed(2)}\n`;
    text += `--------------------------------------------------\n`;

    (seller.cards || []).forEach(c => {
        text += `1x ${c.name} (${c.condition}, ${c.printing}) - $${c.price.toFixed(2)}\n`;
    });

    navigator.clipboard.writeText(text).then(() => {
        showToast(`Copied ${seller.qualifyingCount} cards from ${seller.sellerName} to clipboard!`, "success");
    }).catch(err => {
        console.error("Clipboard copy error:", err);
        showToast("Failed to copy card list.", "error");
    });
}

function openTcgMassEntry(sellerIndex) {
    const seller = currentSellerLots[sellerIndex];
    if (!seller) return;

    let lines = [];
    (seller.cards || []).forEach(c => {
        const cname = c.cleanName || c.name.split(" // ")[0].trim();
        lines.push(`1 ${cname}`);
    });
    const text = lines.join("\n");

    navigator.clipboard.writeText(text).then(() => {
        showToast(`Copied ${seller.qualifyingCount} cards in Mass Entry format! Opening TCGplayer Mass Entry...`, "success");
        window.open("https://www.tcgplayer.com/massentry", "_blank", "noopener,noreferrer");
    }).catch(err => {
        console.error("Clipboard error:", err);
        window.open("https://www.tcgplayer.com/massentry", "_blank", "noopener,noreferrer");
    });
}

// =========================================================================
// TCGplayer Sealed Product Deal Finder & Watchlist Integration
// =========================================================================
let sealedSetsLoaded = false;
let allSealedSets = [];
let currentSealedSetProducts = [];
let selectedSealedProductIds = new Set();
let sealedCustomTargets = {}; // productId -> float
let activeSealedSubtype = "all";

function initSealedProductFinder() {
    if (!sealedSetsLoaded) {
        loadSealedSets();
    }
}

async function loadSealedSets() {
    const badge = document.getElementById("sealed-sets-count-badge");
    const select = document.getElementById("sealed-set-select");
    const recentBar = document.getElementById("sealed-recent-sets-bar");
    if (badge) badge.textContent = "Loading sets...";

    try {
        const resp = await fetch("/api/sealed/sets");
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.error || "Failed to load TCGplayer sealed sets.");
        }

        allSealedSets = data.sets || [];
        sealedSetsLoaded = true;

        if (badge) {
            badge.innerHTML = `TCGplayer Scope: <strong class="text-white">${allSealedSets.length} Sets</strong>`;
        }

        populateSealedSetDropdown(allSealedSets);

        if (recentBar) {
            const topRecent = allSealedSets.slice(0, 8);
            let pillsHtml = `<span class="text-[10px] text-[#64748B] uppercase font-bold flex-shrink-0">RECENT SETS:</span>`;
            topRecent.forEach(s => {
                const codeTag = s.setCode ? `[${s.setCode}] ` : "";
                const safeSlug = (s.setUrlName || "").replace(/'/g, "\\'");
                pillsHtml += `
                    <button type="button" onclick="selectSealedSetQuick('${safeSlug}')"
                        class="tag-pill flex-shrink-0 hover:border-[#F59E0B] hover:text-[#F59E0B]"
                        title="${s.setName} (${s.productCount} sealed items)">
                        ${codeTag}${s.setName}
                    </button>
                `;
            });
            recentBar.innerHTML = pillsHtml;
        }
    } catch (err) {
        console.error("Failed loading sealed sets:", err);
        if (badge) badge.textContent = "Failed to load sets";
        if (select) select.innerHTML = `<option value="">-- ERROR LOADING SETS --</option>`;
        showToast(err.message || "Could not load MTG sets from TCGplayer.", "error");
    }
}

function populateSealedSetDropdown(sets) {
    const select = document.getElementById("sealed-set-select");
    if (!select) return;
    const prevVal = select.value;

    let html = `<option value="">-- SELECT AN MTG SET (${sets.length} AVAILABLE) --</option>`;
    sets.forEach(s => {
        const codePart = s.setCode ? `[${s.setCode}] ` : "";
        const yearPart = s.releasedAt ? ` (${s.releasedAt.slice(0, 4)})` : "";
        html += `<option value="${s.setUrlName}">${codePart}${s.setName}${yearPart} — ${s.productCount} items</option>`;
    });
    select.innerHTML = html;

    if (prevVal && sets.some(s => s.setUrlName === prevVal)) {
        select.value = prevVal;
    }
}

function filterSealedSetDropdown(query) {
    const q = (query || "").trim().toLowerCase();
    if (!q) {
        populateSealedSetDropdown(allSealedSets);
        return;
    }
    const filtered = allSealedSets.filter(s =>
        (s.setName || "").toLowerCase().includes(q) ||
        (s.setUrlName || "").toLowerCase().includes(q) ||
        (s.setCode || "").toLowerCase().includes(q)
    );
    populateSealedSetDropdown(filtered);
    // If exact or single match, auto-select in dropdown
    const select = document.getElementById("sealed-set-select");
    if (select && filtered.length === 1) {
        select.value = filtered[0].setUrlName;
    }
}

function selectSealedSetQuick(setUrlName) {
    const select = document.getElementById("sealed-set-select");
    const search = document.getElementById("sealed-set-search");
    if (search) search.value = "";
    populateSealedSetDropdown(allSealedSets);
    if (select) {
        select.value = setUrlName;
    }
    loadSealedProductsForSelectedSet();
}

function getCheckedSealedCategories() {
    const checked = [];
    document.querySelectorAll(".sealed-cat-checkbox").forEach(cb => {
        if (cb.checked) checked.push(cb.value);
    });
    return checked;
}

function setSealedBoosterSubtype(subtype) {
    activeSealedSubtype = subtype || "all";
    document.querySelectorAll(".sealed-subtype-pill").forEach(btn => {
        if ((btn.dataset.subtype || "all") === activeSealedSubtype) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });
    renderSealedSetProducts();
}

async function loadSealedProductsForSelectedSet() {
    const select = document.getElementById("sealed-set-select");
    const setSlug = select?.value || "";
    const grid = document.getElementById("sealed-products-grid");
    const heading = document.getElementById("sealed-selected-set-heading");
    const countPill = document.getElementById("sealed-product-count-pill");
    const includeCases = document.getElementById("sealed-include-cases")?.checked || false;

    if (!setSlug) return;

    const setObj = allSealedSets.find(s => s.setUrlName === setSlug);
    const setLabel = setObj ? setObj.setName : setSlug;
    if (heading) heading.textContent = `Loading sealed products for ${setLabel}...`;
    if (grid) {
        grid.innerHTML = `<div class="col-span-full py-8 text-center text-xs text-[#00CED1]">Querying TCGplayer catalog for <strong>${setLabel}</strong>...</div>`;
    }

    try {
        const params = new URLSearchParams({
            set: setSlug,
            include_cases: includeCases ? "true" : "false",
        });
        const resp = await fetch(`/api/sealed/products?${params.toString()}`);
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.error || "Failed to load sealed products for set.");
        }

        currentSealedSetProducts = data.products || [];
        selectedSealedProductIds = new Set();
        sealedCustomTargets = {};

        // Pre-populate any existing watchlist target prices
        currentSealedSetProducts.forEach(p => {
            if (p.watchlistTargetPrice) {
                sealedCustomTargets[p.productId] = p.watchlistTargetPrice;
            }
        });

        if (heading) heading.textContent = `${setLabel} — Sealed Catalog`;
        if (countPill) {
            countPill.textContent = `${currentSealedSetProducts.length} products`;
            countPill.classList.remove("hidden");
        }

        renderSealedSetProducts();
    } catch (err) {
        console.error("Error loading set sealed products:", err);
        if (grid) {
            grid.innerHTML = `<div class="col-span-full py-6 text-center text-xs text-[#FF3358]">${err.message || "Error loading sealed products."}</div>`;
        }
        showToast(err.message || "Failed to load sealed products.", "error");
    }
}

function getFilteredSealedProducts() {
    const allowedCats = new Set(getCheckedSealedCategories());
    return currentSealedSetProducts.filter(p => {
        if (allowedCats.size > 0 && !allowedCats.has(p.category)) {
            return false;
        }
        if (activeSealedSubtype && activeSealedSubtype !== "all") {
            if ((p.boosterSubtype || "").toLowerCase() !== activeSealedSubtype.toLowerCase()) {
                return false;
            }
        }
        return true;
    });
}

function renderSealedSetProducts() {
    const grid = document.getElementById("sealed-products-grid");
    const countPill = document.getElementById("sealed-product-count-pill");
    if (!grid) return;

    const products = getFilteredSealedProducts();
    if (countPill && currentSealedSetProducts.length > 0) {
        countPill.textContent = `${products.length} of ${currentSealedSetProducts.length} shown`;
        countPill.classList.remove("hidden");
    }

    if (products.length === 0) {
        grid.innerHTML = `
            <div class="col-span-full py-8 text-center text-xs text-[#94A3B8]">
                No sealed products match the current category / booster type filters for this set. Try checking more categories above.
            </div>
        `;
        updateSealedSelectionUI();
        return;
    }

    let html = "";
    products.forEach(p => {
        const isChecked = selectedSealedProductIds.has(p.productId);
        const mktStr = p.marketPrice ? `$${p.marketPrice.toFixed(2)}` : "N/A";
        const lowStr = p.lowestLandedPrice ? `$${p.lowestLandedPrice.toFixed(2)}` : "Out of Stock";
        const customTarget = sealedCustomTargets[p.productId] !== undefined ? sealedCustomTargets[p.productId] : "";

        let deltaBadge = "";
        if (p.deltaVsMarketPct !== null && p.deltaVsMarketPct !== undefined) {
            if (p.deltaVsMarketPct <= -5) {
                deltaBadge = `<span class="bg-[#10B981]/20 text-[#10B981] border border-[#10B981]/40 text-[10px] px-1.5 py-0.5 font-bold">${p.deltaVsMarketPct}% vs Mkt</span>`;
            } else if (p.deltaVsMarketPct <= 0) {
                deltaBadge = `<span class="bg-[#00CED1]/15 text-[#00CED1] border border-[#00CED1]/40 text-[10px] px-1.5 py-0.5 font-bold">${p.deltaVsMarketPct}% vs Mkt</span>`;
            } else {
                deltaBadge = `<span class="bg-[#10141D] text-[#94A3B8] border border-[#263245] text-[10px] px-1.5 py-0.5">+${p.deltaVsMarketPct}%</span>`;
            }
        }

        const perPackBadge = p.pricePerPack
            ? `<span class="text-[10px] text-[#2DD4BF] bg-[#2DD4BF]/10 border border-[#2DD4BF]/30 px-1.5 py-0.5" title="${p.packCount} packs">$${p.pricePerPack.toFixed(2)}/pk</span>`
            : "";

        const watchedBadge = p.inWatchlist
            ? `<span class="bg-[#9333EA]/20 text-[#C084FC] border border-[#9333EA]/50 text-[9px] px-1.5 py-0.5 font-bold uppercase">★ Watching</span>`
            : "";

        const subtypeBadge = p.boosterSubtype && p.boosterSubtype !== "Standard"
            ? `<span class="bg-[#F59E0B]/15 text-[#F59E0B] border border-[#F59E0B]/40 text-[9px] px-1.5 py-0.5 uppercase font-bold">${p.boosterSubtype}</span>`
            : "";

        html += `
            <div class="bg-[#10141D] border ${isChecked ? 'border-[#F59E0B] bg-[#F59E0B]/5' : 'border-[#263245] hover:border-[#3D4F6B]'} p-3 flex flex-col justify-between gap-2.5 transition" id="sealed-prod-card-${p.productId}">
                <div class="flex items-start gap-2.5">
                    <div class="pt-0.5 flex-shrink-0">
                        <input type="checkbox" class="sealed-prod-checkbox w-4 h-4 text-[#F59E0B] rounded-none bg-[#161C28] border-[#263245] cursor-pointer"
                            data-pid="${p.productId}" ${isChecked ? "checked" : ""}
                            onchange="toggleSealedProductSelection(${p.productId}, this.checked)">
                    </div>
                    <img src="${p.imageUrl}" alt="${p.productName}" loading="lazy" decoding="async"
                        class="w-14 h-14 object-contain border border-[#263245] bg-[#161C28] flex-shrink-0 cursor-pointer"
                        onclick="toggleSealedProductSelection(${p.productId}, !selectedSealedProductIds.has(${p.productId}))">
                    <div class="min-w-0 flex-1">
                        <div class="text-xs font-bold text-white leading-snug cursor-pointer" title="${p.productName}"
                            onclick="toggleSealedProductSelection(${p.productId}, !selectedSealedProductIds.has(${p.productId}))">
                            ${p.productName}
                        </div>
                        <div class="flex flex-wrap items-center gap-1 mt-1">
                            <span class="bg-[#1B2230] text-[#94A3B8] border border-[#263245] text-[9px] px-1.5 py-0.5 uppercase">${p.categoryLabel}</span>
                            ${subtypeBadge}
                            ${watchedBadge}
                        </div>
                    </div>
                </div>

                <!-- Prices -->
                <div class="grid grid-cols-2 gap-2 bg-[#161C28] p-2 border border-[#263245] text-[11px]">
                    <div>
                        <div class="text-[9px] text-[#64748B] uppercase">TCG Market</div>
                        <div class="font-bold text-white">${mktStr}</div>
                    </div>
                    <div>
                        <div class="text-[9px] text-[#64748B] uppercase">Lowest Landed</div>
                        <div class="font-bold ${p.inStock ? 'text-[#00CED1]' : 'text-[#FF3358]'}">${lowStr}</div>
                    </div>
                </div>

                <div class="flex flex-wrap items-center justify-between gap-1.5">
                    <div class="flex items-center gap-1 flex-wrap">
                        ${deltaBadge}
                        ${perPackBadge}
                        <span class="text-[10px] text-[#64748B]">${p.totalListings} listings</span>
                    </div>
                </div>

                <!-- Optional Target Price + Quick Watch -->
                <div class="flex items-center justify-between gap-2 pt-2 border-t border-[#263245]/60">
                    <div class="flex items-center space-x-1 flex-1 min-w-0">
                        <span class="text-[10px] text-[#64748B] uppercase flex-shrink-0">Target $:</span>
                        <input type="number" step="0.50" min="0" inputmode="decimal" value="${customTarget}" placeholder="Optional"
                            class="w-full max-w-[95px] bg-[#161C28] border border-[#263245] px-1.5 py-1 text-[11px] text-white font-mono focus:border-[#F59E0B]"
                            onchange="updateSealedProductTarget(${p.productId}, this.value)">
                    </div>
                    <div class="flex items-center gap-1.5 flex-shrink-0">
                        <button type="button" onclick="quickWatchSealedProduct(${p.productId})"
                            class="text-[10px] px-2 py-1 border border-[#10B981]/40 text-[#10B981] hover:bg-[#10B981] hover:text-[#10141D] font-bold uppercase transition"
                            title="Add this sealed product to your Watchlist">
                            ${p.inWatchlist ? "Update" : "+ Watch"}
                        </button>
                        <a href="${p.tcgplayerUrl}" target="_blank" rel="noopener"
                            class="text-[10px] px-2 py-1 border border-[#263245] text-[#94A3B8] hover:text-white hover:border-[#00CED1] uppercase font-bold">
                            TCG &rarr;
                        </a>
                    </div>
                </div>
            </div>
        `;
    });

    grid.innerHTML = html;
    updateSealedSelectionUI();
}

function toggleSealedProductSelection(productId, isSelected) {
    if (isSelected) {
        selectedSealedProductIds.add(productId);
    } else {
        selectedSealedProductIds.delete(productId);
    }
    const card = document.getElementById(`sealed-prod-card-${productId}`);
    const cb = card?.querySelector(".sealed-prod-checkbox");
    if (cb) cb.checked = isSelected;
    if (card) {
        if (isSelected) {
            card.classList.remove("border-[#263245]");
            card.classList.add("border-[#F59E0B]", "bg-[#F59E0B]/5");
        } else {
            card.classList.remove("border-[#F59E0B]", "bg-[#F59E0B]/5");
            card.classList.add("border-[#263245]");
        }
    }
    updateSealedSelectionUI();
}

function selectSealedProductsPreset(preset) {
    const visible = getFilteredSealedProducts();
    if (preset === "clear") {
        selectedSealedProductIds.clear();
    } else if (preset === "all") {
        visible.forEach(p => { if (p.inStock) selectedSealedProductIds.add(p.productId); });
    } else if (preset === "boxes") {
        selectedSealedProductIds.clear();
        visible.forEach(p => { if (p.category === "booster_box" && p.inStock) selectedSealedProductIds.add(p.productId); });
    } else if (preset === "packs") {
        selectedSealedProductIds.clear();
        visible.forEach(p => { if (p.category === "booster_pack" && p.inStock) selectedSealedProductIds.add(p.productId); });
    } else if (preset === "below_market") {
        selectedSealedProductIds.clear();
        visible.forEach(p => {
            if (p.inStock && p.deltaVsMarketPct !== null && p.deltaVsMarketPct <= 0) {
                selectedSealedProductIds.add(p.productId);
            }
        });
    }
    renderSealedSetProducts();
}

function updateSealedProductTarget(productId, val) {
    const num = parseFloat(val);
    if (!isNaN(num) && num > 0) {
        sealedCustomTargets[productId] = Math.round(num * 100) / 100;
    } else {
        delete sealedCustomTargets[productId];
    }
}

function applySealedTargetPreset(multiplier) {
    const visible = getFilteredSealedProducts();
    const targetList = selectedSealedProductIds.size > 0
        ? visible.filter(p => selectedSealedProductIds.has(p.productId))
        : visible;

    let updatedCount = 0;
    targetList.forEach(p => {
        if (p.marketPrice && p.marketPrice > 0) {
            sealedCustomTargets[p.productId] = Math.round(p.marketPrice * multiplier * 100) / 100;
            updatedCount += 1;
        }
    });
    renderSealedSetProducts();
    const pctLabel = multiplier === 1 ? "Market Price" : `${Math.round((1 - multiplier) * 100)}% below Market`;
    showToast(`Applied target price (${pctLabel}) to ${updatedCount} sealed product(s).`, "info");
}

function updateSealedSelectionUI() {
    const count = selectedSealedProductIds.size;
    const status = document.getElementById("sealed-selection-status");
    const scanBtn = document.getElementById("btn-scan-sealed-deals");
    const watchBtn = document.getElementById("btn-watch-sealed-selected");

    if (status) {
        status.textContent = count === 0
            ? "0 sealed products selected. Check products above to scan live seller listings or add to Watchlist."
            : `${count} sealed product${count === 1 ? "" : "s"} selected.`;
    }
    if (scanBtn) scanBtn.disabled = count === 0;
    if (watchBtn) watchBtn.disabled = count === 0;
}

function buildSelectedSealedPayload() {
    const byId = {};
    currentSealedSetProducts.forEach(p => { byId[p.productId] = p; });
    const list = [];
    selectedSealedProductIds.forEach(pid => {
        const p = byId[pid];
        if (!p) return;
        list.push({
            ...p,
            targetPrice: sealedCustomTargets[pid] || null,
        });
    });
    return list;
}

async function runSealedDealScan() {
    const selected = buildSelectedSealedPayload();
    if (selected.length === 0) {
        showToast("Please select at least one sealed product to scan.", "warning");
        return;
    }

    const btn = document.getElementById("btn-scan-sealed-deals");
    const spinner = document.getElementById("sealed-scan-spinner");
    const label = document.getElementById("sealed-scan-btn-label");
    const toleranceMode = document.getElementById("sealed-tolerance-mode")?.value || "market_0";
    const englishOnly = document.getElementById("sealed-english-only") ? document.getElementById("sealed-english-only").checked : true;

    if (btn) btn.disabled = true;
    if (spinner) spinner.classList.remove("hidden");
    if (label) label.textContent = `SCANNING ${selected.length} PRODUCT(S)...`;

    try {
        const resp = await fetch("/api/sealed/scan-deals", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                products: selected,
                tolerance_mode: toleranceMode,
                english_only: englishOnly,
                max_listings: 10,
            }),
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.error || "Failed to scan sealed product deals.");
        }

        renderSealedDealScanResults(data);
        const dealsFound = data.summary?.dealsFound || 0;
        showToast(`Sealed scan complete: ${dealsFound} active deal(s) across ${data.summary?.productsScanned || 0} product(s).`, dealsFound > 0 ? "success" : "info");
    } catch (err) {
        console.error("Sealed deal scan error:", err);
        showToast(err.message || "Error scanning TCGplayer sealed listings.", "error");
    } finally {
        if (btn) btn.disabled = selectedSealedProductIds.size === 0;
        if (spinner) spinner.classList.add("hidden");
        if (label) label.textContent = "🔍 Scan Live TCGplayer Deals";
    }
}

function renderSealedDealScanResults(data) {
    const kpis = document.getElementById("sealed-scan-kpis");
    const container = document.getElementById("sealed-results-container");
    if (!container) return;

    const summary = data.summary || {};
    if (kpis) {
        kpis.classList.remove("hidden");
        const setEl = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
        setEl("kpi-sealed-scanned", summary.productsScanned || 0);
        setEl("kpi-sealed-duration", `${data.durationSeconds || 0}s live scan`);
        setEl("kpi-sealed-deals", summary.dealsFound || 0);
        setEl("kpi-sealed-best-savings", `$${(summary.bestSavingsAmt || 0).toFixed(2)}`);
        setEl("kpi-sealed-best-pct", `${(summary.bestSavingsPct || 0).toFixed(1)}% off market`);
        setEl("kpi-sealed-multi-sellers", summary.multiSellerCount || 0);
    }

    let html = "";

    // Multi-Product Seller Consolidation Banner
    const multiSellers = data.multiProductSellers || [];
    if (multiSellers.length > 0) {
        html += `
            <div class="bg-[#1B2230] border-2 border-[#00CED1] p-4 font-mono space-y-3">
                <div class="flex items-center justify-between flex-wrap gap-2">
                    <div>
                        <span class="text-[10px] text-[#00CED1] uppercase font-bold tracking-wider">⚡ MULTI-ITEM SELLER OVERLAP</span>
                        <h4 class="text-sm sm:text-base font-heading font-black text-white uppercase">Sellers Carrying 2+ of Your Selected Sealed Products</h4>
                    </div>
                    <span class="text-[11px] text-[#94A3B8]">Combine items from one store to save on shipping</span>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                    ${multiSellers.slice(0, 4).map(s => `
                        <div class="bg-[#10141D] border border-[#263245] p-3 space-y-2 text-xs">
                            <div class="flex items-center justify-between gap-2">
                                <div>
                                    <div class="font-bold text-white">${s.sellerName}</div>
                                    <div class="text-[10px] text-[#94A3B8]">★ ${s.sellerRating}% &bull; ${s.sellerSales} Sales &bull; ${s.productCount} Items</div>
                                </div>
                                <a href="${s.storeUrl}" target="_blank" rel="noopener" class="btn-bridge px-2.5 py-1 text-[10px] text-[#00CED1] hover:text-white uppercase font-bold">
                                    🏪 Store &rarr;
                                </a>
                            </div>
                            <div class="space-y-1 border-t border-[#263245]/60 pt-2">
                                ${(s.products || []).map(p => `
                                    <div class="flex items-center justify-between gap-2 text-[11px]">
                                        <span class="text-[#CBD5E1] truncate">${p.productName}</span>
                                        <div class="flex items-center gap-2 whitespace-nowrap">
                                            <span class="text-[#00CED1] font-bold">$${p.price.toFixed(2)}</span>
                                            <a href="${p.buyUrl}" target="_blank" rel="noopener" class="text-[#FF3358] hover:underline text-[10px]">Buy</a>
                                        </div>
                                    </div>
                                `).join("")}
                            </div>
                            <div class="flex items-center justify-between text-[11px] pt-1.5 border-t border-[#263245]/60">
                                <span class="text-[#94A3B8]">Est. Combined Total: <strong class="text-white">$${s.estCombinedTotal.toFixed(2)}</strong></span>
                                ${s.netSavingsVsMarket > 0 ? `<span class="text-[#10B981] font-bold">Save $${s.netSavingsVsMarket.toFixed(2)} vs Mkt</span>` : ""}
                            </div>
                        </div>
                    `).join("")}
                </div>
            </div>
        `;
    }

    // Per-Product Deal Cards
    const products = data.products || [];
    products.forEach(p => {
        const borderCls = p.isDeal ? "border-[#10B981]" : "border-[#263245]";
        const mktStr = p.marketPrice ? `$${p.marketPrice.toFixed(2)}` : "N/A";
        const bestStr = p.bestLandedPrice ? `$${p.bestLandedPrice.toFixed(2)}` : "Out of Stock";
        const targetStr = p.targetPrice ? `$${p.targetPrice.toFixed(2)}` : "None";

        const dealBadge = p.isDeal
            ? `<span class="bg-[#10B981] text-[#10141D] text-[10px] px-2 py-0.5 font-bold uppercase tracking-wider">✓ ${p.dealLabel || "DEAL"}</span>`
            : `<span class="bg-[#10141D] text-[#94A3B8] border border-[#263245] text-[10px] px-2 py-0.5 uppercase">${p.dealLabel || "Market"}</span>`;

        const savingsText = (p.savingsVsMarket !== null && p.savingsVsMarket !== undefined)
            ? (p.savingsVsMarket >= 0
                ? `<span class="text-[#10B981] font-bold">Save $${p.savingsVsMarket.toFixed(2)} (${p.savingsVsMarketPct}% off Market)</span>`
                : `<span class="text-[#F59E0B]">+$${Math.abs(p.savingsVsMarket).toFixed(2)} (+${Math.abs(p.savingsVsMarketPct)}% vs Market)</span>`)
            : `<span class="text-[#64748B]">No Market Baseline</span>`;

        const rowsHtml = (p.listings || []).map((l, idx) => {
            const shipStr = l.shippingPrice === 0 ? `<span class="text-[#10B981]">FREE</span>` : `+$${l.shippingPrice.toFixed(2)}`;
            const directTag = l.directSeller ? `<span class="bg-[#00CED1]/20 text-[#00CED1] text-[9px] px-1">Direct</span>` : "";
            const goldTag = l.goldSeller ? `<span class="bg-[#F59E0B]/20 text-[#F59E0B] text-[9px] px-1">★ Gold</span>` : "";
            const deltaCell = l.deltaVsMarketPct !== null && l.deltaVsMarketPct !== undefined
                ? `<span class="${l.deltaVsMarketPct <= 0 ? 'text-[#10B981]' : 'text-[#94A3B8]'} font-bold">${l.deltaVsMarketPct > 0 ? '+' : ''}${l.deltaVsMarketPct}%</span>`
                : "--";
            return `
                <tr class="border-b border-[#263245]/60 hover:bg-[#161C28] text-xs ${idx === 0 && l.isDeal ? 'bg-[#10B981]/5' : ''}">
                    <td class="p-2">
                        <div class="font-bold text-white flex items-center gap-1.5 flex-wrap">
                            <span>${l.sellerName}</span>
                            ${directTag}
                            ${goldTag}
                        </div>
                        <div class="text-[10px] text-[#64748B]">★ ${l.sellerRating}% &bull; ${l.sellerSales} sales &bull; Qty: ${l.quantity}</div>
                    </td>
                    <td class="p-2 text-right whitespace-nowrap text-[#CBD5E1]">$${l.price.toFixed(2)}</td>
                    <td class="p-2 text-right whitespace-nowrap">${shipStr}</td>
                    <td class="p-2 text-right whitespace-nowrap">
                        <div class="font-bold text-[#00CED1]">$${l.landedPrice.toFixed(2)}</div>
                        ${l.pricePerPack ? `<div class="text-[10px] text-[#2DD4BF]">$${l.pricePerPack.toFixed(2)}/pk</div>` : ""}
                    </td>
                    <td class="p-2 text-right whitespace-nowrap">${deltaCell}</td>
                    <td class="p-2 text-right whitespace-nowrap">
                        <a href="${l.buyUrl}" target="_blank" rel="noopener" class="btn-crimson inline-block px-2.5 py-1 text-[10px] font-bold uppercase">
                            Buy &rarr;
                        </a>
                    </td>
                </tr>
            `;
        }).join("");

        html += `
            <div class="bg-[#1B2230] border-2 ${borderCls} p-4 font-mono space-y-3">
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-[#263245] pb-3">
                    <div class="flex items-start gap-3 min-w-0">
                        <img src="${p.imageUrl}" alt="${p.productName}" loading="lazy" decoding="async" class="w-14 h-14 object-contain border border-[#263245] bg-[#10141D] flex-shrink-0">
                        <div class="min-w-0">
                            <div class="flex flex-wrap items-center gap-1.5">
                                ${dealBadge}
                                <span class="text-[10px] text-[#94A3B8] uppercase">${p.categoryLabel}</span>
                                ${p.packCount ? `<span class="text-[10px] text-[#2DD4BF]">${p.packCount} Packs</span>` : ""}
                            </div>
                            <h4 class="text-sm sm:text-base font-heading font-black text-white mt-0.5">${p.productName}</h4>
                            <div class="text-xs mt-0.5">${savingsText}</div>
                        </div>
                    </div>
                    <div class="flex flex-wrap items-center gap-2 flex-shrink-0">
                        <button type="button" onclick="quickWatchSealedProduct(${p.productId})"
                            class="btn-bridge px-3 py-1.5 text-xs text-[#10B981] border-[#10B981]/40 hover:bg-[#10B981] hover:text-[#10141D] font-bold uppercase">
                            ${p.inWatchlist ? "★ Update Watchlist" : "+ Add to Watchlist"}
                        </button>
                        <a href="${p.bestBuyUrl}" target="_blank" rel="noopener" class="btn-crimson px-3.5 py-1.5 text-xs font-bold uppercase">
                            Best Deal ${bestStr} &rarr;
                        </a>
                    </div>
                </div>

                <!-- Summary Metrics -->
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 bg-[#10141D] p-2.5 border border-[#263245] text-xs">
                    <div>
                        <div class="text-[10px] text-[#94A3B8] uppercase">Best Landed Price</div>
                        <div class="text-sm font-bold text-[#00CED1]">${bestStr}</div>
                    </div>
                    <div>
                        <div class="text-[10px] text-[#94A3B8] uppercase">TCG Market Price</div>
                        <div class="text-sm font-bold text-white">${mktStr}</div>
                    </div>
                    <div>
                        <div class="text-[10px] text-[#94A3B8] uppercase">Your Target</div>
                        <div class="text-sm font-bold text-white">${targetStr}</div>
                    </div>
                    <div>
                        <div class="text-[10px] text-[#94A3B8] uppercase">Est. Price / Pack</div>
                        <div class="text-sm font-bold text-[#2DD4BF]">${p.pricePerPack ? '$' + p.pricePerPack.toFixed(2) : 'N/A'}</div>
                    </div>
                </div>

                <!-- Top Live Listings Table -->
                ${rowsHtml ? `
                    <div class="border border-[#263245] bg-[#10141D] overflow-x-auto">
                        <table class="w-full text-left border-collapse">
                            <thead>
                                <tr class="border-b border-[#263245] bg-[#161C28] text-[10px] text-[#94A3B8] uppercase font-bold">
                                    <th class="p-2">Seller</th>
                                    <th class="p-2 text-right">Item</th>
                                    <th class="p-2 text-right">Ship</th>
                                    <th class="p-2 text-right">Landed</th>
                                    <th class="p-2 text-right">vs Mkt</th>
                                    <th class="p-2 text-right">Action</th>
                                </tr>
                            </thead>
                            <tbody>${rowsHtml}</tbody>
                        </table>
                    </div>
                ` : `<div class="text-xs text-[#94A3B8] py-2">No live unopened English listings found for this product.</div>`}
            </div>
        `;
    });

    container.innerHTML = html;
}

async function addSelectedSealedToWatchlist() {
    const selected = buildSelectedSealedPayload();
    if (selected.length === 0) {
        showToast("Please select at least one sealed product to add to your Watchlist.", "warning");
        return;
    }

    const tag = (document.getElementById("sealed-watchlist-tag")?.value || "Sealed").trim() || "Sealed";
    const toleranceMode = document.getElementById("sealed-tolerance-mode")?.value || "market_0";
    const btn = document.getElementById("btn-watch-sealed-selected");
    if (btn) btn.disabled = true;

    try {
        const resp = await fetch("/api/watchlist/add-sealed", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                products: selected,
                tag: tag,
                target_strategy: toleranceMode,
            }),
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.error || "Failed to add sealed products to Watchlist.");
        }

        // Update local watchlist flags
        selected.forEach(s => {
            const found = currentSealedSetProducts.find(p => p.productId === s.productId);
            if (found) found.inWatchlist = true;
        });
        renderSealedSetProducts();
        showToast(data.message || "Added sealed products to your Watchlist!", "success");
    } catch (err) {
        console.error("Error adding sealed products to watchlist:", err);
        showToast(err.message || "Failed to save sealed products.", "error");
    } finally {
        if (btn) btn.disabled = selectedSealedProductIds.size === 0;
    }
}

async function quickWatchSealedProduct(productId) {
    const prod = currentSealedSetProducts.find(p => p.productId === productId);
    if (!prod) return;

    const tag = (document.getElementById("sealed-watchlist-tag")?.value || "Sealed").trim() || "Sealed";
    const toleranceMode = document.getElementById("sealed-tolerance-mode")?.value || "market_0";

    try {
        const resp = await fetch("/api/watchlist/add-sealed", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                products: [{ ...prod, targetPrice: sealedCustomTargets[productId] || null }],
                tag: tag,
                target_strategy: toleranceMode,
            }),
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.error || "Failed to save sealed product to Watchlist.");
        }
        prod.inWatchlist = true;
        renderSealedSetProducts();
        showToast(data.message || `Added ${prod.productName} to your Watchlist!`, "success");
    } catch (err) {
        showToast(err.message || "Could not add sealed product to Watchlist.", "error");
    }
}

function filterRetailerRows() {
    const onlyOpps = document.getElementById('retailers-deals-only-toggle')?.checked;
    document.querySelectorAll('.retailer-deal-row').forEach(tr => {
        const isOpp = tr.getAttribute('data-is-opp') === 'true';
        tr.classList.toggle('hidden', Boolean(onlyOpps && !isOpp));
    });
}

async function dealsSellerAction(action, name, urlOrDomain, vendorPriceId, btnEl) {
    let domain = '';
    if (urlOrDomain) {
        try {
            domain = urlOrDomain.includes('://')
                ? new URL(urlOrDomain).hostname.replace(/^www\./, '')
                : urlOrDomain.replace(/^www\./, '');
        } catch (e) { domain = urlOrDomain; }
    }
    const endpoints = {
        trust: '/api/sealed/trusted-shops/add',
        untrust: '/api/sealed/trusted-shops/remove',
        block: '/api/sealed/blocked-shops/add',
        unblock: '/api/sealed/blocked-shops/remove',
    };
    const payload = { name: name || '', domain: domain || '' };
    if (vendorPriceId) payload.vendor_price_id = vendorPriceId;
    try {
        const resp = await fetch(endpoints[action], {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (resp.ok && data.success) {
            const label = name || domain || 'Seller';
            const verbMap = {
                trust: `Whitelisted "${label}"`,
                untrust: `Removed "${label}" from whitelist`,
                block: `Blacklisted "${label}"`,
                unblock: `Unblocked "${label}"`,
            };
            if (typeof showToast === 'function') {
                showToast(verbMap[action] || 'Updated seller list.', 'success');
            }
            const mgrRow = btnEl ? btnEl.closest('.seller-mgr-row') : null;
            if (mgrRow) {
                mgrRow.remove();
            } else if (btnEl) {
                const actionBox = btnEl.parentElement;
                if (actionBox) {
                    const isTrust = action === 'trust';
                    actionBox.innerHTML = `<span class="text-[10px] font-mono font-bold uppercase ${isTrust ? 'text-[#00CED1]' : 'text-[#FF3358]'}">${isTrust ? '✓ Whitelisted' : 'Blocked'}</span>`;
                }
            } else if (action === 'trust' || action === 'block') {
                const listId = action === 'trust' ? 'trusted-shops-list' : 'blocked-shops-list';
                const listEl = document.getElementById(listId);
                if (listEl) {
                    const emptyEl = listEl.querySelector('.seller-empty-msg');
                    if (emptyEl) emptyEl.remove();
                    const row = document.createElement('div');
                    row.className = 'seller-mgr-row flex items-center justify-between text-xs bg-[#10141D] border border-[#263245] px-2.5 py-1.5';
                    const safeLabel = typeof escapeHtml === 'function' ? escapeHtml(label) : label;
                    const safeDomain = domain && name ? (typeof escapeHtml === 'function' ? escapeHtml(domain) : domain) : '';
                    const oppAction = action === 'trust' ? 'block' : 'trust';
                    const oppLabel = action === 'trust' ? 'Blacklist' : 'Whitelist';
                    const oppColor = action === 'trust' ? 'text-[#FF3358]' : 'text-[#00CED1]';
                    const remAction = action === 'trust' ? 'untrust' : 'unblock';
                    const remLabel = action === 'trust' ? 'Remove' : 'Unblock';
                    const nameArg = JSON.stringify(name || '').replace(/"/g, '&quot;');
                    const domArg = JSON.stringify(domain || '').replace(/"/g, '&quot;');
                    row.innerHTML = `<div class="truncate"><span class="text-white font-bold">${safeLabel}</span>${safeDomain ? `<span class="text-[#64748B] ml-1.5">${safeDomain}</span>` : ''}</div><div class="flex items-center space-x-1.5 flex-shrink-0 ml-2"><button type="button" onclick="dealsSellerAction('${oppAction}', ${nameArg}, ${domArg}, null, this)" class="text-[10px] ${oppColor} hover:underline uppercase font-bold">${oppLabel}</button><span class="text-[#334155]">|</span><button type="button" onclick="dealsSellerAction('${remAction}', ${nameArg}, ${domArg}, null, this)" class="text-[10px] text-[#94A3B8] hover:text-white uppercase">${remLabel}</button></div>`;
                    listEl.prepend(row);
                }
            }
            return true;
        } else {
            alert(data.error || 'Failed to update seller list.');
            return false;
        }
    } catch (err) {
        alert('Network error updating seller list.');
        return false;
    }
}

async function manualSellerAdd(action) {
    const prefix = action === 'trust' ? 'whitelist' : 'blacklist';
    const nameEl = document.getElementById(`input-${prefix}-name`);
    const domainEl = document.getElementById(`input-${prefix}-domain`);
    const name = (nameEl?.value || '').trim();
    const domain = (domainEl?.value || '').trim();
    if (!name && !domain) {
        alert('Enter a store name or domain.');
        return;
    }
    const ok = await dealsSellerAction(action, name, domain, null, null);
    if (ok) {
        if (nameEl) nameEl.value = '';
        if (domainEl) domainEl.value = '';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    if (window.location.hash === '#retailers' && typeof switchDealsSection === 'function') {
        switchDealsSection('retailers');
    }
});

document.addEventListener("DOMContentLoaded", () => {
    if (window.location.hash === "#lots" && typeof switchDealsSection === "function") {
        switchDealsSection("lots");
    } else if (window.location.hash === "#sealed" && typeof switchDealsSection === "function") {
        switchDealsSection("sealed");
    }
});
